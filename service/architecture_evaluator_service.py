"""
Evaluate LLM-extracted architecture against a ground-truth Excel, per field.

Usage:
    python -m service.architecture_evaluator_service \
        <ground_truth_architecture.xlsx> <llm_architecture.json> <output_report.xlsx> [threshold]

Defaults:
    threshold = 0.35  (cosine similarity below this is not a match)

Data model
----------
The architecture is extracted into two groups that are evaluated together:
  - Architectural Units  (types: Layer, Component, Service, Device, Connector,
                          Technology, Other)  -> GT sheet "Architectural Units"
  - Patterns             (types: Architectural Pattern, Design Pattern)
                          -> GT sheet "Patterns"
Each element has: id, type, name, description, isPartOf, pageNumber, fixes.

What it does
------------
1. Matches each LLM element to at most one ground-truth (GT) element using two
   passes:
       - Named elements (every type except Connector) are matched on `name`,
         greedy one-to-one at `threshold`, gated so a Pattern only matches a
         Pattern and a (named) Unit only matches a Unit. Within Units a Service
         may still match a Component, so a wrong `type` is measured, not hidden.
       - Connectors are matched on their `isPartOf` endpoints: each connector is
         reduced to the SET of names of the architectural units it links (its
         endpoint ids resolved to unit names on its own side). A GT and an LLM
         connector match when their endpoint-name sets are equal, regardless of
         order (two names are considered the same when they are equal after
         normalisation or their cosine similarity is >= threshold).

2. `name` is the matching anchor for named elements, so it is scored at the
   ELEMENT level with Precision, Recall and Mean Semantic Meaning (its
   denominators span all named items, matched + unmatched):
       Precision_name = correct_name / (LLM items that have a name)
       Recall_name    = correct_name / (GT  items that have a name)

3. Every OTHER field (type, description, pageNumber, isPartOf, fixes) is scored
   as Accuracy over the matched (TP) pairs — the fraction of matched pairs where
   the LLM populated the field and its value agrees with the GT:
       Accuracy_F = correct_F / (matched pairs where LLM populated F)
   Mean Semantic Meaning is additionally reported for `description` and `fixes`.

4. A per-class breakdown (Unit / Pattern / Connector) reports element-level
   Precision/Recall so Connector extraction quality (matched on description) is
   visible even though Connectors carry no name.

Per-field agreement
-------------------
    name, description   cosine similarity >= threshold (blended word+char TF-IDF)
    type                case-insensitive exact match
    pageNumber          the page-number sets overlap (non-empty intersection)
    isPartOf            for connectors: the endpoint-name sets agree (same rule
                        used to match connectors). For every other element: the
                        set of parent elements agrees, resolved THROUGH the
                        matching (each LLM parent id -> its matched GT element),
                        so the differing LLM vs GT id namespaces are never
                        compared directly
    fixes               cosine similarity >= threshold

Output: an xlsx with sheets — Field_Metrics, Class_Breakdown, Matching_Summary,
Field_Counts, Matched_TP, False_Positives, False_Negatives. Two side-car files
are written next to it (matching the requirements evaluator): `<stem>_gt_report.xlsx`
(the unmatched GT elements / false negatives) and `<stem>_llm_report.xlsx` (the
unmatched LLM elements / false positives), each with all fields and the closest
counterpart on the other side.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse the requirements evaluator's text-matching machinery so both evaluators
# share one similarity / greedy-assignment implementation.
from service.evaluator_service import (
    _is_blank,
    norm_text,
    norm_categorical,
    fixes_to_text,
    compute_similarity,
    text_similarity,
    greedy_match,
    _fmt,
    _pick,
)


# ---------------------------------------------------------------------------
# Field specification
# ---------------------------------------------------------------------------
# `name` is the matching anchor for named elements (Precision/Recall/Mean).
# Every other field is scored as Accuracy over matched pairs.
FIELD_SPECS = [
    {"name": "name",        "json": ["name", "Name"],
     "gt": ["Name"],                                   "kind": "semantic",    "semantic": True,  "anchor": True},
    {"name": "type",        "json": ["type", "Type"],
     "gt": ["Type"],                                   "kind": "categorical", "semantic": False, "anchor": False},
    {"name": "description", "json": ["description", "Description"],
     "gt": ["Description"],                            "kind": "semantic",    "semantic": True,  "anchor": False},
    {"name": "pageNumber",  "json": ["pageNumber", "page", "page_number", "Page Number"],
     "gt": ["Page Number", "PageNumber", "Page"],      "kind": "page",        "semantic": False, "anchor": False},
    {"name": "isPartOf",    "json": ["isPartOf", "is_part_of", "is-part-of", "partOf"],
     "gt": ["is-part-of", "isPartOf", "is part of"],   "kind": "parents",     "semantic": False, "anchor": False},
    {"name": "fixes",       "json": ["fixes", "appliedFixes", "Applied Fixes"],
     "gt": ["Applied Fixes", "Fixes", "AppliedFixes"], "kind": "list",        "semantic": True,  "anchor": False},
]

ANCHOR_FIELD = "name"

# Per-element validation field for the overall (full) Precision/Recall. A matched
# pair counts as correct only when this field agrees: named elements (units,
# patterns) are validated on `name`; connectors carry no name, so they are
# validated on `isPartOf` (the set of units they link). Every element class is
# included in the metric — change a value here to validate a class on another field.
VALIDATION_FIELD = {"unit": "name", "pattern": "name", "connector": "isPartOf"}
SPEC_BY_NAME = {s["name"]: s for s in FIELD_SPECS}

ID_JSON_CANDIDATES = ["id", "ID", "unit_id", "au_id", "ad_id"]
ID_GT_CANDIDATES = ["AU ID", "AD ID", "P ID", "P_ID", "PID", "ID", "Id", "id", "AU_ID", "AD_ID"]

# GT sheet names -> element group.
GT_SHEET_GROUPS = {
    "Architectural Units": "unit",
    "Patterns": "pattern",
}
# LLM JSON array keys -> element group.
LLM_GROUP_KEYS = {
    "architectural_units": "unit",
    "patterns": "pattern",
}

_PAGE_RANGE = re.compile(r"(\d+)\s*-\s*(\d+)")
_INT = re.compile(r"\d+")


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------
def is_connector(rec: dict) -> bool:
    return norm_categorical(rec.get("type")) == "connector"


def match_class(rec: dict) -> str:
    """Compatibility class that gates matching: connectors match connectors,
    patterns match patterns, and named units match named units."""
    if is_connector(rec):
        return "connector"
    return rec.get("group") or "unit"


def validation_spec(rec: dict) -> dict:
    """The field spec used to validate this element in the full Precision/Recall:
    `name` for units & patterns, `isPartOf` for connectors (see VALIDATION_FIELD)."""
    return SPEC_BY_NAME[VALIDATION_FIELD.get(match_class(rec), ANCHOR_FIELD)]


def match_text(rec: dict) -> str:
    """The anchor text: a connector's description, otherwise the element name."""
    if is_connector(rec):
        return norm_text(rec.get("description")) or ""
    return norm_text(rec.get("name")) or ""


