import re
import zipfile
from collections.abc import Callable
from concurrent.futures import as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from app.config.offer_types import (
    DEFAULT_OFFER_TYPE,
    IGNORED_EXCEL_TYPES,
    OfferType,
    excel_label,
    normalize_offer_type,
)
from app.core.concurrency import ContextThreadPoolExecutor
from app.core.config import settings
from app.core.exceptions import FileStorageError
from app.core.logger import get_logger
from app.schemas.offer import DealerZipResult, GenerateOffersResult
from app.service.excel_service import ExcelService
from app.utils.output_paths import get_error_directory, get_zip_directory
from app.workflows.offer_generation_graph import OfferGenerationWorkflow

REQUIRED_COLUMNS = {"id", "DealerName", "oem", "type", "url"}

logger = get_logger(__name__)


def _is_active_status(value: Any) -> bool:
    """Interpret an Excel ``status`` cell as active/inactive.

    Truthy: True, 1, "true", "yes", "y", "1". Falsy: False, 0, "0", blank/NaN, anything else.
    """
    if isinstance(value, bool):
        return value
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().casefold() in {"true", "yes", "y", "1"}


@dataclass
class _UrlResult:
    """Outcome of scraping + extracting a single dealer/OEM URL."""

    order: int
    dealer_id: str
    dealer_name: str
    oem: str
    url: str
    count: int
    file_name: str | None = None
    file_bytes: bytes | None = None
    error_message: str | None = None


