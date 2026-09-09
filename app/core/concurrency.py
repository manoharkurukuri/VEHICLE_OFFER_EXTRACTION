import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that runs each task inside a copy of the submitting
    thread's context, so ``ContextVar`` values (e.g. the request correlation id)
    propagate into worker threads."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any):
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, fn, *args, **kwargs)
