"""The extraction behaviours behind the decision versions.

Each strategy is one way of getting the architectural decisions out of a document
and the three artifacts a decision references (requirements, concepts,
architecture); what they share is the shape of the result — a list of decision
records — so the runner can save and evaluate any of them the same way. The
prompts a strategy uses are constructor arguments, not hard-coded, which is what
lets two versions be the same behaviour driven by a different prompt.
"""

from abc import ABC, abstractmethod
from typing import NamedTuple

from service.prompt_service import extract_decisions


class DecisionSources(NamedTuple):
    """The already-final artifacts one decision extraction reads.

    A decision produces no ids of its own for these: `architecturalElementIds` and
    `architecturalDecisionSource` are ids taken from exactly these files, which is
    why the same paths are handed to the evaluator afterwards.
    """
    requirements_json: str
    concepts_json: str
    architecture_json: str


class DecisionResult(NamedTuple):
    """One extraction's output, in the shape `save_decisions` expects."""
    decisions: list


class DecisionExtractionStrategy(ABC):
    """A single way of extracting the architectural decisions of one document."""

    #: Printed once per run so the log says which behaviour produced the file.
    label: str = "decisions"

    @abstractmethod
    def extract(self, file: str, sources: DecisionSources) -> DecisionResult:
        """Extract the decisions of `file`. Nothing is written to disk — the
        runner owns where the results go."""


class SinglePromptExtraction(DecisionExtractionStrategy):
    """The baseline: one prompt receives the document together with the reduced
    requirements+concepts and architecture JSON, and returns every decision with
    its element reference, source reference, rationale and page.

    Which decision prompt does the work is the caller's choice — that is the only
    difference between versions built on this behaviour.
    """

    label = "single decision prompt"

    def __init__(self, prompt: str):
        self.prompt = prompt

    def extract(self, file: str, sources: DecisionSources) -> DecisionResult:
        decisions = extract_decisions(
            file=file,
            requirements_json_dir=sources.requirements_json,
            concepts_json_dir=sources.concepts_json,
            architecture_json_dir=sources.architecture_json,
            prompt=self.prompt,
        )
        return DecisionResult(decisions=decisions)
