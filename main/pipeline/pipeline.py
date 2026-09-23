"""Entry point for the full pipeline: requirement, then architecture, then
architectural-decision extraction, in that order.

Which extraction is run at each stage is decided by REQUIREMENT_VERSION,
ARCHITECTURE_VERSION and DECISION_VERSION in the environment (see each
stage's own main/*/versions.py); this file only starts the three of them in
sequence, over the same DOCUMENTS.
"""

import sys
from pathlib import Path

# The package imports below are absolute (main.*, service.*, resource.*), so the
# project root has to be importable even when this file is run as a script. This
# file sits at main/pipeline/, hence two levels up.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

from main.pipeline.runner import PipelineRunner

load_dotenv()


if __name__ == "__main__":
    try:
        PipelineRunner.from_env().run()
    except Exception as e:
        print(f"Startup error: {e}")