def to_id_list(v) -> list[str]:
    """Normalise an isPartOf value (JSON list or comma/semicolon string) to ids."""
    if _is_blank(v):
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if not _is_blank(x)]
    return [p.strip() for p in re.split(r"[,;]", str(v)) if p.strip()]


def norm_name(v):
    """Normalise a unit name for comparison: lower-cased, whitespace-collapsed."""
    if _is_blank(v):
        return None
    s = re.sub(r"\s+", " ", str(v).strip().lower())
    return s or None


def endpoint_names(rec: dict, records: list[dict], id_index: dict) -> list[str]:
    """The normalised names of the architectural units a connector links.

    Each id in the connector's isPartOf is resolved (within its own side) to a
    record, and that record's name is collected. Unresolved or unnamed endpoints
    are skipped.
    """
    names = []
    for ref in to_id_list(rec.get("isPartOf")):
        idx = id_index.get(str(ref).strip())
        if idx is None:
            continue
        nm = norm_name(records[idx].get("name"))
        if nm:
            names.append(nm)
    return names


def endpoint_indices(rec: dict, id_index: dict) -> list[int]:
    """The record indices of the architectural units a connector links (its
    isPartOf ids resolved within its own side). Unresolved ids are skipped."""
    out = []
    for ref in to_id_list(rec.get("isPartOf")):
        idx = id_index.get(str(ref).strip())
        if idx is not None:
            out.append(idx)
    return out


