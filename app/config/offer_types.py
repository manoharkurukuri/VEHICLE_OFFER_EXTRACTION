"""Central offer-type definitions and normalization.

All supported offer types live here as a single source of truth so raw strings
are never scattered across the codebase. Excel workbooks label rows with the
human-readable names (e.g. "Sales Specials"); internally we use the snake_case
values of :class:`OfferType`.
"""

from __future__ import annotations

from enum import Enum

from app.core.exceptions import UnsupportedOfferTypeError


class OfferType(str, Enum):
    """Internal, snake_case representation of every supported offer type."""

    SALES_SPECIALS = "sales_specials"
    SERVICE_SPECIALS = "service_specials"
    SCHEDULE_SERVICE = "schedule_service"
    NEW_INVENTORY = "new_inventory"
    CERTIFIED_INVENTORY = "certified_inventory"
    USED_INVENTORY = "used_inventory"
    OFFER_TO_PURCHASE = "offer_to_purchase"


DEFAULT_OFFER_TYPE = OfferType.SALES_SPECIALS

SUPPORTED_OFFER_TYPES: tuple[OfferType, ...] = tuple(OfferType)

EXCEL_TYPE_LABELS: dict[OfferType, str] = {
    OfferType.SALES_SPECIALS: "Sales Specials",
    OfferType.SERVICE_SPECIALS: "Service Specials",
    OfferType.SCHEDULE_SERVICE: "Schedule Service",
    OfferType.NEW_INVENTORY: "New Inventory",
    OfferType.CERTIFIED_INVENTORY: "Certified Inventory",
    OfferType.USED_INVENTORY: "Used Inventory",
    OfferType.OFFER_TO_PURCHASE: "Offer To Purchase",
}

IGNORED_EXCEL_TYPES: frozenset[str] = frozenset(
    {"homepage", "contact us", "map"}
)

_ALIASES: dict[str, OfferType] = {}
for _offer_type in OfferType:
    _ALIASES[_offer_type.value] = _offer_type
    _ALIASES[_offer_type.value.replace("_", " ")] = _offer_type
for _offer_type, _label in EXCEL_TYPE_LABELS.items():
    _ALIASES[_label.casefold()] = _offer_type


def supported_values() -> list[str]:
    """The internal values of every supported type, e.g. ``["sales_specials", ...]``."""
    return [offer_type.value for offer_type in SUPPORTED_OFFER_TYPES]


def normalize_offer_type(value: str | OfferType | None) -> OfferType:
    """Normalize an arbitrary type string to an :class:`OfferType`.

    ``None``/empty falls back to :data:`DEFAULT_OFFER_TYPE`. Accepts internal
    values ("sales_specials"), Excel labels ("Sales Specials"), and spacing
    variants, case-insensitively. Raises :class:`UnsupportedOfferTypeError`
    (with the allowed values) for anything unrecognized.
    """
    if value is None:
        return DEFAULT_OFFER_TYPE
    if isinstance(value, OfferType):
        return value

    text = str(value).strip()
    if not text:
        return DEFAULT_OFFER_TYPE

    normalized = _ALIASES.get(text.casefold())
    if normalized is None:
        raise UnsupportedOfferTypeError(
            f"Unsupported offer type: {value}. "
            f"Supported types are: {', '.join(supported_values())}."
        )
    return normalized


def excel_label(offer_type: OfferType) -> str:
    """The Excel ``type`` column label for an internal type."""
    return EXCEL_TYPE_LABELS[offer_type]
