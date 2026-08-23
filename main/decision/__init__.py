"""Decision extraction: one runner, several interchangeable extraction
behaviours, selected by the version named in the environment."""

from main.decision.runner import DecisionExtractionRunner
from main.decision.strategies import (
    DecisionExtractionStrategy,
    DecisionResult,
    DecisionSources,
    SinglePromptExtraction,
)
from main.decision.versions import (
    VERSIONS,
    DecisionVersion,
    get_version,
    get_version_from_env,
)

__all__ = [
    "DecisionExtractionRunner",
    "DecisionExtractionStrategy",
    "DecisionResult",
    "DecisionSources",
    "DecisionVersion",
    "SinglePromptExtraction",
    "VERSIONS",
    "get_version",
    "get_version_from_env",
]
