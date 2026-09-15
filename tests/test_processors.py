import types
import zipfile
from pathlib import Path

from app.core.run_context import get_run_context, start_run
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


def test_used_inventory_output_written_to_run_folder():
    response = InventoryResponse(
        records=[InventoryItem(title="t", vehicle_name="2025 Ford", price="1", url="u")]
    )
    processor = UsedInventoryProcessor(service=_fake_service(extract_result=response))

    result = processor.build_dealer(_payload("used_inventory"))

    run_dir = get_run_context().run_dir
    assert result.zip_path is not None
    assert Path(result.zip_path).parent == run_dir
    with zipfile.ZipFile(result.zip_path) as archive:
        names = archive.namelist()
    assert any(name.endswith(".json") for name in names)


def test_scrape_error_written_to_run_error_dir():
    processor = UsedInventoryProcessor(service=_fake_service(extract_result=None))
    payload = _payload("used_inventory")
    payload["urls"][0] = {
        "oem": "Ford",
        "url": "https://x",
        "body": None,
        "scrape_error": "boom",
    }

    result = processor.build_dealer(payload)

    run_dir = get_run_context().run_dir
    assert result.error_file_path is not None
    assert Path(result.error_file_path).parent == run_dir / "errors"


def test_sales_specials_processor_delegates_to_real_service():
    sentinel = object()
    fake = _fake_service(build_dealer_result=sentinel)
    processor = SalesSpecialsProcessor(service=fake)

    captured = {}
    fake.build_dealer = lambda payload: captured.update(payload) or sentinel

    result = processor.build_dealer(_payload("sales_specials"))

    assert result is sentinel
    assert captured["offer_type"] == "sales_specials"


def test_case7_same_dealer_twice_same_day_outputs_do_not_mix():
    """Case 7: the same dealer processed twice on the same day writes into two
    separate run folders, so the two runs' outputs never mix."""
    response = InventoryResponse(
        records=[InventoryItem(title="t", vehicle_name="2025 Ford", price="1", url="u")]
    )
    processor = UsedInventoryProcessor(service=_fake_service(extract_result=response))
    payload = _payload("used_inventory")

    first_ctx = start_run()
    first = processor.build_dealer(payload)

    second_ctx = start_run()
    second = processor.build_dealer(payload)

    assert first_ctx.run_dir != second_ctx.run_dir
    assert first.zip_path is not None and second.zip_path is not None
    assert Path(first.zip_path).parent == first_ctx.run_dir
    assert Path(second.zip_path).parent == second_ctx.run_dir
    assert first.zip_path != second.zip_path
    assert Path(first.zip_path).exists()
    assert Path(second.zip_path).exists()
