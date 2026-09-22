#!/usr/bin/env python3
"""
One compact table of average Precision / Recall / F1 for all three pipeline
stages (Requirement, Architecture, Decision), split by document set (design /
evaluation / all) and by which Gemini model produced the extraction
(gemini-3-1 / gemini-3-5).

Which prompt version is read per stage is fixed below (STAGES), per the
project's chosen versions: Requirement v2, Architecture v3, Decision v4.

Document sets:
  Design       CF_M01, CF_M05, CF_M08 — the documents the prompts were iterated on.
  Evaluation   CF_M04, CF_M06, CF_M09 — the held-out documents ("validation" in
               the outputs/ folder names) the settled prompt was then tried on.
  All          Design + Evaluation together.

Models:
  gemini-3-1   the project default (DEFAULT_GEMINI_MODEL in infra/gemini_client.py).
               Design-set runs live under outputs/evaluation/<stage>/design/vN/;
               evaluation-set runs under outputs/evaluation/<stage>/validation/
               (or, for the requirement stage specifically, the explicitly named
               .../validation/gemini-3-1-flash-lite/ — the requirement stage is
               the only one that names this folder instead of leaving it bare).
  gemini-3-5   a second model run over BOTH document sets in one pass, under
               .../validation/gemini-3-5/ (architecture, decision) or
               .../validation/gemini-3-5-flash-lite/ (requirement) — there is no
               separate "design" run for this model, so its design-set numbers
               here are the design documents' rows pulled out of that same
               all-documents folder.

Each stage's own already-computed "<doc>_..._eval_avg.xlsx" (the average over
that document's 3 runs, produced by the normal pipeline) is read as-is — this
script performs NO extraction, NO re-evaluation and NO embedding calls, and
writes nothing outside analysis/report/. A document-set's Precision/Recall/F1
here is the mean of its documents' own already-averaged values (consistent
with how every stage's own runner averages runs: the mean of the metric's own
per-unit values, not a value recomputed from pooled counts).

Which row of a report to read as "the" Precision/Recall/F1 differs by stage
report layout, so a single rule is used throughout: read the sheet, and take
the row immediately below the first "field / precision / recall / f1..."
header found — this is the anchor row in every stage's own report (the
decision evaluator's "rationale (matching anchor)" row, the architecture
evaluator's "all elements ..." row, the requirement evaluator's sole
"description" field row), wherever that header happens to sit in the sheet.

Usage (from any directory):
    analysis/script/pipeline_stage_summary.py
"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = PROJECT_ROOT / "outputs" / "evaluation"
REPORT_DIR = PROJECT_ROOT / "analysis" / "report"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
_REEXEC_FLAG = "PIPELINE_STAGE_SUMMARY_REEXEC"


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

import pandas as pd  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DESIGN_DOCS = ["CF_M01", "CF_M05", "CF_M08"]
EVALUATION_DOCS = ["CF_M04", "CF_M06", "CF_M09"]
DOC_SETS = [("Design", DESIGN_DOCS), ("Evaluation", EVALUATION_DOCS),
           ("All", DESIGN_DOCS + EVALUATION_DOCS)]

# One entry per stage: which version to read, where its avg reports live, what
# they are named, and which sheet holds the field/precision/recall/f1 table.
# `evaluation_31_subdir` is where the bare (gemini-3-1) evaluation-set run sits
# under outputs/evaluation/<subdir>/ — every stage but requirement leaves it
# unnamed.
STAGES = [
    {
        "name": "Requirement",
        "version": "v2",
        "subdir": "requirement",
        "avg_suffix": "_req_eval_avg.xlsx",
        "metrics_sheet": "Metrics",
        "evaluation_31_subdir": "validation/gemini-3-1-flash-lite",
        "gemini35_subdir": "validation/gemini-3-5-flash-lite",
    },
    {
        "name": "Architecture",
        "version": "v3",
        "subdir": "architecture",
        "avg_suffix": "_arch_eval_avg.xlsx",
        "metrics_sheet": "Field_Metrics",
        "evaluation_31_subdir": "validation",
        "gemini35_subdir": "validation/gemini-3-5",
    },
    {
        "name": "Decision",
        "version": "v4",
        "subdir": "decision",
        "avg_suffix": "_decision_eval_avg.xlsx",
        "metrics_sheet": "Field_Metrics",
        "evaluation_31_subdir": "validation",
        "gemini35_subdir": "validation/gemini-3-5",
    },
]

MODELS = ["gemini-3-1", "gemini-3-5"]


def anchor_precision_recall_f1(path: Path, sheet_name: str) -> tuple[float, float, float] | None:
    """The stage's headline (precision, recall, f1): the row immediately below
    the first row reading "field", "precision", ... in `sheet_name` — see the
    module docstring for why this one rule covers all three stages' report
    layouts. None if the file or that header cannot be read/found."""
    try:
        df = pd.read_excel(path, sheet_name=sheet_name, header=None)
    except (FileNotFoundError, ValueError):
        return None

    header_row = None
    for i in range(len(df) - 1):
        if (str(df.iat[i, 0]).strip().lower() == "field"
                and str(df.iat[i, 1]).strip().lower() == "precision"):
            header_row = i
            break
    if header_row is None:
        return None

    row = df.iloc[header_row + 1]
    try:
        return float(row[1]), float(row[2]), float(row[3])
    except (TypeError, ValueError):
        return None


def doc_avg_path(stage: dict, model: str, doc: str) -> Path:
    """Where document `doc`'s own <doc>_..._eval_avg.xlsx sits for `model`."""
    if model == "gemini-3-5":
        subdir = stage["gemini35_subdir"]
    elif doc in DESIGN_DOCS:
        subdir = f"design/{stage['version']}"
    else:
        subdir = stage["evaluation_31_subdir"]
    return EVAL_ROOT / stage["subdir"] / subdir / doc / f"{doc}{stage['avg_suffix']}"


