import types
import zipfile

from app.processors.sales_specials_processor import SalesSpecialsProcessor
from app.processors.used_inventory_processor import UsedInventoryProcessor
from app.response_templates.used_inventory import InventoryItem, InventoryResponse


def _fake_service(extract_result=None, build_dealer_result=None):
    """Minimal stand-in for OfferGenerationService used by processors."""
    extractor = types.SimpleNamespace(
        extract=lambda body, prompt=None, schema=None: extract_result
    )
    workflow = types.SimpleNamespace(llm_extractor=extractor)
    return types.SimpleNamespace(
        workflow=workflow,
        build_dealer=lambda payload: build_dealer_result,
    )


def _payload(offer_type):
    return {
        "dealer_id": "D9",
        "dealer_name": "Nine Motors",
        "date_token": "20260101",
        "offer_type": offer_type,
        "urls": [
            {"oem": "Ford", "url": "https://x", "body": "text", "scrape_error": None}
        ],
    }


def test_used_inventory_output_isolated_to_its_own_dir():
    response = InventoryResponse(
        records=[InventoryItem(title="t", vehicle_name="2025 Ford", price="1", url="u")]
    )
    processor = UsedInventoryProcessor(service=_fake_service(extract_result=response))

    result = processor.build_dealer(_payload("used_inventory"))

    assert result.zip_path is not None
    assert "/used_inventory/zip/" in result.zip_path.replace("\\", "/")
    assert "sales_specials" not in result.zip_path
    with zipfile.ZipFile(result.zip_path) as archive:
        names = archive.namelist()
    assert any(name.endswith(".json") for name in names)


def test_scrape_error_written_to_type_error_dir():
    processor = UsedInventoryProcessor(service=_fake_service(extract_result=None))
    payload = _payload("used_inventory")
    payload["urls"][0] = {
        "oem": "Ford",
        "url": "https://x",
        "body": None,
        "scrape_error": "boom",
    }

    result = processor.build_dealer(payload)

    assert result.error_file_path is not None
    assert "/used_inventory/errors/" in result.error_file_path.replace("\\", "/")


def test_sales_specials_processor_delegates_to_real_service():
    sentinel = object()
    fake = _fake_service(build_dealer_result=sentinel)
    processor = SalesSpecialsProcessor(service=fake)

    captured = {}
    fake.build_dealer = lambda payload: captured.update(payload) or sentinel

    result = processor.build_dealer(_payload("sales_specials"))

    assert result is sentinel
    assert captured["offer_type"] == "sales_specials"
