"""
Evaluate LLM-extracted architectural decisions against a ground-truth Excel.

Usage:
    python -m service.decision_evaluator_service \
        <ground_truth_decision.xlsx> <llm_decision.json> \
        <ground_truth_architecture.xlsx> <llm_architecture.json> \
        <ground_truth_requirement.xlsx> <ground_truth_concept.xlsx> \
        <llm_requirement.json> <llm_concepts.json> \
        <output_report.xlsx> [threshold]

`<ground_truth_requirement.xlsx>` and `<ground_truth_concept.xlsx>` may be the
same combined workbook (a "Requirements" sheet plus a "Concepts" sheet) — see
`service.evaluator_service.load_ground_truth` / `load_ground_truth_concepts`.

Defaults:
    threshold = 0.75  (embedding cosine below this is not a match; applies to
                       the rationale anchor and to any field compared by
                       embedding — see `matcher` in the field specs)

Data model
----------
An Architectural Decision links architectural element(s) (units or patterns) to
the requirement(s) or concept(s) that motivated them, evidenced by a rationale.
Each record has: id, architecturalElementIds, architecturalDecisionSource,
rationale, pageNumber.

The two reference fields are ids into OTHER already-extracted artifacts:
    architecturalElementIds      -> Architectural Unit / Pattern ids
    architecturalDecisionSource  -> Requirement / Concept ids

Both are SETS, not single values: a decision routinely cites several elements
and several sources, written either as a JSON list or as a comma/semicolon
separated string. `architecturalElementIds` holds ids only, while
`architecturalDecisionSource` may mix ids with concept names the model coined;
both go through the same resolution, which simply never has to fall back for a
pure-id list. The ground truth and the LLM extraction do
not share an id namespace for these references (the ground truth references its
own human-annotated architecture/requirement/concept ids; the LLM references
the ids of its own architecture/requirement/concept extraction). Direct id
comparison is therefore meaningless — each id is instead RESOLVED to
human-readable text (the referenced element's name, the referenced
requirement's description, or the referenced concept's name) using the matching
ground-truth or LLM architecture, requirement and concept artifacts. A token
that does not resolve to a known id (e.g. the ground truth names a technology
directly instead of citing an id, or the model cites a concept by name) falls
back to using its own raw text, so it can still be compared. Tokens resolving
to the same text are collapsed, so citing one concept twice — once by id, once
by name — is not counted as two references.

What it does
------------
1. The matching ANCHOR is the `rationale`. A GT decision is matched to an LLM
   decision when their rationale texts are semantically similar (embedding
   cosine >= threshold). Among eligible pairs, an optimal (Hungarian)
   one-to-one assignment is solved, maximising the number of matches first and
   using the rationale similarity only as a tie-breaker. This is scored at the
   DECISION level with Precision, Recall and Mean Semantic Meaning (the average
   rationale similarity over matched pairs).
2. `architecturalDecisionSource` and `architecturalElementIds` are scored as
   Accuracy over the matched pairs, SET-WISE, so a decision that cites three
   sources and gets one of them right earns partial credit instead of a flat
   miss. Within a matched pair the two token sets are aligned by an optimal
   one-to-one assignment; that decision's accuracy is the aligned references
   over the UNION of the two sets, so it falls both for a reference the model
   missed and for one it invented. The field's accuracy is the mean over the
   matched pairs, so every decision counts once however many references it
   cites.

   HOW two references are judged to be the same is a per-field choice, declared
   as `matcher` in the field spec and named in the report:
     - architecturalDecisionSource -> ExactMatcher. A source is a requirement or
       a concept from a fixed catalog, so naming the same source means naming
       that entry, not something that merely reads like it. Comparison is exact
       on normalised text (Volere-style section label dropped, whitespace
       collapsed, case folded).
     - architecturalElementIds -> EmbeddingMatcher. An element is matched on its
       free-text name, whose wording legitimately differs between the two
       extractions ("API Gateway" vs "API Gateway Service").
3. `pageNumber` is scored as Accuracy over the matched pairs too (correct
   when the two page-number sets overlap).

Only the anchor carries Precision and Recall — it is the field that decides
which decisions were found at all. Every other field is scored over the pairs
the anchor already matched, where there is nothing to be precise or complete
about: the question is only how much of that field is right.

Output: an xlsx with sheets — Field_Metrics, Matching_Summary, Field_Counts,
Matched_TP, False_Positives, False_Negatives.
"""
import json
import re
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

