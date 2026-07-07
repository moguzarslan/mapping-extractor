"""
Evaluate LLM-extracted requirements against a ground-truth Excel, per field.

Usage:
    python evaluate.py <ground_truth.xlsx> <llm_extraction.json> <output_report.xlsx> [threshold]

Defaults:
    threshold = 0.75  (embedding cosine similarity below this is not a match)

What it does
------------
1. Matches each LLM-extracted requirement to at most one ground-truth (GT)
   requirement using the *description* text (cosine similarity of sentence
   embeddings, greedy one-to-one assignment at `threshold`). This produces the
   true positives (TP) the per-field scoring is built on.
2. For each of seven fields, reports Precision, Recall, and Mean Semantic
   Meaning:

       field           precision   recall   mean semantic meaning
       type               x          x              -
       pageNumber         x          x              -
       description        x          x              x
       concept            x          x              -
       categorization     x          x              -
       relatedTo          x          x              -
       fixes              x          x              x

   - Mean Semantic Meaning is computed ONLY for `description` and `fixes`
     (cosine similarity of the matched pair's values). For every other field
     the cell is "-".
   - Precision/Recall are computed for every field except `id` (id is the key
     used to resolve cross-references, not a scored field).
   - When a field is not populated on either side at all (e.g. nobody has
     `fixes`), its metrics are "-".

Per-field metric definitions
----------------------------
For a field F, over the matched (TP) requirement pairs:
    correct_F  = TP pairs where F is present on BOTH sides AND the values agree

`description` (the matching anchor) is scored at the REQUIREMENT level, so its
denominators span ALL items (matched + unmatched):
    llm_has_F  = number of LLM items with F populated
    gt_has_F   = number of GT items with F populated

Every OTHER field is scored as a success rate over the matched (TP) pairs —
unmatched items are already penalised by the description anchor. Both precision
and recall use `tp` as the denominator, so they are equal and represent the
fraction of matched pairs where the field was correctly identified:

    Precision_F = Recall_F = correct_F / tp   ("-" if tp == 0)

"Agreement" per field:
    type, categorization   case-insensitive exact match
    pageNumber             integer equality
    concept                normalized match (leading "10a."/"12th." labels stripped)
    description, fixes      cosine similarity >= threshold
    relatedTo              both criteria point at the *same* parent requirement,
                           resolved through the matching (so the differing
                           LLM vs GT id namespaces never get compared directly)

Because `description` is the matching anchor, its Precision/Recall equal the
requirement-level Precision/Recall, and its Mean Semantic Meaning is the mean
similarity of the matched pairs.

Output: a single compacted xlsx with four sheets — Metrics (requirement-level
summary stacked on the per-field metrics table), Matched, False_Positives,
False_Negatives. A side-car `<stem>_by_type.xlsx` is also written with a
Type_Breakdown sheet: per requirement-`type` value (e.g. Functional,
Non-Functional), the successful extractions (TP), false positives, false
negatives, precision, recall and F1 — ordered most successful (highest F1)
to least, so the weakest-performing requirement types stand out.
"""
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment


# ---------------------------------------------------------------------------
# Field specification
# ---------------------------------------------------------------------------
# Each canonical field maps to candidate JSON keys, candidate GT column names,
# a comparison "kind", and which of the three metrics apply to it.
# Order here is the row order of the final 7x3 table.
FIELD_SPECS = [
    {"name": "type",           "json": ["type", "requirementType", "category", "Category"],
     "gt": ["Category", "Type", "Requirement Type"],          "kind": "categorical", "semantic": False},
    {"name": "pageNumber",     "json": ["pageNumber", "page", "pageNo", "page_number"],
     "gt": ["Page Number", "PageNumber", "Page"],             "kind": "page",        "semantic": False},
    {"name": "description",    "json": ["description", "requirement", "text", "Requirement"],
     "gt": ["Requirement", "Description", "Text"],            "kind": "semantic",    "semantic": True},
    {"name": "concept",        "json": ["concept", "Concept"],
     "gt": ["Concept"],                                       "kind": "concept",     "semantic": False},
    {"name": "categorization", "json": ["categorization", "Categorization"],
     "gt": ["Categorization"],                                "kind": "categorical", "semantic": False},
    {"name": "relatedTo",      "json": ["relatedTo", "related_to", "RelatedTo", "relatedto"],
     "gt": ["Related to", "RelatedTo", "Related To"],         "kind": "related",     "semantic": False},
    {"name": "fixes",          "json": ["fixes", "appliedFixes", "Applied Fixes"],
     "gt": ["Applied Fixes", "Fixes", "AppliedFixes"],        "kind": "list",        "semantic": True},
]

