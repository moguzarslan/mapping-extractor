"""Requirement extraction: one runner, several interchangeable extraction
behaviours, selected by the version named in the environment."""

from main.requirement.runner import RequirementExtractionRunner
from main.requirement.strategies import (
    RequirementExtractionStrategy,
    RequirementResult,
    SinglePromptExtraction,
)
from main.requirement.versions import (
    VERSIONS,
    RequirementVersion,
    get_version,
    get_version_from_env,
)

__all__ = [
    "RequirementExtractionRunner",
    "RequirementExtractionStrategy",
    "RequirementResult",
    "RequirementVersion",
    "SinglePromptExtraction",
    "VERSIONS",
    "get_version",
    "get_version_from_env",
]
