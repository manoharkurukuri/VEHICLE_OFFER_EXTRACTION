"""Sales Specials response schema.

Re-exports the existing, production schema unchanged so Sales Specials behavior
stays identical. The structured LLM output for Sales Specials is
:class:`OfferExtractionResponse`.
"""

from app.schemas.llm import OfferExtractionResponse, VehicleIncentiveLLM

RESPONSE_SCHEMA = OfferExtractionResponse

__all__ = ["RESPONSE_SCHEMA", "OfferExtractionResponse", "VehicleIncentiveLLM"]
