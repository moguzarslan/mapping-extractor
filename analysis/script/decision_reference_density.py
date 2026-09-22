#!/usr/bin/env python3
"""
Architectural-element reference density of a decision extraction: for every
DISTINCT architectural element referenced by at least one decision in a set,
how many of that set's decisions reference it — averaged over those elements.

A value above 1 means elements are, on average, shared across several
decisions (the same handful of elements keep coming back); a value at or
below 1 means elements are rarely reused, most being cited by only one
decision. This is computed for two decision sets, so the two can be compared
directly:

  LLM extraction         every decision in <doc>_decision.json, for every run.
  Filtered ground truth  the found-elements-only ground truth (see
                          main/decision/runner.py's
                          `build_found_elements_only_ground_truth`) — the
                          ground-truth decisions that are actually answerable
                          from the architecture extraction the decision stage
                          was given, with unanswerable element references
                          already stripped. This is the fairer ground-truth
                          side to compare the LLM against: the full ground
                          truth would count elements the model was never shown.

This script performs analysis only. It reads existing extraction outputs and
ground truth, and writes ONE new report under analysis/report/ — it does not
write, move or modify anything under outputs/ or resource/. The filtered
ground truth is rebuilt in memory (via the exact same, unmodified
`DecisionExtractionRunner.build_found_elements_only_ground_truth` used by the
real pipeline) inside a temporary directory that is discarded immediately.

Which decision version to analyse is set by VERSION_NUMBER below — the design
prompt version (design/v1 .. design/v4), always resolved to the "design/vN"
folder under outputs/gemini/decision/ and outputs/evaluation/decision/, never
the validation set. The document set and run count are discovered from that
folder, not hard-coded. The report is named after the version so different
versions never overwrite each other.

Usage (from any directory):
    analysis/script/decision_reference_density.py
Edit VERSION_NUMBER below and re-run to analyse a different decision version.
"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = PROJECT_ROOT / "analysis" / "report"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
_REEXEC_FLAG = "DECISION_REFERENCE_DENSITY_REEXEC"


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

import tempfile  # noqa: E402

import pandas as pd  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT))
from service.decision_evaluator_service import (  # noqa: E402
    load_ground_truth_decisions,
    load_llm_decisions,
    split_ref_tokens,
)
from main.decision.runner import DecisionExtractionRunner  # noqa: E402
from main.decision.versions import get_version  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — edit and re-run to analyse a different version.
# ---------------------------------------------------------------------------
VERSION_NUMBER = 2

# Always the design set (design/v1 .. design/v4), never validation: the
# validation documents are the held-out check on the design-set-tuned prompt,
# not another version to analyse the same way.
VERSION = f"design/v{VERSION_NUMBER}"


def element_reference_counts(records: list[dict]) -> dict[str, int]:
    """How many of `records` reference each architectural element id — one entry
    per DISTINCT element referenced at least once. Decision records as
    `load_ground_truth_decisions` / `load_llm_decisions` return them.

    Raw ids, not resolved to text: both sides here already share one namespace
    (the ground truth's own ids, or one run's own LLM ids), so resolving through
    an architecture extraction — needed only to compare GT ids against LLM ids —
    would add nothing here."""
    counts: dict[str, int] = {}
    for rec in records:
        for element_id in split_ref_tokens(rec.get("architecturalElementIds")):
            counts[element_id] = counts.get(element_id, 0) + 1
    return counts


def average_references_per_element(counts: dict[str, int]) -> float | None:
    """Over every distinct element referenced at least once, the average number
    of decisions that reference it."""
    return (sum(counts.values()) / len(counts)) if counts else None


def discover_runs(version: str) -> dict[str, list[Path]]:
    """{document: [run decision json paths, sorted]} under
    outputs/gemini/decision/<version>/ — whatever documents and runs actually
    exist there, nothing hard-coded."""
    base = PROJECT_ROOT / "outputs" / "gemini" / "decision" / version
    runs: dict[str, list[Path]] = {}
    for path in sorted(base.glob("*/run_*/*_decision.json")):
        doc = path.parent.parent.name
        runs.setdefault(doc, []).append(path)
    for doc in runs:
        runs[doc].sort(key=lambda p: p.parent.name)
    return dict(sorted(runs.items()))


def _stats_from_records(records: list[dict]) -> dict:
    counts = element_reference_counts(records)
    return {
        "decisions": len(records),
        "distinct_elements": len(counts),
        "total_references": sum(counts.values()),
        "avg_references_per_element": average_references_per_element(counts),
    }


def filtered_ground_truth_stats(runner: DecisionExtractionRunner, doc: str) -> dict | None:
    """Reference-count stats for the found-elements-only ground truth of `doc`,
    or None when it cannot be built (see
    `build_found_elements_only_ground_truth`'s own skip conditions)."""
    sources = runner.sources(doc)
    with tempfile.TemporaryDirectory() as tmp_dir:
        gt_path = runner.build_found_elements_only_ground_truth(doc, sources, Path(tmp_dir))
        if gt_path is None:
            return None
        records = load_ground_truth_decisions(str(gt_path))
    return _stats_from_records(records)


def llm_stats(json_path: Path) -> dict:
    return _stats_from_records(load_llm_decisions(str(json_path)))


def _avg(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return (sum(present) / len(present)) if present else None


def build_report(version: str) -> dict[str, pd.DataFrame]:
    runs_by_doc = discover_runs(version)
    if not runs_by_doc:
        raise ValueError(f"No decision runs found under outputs/gemini/decision/{version}/")

    # Any version works here — `sources` / `build_found_elements_only_ground_truth`
    # do not depend on it, only on the document and the fixed architecture input
    # they resolve for it.
    runner = DecisionExtractionRunner(version=get_version("v1"))

    by_run_rows, by_doc_rows = [], []
    for doc, run_paths in runs_by_doc.items():
        gt = filtered_ground_truth_stats(runner, doc)

        llm_per_run = []
        for run_path in run_paths:
            run_label = run_path.parent.name
            s = llm_stats(run_path)
            llm_per_run.append(s)
            llm_avg_ref = s["avg_references_per_element"]
            gt_avg_ref = gt["avg_references_per_element"] if gt else None
            by_run_rows.append({
                "Document": doc,
                "Run": run_label,
                "LLM decisions": s["decisions"],
                "LLM distinct elements": s["distinct_elements"],
                "LLM total element references": s["total_references"],
                "LLM avg references per element": round(llm_avg_ref, 4) if llm_avg_ref is not None else "-",
                "GT (filtered) decisions": gt["decisions"] if gt else "-",
                "GT (filtered) distinct elements": gt["distinct_elements"] if gt else "-",
                "GT (filtered) total element references": gt["total_references"] if gt else "-",
                "GT (filtered) avg references per element": round(gt_avg_ref, 4) if gt_avg_ref is not None else "-",
                "Difference (LLM - GT)": (round(llm_avg_ref - gt_avg_ref, 4)
                                          if llm_avg_ref is not None and gt_avg_ref is not None
                                          else "-"),
            })

        llm_decisions_avg = _avg([s["decisions"] for s in llm_per_run])
        llm_elements_avg = _avg([s["distinct_elements"] for s in llm_per_run])
        llm_total_refs_avg = _avg([s["total_references"] for s in llm_per_run])
        llm_ref_avg_avg = _avg([s["avg_references_per_element"] for s in llm_per_run])
        gt_ref_avg = gt["avg_references_per_element"] if gt else None
        by_doc_rows.append({
            "Document": doc,
            "Runs": len(llm_per_run),
            "LLM decisions (avg)": round(llm_decisions_avg, 4) if llm_decisions_avg is not None else "-",
            "LLM distinct elements (avg)": round(llm_elements_avg, 4) if llm_elements_avg is not None else "-",
            "LLM total element references (avg)": round(llm_total_refs_avg, 4) if llm_total_refs_avg is not None else "-",
            "LLM avg references per element (avg)": round(llm_ref_avg_avg, 4) if llm_ref_avg_avg is not None else "-",
            "GT (filtered) decisions": gt["decisions"] if gt else "-",
            "GT (filtered) distinct elements": gt["distinct_elements"] if gt else "-",
            "GT (filtered) total element references": gt["total_references"] if gt else "-",
            "GT (filtered) avg references per element": round(gt_ref_avg, 4) if gt_ref_avg is not None else "-",
            "Difference (LLM avg - GT)": (round(llm_ref_avg_avg - gt_ref_avg, 4)
                                          if llm_ref_avg_avg is not None and gt_ref_avg is not None
                                          else "-"),
        })

    # Pooled across every document: ids are already namespaced per document (GT)
    # or per run (LLM, no cross-run identity), so summing distinct-element and
    # total-reference counts is the same as pooling everything into one set —
    # no double counting, and the pooled ratio (not an average of ratios) is
    # what "on average, across every element in this version" should mean.
    total_llm_elements = sum(r["LLM distinct elements (avg)"] for r in by_doc_rows if r["LLM distinct elements (avg)"] != "-")
    total_llm_refs = sum(r["LLM total element references (avg)"] for r in by_doc_rows if r["LLM total element references (avg)"] != "-")
    total_gt_elements = sum(r["GT (filtered) distinct elements"] for r in by_doc_rows if r["GT (filtered) distinct elements"] != "-")
    total_gt_refs = sum(r["GT (filtered) total element references"] for r in by_doc_rows if r["GT (filtered) total element references"] != "-")
    pooled_llm_ratio = (total_llm_refs / total_llm_elements) if total_llm_elements else None
    pooled_gt_ratio = (total_gt_refs / total_gt_elements) if total_gt_elements else None
    by_doc_rows.append({
        "Document": "ALL DOCUMENTS (pooled)",
        "Runs": "-",
        "LLM decisions (avg)": round(sum(r["LLM decisions (avg)"] for r in by_doc_rows if r["LLM decisions (avg)"] != "-"), 4),
        "LLM distinct elements (avg)": round(total_llm_elements, 4),
        "LLM total element references (avg)": round(total_llm_refs, 4),
        "LLM avg references per element (avg)": round(pooled_llm_ratio, 4) if pooled_llm_ratio is not None else "-",
        "GT (filtered) decisions": sum(r["GT (filtered) decisions"] for r in by_doc_rows if r["GT (filtered) decisions"] != "-"),
        "GT (filtered) distinct elements": total_gt_elements,
        "GT (filtered) total element references": total_gt_refs,
        "GT (filtered) avg references per element": round(pooled_gt_ratio, 4) if pooled_gt_ratio is not None else "-",
        "Difference (LLM avg - GT)": (round(pooled_llm_ratio - pooled_gt_ratio, 4)
                                      if pooled_llm_ratio is not None and pooled_gt_ratio is not None else "-"),
    })

    return {
        "By Document": pd.DataFrame(by_doc_rows),
        "By Run": pd.DataFrame(by_run_rows),
    }


def main() -> None:
    sheets = build_report(VERSION)

    version_slug = VERSION.replace("/", "_")
    output_path = REPORT_DIR / f"decision_reference_density_{version_slug}.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        sheets["By Document"].to_excel(writer, sheet_name="By Document", index=False)
        sheets["By Run"].to_excel(writer, sheet_name="By Run", index=False)

    print(f"Version analysed: {VERSION}")
    print(sheets["By Document"].to_string(index=False))
    print(f"\nReport saved: {output_path}")


if __name__ == "__main__":
    main()
