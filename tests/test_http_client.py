import json

import pytest

from mr_mouse_stats.http import LiquipediaClient, LiquipediaError


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(tmp_path, responses, clock=None, **kwargs):
    clock = clock or FakeClock()
    calls = []

    def transport(url):
        calls.append(url)
        return json.dumps(responses[len(calls) - 1]).encode()

    client = LiquipediaClient(
        cache_dir=tmp_path,
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
        **kwargs,
    )
    return client, calls, clock


def test_requests_are_spaced_two_seconds(tmp_path):
    client, calls, clock = make_client(tmp_path, [{"a": 1}, {"b": 2}])
    client.get(action="query", titles="One")
    client.get(action="query", titles="Two")
    assert len(calls) == 2
    assert clock.sleeps == [2.0]


def test_parse_requests_are_spaced_thirty_seconds(tmp_path):
    client, calls, clock = make_client(tmp_path, [{"a": 1}, {"b": 2}, {"c": 3}])
    client.get(action="parse", page="One")
    client.get(action="query", titles="Two")  # only the 2 s gate applies
    client.get(action="parse", page="Three")  # 30 s since the first parse
    assert clock.sleeps == [2.0, 28.0]


def test_no_wait_when_enough_time_elapsed(tmp_path):
    client, calls, clock = make_client(tmp_path, [{"a": 1}, {"b": 2}])
    client.get(action="query", titles="One")
    clock.now += 10
    client.get(action="query", titles="Two")
    assert clock.sleeps == []


def test_cache_serves_repeat_requests_without_transport(tmp_path):
    client, calls, _ = make_client(tmp_path, [{"a": 1}])
    first = client.get(action="query", titles="One")
    second = client.get(action="query", titles="One")
    assert first == second == {"a": 1}
    assert len(calls) == 1


def test_cache_is_shared_across_client_instances(tmp_path):
    client, calls, _ = make_client(tmp_path, [{"a": 1}])
    client.get(action="query", titles="One")
    client2, calls2, _ = make_client(tmp_path, [{"other": 2}])
    assert client2.get(action="query", titles="One") == {"a": 1}
    assert calls2 == []


def test_refresh_bypasses_cache_read_but_rewrites(tmp_path):
    client, _, _ = make_client(tmp_path, [{"a": 1}])
    client.get(action="query", titles="One")
    fresh, calls, _ = make_client(tmp_path, [{"a": 2}], refresh=True)
    assert fresh.get(action="query", titles="One") == {"a": 2}
    assert len(calls) == 1


def test_expired_cache_refetches(tmp_path):
    client, calls, _ = make_client(tmp_path, [{"a": 1}, {"a": 2}], cache_ttl=0.0)
    client.get(action="query", titles="One")
    assert client.get(action="query", titles="One") == {"a": 2}
    assert len(calls) == 2


def test_api_error_raises_and_is_not_cached(tmp_path):
    err = {"error": {"code": "missingtitle", "info": "nope"}}
    client, calls, _ = make_client(tmp_path, [err, {"ok": 1}])
    with pytest.raises(LiquipediaError, match="missingtitle"):
        client.get(action="query", titles="One")
    assert client.get(action="query", titles="One") == {"ok": 1}
    assert len(calls) == 2
