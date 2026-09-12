import json
import threading
import time
from datetime import datetime
from typing import Any

from app.config.type_registry import get_processor
from app.core.correlation import get_correlation_id
from app.core.logger import get_logger
from app.core.run_context import get_run_context
from app.events.broker import extract_broker
from app.events.run_lock import run_lock

logger = get_logger(__name__)


class _RunTracker:
    """Tracks a single offer-generation run so the total elapsed time can be
    logged once every dealer's extraction (stage C) has completed.

    Dealers are dispatched to extract as they finish scraping, so some may
    complete before the total dealer count is known; the lock + late-set
    ``expected`` handle that race and finalize exactly once.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_perf: float | None = None
        self._start_dt: datetime | None = None
        self._offer_type: str | None = None
        self._expected: int | None = None
        self._completed = 0
        self._total_urls = 0
        self._total_urls_succeeded = 0
        self._total_urls_error = 0
        self._total_no_offers = 0
        self._total_scrape_errors = 0
        self._finalized = False

    def queued(self, offer_type: str | None = None) -> None:
        """Write an initial ``queued`` summary the moment a run is accepted, so a
        run_id can always be resolved even if the background job never starts."""
        with self._lock:
            self._start_perf = None
            self._start_dt = None
            self._offer_type = offer_type
            self._expected = None
            self._completed = 0
            self._total_urls = 0
            self._total_urls_succeeded = 0
            self._total_urls_error = 0
            self._total_no_offers = 0
            self._total_scrape_errors = 0
            self._finalized = False
            self._write_summary(status="queued")

    def start(self, offer_type: str | None = None) -> None:
        with self._lock:
            self._start_perf = time.perf_counter()
            self._start_dt = datetime.now()
            self._offer_type = offer_type
            self._expected = None
            self._completed = 0
            self._total_urls = 0
            self._total_urls_succeeded = 0
            self._total_urls_error = 0
            self._total_no_offers = 0
            self._total_scrape_errors = 0
            self._finalized = False
            self._write_summary(status="running")

    def set_expected(self, expected: int) -> None:
        with self._lock:
            self._expected = expected
            self._maybe_finalize()

    def dealer_done(
        self,
        url_count: int = 0,
        error_count: int = 0,
        no_offer_count: int = 0,
        scrape_error_count: int = 0,
    ) -> None:
        with self._lock:
            self._completed += 1
            self._total_urls += url_count
            self._total_urls_error += error_count
            self._total_urls_succeeded += max(url_count - error_count, 0)
            self._total_no_offers += no_offer_count
            self._total_scrape_errors += scrape_error_count
            self._maybe_finalize()

    def _maybe_finalize(self) -> None:
        if (
            self._finalized
            or self._expected is None
            or self._completed < self._expected
        ):
            return
        self._finalized = True
        end_dt = datetime.now()
        duration = (
            time.perf_counter() - self._start_perf if self._start_perf else 0.0
        )
        logger.info(
            "All dealers extraction completed | start=%s | end=%s | "
            "duration_seconds=%.2f | dealer_count=%d | total_urls_processed=%d | "
            "total_urls_succeeded=%d | total_urls_error=%d | "
            "no_offers_extracted=%d | scraping_error_count=%d",
            self._start_dt.strftime("%Y-%m-%d %H:%M:%S") if self._start_dt else "-",
            end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duration,
            self._completed,
            self._total_urls,
            self._total_urls_succeeded,
            self._total_urls_error,
            self._total_no_offers,
            self._total_scrape_errors,
        )
        status = (
            "completed_with_errors" if self._total_urls_error > 0 else "completed"
        )
        self._write_summary(status=status, end_dt=end_dt, duration=duration)
        run_lock.release()

    def _write_summary(
        self,
        status: str,
        end_dt: datetime | None = None,
        duration: float | None = None,
        detail: str = "",
    ) -> None:
        """Write ``run_summary.json`` into the active run's output folder."""
        try:
            ctx = get_run_context()
            ctx.run_dir.mkdir(parents=True, exist_ok=True)
            summary = {
                "run_id": ctx.run_id,
                "offer_type": self._offer_type,
                "status": status,
                "dealer_correlation_id": get_correlation_id(),
                "output_dir": str(ctx.run_dir),
                "total_urls": self._total_urls,
                "successful": self._total_urls_succeeded,
                "failed": self._total_urls_error,
                "started_at": (
                    self._start_dt.strftime("%Y-%m-%d %H:%M:%S")
                    if self._start_dt
                    else None
                ),
                "ended_at": (
                    end_dt.strftime("%Y-%m-%d %H:%M:%S") if end_dt else None
                ),
                "duration_seconds": (
                    round(duration, 2) if duration is not None else None
                ),
                "dealer_count": self._completed,
                "total_urls_processed": self._total_urls,
                "total_urls_succeeded": self._total_urls_succeeded,
                "total_urls_error": self._total_urls_error,
                "no_offers_extracted": self._total_no_offers,
                "scraping_error_count": self._total_scrape_errors,
            }
            if detail:
                summary["detail"] = detail
            (ctx.run_dir / "run_summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.exception("Failed to write run_summary.json")

    def fail(self, reason: str) -> None:
        """Finalize a run that failed before any dealer was dispatched."""
        with self._lock:
            if self._finalized:
                return
            self._finalized = True
            end_dt = datetime.now()
            duration = (
                time.perf_counter() - self._start_perf if self._start_perf else 0.0
            )
            self._write_summary(
                status="failed", end_dt=end_dt, duration=duration, detail=reason
            )


_run_tracker = _RunTracker()


def mark_run_queued(offer_type: str | None = None) -> None:
    """Write an initial ``queued`` run_summary.json for the active run.

    Called from the API right after the run is accepted so every run_id has a
    persisted status on disk, even if the background job never starts.
    """
    _run_tracker.queued(offer_type)


def handle_scrape_event(event: dict[str, Any]) -> None:
    """Stage B subscriber: resolve the processor for the event's offer type,
    scrape every matching URL, and publish each dealer to the extract broker
    (stage C) as soon as that dealer's URLs finish scraping, so extraction
    overlaps with the remaining scraping.

    A single outer ``try/finally`` guarantees the run lock is released whenever
    the run fails before dealers are dispatched to extract — including failures
    while resolving/initializing the processor (e.g. a missing or invalid LLM
    key). On success the lock is instead released by ``_run_tracker`` once every
    dispatched dealer has finished extracting.
    """
    excel_path = event.get("excel_path")
    offer_type = event.get("offer_type")
    dispatched = False
    try:
        processor = get_processor(offer_type)
        logger.info(
            "[%s] Scraping dealer URLs | excel_path=%s",
            processor.offer_type.value,
            excel_path,
        )

        _run_tracker.start(processor.offer_type.value)
        source_file, payloads = processor.scrape(
            excel_path,
            on_dealer_ready=extract_broker.publish,
            on_dealers_enumerated=_run_tracker.set_expected,
        )
        dispatched = True

        logger.info(
            "[%s] Scraping stage completed; all dealers dispatched to extract | "
            "source_file=%s | dealer_count=%d",
            processor.offer_type.value,
            source_file,
            len(payloads),
        )
    except Exception:
        logger.exception(
            "Scrape stage failed before dealers were dispatched | "
            "offer_type=%s | excel_path=%s",
            offer_type,
            excel_path,
        )
        raise
    finally:
        if not dispatched:
            _run_tracker.fail("scrape stage failed before dealers were dispatched")
            run_lock.release()


def handle_extract_event(event: dict[str, Any]) -> None:
    """Stage C subscriber: resolve the processor from the payload's offer type,
    extract for one dealer, and write that dealer's output + error file."""
    dealer_id = event.get("dealer_id")
    processor = get_processor(event.get("offer_type"))
    logger.info(
        "[%s] Extracting offers for dealer | dealer_id=%s",
        processor.offer_type.value,
        dealer_id,
    )

    url_count = len(event.get("urls", []))
    try:
        result = processor.build_dealer(event)
        logger.info(
            "[%s] Dealer extraction completed | dealer_id=%s | zip_name=%s | "
            "error_file_name=%s",
            processor.offer_type.value,
            result.dealer_id,
            result.zip_name,
            result.error_file_name,
        )
        _run_tracker.dealer_done(
            url_count,
            len(result.errors),
            result.no_offer_count,
            result.scrape_error_count,
        )
    except Exception:
        _run_tracker.dealer_done(url_count, url_count)
        raise
