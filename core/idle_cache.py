"""Thread-safe memory caches with bounded size and explicit idle eviction."""

from collections import OrderedDict
from collections.abc import MutableMapping
from contextlib import nullcontext
from sys import getsizeof
from threading import RLock
from time import monotonic
from weakref import WeakValueDictionary


IDLE_CACHE_SECONDS = 10 * 60
_caches = WeakValueDictionary()
_registry_lock = RLock()


def _payload_size(value, seen, limit):
    """Estimate Python payload storage, counting shared references once per entry."""
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = getsizeof(value)
    if size > limit:
        return size
    if isinstance(value, dict):
        for key, item in value.items():
            size += _payload_size(key, seen, limit - size)
            if size > limit:
                break
            size += _payload_size(item, seen, limit - size)
            if size > limit:
                break
    elif isinstance(value, (list, tuple)):
        for item in value:
            size += _payload_size(item, seen, limit - size)
            if size > limit:
                break
    return size


class IdleCache(MutableMapping):
    """An LRU mapping whose unused entries can be released by the GUI timer.

    Reads refresh idle time. Expiration is explicit so consumers can protect
    active content before a sweep. An optional expiration lock coordinates with
    callers that already lock multi-step cache operations.

    max_bytes bounds estimated Python payload storage at insertion, in addition
    to maxsize. Values in a byte-limited cache must be replaced when changed.
    Oversized results are usable by the caller but are not retained here.
    """

    def __init__(self, maxsize=None, *, max_bytes=None, expiration_lock=None):
        if maxsize is not None and maxsize < 0:
            raise ValueError("maxsize must be nonnegative")
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes must be nonnegative")
        self._entries = OrderedDict()
        self._lock = RLock()
        self._expiration_lock = expiration_lock
        self.maxsize = maxsize
        self.max_bytes = max_bytes
        self._size_bytes = 0
        with _registry_lock:
            _caches[id(self)] = self

    def __getitem__(self, key):
        with self._lock:
            _accessed, value, size = self._entries[key]
            self._entries[key] = (monotonic(), value, size)
            self._entries.move_to_end(key)
            return value

    def __setitem__(self, key, value):
        size = (_payload_size((key, value), set(), self.max_bytes)
                if self.max_bytes is not None else 0)
        with self._lock:
            if key in self._entries:
                self._size_bytes -= self._entries.pop(key)[2]
            if self.max_bytes is not None and size > self.max_bytes:
                return
            self._entries[key] = (monotonic(), value, size)
            self._size_bytes += size
            self._entries.move_to_end(key)
            while ((self.maxsize is not None and len(self._entries) > self.maxsize)
                   or (self.max_bytes is not None and self._size_bytes > self.max_bytes)):
                self.popitem(last=False)

    def __delitem__(self, key):
        with self._lock:
            self._size_bytes -= self._entries.pop(key)[2]

    def __iter__(self):
        with self._lock:
            return iter(tuple(self._entries))

    def __len__(self):
        with self._lock:
            return len(self._entries)

    def __contains__(self, key):
        with self._lock:
            return key in self._entries

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._size_bytes = 0

    @property
    def size_bytes(self):
        """Estimated retained payload bytes; zero when byte accounting is disabled."""
        with self._lock:
            return self._size_bytes

    def move_to_end(self, key, last=True):
        with self._lock:
            self._entries.move_to_end(key, last=last)

    def popitem(self, last=True):
        with self._lock:
            key, (_accessed, value, size) = self._entries.popitem(last=last)
            self._size_bytes -= size
            return key, value

    def expire(self, *, protected_keys=(), now=None):
        protected = set(protected_keys)
        with self._expiration_lock or nullcontext():
            with self._lock:
                now = monotonic() if now is None else now
                expired = [key for key, (accessed, _value, _size) in self._entries.items()
                           if key not in protected and now - accessed >= IDLE_CACHE_SECONDS]
                for key in expired:
                    self._size_bytes -= self._entries.pop(key)[2]
                return len(expired)


def expire_idle_caches(protected=None):
    """Sweep living caches without retaining their owners or importing features."""
    protected = protected or {}
    with _registry_lock:
        caches = list(_caches.values())
    return sum(cache.expire(protected_keys=protected.get(id(cache), ()))
               for cache in caches)
