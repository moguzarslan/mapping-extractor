"""Architecture extraction: one runner, several interchangeable extraction
behaviours, selected by the version named in the environment."""

from main.architecture.runner import ArchitectureExtractionRunner
from main.architecture.strategies import (
    ArchitectureExtractionStrategy,
    ArchitectureResult,
    ChainedExtraction,
    CompactedExtraction,
    CompactedWithConnectorsExtraction,
)
from main.architecture.versions import (
    VERSIONS,
    ArchitectureVersion,
    get_version,
    get_version_from_env,
)

__all__ = [
    "ArchitectureExtractionRunner",
    "ArchitectureExtractionStrategy",
    "ArchitectureResult",
    "ArchitectureVersion",
    "ChainedExtraction",
    "CompactedExtraction",
    "CompactedWithConnectorsExtraction",
    "VERSIONS",
    "get_version",
    "get_version_from_env",
]
