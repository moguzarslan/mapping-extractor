"""The full pipeline: requirement, then architecture, then decision — each
stage run by its own existing runner, in order."""

from main.pipeline.runner import PipelineRunner

__all__ = [
    "PipelineRunner",
]
