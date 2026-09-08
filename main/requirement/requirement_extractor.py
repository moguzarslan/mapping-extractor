"""Entry point for requirement extraction.

Which extraction is run is decided by REQUIREMENT_VERSION in the environment
(v1, v2 — see main/requirement/versions.py); this file only starts it.
"""

import sys
from pathlib import Path

# The package imports below are absolute (main.*, service.*, resource.*), so the
# project root has to be importable even when this file is run as a script. This
# file sits at main/requirement/, hence two levels up.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

from main.requirement.runner import RequirementExtractionRunner

load_dotenv()


if __name__ == "__main__":
    try:
        RequirementExtractionRunner.from_env().run()
    except Exception as e:
        print(f"Startup error: {e}")
