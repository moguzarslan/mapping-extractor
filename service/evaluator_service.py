"""
Evaluate LLM-extracted requirements against a ground-truth Excel, per field.

Usage:
    python evaluate.py <ground_truth_requirement.xlsx> <llm_extraction.json> <output_report.xlsx> \
        [--llm-concepts <llm_concepts.json>] [threshold]

`<ground_truth_requirement.xlsx>` is the combined GT workbook: a
"Requirements" sheet plus an optional "Concepts" sheet (Document ID, Concept
ID, Description, Page Number) used to resolve the `concept` field.

Defaults:
    threshold = 0.75  (embedding cosine similarity below this is not a match)

What it does
------------
1. Matches each LLM-extracted requirement to at most one ground-truth (GT)
   requirement using the *description* text (cosine similarity of sentence
   embeddings, greedy one-to-one assignment at `threshold`). This produces the
   true positives (TP) the per-field scoring is built on.
2. Reports two separate per-field metric tables, since `description` (the
   matching anchor) is the only field with genuinely distinct Precision and
   Recall — and therefore the only one carrying an F1, their harmonic mean.
   Every other field is scored as a success rate over the matched pairs, where
   Precision and Recall are equal by construction, so it is reported as a single
   Accuracy metric instead:

       Description field (requirement-level):
       field           precision   recall   f1   mean semantic meaning
       description        x          x       x            x

       Other fields (accuracy over matched pairs):
       field           accuracy
       type               x
       pageNumber         x
       concept            x
       categorization     x
       relatedTo          x
       fixes              x

   - Precision/Recall/Accuracy are computed for every field except `id` (id is
     the key used to resolve cross-references, not a scored field).
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
    concept                each side's conceptId is resolved to its concept's
                           text (LLM: `<stem>_concepts.json`, id -> name; GT:
                           the "Concepts" sheet of the ground truth workbook,
                           Concept ID -> Description) and the two texts are
                           compared by cosine similarity >= threshold. A value
                           that is not a known id (e.g. legacy free text) falls back to
                           being compared as its own raw text. The two sides
                           use unrelated id namespaces, so resolving to text
                           first is required — same approach as
                           `architecturalDecisionSource` in the decision
                           evaluator. A concept ID prefixed with '*' in the
                           GT "Concepts" sheet (or referenced with that prefix
                           directly) is a manual "ignore this concept"
                           annotation (see `load_excluded_concept_ids`): a
                           pair referencing it is dropped from the `concept`
                           field's scoring on both sides, as if unpopulated,
                           rather than counted as a mismatch.
    description, fixes      cosine similarity >= threshold
    relatedTo              both criteria point at the *same* parent requirement,
                           resolved through the matching (so the differing
                           LLM vs GT id namespaces never get compared directly)

Because `description` is the matching anchor, its Precision/Recall equal the
requirement-level Precision/Recall, its F1 is their harmonic mean, and its Mean
Semantic Meaning is the mean similarity of the matched pairs.

Output: a single compacted xlsx with four sheets — Metrics (requirement-level
summary, stacked on the description field's precision/recall/f1/mean-semantic
table, stacked on every other field's accuracy table), Matched,
False_Positives, False_Negatives. A side-car `<stem>_by_type.xlsx` is also written with a
Type_Breakdown sheet: per requirement-`type` value (e.g. Functional,
Non-Functional), the successful extractions (TP), false positives, false
negatives, precision and recall — ordered most successful (most TPs) to least,
so the weakest-performing requirement types stand out.
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
     "gt": ["Concept"],                                       "kind": "concept",     "semantic": True},
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


def resolve_concept_text(v, idx: dict) -> str | None:
    """Resolve a concept reference (a conceptId, or legacy free text) to its
    canonical text: looked up in `idx` (id -> name/description) when it is a
    known id, else kept as its own raw text. Either way, a leading Volere-style
    label ("10a.", "12th.") is stripped before comparison."""
    if _is_blank(v):
        return None
    key = str(v).strip()
    text = idx.get(key, key)
    return _CONCEPT_PREFIX.sub("", text).strip() or None


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
    """Read the GT requirements sheet into canonical records; merge wrapped
    continuation rows. Reads the "Requirements" sheet of the combined
    `<stem>_ground_truth_requirement.xlsx` workbook (a Requirements sheet plus
    an optional Concepts sheet, see `load_ground_truth_concepts`), falling
    back to the first sheet for older single-sheet GT files."""
    xl = pd.ExcelFile(xlsx_path)
    sheet = "Requirements" if "Requirements" in xl.sheet_names else xl.sheet_names[0]
    df = xl.parse(sheet)
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


def load_llm_concepts(json_path_or_data) -> dict[str, str]:
    """Read the LLM's `<stem>_concepts.json` (list of {id, name}, or already-
    parsed data) into {conceptId -> name} — the resolver for a requirement's
    `concept` field, which stores a conceptId rather than free text."""
    if isinstance(json_path_or_data, (list, dict)):
        data = json_path_or_data
    else:
        with open(json_path_or_data) as f:
            data = json.load(f)
    items = data.get("concepts", data) if isinstance(data, dict) else data
    id_to_name = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cid, name = item.get("id"), item.get("name")
        if not _is_blank(cid) and not _is_blank(name):
            id_to_name[str(cid).strip()] = str(name).strip()
    return id_to_name


def load_ground_truth_concepts(xlsx_path: str) -> dict[str, str]:
    """Read the "Concepts" sheet of the combined GT workbook (Document ID,
    Concept ID, Description, Page Number) into {Concept ID -> Description} —
    the resolver for a GT requirement's `concept` field. Returns {} when the
    workbook has no dedicated Concepts sheet (older GT files with no concept
    catalog), so callers can treat `concept` as unresolvable rather than fail."""
    xl = pd.ExcelFile(xlsx_path)
    if "Concepts" not in xl.sheet_names:
        return {}
    df = xl.parse("Concepts")
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
        desc = row[desc_col]
        if not _is_blank(desc):
            id_to_text[str(cid).strip()] = str(desc).strip()
    return id_to_text


CONCEPT_EXCLUDE_MARK = "*"


def load_excluded_concept_ids(xlsx_path: str) -> set[str]:
    """Read the "Concepts" sheet and return the set of Concept IDs (star
    stripped) whose ID is marked with a leading '*' — a manual annotation on
    the GT concept catalog meaning "ignore this concept in the `concept`
    field's evaluation" (e.g. a duplicate or otherwise unreliable catalog
    entry). Requirements reference such a concept by its plain, unstarred ID
    (the star lives on the catalog definition, not the reference), so the
    star is stripped here for membership checks against those references.
    Returns an empty set when the workbook has no Concepts sheet."""
    xl = pd.ExcelFile(xlsx_path)
    if "Concepts" not in xl.sheet_names:
        return set()
    df = xl.parse("Concepts")
    id_col = _pick(list(df.columns), ["Concept ID", "ConceptID", "ID", "Id", "id"])
    if id_col is None:
        return set()

    excluded = set()
    for cid in df[id_col]:
        if _is_blank(cid):
            continue
        s = str(cid).strip()
        if s.startswith(CONCEPT_EXCLUDE_MARK):
            excluded.add(s[len(CONCEPT_EXCLUDE_MARK):].strip())
    return excluded


def is_excluded_concept_ref(v, excluded_ids: set[str]) -> bool:
    """True if a raw `concept` value (LLM or GT) should be ignored: it is
    itself marked with a leading '*' (a direct exclusion), or it references a
    catalog concept that `load_excluded_concept_ids` flagged as excluded."""
    if _is_blank(v):
        return False
    s = str(v).strip()
    if s.startswith(CONCEPT_EXCLUDE_MARK):
        return True
    return s in excluded_ids


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


def greedy_match(sim: np.ndarray, threshold: float) -> list[tuple[int, int, float]]:
    """One-to-one greedy assignment over similarity-sorted pairs, score >= threshold."""
    n_llm, n_gt = sim.shape
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
        pairs.append((i, j, float(score)))
        used_llm.add(i)
        used_gt.add(j)
    return pairs


def optimal_match(sim: np.ndarray, threshold: float,
                  llm_types: list[str | None] | None = None,
                  gt_types: list[str | None] | None = None,
                  type_override_sim: float = 0.9) -> list[tuple[int, int, float]]:
    """Optimal one-to-one assignment, solved exactly with the Hungarian algorithm.

    A pair (i, j) is eligible when its score >= threshold and, if llm_types and
    gt_types are given, the types are compatible (case-insensitive match, a
    missing type on either side acting as a wildcard, or the pair's score
    meeting `type_override_sim`, which relaxes that gate for near-exact text
    matches). Each eligible pair is weighted 1 + score and every other cell 0,
    so the assignment maximises the NUMBER of eligible matches first and uses
    similarity only as the tie-breaker.

    Because it is the exact solution to the assignment problem a greedy
    similarity-sorted walk only approximates, the matched count is always >= a
    greedy walk's: it never drops a valid >= threshold match because a
    higher-similarity pair stole a node the match needed.
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
                 *, llm_idx: dict, gt_idx: dict, llm_to_gt: dict,
                 llm_concept_idx: dict | None = None,
                 gt_concept_idx: dict | None = None) -> tuple[bool, float | None]:
    """Return (agrees, similarity_or_None) for one matched pair on one field."""
    kind = spec["kind"]
    a, b = llm_rec.get(spec["name"]), gt_rec.get(spec["name"])

    if kind == "categorical":
        return (norm_categorical(a) == norm_categorical(b)), None
    if kind == "page":
        return (norm_page(a) == norm_page(b)), None
    if kind == "concept":
        # Each side stores a conceptId (LLM: concepts.json; GT: the ground
        # truth concept xlsx), resolved to text before comparison since the
        # two id namespaces are unrelated.
        ta = resolve_concept_text(a, llm_concept_idx or {})
        tb = resolve_concept_text(b, gt_concept_idx or {})
        sim = text_similarity(ta, tb) if (ta and tb) else 0.0
        return (sim >= threshold), sim
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


