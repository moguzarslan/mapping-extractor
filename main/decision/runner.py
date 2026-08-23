"""The orchestration around a decision extraction: which documents, which
already-final artifacts feed the pass, where the results go and how they are
evaluated.

This part is identical for every version, so it lives here once and takes the
version — behaviour plus configuration — as a collaborator. Paths are resolved
against the project root rather than the working directory, so the entry point
runs the same from anywhere.
"""

import os
from pathlib import Path

from service.decision_evaluator_service import (
    evaluate_decisions,
    write_average_decision_report,
)
from service.prompt_service import save_decisions

from main.decision.strategies import DecisionSources
from main.decision.versions import DecisionVersion, get_version_from_env

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_RUNS = 3
DOCUMENTS_ENV_KEY = "DOCUMENTS"
RUNS_ENV_KEY = "DECISION_RUNS"

# The decision pass consumes three already-final extraction artifacts and produces
# no new ids of its own for them, so it reads the exact files these point at — and
# the evaluator is later handed the same paths, so the ids in a decision resolve
# against the very artifacts the model was shown.
REQUIREMENT_INPUT_SUBDIR = "requirement/validation/gemini-3-5-flash-lite/{file_name}/first"
ARCHITECTURE_INPUT_SUBDIR = "architecture/design/v3/{file_name}/run_1"


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


class DecisionExtractionRunner:
    """Runs one version over the configured documents and evaluates the results."""

    def __init__(self, version: DecisionVersion, runs: int = DEFAULT_RUNS,
                 documents: list[str] = None):
        self.version = version
        self.runs = runs
        self.documents = documents or []

    @classmethod
    def from_env(cls) -> "DecisionExtractionRunner":
        """Build the runner the environment describes: which version, how many
        runs, over which documents."""
        return cls(
            version=get_version_from_env(),
            runs=get_run_count(),
            documents=get_document_files_from_env(),
        )

    def run(self) -> None:
        print(f"Decision extraction '{self.version.name}' — {self.version.description}")
        # One document failing (a missing upstream artifact, a response that will
        # not parse) says nothing about the others, so each is isolated and the
        # batch continues.
        for file_name in self.documents:
            try:
                self.run_document(file_name)
            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    def run_document(self, file_name: str) -> None:
        document = PROJECT_ROOT / "resource" / "docs" / file_name / f"{file_name}.pdf"

        sources = self.sources(file_name)
        for label, path in (("requirements", sources.requirements_json),
                            ("concepts", sources.concepts_json),
                            ("architecture", sources.architecture_json)):
            if not Path(path).exists():
                print(f"No extracted {label} found for '{file_name}' at {path}, skipping.")
                return

        # The strategy is built once per document and reused across the runs: it
        # holds configuration only, and every run of a document is the same call.
        strategy = self.version.strategy()

        # The extraction is repeated `runs` times because the model is sampled,
        # not deterministic: a single run measures one draw, and the averaged
        # report is what characterises the prompt.
        reports = []
        for run in range(1, self.runs + 1):
            run_label = f"run_{run}"
            print(f"Run {run} of {self.runs} for '{file_name}'")

            result = strategy.extract(str(document), sources)

            llm_json_path = save_decisions(
                result.decisions,
                output_file_name=file_name + "_decision",
                output_dir=str(self.extraction_dir(file_name) / run_label),
            )
            print(f"Architectural decisions ({strategy.label}) saved: {llm_json_path}")

            report = self.evaluate(file_name, run_label, llm_json_path, sources)
            if report is not None:
                reports.append(report)

        self.write_averages(file_name, reports)

    def write_averages(self, file_name: str, reports: list) -> None:
        """The average over the runs. With a single run there is nothing to
        average, so that run's own report is left to speak for itself."""
        if len(reports) < 2:
            return

        avg_output_path = (self.evaluation_dir(file_name)
                           / f"{file_name}_decision_eval_avg.xlsx")
        write_average_decision_report(reports, str(avg_output_path))
        print(f"Average evaluation saved: {avg_output_path}")

    def evaluate(self, file_name: str, run_label: str, llm_json_path: str,
                 sources: DecisionSources) -> dict | None:
        """Evaluate against the decision ground truth if available. The decision
        evaluator additionally needs the architecture, requirement and concept
        ground truths/LLM outputs, since a decision's references
        (architecturalElementIds, architecturalDecisionSource) are only meaningful
        once resolved through those artifacts. Returns the run's report, or
        None when there is no ground truth to score it against."""
        gt_decision_path = self.ground_truth("decision", file_name)
        gt_architecture_path = self.ground_truth("architecture", file_name)
        # The combined GT workbook holds both a Requirements sheet and a Concepts
        # sheet (Concept ID -> Description), so both paths point at the same file
        # — see service/evaluator_service.load_ground_truth_concepts.
        gt_requirement_path = self.ground_truth("requirement", file_name)

        if not (gt_decision_path.exists() and gt_architecture_path.exists()
                and gt_requirement_path.exists()):
            print(f"No decision ground truth found for '{file_name}', skipping evaluation.")
            return None

        eval_output_path = (self.evaluation_dir(file_name) / run_label
                            / f"{file_name}_decision_eval.xlsx")
        report = evaluate_decisions(
            gt_decision_path=str(gt_decision_path),
            llm_decision_path=llm_json_path,
            gt_architecture_path=str(gt_architecture_path),
            llm_architecture_path=sources.architecture_json,
            gt_requirement_path=str(gt_requirement_path),
            gt_concept_path=str(gt_requirement_path),
            llm_requirement_path=sources.requirements_json,
            llm_concepts_path=sources.concepts_json,
            output_path=str(eval_output_path),
        )
        print(f"Decision evaluation saved: {eval_output_path}")
        return report

    def sources(self, file_name: str) -> DecisionSources:
        gemini = PROJECT_ROOT / "outputs" / "gemini"
        requirement_dir = gemini / REQUIREMENT_INPUT_SUBDIR.format(file_name=file_name)
        architecture_dir = gemini / ARCHITECTURE_INPUT_SUBDIR.format(file_name=file_name)
        return DecisionSources(
            requirements_json=str(requirement_dir / f"{file_name}_requirements.json"),
            concepts_json=str(requirement_dir / f"{file_name}_concepts.json"),
            architecture_json=str(architecture_dir / f"{file_name}_architecture.json"),
        )

    def ground_truth(self, artifact: str, file_name: str) -> Path:
        return (PROJECT_ROOT / "resource" / "groundTruths" / artifact
                / f"{file_name}_ground_truth_{artifact}.xlsx")

    def extraction_dir(self, file_name: str) -> Path:
        return PROJECT_ROOT / "outputs" / "gemini" / self.version.output_subdir / file_name

    def evaluation_dir(self, file_name: str) -> Path:
        return PROJECT_ROOT / "outputs" / "evaluation" / self.version.output_subdir / file_name
