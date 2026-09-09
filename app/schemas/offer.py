from pydantic import BaseModel


class DealerZipResult(BaseModel):
    dealer_id: str
    dealer_name: str
    zip_name: str | None = None
    zip_path: str | None = None
    excel_files: list[str] = []
    offer_counts: dict[str, int] = {}
    error_file_name: str | None = None
    error_file_path: str | None = None
    errors: dict[str, str] = {}
    no_offer_count: int = 0
    scrape_error_count: int = 0


class GenerateOffersResult(BaseModel):
    source_file: str
    dealers: list[DealerZipResult]
