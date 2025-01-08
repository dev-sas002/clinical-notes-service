"""The cache seam.

Analytics is the read-heavy path and the same dashboard window gets requested
over and over, so a cache is the obvious next lever. What is *not* obvious is
where that cache should live: in-process is right for one container, Redis is
right for several behind a load balancer.

So the service depends on the :class:`AnalyticsCache` protocol rather than on a
cache implementation. Adding Redis later means writing one class that satisfies
the protocol and returning it from :func:`build_cache` -- no change to the
service, the router or the tests.

Cache keys are built by :func:`stats_cache_key`, which puts the tenant id first
and is the only supported way to make one. A cache shared between tenants with
a key that omits the tenant is a data leak, so the key is not left to callers.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "AnalyticsCache",
    "InMemoryTTLCache",
    "NullCache",
    "build_cache",
    "stats_cache_key",
]


@runtime_checkable
class AnalyticsCache(Protocol):
    """Minimal cache contract: get, set, clear."""

    def get(self, key: str) -> Any | None:
        """Return the cached value, or ``None`` if absent or expired."""

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key``."""

    def clear(self) -> None:
        """Drop everything."""


class NullCache:
    """Cache that never caches. Used when the TTL is configured to 0."""

    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any) -> None:
        return None

    def clear(self) -> None:
        return None


class InMemoryTTLCache:
    """Bounded, per-process TTL cache with LRU eviction.

    Bounded on purpose: an unbounded dict keyed by user-supplied date ranges is
    a memory-exhaustion vector, since a client can mint unlimited distinct keys.
    """

    def __init__(self, ttl_seconds: float, max_entries: int = 512) -> None:
        self._ttl = float(ttl_seconds)
        self._max_entries = int(max_entries)
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        self._entries[key] = (time.monotonic() + self._ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


def stats_cache_key(
    tenant_id: int,
    facility_ids: tuple[int, ...] | None,
    start: object,
    end: object,
) -> str:
    """Build a analytics cache key. The tenant id always comes first."""
    facilities = ",".join(str(f) for f in sorted(facility_ids)) if facility_ids else "*"
    return f"stats:t{tenant_id}:f{facilities}:{start!s}:{end!s}"


def build_cache(ttl_seconds: int, max_entries: int) -> AnalyticsCache:
    """Return the cache implementation the configuration asks for."""
    if ttl_seconds <= 0:
        return NullCache()
    return InMemoryTTLCache(ttl_seconds=ttl_seconds, max_entries=max_entries)
