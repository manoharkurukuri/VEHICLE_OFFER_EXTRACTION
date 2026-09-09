"""Processor abstraction: one processor per offer type.

Every processor exposes the same interface so the API and broker can drive any
offer type uniformly:

    processor = get_processor(offer_type)
    processor.process(excel_path)
    processor.scrape(excel_path, on_dealer_ready)
    processor.build_dealer(payload)

Shared scraping + parallelism live here (and in :class:`OfferGenerationService`),
so type-specific behavior is isolated to ``build_dealer``. The Sales Specials
processor delegates to the existing, unchanged extraction logic; the other types
use a lightweight placeholder that reuses the shared scrape + LLM flow.
"""

from __future__ import annotations

import json
import re
import zipfile
from concurrent.futures import as_completed
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config.offer_types import DEFAULT_OFFER_TYPE, OfferType
from app.core.concurrency import ContextThreadPoolExecutor
from app.core.config import settings
from app.core.logger import get_logger
from app.schemas.offer import DealerZipResult, GenerateOffersResult
from app.service.offer_generation_service import OfferGenerationService
from app.utils.output_paths import get_error_directory, get_zip_directory

logger = get_logger(__name__)


def _slugify(value: str) -> str:
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "dealer"


class BaseProcessor:
    """Base class holding the shared scrape + parallel-run infrastructure.

    Subclasses set :attr:`offer_type`, :attr:`prompt`, and :attr:`response_schema`
    and may override :meth:`build_dealer` to diverge per type.
    """

    offer_type: OfferType = DEFAULT_OFFER_TYPE
    prompt: str | None = None
    response_schema: type[BaseModel] | None = None

    def __init__(self, service: OfferGenerationService | None = None) -> None:
        self.service = service or OfferGenerationService()

    def scrape(
        self,
        excel_path: str | Path,
        on_dealer_ready=None,
        on_dealers_enumerated=None,
    ) -> tuple[str, list[dict[str, Any]]]:
        return self.service.scrape_dealers(
            excel_path,
            offer_type=self.offer_type,
            on_dealer_ready=on_dealer_ready,
            on_dealers_enumerated=on_dealers_enumerated,
        )

    def build_dealer(self, payload: dict[str, Any]) -> DealerZipResult:
        """Default (placeholder) extraction: run the type's prompt + schema over
        each already-scraped URL and write one JSON per dealer, zipped, plus a
        combined error file — all under the type's output directory.

        TODO: replace with real per-type extraction/serialization as each type is
        productionized.
        """
        dealer_id = payload["dealer_id"]
        dealer_name = payload["dealer_name"]
        date_token = payload["date_token"]
        prefix = f"[{self.offer_type.value}]"
        logger.info(
            "%s Extracting dealer records | dealer_id=%s | url_count=%d",
            prefix,
            dealer_id,
            len(payload["urls"]),
        )

        extractor = self.service.workflow.llm_extractor
        records: list[dict[str, Any]] = []
        error_sections: list[str] = []
        errors: dict[str, str] = {}
        cache: dict[str, list[dict[str, Any]] | Exception] = {}

        for entry in payload["urls"]:
            oem = entry.get("oem", "")
            url = entry.get("url", "")
            scrape_error = entry.get("scrape_error")
            if scrape_error:
                msg = f"URL: {url}\nOEM: {oem}\nError: {scrape_error}"
                error_sections.append(msg)
                errors[oem] = str(scrape_error)
                continue

            cached = cache.get(url)
            if cached is None:
                try:
                    result = extractor.extract(
                        entry["body"],
                        prompt=self.prompt,
                        schema=self.response_schema,
                    )
                    cached = [
                        rec.model_dump() for rec in getattr(result, "records", [])
                    ]
                except Exception as exc:
                    logger.error(
                        "%s Extraction failed | dealer_id=%s | oem=%s | url=%s | error=%s",
                        prefix,
                        dealer_id,
                        oem,
                        url,
                        str(exc),
                    )
                    cached = exc
                cache[url] = cached

            if isinstance(cached, Exception):
                msg = f"URL: {url}\nOEM: {oem}\nError: {cached}"
                error_sections.append(msg)
                errors[oem] = str(cached)
                continue

            for rec in cached:
                records.append({"oem": oem, "url": url, **rec})

        return self._assemble(
            dealer_id, dealer_name, date_token, records, error_sections, errors
        )

    def _assemble(
        self,
        dealer_id: str,
        dealer_name: str,
        date_token: str,
        records: list[dict[str, Any]],
        error_sections: list[str],
        errors: dict[str, str],
    ) -> DealerZipResult:
        result = DealerZipResult(
            dealer_id=dealer_id,
            dealer_name=dealer_name,
            offer_counts={"records": len(records)},
            errors=errors,
        )
        prefix = f"[{self.offer_type.value}]"

        if records:
            stem = f"{_slugify(dealer_id)}_{_slugify(dealer_name)}_{date_token}"
            json_bytes = json.dumps(records, indent=2, default=str).encode("utf-8")
            zip_dir = get_zip_directory(self.offer_type)
            zip_path = zip_dir / f"{stem}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(f"{stem}.json", json_bytes)
            result.zip_name = zip_path.name
            result.zip_path = str(zip_path)
            result.excel_files = [f"{stem}.json"]
            logger.info(
                "%s Dealer zip created | dealer_id=%s | zip_name=%s | record_count=%d",
                prefix,
                dealer_id,
                zip_path.name,
                len(records),
            )

        if error_sections:
            error_dir = get_error_directory(self.offer_type)
            error_name = (
                f"error_{_slugify(dealer_id)}_{_slugify(dealer_name)}_{date_token}.txt"
            )
            error_path = error_dir / error_name
            error_path.write_text(
                ("\n\n" + "-" * 60 + "\n\n").join(error_sections), encoding="utf-8"
            )
            result.error_file_name = error_name
            result.error_file_path = str(error_path)
            logger.info(
                "%s Dealer error file created | dealer_id=%s | error_file_name=%s | error_count=%d",
                prefix,
                dealer_id,
                error_name,
                len(error_sections),
            )

        return result

    def process(self, excel_path: str | Path) -> GenerateOffersResult:
        prefix = f"[{self.offer_type.value}]"
        logger.info("%s Starting extraction | excel_path=%s", prefix, str(excel_path))
        source_file, payloads = self.scrape(excel_path)

        dealers: list[DealerZipResult | None] = [None] * len(payloads)
        if payloads:
            workers = max(1, min(settings.dealer_extract_workers, len(payloads)))
            with ContextThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self.build_dealer, payload): index
                    for index, payload in enumerate(payloads)
                }
                for future in as_completed(futures):
                    dealers[futures[future]] = future.result()

        return GenerateOffersResult(
            source_file=source_file,
            dealers=[dealer for dealer in dealers if dealer is not None],
        )
