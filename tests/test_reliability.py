"""Reliability tests for the offer-generation run lifecycle.

Covers the exact scenarios required for the ~60-URL, once-or-twice-a-month
workload, without hitting the network or the LLM:

  1. Missing Gemini key -> run fails -> lock released -> next run can start.
  2. Missing Excel file -> API returns an error immediately.
  3. 60 URLs all succeed.
  4. 60 URLs with 5 failures -> 55 succeeded + 5 failed, run still completes.
  8. Background processing crashes -> run status becomes failed.
  9. Output directory cannot be written -> run reports failed.

Case 7 (same dealer processed twice on the same day -> outputs do not mix) lives
in ``test_processors.py`` next to the processor output-isolation tests, and case
10 (``.env`` excluded from the Docker build context) lives in
``test_docker_build_context.py``.

The run-lifecycle cases drive the real run lock, run tracker, and
``run_summary.json`` writer through the subscriber, using a fake processor so the
orchestration is exercised deterministically.
"""

import json

import pytest
from fastapi.testclient import TestClient

import app.api.offers as offers_module
import app.events.subscriber as subscriber_module
from app.config.offer_types import normalize_offer_type
from app.core.exceptions import LLMExtractionError
from app.core.run_context import start_run
from app.events.run_lock import run_lock
from app.main import app
from app.schemas.offer import DealerZipResult


class FakeProcessor:
    """Stand-in for a real processor that produces a controllable run.

    ``dealer_count`` one-URL dealers are enumerated and dispatched. Dealers whose
    index is in ``fail_indices`` report a scrape failure (counted as a failed
    URL). When ``scrape_raises`` is set, the scrape stage raises before any dealer
    is dispatched, simulating a background crash / missing-key failure.
    """

    def __init__(
        self,
        offer_type: str = "sales_specials",
        dealer_count: int = 1,
        fail_indices=(),
        scrape_raises: Exception | None = None,
    ) -> None:
        self.offer_type = normalize_offer_type(offer_type)
        self.dealer_count = dealer_count
        self.fail_indices = set(fail_indices)
        self.scrape_raises = scrape_raises

    def scrape(self, excel_path, on_dealer_ready=None, on_dealers_enumerated=None):
        if self.scrape_raises is not None:
            raise self.scrape_raises
        if on_dealers_enumerated is not None:
            on_dealers_enumerated(self.dealer_count)
        payloads = []
        for i in range(self.dealer_count):
            payload = {
                "dealer_id": f"D{i}",
                "dealer_name": f"Dealer {i}",
                "date_token": "20260101",
                "offer_type": self.offer_type.value,
                "urls": [
                    {
                        "oem": f"OEM{i}",
                        "url": f"https://dealer{i}.example",
                        "body": "page text",
                        "scrape_error": None,
                    }
                ],
            }
            payloads.append(payload)
            if on_dealer_ready is not None:
                on_dealer_ready(payload)
        return "source.xlsx", payloads

    def build_dealer(self, payload) -> DealerZipResult:
        index = int(payload["dealer_id"][1:])
        if index in self.fail_indices:
            return DealerZipResult(
                dealer_id=payload["dealer_id"],
                dealer_name=payload["dealer_name"],
                errors={payload["urls"][0]["oem"]: "scrape failed"},
                scrape_error_count=1,
            )
        return DealerZipResult(
            dealer_id=payload["dealer_id"],
            dealer_name=payload["dealer_name"],
            offer_counts={"records": 1},
        )


def _install(monkeypatch, processor):
    """Route both stages through the fake processor and run extract inline."""
    monkeypatch.setattr(subscriber_module, "get_processor", lambda *_: processor)
    monkeypatch.setattr(
        subscriber_module.extract_broker,
        "publish",
        subscriber_module.handle_extract_event,
    )


def _start_run_and_lock(offer_type: str = "sales_specials"):
    ctx = start_run()
    run_lock.release()
    acquired, _ = run_lock.acquire(offer_type)
    assert acquired
    return ctx


