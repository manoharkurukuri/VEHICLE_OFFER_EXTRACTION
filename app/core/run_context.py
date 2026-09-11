"""Per-run output isolation.

Every offer-generation execution ("run") gets its own timestamped output folder
so files from two different runs can never mix:

    <local_storage_dir>/<YYYYMMDD_HHMMSS>_<run_id>/
        <dealer>.zip
        errors/
        run_summary.json

The active run is tracked in a :class:`contextvars.ContextVar` so it propagates
into worker threads (via :class:`ContextThreadPoolExecutor`) and across the
in-memory brokers (which serialize/restore it per event).
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings

_run_context: ContextVar["RunContext | None"] = ContextVar(
    "run_context", default=None
)


@dataclass(frozen=True)
class RunContext:
    """Identifies a single run and the unique folder its output is written to."""

    run_id: str
    run_dir: Path

    def to_dict(self) -> dict[str, str]:
        return {"run_id": self.run_id, "run_dir": str(self.run_dir)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunContext":
        return cls(run_id=str(data["run_id"]), run_dir=Path(data["run_dir"]))


def generate_run_id() -> str:
    """Return a short, unique identifier for a run."""
    return uuid.uuid4().hex[:12]


def create_run_context(run_id: str | None = None) -> RunContext:
    """Build a run context with a fresh, unique output folder path.

    The folder is not created on disk here; it is created lazily the first time
    output is written, so failed runs that produce no files leave no empty dirs.
    """
    run_id = run_id or generate_run_id()
    timestamp = datetime.now(ZoneInfo(settings.app_timezone)).strftime("%Y%m%d_%H%M%S")
    run_dir = Path(settings.local_storage_dir) / f"{timestamp}_{run_id}"
    return RunContext(run_id=run_id, run_dir=run_dir)


def set_run_context(ctx: RunContext | None) -> None:
    _run_context.set(ctx)


def peek_run_context() -> RunContext | None:
    """Return the active run context, or ``None`` if none is set (no lazy create)."""
    return _run_context.get()


def get_run_context() -> RunContext:
    """Return the active run context, creating one on demand as a safety net."""
    ctx = _run_context.get()
    if ctx is None:
        ctx = create_run_context()
        _run_context.set(ctx)
    return ctx


def start_run(run_id: str | None = None) -> RunContext:
    """Create a new run context and make it the active one."""
    ctx = create_run_context(run_id)
    set_run_context(ctx)
    return ctx
