"""The catalogue of architectural-decision extraction versions.

A version is a name (the one written in the environment) bound to a behaviour and
the configuration that behaviour runs with. V1 is the original single-prompt
extraction; a new prompt that keeps the same behaviour is one more entry here,
differing only in the prompt, which is the whole point of keeping the prompt out
of the strategy class.

Adding a version is one entry here; no other file needs to change.
"""

import os
from dataclasses import dataclass
from typing import Callable

from resource.prompts.prompts import Prompts

from main.decision.strategies import (
    DecisionExtractionStrategy,
    SinglePromptExtraction,
)

DEFAULT_VERSION = "v1"
VERSION_ENV_KEY = "DECISION_VERSION"


@dataclass(frozen=True)
class DecisionVersion:
    """One selectable extraction version."""

    #: Canonical name, as written in the environment variable.
    name: str
    #: Path, relative to outputs/gemini and outputs/evaluation, that this version's
    #: results live under — every version sits in its own subfolder of a shared
    #: `decision` folder, so the versions group together and never overwrite each
    #: other.
    output_subdir: str
    #: What this version does, printed when a run starts.
    description: str
    #: Built lazily so selecting a version never constructs the others.
    build: Callable[[], DecisionExtractionStrategy]

    def strategy(self) -> DecisionExtractionStrategy:
        return self.build()


VERSIONS: dict[str, DecisionVersion] = {
    "v1": DecisionVersion(
        name="v1",
        output_subdir="decision/v1",
        description="single decision prompt (V1)",
        build=lambda: SinglePromptExtraction(
            prompt=Prompts.ARCHITECTURAL_DECISION_EXTRACTION_PROMPT,
        ),
    ),
}


def normalise_version(value: str) -> str:
    """Accept the forms a version is naturally written in — "V2", "v2", "2" — and
    return the key used in `VERSIONS`."""
    key = value.strip().lower()
    return f"v{key}" if key.isdigit() else key


def get_version(name: str) -> DecisionVersion:
    key = normalise_version(name)
    if key not in VERSIONS:
        known = ", ".join(VERSIONS)
        raise ValueError(f"Unknown decision version {name!r}. Known versions: {known}.")
    return VERSIONS[key]


def get_version_from_env(env_key: str = VERSION_ENV_KEY,
                         default: str = DEFAULT_VERSION) -> DecisionVersion:
    """The version to run. The variable is optional — absent or blank means
    `default` — so a checkout without it still runs the current version."""
    return get_version(os.getenv(env_key, "").strip() or default)