from service.evaluator_service import (
    _CONCEPT_PREFIX,
    _is_blank,
    norm_text,
    compute_similarity,
    optimal_match,
    _fmt,
    _pick,
)
from service.evaluator_service import load_ground_truth as load_requirement_ground_truth
from service.evaluator_service import load_ground_truth_concepts
from service.architecture_evaluator_service import (
    _is_number,
    load_ground_truth as load_architecture_ground_truth,
    load_llm_extraction as load_architecture_llm_extraction,
    page_set,
)


# ---------------------------------------------------------------------------
# Field specification.
# `rationale` is the matching anchor; the reference fields are scored set-wise
# and `pageNumber` as plain accuracy — all three over the matched pairs only.
# ---------------------------------------------------------------------------
RATIONALE_SPEC = {"name": "rationale", "json": ["rationale", "Rationale"],
                  "gt": ["Rationale"]}

# `matcher` is how two resolved references are judged to name the same thing.
# A source is a requirement or a concept drawn from a fixed catalog, so naming
# the same one means naming it exactly; an element is matched on its free-text
# name, where wording legitimately varies between the two extractions.
SOURCE_SPEC = {"name": "architecturalDecisionSource",
               "json": ["architecturalDecisionSource", "architectural_decision_source", "ArchitecturalDecisionSource"],
               "gt": ["AD Source", "ArchitecturalDecisionSource"],
               "matcher": lambda threshold: ExactMatcher()}
ELEMENT_SPEC = {"name": "architecturalElementIds",
                # The singular spellings are kept so extractions made before the
                # field became a list still load.
                "json": ["architecturalElementIds", "architectural_element_ids", "ArchitecturalElementIds",
                         "architecturalElementId", "architectural_element_id", "ArchitecturalElementId"],
                "gt": ["Architectural Element IDs", "Architectural Element ID",
                       "ArchitecturalElementIds", "ArchitecturalElementId"],
                "matcher": lambda threshold: EmbeddingMatcher(threshold)}
PAGE_SPEC = {"name": "pageNumber",
             "json": ["pageNumber", "page", "page_number", "Page Number"],
             "gt": ["Page Number", "PageNumber", "Page"],
             "kind": "page",
             "comparison": "page-number sets overlap"}

REFERENCE_SPECS = [SOURCE_SPEC, ELEMENT_SPEC]
OTHER_SPECS = [PAGE_SPEC]
ALL_SPECS = [RATIONALE_SPEC] + REFERENCE_SPECS + OTHER_SPECS

ID_JSON_CANDIDATES = ["id", "ID", "ad_id", "AD ID"]
ID_GT_CANDIDATES = ["AD ID", "ID", "Id", "id"]

_REF_SPLIT = re.compile(r"[,;]")
_TRAILING_MARK = re.compile(r"[*\s]+$")


# ---------------------------------------------------------------------------
# Loading — decisions
# ---------------------------------------------------------------------------
def load_ground_truth_decisions(xlsx_path: str) -> list[dict]:
    """Read the GT decision xlsx into canonical records; merges wrapped
    continuation rows (blank AD ID) into the previous record's rationale."""
    df = pd.read_excel(xlsx_path)
    cols = list(df.columns)

    id_col = _pick(cols, ID_GT_CANDIDATES)
    field_cols = {s["name"]: _pick(cols, s["gt"]) for s in ALL_SPECS}
    rationale_col = field_cols[RATIONALE_SPEC["name"]]

    records = []
    for _, row in df.iterrows():
        id_val = row[id_col] if id_col else None
        if id_col is not None and not _is_blank(id_val):
            rec = {"id": str(id_val).strip(), "_raw": {}}
            for s in ALL_SPECS:
                col = field_cols[s["name"]]
                rec[s["name"]] = row[col] if (col and not _is_blank(row[col])) else None
            records.append(rec)
        else:
            if rationale_col and not _is_blank(row[rationale_col]) and records:
                prev = records[-1]
                prev["rationale"] = f"{prev.get('rationale') or ''} {str(row[rationale_col]).strip()}".strip()
    return records


