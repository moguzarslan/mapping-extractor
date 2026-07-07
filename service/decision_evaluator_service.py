"""
Evaluate LLM-extracted architectural decisions against a ground-truth Excel.

Usage:
    python -m service.decision_evaluator_service \
        <ground_truth_decision.xlsx> <llm_decision.json> \
        <ground_truth_architecture.xlsx> <llm_architecture.json> \
        <ground_truth_requirement.xlsx> <ground_truth_concept.xlsx> \
        <llm_requirement.json> <llm_concepts.json> \
        <output_report.xlsx> [threshold]

Defaults:
    threshold = 0.75  (embedding cosine similarity below this is not a match)

Data model
----------
An Architectural Decision links ONE architectural element (a unit or a pattern)
to ONE requirement or concept that motivated it, evidenced by a rationale. Each
record has: id, architecturalElementId, architecturalDecisionSource, rationale,
pageNumber.

The two reference fields are ids into OTHER already-extracted artifacts:
    architecturalElementId       -> an Architectural Unit / Pattern id
    architecturalDecisionSource  -> a Requirement / Concept id

The ground truth and the LLM extraction do not share an id namespace for these
references (the ground truth references its own human-annotated architecture/
requirement/concept ids; the LLM references the ids of its own architecture/
requirement/concept extraction). Direct id comparison is therefore meaningless
— each reference is instead RESOLVED to human-readable text (the referenced
element's name, the referenced requirement's description, or the referenced
concept's name) using the matching ground-truth or LLM architecture,
requirement and concept artifacts. A reference that does not resolve to a known
id (e.g. the ground truth names a technology directly instead of citing an id)
falls back to using its own raw text, so it can still be compared.

What it does
------------
1. The matching ANCHOR is the PAIR (architecturalDecisionSource,
   architecturalElementId) — NOT the rationale. A GT decision is matched to an
   LLM decision only when BOTH resolved references agree: the resolved source
   texts are semantically similar (>= threshold) AND the resolved element texts
   are semantically similar (>= threshold). Among eligible pairs, an optimal
   (Hungarian) one-to-one assignment is solved, maximising the number of
   matches first and using the average of the two similarities as a
   tie-breaker. This is scored at the DECISION level with Precision, Recall and
   Mean Semantic Meaning (the average of the two resolved-reference
   similarities over matched pairs).
2. `rationale` and `pageNumber` are scored as Accuracy over the matched pairs
   (rationale: cosine similarity >= threshold, with Mean Semantic Meaning;
   pageNumber: page-number sets overlap).

A reference field may be a comma/semicolon separated list (the ground truth is
not always clean); each side is resolved and joined into one string before
similarity is computed, so a decision with multiple sources/elements is
compared holistically rather than requiring an exact token-for-token match.

Output: an xlsx with sheets — Field_Metrics, Matching_Summary, Field_Counts,
Matched_TP, False_Positives, False_Negatives.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from service.evaluator_service import (
    _is_blank,
    norm_text,
    compute_similarity,
    text_similarity,
    _fmt,
    _pick,
)
from service.evaluator_service import load_ground_truth as load_requirement_ground_truth
from service.architecture_evaluator_service import (
    load_ground_truth as load_architecture_ground_truth,
    load_llm_extraction as load_architecture_llm_extraction,
    page_set,
)


# ---------------------------------------------------------------------------
# Field specification — fields scored as Accuracy over the matched pairs.
# (architecturalDecisionSource / architecturalElementId are NOT here: they
# form the matching anchor and are scored separately, see `build_report`.)
# ---------------------------------------------------------------------------
FIELD_SPECS = [
    {"name": "rationale", "json": ["rationale", "Rationale"],
     "gt": ["Rationale"], "kind": "semantic", "semantic": True},
    {"name": "pageNumber", "json": ["pageNumber", "page", "page_number", "Page Number"],
     "gt": ["Page Number", "PageNumber", "Page"], "kind": "page", "semantic": False},
]

SOURCE_SPEC = {"name": "architecturalDecisionSource",
              "json": ["architecturalDecisionSource", "architectural_decision_source", "ArchitecturalDecisionSource"],
              "gt": ["AD Source", "ArchitecturalDecisionSource"]}
ELEMENT_SPEC = {"name": "architecturalElementId",
                "json": ["architecturalElementId", "architectural_element_id", "ArchitecturalElementId"],
                "gt": ["Architectural Element ID", "ArchitecturalElementId"]}
ANCHOR_SPECS = [SOURCE_SPEC, ELEMENT_SPEC]

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
    all_specs = FIELD_SPECS + ANCHOR_SPECS
    field_cols = {s["name"]: _pick(cols, s["gt"]) for s in all_specs}
    rationale_col = field_cols["rationale"]

    records = []
    for _, row in df.iterrows():
        id_val = row[id_col] if id_col else None
        if id_col is not None and not _is_blank(id_val):
            rec = {"id": str(id_val).strip(), "_raw": {}}
            for s in all_specs:
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

    all_specs = FIELD_SPECS + ANCHOR_SPECS
    records = []
    for item in items:
        if not isinstance(item, dict):
            continue
        id_val = get(item, ID_JSON_CANDIDATES)
        rec = {"id": str(id_val).strip() if id_val is not None else "", "_raw": item}
        for s in all_specs:
            rec[s["name"]] = get(item, s["json"])
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Reference resolution — architecturalElementId / architecturalDecisionSource
# ---------------------------------------------------------------------------
def split_ref_tokens(v) -> list[str]:
    """Split a (possibly messy) reference field into individual tokens: strips
    stray trailing markers (e.g. a lone '*') and splits on comma/semicolon."""
    if _is_blank(v):
        return []
    parts = _REF_SPLIT.split(str(v))
    tokens = []
    for p in parts:
        t = _TRAILING_MARK.sub("", p.strip()).strip()
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


def load_ground_truth_concepts(xlsx_path: str) -> dict:
    """Read the GT concept xlsx (Document ID, Concept ID, Description, Page
    Number) into {Concept ID -> Description}. This is the authoritative source
    for resolving a GT decision's concept references (e.g. 'CF_M01_C01' ->
    'Security'), replacing the earlier guesswork of reconstructing concept ids
    from repeated `concept` text on the requirement ground truth."""
    df = pd.read_excel(xlsx_path)
    cols = list(df.columns)
    id_col = _pick(cols, ["Concept ID", "ConceptID", "ID", "Id", "id"])
    desc_col = _pick(cols, ["Description", "Name", "Concept"])
    if id_col is None or desc_col is None:
        raise ValueError(f"Could not find Concept ID / Description columns in {cols}")

    id_to_text = {}
    for _, row in df.iterrows():
        cid = row[id_col]
        if _is_blank(cid):
            continue
        name = row[desc_col]
        if not _is_blank(name):
            id_to_text[str(cid).strip()] = norm_text(name)
    return id_to_text


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


def resolve_tokens(tokens: list[str], id_to_text: dict) -> list[str]:
    """Resolve each token to its referenced text; a token that is not a known
    id is kept as its own raw text (handles the ground truth occasionally
    naming an element/requirement directly instead of citing an id)."""
    return [id_to_text.get(t, t) for t in tokens]


def resolved_field_text(raw_value, id_to_text: dict) -> str:
    """Resolve a (possibly multi-token) reference field to ONE string: every
    token is resolved to its referenced text (or kept raw if unresolved) and
    joined, so a decision citing several sources/elements is compared as a
    whole rather than requiring an exact token-for-token correspondence."""
    resolved = resolve_tokens(split_ref_tokens(raw_value), id_to_text)
    parts = [p for p in (norm_text(t) for t in resolved) if p]
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Matching — anchor is the (architecturalDecisionSource, architecturalElementId)
# pair, not the rationale.
# ---------------------------------------------------------------------------
def pair_optimal_match(source_sim: np.ndarray, element_sim: np.ndarray,
                       threshold: float) -> list[tuple[int, int, float]]:
    """Optimal one-to-one assignment of LLM decisions to GT decisions, gated on
    BOTH the resolved-source similarity AND the resolved-element similarity
    clearing `threshold` — the anchor is the PAIR, not either field alone.

    Solved exactly via the Hungarian algorithm: eligible cells are weighted
    1 + average(source_sim, element_sim), so maximising total weight maximises
    the NUMBER of eligible matches first and uses the average similarity only
    as a tie-breaker (same convention as `evaluator_service.optimal_match`).
    """
    n_llm, n_gt = source_sim.shape
    if n_llm == 0 or n_gt == 0:
        return []
    valid = (source_sim >= threshold) & (element_sim >= threshold)
    avg = (source_sim + element_sim) / 2.0
    profit = np.where(valid, 1.0 + avg, 0.0)
    rows, cols = linear_sum_assignment(profit, maximize=True)
    return [(int(i), int(j), float(avg[i, j])) for i, j in zip(rows, cols) if valid[i, j]]


def pair_present(rec: dict) -> bool:
    return not _is_blank(rec.get(SOURCE_SPEC["name"])) and not _is_blank(rec.get(ELEMENT_SPEC["name"]))


# ---------------------------------------------------------------------------
# Per-field agreement (rationale / pageNumber only — see module docstring)
# ---------------------------------------------------------------------------
def field_present(rec: dict, spec: dict) -> bool:
    return not _is_blank(rec.get(spec["name"]))


def field_agrees(spec: dict, llm_rec: dict, gt_rec: dict, threshold: float) -> tuple[bool, float | None]:
    kind = spec["kind"]
    a, b = llm_rec.get(spec["name"]), gt_rec.get(spec["name"])

    if kind == "page":
        pa, pb = page_set(a), page_set(b)
        return (bool(pa) and bool(pb) and bool(pa & pb)), None
    if kind == "semantic":  # rationale
        ta, tb = norm_text(a), norm_text(b)
        sim = text_similarity(ta, tb) if (ta and tb) else 0.0
        return (sim >= threshold), sim
    raise ValueError(f"unknown kind {kind}")


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def build_report(gt, llm, pairs, threshold, *, source_sim, element_sim, closest_sim):
    matched_llm = {i for i, _, _ in pairs}
    matched_gt = {j for _, j, _ in pairs}

    tp, fp, fn = len(pairs), len(llm) - len(pairs), len(gt) - len(pairs)

    # --- Anchor: the (source, element) pair, at the DECISION level -----------
    llm_pair_total = sum(pair_present(r) for r in llm)
    gt_pair_total = sum(pair_present(r) for r in gt)
    pair_precision = (tp / llm_pair_total) if llm_pair_total else None
    pair_recall = (tp / gt_pair_total) if gt_pair_total else None
    mean_source_sim = float(np.mean([source_sim[i, j] for i, j, _ in pairs])) if pairs else None
    mean_element_sim = float(np.mean([element_sim[i, j] for i, j, _ in pairs])) if pairs else None
    mean_pair_sim = float(np.mean([s for _, _, s in pairs])) if pairs else None

    anchor_rows = [{
        "field": "architecturalDecisionSource + architecturalElementId (pair)",
        "precision": _fmt(pair_precision),
        "recall": _fmt(pair_recall),
        "mean semantic meaning": _fmt(mean_pair_sim),
        "mean source similarity": _fmt(mean_source_sim),
        "mean element similarity": _fmt(mean_element_sim),
    }]
    field_metrics_anchor = pd.DataFrame(
        anchor_rows,
        columns=["field", "precision", "recall", "mean semantic meaning",
                "mean source similarity", "mean element similarity"],
    )

    count_rows = [{
        "field": "architecturalDecisionSource + architecturalElementId (pair)",
        "gt_populated_total": gt_pair_total,
        "llm_populated_total": llm_pair_total,
        "gt_populated_matched": sum(pair_present(gt[j]) for _, j, _ in pairs),
        "llm_populated_matched": sum(pair_present(llm[i]) for i, _, _ in pairs),
        "correct_in_matched": tp,
        "matched_pairs (TP)": tp,
    }]

    # --- Other fields: Accuracy over the matched pairs ------------------------
    other_rows = []
    for spec in FIELD_SPECS:
        name = spec["name"]
        llm_has_total = sum(field_present(r, spec) for r in llm)
        gt_has_total = sum(field_present(r, spec) for r in gt)

        correct, sims = 0, []
        llm_has_matched, gt_has_matched = 0, 0
        for i, j, _ in pairs:
            llm_populated = field_present(llm[i], spec)
            gt_populated = field_present(gt[j], spec)
            llm_has_matched += llm_populated
            gt_has_matched += gt_populated
            if not (llm_populated and gt_populated):
                continue
            agree, s = field_agrees(spec, llm[i], gt[j], threshold)
            if agree:
                correct += 1
            if spec["semantic"] and s is not None:
                sims.append(s)

        mean_sem = (float(np.mean(sims)) if sims else None) if spec["semantic"] else None
        accuracy = (correct / llm_has_matched) if llm_has_matched else None
        other_rows.append({
            "field": name,
            "accuracy": _fmt(accuracy),
            "mean semantic meaning": _fmt(mean_sem),
        })
        count_rows.append({
            "field": name,
            "gt_populated_total": gt_has_total,
            "llm_populated_total": llm_has_total,
            "gt_populated_matched": gt_has_matched,
            "llm_populated_matched": llm_has_matched,
            "correct_in_matched": correct,
            "matched_pairs (TP)": tp,
        })

    field_metrics_other = pd.DataFrame(other_rows, columns=["field", "accuracy", "mean semantic meaning"])
    field_counts = pd.DataFrame(count_rows)

    summary = pd.DataFrame([
        {"Metric": "Ground truth count", "Value": len(gt)},
        {"Metric": "LLM extracted count", "Value": len(llm)},
        {"Metric": "True Positives (matched)", "Value": tp},
        {"Metric": "False Positives", "Value": fp},
        {"Metric": "False Negatives", "Value": fn},
        {"Metric": "Decision precision (by source+element pair)", "Value": _fmt(pair_precision)},
        {"Metric": "Decision recall (by source+element pair)", "Value": _fmt(pair_recall)},
        {"Metric": "Match threshold (cosine)", "Value": threshold},
    ])

    def fields_blob(rec, prefix):
        return {f"{prefix}_{s['name']}": rec.get(s["name"]) for s in (ANCHOR_SPECS + FIELD_SPECS)}

    matched = pd.DataFrame([
        {"LLM_ID": llm[i]["id"], "GT_ID": gt[j]["id"],
         "source_similarity": round(float(source_sim[i, j]), 4),
         "element_similarity": round(float(element_sim[i, j]), 4),
         **fields_blob(llm[i], "LLM"), **fields_blob(gt[j], "GT")}
        for i, j, s in sorted(pairs, key=lambda x: x[2])
    ])

    fps = pd.DataFrame([
        {"LLM_ID": llm[i]["id"], **fields_blob(llm[i], "LLM"),
         "closest_GT_ID": gt[int(closest_sim[i].argmax())]["id"] if len(gt) else None,
         "closest_similarity": round(float(closest_sim[i].max()), 4) if len(gt) else 0.0}
        for i in range(len(llm)) if i not in matched_llm
    ])

    fns = pd.DataFrame([
        {"GT_ID": gt[j]["id"], **fields_blob(gt[j], "GT"),
         "closest_LLM_ID": llm[int(closest_sim[:, j].argmax())]["id"] if len(llm) else None,
         "closest_similarity": round(float(closest_sim[:, j].max()), 4) if len(llm) else 0.0}
        for j in range(len(gt)) if j not in matched_gt
    ])

    return {
        "Field_Metrics_Anchor": field_metrics_anchor,
        "Field_Metrics_Other": field_metrics_other,
        "Matching_Summary": summary,
        "Field_Counts": field_counts,
        "Matched_TP": matched,
        "False_Positives": fps,
        "False_Negatives": fns,
        "stats": {"tp": tp, "fp": fp, "fn": fn},
    }


def evaluate_decisions(gt_decision_path, llm_decision_path,
                       gt_architecture_path, llm_architecture_path,
                       gt_requirement_path, gt_concept_path,
                       llm_requirement_path, llm_concepts_path,
                       output_path, threshold=0.75):
    gt = load_ground_truth_decisions(gt_decision_path)
    llm = load_llm_decisions(llm_decision_path)
    if not gt or not llm:
        raise ValueError(f"Empty input — gt={len(gt)} llm={len(llm)}")

    # Resolvers: an architecturalElementId/architecturalDecisionSource is a
    # reference into another already-extracted artifact, so it is resolved to
    # human-readable text through THAT artifact's own ground truth/LLM output,
    # rather than compared as a raw id (the two sides use unrelated id
    # namespaces). The source side merges the requirement ground truth
    # (Requirement ID -> text) with the dedicated concept ground truth
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

    gt_source_texts = [resolved_field_text(r.get(SOURCE_SPEC["name"]), source_gt_idx) for r in gt]
    llm_source_texts = [resolved_field_text(r.get(SOURCE_SPEC["name"]), source_llm_idx) for r in llm]
    gt_element_texts = [resolved_field_text(r.get(ELEMENT_SPEC["name"]), element_gt_idx) for r in gt]
    llm_element_texts = [resolved_field_text(r.get(ELEMENT_SPEC["name"]), element_llm_idx) for r in llm]

    source_sim = compute_similarity(gt_source_texts, llm_source_texts)    # shape (n_llm, n_gt)
    element_sim = compute_similarity(gt_element_texts, llm_element_texts)  # shape (n_llm, n_gt)
    closest_sim = (source_sim + element_sim) / 2.0  # for FP/FN "closest counterpart" diagnostics

    pairs = pair_optimal_match(source_sim, element_sim, threshold)

    report = build_report(gt, llm, pairs, threshold,
                          source_sim=source_sim, element_sim=element_sim, closest_sim=closest_sim)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        anchor_df = report["Field_Metrics_Anchor"]
        other_df = report["Field_Metrics_Other"]
        anchor_df.to_excel(w, sheet_name="Field_Metrics", index=False, startrow=0)
        other_start = len(anchor_df) + 3
        other_df.to_excel(w, sheet_name="Field_Metrics", index=False, startrow=other_start)
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