ID_JSON_CANDIDATES = ["id", "ID", "requirement_id", "Requirement ID"]
ID_GT_CANDIDATES   = ["Requirement ID", "ID", "Req ID", "RequirementID", "id"]
# A description column is required to anchor matching.
DESC_REQUIRED_NAME = "description"

_CONCEPT_PREFIX = re.compile(r"^\s*\d+\s*[a-z]{0,3}\.?\s*", re.IGNORECASE)
_REQ_TOKEN      = re.compile(r"R[\s_]*0*(\d+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Value normalisation / presence
# ---------------------------------------------------------------------------
def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and np.isnan(v):
        return True
    if isinstance(v, (list, tuple, dict)):
        return len(v) == 0
    if isinstance(v, str):
        return v.strip() == ""
    return False


def norm_text(v):
    return None if _is_blank(v) else str(v).strip()


def norm_categorical(v):
    return None if _is_blank(v) else str(v).strip().lower()


def norm_page(v):
    if _is_blank(v):
        return None
    s = str(v).strip()
    try:
        return str(int(float(s)))
    except ValueError:
        return s.lower()


def norm_concept(v):
    if _is_blank(v):
        return None
    return _CONCEPT_PREFIX.sub("", str(v).strip()).strip().lower()


def fixes_to_text(v):
    if _is_blank(v):
        return None
    if isinstance(v, (list, tuple)):
        parts = [str(x).strip() for x in v if not _is_blank(x)]
        return " ".join(parts) if parts else None
    return str(v).strip()


def req_token(v):
    """Trailing requirement number token, e.g. 'CF_M05_R08' -> '8'. None if absent."""
    if _is_blank(v):
        return None
    matches = _REQ_TOKEN.findall(str(v))
    return matches[-1] if matches else None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _pick(colnames, candidates):
    for c in candidates:
        if c in colnames:
            return c
    return None


def load_ground_truth(xlsx_path: str) -> list[dict]:
    """Read GT xlsx into canonical records; merge wrapped continuation rows."""
    df = pd.read_excel(xlsx_path)
    cols = list(df.columns)

    id_col   = _pick(cols, ID_GT_CANDIDATES)
    desc_col = _pick(cols, next(s for s in FIELD_SPECS if s["name"] == "description")["gt"])
    if desc_col is None:
        raise ValueError(f"Could not find a requirement/description column in {cols}")

    field_cols = {s["name"]: _pick(cols, s["gt"]) for s in FIELD_SPECS}

    records = []
    for _, row in df.iterrows():
        id_val = row[id_col] if id_col else None
        if id_col is not None and not _is_blank(id_val):
            rec = {"id": str(id_val).strip(), "_raw": {}}
            for s in FIELD_SPECS:
                col = field_cols[s["name"]]
                rec[s["name"]] = row[col] if (col and not _is_blank(row[col])) else None
            records.append(rec)
        else:
            # Continuation row: blank ID, more description text for the previous row.
            if not _is_blank(row[desc_col]) and records:
                prev = records[-1]
                prev["description"] = f"{prev.get('description') or ''} {str(row[desc_col]).strip()}".strip()
    return records


def load_llm_extraction(json_path_or_data) -> list[dict]:
    """Read LLM JSON (list or {requirements: [...]}) into canonical records.

    Accepts either a path to a JSON file or already-parsed data (a list or
    dict), so the requirements pipeline can evaluate its in-memory result
    without round-tripping it through an intermediate file on disk.
    """
    if isinstance(json_path_or_data, (list, dict)):
        data = json_path_or_data
    else:
        with open(json_path_or_data) as f:
            data = json.load(f)
    items = data.get("requirements", data) if isinstance(data, dict) else data
    if not items:
        return []

    def get(item, candidates):
        for k in candidates:
            if k in item and not _is_blank(item[k]):
                return item[k]
        return None

    records = []
    for item in items:
        id_val = get(item, ID_JSON_CANDIDATES)
        rec = {"id": str(id_val).strip() if id_val is not None else "", "_raw": item}
        for s in FIELD_SPECS:
            rec[s["name"]] = get(item, s["json"])
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Matching (on description)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Semantic similarity via sentence embeddings
# ---------------------------------------------------------------------------
# Matching is anchored on the cosine similarity of sentence embeddings rather
# than lexical TF-IDF, so paraphrases ("System saves the playlogs" vs "The
# system shall store playlogs") are recognised as the same requirement instead
# of being split into a false positive plus a false negative. Embeddings are
# served by the same Vertex AI client the extraction pipeline uses.
EMBED_MODEL  = os.getenv("EVAL_EMBEDDING_MODEL", "text-embedding-005")
EMBED_TASK   = os.getenv("EVAL_EMBEDDING_TASK", "SEMANTIC_SIMILARITY")
_EMBED_BATCH = 100

_embed_client = None
_embed_cache: dict[str, np.ndarray] = {}
_embed_dim: int | None = None


def _get_embed_client():
    global _embed_client
    if _embed_client is None:
        from infra.gemini_client import create_gemini_client
        _embed_client = create_gemini_client()
    return _embed_client


def _embed_texts(texts: list[str]) -> np.ndarray:
    """Return an L2-normalised embedding matrix (len(texts) x dim).

    Blank texts map to a zero vector (cosine 0 against anything). Embeddings are
    cached per unique text, so identical descriptions and repeated GUI
    re-matching do not re-hit the API.
    """
    global _embed_dim
    todo = [t for t in dict.fromkeys(texts) if t and t not in _embed_cache]
    if todo:
        client = _get_embed_client()
        for i in range(0, len(todo), _EMBED_BATCH):
            batch = todo[i:i + _EMBED_BATCH]
            try:
                resp = client.models.embed_content(
                    model=EMBED_MODEL, contents=batch,
                    config={"task_type": EMBED_TASK},
                )
            except Exception as e:  # noqa: BLE001 - surface a clear, actionable error
                raise RuntimeError(
                    f"Embedding request failed for model '{EMBED_MODEL}'. Vertex AI "
                    f"credentials must be configured (ADC / GOOGLE_CLOUD_PROJECT — see "
                    f"infra/gemini_client.py). Original error: {e}"
                ) from e
            for t, emb in zip(batch, resp.embeddings):
                v = np.asarray(emb.values, dtype=float)
                norm = np.linalg.norm(v)
                _embed_cache[t] = v / norm if norm else v
                _embed_dim = v.shape[0]

    dim = _embed_dim or 768
    out = np.zeros((len(texts), dim))
    for k, t in enumerate(texts):
        if t and t in _embed_cache:
            out[k] = _embed_cache[t]
    return out


def compute_similarity(gt_texts: list[str], llm_texts: list[str]) -> np.ndarray:
    """Cosine similarity (LLM rows x GT cols) of sentence embeddings."""
    if not gt_texts or not llm_texts:
        return np.zeros((len(llm_texts), len(gt_texts)))
    Eg = _embed_texts(list(gt_texts))
    El = _embed_texts(list(llm_texts))
    return El @ Eg.T  # vectors are L2-normalised, so dot product == cosine


def text_similarity(a: str, b: str) -> float:
    """Standalone pairwise embedding cosine similarity (used for fixes text)."""
    if _is_blank(a) or _is_blank(b):
        return 0.0
    try:
        m = compute_similarity([a], [b])
        return float(m[0, 0])
    except ValueError:
        return 0.0


def greedy_match(sim: np.ndarray, threshold: float,
                 llm_types: list[str | None] | None = None,
                 gt_types: list[str | None] | None = None,
                 type_override_sim: float = 0.9) -> list[tuple[int, int, float]]:
    """One-to-one greedy assignment over similarity-sorted pairs, score >= threshold.

    If llm_types and gt_types are provided, a pair (i, j) is only considered
    when both sides have the same type (case-insensitive), or when at least one
    side has no type information (treated as a wildcard so untyped requirements
    are still matchable).

    `type_override_sim` relaxes that gate: when the description similarity of a
    pair is at least this value, the type check is bypassed. A near-exact text
    match almost always denotes the same requirement, so a differing type label
    (e.g. a GT acceptance "criterion" vs an LLM "QR" that absorbed it, or a split
    child that inherited its parent's type) must not veto an otherwise perfect
    match. Set to a value > 1.0 to disable the override and always enforce types.
    """
    n_llm, n_gt = sim.shape
    check_types = llm_types is not None and gt_types is not None

    def types_compatible(i: int, j: int, score: float) -> bool:
        if not check_types:
            return True
        if score >= type_override_sim:
            return True      # near-exact text match overrides a differing type label
        lt = llm_types[i]   # type: ignore[index]
        gt = gt_types[j]    # type: ignore[index]
        if lt is None or gt is None:
            return True      # wildcard: one side has no type, allow the pair
        return norm_categorical(lt) == norm_categorical(gt)

    candidates = sorted(
        ((sim[i, j], i, j) for i in range(n_llm) for j in range(n_gt)),
        reverse=True,
    )
    used_llm, used_gt, pairs = set(), set(), []
    for score, i, j in candidates:
        if score < threshold:
            break
        if i in used_llm or j in used_gt:
            continue
        if not types_compatible(i, j, score):
            continue
        pairs.append((i, j, float(score)))
        used_llm.add(i)
        used_gt.add(j)
    return pairs


def optimal_match(sim: np.ndarray, threshold: float,
                  llm_types: list[str | None] | None = None,
                  gt_types: list[str | None] | None = None,
                  type_override_sim: float = 0.9) -> list[tuple[int, int, float]]:
    """Optimal one-to-one assignment with the same gating as `greedy_match`.

    Identical contract and gates as `greedy_match` (a pair is eligible only when
    its score >= threshold and the types are compatible, with the same
    `type_override_sim` relaxation), but solved exactly with the Hungarian
    algorithm instead of the greedy similarity-sorted walk. Each eligible pair is
    weighted 1 + score and every other cell 0, so the assignment maximises the
    NUMBER of eligible matches first and uses similarity only as the tie-breaker.

    Because it is the exact solution to the assignment problem greedy approximates,
    the matched count is always >= greedy's: it never drops a valid >= threshold
    match because a higher-similarity pair stole a node the match needed.
    """
    n_llm, n_gt = sim.shape
    if n_llm == 0 or n_gt == 0:
        return []
    check_types = llm_types is not None and gt_types is not None

    def types_compatible(i: int, j: int, score: float) -> bool:
        if not check_types:
            return True
        if score >= type_override_sim:
            return True
        lt = llm_types[i]   # type: ignore[index]
        gt = gt_types[j]    # type: ignore[index]
        if lt is None or gt is None:
            return True
        return norm_categorical(lt) == norm_categorical(gt)

    # Eligible cells weigh 1 + score (>= 1) so maximising total weight maximises
    # the count of eligible matches first; ineligible cells weigh 0 and are
    # dropped from the result after the assignment.
    valid = np.zeros((n_llm, n_gt), dtype=bool)
    profit = np.zeros((n_llm, n_gt), dtype=float)
    for i in range(n_llm):
        for j in range(n_gt):
            s = float(sim[i, j])
            if s >= threshold and types_compatible(i, j, s):
                valid[i, j] = True
                profit[i, j] = 1.0 + s

    rows, cols = linear_sum_assignment(profit, maximize=True)
    return [(int(i), int(j), float(sim[i, j]))
            for i, j in zip(rows, cols) if valid[i, j]]


# ---------------------------------------------------------------------------
# Per-field agreement
# ---------------------------------------------------------------------------
def build_id_index(records: list[dict]) -> dict:
    """Map id -> index, with a fallback by trailing requirement-number token."""
    exact = {r["id"]: i for i, r in enumerate(records) if r["id"]}
    token_map: dict[str, list[int]] = {}
    for i, r in enumerate(records):
        t = req_token(r["id"])
        if t is not None:
            token_map.setdefault(t, []).append(i)
    return {"exact": exact, "token": token_map}


def resolve_id(index: dict, value) -> int | None:
    """Resolve a (possibly foreign-formatted) id reference to a record index."""
    if _is_blank(value):
        return None
    v = str(value).strip()
    if v in index["exact"]:
        return index["exact"][v]
    t = req_token(v)
    if t is not None and len(index["token"].get(t, [])) == 1:
        return index["token"][t][0]
    return None


def field_present(rec: dict, spec: dict) -> bool:
    return not _is_blank(rec.get(spec["name"]))


def field_agrees(spec: dict, llm_rec: dict, gt_rec: dict, threshold: float,
                 *, llm_idx: dict, gt_idx: dict, llm_to_gt: dict) -> tuple[bool, float | None]:
    """Return (agrees, similarity_or_None) for one matched pair on one field."""
    kind = spec["kind"]
    a, b = llm_rec.get(spec["name"]), gt_rec.get(spec["name"])

    if kind == "categorical":
        return (norm_categorical(a) == norm_categorical(b)), None
    if kind == "page":
        return (norm_page(a) == norm_page(b)), None
    if kind == "concept":
        return (norm_concept(a) == norm_concept(b)), None
    if kind == "semantic":  # description anchor
        ta, tb = norm_text(a), norm_text(b)
        sim = text_similarity(ta, tb) if (ta and tb) else 0.0
        return (sim >= threshold), sim
    if kind == "list":  # fixes
        ta, tb = fixes_to_text(a), fixes_to_text(b)
        sim = text_similarity(ta, tb) if (ta and tb) else 0.0
        return (sim >= threshold), sim
    if kind == "related":
        # LLM-predicted parent -> resolve within LLM list -> map to a GT index.
        p = resolve_id(llm_idx, a)
        gt_pred_parent = llm_to_gt.get(p) if p is not None else None
        # GT-stated parent -> resolve within GT list.
        gt_true_parent = resolve_id(gt_idx, b)
        agree = (gt_pred_parent is not None and gt_pred_parent == gt_true_parent)
        return agree, None
    raise ValueError(f"unknown kind {kind}")


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def _fmt(x):
    return "-" if x is None else round(float(x), 4)


def build_type_breakdown(gt: list[dict], llm: list[dict],
                         pairs: list[tuple[int, int, float]],
                         type_field: str = "type") -> pd.DataFrame:
    """Per-type Precision/Recall/F1, ranked most successful (highest F1) first.

    Follows the multi-class `classification_report` convention: a matched pair
    counts as a successful extraction (TP) for a type only when BOTH sides
    agree on that type. A matched pair with disagreeing types therefore counts
    as a false negative for the GT's type and a false positive for the LLM's
    type, and an unmatched item falls back to its own recorded type. Items
    with no value in `type_field` are grouped under "(unspecified)".
    """
    llm_to_gt = {i: j for i, j, _ in pairs}
    gt_to_llm = {j: i for i, j, _ in pairs}

    def type_of(rec):
        return norm_categorical(rec.get(type_field)) or "(unspecified)"

    counts: dict[str, dict[str, int]] = {}

    def bump(t, key):
        counts.setdefault(t, {"tp": 0, "fp": 0, "fn": 0, "gt_count": 0, "llm_count": 0})[key] += 1

    for j, rec in enumerate(gt):
        t = type_of(rec)
        bump(t, "gt_count")
        i = gt_to_llm.get(j)
        if i is not None and type_of(llm[i]) == t:
            bump(t, "tp")
        else:
            bump(t, "fn")

    for i, rec in enumerate(llm):
        t = type_of(rec)
        bump(t, "llm_count")
        j = llm_to_gt.get(i)
        if not (j is not None and type_of(gt[j]) == t):
            bump(t, "fp")

    rows = []
    for t, c in counts.items():
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        precision = (tp / (tp + fp)) if (tp + fp) else None
        recall = (tp / (tp + fn)) if (tp + fn) else None
        f1 = (2 * precision * recall / (precision + recall)) if (precision and recall) else None
        rows.append({
            "type": t,
            "gt_count": c["gt_count"],
            "llm_count": c["llm_count"],
            "successful_extractions": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": _fmt(precision),
            "recall": _fmt(recall),
            "f1": _fmt(f1),
        })

    rows.sort(key=lambda r: (r["f1"] if isinstance(r["f1"], (int, float)) else -1,
                             r["successful_extractions"]), reverse=True)
    return pd.DataFrame(rows, columns=["type", "gt_count", "llm_count", "successful_extractions",
                                       "false_positives", "false_negatives",
                                       "precision", "recall", "f1"])


def build_report(gt, llm, sim, pairs, threshold, forced_pairs=None):
    """Assemble the evaluation report from a set of matched pairs.

    `forced_pairs` is an optional set of (llm_idx, gt_idx) that were matched by a
    human (e.g. through the GUI) rather than by the automatic matcher. For such
    pairs the *description anchor* is counted as agreeing regardless of its
    TF-IDF similarity, because the human has asserted the two requirements
    correspond. All other fields are still compared on their real values.
    """
    forced_pairs = forced_pairs or set()
    matched_llm = {i for i, _, _ in pairs}
    matched_gt  = {j for _, j, _ in pairs}
    llm_to_gt   = {i: j for i, j, _ in pairs}

    tp, fp, fn = len(pairs), len(llm) - len(pairs), len(gt) - len(pairs)

    llm_idx = build_id_index(llm)
    gt_idx  = build_id_index(gt)

    field_rows, count_rows = [], []
    for spec in FIELD_SPECS:
        name = spec["name"]
        llm_has_total = sum(field_present(r, spec) for r in llm)
        gt_has_total  = sum(field_present(r, spec) for r in gt)

        correct, sims = 0, []
        llm_has_matched, gt_has_matched = 0, 0
        for i, j, _ in pairs:
            llm_populated = field_present(llm[i], spec)
            gt_populated  = field_present(gt[j], spec)
            if llm_populated:
                llm_has_matched += 1
            if gt_populated:
                gt_has_matched += 1
            if not (llm_populated and gt_populated):
                continue
            agree, s = field_agrees(spec, llm[i], gt[j], threshold,
                                    llm_idx=llm_idx, gt_idx=gt_idx, llm_to_gt=llm_to_gt)
            # A human-forced match overrides the TF-IDF threshold on the
            # description anchor: the pair is a deliberate true correspondence.
            if name == DESC_REQUIRED_NAME and (i, j) in forced_pairs:
                agree = True
            if agree:
                correct += 1
            if spec["semantic"] and s is not None:
                sims.append(s)

        if name == DESC_REQUIRED_NAME:
            # Description is the matching anchor: score it at the requirement
            # level, so its denominators span ALL items (matched + unmatched).
            llm_den, gt_den = llm_has_total, gt_has_total
        else:
            # Every other field is scored as a success rate over the matched
            # pairs (TP): correct / tp, so precision == recall == success rate.
            # Unmatched items are already penalised via the description anchor.
            llm_den, gt_den = llm_has_matched, llm_has_matched

        precision = (correct / llm_den) if llm_den else None
        recall    = (correct / gt_den) if gt_den else None
        mean_sem  = (float(np.mean(sims)) if sims else None) if spec["semantic"] else None

        field_rows.append({
            "field": name,
            "precision": _fmt(precision),
            "recall": _fmt(recall),
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

    desc_rows  = [r for r in field_rows if r["field"] == DESC_REQUIRED_NAME]
    other_rows = [{"field": r["field"], "accuracy": r["precision"]}
                  for r in field_rows if r["field"] != DESC_REQUIRED_NAME]
    field_metrics_desc  = pd.DataFrame(desc_rows,  columns=["field", "precision", "recall", "mean semantic meaning"])
    field_metrics_other = pd.DataFrame(other_rows, columns=["field", "accuracy"])
    field_metrics = pd.DataFrame(field_rows, columns=["field", "precision", "recall", "mean semantic meaning"])
    field_counts  = pd.DataFrame(count_rows)

    req_precision = (tp / (tp + fp)) if (tp + fp) else None
    req_recall    = (tp / (tp + fn)) if (tp + fn) else None
    req_summary = pd.DataFrame([
        {"Metric": "Ground truth count",     "Value": len(gt)},
        {"Metric": "LLM extracted count",    "Value": len(llm)},
        {"Metric": "True Positives (matched)", "Value": tp},
        {"Metric": "False Positives",        "Value": fp},
        {"Metric": "False Negatives",        "Value": fn},
        {"Metric": "Requirement precision",  "Value": _fmt(req_precision)},
        {"Metric": "Requirement recall",     "Value": _fmt(req_recall)},
        {"Metric": "Match threshold (cosine)", "Value": threshold},
    ])

    def fields_blob(rec, prefix):
        return {f"{prefix}_{s['name']}": (fixes_to_text(rec.get(s['name']))
                                          if s['name'] == 'fixes' else rec.get(s['name']))
                for s in FIELD_SPECS}

    matched = pd.DataFrame([
        {"LLM_ID": llm[i]["id"], "GT_ID": gt[j]["id"], "desc_similarity": round(s, 4),
         **fields_blob(llm[i], "LLM"), **fields_blob(gt[j], "GT")}
        for i, j, s in sorted(pairs, key=lambda x: x[2])
    ])

    fps = pd.DataFrame([
        {"LLM_ID": llm[i]["id"], "LLM_type": llm[i].get("type"),
         "LLM_description": llm[i].get("description"),
         "closest_GT_ID": gt[int(sim[i].argmax())]["id"],
         "closest_GT_description": gt[int(sim[i].argmax())].get("description"),
         "similarity": round(float(sim[i].max()), 4)}
        for i in range(len(llm)) if i not in matched_llm
    ])

    fns = pd.DataFrame([
        {"GT_ID": gt[j]["id"], "GT_type": gt[j].get("type"),
         "GT_description": gt[j].get("description"),
         "closest_LLM_ID": llm[int(sim[:, j].argmax())]["id"],
         "closest_LLM_description": llm[int(sim[:, j].argmax())].get("description"),
         "similarity": round(float(sim[:, j].max()), 4)}
        for j in range(len(gt)) if j not in matched_gt
    ])

    type_breakdown = build_type_breakdown(gt, llm, pairs)

    return {
        "Field_Metrics_Desc": field_metrics_desc,
        "Field_Metrics_Other": field_metrics_other,
        "Field_Metrics": field_metrics,
        "Field_Counts": field_counts,
        "Requirement_Matching": req_summary,
        "Matched_TP": matched,
        "False_Positives": fps,
        "False_Negatives": fns,
        "Type_Breakdown": type_breakdown,
        "stats": {"tp": tp, "fp": fp, "fn": fn, "field_metrics": field_rows},
    }


def write_report(report: dict, output_path) -> None:
    """Write the compacted requirement evaluation workbook — a single file with
    four sheets: Metrics, Matched, False_Positives, False_Negatives.

    The Metrics sheet stacks the requirement-level summary (counts, precision,
    recall, threshold) on top of the per-field metrics table.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = report["Requirement_Matching"]
    field_metrics = report["Field_Metrics"]
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="Metrics", index=False, startrow=0)
        field_metrics.to_excel(w, sheet_name="Metrics", index=False,
                               startrow=len(summary) + 3)  # one blank row separator
        report["Matched_TP"].to_excel(w, sheet_name="Matched", index=False)
        report["False_Positives"].to_excel(w, sheet_name="False_Positives", index=False)
        report["False_Negatives"].to_excel(w, sheet_name="False_Negatives", index=False)


def write_type_breakdown_report(report: dict, output_path) -> None:
    """Write the per-type breakdown as its own single-sheet workbook, named
    `<stem>_by_type.xlsx` next to the main report (same side-car convention
    the architecture evaluator uses for its GT/LLM reports)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        report["Type_Breakdown"].to_excel(w, sheet_name="Type_Breakdown", index=False)


def evaluate(gt_path, llm_path, output_path, threshold=0.75):
    gt  = load_ground_truth(gt_path)
    llm = load_llm_extraction(llm_path)
    if not gt or not llm:
        raise ValueError(f"Empty input — gt={len(gt)} llm={len(llm)}")

    sim   = compute_similarity([norm_text(r["description"]) or "" for r in gt],
                               [norm_text(r["description"]) or "" for r in llm])

    type_spec = next(s for s in FIELD_SPECS if s["name"] == "type")
    llm_types = [norm_categorical(r.get(type_spec["name"])) for r in llm]
    gt_types  = [norm_categorical(r.get(type_spec["name"])) for r in gt]
    pairs = greedy_match(sim, threshold, llm_types=llm_types, gt_types=gt_types)
    report = build_report(gt, llm, sim, pairs, threshold)
    write_report(report, output_path)

    type_breakdown_path = Path(output_path).with_name(Path(output_path).stem + "_by_type.xlsx")
    write_type_breakdown_report(report, type_breakdown_path)
    return report