def f1_score(precision: float | None, recall: float | None) -> float | None:
    """Harmonic mean of precision and recall — None when either is undefined, 0.0
    when both are zero.

    Reported for the matching anchor only, here and in the architecture and
    decision evaluators alike. Every other field is scored as a success rate over
    the pairs the anchor already matched, where precision and recall are equal by
    construction: their harmonic mean would just restate the accuracy already in
    the table.
    """
    if precision is None or recall is None:
        return None
    return (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0


def build_type_breakdown(gt: list[dict], llm: list[dict],
                         pairs: list[tuple[int, int, float]],
                         type_field: str = "type",
                         taxonomy: list[str] | None = None) -> pd.DataFrame:
    """Per-type Precision/Recall, ranked most successful first.

    Follows the multi-class `classification_report` convention: a matched pair
    counts as a successful extraction (TP) for a type only when BOTH sides
    agree on that type. A matched pair with disagreeing types therefore counts
    as a false negative for the GT's type and a false positive for the LLM's
    type, and an unmatched item falls back to its own recorded type. Items
    with no value in `type_field` are grouped under "(unspecified)".

    `taxonomy` fixes the row set. When it is given (a closed vocabulary, e.g.
    the architecture element types), every one of its values gets a row — with
    explicit zeros when the type occurs on neither side — and the rows come out
    in taxonomy order, so the table has the same shape for every document and
    every run. Any type found in the data but absent from the taxonomy is still
    reported, appended after the taxonomy rows, so nothing is silently dropped.
    When `taxonomy` is None (the open-vocabulary case, e.g. requirement types)
    the rows are whatever occurs in the data, ranked most successful first.
    """
    llm_to_gt = {i: j for i, j, _ in pairs}
    gt_to_llm = {j: i for i, j, _ in pairs}

    def type_of(rec):
        return norm_categorical(rec.get(type_field)) or "(unspecified)"

    counts: dict[str, dict[str, int]] = {}

    def bump(t, key):
        counts.setdefault(t, {"tp": 0, "fp": 0, "fn": 0, "gt_count": 0, "llm_count": 0})[key] += 1

    # Seed the closed vocabulary so a type absent from both sides still gets a
    # row of zeros instead of vanishing from the report.
    taxonomy_keys = [norm_categorical(t) for t in (taxonomy or [])]
    for t in taxonomy_keys:
        counts.setdefault(t, {"tp": 0, "fp": 0, "fn": 0, "gt_count": 0, "llm_count": 0})

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
        rows.append({
            "type": t,
            "gt_count": c["gt_count"],
            "llm_count": c["llm_count"],
            "successful_extractions": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": _fmt(precision),
            "recall": _fmt(recall),
        })

    if taxonomy_keys:
        # Fixed row order, so the table lines up across documents and runs. Any
        # type outside the taxonomy (a typo, "(unspecified)") lands after it.
        order = {t: k for k, t in enumerate(taxonomy_keys)}
        rows.sort(key=lambda r: (order.get(r["type"], len(order)),
                                 -r["successful_extractions"], r["type"]))
    else:
        rows.sort(key=lambda r: (r["successful_extractions"], r["gt_count"]), reverse=True)
    return pd.DataFrame(rows, columns=["type", "gt_count", "llm_count", "successful_extractions",
                                       "false_positives", "false_negatives",
                                       "precision", "recall"])


def build_report(gt, llm, sim, pairs, threshold, forced_pairs=None,
                 llm_concept_idx=None, gt_concept_idx=None,
                 excluded_concept_ids=None):
    """Assemble the evaluation report from a set of matched pairs.

    `forced_pairs` is an optional set of (llm_idx, gt_idx) that were matched by a
    human (e.g. through the GUI) rather than by the automatic matcher. For such
    pairs the *description anchor* is counted as agreeing regardless of its
    TF-IDF similarity, because the human has asserted the two requirements
    correspond. All other fields are still compared on their real values.

    `llm_concept_idx` / `gt_concept_idx` resolve a `concept` field's conceptId
    to text (see `load_llm_concepts` / `load_ground_truth_concepts`); omit them
    to fall back to comparing each side's raw `concept` value as text.

    `excluded_concept_ids` (see `load_excluded_concept_ids`) is the set of GT
    concept IDs manually marked with a leading '*' to be ignored. For a pair
    where either side's `concept` reference is excluded, the field is treated
    as unpopulated on BOTH sides for that pair — dropped from the concept
    field's precision/recall/accuracy the same way an unpopulated field is,
    rather than counted as a miss. Other fields are unaffected.
    """
    forced_pairs = forced_pairs or set()
    excluded_concept_ids = excluded_concept_ids or set()
    matched_llm = {i for i, _, _ in pairs}
    matched_gt  = {j for _, j, _ in pairs}
    llm_to_gt   = {i: j for i, j, _ in pairs}

    tp, fp, fn = len(pairs), len(llm) - len(pairs), len(gt) - len(pairs)

    llm_idx = build_id_index(llm)
    gt_idx  = build_id_index(gt)

    field_rows, count_rows = [], []
    for spec in FIELD_SPECS:
        name = spec["name"]
        is_concept = name == "concept"

        def _concept_ok(r):
            return not is_excluded_concept_ref(r.get("concept"), excluded_concept_ids)

        if is_concept:
            llm_has_total = sum(field_present(r, spec) and _concept_ok(r) for r in llm)
            gt_has_total  = sum(field_present(r, spec) and _concept_ok(r) for r in gt)
        else:
            llm_has_total = sum(field_present(r, spec) for r in llm)
            gt_has_total  = sum(field_present(r, spec) for r in gt)

        correct, sims = 0, []
        llm_has_matched, gt_has_matched = 0, 0
        for i, j, _ in pairs:
            llm_populated = field_present(llm[i], spec)
            gt_populated  = field_present(gt[j], spec)
            if is_concept and (not _concept_ok(llm[i]) or not _concept_ok(gt[j])):
                llm_populated = gt_populated = False
            if llm_populated:
                llm_has_matched += 1
            if gt_populated:
                gt_has_matched += 1
            if not (llm_populated and gt_populated):
                continue
            agree, s = field_agrees(spec, llm[i], gt[j], threshold,
                                    llm_idx=llm_idx, gt_idx=gt_idx, llm_to_gt=llm_to_gt,
                                    llm_concept_idx=llm_concept_idx, gt_concept_idx=gt_concept_idx)
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

        row = {
            "field": name,
            "precision": _fmt(precision),
            "recall": _fmt(recall),
            "mean semantic meaning": _fmt(mean_sem),
        }
        if name == DESC_REQUIRED_NAME:
            # Only the anchor carries an F1: it is the one field whose precision
            # and recall are genuinely distinct (see `f1_score`).
            row["f1"] = _fmt(f1_score(precision, recall))
        field_rows.append(row)
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
    field_metrics_desc  = pd.DataFrame(desc_rows,  columns=["field", "precision", "recall", "f1", "mean semantic meaning"])
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

    The Metrics sheet stacks, top to bottom: the requirement-level summary
    (counts, precision, recall, threshold), the description field's metrics
    (precision, recall, f1, mean semantic meaning — the only field with genuinely
    distinct precision/recall, since it is the matching anchor, and so the only
    one whose F1 is not just a restatement of its accuracy), and every
    other field's metrics (a single accuracy column, since precision == recall
    for a field scored as a success rate over matched pairs).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = report["Requirement_Matching"]
    desc_metrics = report["Field_Metrics_Desc"]
    other_metrics = report["Field_Metrics_Other"]
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="Metrics", index=False, startrow=0)
        desc_start = len(summary) + 3  # one blank row separator
        desc_metrics.to_excel(w, sheet_name="Metrics", index=False, startrow=desc_start)
        other_start = desc_start + len(desc_metrics) + 3  # one blank row separator
        other_metrics.to_excel(w, sheet_name="Metrics", index=False, startrow=other_start)
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