def _avg(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def build_report() -> dict[str, pd.DataFrame]:
    summary_rows, detail_rows = [], []

    for stage in STAGES:
        for model in MODELS:
            per_doc: dict[str, tuple[float, float, float]] = {}
            for doc in DESIGN_DOCS + EVALUATION_DOCS:
                path = doc_avg_path(stage, model, doc)
                prf = anchor_precision_recall_f1(path, stage["metrics_sheet"])
                if prf is None:
                    print(f"{stage['name']} / {model} / {doc}: no report found at {path}, skipping.")
                    continue
                per_doc[doc] = prf
                detail_rows.append({
                    "Stage": stage["name"], "Version": stage["version"], "Model": model,
                    "Document": doc, "Precision": round(prf[0], 4),
                    "Recall": round(prf[1], 4), "F1": round(prf[2], 4),
                })

            for set_label, set_docs in DOC_SETS:
                docs_here = [d for d in set_docs if d in per_doc]
                p = _avg([per_doc[d][0] for d in docs_here])
                r = _avg([per_doc[d][1] for d in docs_here])
                f = _avg([per_doc[d][2] for d in docs_here])
                summary_rows.append({
                    "Stage": stage["name"],
                    "Version": stage["version"],
                    "Model": model,
                    "Document set": set_label,
                    "Documents": len(docs_here),
                    "Precision": round(p, 4) if p is not None else "-",
                    "Recall": round(r, 4) if r is not None else "-",
                    "F1": round(f, 4) if f is not None else "-",
                })

    return {
        "Summary": pd.DataFrame(summary_rows),
        "Per Document": pd.DataFrame(detail_rows),
    }


def main() -> None:
    sheets = build_report()

    output_path = REPORT_DIR / "pipeline_stage_summary.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        sheets["Summary"].to_excel(writer, sheet_name="Summary", index=False)
        sheets["Per Document"].to_excel(writer, sheet_name="Per Document", index=False)

    print()
    print(sheets["Summary"].to_string(index=False))
    print(f"\nReport saved: {output_path}")


if __name__ == "__main__":
    main()
