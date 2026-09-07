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
    # Optional comma-separated keys; each concurrent LLM call uses a distinct key
    # to spread load past a single key's rate limit. Falls back to gemini_api_key.
    gemini_api_keys: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_timeout_seconds: int = 120
    gemini_max_retries: int = 2
    # Cap completion size so long multi-offer outputs don't get truncated
    # mid-JSON. Offer-rich dealer pages can exceed 8k tokens of structured output,
    # which fails the parse and loses every offer, so keep this generous.
    gemini_max_tokens: int = 32000

    max_body_chars: int = 350_000
    # Number of dealer URLs scraped/extracted in parallel. Each worker runs its
    # own headless browser + LLM call, so raise it only as far as CPU/RAM and the
    # LLM provider's rate limits allow.
    scraper_max_workers: int = 5
    # Number of dealers whose offers are extracted (stage C) in parallel. Each
    # dealer's URLs go to the LLM sequentially, so keep this near the API-key
    # count so concurrent dealers each get their own key.
    dealer_extract_workers: int = 5

    # Default input workbook used when a caller omits the path (CLI/scheduler).
    default_excel_path: str = "offers/status_ex.xlxs"

    # --- Scheduler (APScheduler, in-process) -------------------------------
    # Off by default; enable to run per-type cron jobs inside the API process.
    scheduler_enabled: bool = False
    # Per-type cron expressions (minute hour day month day_of_week). Each type
    # runs once a month on the 5th, staggered to a different hour so they don't
    # all fire at once. Override any of them via env, e.g.
    # SCHEDULE_SALES_SPECIALS="30 1 5 * *".
    schedule_sales_specials: str = "0 1 5 * *"
    schedule_service_specials: str = "0 2 5 * *"
    schedule_schedule_service: str = "0 3 5 * *"
    schedule_new_inventory: str = "0 4 5 * *"
    schedule_certified_inventory: str = "0 5 5 * *"
    schedule_used_inventory: str = "0 6 5 * *"
    schedule_offer_to_purchase: str = "0 7 5 * *"

    # --- Service activation ------------------------------------------------
    # Per-type on/off switches. A request for an inactive service is rejected.
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