def average_reports(reports: list[dict]) -> dict:
    """Average the Requirement_Matching, Field_Metrics_Desc and
    Field_Metrics_Other tables across N `evaluate()` reports (e.g. repeated
    extraction runs over the same document) into a single report of the same
    shape.

    Each metric cell is either a rounded float or "-" (see `_fmt`); "-" /
    non-numeric cells are excluded from the average rather than treated as 0,
    so a field that was unscored in some runs is averaged only over the runs
    that did score it. The threshold row is constant across runs and taken
    as-is from the first report.

    Every metric is averaged the same way — the mean of the runs' values — F1
    included. The averaged F1 is therefore the mean of the runs' F1 scores, not
    the F1 recomputed from the averaged precision and recall; the two differ
    slightly, and the mean-of-runs form is what makes F1 read like every other
    cell in the table.
    """
    if not reports:
        raise ValueError("average_reports requires at least one report")

    def avg(values):
        nums = [v for v in values if isinstance(v, (int, float))]
        return round(sum(nums) / len(nums), 4) if nums else "-"

    summaries = [r["Requirement_Matching"] for r in reports]
    summary_rows = []
    for metric in summaries[0]["Metric"]:
        values = [s.loc[s["Metric"] == metric, "Value"].iloc[0] for s in summaries]
        if metric == "Match threshold (cosine)":
            summary_rows.append({"Metric": metric, "Value": values[0]})
        else:
            summary_rows.append({"Metric": metric, "Value": avg(values)})
    avg_summary = pd.DataFrame(summary_rows, columns=["Metric", "Value"])

    desc_tables = [r["Field_Metrics_Desc"] for r in reports]
    desc_rows = []
    for field in desc_tables[0]["field"]:
        precisions = [t.loc[t["field"] == field, "precision"].iloc[0] for t in desc_tables]
        recalls = [t.loc[t["field"] == field, "recall"].iloc[0] for t in desc_tables]
        f1s = [t.loc[t["field"] == field, "f1"].iloc[0] for t in desc_tables]
        sems = [t.loc[t["field"] == field, "mean semantic meaning"].iloc[0] for t in desc_tables]
        desc_rows.append({
            "field": field,
            "precision": avg(precisions),
            "recall": avg(recalls),
            "f1": avg(f1s),
            "mean semantic meaning": avg(sems),
        })
    avg_field_metrics_desc = pd.DataFrame(
        desc_rows, columns=["field", "precision", "recall", "f1", "mean semantic meaning"])

    other_tables = [r["Field_Metrics_Other"] for r in reports]
    other_rows = []
    for field in other_tables[0]["field"]:
        accuracies = [t.loc[t["field"] == field, "accuracy"].iloc[0] for t in other_tables]
        other_rows.append({"field": field, "accuracy": avg(accuracies)})
    avg_field_metrics_other = pd.DataFrame(other_rows, columns=["field", "accuracy"])

    return {
        "Requirement_Matching": avg_summary,
        "Field_Metrics_Desc": avg_field_metrics_desc,
        "Field_Metrics_Other": avg_field_metrics_other,
    }


