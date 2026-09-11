from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config.offer_types import (
    DEFAULT_OFFER_TYPE,
    normalize_offer_type,
    supported_values,
)
from app.core.config import settings
from app.core.correlation import get_correlation_id
from app.core.exceptions import (
    ExcelFileNotFoundError,
    OfferRunInProgressError,
    RunNotFoundError,
    ServiceNotActiveError,
)
from app.core.run_context import start_run
from app.events.broker import scrape_broker
from app.events.run_lock import run_lock
from app.events.subscriber import mark_run_queued
from app.service.run_summary_service import read_run_summary

router = APIRouter(prefix=f"{settings.api_v1_prefix}/offers", tags=["offers"])


class ProcessRequest(BaseModel):
    type: str | None = None
    path: str | None = None


@router.post("/process")
def process_offers(request: ProcessRequest) -> dict[str, str]:
    """Publish an offer-generation event for a given type + input path.

    ``type`` defaults to ``sales_specials`` when omitted. An unsupported type
    returns a clear validation error listing the allowed values. ``path`` defaults
    to the configured ``default_excel_path``.

    Stage B (scrape) picks up the event, scrapes each matching URL in parallel,
    and fans out per-dealer data to stage C (extract), which runs the LLM and
    writes each dealer's output under ``storage/offers/<type>/``.
    """
    offer_type = normalize_offer_type(request.type)
    if not settings.is_service_active(offer_type.value):
        raise ServiceNotActiveError(offer_type.value)
    excel_path = request.path or settings.default_excel_path
    if not Path(excel_path).is_file():
        raise ExcelFileNotFoundError(excel_path, get_correlation_id())
    acquired, running = run_lock.acquire(offer_type.value)
    if not acquired:
        raise OfferRunInProgressError(running or "unknown")
    run_ctx = start_run()
    mark_run_queued(offer_type.value)
    scrape_broker.publish({"excel_path": excel_path, "offer_type": offer_type.value})
    return {
        "status": "processing",
        "message": "Your request has been accepted and is being processed. "
        "Offers will be generated in a few minutes.",
        "offer_type": offer_type.value,
        "excel_path": excel_path,
        "run_id": run_ctx.run_id,
        "output_dir": str(run_ctx.run_dir),
        "correlation_id": get_correlation_id(),
    }


@router.get("/types")
def list_types() -> dict[str, object]:
    """List every supported offer type and the default."""
    return {
        "supported": supported_values(),
        "default": DEFAULT_OFFER_TYPE.value,
        "correlation_id": get_correlation_id(),
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, object]:
    """Return the persisted status + counts for a run.

    Answers, for any run: did it finish, did it fail, how many URLs succeeded /
    failed, and where the output files are (``output_dir``). Returns 404 if no
    run with ``run_id`` exists.
    """
    summary = read_run_summary(run_id)
    if summary is None:
        raise RunNotFoundError(run_id)
    return {**summary, "correlation_id": get_correlation_id()}



@router.get("/generate")
def generate_offers(
    excel_path: str | None = Query(
        default=None,
        description="Path to the dealer-URL Excel workbook to process.",
    ),
    type: str | None = Query(
        default=None,
        description="Offer type (defaults to sales_specials).",
    ),
) -> dict[str, str]:
    """Backwards-compatible endpoint. Defaults to ``sales_specials`` and the
    configured default workbook path."""
    offer_type = normalize_offer_type(type)
    if not settings.is_service_active(offer_type.value):
        raise ServiceNotActiveError(offer_type.value)
    path = excel_path or settings.default_excel_path
    if not Path(path).is_file():
        raise ExcelFileNotFoundError(path, get_correlation_id())
    acquired, running = run_lock.acquire(offer_type.value)
    if not acquired:
        raise OfferRunInProgressError(running or "unknown")
    run_ctx = start_run()
    mark_run_queued(offer_type.value)
    scrape_broker.publish({"excel_path": path, "offer_type": offer_type.value})
    return {
        "status": "processing",
        "message": "Your request has been accepted and is being processed. "
        "Offers will be generated in a few minutes.",
        "offer_type": offer_type.value,
        "excel_path": path,
        "run_id": run_ctx.run_id,
        "output_dir": str(run_ctx.run_dir),
        "correlation_id": get_correlation_id(),
    }
