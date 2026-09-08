"""The catalogue of requirement extraction versions.

A version is a name (the one written in the environment) bound to a behaviour and
the configuration that behaviour runs with. V1 and V2 are deliberately the same
behaviour — the single-prompt extraction — differing only in the prompt, which is
the whole point of keeping the prompt out of the strategy class.

Adding a version is one entry here; no other file needs to change.
"""

import os
from dataclasses import dataclass
from typing import Callable

from resource.prompts.prompts import Prompts

from main.requirement.strategies import (
    RequirementExtractionStrategy,
    SinglePromptExtraction,
)

DEFAULT_VERSION = "v2"
VERSION_ENV_KEY = "REQUIREMENT_VERSION"


@dataclass(frozen=True)
class RequirementVersion:
    """One selectable extraction version."""

    #: Canonical name, as written in the environment variable.
    name: str
    #: Path, relative to outputs/gemini and outputs/evaluation, that this version's
    #: results live under — every version sits in its own subfolder of a shared
    #: `requirement` folder, so the versions group together and never overwrite
    #: each other.
    output_subdir: str
    #: What this version does, printed when a run starts.
    description: str
    #: Built lazily so selecting a version never constructs the others.
    build: Callable[[], RequirementExtractionStrategy]

    def strategy(self) -> RequirementExtractionStrategy:
        return self.build()


VERSIONS: dict[str, RequirementVersion] = {
    "v1": RequirementVersion(
        name="v1",
        output_subdir="requirement/v1",
        description="single requirement prompt (V1)",
        build=lambda: SinglePromptExtraction(
            prompt=Prompts.REQUIREMENT_EXTRACTION_PROMPT_V1,
        ),
    ),
    # Same single-prompt behaviour as V1 — only the prompt differs, which is
    # exactly the case the strategy/config split exists for.
    "v2": RequirementVersion(
        name="v2",
        output_subdir="requirement/v2",
        description="single requirement prompt (V2)",
        build=lambda: SinglePromptExtraction(
            prompt=Prompts.REQUIREMENT_EXTRACTION_PROMPT_V2,
        ),
    ),
}


def normalise_version(value: str) -> str:
    """Accept the forms a version is naturally written in — "V2", "v2", "2" — and
    return the key used in `VERSIONS`."""
    key = value.strip().lower()
    return f"v{key}" if key.isdigit() else key


def get_version(name: str) -> RequirementVersion:
    key = normalise_version(name)
    if key not in VERSIONS:
        known = ", ".join(VERSIONS)
        raise ValueError(f"Unknown requirement version {name!r}. Known versions: {known}.")
    return VERSIONS[key]


def get_version_from_env(env_key: str = VERSION_ENV_KEY,
                         default: str = DEFAULT_VERSION) -> RequirementVersion:
    """The version to run. The variable is optional — absent or blank means
    `default` — so a checkout without it still runs the current version."""
    return get_version(os.getenv(env_key, "").strip() or default)
