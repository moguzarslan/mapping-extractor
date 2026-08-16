"""The extraction behaviours behind the architecture versions.

Each strategy is one way of getting an architecture out of a document; what they
share is the shape of the result — units, connectors and patterns — so the runner
can save and evaluate any of them the same way. The prompts a strategy uses are
constructor arguments, not hard-coded, which is what lets two versions (V1 and V2)
be the same behaviour driven by a different prompt.
"""

from abc import ABC, abstractmethod
from typing import NamedTuple

from service.prompt_service import (
    apply_ispartof,
    extract_architecture_compacted,
    extract_architecture_group,
    extract_connectors,
    extract_ispartof_links,
)


class ArchitectureResult(NamedTuple):
    """One extraction's output, in the three groups `save_architecture` expects.

    `connectors` is the separate connector pass's records, which still have to be
    merged into the units. A strategy whose prompt already returns the connectors
    among the units leaves it empty — those connectors keep the ids the model gave
    them.
    """
    units: list
    connectors: list
    patterns: list


class ArchitectureExtractionStrategy(ABC):
    """A single way of extracting the architecture of one document."""

    #: Printed once per run so the log says which behaviour produced the file.
    label: str = "architecture"

    @abstractmethod
    def extract(self, file: str) -> ArchitectureResult:
        """Extract the architecture of `file`. Nothing is written to disk — the
        runner owns where the results go."""


class ChainedExtraction(ArchitectureExtractionStrategy):
    """The four-pass baseline: units, patterns, connectors and cross-group links
    each come from their own prompt and are merged in the program. Only the
    document text is sent to the model."""

    label = "chained prompts"

    def __init__(self, unit_prompt: str, pattern_prompt: str,
                 connector_prompt: str, ispartof_prompt: str):
        self.unit_prompt = unit_prompt
        self.pattern_prompt = pattern_prompt
        self.connector_prompt = connector_prompt
        self.ispartof_prompt = ispartof_prompt

    def extract(self, file: str) -> ArchitectureResult:
        # Pass A — Architectural Units (Layer, Component, Service, Device,
        # Technology, Other; connectors are extracted in Pass C).
        units = extract_architecture_group(file, self.unit_prompt, "architectural_units")

        # Pass B — Patterns (Architectural Pattern, Design Pattern).
        patterns = extract_architecture_group(file, self.pattern_prompt, "patterns")

        # Pass C — Connectors (the communications between units), extracted with
        # the document and the already-extracted units.
        connectors = extract_connectors(file, units, self.connector_prompt)

        # Pass D — cross-group isPartOf links only (unit<->pattern, either
        # direction); the within-group links (unit->unit, pattern->pattern) come
        # from the extraction passes and are unioned in. Connectors keep their own
        # endpoint isPartOf.
        links = extract_ispartof_links(file, units, patterns, self.ispartof_prompt)
        return ArchitectureResult(
            units=apply_ispartof(units, links),
            connectors=connectors,
            patterns=apply_ispartof(patterns, links),
        )


class CompactedExtraction(ArchitectureExtractionStrategy):
    """The whole architecture — units, connectors and patterns, with every isPartOf
    link already resolved — from a SINGLE prompt instead of the four chained passes.

    Units use the AU_xx id namespace (connectors included) and patterns use P_xx,
    and isPartOf may reference either namespace. Which compacted prompt does the
    work is the caller's choice: that is the only difference between V1 and V2.
    """

    label = "single compacted prompt"

    def __init__(self, prompt: str):
        self.prompt = prompt

    def extract(self, file: str) -> ArchitectureResult:
        units, patterns = extract_architecture_compacted(file, self.prompt)
        # No connectors are returned separately: they are already part of the
        # extracted units.
        return ArchitectureResult(units=units, connectors=[], patterns=patterns)


class CompactedWithConnectorsExtraction(ArchitectureExtractionStrategy):
    """Two prompts. Pass A returns the units AND the patterns with every isPartOf
    link (within- and cross-group) already resolved, so no separate pattern or
    linking pass is needed; Pass B extracts the connectors between those units and
    the technologies those communications use.

    `save_architecture` merges Pass B into the units, folding each technology Pass A
    already found into that existing unit instead of repeating it.
    """

    label = "compacted + connector prompt"

    def __init__(self, prompt: str, connector_prompt: str):
        self.prompt = prompt
        self.connector_prompt = connector_prompt

    def extract(self, file: str) -> ArchitectureResult:
        units, patterns = extract_architecture_compacted(file, self.prompt)
        connectors = extract_connectors(file, units, self.connector_prompt)
        return ArchitectureResult(units=units, connectors=connectors, patterns=patterns)