def endpoints_match_through_units(llm_idxs: list[int], gt_idxs: list[int],
                                  llm_to_gt: dict) -> bool:
    """True if an LLM connector links the SAME units a GT connector links, judged
    THROUGH the unit matching: each LLM endpoint-unit is mapped to the GT unit it
    matched, and the resulting GT-unit set must equal the GT connector's endpoint
    set. This recognises two connectors as the same when their endpoints already
    matched as units, even if the endpoint names differ (e.g. "payment service"
    vs "payment processing"). Returns False if any endpoint unit did not match."""
    if not llm_idxs or not gt_idxs:
        return False
    mapped = set()
    for li in llm_idxs:
        gj = llm_to_gt.get(li)
        if gj is None:
            return False
        mapped.add(gj)
    return mapped == set(gt_idxs)


def name_sets_match(a: list[str], b: list[str], threshold: float) -> bool:
    """True if the two endpoint-name lists match order-independently.

    Two names are the same when normalised-equal or their cosine similarity is
    >= threshold. Requires a one-to-one correspondence (equal sizes, non-empty).
    """
    if not a or len(a) != len(b):
        return False
    remaining = list(b)
    for nm in a:
        hit = None
        for k, other in enumerate(remaining):
            if nm == other or text_similarity(nm, other) >= threshold:
                hit = k
                break
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def page_set(v) -> set[int]:
    """Parse a page reference ('44', '44, 45', '68-69', '58.59') to a set of ints."""
    if _is_blank(v):
        return set()
    s = str(v)
    pages: set[int] = set()
    for m in _PAGE_RANGE.finditer(s):
        a, b = int(m.group(1)), int(m.group(2))
        if a <= b <= a + 100:
            pages.update(range(a, b + 1))
    for m in _INT.finditer(_PAGE_RANGE.sub(" ", s)):
        pages.add(int(m.group()))
    return pages


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_ground_truth(xlsx_path: str) -> list[dict]:
    """Read both GT sheets (Architectural Units + Patterns) into canonical records."""
    xls = pd.ExcelFile(xlsx_path)
    records: list[dict] = []
    for sheet_name in xls.sheet_names:
        group = GT_SHEET_GROUPS.get(sheet_name.strip())
        if group is None:
            # Tolerate slightly different sheet titles.
            low = sheet_name.strip().lower()
            if "pattern" in low:
                group = "pattern"
            elif "unit" in low:
                group = "unit"
            else:
                continue

        df = xls.parse(sheet_name)
        cols = list(df.columns)
        id_col = _pick(cols, ID_GT_CANDIDATES)
        field_cols = {s["name"]: _pick(cols, s["gt"]) for s in FIELD_SPECS}
        desc_col = field_cols["description"]
        name_col = field_cols["name"]

        for _, row in df.iterrows():
            id_val = row[id_col] if id_col else None
            if id_col is not None and not _is_blank(id_val):
                rec = {"id": str(id_val).strip(), "group": group, "_raw": {}}
                for s in FIELD_SPECS:
                    col = field_cols[s["name"]]
                    rec[s["name"]] = row[col] if (col and not _is_blank(row[col])) else None
                records.append(rec)
            else:
                # Continuation row (blank id): append wrapped text to previous record.
                if records and group == records[-1]["group"]:
                    prev = records[-1]
                    if desc_col and not _is_blank(row[desc_col]):
                        prev["description"] = f"{prev.get('description') or ''} {str(row[desc_col]).strip()}".strip()
                    elif name_col and not _is_blank(row[name_col]):
                        prev["name"] = f"{prev.get('name') or ''} {str(row[name_col]).strip()}".strip()
    return records


def load_llm_extraction(json_path: str) -> list[dict]:
    """Read the LLM architecture JSON into canonical records (units + patterns)."""
    with open(json_path) as f:
        data = json.load(f)

    def get(item, candidates):
        for k in candidates:
            if k in item and not _is_blank(item[k]):
                return item[k]
        return None

    records: list[dict] = []
    if isinstance(data, dict):
        groups = [(data.get(key, []) or [], group) for key, group in LLM_GROUP_KEYS.items()]
        # If none of the expected keys exist, fall back to any list value found.
        if all(len(items) == 0 for items, _ in groups):
            for v in data.values():
                if isinstance(v, list):
                    groups.append((v, "unit"))
    else:
        groups = [(data or [], "unit")]

    for items, group in groups:
        for item in items:
            if not isinstance(item, dict):
                continue
            id_val = get(item, ID_JSON_CANDIDATES)
            rec = {"id": str(id_val).strip() if id_val is not None else "",
                   "group": group, "_raw": item}
            for s in FIELD_SPECS:
                rec[s["name"]] = get(item, s["json"])
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Id resolution (exact, within one list) for isPartOf
# ---------------------------------------------------------------------------
def build_id_index(records: list[dict]) -> dict:
    return {r["id"]: i for i, r in enumerate(records) if r.get("id")}


