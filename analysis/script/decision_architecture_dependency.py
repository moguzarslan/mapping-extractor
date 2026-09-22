#!/usr/bin/env python3
"""
Per-document dependency of Stage III (decisions) on Stage II (architecture):
how many ground-truth decisions can NEVER be correctly extracted because every
architectural element they cite was missed by the architecture extraction.

For each document:
  1. Read the Stage II architecture evaluator's own report of which
     ground-truth architectural elements that extraction did NOT find
     (<doc>_arch_eval_gt_report.xlsx — its unmatched-GT / false-negative sheet,
     GT_ID column).
  2. Read the Stage III ground truth (resource/groundTruths/decision/), NOT any
     LLM output — this script inspects ground truths only, so no decision
     prompt version is involved.
  3. For every ground-truth decision, split its Architectural Element ID field
     into tokens and classify it:
       - "fully dependent"   EVERY cited element is in the not-found set — this
                              decision cannot be correctly extracted by Stage
                              III however good its prompt is, because Stage II
                              never surfaced anything for it to cite. This is
                              the "dropped" count the found-elements-only
                              evaluation (main/decision/runner.py's
                              build_found_elements_only_ground_truth) removes.
       - "partially dependent" cites a MIX of found and not-found elements —
                              answerable, but not on every cited element.
       - "independent"        every cited element was found.

Which architecture extraction is "Stage II's output" for a document is fixed,
not configurable, matching the one actually fed to the decision stage (see
main/decision/runner.py's ARCHITECTURE_INPUT_SUBDIR and its module docstring
for how this was established for each document set):
  Design documents (CF_M01, CF_M05, CF_M08)   architecture/design/v3/<doc>/run_1
  Pilot documents  (CF_M04, CF_M06, CF_M09)   architecture/validation/<doc>/run_1
(Design uses the v3 architecture prompt per the current project convention;
the pilot/validation architecture extraction has no separate version — it is
the one run made available there.)

This script performs analysis only: it reads existing ground truth and
evaluation reports and writes ONE new report under analysis/report/. It makes
no extraction or evaluation calls (no embeddings, no LLM), and writes nothing
under outputs/ or resource/.

Usage (from any directory):
    analysis/script/decision_architecture_dependency.py
"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = PROJECT_ROOT / "outputs" / "evaluation"
GROUND_TRUTHS = PROJECT_ROOT / "resource" / "groundTruths"
REPORT_DIR = PROJECT_ROOT / "analysis" / "report"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
_REEXEC_FLAG = "DECISION_ARCHITECTURE_DEPENDENCY_REEXEC"


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

sys.path.insert(0, str(PROJECT_ROOT))
from service.decision_evaluator_service import (  # noqa: E402
    load_ground_truth_decisions,
    split_ref_tokens,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DESIGN_DOCS = ["CF_M01", "CF_M05", "CF_M08"]
PILOT_DOCS = ["CF_M04", "CF_M06", "CF_M09"]
ARCHITECTURE_VERSION = "v3"  # design set only — see module docstring


def architecture_gt_report_path(doc: str) -> Path:
    if doc in DESIGN_DOCS:
        subdir = f"design/{ARCHITECTURE_VERSION}/{doc}/run_1"
    else:
        subdir = f"validation/{doc}/run_1"
    return EVAL_ROOT / "architecture" / subdir / f"{doc}_arch_eval_gt_report.xlsx"


def not_found_element_ids(doc: str) -> set[str]:
    """Ground-truth architectural element ids Stage II's own extraction for
    `doc` did not find."""
    path = architecture_gt_report_path(doc)
    df = pd.read_excel(path)
    return set(df["GT_ID"].dropna().astype(str).str.strip()) if "GT_ID" in df.columns else set()


def classify_decisions(doc: str, not_found: set[str]) -> tuple[list[dict], list[dict]]:
    """(summary row, per-decision detail rows) for `doc`."""
    gt_path = GROUND_TRUTHS / "decision" / f"{doc}_ground_truth_decision.xlsx"
    records = load_ground_truth_decisions(str(gt_path))

    fully = partially = independent = 0
    details = []
    for rec in records:
        tokens = split_ref_tokens(rec.get("architecturalElementIds"))
        missing = [t for t in tokens if t in not_found]
        if tokens and len(missing) == len(tokens):
            status = "fully dependent"
            fully += 1
        elif missing:
            status = "partially dependent"
            partially += 1
        else:
            status = "independent"
            independent += 1
        details.append({
            "Document": doc,
            "AD ID": rec["id"],
            "Architectural Element ID(s)": ", ".join(tokens),
            "Missing element(s)": ", ".join(missing) if missing else "-",
            "Status": status,
            "Rationale": rec.get("rationale"),
        })

    total = len(records)
    summary = {
        "Document": doc,
        "Document set": "Design" if doc in DESIGN_DOCS else "Pilot",
        "GT decisions": total,
        "Non-extracted architectural elements (Stage II)": len(not_found),
        "Decisions fully dependent on non-extracted elements": fully,
        "% of GT decisions fully dependent": round(fully / total, 4) if total else "-",
        "Decisions partially dependent": partially,
        "Decisions independent of missing elements": independent,
    }
    return summary, details


def build_report() -> dict[str, pd.DataFrame]:
    summary_rows, detail_rows = [], []
    for doc in DESIGN_DOCS + PILOT_DOCS:
        try:
            not_found = not_found_element_ids(doc)
        except FileNotFoundError as e:
            print(f"{doc}: architecture gt_report not found ({e}), skipping.")
            continue
        summary, details = classify_decisions(doc, not_found)
        summary_rows.append(summary)
        detail_rows.extend(details)

    summary_df = pd.DataFrame(summary_rows)

    # Pooled rows: Design / Pilot / All, counts summed (not averaged) since the
    # question is "how many decisions, out of how many" — a rate over the pool.
    for label, docs in [("DESIGN (all)", DESIGN_DOCS), ("PILOT (all)", PILOT_DOCS),
                        ("ALL DOCUMENTS", DESIGN_DOCS + PILOT_DOCS)]:
        rows = summary_df[summary_df["Document"].isin(docs)]
        if rows.empty:
            continue
        total = rows["GT decisions"].sum()
        fully = rows["Decisions fully dependent on non-extracted elements"].sum()
        summary_rows.append({
            "Document": label,
            "Document set": "-",
            "GT decisions": total,
            "Non-extracted architectural elements (Stage II)": rows["Non-extracted architectural elements (Stage II)"].sum(),
            "Decisions fully dependent on non-extracted elements": fully,
            "% of GT decisions fully dependent": round(fully / total, 4) if total else "-",
            "Decisions partially dependent": rows["Decisions partially dependent"].sum(),
            "Decisions independent of missing elements": rows["Decisions independent of missing elements"].sum(),
        })

    return {
        "Summary": pd.DataFrame(summary_rows),
        "Per Decision": pd.DataFrame(detail_rows),
    }


def main() -> None:
    sheets = build_report()

    output_path = REPORT_DIR / "decision_architecture_dependency.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        sheets["Summary"].to_excel(writer, sheet_name="Summary", index=False)
        sheets["Per Decision"].to_excel(writer, sheet_name="Per Decision", index=False)

    print(sheets["Summary"].to_string(index=False))
    print(f"\nReport saved: {output_path}")


if __name__ == "__main__":
    main()
