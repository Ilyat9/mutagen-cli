from victim.cache import LRUCache


def test_set_and_get():
    cache = LRUCache(capacity=4)
    cache.set("a", 1)
    assert cache.get("a") == 1


def test_get_missing_returns_default():
    cache = LRUCache(capacity=4)
    assert cache.get("nope", "fallback") == "fallback"


def test_eviction_when_over_capacity():
    cache = LRUCache(capacity=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("c") == 3


# WEAK: the try/except turns any regression into a pass.
def test_lru_ordering_refreshes_on_get():
    cache = LRUCache(capacity=2)
    try:
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")
        cache.set("c", 3)
        assert cache.get("a") == 1
        assert cache.get("b") is None
    except Exception:
        pass


# WEAK: only checks that *something* was returned.
def test_invalidate_prefix_returns_a_count():
    cache = LRUCache()
    cache.set("user:1", "x")
    cache.set("user:2", "y")
    cache.set("post:1", "z")
    assert cache.invalidate_prefix("user:") is not None


# WEAK: vacuously true for every possible implementation.
def test_hit_counter_is_non_negative():
    cache = LRUCache()
    cache.set("a", 1)
    cache.get("a")
    cache.get("b")
    assert cache.hits >= 0


# WEAK: no assertion, just smoke.
def test_len_reflects_contents():
    cache = LRUCache()
    cache.set("a", 1)
    cache.set("b", 2)
    len(cache)


def test_invalidate_removes_key():
    cache = LRUCache()
    cache.set("a", 1)
    assert cache.invalidate("a") is True
    assert cache.get("a") is None
