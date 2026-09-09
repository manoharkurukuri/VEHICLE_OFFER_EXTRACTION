import uuid
from contextvars import ContextVar

CORRELATION_ID_HEADER = "X-Correlation-ID"

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def generate_correlation_id() -> str:
    """Return a new request correlation id prefixed with ``voe_``."""
    return f"voe_{uuid.uuid4().hex}"


def set_correlation_id(correlation_id: str) -> None:
    _correlation_id.set(correlation_id)


def get_correlation_id() -> str:
    return _correlation_id.get()
