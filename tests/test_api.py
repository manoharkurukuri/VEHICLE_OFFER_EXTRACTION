import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.api.offers as offers_module
import app.events.subscriber as subscriber_module
from app.events.run_lock import run_lock
from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(offers_module.scrape_broker, "publish", lambda event: None)
    run_lock.release()
    with TestClient(app) as test_client:
        yield test_client
    run_lock.release()


@pytest.fixture
def excel_file(tmp_path):
    """A real workbook path so config validation passes; contents are never
    parsed because ``scrape_broker.publish`` is stubbed in the ``client`` fixture."""
    path = tmp_path / "dealers.xlsx"
    path.write_bytes(b"")
    return str(path)


def test_list_types(client):
    resp = client.get("/api/v1/offers/types")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default"] == "sales_specials"
    assert "used_inventory" in body["supported"]


def test_process_defaults_to_sales_specials(client, excel_file):
    resp = client.post("/api/v1/offers/process", json={"path": excel_file})
    assert resp.status_code == 200
    assert resp.json()["offer_type"] == "sales_specials"


def test_process_explicit_type(client, excel_file):
    resp = client.post(
        "/api/v1/offers/process",
        json={"type": "sales_specials", "path": excel_file},
    )
    assert resp.status_code == 200
    assert resp.json()["offer_type"] == "sales_specials"


def test_inactive_service_returns_403(client):
    resp = client.post(
        "/api/v1/offers/process",
        json={"type": "service_specials", "path": "x.xlsx"},
    )
    assert resp.status_code == 403
    body = resp.json()["error"]
    assert body["code"] == "service_not_active"
    assert "service_specials" in body["message"]


def test_process_invalid_type_returns_error(client):
    resp = client.post(
        "/api/v1/offers/process", json={"type": "abc", "path": "x.xlsx"}
    )
    assert resp.status_code == 400
    message = resp.json()["error"]["message"]
    assert "abc" in message
    assert "sales_specials" in message


def test_missing_excel_file_returns_404(client):
    resp = client.post(
        "/api/v1/offers/process",
        json={"type": "sales_specials", "path": "does-not-exist.xlsx"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "excel_file_not_found"


def test_missing_gemini_key_returns_error(client, excel_file, monkeypatch):
    monkeypatch.setattr(offers_module.settings, "gemini_api_key", SecretStr(""))
    monkeypatch.setattr(offers_module.settings, "gemini_api_keys", SecretStr(""))
    resp = client.post(
        "/api/v1/offers/process",
        json={"type": "sales_specials", "path": excel_file},
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "llm_configuration_error"


def test_unwritable_output_dir_returns_error(client, excel_file, monkeypatch):
    monkeypatch.setattr(
        offers_module.settings, "local_storage_dir", "/proc/nope/storage"
    )
    resp = client.post(
        "/api/v1/offers/process",
        json={"type": "sales_specials", "path": excel_file},
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "output_directory_error"


def test_second_request_while_running_is_rejected(client, excel_file):
    first = client.post(
        "/api/v1/offers/process", json={"type": "sales_specials", "path": excel_file}
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/offers/process", json={"type": "sales_specials", "path": excel_file}
    )
    assert second.status_code == 409
    body = second.json()["error"]
    assert body["code"] == "offer_run_in_progress"
    assert "sales_specials" in body["message"]


def test_lock_releases_after_run_completes(client, monkeypatch):
    first = client.post("/api/v1/offers/process", json={"type": "sales_specials"})
    assert first.status_code == 200
    run_lock.release()

    second = client.post("/api/v1/offers/process", json={"type": "sales_specials"})
    assert second.status_code == 200
    assert second.json()["offer_type"] == "sales_specials"


def test_lock_releases_when_processor_init_fails(monkeypatch):
    """A failure while resolving/initializing the processor (e.g. a missing or
    invalid LLM key) must still release the run lock so the next run can start."""
    run_lock.release()

    def _boom(offer_type):
        raise RuntimeError("Gemini API key is missing")

    monkeypatch.setattr(subscriber_module, "get_processor", _boom)

    acquired, _ = run_lock.acquire("sales_specials")
    assert acquired

    with pytest.raises(RuntimeError):
        subscriber_module.handle_scrape_event(
            {"excel_path": "x.xlsx", "offer_type": "sales_specials"}
        )

    assert run_lock.current() is None
    reacquired, _ = run_lock.acquire("sales_specials")
    assert reacquired
    run_lock.release()
