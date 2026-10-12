"""Central settings, read from environment variables (and a local .env file)."""
import logging.config
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()  # reads .env from the current folder if it exists

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"  # saved copies of fetched pages; git-ignored, internal only

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://scanner:scanner_dev@localhost:5432/scanner",
)
SCANNER_USER_AGENT = os.environ.get(
    "SCANNER_USER_AGENT",
    "MarketplaceScannerBot/0.1 (+https://github.com/aggreypaintsil168/marketplace-scanner; contact: SET-ME)",
)


def configure_logging() -> None:
    """Apply config/logging.yaml, writing the log file to data/scanner.log."""
    settings = yaml.safe_load((Path(__file__).parent / "logging.yaml").read_text(encoding="utf-8"))
    log_dir = ROOT / "data"
    log_dir.mkdir(exist_ok=True)
    settings["handlers"]["file"]["filename"] = str(log_dir / "scanner.log")
    logging.config.dictConfig(settings)
