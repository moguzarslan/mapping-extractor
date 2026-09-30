"""The orchestrator across the three extraction stages: requirement, then
architecture, then architectural decision — in that order, because the
decision stage consumes the requirement and architecture artifacts the two
earlier stages produce.

A pipeline execution differs from running the stages on their own in two ways:
  - every stage runs exactly once per document, whatever its *_RUNS variable
    says — the pipeline produces one chained extraction, not a repeated
    experiment;
  - the decision stage reads the requirements, concepts and architecture this
    very execution has just extracted, instead of the fixed, already-final
    artifacts its standalone runner reads (see main/decision/runner.py's
    REQUIREMENT_INPUT_SUBDIR / ARCHITECTURE_INPUT_SUBDIR).

Everything the pipeline writes lives under outputs/pipeline/, so an execution
never overwrites a stage's standalone runs. The stages' own behaviour —
versions, extraction, evaluation, isolating one document's failure from the
others — is left to their runners, unchanged.
"""

from pathlib import Path

from main.architecture.runner import ArchitectureExtractionRunner
from main.architecture.versions import get_version_from_env as get_architecture_version
from main.decision.runner import DecisionExtractionRunner
from main.decision.strategies import DecisionSources
from main.decision.versions import get_version_from_env as get_decision_version
from main.requirement.runner import (
    DEFAULT_OUTPUT_ROOT,
    RequirementExtractionRunner,
    get_document_files_from_env,
)
from main.requirement.versions import get_version_from_env as get_requirement_version

PIPELINE_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT / "pipeline"

# Each stage runs once, so its single run is always this one.
PIPELINE_RUNS = 1
RUN_LABEL = "run_1"


class PipelineRunner:
    """Runs the requirement, architecture and decision stages in sequence, each
    once, with the decision stage chained onto the other two's output."""

    def __init__(self, requirement_runner: RequirementExtractionRunner,
                 architecture_runner: ArchitectureExtractionRunner,
                 decision_runner: DecisionExtractionRunner):
        self.requirement_runner = requirement_runner
        self.architecture_runner = architecture_runner
        self.decision_runner = decision_runner

    @classmethod
    def from_env(cls, output_root: Path = PIPELINE_OUTPUT_ROOT) -> "PipelineRunner":
        """Build the pipeline the environment describes. Each stage reads its own
        version variable (REQUIREMENT_VERSION, ARCHITECTURE_VERSION,
        DECISION_VERSION) and all three share DOCUMENTS; the *_RUNS variables are
        deliberately not read, since a pipeline stage always runs once."""
        documents = get_document_files_from_env()

        requirement_runner = RequirementExtractionRunner(
            version=get_requirement_version(), runs=PIPELINE_RUNS,
            documents=documents, output_root=output_root)
        architecture_runner = ArchitectureExtractionRunner(
            version=get_architecture_version(), runs=PIPELINE_RUNS,
            documents=documents, output_root=output_root)

        def upstream_sources(file_name: str) -> DecisionSources:
            """The artifacts the two earlier stages of this execution wrote for
            `file_name` — where each stage's runner saves its single run."""
            requirement_dir = requirement_runner.extraction_dir(file_name) / RUN_LABEL
            architecture_dir = architecture_runner.extraction_dir(file_name) / RUN_LABEL
            return DecisionSources(
                requirements_json=str(requirement_dir / f"{file_name}_requirements.json"),
                concepts_json=str(requirement_dir / f"{file_name}_concepts.json"),
                architecture_json=str(architecture_dir / f"{file_name}_architecture.json"),
            )

        decision_runner = DecisionExtractionRunner(
            version=get_decision_version(), runs=PIPELINE_RUNS,
            documents=documents, output_root=output_root,
            sources_resolver=upstream_sources)

        return cls(requirement_runner, architecture_runner, decision_runner)

    def run(self) -> None:
        print("========== Stage I: Requirement extraction ==========")
        self.requirement_runner.run()

        print("\n========== Stage II: Architecture extraction ==========")
        self.architecture_runner.run()

        print("\n========== Stage III: Architectural decision extraction ==========")
        self.decision_runner.run()

        print("\nPipeline complete.")