def load_llm_decisions(json_path: str) -> list[dict]:
    """Read the LLM architectural-decision JSON into canonical records."""
    with open(json_path) as f:
        data = json.load(f)
    items = data.get("architectural_decisions", data) if isinstance(data, dict) else data
    if not items:
        return []

    def get(item, candidates):
        for k in candidates:
            if k in item and not _is_blank(item[k]):
                return item[k]
        return None

    records = []
    for item in items:
        if not isinstance(item, dict):
            continue
        id_val = get(item, ID_JSON_CANDIDATES)
        rec = {"id": str(id_val).strip() if id_val is not None else "", "_raw": item}
        for s in ALL_SPECS:
            rec[s["name"]] = get(item, s["json"])
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Reference resolution — architecturalElementIds / architecturalDecisionSource
# ---------------------------------------------------------------------------
def split_ref_tokens(v) -> list[str]:
    """Split a reference field into individual id/name tokens.

    The field arrives in whichever shape its producer used: a JSON list
    (["C_05", "Security"] — what the model emits), a separated string
    ("CF_M01_C07, CF_M01_C08" — what the ground truth writes), or a single
    value. All three are flattened the same way, and each element is still
    split on comma/semicolon so a list holding a packed string is handled too.
    Stray trailing markers (e.g. a lone '*') are stripped.
    """
    if _is_blank(v):
        return []
    elements = v if isinstance(v, (list, tuple)) else [v]
    tokens = []
    for element in elements:
        if _is_blank(element):
            continue
        for part in _REF_SPLIT.split(str(element)):
            t = _TRAILING_MARK.sub("", part.strip()).strip()
            if t and t != "*":
                tokens.append(t)
    return tokens


def build_architecture_id_to_name(records: list[dict]) -> dict:
    return {r["id"]: norm_text(r.get("name")) for r in records if r.get("id") and not _is_blank(r.get("name"))}


def build_gt_requirement_id_to_text(gt_requirements: list[dict]) -> dict:
    """id -> description for the GT requirement universe (Requirement ID ->
    Requirement text). Concept ids are NOT covered here — they come from the
    dedicated concept ground truth, see `load_ground_truth_concepts`."""
    return {
        r["id"]: norm_text(r.get("description"))
        for r in gt_requirements if r.get("id") and not _is_blank(r.get("description"))
    }


def build_llm_requirement_id_to_text(requirements_json_path: str, concepts_json_path: str) -> dict:
    """id -> resolved text from the LLM's final requirements JSON (id: R_xx ->
    description) and its final concepts JSON (id: C_xx -> name) — exactly the
    two artifacts given to the decision pass, so their own ids resolve
    directly."""
    def load_items(path):
        with open(path) as f:
            data = json.load(f)
        return data.get("requirements", data) if isinstance(data, dict) else (data or [])

    id_to_text = {}
    for item in list(load_items(requirements_json_path)) + list(load_items(concepts_json_path)):
        if not isinstance(item, dict):
            continue
        rid = item.get("id")
        if not rid:
            continue
        text = item.get("description") if item.get("description") is not None else item.get("name")
        if not _is_blank(text):
            id_to_text[str(rid).strip()] = norm_text(text)
    return id_to_text


def resolved_ref_tokens(raw_value, id_to_text: dict) -> list[str]:
    """Resolve a reference field to the list of texts it cites — one entry per
    distinct reference, which is what makes the field scorable set-wise.

    Every token is resolved to its referenced text, or kept as its own raw text
    when it is not a known id (the ground truth naming a technology directly,
    the model citing a concept by name). Duplicates are then collapsed
    case-insensitively: a model that cites one concept both by id and by name
    ("C_05", "Security" -> "Security", "Security") is citing one reference, and
    counting it twice would penalise it for being explicit.
    """
    seen, tokens = set(), []
    for token in split_ref_tokens(raw_value):
        text = norm_text(id_to_text.get(token, token))
        if not text:
            continue
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            tokens.append(text)
    return tokens


# ---------------------------------------------------------------------------
# How two resolved references are judged to name the same thing
# ---------------------------------------------------------------------------
_WHITESPACE = re.compile(r"\s+")


def normalise_reference_text(text) -> str:
    """Canonical form of a reference text for exact comparison.

    Drops the Volere-style section label a concept name may carry
    ("12g. Scalability" -> "Scalability") using the same pattern the
    requirement evaluator applies, so a concept means the same thing in both
    evaluations; then collapses whitespace and folds case. Everything else is
    left alone: the point is to remove notation, not to paraphrase.
    """
    stripped = _CONCEPT_PREFIX.sub("", str(text)).strip()
    return _WHITESPACE.sub(" ", stripped).casefold()


