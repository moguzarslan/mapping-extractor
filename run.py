"""Single entry point: runs one stage, or the full pipeline, as named by
EXECUTION_MODE in the environment.

    REQUIREMENT   requirement extraction  (main/requirement/requirement_extractor.py)
    ARCHITECTURE  architecture extraction (main/architecture/architecture_extractor.py)
    DECISION      architectural-decision extraction (main/decision/decision_extractor.py)
    PIPELINE      all three stages in order (main/pipeline/pipeline.py)

The value is case-insensitive. Each mode starts exactly the runner its own entry
script starts; how that runner behaves is still decided by its own variables
(*_VERSION, *_RUNS, DOCUMENTS, ...).
"""

import os

from dotenv import load_dotenv

from main.architecture.runner import ArchitectureExtractionRunner
from main.decision.runner import DecisionExtractionRunner
from main.pipeline.runner import PipelineRunner
from main.requirement.runner import RequirementExtractionRunner

load_dotenv()

EXECUTION_MODE_ENV = "EXECUTION_MODE"

RUNNERS = {
    "REQUIREMENT": RequirementExtractionRunner,
    "ARCHITECTURE": ArchitectureExtractionRunner,
    "DECISION": DecisionExtractionRunner,
    "PIPELINE": PipelineRunner,
}


def get_runner_from_env(env_key: str = EXECUTION_MODE_ENV):
    value = os.getenv(env_key, "").strip().upper()
    if value not in RUNNERS:
        raise ValueError(f"{env_key} must be one of {', '.join(RUNNERS)} (got {value or 'nothing'})")
    return RUNNERS[value]


if __name__ == "__main__":
    try:
        get_runner_from_env().from_env().run()
    except Exception as e:
        print(f"Startup error: {e}")
