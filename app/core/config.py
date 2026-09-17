from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Vehicle Offer Extraction API"
    app_env: str = "local"
    api_v1_prefix: str = "/api/v1"
    local_storage_dir: str = "./storage/offers"
    app_timezone: str = "Asia/Kolkata"

    llm_provider: str = "gemini"

    gemini_api_key: SecretStr = SecretStr("")
    gemini_api_keys: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_timeout_seconds: int = 120
    gemini_max_retries: int = 2
    gemini_max_tokens: int = 32000

    max_body_chars: int = 350_000
    scraper_max_workers: int = 5
    dealer_extract_workers: int = 5

    default_excel_path: str = "offers/dealer.xlsx"

    sales_specials: bool = True
    service_specials: bool = False
    new_inventory: bool = False
    certified_inventory: bool = False
    used_inventory: bool = False
    offer_to_purchase: bool = False
    schedule_service: bool = False


    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def resolved_api_keys(self) -> list[str]:
        """All Gemini API keys, preferring the multi-key list, else the single key."""
        raw = self.gemini_api_keys.get_secret_value().strip()
        if raw:
            keys = [key.strip() for key in raw.split(",") if key.strip()]
            if keys:
                return keys
        single = self.gemini_api_key.get_secret_value().strip()
        return [single] if single else []

    def is_service_active(self, offer_type_value: str) -> bool:
        """Whether a given offer type's service is enabled via env flags."""
        return bool(getattr(self, offer_type_value, False))

@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()