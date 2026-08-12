"""Drives NightbotClient against the saved responses of a real channel.

Fixtures are verbatim captures from api.nightbot.tv for `aplycs`; the
endpoints are undocumented, so a shape change should surface here as a
deliberate fixture refresh.
"""

import json
from pathlib import Path

import pytest

from mr_mouse_stats.http import HttpNotFound, NightbotClient
from mr_mouse_stats.nightbot import api as nightbot_api

FIXTURES = Path(__file__).parent / "fixtures"
CHANNEL_ID = "65ce2bff709e0dbaf5f33285"


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(tmp_path, missing=(), **kwargs):
    """A client serving the saved fixtures. Logins in `missing` 404."""
    calls = []
    channel = json.loads((FIXTURES / "nightbot_channel_aplycs.json").read_text())
    commands = json.loads((FIXTURES / "nightbot_commands_aplycs.json").read_text())
    clock = FakeClock()

    def transport(url, headers):
        calls.append((url, dict(headers)))
        login = url.rsplit("/", 1)[-1]
        if url.endswith("/commands"):
            return json.dumps(commands).encode()
        if login in missing:
            raise HttpNotFound(url)
        return json.dumps(channel).encode()

    client = NightbotClient(
        cache_dir=tmp_path,
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
        **kwargs,
    )
    return client, calls, clock


def test_only_settings_commands_are_returned(tmp_path):
    client, _, _ = make_client(tmp_path)
    result = nightbot_api.fetch_channel(client, "aplycs")
    assert result.registered
    assert result.bot_channel_id == CHANNEL_ID
    assert {c.name for c in result.commands} == {"sens", "mouse", "mousepad"}


def test_the_channel_id_travels_in_a_header(tmp_path):
    client, calls, _ = make_client(tmp_path)
    nightbot_api.fetch_channel(client, "aplycs")
    commands_call = next(c for c in calls if c[0].endswith("/commands"))
    assert commands_call[1]["Nightbot-Channel"] == CHANNEL_ID


def test_timestamps_match_the_shape_the_twitch_capture_writes(tmp_path):
    client, _, _ = make_client(tmp_path)
    result = nightbot_api.fetch_channel(client, "aplycs")
    sens = next(c for c in result.commands if c.name == "sens")
    # Nightbot sends "2026-08-07T08:58:41.061Z"; settings_observations sorts
    # observed_at as text, so it has to be normalized to the same form.
    assert sens.updated_at == "2026-08-07T08:58:41+00:00"
    assert sens.message == "0.125 1600dpi"


def test_unregistered_channel_is_a_recorded_answer_not_an_error(tmp_path):
    client, _, _ = make_client(tmp_path, missing={"shroud"})
    result = nightbot_api.fetch_channel(client, "shroud")
    assert result.registered is False
    assert result.commands == []


def test_a_404_is_cached_so_it_costs_one_request(tmp_path):
    client, calls, _ = make_client(tmp_path, missing={"shroud"})
    nightbot_api.fetch_channel(client, "shroud")
    nightbot_api.fetch_channel(client, "shroud")
    assert len(calls) == 1


def test_requests_are_rate_gated(tmp_path):
    client, _, clock = make_client(tmp_path)
    list(nightbot_api.fetch_channels(client, ["aplycs", "aplycs"]))
    # Two requests for the first channel; the repeat is served from cache.
    assert clock.sleeps == [1.0]


def test_channels_are_deduplicated_case_insensitively(tmp_path):
    client, _, _ = make_client(tmp_path)
    results = list(nightbot_api.fetch_channels(client, ["Aplycs", "aplycs"]))
    assert [r.channel for r in results] == ["aplycs"]


def test_command_arguments_are_redacted_before_they_are_returned(tmp_path):
    """Whatever the API sends, what leaves this layer is already redacted."""
    calls = []
    channel = json.loads((FIXTURES / "nightbot_channel_aplycs.json").read_text())
    secret = {
        "_total": 1,
        "commands": [{
            "_id": "1", "name": "!dpi", "userLevel": "everyone",
            "message": "$(urlfetch https://example.com/dpi?token=SECRET)",
            "updatedAt": "2026-08-07T08:58:41.061Z",
        }],
    }

    def transport(url, headers):
        payload = secret if url.endswith("/commands") else channel
        return json.dumps(payload).encode()

    client = NightbotClient(cache_dir=tmp_path, transport=transport,
                            sleep=lambda _: None)
    result = nightbot_api.fetch_channel(client, "aplycs")
    assert result.commands[0].message == "$(urlfetch)"
    assert "SECRET" not in result.commands[0].message


@pytest.mark.parametrize("login", ["aplycs", "APLYCS"])
def test_lookup_is_case_insensitive(tmp_path, login):
    client, _, _ = make_client(tmp_path)
    assert nightbot_api.fetch_channel(client, login).channel == "aplycs"
