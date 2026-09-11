from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.correlation import get_correlation_id
from app.core.exceptions import AppException
from app.core.logger import get_logger

logger = get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def handle_app_exception(
        request: Request, exc: AppException
    ) -> JSONResponse:
        logger.error(
            "Application error | error_code=%s | error_message=%s | path=%s",
            exc.code,
            exc.message,
            request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "correlation_id": get_correlation_id(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            "Unhandled application exception | path=%s | error=%s",
            request.url.path,
            str(exc),
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_server_error",
                    "message": "An unexpected error occurred.",
                }
            },
        )
