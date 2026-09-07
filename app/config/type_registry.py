"""Central offer-type registry.

Maps each supported offer type to its prompt, response schema, processor, and
output subdirectory. This is the single place to wire a type end to end: adding a
new type means adding its prompt/template/processor and one entry here — no
scattered ``if/elif`` blocks elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.config.offer_types import (
    EXCEL_TYPE_LABELS,
    OfferType,
    normalize_offer_type,
)

if TYPE_CHECKING:
    from app.processors.base import BaseProcessor


@dataclass(frozen=True)
class TypeConfig:
    offer_type: OfferType
    prompt: str
    response_schema: type[BaseModel]
    processor_class: type["BaseProcessor"]
    excel_label: str
    output_subdir: str


def _build_registry() -> dict[OfferType, TypeConfig]:
    from app.processors.certified_inventory_processor import CertifiedInventoryProcessor
    from app.processors.new_inventory_processor import NewInventoryProcessor
    from app.processors.offer_to_purchase_processor import OfferToPurchaseProcessor
    from app.processors.sales_specials_processor import SalesSpecialsProcessor
    from app.processors.schedule_service_processor import ScheduleServiceProcessor
    from app.processors.service_specials_processor import ServiceSpecialsProcessor
    from app.processors.used_inventory_processor import UsedInventoryProcessor

    processors: dict[OfferType, type[BaseProcessor]] = {
        OfferType.SALES_SPECIALS: SalesSpecialsProcessor,
        OfferType.SERVICE_SPECIALS: ServiceSpecialsProcessor,
        OfferType.SCHEDULE_SERVICE: ScheduleServiceProcessor,
        OfferType.NEW_INVENTORY: NewInventoryProcessor,
        OfferType.CERTIFIED_INVENTORY: CertifiedInventoryProcessor,
        OfferType.USED_INVENTORY: UsedInventoryProcessor,
        OfferType.OFFER_TO_PURCHASE: OfferToPurchaseProcessor,
    }

    registry: dict[OfferType, TypeConfig] = {}
    for offer_type, processor_class in processors.items():
        registry[offer_type] = TypeConfig(
            offer_type=offer_type,
            prompt=processor_class.prompt,
            response_schema=processor_class.response_schema,
            processor_class=processor_class,
            excel_label=EXCEL_TYPE_LABELS[offer_type],
            output_subdir=offer_type.value,
        )
    return registry


@lru_cache
def _registry() -> dict[OfferType, TypeConfig]:
    return _build_registry()


def get_type_config(offer_type: str | OfferType) -> TypeConfig:
    return _registry()[normalize_offer_type(offer_type)]


def get_prompt(offer_type: str | OfferType) -> str:
    return get_type_config(offer_type).prompt


def get_response_schema(offer_type: str | OfferType) -> type[BaseModel]:
    return get_type_config(offer_type).response_schema


@lru_cache
def _shared_service():
    from app.service.offer_generation_service import OfferGenerationService

    return OfferGenerationService()


@lru_cache
def _processor_cache(offer_type: OfferType) -> "BaseProcessor":
    return get_type_config(offer_type).processor_class(service=_shared_service())


def get_processor(offer_type: str | OfferType) -> "BaseProcessor":
    return _processor_cache(normalize_offer_type(offer_type))
