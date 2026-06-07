"""
Evaluate LLM-extracted requirements against a ground-truth Excel, per field.

Usage:
    python evaluate.py <ground_truth.xlsx> <llm_extraction.json> <output_report.xlsx> [threshold]

Defaults:
    threshold = 0.35  (cosine similarity below this is not a match)

What it does
------------
1. Matches each LLM-extracted requirement to at most one ground-truth (GT)
   requirement using the *description* text (combined word + char TF-IDF,
   greedy one-to-one assignment at `threshold`). This produces the true
   positives (TP) the per-field scoring is built on.
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

Output: an xlsx with sheets — Field_Metrics (the 7x3 table), Field_Counts,
Requirement_Matching, Matched_TP, False_Positives, False_Negatives.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


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


def load_llm_extraction(json_path: str) -> list[dict]:
    """Read LLM JSON (list or {requirements: [...]}) into canonical records."""
    with open(json_path) as f:
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
def compute_similarity(gt_texts: list[str], llm_texts: list[str]) -> np.ndarray:
    """Cosine similarity (LLM rows x GT cols): elementwise max of word & char TF-IDF."""
    all_texts = gt_texts + llm_texts
    n_gt = len(gt_texts)

    v_word = TfidfVectorizer(lowercase=True, stop_words="english", ngram_range=(1, 2), min_df=1)
    v_char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), lowercase=True)

    Xw = v_word.fit_transform(all_texts)
    Xc = v_char.fit_transform(all_texts)

    sim_w = cosine_similarity(Xw[n_gt:], Xw[:n_gt])
    sim_c = cosine_similarity(Xc[n_gt:], Xc[:n_gt])
    return np.maximum(sim_w, sim_c)


def text_similarity(a: str, b: str) -> float:
    """Standalone pairwise similarity for fixes text (same blended TF-IDF idea)."""
    if _is_blank(a) or _is_blank(b):
        return 0.0
    try:
        m = compute_similarity([a], [b])
        return float(m[0, 0])
    except ValueError:
        return 0.0


def greedy_match(sim: np.ndarray, threshold: float,
                 llm_types: list[str | None] | None = None,
                 gt_types: list[str | None] | None = None) -> list[tuple[int, int, float]]:
    """One-to-one greedy assignment over similarity-sorted pairs, score >= threshold.

    If llm_types and gt_types are provided, a pair (i, j) is only considered
    when both sides have the same type (case-insensitive), or when at least one
    side has no type information (treated as a wildcard so untyped requirements
    are still matchable).
    """
    n_llm, n_gt = sim.shape
    check_types = llm_types is not None and gt_types is not None

    def types_compatible(i: int, j: int) -> bool:
        if not check_types:
            return True
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
        if not types_compatible(i, j):
            continue
        pairs.append((i, j, float(score)))
        used_llm.add(i)
        used_gt.add(j)
    return pairs


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


def build_report(gt, llm, sim, pairs, threshold):
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
    field_counts  = pd.DataFrame(count_rows)

    req_summary = pd.DataFrame([
        {"Metric": "Ground truth count",     "Value": len(gt)},
        {"Metric": "LLM extracted count",    "Value": len(llm)},
        {"Metric": "True Positives (matched)", "Value": tp},
        {"Metric": "False Positives",        "Value": fp},
        {"Metric": "False Negatives",        "Value": fn},
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

    gt_report = pd.DataFrame([
        {
            "GT_ID": gt[j]["id"],
            "GT_description": gt[j].get("description"),
            "GT_type": gt[j].get("type"),
            "GT_pageNumber": gt[j].get("pageNumber"),
            "GT_concept": gt[j].get("concept"),
            "GT_categorization": gt[j].get("categorization"),
            "GT_relatedTo": gt[j].get("relatedTo"),
            "GT_fixes": fixes_to_text(gt[j].get("fixes")),
            "closest_LLM_ID": llm[int(sim[:, j].argmax())]["id"],
            "closest_LLM_description": llm[int(sim[:, j].argmax())].get("description"),
            "best_similarity": round(float(sim[:, j].max()), 4),
        }
        for j in range(len(gt)) if j not in matched_gt
    ])

    llm_report = pd.DataFrame([
        {
            "LLM_ID": llm[i]["id"],
            "LLM_description": llm[i].get("description"),
            "LLM_type": llm[i].get("type"),
            "LLM_pageNumber": llm[i].get("pageNumber"),
            "LLM_concept": llm[i].get("concept"),
            "LLM_categorization": llm[i].get("categorization"),
            "LLM_relatedTo": llm[i].get("relatedTo"),
            "LLM_fixes": fixes_to_text(llm[i].get("fixes")),
            "closest_GT_ID": gt[int(sim[i].argmax())]["id"],
            "closest_GT_description": gt[int(sim[i].argmax())].get("description"),
            "best_similarity": round(float(sim[i].max()), 4),
        }
        for i in range(len(llm)) if i not in matched_llm
    ])

    return {
        "Field_Metrics_Desc": field_metrics_desc,
        "Field_Metrics_Other": field_metrics_other,
        "Field_Counts": field_counts,
        "Requirement_Matching": req_summary,
        "Matched_TP": matched,
        "False_Positives": fps,
        "False_Negatives": fns,
        "LLM_Requirements_Report": llm_report,
        "GT_Requirements_Report": gt_report,
        "stats": {"tp": tp, "fp": fp, "fn": fn, "field_metrics": field_rows},
    }


def evaluate(gt_path, llm_path, output_path, threshold=0.35):
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

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        # Field_Metrics: description table first, then other-fields table below it
        desc_df  = report["Field_Metrics_Desc"]
        other_df = report["Field_Metrics_Other"]
        desc_df.to_excel(w, sheet_name="Field_Metrics", index=False, startrow=0)
        other_start = len(desc_df) + 3  # leave one blank row as separator
        other_df.to_excel(w, sheet_name="Field_Metrics", index=False, startrow=other_start)
        for sheet in ("Field_Counts", "Requirement_Matching",
                      "Matched_TP", "False_Positives", "False_Negatives"):
            report[sheet].to_excel(w, sheet_name=sheet, index=False)

    report_path = Path(output_path).with_name(Path(output_path).stem + "_llm_report.xlsx")
    with pd.ExcelWriter(report_path, engine="openpyxl") as w:
        report["LLM_Requirements_Report"].to_excel(w, sheet_name="LLM_Requirements_Report", index=False)

    gt_report_path = Path(output_path).with_name(Path(output_path).stem + "_gt_report.xlsx")
    with pd.ExcelWriter(gt_report_path, engine="openpyxl") as w:
        report["GT_Requirements_Report"].to_excel(w, sheet_name="GT_Requirements_Report", index=False)
    return report