def write_average_report(reports: list[dict], output_path) -> None:
    """Write the across-runs average Metrics sheet (same layout as
    `write_report`'s Metrics sheet: requirement summary, then the description
    field's precision/recall/f1/mean-semantic table, then every other field's
    accuracy table) to a standalone workbook."""
    avg = average_reports(reports)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = avg["Requirement_Matching"]
    desc_metrics = avg["Field_Metrics_Desc"]
    other_metrics = avg["Field_Metrics_Other"]
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="Metrics", index=False, startrow=0)
        desc_start = len(summary) + 3  # one blank row separator
        desc_metrics.to_excel(w, sheet_name="Metrics", index=False, startrow=desc_start)
        other_start = desc_start + len(desc_metrics) + 3  # one blank row separator
        other_metrics.to_excel(w, sheet_name="Metrics", index=False,
                               startrow=other_start)


def average_type_breakdowns(reports: list[dict]) -> pd.DataFrame:
    """Average the Type_Breakdown tables across N `evaluate()` reports (e.g.
    repeated extraction runs over the same document) into a single
    per-type breakdown of the same shape as `build_type_breakdown`'s output.

    A `type` value is not guaranteed to appear in every run's breakdown (e.g.
    an "(unspecified)" type only shows up in a run where the LLM omitted the
    type field). For the count columns (gt_count, llm_count,
    successful_extractions, false_positives, false_negatives) a run missing a
    type genuinely had zero occurrences of it, so it is averaged in as 0 over
    all N runs. For precision/recall — which are "-" (undefined) rather than 0
    when a type doesn't occur — only the runs where the type is present
    contribute to the average, same convention as `average_reports`.

    Row order is inherited from the input tables (first-seen across the runs),
    so it keeps whatever convention `build_type_breakdown` used: taxonomy order
    for a closed vocabulary, most-successful-first for an open one.
    """
    if not reports:
        raise ValueError("average_type_breakdowns requires at least one report")

    tables = [r["Type_Breakdown"] for r in reports]
    n = len(tables)

    seen, all_types = set(), []
    for t in tables:
        for ty in t["type"]:
            if ty not in seen:
                seen.add(ty)
                all_types.append(ty)

    count_cols = ["gt_count", "llm_count", "successful_extractions",
                  "false_positives", "false_negatives"]
    ratio_cols = ["precision", "recall"]

    def avg_ratio(values):
        nums = [v for v in values if isinstance(v, (int, float))]
        return round(sum(nums) / len(nums), 4) if nums else "-"

    rows = []
    for ty in all_types:
        row = {"type": ty}
        for col in count_cols:
            total = sum(
                (t.loc[t["type"] == ty, col].iloc[0] if (t["type"] == ty).any() else 0)
                for t in tables
            )
            row[col] = round(total / n, 4)
        for col in ratio_cols:
            values = [t.loc[t["type"] == ty, col].iloc[0] for t in tables if (t["type"] == ty).any()]
            row[col] = avg_ratio(values)
        rows.append(row)

    # No re-sort: `all_types` already carries the input tables' row order.
    return pd.DataFrame(rows, columns=["type"] + count_cols + ratio_cols)


