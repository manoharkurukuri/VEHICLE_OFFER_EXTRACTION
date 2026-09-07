class AppException(Exception):
    status_code = 500
    code = "application_error"

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class CompanyNotFoundError(AppException):
    status_code = 404
    code = "company_not_found"


class ScrapingError(AppException):
    status_code = 502
    code = "scraping_failed"


class LLMExtractionError(AppException):
    status_code = 502
    code = "llm_extraction_failed"


class ExcelGenerationError(AppException):
    status_code = 500
    code = "excel_generation_failed"


class FileStorageError(AppException):
    status_code = 502
    code = "file_storage_failed"


class ConfigurationError(AppException):
    status_code = 500
    code = "configuration_error"


class UnsupportedOfferTypeError(AppException):
    status_code = 400
    code = "unsupported_offer_type"


class ServiceNotActiveError(AppException):
    status_code = 403
    code = "service_not_active"

    def __init__(self, offer_type: str) -> None:
        self.offer_type = offer_type
        super().__init__(f"The '{offer_type}' service is not active.")


class OfferRunInProgressError(AppException):
    status_code = 409
    code = "offer_run_in_progress"

    def __init__(self, running_offer_type: str) -> None:
        self.running_offer_type = running_offer_type
        super().__init__(
            f"An offer-generation run for '{running_offer_type}' is currently "
            f"running. Please wait until it completes before starting another."
        )
