"""The extraction behaviours behind the requirement versions.

Each strategy is one way of getting the requirements out of a document; what they
share is the shape of the result — the requirements plus the concept records their
`concept` ids point at — so the runner can save and evaluate any of them the same
way. The prompts a strategy uses are constructor arguments, not hard-coded, which
is what lets two versions (V1 and V2) be the same behaviour driven by a different
prompt.
"""

from abc import ABC, abstractmethod
from typing import NamedTuple

from service.prompt_service import extract_concepts, extract_requirements


class RequirementResult(NamedTuple):
    """One extraction's output, in the two groups `save_requirements` expects.

    `requirements` carries a concept id in each `concept` field rather than the
    concept text the model wrote; `concepts` holds the records those ids refer to.
    """
    requirements: list
    concepts: list


class RequirementExtractionStrategy(ABC):
    """A single way of extracting the requirements of one document."""

    #: Printed once per run so the log says which behaviour produced the file.
    label: str = "requirements"

    @abstractmethod
    def extract(self, file: str, folder: str) -> RequirementResult:
        """Extract the requirements of `file`, whose figures live in `folder`.
        Nothing is written to disk — the runner owns where the results go."""


class SinglePromptExtraction(RequirementExtractionStrategy):
    """The baseline: one prompt receives the document and returns every functional
    requirement, quality requirement, constraint and acceptance criterion, after
    which the quality-requirement concepts are folded out into their own records.

    Concept extraction is not a second model call — it is the deduplication that
    turns the concept text the model wrote into the `C_xx` ids the requirements
    reference, and every version built on this behaviour needs it. Which
    requirement prompt does the work is the caller's choice: that is the only
    difference between V1 and V2.
    """

    label = "single requirement prompt"

    def __init__(self, prompt: str):
        self.prompt = prompt

    def extract(self, file: str, folder: str) -> RequirementResult:
        requirements = extract_requirements(
            file=file,
            folder=folder,
            prompt=self.prompt,
        )
        requirements, concepts = extract_concepts(requirements)
        return RequirementResult(requirements=requirements, concepts=concepts)
