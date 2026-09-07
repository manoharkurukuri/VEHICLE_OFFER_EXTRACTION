import pandas as pd
import pytest

from app.config.offer_types import OfferType
from app.service.offer_generation_service import OfferGenerationService


def _make_workbook(path):
    df = pd.DataFrame(
        [
            {"id": "D1", "DealerName": "Dealer One", "oem": "GMC", "type": "Sales Specials", "url": "https://a"},
            {"id": "D1", "DealerName": "Dealer One", "oem": "GMC", "type": "Service Specials", "url": "https://b"},
            {"id": "D2", "DealerName": "Dealer Two", "oem": "Ford", "type": "Used Inventory", "url": "https://c"},
            {"id": "D3", "DealerName": "Dealer Three", "oem": "Kia", "type": "Homepage", "url": "https://d"},
            {"id": "D3", "DealerName": "Dealer Three", "oem": "Kia", "type": "Contact Us", "url": "https://e"},
            {"id": "D4", "DealerName": "Dealer Four", "oem": "BMW", "type": "Map", "url": "https://f"},
        ]
    )
    df.to_excel(path, index=False)


@pytest.fixture
def service(monkeypatch):
    svc = OfferGenerationService()
    monkeypatch.setattr(svc, "_scrape_url", lambda url: (url, "body-text", None))
    return svc


def _dealer_ids(payloads):
    return {p["dealer_id"] for p in payloads if p["urls"]}


def test_default_processes_only_sales_specials(tmp_path, service):
    wb = tmp_path / "in.xlsx"
    _make_workbook(wb)
    _, payloads = service.scrape_dealers(wb)
    assert _dealer_ids(payloads) == {"D1"}
    d1 = next(p for p in payloads if p["dealer_id"] == "D1")
    assert len(d1["urls"]) == 1
    assert d1["offer_type"] == "sales_specials"


def test_explicit_type_filters_rows(tmp_path, service):
    wb = tmp_path / "in.xlsx"
    _make_workbook(wb)
    _, payloads = service.scrape_dealers(wb, offer_type=OfferType.USED_INVENTORY)
    assert _dealer_ids(payloads) == {"D2"}
    d2 = next(p for p in payloads if p["dealer_id"] == "D2")
    assert d2["offer_type"] == "used_inventory"


def test_unsupported_rows_are_skipped_not_failed(tmp_path, service):
    wb = tmp_path / "in.xlsx"
    _make_workbook(wb)
    _, payloads = service.scrape_dealers(wb, offer_type=OfferType.SERVICE_SPECIALS)
    assert _dealer_ids(payloads) == {"D1"}


def test_status_false_dealers_are_excluded(tmp_path, service):
    wb = tmp_path / "in.xlsx"
    df = pd.DataFrame(
        [
            {"id": "D1", "DealerName": "Dealer One", "oem": "GMC", "type": "Sales Specials", "url": "https://a", "status": True},
            {"id": "D2", "DealerName": "Dealer Two", "oem": "Ford", "type": "Sales Specials", "url": "https://b", "status": False},
        ]
    )
    df.to_excel(wb, index=False)
    _, payloads = service.scrape_dealers(wb)
    assert _dealer_ids(payloads) == {"D1"}


def test_status_accepts_string_true(tmp_path, service):
    wb = tmp_path / "in.xlsx"
    df = pd.DataFrame(
        [
            {"id": "D1", "DealerName": "Dealer One", "oem": "GMC", "type": "Sales Specials", "url": "https://a", "status": "TRUE"},
            {"id": "D2", "DealerName": "Dealer Two", "oem": "Ford", "type": "Sales Specials", "url": "https://b", "status": "false"},
        ]
    )
    df.to_excel(wb, index=False)
    _, payloads = service.scrape_dealers(wb)
    assert _dealer_ids(payloads) == {"D1"}


def test_status_accepts_numeric_0_and_1(tmp_path, service):
    wb = tmp_path / "in.xlsx"
    df = pd.DataFrame(
        [
            {"id": "D1", "DealerName": "Dealer One", "oem": "GMC", "type": "Sales Specials", "url": "https://a", "status": 1},
            {"id": "D2", "DealerName": "Dealer Two", "oem": "Ford", "type": "Sales Specials", "url": "https://b", "status": 0},
        ]
    )
    df.to_excel(wb, index=False)
    _, payloads = service.scrape_dealers(wb)
    assert _dealer_ids(payloads) == {"D1"}


