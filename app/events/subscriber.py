import threading
import time
from datetime import datetime
from typing import Any

from app.config.type_registry import get_processor
from app.core.logger import get_logger
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
        self._expected: int | None = None
        self._completed = 0
        self._total_urls = 0
        self._finalized = False

    def start(self) -> None:
        with self._lock:
            self._start_perf = time.perf_counter()
            self._start_dt = datetime.now()
            self._expected = None
            self._completed = 0
            self._total_urls = 0
            self._finalized = False

    def set_expected(self, expected: int) -> None:
        with self._lock:
            self._expected = expected
            self._maybe_finalize()

    def dealer_done(self, url_count: int = 0) -> None:
        with self._lock:
            self._completed += 1
            self._total_urls += url_count
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
            "duration_seconds=%.2f | dealer_count=%d | total_urls_processed=%d",
            self._start_dt.strftime("%Y-%m-%d %H:%M:%S") if self._start_dt else "-",
            end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duration,
            self._completed,
            self._total_urls,
        )
        run_lock.release()


_run_tracker = _RunTracker()


def handle_scrape_event(event: dict[str, Any]) -> None:
    """Stage B subscriber: resolve the processor for the event's offer type,
    scrape every matching URL, and publish each dealer to the extract broker
    (stage C) as soon as that dealer's URLs finish scraping, so extraction
    overlaps with the remaining scraping."""
    excel_path = event["excel_path"]
    offer_type = event.get("offer_type")
    processor = get_processor(offer_type)
    logger.info(
        "[%s] Scraping dealer URLs | excel_path=%s",
        processor.offer_type.value,
        excel_path,
    )

    _run_tracker.start()
    try:
        source_file, payloads = processor.scrape(
            excel_path,
            on_dealer_ready=extract_broker.publish,
            on_dealers_enumerated=_run_tracker.set_expected,
        )
    except Exception:
        logger.exception(
            "[%s] Scraping stage failed | excel_path=%s",
            processor.offer_type.value,
            excel_path,
        )
        run_lock.release()
        raise

    logger.info(
        "[%s] Scraping stage completed; all dealers dispatched to extract | "
        "source_file=%s | dealer_count=%d",
        processor.offer_type.value,
        source_file,
        len(payloads),
    )


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

    try:
        result = processor.build_dealer(event)
        logger.info(
            "[%s] Dealer extraction completed | dealer_id=%s | zip_name=%s | error_file_name=%s",
            processor.offer_type.value,
            result.dealer_id,
            result.zip_name,
            result.error_file_name,
        )
    finally:
        _run_tracker.dealer_done(len(event.get("urls", [])))