class ReferenceMatcher(ABC):
    """A comparison strategy for one reference field.

    A matcher scores two token vocabularies against each other and states the
    score at which a pair counts as the same reference. Everything downstream —
    alignment and set-wise accuracy — is identical whichever matcher a field
    uses, so changing how a field is compared is a one-line change in its spec
    rather than a change to the scoring code.
    """

    #: Score at or above which two references are the same.
    threshold: float
    #: Whether scores mean anything beyond match/no-match. An exact matcher
    #: emits only 1.0 or 0.0, so averaging its scores would report nothing.
    scores_are_graded: bool
    #: Named in the report, so a reader can see how each field was compared.
    label: str

    @abstractmethod
    def score_matrix(self, gt_texts: list[str], llm_texts: list[str]) -> np.ndarray:
        """Score every LLM text against every GT text; shape (llm x gt)."""


class ExactMatcher(ReferenceMatcher):
    """Two references are the same only when their normalised texts are equal.

    Used where both sides draw from a fixed catalog — a requirement or a
    concept — so that naming the same reference means naming that entry and not
    merely something that reads like it. Embedding similarity cannot make that
    distinction: on this corpus 'Confidentiality' and 'Security' score 0.771,
    which a cosine gate would accept as the same source.
    """

    threshold = 1.0
    scores_are_graded = False
    label = "exact match on normalised text"

    def score_matrix(self, gt_texts: list[str], llm_texts: list[str]) -> np.ndarray:
        if not gt_texts or not llm_texts:
            return np.zeros((len(llm_texts), len(gt_texts)))
        gt_keys = [normalise_reference_text(t) for t in gt_texts]
        llm_keys = [normalise_reference_text(t) for t in llm_texts]
        return np.array([[1.0 if (a and a == b) else 0.0 for b in gt_keys]
                         for a in llm_keys], dtype=float)


class EmbeddingMatcher(ReferenceMatcher):
    """Two references are the same when their texts are semantically close.

    Used where the reference is free text whose wording legitimately differs
    between the two extractions — an architectural element named "API Gateway"
    on one side and "API Gateway Service" on the other is the same element.
    """

    scores_are_graded = True

    def __init__(self, threshold: float):
        self.threshold = threshold
        self.label = f"embedding cosine >= {threshold}"

    def score_matrix(self, gt_texts: list[str], llm_texts: list[str]) -> np.ndarray:
        return compute_similarity(gt_texts, llm_texts)


# ---------------------------------------------------------------------------
# Set-wise scoring of a reference field
# ---------------------------------------------------------------------------
class ReferenceField(NamedTuple):
    """One reference field's resolved token sets, the matcher that compares
    them, and the token-level score matrix it produced.

    The matrix is computed once over the field's whole token vocabulary rather
    than per decision, so each distinct referenced text is scored once no
    matter how many decisions cite it.
    """
    gt: list[list[str]]        # resolved tokens, per GT record
    llm: list[list[str]]       # resolved tokens, per LLM record
    scores: np.ndarray         # (llm vocab x gt vocab), per `matcher`
    llm_index: dict            # token -> row in `scores`
    gt_index: dict             # token -> column in `scores`
    matcher: ReferenceMatcher  # how two tokens are judged to be the same

    def align(self, i: int, j: int) -> tuple[int, list[float]]:
        """Align LLM decision `i`'s tokens with GT decision `j`'s: an optimal
        one-to-one assignment over the pair's token scores, keeping only
        alignments the matcher accepts. Returns the number of aligned tokens
        and their scores."""
        llm_tokens, gt_tokens = self.llm[i], self.gt[j]
        if not llm_tokens or not gt_tokens:
            return 0, []
        rows = [self.llm_index[t] for t in llm_tokens]
        cols = [self.gt_index[t] for t in gt_tokens]
        pair_scores = self.scores[np.ix_(rows, cols)]
        aligned = optimal_match(pair_scores, self.matcher.threshold)
        return len(aligned), [s for _, _, s in aligned]


def build_reference_field(gt_values, llm_values, gt_idx: dict, llm_idx: dict,
                          matcher: ReferenceMatcher) -> ReferenceField:
    """Resolve one reference field on both sides and score its token vocabulary
    with the field's matcher."""
    gt_tokens = [resolved_ref_tokens(v, gt_idx) for v in gt_values]
    llm_tokens = [resolved_ref_tokens(v, llm_idx) for v in llm_values]

    gt_vocab = sorted({t for toks in gt_tokens for t in toks})
    llm_vocab = sorted({t for toks in llm_tokens for t in toks})
    return ReferenceField(
        gt=gt_tokens, llm=llm_tokens,
        scores=matcher.score_matrix(gt_vocab, llm_vocab),
        llm_index={t: i for i, t in enumerate(llm_vocab)},
        gt_index={t: j for j, t in enumerate(gt_vocab)},
        matcher=matcher,
    )