def resolve_id(index: dict, value) -> int | None:
    if _is_blank(value):
        return None
    return index.get(str(value).strip())


# ---------------------------------------------------------------------------
# Per-field agreement
# ---------------------------------------------------------------------------
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
        pa, pb = page_set(a), page_set(b)
        return (bool(pa) and bool(pb) and bool(pa & pb)), None
    if kind == "semantic":  # name / description
        ta, tb = norm_text(a), norm_text(b)
        sim = text_similarity(ta, tb) if (ta and tb) else 0.0
        return (sim >= threshold), sim
    if kind == "list":  # fixes
        ta, tb = fixes_to_text(a), fixes_to_text(b)
        sim = text_similarity(ta, tb) if (ta and tb) else 0.0
        return (sim >= threshold), sim
    if kind == "parents":  # isPartOf
        # For connectors, agreement uses the same rule that matched them: the
        # endpoints agree when they resolve to the same matched units (through the
        # unit matching), or, failing that, when the endpoint names agree.
        if is_connector(llm_rec) and is_connector(gt_rec):
            agree = endpoints_match_through_units(llm_rec.get("_endpoint_idx") or [],
                                                  gt_rec.get("_endpoint_idx") or [],
                                                  llm_to_gt) \
                or name_sets_match(llm_rec.get("_endpoint_names") or [],
                                   gt_rec.get("_endpoint_names") or [], threshold)
            return agree, None
        pred = set()
        for ref in to_id_list(a):
            li = resolve_id(llm_idx, ref)
            if li is not None and li in llm_to_gt:
                pred.add(llm_to_gt[li])
        true = set()
        for ref in to_id_list(b):
            gi = resolve_id(gt_idx, ref)
            if gi is not None:
                true.add(gi)
        return (len(pred) > 0 and pred == true), None
    raise ValueError(f"unknown kind {kind}")


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def build_report(gt, llm, sim, pairs, threshold):
    matched_llm = {i for i, _, _ in pairs}
    matched_gt = {j for _, j, _ in pairs}
    llm_to_gt = {i: j for i, j, _ in pairs}

    tp, fp, fn = len(pairs), len(llm) - len(pairs), len(gt) - len(pairs)

    llm_idx = build_id_index(llm)
    gt_idx = build_id_index(gt)

    name_rows, other_rows, count_rows = [], [], []
    for spec in FIELD_SPECS:
        name = spec["name"]
        llm_has_total = sum(field_present(r, spec) for r in llm)
        gt_has_total = sum(field_present(r, spec) for r in gt)

        correct, sims = 0, []
        llm_has_matched, gt_has_matched, both_has_matched = 0, 0, 0
        for i, j, _ in pairs:
            llm_populated = field_present(llm[i], spec)
            gt_populated = field_present(gt[j], spec)
            llm_has_matched += llm_populated
            gt_has_matched += gt_populated
            if not (llm_populated and gt_populated):
                continue
            both_has_matched += 1
            agree, s = field_agrees(spec, llm[i], gt[j], threshold,
                                    llm_idx=llm_idx, gt_idx=gt_idx, llm_to_gt=llm_to_gt)
            if agree:
                correct += 1
            if spec["semantic"] and s is not None:
                sims.append(s)

        mean_sem = (float(np.mean(sims)) if sims else None) if spec["semantic"] else None

        if spec.get("anchor"):
            # name anchor: element-level Precision/Recall over ALL named items.
            precision = (correct / llm_has_total) if llm_has_total else None
            recall = (correct / gt_has_total) if gt_has_total else None
            name_rows.append({
                "field": name,
                "precision": _fmt(precision),
                "recall": _fmt(recall),
                "mean semantic meaning": _fmt(mean_sem),
            })
        else:
            # Accuracy over matched pairs where the LLM populated the field.
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
            "both_populated_matched": both_has_matched,
            "correct_in_matched": correct,
            "matched_pairs (TP)": tp,
        })

    # Full element-level Precision/Recall over ALL elements (units, patterns AND
    # connectors). Every matched pair is validated on its type-appropriate field —
    # name for units/patterns, isPartOf (endpoint set) for connectors — and the
    # denominators span every element. Surfaced as the headline row of the field
    # metrics so the precision/recall there includes connectors, not just named items.
    full_correct = 0
    for i, j, _ in pairs:
        agree, _s = field_agrees(validation_spec(gt[j]), llm[i], gt[j], threshold,
                                 llm_idx=llm_idx, gt_idx=gt_idx, llm_to_gt=llm_to_gt)
        if agree:
            full_correct += 1
    full_precision = (full_correct / len(llm)) if llm else None
    full_recall = (full_correct / len(gt)) if gt else None
    name_rows.insert(0, {
        "field": "all elements (name | isPartOf)",
        "precision": _fmt(full_precision),
        "recall": _fmt(full_recall),
        "mean semantic meaning": "-",
    })

    field_metrics_name = pd.DataFrame(name_rows, columns=["field", "precision", "recall", "mean semantic meaning"])
    field_metrics_other = pd.DataFrame(other_rows, columns=["field", "accuracy", "mean semantic meaning"])
    field_counts = pd.DataFrame(count_rows)

    # Per-class element-level Precision/Recall (Unit / Pattern / Connector).
    class_rows = []
    classes = ["unit", "pattern", "connector"]
    gt_class = [match_class(r) for r in gt]
    llm_class = [match_class(r) for r in llm]
    for c in classes:
        gt_c = sum(1 for x in gt_class if x == c)
        llm_c = sum(1 for x in llm_class if x == c)
        tp_c = sum(1 for i, j, _ in pairs if gt_class[j] == c)
        precision = (tp_c / llm_c) if llm_c else None
        recall = (tp_c / gt_c) if gt_c else None
        class_rows.append({
            "class": c,
            "gt_count": gt_c,
            "llm_count": llm_c,
            "matched (TP)": tp_c,
            "precision": _fmt(precision),
            "recall": _fmt(recall),
        })
    class_breakdown = pd.DataFrame(class_rows)

    summary = pd.DataFrame([
        {"Metric": "Ground truth count", "Value": len(gt)},
        {"Metric": "LLM extracted count", "Value": len(llm)},
        {"Metric": "True Positives (matched)", "Value": tp},
        {"Metric": "Validated correct (name for units/patterns, isPartOf for connectors)", "Value": full_correct},
        {"Metric": "False Positives", "Value": fp},
        {"Metric": "False Negatives", "Value": fn},
        {"Metric": "Precision (all elements; units/patterns by name, connectors by isPartOf)", "Value": _fmt(full_precision)},
        {"Metric": "Recall (all elements; units/patterns by name, connectors by isPartOf)", "Value": _fmt(full_recall)},
        {"Metric": "Match threshold (cosine)", "Value": threshold},
    ])

    def blob(rec, prefix):
        out = {f"{prefix}_{s['name']}": (fixes_to_text(rec.get(s["name"]))
                                         if s["name"] == "fixes" else rec.get(s["name"]))
               for s in FIELD_SPECS}
        out[f"{prefix}_id"] = rec.get("id")
        eps = rec.get("_endpoint_names")
        out[f"{prefix}_endpoints"] = ", ".join(eps) if eps else None
        return out

    matched = pd.DataFrame([
        {"LLM_ID": llm[i]["id"], "GT_ID": gt[j]["id"],
         "match_class": match_class(gt[j]), "anchor_similarity": round(s, 4),
         **blob(llm[i], "LLM"), **blob(gt[j], "GT")}
        for i, j, s in sorted(pairs, key=lambda x: x[2])
    ])

    def eps_str(rec):
        eps = rec.get("_endpoint_names")
        return ", ".join(eps) if eps else None

    fps = pd.DataFrame([
        {"LLM_ID": llm[i]["id"], "LLM_type": llm[i].get("type"),
         "LLM_name": llm[i].get("name"), "LLM_endpoints": eps_str(llm[i]),
         "LLM_description": llm[i].get("description"),
         "closest_GT_ID": gt[int(sim[i].argmax())]["id"] if len(gt) else None,
         "closest_GT_name": gt[int(sim[i].argmax())].get("name") if len(gt) else None,
         "similarity": round(float(sim[i].max()), 4) if len(gt) else 0.0}
        for i in range(len(llm)) if i not in matched_llm
    ])

    fns = pd.DataFrame([
        {"GT_ID": gt[j]["id"], "GT_type": gt[j].get("type"),
         "GT_name": gt[j].get("name"), "GT_endpoints": eps_str(gt[j]),
         "GT_description": gt[j].get("description"),
         "closest_LLM_ID": llm[int(sim[:, j].argmax())]["id"] if len(llm) else None,
         "closest_LLM_name": llm[int(sim[:, j].argmax())].get("name") if len(llm) else None,
         "similarity": round(float(sim[:, j].max()), 4) if len(llm) else 0.0}
        for j in range(len(gt)) if j not in matched_gt
    ])

    def parents_str(rec):
        ids = to_id_list(rec.get("isPartOf"))
        return ", ".join(ids) if ids else None

    # Full-field views of the items that were NOT matched: the GT report lists
    # every ground-truth element the LLM missed (false negatives) and the LLM
    # report lists every LLM element with no ground-truth counterpart (false
    # positives). Each row also carries its closest counterpart on the other
    # side and that similarity, mirroring the requirements evaluator's reports.
    gt_report = pd.DataFrame([
        {
            "GT_ID": gt[j]["id"],
            "GT_name": gt[j].get("name"),
            "GT_type": gt[j].get("type"),
            "GT_description": gt[j].get("description"),
            "GT_pageNumber": gt[j].get("pageNumber"),
            "GT_isPartOf": parents_str(gt[j]),
            "GT_endpoints": eps_str(gt[j]),
            "GT_fixes": fixes_to_text(gt[j].get("fixes")),
            "closest_LLM_ID": llm[int(sim[:, j].argmax())]["id"] if len(llm) else None,
            "closest_LLM_name": llm[int(sim[:, j].argmax())].get("name") if len(llm) else None,
            "closest_LLM_description": llm[int(sim[:, j].argmax())].get("description") if len(llm) else None,
            "best_similarity": round(float(sim[:, j].max()), 4) if len(llm) else 0.0,
        }
        for j in range(len(gt)) if j not in matched_gt
    ])

    llm_report = pd.DataFrame([
        {
            "LLM_ID": llm[i]["id"],
            "LLM_name": llm[i].get("name"),
            "LLM_type": llm[i].get("type"),
            "LLM_description": llm[i].get("description"),
            "LLM_pageNumber": llm[i].get("pageNumber"),
            "LLM_isPartOf": parents_str(llm[i]),
            "LLM_endpoints": eps_str(llm[i]),
            "LLM_fixes": fixes_to_text(llm[i].get("fixes")),
            "closest_GT_ID": gt[int(sim[i].argmax())]["id"] if len(gt) else None,
            "closest_GT_name": gt[int(sim[i].argmax())].get("name") if len(gt) else None,
            "closest_GT_description": gt[int(sim[i].argmax())].get("description") if len(gt) else None,
            "best_similarity": round(float(sim[i].max()), 4) if len(gt) else 0.0,
        }
        for i in range(len(llm)) if i not in matched_llm
    ])

    return {
        "Field_Metrics_Name": field_metrics_name,
        "Field_Metrics_Other": field_metrics_other,
        "Class_Breakdown": class_breakdown,
        "Matching_Summary": summary,
        "Field_Counts": field_counts,
        "Matched_TP": matched,
        "False_Positives": fps,
        "False_Negatives": fns,
        "LLM_Architecture_Report": llm_report,
        "GT_Architecture_Report": gt_report,
        "stats": {"tp": tp, "fp": fp, "fn": fn,
                  "name_metrics": name_rows, "other_metrics": other_rows,
                  "class_breakdown": class_rows},
    }


