#!/usr/bin/env python3
#
# Requires `langdetect` and `fast-langdetect` (through translation_audit.py,
# which defines what counts as untranslated): .venv/bin/pip install langdetect
# fast-langdetect. Calls the Gemini API (translation) and the embedding model
# the evaluators use (Vertex AI), so the project's .env credentials must work.
"""
Does an untranslated anchor sentence distort the evaluation?

Every evaluator matches an LLM extraction to the ground truth on an anchor
(requirement `description`, decision `rationale`, architecture unit/pattern
`name` and connector `description`) against an English ground truth at a
0.75 threshold. The gemini-3-5 extractions left many anchors in Spanish or
Catalan (see analysis/script/translation_audit.py). This experiment measures
what that did to the scores end to end.

Every gemini-3-5 run's anchor matching is recomputed twice — once with the
anchors exactly as extracted, once with each untranslated anchor replaced by
its faithful English translation — using each evaluator's own matching at its
own threshold, and the resulting TP and F1 are compared:
  requirement   `greedy_match` on descriptions
  decision      `optimal_match` on rationales
  architecture  the architecture evaluator's full chain — `match_named_elements`
                (exact name, kind-stripped name, then shared token + cosine)
                for units/patterns, `match_connectors` on descriptions — and
                its own `build_report` for the headline "all elements"
                precision/recall/F1. This covers the lexical name rules too,
                not only embedding similarity.
Alongside, for each untranslated anchor, the summary reports the mean of
cos(translation, closest GT anchor) - cos(original, that same GT anchor): how
much similarity the source language costs against the item it should match.
The as-extracted TP is also checked against the TP in the existing evaluation
report, to show the recomputation reproduces the reported numbers — so any
difference between the two conditions is due to the translation alone.

What counts as untranslated follows translation_audit.py: sentences
(descriptions, rationales, connector descriptions) use its `classify_prose`
rule; architecture unit/pattern names are short labels, so — like concept
names there — they are checked with fastText (fast-langdetect, "lite" model).
Either way an anchor only counts as untranslated if its translation actually
differs from it: a name the detector flags but that has no translation (a
product name such as "Traefik") comes back unchanged, so it is neither
counted nor altered.

Translations come from Gemini (temperature 0) with a faithful, literal
translation instruction, and are cached in TRANSLATION_CACHE: a rerun reuses
them rather than re-translating, so the numbers are reproducible and the
exact translations used can be inspected there.

Report sheets: Summary (one row per stage) and End to end (per run).

Scope: requirement, decision and architecture anchors of every gemini-3-5
run (outputs/gemini/<stage>/validation/gemini-3-5/). Only anchors are
translated; an architecture element's non-anchor description is left as it
was, since it does not decide whether the element is matched.

Nothing under outputs/, resource/ or any code is modified. Writes the report
and the translation cache under analysis/report/.

Usage (from any directory):
    analysis/script/cross_lingual_similarity.py
"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = PROJECT_ROOT / "analysis" / "report"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
_REEXEC_FLAG = "CROSS_LINGUAL_SIMILARITY_REEXEC"


def _sysctl(name: str) -> str:
    try:
        return subprocess.run(["/usr/sbin/sysctl", "-n", name],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def _ensure_project_interpreter() -> None:
    """Re-run the script under the project's virtualenv, natively — see the
    identical guard in analysis/script/ispartof_causes.py for why."""
    if os.environ.get(_REEXEC_FLAG) or not VENV_PYTHON.exists():
        return
    in_venv = Path(sys.prefix).resolve() == VENV_PYTHON.parent.parent.resolve()
    translated = sys.platform == "darwin" and _sysctl("sysctl.proc_translated") == "1"
    if in_venv and not translated:
        return
    command = [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
    if sys.platform == "darwin" and _sysctl("hw.optional.arm64") == "1":
        command = ["/usr/bin/arch", "-arm64", *command]
    os.environ[_REEXEC_FLAG] = "1"
    os.execv(command[0], command)


_ensure_project_interpreter()

import copy  # noqa: E402
import importlib.util  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT))
from service.architecture_evaluator_service import (  # noqa: E402
    attach_endpoint_names,
    build_report as architecture_report,
    is_connector,
    load_ground_truth as load_architecture_ground_truth,
    load_llm_extraction as load_architecture_llm_extraction,
    match_connectors,
    match_named_elements,
    match_text,
)
from service.decision_evaluator_service import (  # noqa: E402
    load_ground_truth_decisions,
    load_llm_decisions,
)
from service.evaluator_service import (  # noqa: E402
    compute_similarity,
    f1_score,
    greedy_match,
    load_ground_truth,
    load_llm_extraction,
    norm_text,
    optimal_match,
)

_audit_spec = importlib.util.spec_from_file_location(
    "translation_audit", Path(__file__).resolve().parent / "translation_audit.py")
_audit = importlib.util.module_from_spec(_audit_spec)
_audit_spec.loader.exec_module(_audit)
# The untranslated-text rules and the (cached) translator are translation_audit's,
# so both analyses agree on what is untranslated and share one set of translations.
classify_prose = _audit.classify_prose
is_untranslated_name = _audit.fasttext_flags_name
translate_all = _audit.translate_all
TRANSLATION_CACHE = _audit.TRANSLATION_CACHE
_norm = _audit._norm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
THRESHOLD = 0.75  # the evaluators' default anchor threshold
DOCS = ["CF_M01", "CF_M05", "CF_M08", "CF_M04", "CF_M06", "CF_M09"]

GEMINI = PROJECT_ROOT / "outputs" / "gemini"
EVALUATION = PROJECT_ROOT / "outputs" / "evaluation"
GROUND_TRUTHS = PROJECT_ROOT / "resource" / "groundTruths"

STAGES = {
    "requirement": {
        "llm_glob": "requirement/validation/gemini-3-5/{doc}/*/{doc}_requirements.json",
        "gt": lambda doc: GROUND_TRUTHS / "requirement" / f"{doc}_ground_truth_requirement.xlsx",
        "load_gt": load_ground_truth,
        "load_llm": load_llm_extraction,
        "anchor": "description",
        "match": greedy_match,
        "report": lambda doc, run: (EVALUATION / "requirement" / "validation" / "gemini-3-5-flash-lite"
                                    / doc / run / f"{doc}_req_eval.xlsx", "Metrics"),
    },
    "decision": {
        "llm_glob": "decision/validation/gemini-3-5/{doc}/*/{doc}_decision.json",
        "gt": lambda doc: GROUND_TRUTHS / "decision" / f"{doc}_ground_truth_decision.xlsx",
        "load_gt": load_ground_truth_decisions,
        "load_llm": load_llm_decisions,
        "anchor": "rationale",
        "match": optimal_match,
        "report": lambda doc, run: (EVALUATION / "decision" / "validation" / "gemini-3-5"
                                    / doc / run / f"{doc}_decision_eval.xlsx", "Matching_Summary"),
    },
    # Matched record-by-record rather than on an anchor list — see
    # collect_architecture_runs / architecture_scores.
    "architecture": {
        "llm_glob": "architecture/validation/gemini-3-5/{doc}/*/{doc}_architecture.json",
        "gt": lambda doc: GROUND_TRUTHS / "architecture" / f"{doc}_ground_truth_architecture.xlsx",
        "report": lambda doc, run: (EVALUATION / "architecture" / "validation" / "gemini-3-5"
                                    / doc / run / f"{doc}_arch_eval.xlsx", "Matching_Summary"),
    },
}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def anchor_text(record: dict, field: str) -> str:
    return norm_text(record.get(field)) or ""


def is_untranslated(text: str) -> bool:
    verdict = classify_prose(re.sub(r"\s+", " ", text).strip())
    return verdict is not None and verdict[2] == "leak"


def reported_tp(stage: str, doc: str, run: str):
    path, sheet = STAGES[stage]["report"](doc, run)
    if not path.exists():
        return None
    df = pd.read_excel(path, sheet_name=sheet, header=None)
    for i in range(len(df)):
        if str(df.iat[i, 0]).strip().startswith("True Positives"):
            return df.iat[i, 1]
    return None


def collect_architecture_runs() -> list[dict]:
    """Architecture runs keep their full records — the evaluator matches on the
    record (name tiers, connector endpoints), not on an anchor string alone."""
    cfg = STAGES["architecture"]
    runs = []
    for doc in DOCS:
        gt_path = cfg["gt"](doc)
        if not gt_path.exists():
            continue
        gt_records = load_architecture_ground_truth(str(gt_path))
        for llm_path in sorted(GEMINI.glob(cfg["llm_glob"].format(doc=doc))):
            llm_records = load_architecture_llm_extraction(str(llm_path))
            leaks = []
            for i, rec in enumerate(llm_records):
                text = match_text(rec)
                if text and (is_untranslated(text) if is_connector(rec) else is_untranslated_name(text)):
                    leaks.append(i)
            runs.append({"stage": "architecture", "doc": doc, "run": llm_path.parent.name,
                         "gt": [match_text(r) for r in gt_records],
                         "llm": [match_text(r) for r in llm_records],
                         "gt_records": gt_records, "llm_records": llm_records,
                         "leaks": leaks})
    return runs


def collect_runs() -> list[dict]:
    runs = []
    for stage, cfg in STAGES.items():
        if stage == "architecture":
            runs.extend(collect_architecture_runs())
            continue
        for doc in DOCS:
            gt_path = cfg["gt"](doc)
            if not gt_path.exists():
                continue
            gt_anchors = [anchor_text(r, cfg["anchor"]) for r in cfg["load_gt"](str(gt_path))]
            for llm_path in sorted(GEMINI.glob(cfg["llm_glob"].format(doc=doc))):
                llm_anchors = [anchor_text(r, cfg["anchor"]) for r in cfg["load_llm"](str(llm_path))]
                runs.append({"stage": stage, "doc": doc, "run": llm_path.parent.name,
                             "gt": gt_anchors, "llm": llm_anchors,
                             "leaks": [i for i, t in enumerate(llm_anchors) if t and is_untranslated(t)]})
    return runs


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------
def similarity(gt: list[str], llm: list[str], attempts: int = 15, wait_s: int = 65) -> np.ndarray:
    """`compute_similarity`, retried when the embedding API's per-minute quota
    is hit. The evaluator caches every embedding it already received, so a
    retry resumes where the failed batch stopped."""
    for attempt in range(1, attempts + 1):
        try:
            return compute_similarity(gt, llm)
        except RuntimeError as quota_error:
            if "429" not in str(quota_error) or attempt == attempts:
                raise
            print(f"  embedding quota reached; waiting {wait_s}s (attempt {attempt}/{attempts})")
            time.sleep(wait_s)


def anchor_scores(n_llm_pop: int, n_gt_pop: int, pairs) -> dict:
    tp = len(pairs)
    p = tp / n_llm_pop if n_llm_pop else None
    r = tp / n_gt_pop if n_gt_pop else None
    return {"tp": tp, "precision": p, "recall": r, "f1": f1_score(p, r)}


def architecture_scores(gt_records: list[dict], llm_records: list[dict]) -> dict:
    """One run scored exactly as `evaluate_architecture` scores it: anchor
    similarity, name tiers for units/patterns, connector matching, then its
    own `build_report` headline over all elements. Records are copied — the
    evaluator annotates them."""
    gt, llm = copy.deepcopy(gt_records), copy.deepcopy(llm_records)
    sim = similarity([match_text(r) for r in gt], [match_text(r) for r in llm])
    attach_endpoint_names(llm)
    attach_endpoint_names(gt)
    pairs = match_named_elements(llm, gt, sim, THRESHOLD,
                                 [i for i, r in enumerate(llm) if not is_connector(r)],
                                 [j for j, r in enumerate(gt) if not is_connector(r)])
    pairs += match_connectors(llm, gt, sim, THRESHOLD,
                              [i for i, r in enumerate(llm) if is_connector(r)],
                              [j for j, r in enumerate(gt) if is_connector(r)])
    headline = architecture_report(gt, llm, sim, pairs, THRESHOLD)["Field_Metrics_Name"].iloc[0]

    def num(v):
        return None if v in ("-", None) else float(v)

    return {"tp": len(pairs), "precision": num(headline["precision"]),
            "recall": num(headline["recall"]), "f1": num(headline["f1"])}


def translated_architecture(llm_records: list[dict], leaks: list[int],
                            translations: dict[str, str]) -> list[dict]:
    """The records with each untranslated anchor replaced by its translation —
    the name of a unit/pattern, the description of a connector."""
    records = copy.deepcopy(llm_records)
    for i in leaks:
        field = "description" if is_connector(records[i]) else "name"
        records[i][field] = translations[match_text(llm_records[i])]
    return records


def closest_gt_differences(run: dict, translations: dict[str, str]) -> list[tuple[str, float]]:
    """For each untranslated anchor of the run: cos(translation, closest GT
    anchor) - cos(original, that same GT anchor). Architecture names are
    compared with GT names and connectors with GT connectors, as the evaluator
    matches them."""
    differences = []
    for i in run["leaks"]:
        source = run["llm"][i]
        if run["stage"] == "architecture":
            connector = is_connector(run["llm_records"][i])
            gt = [g for g, rec in zip(run["gt"], run["gt_records"]) if g and is_connector(rec) == connector]
        else:
            gt = [g for g in run["gt"] if g]
        if not gt:
            continue
        sim_translated = similarity(gt, [translations[source]])[0]
        j = int(sim_translated.argmax())
        differences.append((source, float(sim_translated[j] - similarity(gt, [source])[0][j])))
    return differences


def _r(x):
    return round(float(x), 4) if x is not None else None


def build_report(runs: list[dict], translations: dict[str, str]) -> dict[str, pd.DataFrame]:
    end_to_end, differences = [], []
    for run in runs:
        cfg = STAGES[run["stage"]]
        gt, llm = run["gt"], run["llm"]

        if run["stage"] == "architecture":
            src = architecture_scores(run["gt_records"], run["llm_records"])
            tr = architecture_scores(run["gt_records"],
                                     translated_architecture(run["llm_records"], run["leaks"], translations))
        else:
            llm_translated = [translations[t] if i in run["leaks"] else t for i, t in enumerate(llm)]
            n_llm_pop, n_gt_pop = sum(1 for t in llm if t), sum(1 for t in gt if t)
            src = anchor_scores(n_llm_pop, n_gt_pop, cfg["match"](similarity(gt, llm), THRESHOLD))
            tr = anchor_scores(n_llm_pop, n_gt_pop, cfg["match"](similarity(gt, llm_translated), THRESHOLD))
        end_to_end.append({
            "Stage": run["stage"], "Document": run["doc"], "Run": run["run"],
            "Untranslated anchors": len(run["leaks"]),
            "TP (existing report)": reported_tp(run["stage"], run["doc"], run["run"]),
            "TP as extracted": src["tp"], "TP translated": tr["tp"],
            "F1 as extracted": _r(src["f1"]), "F1 translated": _r(tr["f1"]),
        })
        differences += [{"Stage": run["stage"], "Document": run["doc"], "Source text": text,
                         "Difference": diff} for text, diff in closest_gt_differences(run, translations)]

    e2e = pd.DataFrame(end_to_end)
    # The existing-report TP only feeds the summary's reproduction check.
    return {"Summary": summarise(e2e, pd.DataFrame(differences)),
            "End to end (per run)": e2e.drop(columns="TP (existing report)")}


def summarise(e2e: pd.DataFrame, differences: pd.DataFrame) -> pd.DataFrame:
    """One row per stage, over every run of that stage. The closest-GT cosine
    difference is averaged over UNIQUE untranslated anchors per document (the
    same text recurring across runs is counted once)."""
    differences = differences.drop_duplicates(["Stage", "Document", "Source text"])
    rows = []
    for stage in STAGES:
        e = e2e[e2e["Stage"] == stage]
        if e.empty:
            continue
        reported = pd.to_numeric(e["TP (existing report)"], errors="coerce")
        f1_src = pd.to_numeric(e["F1 as extracted"], errors="coerce").mean()
        f1_tr = pd.to_numeric(e["F1 translated"], errors="coerce").mean()
        rows.append({
            "Stage": stage,
            "Runs": len(e),
            "Untranslated anchors (all runs)": int(e["Untranslated anchors"].sum()),
            "Mean cos difference to closest GT (translated - original)":
                _r(differences.loc[differences["Stage"] == stage, "Difference"].mean()),
            "Runs with TP equal to existing report":
                f"{int((reported == e['TP as extracted']).sum())}/{int(reported.notna().sum())}",
            "TP as extracted": int(e["TP as extracted"].sum()),
            "TP translated": int(e["TP translated"].sum()),
            "Mean F1 as extracted": _r(f1_src),
            "Mean F1 translated": _r(f1_tr),
            "F1 difference (translated - as extracted)": _r(f1_tr - f1_src),
        })
    return pd.DataFrame(rows)


def main() -> None:
    runs = collect_runs()
    leaked_texts = [run["llm"][i] for run in runs for i in run["leaks"]]
    print(f"{len(runs)} gemini-3-5 runs, {len(leaked_texts)} untranslated anchors "
          f"({len(set(leaked_texts))} unique) — translating")
    translations = translate_all(leaked_texts)

    # An anchor whose translation is the text itself (a product name the
    # detector flagged) was not left untranslated — drop it.
    for run in runs:
        run["leaks"] = [i for i in run["leaks"]
                        if _norm(translations[run["llm"][i]]) != _norm(run["llm"][i])]
    kept = [run["llm"][i] for run in runs for i in run["leaks"]]
    print(f"{len(kept)} anchors actually changed by translation ({len(set(kept))} unique)")

    # Embed every distinct text once, up front, in the evaluator's own batches:
    # the per-run similarity calls below then hit its cache instead of each
    # sending small requests against a tight per-minute embedding quota. The
    # architecture report also compares every record's name and description.
    unique_texts = [t for run in runs for t in run["gt"] + run["llm"] if t]
    unique_texts += [translations[t] for t in kept]
    for run in runs:
        for rec in run.get("gt_records", []) + run.get("llm_records", []):
            unique_texts += [t for t in (norm_text(rec.get("name")), norm_text(rec.get("description"))) if t]
    unique_texts = list(dict.fromkeys(unique_texts))
    print(f"Embedding {len(unique_texts)} texts")
    similarity(unique_texts, unique_texts[:1])

    sheets = build_report(runs, translations)
    output_path = REPORT_DIR / "cross_lingual_similarity.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)

    print(sheets["Summary"].set_index("Stage").T.to_string())
    print(f"\nReport saved: {output_path}")
    print(f"Translation cache: {TRANSLATION_CACHE}")


if __name__ == "__main__":
    main()
