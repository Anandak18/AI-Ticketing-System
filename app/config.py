import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

TICKETS_PATH = Path(os.getenv("TICKETS_PATH", DATA_DIR / "tickets.json"))
MEMORY_PATH = Path(os.getenv("MEMORY_PATH", DATA_DIR / "memory.json"))

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "120"))  # every 2 minutes
CONFIDENCE_CLOSE_THRESHOLD = float(os.getenv("CONFIDENCE_CLOSE_THRESHOLD", "0.85"))
