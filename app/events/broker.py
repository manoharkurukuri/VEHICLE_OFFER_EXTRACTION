import queue
import threading
from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.core.correlation import get_correlation_id, set_correlation_id
from app.core.logger import get_logger
from app.core.run_context import (
    RunContext,
    peek_run_context,
    set_run_context,
)

Event = dict[str, Any]
Handler = Callable[[Event], None]

logger = get_logger(__name__)

_STOP = object()
_CORRELATION_KEY = "__correlation_id__"
_RUN_CONTEXT_KEY = "__run_context__"


class InMemoryBroker:
    """A tiny in-process publish/subscribe broker.

    The publisher drops an event on the queue and returns immediately. One or more
    background worker threads deliver each event to the registered subscriber, so
    slow work (scraping + LLM) runs off the request thread. Playwright's sync API
    needs a thread with no running asyncio loop, which these dedicated worker
    threads provide. Use ``workers > 1`` to process events concurrently.
    """

    def __init__(self, name: str = "broker", workers: int = 1) -> None:
        self.name = name
        self._workers = max(1, workers)
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._handler: Handler | None = None
        self._threads: list[threading.Thread] = []

    def subscribe(self, handler: Handler) -> None:
        self._handler = handler

    def publish(self, event: Event) -> None:
        event.setdefault(_CORRELATION_KEY, get_correlation_id())
        run_ctx = peek_run_context()
        if run_ctx is not None:
            event.setdefault(_RUN_CONTEXT_KEY, run_ctx.to_dict())
        self._queue.put(event)
        logger.info("Event published | broker=%s", self.name)

    def start(self) -> None:
        if self._threads and any(t.is_alive() for t in self._threads):
            return
        self._threads = []
        for i in range(self._workers):
            thread = threading.Thread(
                target=self._run, name=f"{self.name}-worker-{i}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        for _ in self._threads:
            self._queue.put(_STOP)
        for thread in self._threads:
            thread.join(timeout=5)

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            try:
                if event is _STOP:
                    return
                if self._handler is None:
                    logger.warning(
                        "No subscriber registered; event dropped | broker=%s", self.name
                    )
                    continue
                set_correlation_id(event.get(_CORRELATION_KEY, "-"))
                run_data = event.get(_RUN_CONTEXT_KEY)
                set_run_context(
                    RunContext.from_dict(run_data) if run_data else None
                )
                self._handler(event)
            except Exception as exc:
                logger.error(
                    "Subscriber failed to process event | broker=%s | error=%s",
                    self.name,
                    str(exc),
                )
            finally:
                self._queue.task_done()


scrape_broker = InMemoryBroker(name="scrape", workers=1)

extract_broker = InMemoryBroker(
    name="extract", workers=settings.dealer_extract_workers
)

