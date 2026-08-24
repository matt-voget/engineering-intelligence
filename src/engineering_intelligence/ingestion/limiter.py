"""Shared provider request concurrency limiter."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock


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


class StripedLock:
    """Bounded lock set that serializes writes for the same record key."""

    def __init__(self, stripes: int = 256) -> None:
        if stripes < 1:
            raise ValueError("Lock stripes must be at least 1")
        self._locks = [Lock() for _ in range(stripes)]

    @contextmanager
    def slot(self, key: str) -> Iterator[None]:
        lock = self._locks[hash(key) % len(self._locks)]
        with lock:
            yield