def evaluate_architecture(gt_path, llm_path, output_path, threshold=0.35):
    gt = load_ground_truth(gt_path)
    llm = load_llm_extraction(llm_path)
    if not gt or not llm:
        raise ValueError(f"Empty input — gt={len(gt)} llm={len(llm)}")

    gt_texts = [match_text(r) for r in gt]
    llm_texts = [match_text(r) for r in llm]
    sim = compute_similarity(gt_texts, llm_texts)  # shape (n_llm, n_gt)

    # Attach each connector's endpoint-name set (resolved on its own side) so it
    # can be matched and scored on the units it links.
    llm_id_index = build_id_index(llm)
    gt_id_index = build_id_index(gt)
    for r in llm:
        if is_connector(r):
            r["_endpoint_names"] = endpoint_names(r, llm, llm_id_index)
            r["_endpoint_idx"] = endpoint_indices(r, llm_id_index)
    for r in gt:
        if is_connector(r):
            r["_endpoint_names"] = endpoint_names(r, gt, gt_id_index)
            r["_endpoint_idx"] = endpoint_indices(r, gt_id_index)

    # Pass 1 — named elements (everything except connectors) matched on name,
    # gated so patterns only match patterns and units only match units (override
    # disabled with >1.0 so the class gate is always enforced).
    nonconn_llm = [i for i in range(len(llm)) if not is_connector(llm[i])]
    nonconn_gt = [j for j in range(len(gt)) if not is_connector(gt[j])]
    pairs = []
    if nonconn_llm and nonconn_gt:
        subsim = sim[np.ix_(nonconn_llm, nonconn_gt)]
        sub_pairs = greedy_match(
            subsim, threshold,
            llm_types=[match_class(llm[i]) for i in nonconn_llm],
            gt_types=[match_class(gt[j]) for j in nonconn_gt],
            type_override_sim=2.0,
        )
        pairs.extend((nonconn_llm[a], nonconn_gt[b], s) for a, b, s in sub_pairs)

    # Pass 2 — connectors matched on the units they link. A connector matches when
    # its endpoint units already matched as units (resolved THROUGH the Pass-1 unit
    # matching), or, failing that, when the endpoint names themselves agree.
    llm_to_gt = {i: j for i, j, _ in pairs}
    llm_conn = [i for i in range(len(llm)) if is_connector(llm[i])]
    gt_conn = [j for j in range(len(gt)) if is_connector(gt[j])]
    used_gt = set()
    for i in llm_conn:
        for j in gt_conn:
            if j in used_gt:
                continue
            if endpoints_match_through_units(llm[i]["_endpoint_idx"], gt[j]["_endpoint_idx"], llm_to_gt) \
               or name_sets_match(llm[i]["_endpoint_names"], gt[j]["_endpoint_names"], threshold):
                pairs.append((i, j, 1.0))
                used_gt.add(j)
                break

    report = build_report(gt, llm, sim, pairs, threshold)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        name_df = report["Field_Metrics_Name"]
        other_df = report["Field_Metrics_Other"]
        name_df.to_excel(w, sheet_name="Field_Metrics", index=False, startrow=0)
        other_start = len(name_df) + 3
        other_df.to_excel(w, sheet_name="Field_Metrics", index=False, startrow=other_start)
        for sheet in ("Class_Breakdown", "Matching_Summary", "Field_Counts",
                      "Matched_TP", "False_Positives", "False_Negatives"):
            report[sheet].to_excel(w, sheet_name=sheet, index=False)

    # Side-car reports (same naming convention as the requirements evaluator):
    # the unmatched LLM elements (false positives) and the unmatched GT elements
    # (false negatives), each with their full fields and closest counterpart.
    report_path = Path(output_path).with_name(Path(output_path).stem + "_llm_report.xlsx")
    with pd.ExcelWriter(report_path, engine="openpyxl") as w:
        report["LLM_Architecture_Report"].to_excel(w, sheet_name="LLM_Architecture_Report", index=False)

    gt_report_path = Path(output_path).with_name(Path(output_path).stem + "_gt_report.xlsx")
    with pd.ExcelWriter(gt_report_path, engine="openpyxl") as w:
        report["GT_Architecture_Report"].to_excel(w, sheet_name="GT_Architecture_Report", index=False)
    return report


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python -m service.architecture_evaluator_service "
              "<ground_truth_architecture.xlsx> <llm_architecture.json> "
              "<output_report.xlsx> [threshold]")
        raise SystemExit(1)
    gt_p, llm_p, out_p = sys.argv[1], sys.argv[2], sys.argv[3]
    thr = float(sys.argv[4]) if len(sys.argv) > 4 else 0.35
    rep = evaluate_architecture(gt_p, llm_p, out_p, thr)
    s = rep["stats"]
    print(f"TP={s['tp']} FP={s['fp']} FN={s['fn']}")
    print("Saved:", out_p)