def set_accuracy(aligned: int, n_gt: int, n_llm: int) -> float | None:
    """How much of one decision's reference set is right, in [0, 1].

    The aligned references over the union of the two sets, so the score falls
    both when the model misses a reference the ground truth cites and when it
    cites one the ground truth does not. Citing 1 of 3 correct sources scores
    1/3, not 1 — partial credit, but not credit for being incomplete.
    """
    union = n_gt + n_llm - aligned
    return (aligned / union) if union else None


def score_reference_field(field: ReferenceField, pairs) -> dict:
    """Score one reference field set-wise over the matched decision pairs.

    Accuracy is the mean of the per-decision set accuracies, so every matched
    decision counts once regardless of how many references it cites. A pair
    where either side left the field empty is not scored — there is no
    reference set to compare — but is still counted, so the populated totals in
    Field_Counts explain the denominator.
    """
    aligned_total = llm_total = gt_total = 0
    accuracies, scores, scored_pairs = [], [], 0

    for i, j, _ in pairs:
        llm_tokens, gt_tokens = field.llm[i], field.gt[j]
        if not llm_tokens or not gt_tokens:
            continue
        scored_pairs += 1
        aligned, matched_scores = field.align(i, j)
        aligned_total += aligned
        llm_total += len(llm_tokens)
        gt_total += len(gt_tokens)
        scores.extend(matched_scores)
        accuracies.append(set_accuracy(aligned, len(gt_tokens), len(llm_tokens)))

    return {
        "accuracy": float(np.mean(accuracies)) if accuracies else None,
        # Only meaningful for a graded matcher — an exact matcher's aligned
        # scores are all 1.0 by construction.
        "mean_semantic": (float(np.mean(scores)) if scores else None)
                         if field.matcher.scores_are_graded else None,
        "matched_tokens": aligned_total,
        "llm_tokens": llm_total,
        "gt_tokens": gt_total,
        "scored_pairs": scored_pairs,
    }


# ---------------------------------------------------------------------------
# Per-field agreement (pageNumber — the only field still scored all-or-nothing)
# ---------------------------------------------------------------------------
def field_present(rec: dict, spec: dict) -> bool:
    return not _is_blank(rec.get(spec["name"]))


