"""Paths, scoring config and environment variables."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def load_scoring(path: Path | None = None) -> dict:
    return yaml.safe_load((path or ROOT / "scoring.yaml").read_text())


def load_env() -> None:
    """Load .env from the repo root if python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)}")
    return {n: os.environ[n] for n in names}
