"""The orchestrator across the three extraction stages: requirement, then
architecture, then architectural decision — in that order, matching the
chained pipeline itself: the decision stage's own runner reads already-final
requirement and architecture artifacts (see
main/decision/runner.py's REQUIREMENT_INPUT_SUBDIR / ARCHITECTURE_INPUT_SUBDIR),
so those need to exist before a decision run produces anything useful.

This adds no extraction behaviour of its own. Each stage already has a runner
that builds itself from the environment and runs end to end, isolating one
document's failure from the others; this class does nothing more than call
the three of them in sequence, over the same documents.
"""

from main.architecture.runner import ArchitectureExtractionRunner
from main.decision.runner import DecisionExtractionRunner
from main.requirement.runner import RequirementExtractionRunner


class PipelineRunner:
    """Runs the requirement, architecture and decision stages in sequence,
    each with its own already-implemented runner and version/strategy."""

    def __init__(self, requirement_runner: RequirementExtractionRunner,
                 architecture_runner: ArchitectureExtractionRunner,
                 decision_runner: DecisionExtractionRunner):
        self.requirement_runner = requirement_runner
        self.architecture_runner = architecture_runner
        self.decision_runner = decision_runner

    @classmethod
    def from_env(cls) -> "PipelineRunner":
        """Build the pipeline the environment describes. Each stage reads its
        own version and run-count variables (REQUIREMENT_VERSION,
        ARCHITECTURE_VERSION, DECISION_VERSION and their *_RUNS counterparts);
        all three share the same DOCUMENTS variable, since every stage's own
        `from_env` already reads that key independently."""
        return cls(
            requirement_runner=RequirementExtractionRunner.from_env(),
            architecture_runner=ArchitectureExtractionRunner.from_env(),
            decision_runner=DecisionExtractionRunner.from_env(),
        )

    def run(self) -> None:
        print("========== Stage I: Requirement extraction ==========")
        self.requirement_runner.run()

        print("\n========== Stage II: Architecture extraction ==========")
        self.architecture_runner.run()

        print("\n========== Stage III: Architectural decision extraction ==========")
        self.decision_runner.run()

        print("\nPipeline complete.")
