"""The catalogue of architecture extraction versions.

A version is a name (the one written in the environment) bound to a behaviour and
the configuration that behaviour runs with. V1 and V2 are deliberately the same
behaviour — the compacted single-prompt extraction — differing only in the prompt,
which is the whole point of keeping the prompt out of the strategy class.

Adding a version is one entry here; no other file needs to change.
"""

import os
from dataclasses import dataclass
from typing import Callable

from resource.prompts.prompts import Prompts

from main.architecture.strategies import (
    ArchitectureExtractionStrategy,
    ChainedExtraction,
    CompactedExtraction,
    CompactedWithConnectorsExtraction,
)

DEFAULT_VERSION = "v3"
VERSION_ENV_KEY = "ARCHITECTURE_VERSION"


@dataclass(frozen=True)
class ArchitectureVersion:
    """One selectable extraction version."""

    #: Canonical name, as written in the environment variable.
    name: str
    #: Path, relative to outputs/gemini and outputs/evaluation, that this version's
    #: results live under — every version sits in its own subfolder of a shared
    #: `architecture` folder, so the versions group together and never overwrite
    #: each other.
    output_subdir: str
    #: What this version does, printed when a run starts.
    description: str
    #: Built lazily so selecting a version never constructs the others.
    build: Callable[[], ArchitectureExtractionStrategy]

    def strategy(self) -> ArchitectureExtractionStrategy:
        return self.build()


VERSIONS: dict[str, ArchitectureVersion] = {
    "chained": ArchitectureVersion(
        name="chained",
        output_subdir="architecture/chained",
        description="four chained passes (units, patterns, connectors, isPartOf links)",
        build=lambda: ChainedExtraction(
            unit_prompt=Prompts.ARCHITECTURAL_UNIT_EXTRACTION_PROMPT,
            pattern_prompt=Prompts.PATTERN_EXTRACTION_PROMPT,
            connector_prompt=Prompts.CONNECTOR_EXTRACTION_PROMPT,
            ispartof_prompt=Prompts.ISPARTOF_LINKING_PROMPT,
        ),
    ),
    "v1": ArchitectureVersion(
        name="v1",
        output_subdir="architecture/v1",
        description="single compacted prompt (V1)",
        build=lambda: CompactedExtraction(
            prompt=Prompts.ARCHITECTURE_EXTRACTION_PROMPT_COMPACTED,
        ),
    ),
    "v2": ArchitectureVersion(
        name="v2",
        output_subdir="architecture/v2",
        description="single compacted prompt (V2)",
        build=lambda: CompactedExtraction(
            prompt=Prompts.ARCHITECTURE_EXTRACTION_PROMPT_COMPACTED_V2,
        ),
    ),
    "v3": ArchitectureVersion(
        name="v3",
        output_subdir="architecture/v3",
        description="compacted prompt (V3) plus a separate connector prompt",
        build=lambda: CompactedWithConnectorsExtraction(
            prompt=Prompts.ARCHITECTURE_EXTRACTION_PROMPT_COMPACTED_V3,
            connector_prompt=Prompts.CONNECTOR_EXTRACTION_PROMPT_V2,
        ),
    ),
    # Same two-pass behaviour as V3 — only the compacted prompt differs, which is
    # exactly the case the strategy/config split exists for.
    "v4": ArchitectureVersion(
        name="v4",
        output_subdir="architecture/v4",
        description="compacted prompt (V4) plus a separate connector prompt",
        build=lambda: CompactedWithConnectorsExtraction(
            prompt=Prompts.ARCHITECTURE_EXTRACTION_PROMPT_COMPACTED_V4,
            connector_prompt=Prompts.CONNECTOR_EXTRACTION_PROMPT_V2,
        ),
    ),
}


def normalise_version(value: str) -> str:
    """Accept the forms a version is naturally written in — "V2", "v2", "2" — and
    return the key used in `VERSIONS`."""
    key = value.strip().lower()
    return f"v{key}" if key.isdigit() else key


def get_version(name: str) -> ArchitectureVersion:
    key = normalise_version(name)
    if key not in VERSIONS:
        known = ", ".join(VERSIONS)
        raise ValueError(f"Unknown architecture version {name!r}. Known versions: {known}.")
    return VERSIONS[key]


def get_version_from_env(env_key: str = VERSION_ENV_KEY,
                         default: str = DEFAULT_VERSION) -> ArchitectureVersion:
    """The version to run. The variable is optional — absent or blank means
    `default` — so a checkout without it still runs the current version."""
    return get_version(os.getenv(env_key, "").strip() or default)
