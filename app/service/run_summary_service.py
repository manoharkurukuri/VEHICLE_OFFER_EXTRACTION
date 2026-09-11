"""Read persisted run summaries from disk.

Each run writes a ``run_summary.json`` into its own folder
``<local_storage_dir>/<YYYYMMDD_HHMMSS>_<run_id>/``. This service resolves a
``run_id`` back to that folder and returns the parsed summary so callers can
answer "did it finish / fail?" and "how many URLs succeeded / failed?" for any
past or in-flight run.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


def read_run_summary(run_id: str) -> dict | None:
    """Return the summary dict for ``run_id``, or ``None`` if no run is found.

    The most recent matching folder wins when a ``run_id`` somehow repeats.
    """
    storage_dir = Path(settings.local_storage_dir)
    if not storage_dir.is_dir():
        return None

    matches = sorted(
        (p for p in storage_dir.glob(f"*_{run_id}") if p.is_dir()),
        reverse=True,
    )
    for run_dir in matches:
        summary_path = run_dir / "run_summary.json"
        if not summary_path.is_file():
            continue
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.exception(
                "Failed to read run_summary.json for run_id=%s", run_id
            )
            return None
    return None
