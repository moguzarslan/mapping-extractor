"""The orchestration around an extraction: which documents, how many runs, where
the results go and how they are evaluated.

This part is identical for every version, so it lives here once and takes the
version — behaviour plus configuration — as a collaborator. Paths are resolved
against the project root rather than the working directory, so the entry point
runs the same from anywhere.
"""

import os
from pathlib import Path

from service.architecture_evaluator_service import (
    evaluate_architecture,
    write_average_architecture_report,
)
from service.evaluator_service import write_average_type_breakdown_report
from service.prompt_service import save_architecture

from main.architecture.versions import ArchitectureVersion, get_version_from_env

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_RUNS = 3
DOCUMENTS_ENV_KEY = "DOCUMENTS"
RUNS_ENV_KEY = "ARCHITECTURE_RUNS"


def get_document_files_from_env(env_key: str = DOCUMENTS_ENV_KEY) -> list[str]:
    value = os.getenv(env_key, "").strip()
    if not value:
        raise ValueError(f"Environment variable '{env_key}' is empty or not set.")

    files = [item.strip() for item in value.split(",") if item.strip()]
    if not files:
        raise ValueError(f"No valid file paths found in '{env_key}'.")

    return files


def get_run_count(env_key: str = RUNS_ENV_KEY, default: int = DEFAULT_RUNS) -> int:
    """How many times to repeat the extraction for each document. The variable is
    optional — absent or blank means `default` — so the run count is configurable
    without touching the code."""
    value = os.getenv(env_key, "").strip()
    if not value:
        return default
    try:
        count = int(value)
    except ValueError:
        raise ValueError(f"'{env_key}' must be a whole number, got {value!r}.") from None
    if count < 1:
        raise ValueError(f"'{env_key}' must be at least 1, got {count}.")
    return count


class ArchitectureExtractionRunner:
    """Runs one version over the configured documents and evaluates the results."""

    def __init__(self, version: ArchitectureVersion, runs: int = DEFAULT_RUNS,
                 documents: list[str] = None):
        self.version = version
        self.runs = runs
        self.documents = documents or []

    @classmethod
    def from_env(cls) -> "ArchitectureExtractionRunner":
        """Build the runner the environment describes: which version, how many runs,
        over which documents."""
        return cls(
            version=get_version_from_env(),
            runs=get_run_count(),
            documents=get_document_files_from_env(),
        )

    def run(self) -> None:
        print(f"Architecture extraction '{self.version.name}' — {self.version.description}")
        # One document failing (a missing PDF, a response that will not parse) says
        # nothing about the others, so each is isolated and the batch continues.
        for file_name in self.documents:
            try:
                self.run_document(file_name)
            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    def run_document(self, file_name: str) -> None:
        document = PROJECT_ROOT / "resource" / "docs" / file_name / f"{file_name}.pdf"

        gt_path = (PROJECT_ROOT / "resource" / "groundTruths" / "architecture"
                   / f"{file_name}_ground_truth_architecture.xlsx")
        has_gt = gt_path.exists()
        if not has_gt:
            print(f"No architecture ground truth found for '{file_name}', skipping evaluation.")

        # The strategy is built once per document and reused across the runs: it
        # holds configuration only, and every run of a document is the same call.
        strategy = self.version.strategy()

        # The extraction is repeated `runs` times because the model is sampled, not
        # deterministic: a single run measures one draw, and the averaged report is
        # what characterises the prompt.
        reports = []
        for run in range(1, self.runs + 1):
            run_label = f"run_{run}"
            print(f"Run {run} of {self.runs} for '{file_name}'")

            result = strategy.extract(str(document))

            # The single canonical architecture file the evaluation consumes.
            llm_json_path = save_architecture(
                result.units, result.connectors, result.patterns,
                output_file_name=file_name + "_architecture",
                output_dir=str(self.extraction_dir(file_name) / run_label),
            )
            print(f"Architecture ({strategy.label}) saved: {llm_json_path}")

            if has_gt:
                eval_output_path = (self.evaluation_dir(file_name) / run_label
                                    / f"{file_name}_arch_eval.xlsx")
                reports.append(evaluate_architecture(
                    str(gt_path), llm_json_path, str(eval_output_path)))
                print(f"Architecture evaluation saved: {eval_output_path}")

        self.write_averages(file_name, reports)

    def write_averages(self, file_name: str, reports: list) -> None:
        """The average over the runs. With a single run there is nothing to average,
        so that run's own report is left to speak for itself."""
        if len(reports) < 2:
            return

        avg_dir = self.evaluation_dir(file_name)

        avg_output_path = avg_dir / f"{file_name}_arch_eval_avg.xlsx"
        write_average_architecture_report(reports, str(avg_output_path))
        print(f"Average evaluation saved: {avg_output_path}")

        avg_by_type_output_path = avg_dir / f"{file_name}_arch_eval_avg_by_type.xlsx"
        write_average_type_breakdown_report(reports, str(avg_by_type_output_path))
        print(f"Average by-type evaluation saved: {avg_by_type_output_path}")

    def extraction_dir(self, file_name: str) -> Path:
        return PROJECT_ROOT / "outputs" / "gemini" / self.version.output_subdir / file_name

    def evaluation_dir(self, file_name: str) -> Path:
        return PROJECT_ROOT / "outputs" / "evaluation" / self.version.output_subdir / file_name
