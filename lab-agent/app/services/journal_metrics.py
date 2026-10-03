"""Read an explicitly supplied, licensed Journal Impact Factor mapping."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def impact_factor(journal: str | None) -> dict[str, Any] | None:
    """Look up JIF from a local JCR-derived JSON file, never from an unlicensed scrape."""
    configured = os.getenv("LAB_AGENT_JCR_METRICS_PATH")
    if not configured or not journal:
        return None
    path = Path(configured)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        record = payload.get(_key(journal))
        value = float(record["jif"])
        year = str(record["year"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if value < 0:
        return None
    return {"value": value, "year": year, "source": "JCR local import"}