def _summary(ctx) -> dict:
    return json.loads((ctx.run_dir / "run_summary.json").read_text(encoding="utf-8"))


def _run(offer_type: str = "sales_specials") -> None:
    subscriber_module.handle_scrape_event(
        {"excel_path": "x.xlsx", "offer_type": offer_type}
    )


# --- Case 1 -----------------------------------------------------------------


def test_case1_missing_key_fails_releases_lock_then_next_run_starts(monkeypatch):
    """Missing Gemini key -> run fails -> lock released -> next run can start."""
    failing = FakeProcessor(
        scrape_raises=LLMExtractionError("No Gemini API key configured.")
    )
    _install(monkeypatch, failing)
    ctx = _start_run_and_lock()

    with pytest.raises(LLMExtractionError):
        _run()

    assert _summary(ctx)["status"] == "failed"
    assert run_lock.current() is None

    healthy = FakeProcessor(dealer_count=3)
    _install(monkeypatch, healthy)
    ctx2 = _start_run_and_lock()
    _run()

    summary = _summary(ctx2)
    assert summary["status"] == "completed"
    assert summary["successful"] == 3
    assert run_lock.current() is None


# --- Case 3 -----------------------------------------------------------------


def test_case3_sixty_urls_all_succeed(monkeypatch):
    processor = FakeProcessor(dealer_count=60)
    _install(monkeypatch, processor)
    ctx = _start_run_and_lock()

    _run()

    summary = _summary(ctx)
    assert summary["total_urls"] == 60
    assert summary["successful"] == 60
    assert summary["failed"] == 0
    assert summary["dealer_count"] == 60
    assert summary["status"] == "completed"
    assert run_lock.current() is None


# --- Case 4 -----------------------------------------------------------------


def test_case4_sixty_urls_five_failures_still_completes(monkeypatch):
    processor = FakeProcessor(dealer_count=60, fail_indices=range(5))
    _install(monkeypatch, processor)
    ctx = _start_run_and_lock()

    _run()

    summary = _summary(ctx)
    assert summary["total_urls"] == 60
    assert summary["successful"] == 55
    assert summary["failed"] == 5
    assert summary["dealer_count"] == 60
    assert summary["status"] == "completed_with_errors"
    assert run_lock.current() is None


# --- Case 8 -----------------------------------------------------------------


def test_case8_background_crash_marks_run_failed(monkeypatch):
    """A crash in the background scrape stage finalizes the run as failed and
    releases the lock so the service is not stuck."""
    processor = FakeProcessor(scrape_raises=RuntimeError("boom in background"))
    _install(monkeypatch, processor)
    ctx = _start_run_and_lock()

    with pytest.raises(RuntimeError):
        _run()

    summary = _summary(ctx)
    assert summary["status"] == "failed"
    assert summary.get("detail")
    assert run_lock.current() is None


# --- Case 2 and 9 (API boundary) --------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(offers_module.scrape_broker, "publish", lambda event: None)
    run_lock.release()
    with TestClient(app) as test_client:
        yield test_client
    run_lock.release()


@pytest.fixture
def excel_file(tmp_path):
    path = tmp_path / "dealers.xlsx"
    path.write_bytes(b"")
    return str(path)


def test_case2_missing_excel_returns_error_immediately(client):
    resp = client.post(
        "/api/v1/offers/process",
        json={"type": "sales_specials", "path": "does-not-exist.xlsx"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "excel_file_not_found"
    assert run_lock.current() is None


def test_case9_unwritable_output_dir_reports_failed(client, excel_file, monkeypatch):
    monkeypatch.setattr(
        offers_module.settings, "local_storage_dir", "/proc/nope/storage"
    )
    resp = client.post(
        "/api/v1/offers/process",
        json={"type": "sales_specials", "path": excel_file},
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "output_directory_error"
    assert run_lock.current() is None
