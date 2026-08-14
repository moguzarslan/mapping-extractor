"""
Evaluate LLM-extracted architecture against a ground-truth Excel, per field.

Usage:
    python -m service.architecture_evaluator_service \
        <ground_truth_architecture.xlsx> <llm_architecture.json> <output_report.xlsx> [threshold]

Defaults:
    threshold = 0.75  (embedding cosine similarity below this is not a match)

Data model
----------
The architecture is extracted into two groups that are evaluated together:
  - Architectural Units  (types: Layer, Component, Service, Device, Connector,
                          Technology, Other)  -> GT sheet "Architectural Units"
  - Patterns             (types: Architectural Pattern, Design Pattern)
                          -> GT sheet "Patterns"
Each element has: id, type, name, description, isPartOf, pageNumber, fixedType.

What it does
------------
1. Matches each LLM element to at most one ground-truth (GT) element using two
   passes:
       - Named elements (every type except Connector) are matched on `name`, in
         three tiers (see "Name matching"): identical normalised names, then
         names identical once the generic kind noun is dropped, then an optimal
         assignment on embedding similarity >= `threshold` restricted to pairs
         sharing a content token. There is no class or type gate: a Unit may
         match a Pattern and a Service may match a Component, so a wrong `type`
         is measured, not hidden.
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

3. Every OTHER field (type, description, pageNumber, isPartOf, fixedType) is scored
   as Accuracy over the matched (TP) pairs — the fraction of matched pairs where
   the LLM populated the field and its value agrees with the GT:
       Accuracy_F = correct_F / (matched pairs where LLM populated F)
   Mean Semantic Meaning is additionally reported for `description` and `fixedType`.

4. Connectors are additionally scored at the UNIT-PAIR level for the headline and
   the per-class breakdown. Each connector (GT and LLM) is star-decomposed into the
   set of unit-to-unit communications it asserts — the central (first) endpoint
   paired with every other endpoint — and Precision/Recall are computed over those
   pair sets, with LLM pairs mapped to GT units through the unit matching. This
   removes the group-vs-split ambiguity (a one-to-many connector and the equivalent
   pairwise connectors decompose to the same pairs) and gives partial credit to a
   partially-correct connector. The headline metric micro-averages named elements
   (counted one each) with connector unit-pairs (counted one each).

5. A per-class breakdown reports Precision/Recall for Units, Patterns (element
   level) and Connectors (unit-pair level), so connector quality is visible even
   though connectors carry no name.

Per-field agreement
-------------------
    name                the three-tier rule that matched the pair, so an element
                        is never matched on its name and then scored wrong on it
    description         embedding cosine similarity >= threshold
    type                case-insensitive exact match
    pageNumber          the page-number sets overlap (non-empty intersection)
    isPartOf            for connectors: the endpoint-name sets agree (same rule
                        used to match connectors). For every other element: the
                        set of parent elements agrees, resolved THROUGH the
                        matching (each LLM parent id -> its matched GT element),
                        so the differing LLM vs GT id namespaces are never
                        compared directly
    fixedType           cosine similarity >= threshold

Output: an xlsx with sheets — Field_Metrics, Class_Breakdown, Matching_Summary,
Field_Counts, Matched_TP, False_Positives, False_Negatives. Three side-car files
are written next to it (matching the requirements evaluator): `<stem>_gt_report.xlsx`
(the unmatched GT elements / false negatives), `<stem>_llm_report.xlsx` (the
unmatched LLM elements / false positives), each with all fields and the closest
counterpart on the other side, and `<stem>_by_type.xlsx` with a Type_Breakdown
sheet: one row per element `type` in the fixed taxonomy (Layer, Component,
Service, Device, Connector, Technology, Other, Architectural Pattern, Design
Pattern) with the successful extractions (TP), false positives, false negatives,
precision and recall. All nine rows are always present, in that order and
zero-filled where a type does not occur, so the sheet lines up across documents.
Eight rows count ELEMENTS; the connector row — labelled `connector (unit-pairs)`
— counts UNIT-PAIRS, the same basis as Class_Breakdown and the headline
Precision/Recall, so every connector figure in the report agrees.
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
    optimal_match,
    build_type_breakdown,
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
     "gt": ["Name"],                                   "kind": "name",        "semantic": True,  "anchor": True},
    {"name": "type",        "json": ["type", "Type"],
     "gt": ["Type"],                                   "kind": "categorical", "semantic": False, "anchor": False},
    {"name": "description", "json": ["description", "Description"],
     "gt": ["Description"],                            "kind": "semantic",    "semantic": True,  "anchor": False},
    {"name": "pageNumber",  "json": ["pageNumber", "page", "page_number", "Page Number"],
     "gt": ["Page Number", "PageNumber", "Page"],      "kind": "page",        "semantic": False, "anchor": False},
    {"name": "isPartOf",    "json": ["isPartOf", "is_part_of", "is-part-of", "partOf"],
     "gt": ["is-part-of", "isPartOf", "is part of"],   "kind": "parents",     "semantic": False, "anchor": False},
    {"name": "fixedType",   "json": ["fixedType", "fixed_type", "FixedType"],
     "gt": ["fixedType", "Fixed Type", "FixedType"],   "kind": "list",        "semantic": True,  "anchor": False},
]

ANCHOR_FIELD = "name"

# Per-element validation field for the overall (full) Precision/Recall. A matched
# pair counts as correct only when this field agrees: named elements (units,
# patterns) are validated on `name`; connectors carry no name, so they are
# validated on `isPartOf` (the set of units they link). Every element class is
# included in the metric — change a value here to validate a class on another field.
VALIDATION_FIELD = {"unit": "name", "pattern": "name", "connector": "isPartOf"}
SPEC_BY_NAME = {s["name"]: s for s in FIELD_SPECS}

# The closed architecture element taxonomy, in report order. Passed to
# `build_type_breakdown` so the Type_Breakdown sheet always has these nine rows —
# with explicit zeros for a type that occurs in neither the GT nor the LLM output —
# instead of a per-document subset that also shifts with what the model emitted.
# Any value outside this list still gets its own row, appended after these.
ARCHITECTURE_TYPES = [
    "Layer", "Component", "Service", "Device", "Connector", "Technology", "Other",
    "Architectural Pattern", "Design Pattern",
]

# Label of the Type_Breakdown connector row. It names the unit of counting,
# because that row alone is scored in unit-pairs rather than in elements.
CONNECTOR_TYPE_ROW = "connector (unit-pairs)"

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
    """The element class (`connector`, `pattern`, `unit`). It no longer gates
    matching — named elements are matched on `name` alone — and is used only to
    split connectors from named elements, to pick the validation field, and to
    report the per-class breakdown."""
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


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------
# Two names denote the same element when they are identical once normalised,
# when they are identical after dropping the generic noun that only states the
# element's kind ("Portal Microservice" -> "portal"), or when they share a
# content token AND their embedding similarity clears the threshold.
#
# Embedding similarity alone cannot carry this decision. Over the one- and
# two-word names architecture elements carry it measures topical relatedness, so
# it ranks unrelated siblings (Kubernetes / Docker at 0.77, Repository /
# Database at 0.75) above genuine matches (Portal / Portal Microservice at 0.79)
# — no threshold separates the two. The token rules decide those cases instead,
# and similarity is consulted only for the remainder.

# Nouns that state an element's kind rather than its identity.
GENERIC_TOKENS = {
    "service", "services", "microservice", "microservices", "module", "modules",
    "component", "components", "layer", "layers", "pattern", "patterns",
    "system", "systems", "api", "tier", "engine",
}
# Vendor names, shared by unrelated products (Amazon RDS / Amazon Web Services),
# so they are never evidence on their own that two names denote the same element.
VENDOR_TOKENS = {"aws", "amazon", "google", "microsoft", "azure", "apache"}

_PARENTHETICAL = re.compile(r"\([^)]*\)")
_NON_WORD = re.compile(r"[^\w\s]")


def name_key(v) -> str | None:
    """Comparison key for a name: lower-cased, parentheticals and punctuation
    dropped, whitespace collapsed. "Contract Layer (DTO/Mappers)" -> "contract layer"."""
    if _is_blank(v):
        return None
    s = _PARENTHETICAL.sub(" ", str(v).lower())
    return re.sub(r"\s+", " ", _NON_WORD.sub(" ", s)).strip() or None


def stripped_key(v) -> str | None:
    """`name_key` with the generic nouns removed, so a name and that same name
    qualified by its kind collapse together: "Portal Microservice" and "Portal"
    both give "portal". None when nothing but generic nouns is left."""
    key = name_key(v)
    if not key:
        return None
    return " ".join(t for t in key.split() if t not in GENERIC_TOKENS) or None


def name_tokens(v) -> set[str]:
    """The content tokens of a name — its key's tokens minus the generic and
    vendor words, neither of which evidences identity."""
    key = name_key(v)
    return set(key.split()) - GENERIC_TOKENS - VENDOR_TOKENS if key else set()


def names_agree(a, b, threshold: float, similarity: float | None = None) -> bool:
    """Whether two names denote the same element, by the three rules above.
    `similarity` is the already-computed embedding cosine, when the caller has it."""
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    sa = stripped_key(a)
    if sa is not None and sa == stripped_key(b):
        return True
    if not (name_tokens(a) & name_tokens(b)):
        return False
    sim = text_similarity(ka, kb) if similarity is None else similarity
    return sim >= threshold


def _unique_by_key(records: list[dict], idxs: list[int], key_of) -> dict:
    """{key: index} over `idxs`, keeping only the keys exactly one record carries.
    A key two records share is dropped: which of them is the intended match is
    precisely what the similarity assignment exists to decide."""
    seen: dict = {}
    for i in idxs:
        key = key_of(records[i].get("name"))
        if key is not None:
            seen[key] = i if key not in seen else None
    return {k: i for k, i in seen.items() if i is not None}


def match_named_elements(llm: list[dict], gt: list[dict], sim: np.ndarray,
                         threshold: float, llm_idxs: list[int],
                         gt_idxs: list[int]) -> list[tuple[int, int, float]]:
    """One-to-one match the named elements (everything except connectors) on `name`.

    The deterministic key matches are taken first — exact, then generic-noun
    stripped — and only where the key is unambiguous on both sides. Elements whose
    names simply agree are therefore paired before any similarity is consulted, and
    cannot be stolen by a higher-scoring near-miss: the real "Client-Server" pattern
    claims its counterpart before the "Client" unit can. Whatever is left is
    assigned optimally on embedding similarity, restricted to pairs that share a
    content token.
    """
    pairs: list[tuple[int, int, float]] = []
    used_llm: set[int] = set()
    used_gt: set[int] = set()

    for key_of in (name_key, stripped_key):
        gt_by_key = _unique_by_key(gt, [j for j in gt_idxs if j not in used_gt], key_of)
        llm_by_key = _unique_by_key(llm, [i for i in llm_idxs if i not in used_llm], key_of)
        for key, i in llm_by_key.items():
            j = gt_by_key.get(key)
            if j is not None:
                pairs.append((i, j, float(sim[i, j])))
                used_llm.add(i)
                used_gt.add(j)

    rest_llm = [i for i in llm_idxs if i not in used_llm]
    rest_gt = [j for j in gt_idxs if j not in used_gt]
    if not rest_llm or not rest_gt:
        return pairs

    # Similarity tier: zero out every pair with no shared content token, so the
    # assignment can only choose among pairs the names themselves support.
    sub = sim[np.ix_(rest_llm, rest_gt)].copy()
    for a, i in enumerate(rest_llm):
        for b, j in enumerate(rest_gt):
            if not (name_tokens(llm[i].get("name")) & name_tokens(gt[j].get("name"))):
                sub[a, b] = 0.0
    pairs.extend((rest_llm[a], rest_gt[b], s)
                 for a, b, s in optimal_match(sub, threshold))
    return pairs


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


def connector_pairs(rec: dict, id_index: dict) -> set[frozenset]:
    """Star-decompose a connector into the set of unordered endpoint-index pairs
    centered on its first (hub) endpoint: {hub, e1}, {hub, e2}, ...

    A connector linking N units therefore asserts N-1 pairwise communications, not
    a full clique. This mirrors the extraction convention (a one-to-many connector
    lists the central unit first, then every unit it communicates with), so the
    decomposition recovers exactly the intended unit-to-unit communications.
    Indices are resolved within the connector's own side; duplicates are dropped.
    """
    idxs: list[int] = []
    for x in endpoint_indices(rec, id_index):
        if x not in idxs:
            idxs.append(x)
    if len(idxs) < 2:
        return set()
    hub = idxs[0]
    return {frozenset((hub, other)) for other in idxs[1:] if other != hub}


def _map_pair_through_units(pair_llm: frozenset, llm_to_gt: dict) -> frozenset | None:
    """Map an LLM endpoint-index pair to the GT-index pair it corresponds to, via
    the unit matching. Returns None if either endpoint unit did not match a GT unit
    or both endpoints collapse onto the same GT unit."""
    mapped = set()
    for li in pair_llm:
        gj = llm_to_gt.get(li)
        if gj is None:
            return None
        mapped.add(gj)
    return frozenset(mapped) if len(mapped) == 2 else None


def connector_pair_stats(gt: list, llm: list, llm_to_gt: dict,
                         gt_id_index: dict, llm_id_index: dict) -> dict:
    """Pair-level connector scoring.

    Every connector (GT and LLM) is star-decomposed into the set of unit-to-unit
    communications it asserts. LLM pairs are mapped to GT units THROUGH the unit
    matching, then precision/recall are computed over the distinct pair sets. This
    removes the group-vs-split ambiguity — a one-to-many connector and the
    equivalent set of pairwise connectors decompose to the same pairs — and lets a
    partially-correct connector earn partial credit instead of nothing.
    """
    gt_pairs: set[frozenset] = set()
    for r in gt:
        if is_connector(r):
            gt_pairs |= connector_pairs(r, gt_id_index)
    llm_pairs: set[frozenset] = set()
    for r in llm:
        if is_connector(r):
            llm_pairs |= connector_pairs(r, llm_id_index)

    precision_tp = 0
    covered_gt: set[frozenset] = set()
    for p in llm_pairs:
        mp = _map_pair_through_units(p, llm_to_gt)
        if mp is not None and mp in gt_pairs:
            precision_tp += 1
            covered_gt.add(mp)
    recall_tp = len(covered_gt)

    return {
        "gt_pairs": len(gt_pairs),
        "llm_pairs": len(llm_pairs),
        "precision_tp": precision_tp,
        "recall_tp": recall_tp,
        "precision": (precision_tp / len(llm_pairs)) if llm_pairs else None,
        "recall": (recall_tp / len(gt_pairs)) if gt_pairs else None,
    }


def apply_connector_pair_row(breakdown: pd.DataFrame, conn_stats: dict) -> pd.DataFrame:
    """Rewrite a Type_Breakdown's `connector` row at the UNIT-PAIR level.

    `build_type_breakdown` counts every type in elements. For connectors that is
    the wrong basis and it disagrees with both `Class_Breakdown` and the headline
    Precision/Recall, which score connectors by the unit-to-unit communications
    they assert. Counting connector elements here would also reintroduce the two
    biases the star decomposition exists to remove: the group-vs-split penalty (a
    one-to-many connector versus the equivalent pairwise connectors) and the
    all-or-nothing cliff (a connector with one wrong endpoint scored as a whole
    false positive plus a whole false negative).

    The row stays internally consistent — precision == TP/(TP+FP) and
    recall == TP/(TP+FN) — because `connector_pair_stats`' two true-positive
    counts are necessarily equal: the unit matching is one-to-one, so distinct
    LLM pairs cannot collapse onto the same GT pair.
    """
    tp = conn_stats["recall_tp"]
    gt_pairs, llm_pairs = conn_stats["gt_pairs"], conn_stats["llm_pairs"]
    row = {
        "type": CONNECTOR_TYPE_ROW,
        "gt_count": gt_pairs,
        "llm_count": llm_pairs,
        "successful_extractions": tp,
        "false_positives": llm_pairs - tp,
        "false_negatives": gt_pairs - tp,
        "precision": _fmt(conn_stats["precision"]),
        "recall": _fmt(conn_stats["recall"]),
    }
    # Rebuild from records rather than assigning in place: the ratio columns hold
    # floats or the "-" placeholder, so in-place assignment would fight dtypes.
    rows = breakdown.to_dict("records")
    for k, r in enumerate(rows):
        if norm_categorical(r["type"]) == "connector":
            rows[k] = row
            break
    else:
        rows.append(row)
    return pd.DataFrame(rows, columns=list(breakdown.columns))


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
    if kind == "name":
        # The same rule that matched the pair, so an element can never be matched
        # on its name and then scored wrong on that name.
        ta, tb = norm_text(a), norm_text(b)
        sim = text_similarity(ta, tb) if (ta and tb) else 0.0
        return names_agree(a, b, threshold, sim), sim
    if kind == "semantic":  # description
        ta, tb = norm_text(a), norm_text(b)
        sim = text_similarity(ta, tb) if (ta and tb) else 0.0
        return (sim >= threshold), sim
    if kind == "list":  # fixedType
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

    # Headline Precision/Recall over ALL elements. Named elements (units, patterns)
    # are scored at the ELEMENT level on `name`; connectors are scored at the PAIR
    # level — each connector is star-decomposed into the unit-to-unit communications
    # it asserts, so a one-to-many connector and the equivalent pairwise connectors
    # are credited identically (no group-vs-split penalty) and a partially-correct
    # connector earns partial credit. The metric micro-averages the two: a named
    # element counts as one item, a connector counts as its unit-pairs.
    conn_stats = connector_pair_stats(gt, llm, llm_to_gt, gt_idx, llm_idx)

    named_correct = 0
    for i, j, _ in pairs:
        if is_connector(gt[j]):
            continue
        agree, _s = field_agrees(validation_spec(gt[j]), llm[i], gt[j], threshold,
                                 llm_idx=llm_idx, gt_idx=gt_idx, llm_to_gt=llm_to_gt)
        if agree:
            named_correct += 1

    named_llm = sum(1 for r in llm if not is_connector(r))
    named_gt = sum(1 for r in gt if not is_connector(r))

    full_precision_den = named_llm + conn_stats["llm_pairs"]
    full_recall_den = named_gt + conn_stats["gt_pairs"]
    full_precision = ((named_correct + conn_stats["precision_tp"]) / full_precision_den) if full_precision_den else None
    full_recall = ((named_correct + conn_stats["recall_tp"]) / full_recall_den) if full_recall_den else None
    name_rows.insert(0, {
        "field": "all elements (units/patterns by name, connectors by unit-pair)",
        "precision": _fmt(full_precision),
        "recall": _fmt(full_recall),
        "mean semantic meaning": "-",
    })

    field_metrics_name = pd.DataFrame(name_rows, columns=["field", "precision", "recall", "mean semantic meaning"])
    field_metrics_other = pd.DataFrame(other_rows, columns=["field", "accuracy", "mean semantic meaning"])
    field_counts = pd.DataFrame(count_rows)

    # Per-class Precision/Recall. Units and Patterns are scored at the element
    # level; Connectors are scored at the unit-pair level (see connector_pair_stats),
    # so the counts in the connector row are pairs, not elements.
    # Matching is no longer class-gated, so a pair can straddle two classes; as in
    # `build_type_breakdown`, a pair counts as a TP for a class only when BOTH
    # sides are of that class (a cross-class pair is an FN for the GT's class and
    # an FP for the LLM's class).
    class_rows = []
    gt_class = [match_class(r) for r in gt]
    llm_class = [match_class(r) for r in llm]
    for c in ("unit", "pattern"):
        gt_c = sum(1 for x in gt_class if x == c)
        llm_c = sum(1 for x in llm_class if x == c)
        tp_c = sum(1 for i, j, _ in pairs if gt_class[j] == c and llm_class[i] == c)
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
    class_rows.append({
        "class": "connector (unit-pairs)",
        "gt_count": conn_stats["gt_pairs"],
        "llm_count": conn_stats["llm_pairs"],
        "matched (TP)": conn_stats["recall_tp"],
        "precision": _fmt(conn_stats["precision"]),
        "recall": _fmt(conn_stats["recall"]),
    })
    class_breakdown = pd.DataFrame(class_rows)

    summary = pd.DataFrame([
        {"Metric": "Ground truth count", "Value": len(gt)},
        {"Metric": "LLM extracted count", "Value": len(llm)},
        {"Metric": "True Positives (matched elements)", "Value": tp},
        {"Metric": "Named correct (units/patterns by name)", "Value": named_correct},
        {"Metric": "Connector unit-pairs (GT / LLM)", "Value": f"{conn_stats['gt_pairs']} / {conn_stats['llm_pairs']}"},
        {"Metric": "Connector pair TP (recall / precision)", "Value": f"{conn_stats['recall_tp']} / {conn_stats['precision_tp']}"},
        {"Metric": "False Positives", "Value": fp},
        {"Metric": "False Negatives", "Value": fn},
        {"Metric": "Precision (units/patterns by name, connectors by unit-pair)", "Value": _fmt(full_precision)},
        {"Metric": "Recall (units/patterns by name, connectors by unit-pair)", "Value": _fmt(full_recall)},
        {"Metric": "Match threshold (cosine)", "Value": threshold},
    ])

    def blob(rec, prefix):
        out = {f"{prefix}_{s['name']}": (fixes_to_text(rec.get(s["name"]))
                                         if s["name"] == "fixedType" else rec.get(s["name"]))
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
            "GT_fixedType": fixes_to_text(gt[j].get("fixedType")),
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
            "LLM_fixedType": fixes_to_text(llm[i].get("fixedType")),
            "closest_GT_ID": gt[int(sim[i].argmax())]["id"] if len(gt) else None,
            "closest_GT_name": gt[int(sim[i].argmax())].get("name") if len(gt) else None,
            "closest_GT_description": gt[int(sim[i].argmax())].get("description") if len(gt) else None,
            "best_similarity": round(float(sim[i].max()), 4) if len(gt) else 0.0,
        }
        for i in range(len(llm)) if i not in matched_llm
    ])

    # Per-type breakdown over the closed ARCHITECTURE_TYPES taxonomy: always the
    # same nine rows in the same order, zero-filled where a type does not occur,
    # so the sheet is comparable across documents and runs. The connector row is
    # then restated in unit-pairs so it sits on the same basis as Class_Breakdown
    # and the headline metric (see `apply_connector_pair_row`).
    type_breakdown = build_type_breakdown(gt, llm, pairs, taxonomy=ARCHITECTURE_TYPES)
    type_breakdown = apply_connector_pair_row(type_breakdown, conn_stats)

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
        "Type_Breakdown": type_breakdown,
        "stats": {"tp": tp, "fp": fp, "fn": fn,
                  "name_metrics": name_rows, "other_metrics": other_rows,
                  "class_breakdown": class_rows},
    }


# ---------------------------------------------------------------------------
# Averaging across repeated runs
# ---------------------------------------------------------------------------
def _is_number(v) -> bool:
    """Whether a report cell holds a number. The numpy scalar types matter: a
    whole-number column (the counts) comes back as np.int64, which is not an
    `int`, and treating one as non-numeric would average a count column to "-"."""
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)


def _avg(values) -> float | str:
    """Mean of the numeric values, rounded. "-" cells (a metric undefined in that
    run) are skipped rather than counted as 0, so a metric is averaged only over
    the runs that scored it; "-" when no run did."""
    nums = [float(v) for v in values if _is_number(v)]
    return round(sum(nums) / len(nums), 4) if nums else "-"


def _average_table(tables: list[pd.DataFrame], key: str) -> pd.DataFrame:
    """Average every non-key column of the same table across runs, rows matched
    on `key`. Row order and columns are taken from the first run."""
    columns = list(tables[0].columns)
    rows = []
    for k in tables[0][key]:
        row = {key: k}
        for col in columns[1:]:
            row[col] = _avg([t.loc[t[key] == k, col].iloc[0]
                             for t in tables if (t[key] == k).any()])
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def _average_summary(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """Average the Matching_Summary values across runs. Numbers average normally;
    the "GT / LLM" pair cells average side by side; anything else is constant
    across runs and taken from the first."""
    def avg_value(values):
        if all(_is_number(v) for v in values):
            return _avg(values)
        parts = [str(v).split("/") for v in values]
        if all(len(p) == 2 for p in parts):
            try:
                return f"{_avg([float(p[0]) for p in parts])} / {_avg([float(p[1]) for p in parts])}"
            except ValueError:
                pass
        return values[0]

    rows = [{"Metric": metric,
             "Value": avg_value([t.loc[t["Metric"] == metric, "Value"].iloc[0] for t in tables])}
            for metric in tables[0]["Metric"]]
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def average_architecture_reports(reports: list[dict]) -> dict:
    """Average the metric tables across N `evaluate_architecture` reports — the
    repeated extraction runs over one document — into tables of the same shape.

    Only the metric tables are averaged. The per-element sheets (Matched_TP, the
    false positive / negative lists) belong to one run each and stay in that run's
    own workbook. Type_Breakdown is averaged by
    `evaluator_service.average_type_breakdowns`, which this report shape already
    fits.
    """
    if not reports:
        raise ValueError("average_architecture_reports requires at least one report")
    return {
        "Field_Metrics_Name": _average_table([r["Field_Metrics_Name"] for r in reports], "field"),
        "Field_Metrics_Other": _average_table([r["Field_Metrics_Other"] for r in reports], "field"),
        "Class_Breakdown": _average_table([r["Class_Breakdown"] for r in reports], "class"),
        "Matching_Summary": _average_summary([r["Matching_Summary"] for r in reports]),
    }


def write_average_architecture_report(reports: list[dict], output_path) -> None:
    """Write the across-runs average to a standalone workbook, laid out like a
    single run's: a Field_Metrics sheet (the name rows, then the other fields'
    accuracy rows) plus Class_Breakdown and Matching_Summary."""
    avg = average_architecture_reports(reports)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        name_df = avg["Field_Metrics_Name"]
        name_df.to_excel(w, sheet_name="Field_Metrics", index=False, startrow=0)
        avg["Field_Metrics_Other"].to_excel(
            w, sheet_name="Field_Metrics", index=False, startrow=len(name_df) + 3)
        avg["Class_Breakdown"].to_excel(w, sheet_name="Class_Breakdown", index=False)
        avg["Matching_Summary"].to_excel(w, sheet_name="Matching_Summary", index=False)


def evaluate_architecture(gt_path, llm_path, output_path, threshold=0.75):
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

    # Pass 1 — named elements (everything except connectors) matched on name
    # alone, by `match_named_elements`, with no class or type gate. Dropping the
    # gate lets a unit match a pattern (or a Service match a Component) so a
    # misclassified element is measured on `type` instead of hidden as an
    # unmatched FP/FN pair.
    nonconn_llm = [i for i in range(len(llm)) if not is_connector(llm[i])]
    nonconn_gt = [j for j in range(len(gt)) if not is_connector(gt[j])]
    pairs = match_named_elements(llm, gt, sim, threshold, nonconn_llm, nonconn_gt)

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

    type_breakdown_path = Path(output_path).with_name(Path(output_path).stem + "_by_type.xlsx")
    with pd.ExcelWriter(type_breakdown_path, engine="openpyxl") as w:
        report["Type_Breakdown"].to_excel(w, sheet_name="Type_Breakdown", index=False)
    return report