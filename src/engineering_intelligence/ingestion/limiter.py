"""Shared provider request concurrency limiter."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore


class RequestLimiter:
    """Bound concurrent provider requests across all source workers."""

    def __init__(self, concurrency: int) -> None:
        if concurrency < 1:
            raise ValueError("Request concurrency must be at least 1")
        self.concurrency = concurrency
        self._semaphore = BoundedSemaphore(concurrency)

    @contextmanager
    def slot(self) -> Iterator[None]:
        self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()
