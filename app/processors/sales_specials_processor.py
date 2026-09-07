"""Sales Specials processor — the real, production extraction flow.

Delegates to :class:`OfferGenerationService` unchanged so Sales Specials behavior
stays identical. Only the output directory is type-scoped (handled by the service
via the ``offer_type`` carried in each payload).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.offer_types import OfferType
from app.prompts.sales_specials import SYSTEM_PROMPT
from app.processors.base import BaseProcessor
from app.response_templates.sales_specials import RESPONSE_SCHEMA
from app.schemas.offer import DealerZipResult


class SalesSpecialsProcessor(BaseProcessor):
    offer_type = OfferType.SALES_SPECIALS
    prompt = SYSTEM_PROMPT
    response_schema = RESPONSE_SCHEMA

    def build_dealer(self, payload: dict[str, Any]) -> DealerZipResult:
        payload.setdefault("offer_type", self.offer_type.value)
        return self.service.build_dealer(payload)