class OfferGenerationService:
    """Reads a dealer-URL workbook, extracts Sales Specials offers per OEM, and
    packages each dealer's generated Excel files into a single zip."""

    def __init__(self) -> None:
        self.timezone_name = settings.app_timezone
        self.storage_dir = Path(settings.local_storage_dir)
        self.excel_service = ExcelService()
        self.workflow = OfferGenerationWorkflow(excel_service=self.excel_service)

    @staticmethod
    def _slugify(value: str) -> str:
        value = str(value).strip().lower()
        value = re.sub(r"[^a-z0-9]+", "_", value)
        return value.strip("_") or "dealer"

    def _date_token(self) -> str:
        return datetime.now(ZoneInfo(self.timezone_name)).strftime("%Y%m%d")

    def generate_from_excel(
        self,
        excel_path: str | Path,
        offer_type: str | OfferType = DEFAULT_OFFER_TYPE,
    ) -> GenerateOffersResult:
        """Synchronous end-to-end run (used by the CLI): scrape every dealer URL,
        then extract + zip each dealer, dealers processed in parallel."""
        resolved = normalize_offer_type(offer_type)
        source_file, payloads = self.scrape_dealers(excel_path, offer_type=resolved)

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

    def scrape_dealers(
        self,
        excel_path: str | Path,
        offer_type: str | OfferType = DEFAULT_OFFER_TYPE,
        on_dealer_ready: Callable[[dict[str, Any]], None] | None = None,
        on_dealers_enumerated: Callable[[int], None] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Stage B: read the workbook, scrape every URL for ``offer_type`` in
        parallel, and return one scraped-data payload per dealer (id, name,
        per-URL bodies).

        If ``on_dealer_ready`` is given, it is called with a dealer's payload the
        moment that dealer's URLs finish scraping, so extraction (stage C) can start
        while the remaining dealers are still being scraped.

        ``on_dealers_enumerated`` is called with the total dealer count as soon as
        the workbook is parsed (before scraping), so callers can track run progress
        without waiting for all scraping to finish.
        """
        resolved = normalize_offer_type(offer_type)
        target_label = excel_label(resolved)
        log_prefix = f"[{resolved.value}]"
        excel_path = Path(excel_path)
        df = pd.read_excel(excel_path)

        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise FileStorageError(
                f"Input workbook is missing required columns: {sorted(missing)}"
            )

        if "status" in df.columns:
            active_mask = df["status"].map(_is_active_status)
            disabled_count = int((~active_mask).sum())
            if disabled_count:
                logger.info(
                    "%s Skipping disabled dealers (status != True) | skipped_rows=%d",
                    log_prefix,
                    disabled_count,
                )
            df = df[active_mask]

        type_series = df["type"].astype(str)
        matching = df[type_series.str.strip().str.casefold() == target_label.casefold()]

        skipped = type_series[
            type_series.str.strip().str.casefold() != target_label.casefold()
        ]
        ignored_rows = skipped[
            skipped.str.strip().str.casefold().isin(IGNORED_EXCEL_TYPES)
        ]
        if len(skipped) > 0:
            logger.info(
                "%s Skipping non-matching rows | skipped_rows=%d | ignored_rows=%d",
                log_prefix,
                len(skipped),
                len(ignored_rows),
            )
        logger.info(
            "%s Filtered rows | source_file=%s | total_rows=%d | matching_rows=%d",
            log_prefix,
            str(excel_path),
            len(df),
            len(matching),
        )

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        date_token = self._date_token()

        rows: list[tuple[int, str, str, str, str, str]] = []
        dealer_order: list[tuple[str, str]] = []
        remaining: dict[tuple[str, str], int] = {}
        for index, row in enumerate(matching.itertuples(index=False)):
            dealer_id = str(row.id)
            dealer_name = str(row.DealerName)
            key = (dealer_id, dealer_name)
            if key not in remaining:
                dealer_order.append(key)
                remaining[key] = 0
            remaining[key] += 1
            rows.append(
                (index, dealer_id, dealer_name, str(row.oem), str(row.type), str(row.url))
            )

        if on_dealers_enumerated is not None:
            on_dealers_enumerated(len(dealer_order))

        scraped: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
        ready: dict[tuple[str, str], dict[str, Any]] = {}

        def _dealer_payload(dealer_id: str, dealer_name: str) -> dict[str, Any]:
            entries = [
                entry
                for _, entry in sorted(
                    scraped.get((dealer_id, dealer_name), []), key=lambda t: t[0]
                )
            ]
            return {
                "dealer_id": dealer_id,
                "dealer_name": dealer_name,
                "date_token": date_token,
                "offer_type": resolved.value,
                "urls": entries,
            }

        if rows:
            url_to_rows: dict[str, list[tuple[int, str, str, str, str, str]]] = {}
            for row in rows:
                url_to_rows.setdefault(row[5], []).append(row)

            workers = max(1, min(settings.scraper_max_workers, len(url_to_rows)))
            logger.info(
                "Scraping Sales Specials URLs concurrently | url_count=%d | unique_urls=%d | max_workers=%d",
                len(rows),
                len(url_to_rows),
                workers,
            )

            def _finish_url(url: str, body: str | None, scrape_error: str | None) -> None:
                for index, d_id, d_name, oem, type_, _ in url_to_rows[url]:
                    entry = {
                        "oem": oem,
                        "type": type_,
                        "url": url,
                        "body": body,
                        "scrape_error": scrape_error,
                    }
                    key = (d_id, d_name)
                    scraped.setdefault(key, []).append((index, entry))
                    remaining[key] -= 1
                    if remaining[key] == 0:
                        payload = _dealer_payload(*key)
                        ready[key] = payload
                        if on_dealer_ready is not None:
                            logger.info(
                                "Dealer scraped; dispatching to extract | dealer_id=%s | url_count=%d",
                                key[0],
                                len(payload["urls"]),
                            )
                            on_dealer_ready(payload)

            with ContextThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(self._scrape_url, url) for url in url_to_rows
                ]
                for future in as_completed(futures):
                    url, body, scrape_error = future.result()
                    _finish_url(url, body, scrape_error)

        payloads = [
            ready.get(key) or _dealer_payload(*key) for key in dealer_order
        ]
        return str(excel_path), payloads

    def _scrape_url(self, url: str) -> tuple[str, str | None, str | None]:
        logger.info("Scraping URL | url=%s", url)
        try:
            return url, self.workflow.scrape(url), None
        except Exception as exc:
            logger.error("Scrape failed for URL | url=%s | error=%s", url, str(exc))
            return url, None, str(exc)

    def build_dealer(self, payload: dict[str, Any]) -> DealerZipResult:
        """Stage C: extract offers for one dealer, running the LLM sequentially over
        that dealer's already-scraped URLs, then write the dealer zip + error file.

        OEMs that share the same URL are extracted only once and reuse the same
        offers, so identical URLs always yield an identical, complete offer set."""
        dealer_id = payload["dealer_id"]
        dealer_name = payload["dealer_name"]
        date_token = payload["date_token"]
        offer_type = normalize_offer_type(payload.get("offer_type"))
        logger.info(
            "[%s] Extracting dealer offers | dealer_id=%s | url_count=%d",
            offer_type.value,
            dealer_id,
            len(payload["urls"]),
        )
        cache: dict[str, tuple[list[Any], int] | Exception] = {}
        results = [
            self._extract_one(index, dealer_id, dealer_name, entry, date_token, cache)
            for index, entry in enumerate(payload["urls"])
        ]
        return self._assemble_dealer(
            dealer_id, dealer_name, results, date_token, offer_type
        )

    def _extract_one(
        self,
        order: int,
        dealer_id: str,
        dealer_name: str,
        entry: dict[str, Any],
        date_token: str,
        cache: dict[str, Any],
    ) -> _UrlResult:
        oem = entry["oem"]
        url = entry["url"]
        file_stem = f"{dealer_id}_{oem}_{date_token}"

        scrape_error = entry.get("scrape_error")
        if scrape_error:
            return _UrlResult(
                order=order,
                dealer_id=dealer_id,
                dealer_name=dealer_name,
                oem=oem,
                url=url,
                count=0,
                error_message=f"URL: {url}\nOEM: {oem}\nError: {scrape_error}",
            )

        cached = cache.get(url)
        if cached is None:
            try:
                cached = self.workflow.incentives_from_body(entry["body"])
            except Exception as exc:
                logger.error(
                    "Offer extraction failed for URL | dealer_id=%s | oem=%s | url=%s | error=%s",
                    dealer_id,
                    oem,
                    url,
                    str(exc),
                )
                cached = exc
            cache[url] = cached

        if isinstance(cached, Exception):
            return _UrlResult(
                order=order,
                dealer_id=dealer_id,
                dealer_name=dealer_name,
                oem=oem,
                url=url,
                count=0,
                error_message=f"URL: {url}\nOEM: {oem}\nError: {cached}",
            )

        incentives, count = cached
        if count > 0:
            file_name, file_bytes = self.excel_service.build_workbook_bytes(
                dealer_name=dealer_name,
                incentives=incentives,
                source_url=url,
                file_stem=file_stem,
            )
            return _UrlResult(
                order=order,
                dealer_id=dealer_id,
                dealer_name=dealer_name,
                oem=oem,
                url=url,
                count=count,
                file_name=file_name,
                file_bytes=file_bytes,
            )

        logger.warning(
            "No offers extracted for URL | dealer_id=%s | oem=%s | url=%s",
            dealer_id,
            oem,
            url,
        )
        return _UrlResult(
            order=order,
            dealer_id=dealer_id,
            dealer_name=dealer_name,
            oem=oem,
            url=url,
            count=0,
            error_message=(
                f"URL: {url}\nOEM: {oem}\n"
                "Error: No offers were extracted from this page."
            ),
        )

    def _assemble_dealer(
        self,
        dealer_id: str,
        dealer_name: str,
        results: list[_UrlResult],
        date_token: str,
        offer_type: OfferType = DEFAULT_OFFER_TYPE,
    ) -> DealerZipResult:
        workbooks: list[tuple[str, bytes]] = []
        offer_counts: dict[str, int] = {}
        error_sections: list[str] = []
        errors: dict[str, str] = {}

        for res in results:
            offer_counts[res.oem] = res.count
            if res.count > 0 and res.file_name and res.file_bytes is not None:
                workbooks.append((res.file_name, res.file_bytes))
            if res.error_message:
                error_sections.append(res.error_message)
                errors[res.oem] = res.error_message.splitlines()[-1].removeprefix(
                    "Error: "
                )

        result = DealerZipResult(
            dealer_id=dealer_id,
            dealer_name=dealer_name,
            excel_files=[name for name, _ in workbooks],
            offer_counts=offer_counts,
            errors=errors,
        )

        zip_dir = get_zip_directory(offer_type)
        error_dir = get_error_directory(offer_type)

        if workbooks:
            zip_name, zip_path = self._write_zip(
                zip_dir,
                f"{self._slugify(dealer_id)}_{self._slugify(dealer_name)}_{date_token}.zip",
                workbooks,
            )
            result.zip_name = zip_name
            result.zip_path = zip_path
            logger.info(
                "[%s] Dealer zip created | dealer_id=%s | zip_name=%s | excel_count=%d",
                offer_type.value,
                dealer_id,
                zip_name,
                len(workbooks),
            )

        if error_sections:
            error_file_name, error_file_path = self._write_text(
                error_dir,
                f"error_{self._slugify(dealer_id)}_{self._slugify(dealer_name)}_{date_token}.txt",
                ("\n\n" + "-" * 60 + "\n\n").join(error_sections),
            )
            result.error_file_name = error_file_name
            result.error_file_path = error_file_path
            logger.info(
                "[%s] Dealer error file created | dealer_id=%s | error_file_name=%s | error_count=%d",
                offer_type.value,
                dealer_id,
                error_file_name,
                len(error_sections),
            )

        return result

    def _write_zip(
        self,
        directory: Path,
        zip_name: str,
        files: list[tuple[str, bytes]],
    ) -> tuple[str, str]:
        directory.mkdir(parents=True, exist_ok=True)
        zip_path = directory / zip_name
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for file_name, file_bytes in files:
                    archive.writestr(file_name, file_bytes)
        except OSError as exc:
            raise FileStorageError(
                f"Failed to create zip {zip_name}: {exc}"
            ) from exc
        return zip_name, str(zip_path)

    def _write_text(
        self, directory: Path, file_name: str, text: str
    ) -> tuple[str, str]:
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / file_name
        try:
            file_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise FileStorageError(
                f"Failed to write file {file_name}: {exc}"
            ) from exc
        return file_name, str(file_path)
