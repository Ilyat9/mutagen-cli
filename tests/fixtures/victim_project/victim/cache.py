"""A tiny LRU cache with prefix invalidation."""

from collections import OrderedDict

_MISSING = object()


class LRUCache:
    """Least-recently-used cache bounded by ``capacity`` entries."""

    def __init__(self, capacity=128):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._data = OrderedDict()
        self.hits = 0
        self.misses = 0

    def __len__(self):
        return len(self._data)

    def get(self, key, default=None):
        """Return the cached value, refreshing its recency."""
        if key not in self._data:
            self.misses += 1
            return default
        self.hits += 1
        self._data.move_to_end(key)
        return self._data[key]

    def set(self, key, value):
        """Store ``value``, evicting the least-recently-used entry if full."""
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        if len(self._data) > self.capacity:
            self._data.popitem(last=False)

    def invalidate(self, key):
        """Drop a single key. Returns True if something was removed."""
        if key in self._data:
            del self._data[key]
            return True
        return False

    def invalidate_prefix(self, prefix):
        """Drop every key starting with ``prefix``. Returns how many went."""
        doomed = [key for key in self._data if key.startswith(prefix)]
        for key in doomed:
            del self._data[key]
        return len(doomed)
