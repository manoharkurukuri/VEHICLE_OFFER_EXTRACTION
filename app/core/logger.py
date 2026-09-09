import logging
import sys

from app.core.correlation import get_correlation_id

_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(correlation_id)s | %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


class _CorrelationIdFilter(logging.Filter):
    """Inject the current request's correlation id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        return True


def _configure() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.addFilter(_CorrelationIdFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger that prints to stdout."""
    _configure()
    return logging.getLogger(name)
