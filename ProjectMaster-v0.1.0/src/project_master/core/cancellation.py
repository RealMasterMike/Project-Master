from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable


class CancellationToken:
    """Thread-safe cooperative cancellation with one bound blocking-operation closer."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._closer: Callable[[], None] | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            closer = self._closer
        if closer is not None:
            try:
                closer()
            except Exception:
                # Cancellation is best-effort and must remain idempotent.
                pass

    def bind_closer(self, closer: Callable[[], None]) -> None:
        with self._lock:
            if not self._event.is_set():
                self._closer = closer
                return
        try:
            closer()
        except Exception:
            pass

    def unbind_closer(self, closer: Callable[[], None]) -> None:
        with self._lock:
            if self._closer is closer:
                self._closer = None


class StreamCancellationRegistry:
    """Own active stream tokens and safely handle cancel-before-start races."""

    def __init__(self, pending_ttl_seconds: float = 30.0, max_pending: int = 256) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, CancellationToken] = {}
        self._pending: OrderedDict[str, float] = OrderedDict()
        self._pending_ttl_seconds = pending_ttl_seconds
        self._max_pending = max_pending

    def register(self, request_id: str) -> CancellationToken:
        with self._lock:
            self._purge_pending()
            if request_id in self._active:
                raise ValueError("A stream with this request ID is already active")
            token = CancellationToken()
            was_cancelled = self._pending.pop(request_id, None) is not None
            self._active[request_id] = token
        if was_cancelled:
            token.cancel()
        return token

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            self._purge_pending()
            token = self._active.get(request_id)
            if token is None:
                self._pending[request_id] = time.monotonic()
                self._pending.move_to_end(request_id)
                while len(self._pending) > self._max_pending:
                    self._pending.popitem(last=False)
        if token is not None:
            token.cancel()
            return True
        return False

    def finish(self, request_id: str, token: CancellationToken) -> None:
        with self._lock:
            if self._active.get(request_id) is token:
                del self._active[request_id]

    def _purge_pending(self) -> None:
        cutoff = time.monotonic() - self._pending_ttl_seconds
        while self._pending:
            _request_id, created_at = next(iter(self._pending.items()))
            if created_at >= cutoff:
                return
            self._pending.popitem(last=False)
