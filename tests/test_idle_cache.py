from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import weakref
from weakref import WeakValueDictionary

import pytest

import core.idle_cache as caches
from core.idle_cache import IdleCache, expire_idle_caches


@pytest.fixture
def clock(monkeypatch):
    time = [0.0]
    monkeypatch.setattr(caches, "monotonic", lambda: time[0])
    monkeypatch.setattr(caches, "_caches", WeakValueDictionary())
    return time


def test_idle_sweep_keeps_recently_used_entries(clock):
    cache = IdleCache()
    cache["unused"] = "old"
    cache["used"] = "recent"
    clock[0] = 599
    assert cache.get("used") == "recent"
    clock[0] = 600
    assert expire_idle_caches() == 1
    assert list(cache) == ["used"]
    clock[0] = 1199
    assert expire_idle_caches() == 1
    assert not cache


def test_sweep_releases_buffers_and_protects_active_content(clock):
    class Buffer:
        pass

    cache = IdleCache()
    buffer = Buffer()
    reference = weakref.ref(buffer)
    cache["visible"] = buffer
    del buffer
    clock[0] = 600
    assert expire_idle_caches({id(cache): {"visible"}}) == 0
    assert reference() is not None
    assert expire_idle_caches() == 1
    assert reference() is None


def test_size_limit_still_evicts_least_recently_used_entries(clock):
    cache = IdleCache(2)
    cache["first"] = 1
    cache["second"] = 2
    assert cache["first"] == 1
    cache["third"] = 3
    assert list(cache) == ["first", "third"]
    assert cache.popitem(last=False) == ("first", 1)
    cache.clear()
    clock[0] = 600
    assert expire_idle_caches() == 0


def test_registry_does_not_keep_feature_caches_alive(clock):
    cache = IdleCache()
    reference = weakref.ref(cache)
    del cache
    assert reference() is None
    assert expire_idle_caches() == 0


def test_sweep_coordinates_with_existing_worker_lock(clock):
    lock = Lock()
    cache = IdleCache(expiration_lock=lock)
    cache["reading"] = "tokens"
    clock[0] = 600
    started = Event()

    def expire():
        started.set()
        return cache.expire()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with lock:
            future = executor.submit(expire)
            assert started.wait(2)
            assert not future.done()
            assert cache["reading"] == "tokens"
        assert future.result(timeout=2) == 0


@pytest.mark.parametrize("max_bytes", [None, 2048])
def test_worker_reads_and_writes_can_overlap_sweeps(clock, max_bytes):
    cache = IdleCache(50, max_bytes=max_bytes)

    def use_cache():
        for index in range(2000):
            cache[index] = index
            value = cache.get(index)
            assert value is None or value == index

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(use_cache)
        for _ in range(100):
            cache.expire(now=600)
        future.result(timeout=5)
    assert len(cache) <= 50
    assert max_bytes is None or cache.size_bytes <= max_bytes


def test_byte_budget_evicts_old_payloads_in_access_order(clock):
    cache = IdleCache(50, max_bytes=2000)
    cache["first"] = b"a" * 800
    cache["second"] = b"b" * 800
    assert list(cache) == ["first", "second"]
    assert cache["first"] == b"a" * 800
    cache["third"] = b"c" * 800
    assert list(cache) == ["first", "third"]
    assert 1600 <= cache.size_bytes <= 2000


def test_accounting_tracks_replacements_removals_and_expiration(clock):
    cache = IdleCache(max_bytes=4000)
    cache["key"] = b"a" * 800
    initial_size = cache.size_bytes
    cache["key"] = b"b" * 400
    assert cache.size_bytes == initial_size - 400
    cache["other"] = b"c" * 400
    del cache["key"]
    assert 400 <= cache.size_bytes < 800
    assert cache.popitem() == ("other", b"c" * 400)
    assert cache.size_bytes == 0
    cache["key"] = b"a" * 800
    clock[0] = 600
    assert cache.expire() == 1
    assert cache.size_bytes == 0
    cache["key"] = b"a" * 800
    cache.clear()
    assert cache.size_bytes == 0


def test_oversized_replacement_is_not_retained_or_allowed_to_evict_other_entries(clock):
    cache = IdleCache(max_bytes=2000)
    cache["first"] = b"a" * 400
    cache["second"] = b"b" * 400
    cache["first"] = b"x" * 4000
    assert list(cache) == ["second"]
    assert 400 <= cache.size_bytes < 800


def test_payload_budget_counts_nested_data_and_handles_shared_references(clock):
    cache = IdleCache(max_bytes=2000)
    shared = bytearray(800)
    cache["shared"] = [shared, shared]
    assert "shared" in cache
    cache["nested"] = {"words": [bytearray(800), bytearray(800), bytearray(800)]}
    assert "nested" not in cache
    assert "shared" in cache
    recursive = []
    recursive.append(recursive)
    cache["cycle"] = recursive
    assert cache["cycle"][0] is recursive
    assert cache.size_bytes <= 2000


def test_oversized_values_stop_size_estimation_without_traversing_remaining_data(clock):
    class UnvisitedList(list):
        def __iter__(self):
            raise AssertionError("The payload already exceeds the budget")

    cache = IdleCache(max_bytes=2000)
    cache["large"] = [bytearray(4000), UnvisitedList()]
    assert not cache and cache.size_bytes == 0


@pytest.mark.parametrize("options", [{"maxsize": -1}, {"max_bytes": -1}])
def test_negative_limits_are_rejected(options):
    with pytest.raises(ValueError):
        IdleCache(**options)


def test_track_metadata_reloads_after_idle_eviction(clock, tmp_path, monkeypatch):
    import core.music_library as library

    cache = IdleCache(5000)
    monkeypatch.setattr(library, "_TRACK_CACHE", cache)
    calls = []
    monkeypatch.setattr(library, "MutagenFile", lambda *args, **kwargs: calls.append(args))
    song = tmp_path / "song.mp3"
    song.write_bytes(b"audio")
    first = library.read_track(song)
    assert library.read_track(song) is first
    clock[0] = 600
    assert expire_idle_caches() == 1
    rebuilt = library.read_track(song)
    assert rebuilt == first and rebuilt is not first
    assert len(calls) == 2


def test_lyric_search_text_reloads_after_idle_eviction(clock, tmp_path):
    from core.library_lyrics import LibraryLyricsIndex

    index = LibraryLyricsIndex()
    lyric = tmp_path / "song.lrc"
    lyric.write_text("[00:01]Hello world", encoding="utf-8")
    first = index._text(lyric)
    assert index._text(lyric) is first
    clock[0] = 600
    assert expire_idle_caches() == 1
    rebuilt = index._text(lyric)
    assert rebuilt == first and rebuilt is not first
