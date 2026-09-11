"""Per-run output directory helpers.

Every run writes into its own unique folder so files from two different runs can
never mix, even when the app is run twice on the same day:

    <local_storage_dir>/<YYYYMMDD_HHMMSS>_<run_id>/
        <dealer>.zip
        errors/
        run_summary.json

Use these helpers instead of constructing paths by hand. The active run is
resolved from :mod:`app.core.run_context`.
"""

from __future__ import annotations

from pathlib import Path

from app.config.offer_types import OfferType
from app.core.run_context import get_run_context


def get_output_directory(offer_type: str | OfferType | None = None) -> Path:
    """The active run's output folder, e.g. ``storage/offers/20260911_140500_ab12``."""
    path = get_run_context().run_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_zip_directory(offer_type: str | OfferType | None = None) -> Path:
    """Directory for a run's ZIP files (the run folder itself)."""
    return get_output_directory(offer_type)


def get_error_directory(offer_type: str | OfferType | None = None) -> Path:
    """Directory for a run's error files, e.g. ``.../20260911_140500_ab12/errors``."""
    path = get_output_directory(offer_type) / "errors"
    path.mkdir(parents=True, exist_ok=True)
    return path
