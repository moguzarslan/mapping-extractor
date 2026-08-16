"""Entry point for architecture extraction.

Which extraction is run is decided by ARCHITECTURE_VERSION in the environment
(chained, v1, v2, v3, v4 — see main/architecture/versions.py); this file only
starts it.
"""

import sys
from pathlib import Path

# The package imports below are absolute (main.*, service.*, resource.*), so the
# project root has to be importable even when this file is run as a script. This
# file sits at main/architecture/, hence two levels up.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

from main.architecture.runner import ArchitectureExtractionRunner

load_dotenv()


if __name__ == "__main__":
    try:
        ArchitectureExtractionRunner.from_env().run()
    except Exception as e:
        print(f"Startup error: {e}")
