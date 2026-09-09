from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse
from app.core.exceptions import AppException
from app.core.config import settings  
from app.core.logger import get_logger
from app.core.correlation import (
    CORRELATION_ID_HEADER,
    generate_correlation_id,
    set_correlation_id,
)
from app.core.exception_handlers import register_exception_handlers
from app.api.offers import router as offers_router
from app.events.broker import extract_broker, scrape_broker
from app.events.subscriber import handle_extract_event, handle_scrape_event
from dotenv import load_dotenv

load_dotenv()

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scrape_broker.subscribe(handle_scrape_event)
    extract_broker.subscribe(handle_extract_event)
    scrape_broker.start()
    extract_broker.start()
    yield
    scrape_broker.stop()
    extract_broker.stop()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.include_router(offers_router)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    correlation_id = request.headers.get(CORRELATION_ID_HEADER) or generate_correlation_id()
    set_correlation_id(correlation_id)
    logger.info("Incoming request | method=%s | path=%s", request.method, request.url.path)
    response = await call_next(request)
    response.headers[CORRELATION_ID_HEADER] = correlation_id
    return response

@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