def field_agrees(spec: dict, llm_rec: dict, gt_rec: dict) -> bool:
    kind = spec["kind"]
    if kind == "page":
        pa, pb = page_set(llm_rec.get(spec["name"])), page_set(gt_rec.get(spec["name"]))
        return bool(pa) and bool(pb) and bool(pa & pb)
    raise ValueError(f"unknown kind {kind}")


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def build_report(gt, llm, pairs, threshold, *, rationale_sim, ref_fields):
    matched_llm = {i for i, _, _ in pairs}
    matched_gt = {j for _, j, _ in pairs}

    tp, fp, fn = len(pairs), len(llm) - len(pairs), len(gt) - len(pairs)

    # --- Anchor: the rationale, at the DECISION level ------------------------
    llm_rationale_total = sum(field_present(r, RATIONALE_SPEC) for r in llm)
    gt_rationale_total = sum(field_present(r, RATIONALE_SPEC) for r in gt)
    precision = (tp / llm_rationale_total) if llm_rationale_total else None
    recall = (tp / gt_rationale_total) if gt_rationale_total else None
    mean_rationale_sim = float(np.mean([s for _, _, s in pairs])) if pairs else None

    field_metrics_anchor = pd.DataFrame(
        [{
            "field": "rationale (matching anchor)",
            "precision": _fmt(precision),
            "recall": _fmt(recall),
            "mean semantic meaning": _fmt(mean_rationale_sim),
        }],
        columns=["field", "precision", "recall", "mean semantic meaning"],
    )

    count_rows = [{
        "field": "rationale (matching anchor)",
        "gt_populated_total": gt_rationale_total,
        "llm_populated_total": llm_rationale_total,
        "gt_populated_matched": sum(field_present(gt[j], RATIONALE_SPEC) for _, j, _ in pairs),
        "llm_populated_matched": sum(field_present(llm[i], RATIONALE_SPEC) for i, _, _ in pairs),
        "correct_in_matched": tp,
        "matched_pairs (TP)": tp,
    }]

    # --- Every other field: Accuracy over the matched pairs -------------------
    field_rows = []
    for spec in REFERENCE_SPECS:
        name = spec["name"]
        field = ref_fields[name]
        scores = score_reference_field(field, pairs)
        field_rows.append({
            "field": name,
            "comparison": field.matcher.label,
            "accuracy": _fmt(scores["accuracy"]),
            "mean semantic meaning": _fmt(scores["mean_semantic"]),
        })
        count_rows.append({
            "field": name,
            "gt_populated_total": sum(field_present(r, spec) for r in gt),
            "llm_populated_total": sum(field_present(r, spec) for r in llm),
            "gt_populated_matched": sum(field_present(gt[j], spec) for _, j, _ in pairs),
            "llm_populated_matched": sum(field_present(llm[i], spec) for i, _, _ in pairs),
            "correct_in_matched": scores["matched_tokens"],
            "matched_pairs (TP)": tp,
            "scored_pairs": scores["scored_pairs"],
            "gt_references_in_scored": scores["gt_tokens"],
            "llm_references_in_scored": scores["llm_tokens"],
            "matched_references": scores["matched_tokens"],
        })

    for spec in OTHER_SPECS:
        name = spec["name"]
        correct, scored_pairs = 0, 0
        llm_has_matched, gt_has_matched = 0, 0
        for i, j, _ in pairs:
            llm_populated = field_present(llm[i], spec)
            gt_populated = field_present(gt[j], spec)
            llm_has_matched += llm_populated
            gt_has_matched += gt_populated
            if not (llm_populated and gt_populated):
                continue
            scored_pairs += 1
            correct += field_agrees(spec, llm[i], gt[j])

        field_rows.append({
            "field": name,
            "comparison": spec["comparison"],
            "accuracy": _fmt((correct / scored_pairs) if scored_pairs else None),
            "mean semantic meaning": _fmt(None),
        })
        count_rows.append({
            "field": name,
            "gt_populated_total": sum(field_present(r, spec) for r in gt),
            "llm_populated_total": sum(field_present(r, spec) for r in llm),
            "gt_populated_matched": gt_has_matched,
            "llm_populated_matched": llm_has_matched,
            "correct_in_matched": correct,
            "matched_pairs (TP)": tp,
            "scored_pairs": scored_pairs,
        })

    field_metrics_fields = pd.DataFrame(
        field_rows, columns=["field", "comparison", "accuracy", "mean semantic meaning"])
    field_counts = pd.DataFrame(count_rows)

    summary = pd.DataFrame([
        {"Metric": "Ground truth count", "Value": len(gt)},
        {"Metric": "LLM extracted count", "Value": len(llm)},
        {"Metric": "True Positives (matched)", "Value": tp},
        {"Metric": "False Positives", "Value": fp},
        {"Metric": "False Negatives", "Value": fn},
        {"Metric": "Decision precision (by rationale)", "Value": _fmt(precision)},
        {"Metric": "Decision recall (by rationale)", "Value": _fmt(recall)},
        {"Metric": "Rationale match threshold (cosine)", "Value": threshold},
    ])

    def fields_blob(rec, prefix):
        return {f"{prefix}_{s['name']}": _cell(rec.get(s["name"])) for s in ALL_SPECS}

    def reference_blob(i, j):
        """Per-pair set-wise detail: how many of the cited references lined up,
        out of how many each side cited."""
        blob = {}
        for spec in REFERENCE_SPECS:
            field = ref_fields[spec["name"]]
            aligned, scores = field.align(i, j)
            n_llm, n_gt = len(field.llm[i]), len(field.gt[j])
            short = "source" if spec is SOURCE_SPEC else "element"
            blob[f"{short}_matched"] = aligned
            blob[f"{short}_gt_total"] = n_gt
            blob[f"{short}_llm_total"] = n_llm
            blob[f"{short}_accuracy"] = _fmt(
                set_accuracy(aligned, n_gt, n_llm) if (n_llm and n_gt) else None)
            blob[f"{short}_mean_similarity"] = _fmt(
                (float(np.mean(scores)) if scores else None)
                if field.matcher.scores_are_graded else None)
        return blob

    matched = pd.DataFrame([
        {"LLM_ID": llm[i]["id"], "GT_ID": gt[j]["id"],
         "rationale_similarity": round(float(rationale_sim[i, j]), 4),
         **reference_blob(i, j),
         **fields_blob(llm[i], "LLM"), **fields_blob(gt[j], "GT")}
        for i, j, s in sorted(pairs, key=lambda x: x[2])
    ])

    fps = pd.DataFrame([
        {"LLM_ID": llm[i]["id"], **fields_blob(llm[i], "LLM"),
         "closest_GT_ID": gt[int(rationale_sim[i].argmax())]["id"] if len(gt) else None,
         "closest_similarity": round(float(rationale_sim[i].max()), 4) if len(gt) else 0.0}
        for i in range(len(llm)) if i not in matched_llm
    ])

    fns = pd.DataFrame([
        {"GT_ID": gt[j]["id"], **fields_blob(gt[j], "GT"),
         "closest_LLM_ID": llm[int(rationale_sim[:, j].argmax())]["id"] if len(llm) else None,
         "closest_similarity": round(float(rationale_sim[:, j].max()), 4) if len(llm) else 0.0}
        for j in range(len(gt)) if j not in matched_gt
    ])

    return {
        "Field_Metrics_Anchor": field_metrics_anchor,
        "Field_Metrics_Fields": field_metrics_fields,
        "Matching_Summary": summary,
        "Field_Counts": field_counts,
        "Matched_TP": matched,
        "False_Positives": fps,
        "False_Negatives": fns,
        "stats": {"tp": tp, "fp": fp, "fn": fn},
    }


