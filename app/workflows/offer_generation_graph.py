from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from app.core.logger import get_logger
from app.schemas.llm import OfferExtractionResponse, VehicleIncentiveLLM
from app.service.excel_service import ExcelService
from app.service.llm_extractor import LLMOfferExtractor
from app.service.scraper import get_website_content_from_url

logger = get_logger(__name__)


class OfferWorkflowState(TypedDict, total=False):
    dealer_id: str
    dealer_name: str
    oem: str
    url: str
    file_stem: str
    body: str
    body_char_count: int
    extraction: OfferExtractionResponse
    incentives: list[VehicleIncentiveLLM]
    file_name: str
    file_bytes: bytes
    incentive_count: int


class OfferGenerationWorkflow:
    def __init__(self, excel_service: ExcelService | None = None) -> None:
        self.llm_extractor = LLMOfferExtractor()
        self.excel_service = excel_service or ExcelService()
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(OfferWorkflowState)
        builder.add_node("scrape_website", self._scrape_website)
        builder.add_node("extract_with_llm", self._extract_with_llm)
        builder.add_node("normalize_offers", self._normalize_offers)
        builder.add_node("create_excel", self._create_excel)

        builder.add_edge(START, "scrape_website")
        builder.add_edge("scrape_website", "extract_with_llm")
        builder.add_edge("extract_with_llm", "normalize_offers")
        builder.add_edge("normalize_offers", "create_excel")
        builder.add_edge("create_excel", END)
        return builder.compile()

    @staticmethod
    def _scrape_website(state: OfferWorkflowState) -> dict[str, Any]:
        content = get_website_content_from_url(state["url"])
        body = content["body"]
        logger.info(
            "Extracted body from HTML content | url=%s | body_char_count=%d",
            state["url"],
            len(body),
        )
        return {
            "body": body,
            "body_char_count": len(body),
        }

    def _extract_with_llm(self, state: OfferWorkflowState) -> dict[str, Any]:
        logger.info(
            "Sending body to LLM for offer extraction | body_char_count=%d",
            len(state["body"]),
        )
        try:
            extraction = self.llm_extractor.extract(state["body"])
        except Exception as exc:
            logger.error("LLM failed to generate offer response | error=%s", str(exc))
            raise
        logger.info(
            "LLM successfully generated offer response | offer_count=%d",
            len(extraction.offers),
        )
        return {"extraction": extraction}

    @staticmethod
    def _offer_keys(incentive: VehicleIncentiveLLM) -> list[tuple]:
        """Identity keys used to detect the same offer repeated by the page/LLM.

        Dealer pages echo one offer across a hero banner, an offer card, a
        "See details" modal, and its disclaimer, so the LLM can return the same
        offer several times. The copies do not always agree on identifiers: the
        hero card may carry no VIN while the disclaimer does. We therefore return
        every key that can identify the offer (each VIN, the stock number, and the
        vehicle + headline-terms signature) and treat two offers as the same when
        they share ANY key, so a VIN-bearing copy still collapses onto a VIN-less
        copy of the same vehicle and terms.
        """

        def norm(value: Any) -> str | None:
            if value is None:
                return None
            text = str(value).strip().casefold()
            return text or None

        keys: list[tuple] = []
        vin = norm(incentive.vin_number)
        if vin:
            keys.extend(("vin", part.strip()) for part in vin.split(",") if part.strip())
        stock = norm(incentive.stock_number)
        if stock:
            keys.append(("stock", stock))
        keys.append(
            (
                "vehicle",
                incentive.year,
                norm(incentive.make),
                norm(incentive.model),
                norm(incentive.trim),
                incentive.lowest_monthly_payment,
                incentive.lease_term_months,
                incentive.total_due_at_signing,
                incentive.finance_rate,
            )
        )
        return keys

    @staticmethod
    def _normalize_offers(state: OfferWorkflowState) -> dict[str, Any]:
        seen: set[tuple] = set()
        deduped: list[VehicleIncentiveLLM] = []
        for incentive in state["extraction"].offers:
            keys = OfferGenerationWorkflow._offer_keys(incentive)
            if any(key in seen for key in keys):
                continue
            seen.update(keys)
            deduped.append(incentive)

        duplicate_count = len(state["extraction"].offers) - len(deduped)

        normalized: list[VehicleIncentiveLLM] = []
        for index, incentive in enumerate(deduped, start=1):
            normalized.append(
                incentive.model_copy(
                    update={
                        "offer_priority": f"Vehicle #{index}",
                        "offer_emphasis": None,
                    }
                )
            )

        logger.info(
            "Offers normalized | offer_count=%d | duplicates_removed=%d",
            len(normalized),
            duplicate_count,
        )
        return {
            "incentives": normalized,
            "incentive_count": len(normalized),
        }

    def _create_excel(self, state: OfferWorkflowState) -> dict[str, Any]:
        file_name, file_bytes = self.excel_service.build_workbook_bytes(
            dealer_name=state["dealer_name"],
            incentives=state["incentives"],
            source_url=state["url"],
            file_stem=state.get("file_stem"),
        )
        return {
            "file_name": file_name,
            "file_bytes": file_bytes,
        }

    def invoke(self, initial_state: OfferWorkflowState) -> OfferWorkflowState:
        return self.graph.invoke(initial_state)

    @staticmethod
    def scrape(url: str) -> str:
        """Stage B helper: fetch a URL and return its extracted body text."""
        return get_website_content_from_url(url)["body"]

    def incentives_from_body(
        self, body: str
    ) -> tuple[list[VehicleIncentiveLLM], int]:
        """Extract + dedupe offers from an already-scraped body, returning the
        normalized incentives and their count. Excel building is separate so one
        extraction can be reused across OEMs that share the same URL."""
        state: OfferWorkflowState = {"body": body}
        state.update(self._extract_with_llm(state))
        state.update(self._normalize_offers(state))
        return state["incentives"], state.get("incentive_count", 0)

    def build_from_body(
        self,
        *,
        dealer_name: str,
        oem: str,
        url: str,
        body: str,
        file_stem: str,
    ) -> dict[str, Any]:
        """Stage C helper: run LLM extraction, dedupe, and build the Excel from an
        already-scraped body (no scraping)."""
        state: OfferWorkflowState = {
            "dealer_name": dealer_name,
            "oem": oem,
            "url": url,
            "body": body,
            "file_stem": file_stem,
        }
        state.update(self._extract_with_llm(state))
        state.update(self._normalize_offers(state))
        state.update(self._create_excel(state))
        return {
            "incentive_count": state.get("incentive_count", 0),
            "file_name": state.get("file_name"),
            "file_bytes": state.get("file_bytes"),
        }