def write_average_type_breakdown_report(reports: list[dict], output_path) -> None:
    """Write the across-runs average Type_Breakdown as its own single-sheet
    workbook, named `<stem>_avg_by_type.xlsx` (same side-car convention
    `write_type_breakdown_report` uses for a single run's `_by_type.xlsx`)."""
    avg_breakdown = average_type_breakdowns(reports)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        avg_breakdown.to_excel(w, sheet_name="Type_Breakdown", index=False)


def evaluate(gt_path, llm_path, output_path, threshold=0.75, llm_concepts_path=None):
    """
    `gt_path` is the combined GT workbook — a "Requirements" sheet plus an
    optional "Concepts" sheet (Concept ID -> Description), the latter used to
    resolve a GT requirement's `concept` field to text. A Concept ID prefixed
    with '*' in that sheet (see `load_excluded_concept_ids`) is ignored in the
    `concept` field's scoring. `llm_concepts_path` points at the LLM's
    `<stem>_concepts.json` (a path, or already-parsed data) used to resolve
    the LLM side's conceptId the same way. Either concept source may be
    absent, in which case `concept` values are compared as their own raw
    text.
    """
    gt  = load_ground_truth(gt_path)
    llm = load_llm_extraction(llm_path)
    if not gt or not llm:
        raise ValueError(f"Empty input — gt={len(gt)} llm={len(llm)}")

    gt_concept_idx       = load_ground_truth_concepts(gt_path)
    excluded_concept_ids = load_excluded_concept_ids(gt_path)
    llm_concept_idx      = load_llm_concepts(llm_concepts_path) if llm_concepts_path else {}

    sim   = compute_similarity([norm_text(r["description"]) or "" for r in gt],
                               [norm_text(r["description"]) or "" for r in llm])

    pairs = greedy_match(sim, threshold)
    report = build_report(gt, llm, sim, pairs, threshold,
                          llm_concept_idx=llm_concept_idx, gt_concept_idx=gt_concept_idx,
                          excluded_concept_ids=excluded_concept_ids)
    write_report(report, output_path)

    type_breakdown_path = Path(output_path).with_name(Path(output_path).stem + "_by_type.xlsx")
    write_type_breakdown_report(report, type_breakdown_path)
    return report


