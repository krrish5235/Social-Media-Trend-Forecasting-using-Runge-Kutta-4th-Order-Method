"""Orchestration: provider + cache + snapshot store + report builder."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Any, Callable, Deque, Dict, Optional, Tuple

from ..providers.base import Bundle, Provider
from .report import build_report
from .store import SnapshotStore


class TTLCache:
    """Small thread-safe cache with expiry and a size cap."""

    def __init__(self, ttl: float, max_items: int = 256, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl, self.max_items, self._clock = ttl, max_items, clock
        self._data: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < self._clock():
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (self._clock() + self.ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)


class RateLimiter:
    """Sliding-window limiter keyed by client (in-memory, per process)."""

    def __init__(self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window, self._clock = limit, window_s, clock
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = self._clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 5000:  # drop idle clients
                for k in [k for k, d in self._hits.items() if not d or d[-1] <= now - self.window]:
                    del self._hits[k]
            return True


class Analyzer:
    def __init__(
        self,
        provider: Provider,
        store: Optional[SnapshotStore] = None,
        *,
        cache_ttl: float = 900.0,
        n_draws: int = 400,
    ) -> None:
        self.provider = provider
        self.store = store
        self.n_draws = n_draws
        self._bundles = TTLCache(cache_ttl)

    def analyze(self, video_id: str) -> Dict[str, Any]:
        bundle: Optional[Bundle] = self._bundles.get(video_id)
        if bundle is None:
            bundle = self.provider.fetch(video_id)
            self._bundles.set(video_id, bundle)

        stored = []
        if self.store is not None and bundle.source == "youtube":
            v = bundle.video
            self.store.record(video_id, bundle.fetched_at.timestamp(), v.views, v.likes, v.comments)
            published = v.published_at.timestamp()
            stored = [((ts - published) / 86400.0, views) for ts, views, _l, _c in self.store.history(video_id)]
        return build_report(bundle, stored, n_draws=self.n_draws)