def _cell(v):
    """Excel cannot hold a list, and a reference field is often one."""
    return ", ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else v


# ---------------------------------------------------------------------------
# Averaging across runs
# ---------------------------------------------------------------------------
def _average_cell(values):
    """Mean of the numeric values, rounded.

    "-" cells (a metric that run could not define) are skipped rather than
    counted as zero, so a metric is averaged only over the runs that scored it.
    A column that is constant text across runs — `comparison`, naming how a
    field was compared — is carried through unchanged rather than averaged
    away.
    """
    nums = [float(v) for v in values if _is_number(v)]
    if nums:
        return round(sum(nums) / len(nums), 4)
    return values[0] if len({str(v) for v in values}) == 1 else "-"


def _average_table(tables: list[pd.DataFrame], key: str = "field") -> pd.DataFrame:
    """Average every non-key column of the same table across runs, rows matched
    on `key`. Row order and columns are taken from the first run."""
    columns = list(tables[0].columns)
    rows = []
    for k in tables[0][key]:
        row = {key: k}
        for col in columns[1:]:
            row[col] = _average_cell([t.loc[t[key] == k, col].iloc[0]
                                      for t in tables if (t[key] == k).any()])
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def _average_summary(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """Average the Matching_Summary values across runs. The counts average to
    fractions on purpose: 'True Positives 3.6667' is the mean number of
    decisions the three runs matched, which is the quantity a single run's
    integer is an estimate of."""
    rows = [{"Metric": metric,
             "Value": _average_cell([t.loc[t["Metric"] == metric, "Value"].iloc[0] for t in tables])}
            for metric in tables[0]["Metric"]]
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def average_decision_reports(reports: list[dict]) -> dict:
    """Average the metric tables across N `evaluate_decisions` reports — the
    repeated extraction runs over one document — into tables of the same shape.

    Only the metric tables are averaged. The per-decision sheets (Matched_TP,
    the false positive / negative lists) belong to one run each and stay in that
    run's own workbook: averaging them would mean averaging different decisions.
    """
    if not reports:
        raise ValueError("average_decision_reports requires at least one report")
    return {
        "Field_Metrics_Anchor": _average_table([r["Field_Metrics_Anchor"] for r in reports]),
        "Field_Metrics_Fields": _average_table([r["Field_Metrics_Fields"] for r in reports]),
        "Matching_Summary": _average_summary([r["Matching_Summary"] for r in reports]),
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _write_field_metrics(writer, anchor_df: pd.DataFrame, fields_df: pd.DataFrame) -> None:
    """The Field_Metrics sheet: the anchor's row first, then every other field's
    accuracy row, separated by two blank lines."""
    anchor_df.to_excel(writer, sheet_name="Field_Metrics", index=False, startrow=0)
    fields_df.to_excel(writer, sheet_name="Field_Metrics", index=False,
                       startrow=len(anchor_df) + 3)


def write_average_decision_report(reports: list[dict], output_path) -> None:
    """Write the across-runs average to a standalone workbook, laid out like a
    single run's: a Field_Metrics sheet plus Matching_Summary."""
    avg = average_decision_reports(reports)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        _write_field_metrics(w, avg["Field_Metrics_Anchor"], avg["Field_Metrics_Fields"])
        avg["Matching_Summary"].to_excel(w, sheet_name="Matching_Summary", index=False)


def evaluate_decisions(gt_decision_path, llm_decision_path,
                       gt_architecture_path, llm_architecture_path,
                       gt_requirement_path, gt_concept_path,
                       llm_requirement_path, llm_concepts_path,
                       output_path, threshold=0.75):
    gt = load_ground_truth_decisions(gt_decision_path)
    llm = load_llm_decisions(llm_decision_path)
    if not gt or not llm:
        raise ValueError(f"Empty input — gt={len(gt)} llm={len(llm)}")

    # Matching anchor: the rationale, compared as plain text.
    gt_rationale_texts = [norm_text(r.get(RATIONALE_SPEC["name"])) or "" for r in gt]
    llm_rationale_texts = [norm_text(r.get(RATIONALE_SPEC["name"])) or "" for r in llm]
    rationale_sim = compute_similarity(gt_rationale_texts, llm_rationale_texts)  # (n_llm, n_gt)

    pairs = optimal_match(rationale_sim, threshold)

    # Resolvers: an architecturalElementIds/architecturalDecisionSource is a set
    # of references into other already-extracted artifacts, so each id is
    # resolved to human-readable text through THAT artifact's own ground
    # truth/LLM output, rather than compared as a raw id (the two sides use
    # unrelated id namespaces). The source side merges the requirement ground
    # truth (Requirement ID -> text) with the dedicated concept ground truth
    # (Concept ID -> name); concept ids take precedence on key collision, which
    # cannot happen in practice since the two id namespaces (R_xx / C_xx) are
    # disjoint by construction.
    element_gt_idx = build_architecture_id_to_name(load_architecture_ground_truth(gt_architecture_path))
    element_llm_idx = build_architecture_id_to_name(load_architecture_llm_extraction(llm_architecture_path))
    source_gt_idx = {
        **build_gt_requirement_id_to_text(load_requirement_ground_truth(gt_requirement_path)),
        **load_ground_truth_concepts(gt_concept_path),
    }
    source_llm_idx = build_llm_requirement_id_to_text(llm_requirement_path, llm_concepts_path)

    # Scored set-wise over the matched pairs; not used for matching. Each field
    # is compared the way its spec says — see `matcher` in REFERENCE_SPECS.
    resolvers = {
        SOURCE_SPEC["name"]: (source_gt_idx, source_llm_idx),
        ELEMENT_SPEC["name"]: (element_gt_idx, element_llm_idx),
    }
    ref_fields = {
        spec["name"]: build_reference_field(
            [r.get(spec["name"]) for r in gt],
            [r.get(spec["name"]) for r in llm],
            *resolvers[spec["name"]],
            matcher=spec["matcher"](threshold),
        )
        for spec in REFERENCE_SPECS
    }

    report = build_report(gt, llm, pairs, threshold,
                          rationale_sim=rationale_sim, ref_fields=ref_fields)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        _write_field_metrics(w, report["Field_Metrics_Anchor"], report["Field_Metrics_Fields"])
        for sheet in ("Matching_Summary", "Field_Counts", "Matched_TP", "False_Positives", "False_Negatives"):
            report[sheet].to_excel(w, sheet_name=sheet, index=False)

    return report


if __name__ == "__main__":
    if len(sys.argv) < 10:
        print("Usage: python -m service.decision_evaluator_service "
              "<ground_truth_decision.xlsx> <llm_decision.json> "
              "<ground_truth_architecture.xlsx> <llm_architecture.json> "
              "<ground_truth_requirement.xlsx> <ground_truth_concept.xlsx> "
              "<llm_requirement.json> <llm_concepts.json> "
              "<output_report.xlsx> [threshold]")
        raise SystemExit(1)
    gt_d, llm_d, gt_a, llm_a, gt_r, gt_c, llm_r, llm_cpt, out_p = sys.argv[1:10]
    thr = float(sys.argv[10]) if len(sys.argv) > 10 else 0.75
    rep = evaluate_decisions(gt_d, llm_d, gt_a, llm_a, gt_r, gt_c, llm_r, llm_cpt, out_p, thr)
    s = rep["stats"]
    print(f"TP={s['tp']} FP={s['fp']} FN={s['fn']}")
    print("Saved:", out_p)
