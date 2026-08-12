"""The only module allowed to touch the network.

Every outbound request goes through CachedHttpClient, which enforces the
obligations both sources share, structurally: a custom User-Agent with
contact info, gzip, a monotonic-clock rate gate, and an on-disk response
cache so repeated dev runs never re-hit the API. Subclasses add only their
own URL shape, their own gate intervals, and their own error payloads.

LiquipediaClient carries the Liquipedia ToS limits (1 req / 2 s general,
1 req / 30 s for action=parse). NightbotClient talks to the undocumented
endpoints behind nightbot.tv's public command pages; nothing there is
rate-limit documented, so it self-throttles to 1 req / s.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import config

logger = logging.getLogger(__name__)

USER_AGENT = (
    "mr-mouse-stats/0.1 "
    "(Marvel Rivals pro settings stats project; contact: tristann0708@gmail.com)"
)
GENERAL_INTERVAL = 2.0
PARSE_INTERVAL = 30.0
NIGHTBOT_INTERVAL = 1.0
DEFAULT_CACHE_TTL = 24 * 3600.0

Gate = tuple[str, float]


class LiquipediaError(RuntimeError):
    """The API returned an error payload."""


class NightbotError(RuntimeError):
    """The Nightbot API returned an error payload."""


class HttpNotFound(RuntimeError):
    """The upstream returned 404.

    Its own type because a 404 is data, not a failure: a channel that never
    registered with Nightbot is a normal, expected outcome that the caller
    records rather than retries.
    """


def _default_transport(url: str, headers: Mapping[str, str]) -> bytes:
    request_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise HttpNotFound(url) from exc
        raise
    return raw


class CachedHttpClient:
    """Rate-gated, disk-cached JSON GET. Subclass; do not use directly."""

    # Cache subdirectory, so two sources never collide on a query hash.
    namespace = "http"

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        cache_ttl: float = DEFAULT_CACHE_TTL,
        refresh: bool = False,
        transport: Callable[[str, Mapping[str, str]], bytes] = _default_transport,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache_dir = Path(cache_dir) / self.namespace
        self.cache_ttl = cache_ttl
        self.refresh = refresh
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        # gate name -> monotonic stamp of the last request that used it
        self._last: dict[str, float] = {}

    def _request(
        self,
        url: str,
        cache_key: str,
        gates: Iterable[Gate],
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """GET url, serving from the disk cache when possible.

        Returns the decoded JSON body, or None when the upstream answered
        404 — which is cached too, so a player with no account there costs
        one request per cache lifetime rather than one per run.
        """
        cache_path = self._cache_path(cache_key)
        if not self.refresh:
            entry = self._read_cache(cache_path)
            if entry is not None:
                logger.debug("cache hit", extra={"fields": {"key": cache_key}})
                return entry["response"]

        self._throttle(gates)
        logger.info("request", extra={"fields": {"url": url}})
        try:
            raw = self._transport(url, headers or {})
        except HttpNotFound:
            logger.info("not found", extra={"fields": {"url": url}})
            self._write_cache(cache_path, cache_key, None)
            return None
        data = json.loads(raw)
        self._check_error(data)
        self._write_cache(cache_path, cache_key, data)
        return data

    def _check_error(self, data: Any) -> None:
        """Raise on an error payload, before anything is cached."""

    def _throttle(self, gates: Iterable[Gate]) -> None:
        """Wait out every named gate, then stamp all of them.

        Named rather than positional so a request can sit behind several at
        once: an action=parse call takes both the general 2 s gate and the
        30 s parse gate, and stamps both; a plain query stamps only the
        general one, leaving the parse gate where it was.
        """
        gates = list(gates)
        now = self._clock()
        wait = 0.0
        for name, interval in gates:
            last = self._last.get(name)
            if last is not None:
                wait = max(wait, last + interval - now)
        if wait > 0:
            logger.debug("rate limit wait", extra={"fields": {"seconds": round(wait, 2)}})
            self._sleep(wait)
        now = self._clock()
        for name, _ in gates:
            self._last[name] = now

    def _cache_path(self, cache_key: str) -> Path:
        digest = hashlib.sha256(cache_key.encode()).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path) -> dict | None:
        """The cache entry, or None when missing or stale.

        The entry rather than the response, because a cached 404 stores a
        null response and has to stay distinguishable from a cache miss.
        """
        if not path.exists():
            return None
        entry = json.loads(path.read_text())
        if time.time() - entry["fetched_at"] > self.cache_ttl:
            return None
        return entry

    def _write_cache(self, path: Path, cache_key: str, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"query": cache_key, "fetched_at": time.time(), "response": data}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry))
        tmp.replace(path)


class LiquipediaClient(CachedHttpClient):
    def __init__(
        self,
        wiki: str | None = None,
        cache_dir: Path | str | None = None,
        **kwargs: Any,
    ) -> None:
        # Read the environment lazily rather than freezing it at import time.
        wiki = wiki or config.wiki()
        cache_dir = config.cache_dir() if cache_dir is None else cache_dir
        # The wiki name is the cache namespace, as it has always been.
        self.namespace = wiki
        super().__init__(cache_dir=cache_dir, **kwargs)
        self.api_url = f"https://liquipedia.net/{wiki}/api.php"

    def _check_error(self, data: Any) -> None:
        if isinstance(data, dict) and "error" in data:
            raise LiquipediaError(
                f"API error {data['error'].get('code')}: {data['error'].get('info')}"
            )

    def get(self, **params: Any) -> dict:
        """Perform an API GET, serving from the disk cache when possible."""
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        query = urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items()))
        gates: list[Gate] = [("general", GENERAL_INTERVAL)]
        if params.get("action") == "parse":
            gates.append(("parse", PARSE_INTERVAL))
        return self._request(f"{self.api_url}?{query}", query, gates)


class NightbotClient(CachedHttpClient):
    """Reads the two undocumented endpoints behind nightbot.tv/t/<name>/commands.

    Both are unauthenticated and neither is in Nightbot's published API docs,
    which document the same paths as OAuth-only. They are what the public
    command page itself calls, so they are real but unsupported: treat a
    shape change as a fixture refresh, never as a crash.
    """

    namespace = "nightbot"
    api_url = "https://api.nightbot.tv/1"

    def __init__(self, cache_dir: Path | str | None = None, **kwargs: Any) -> None:
        cache_dir = config.nightbot_cache_dir() if cache_dir is None else cache_dir
        super().__init__(cache_dir=cache_dir, **kwargs)

    def _check_error(self, data: Any) -> None:
        # Nightbot answers {"status": 4xx, "message": ...} on some errors
        # rather than using the HTTP status alone.
        if isinstance(data, dict) and isinstance(data.get("status"), int):
            if data["status"] >= 400:
                raise NightbotError(f"{data['status']}: {data.get('message')}")

    def channel(self, twitch_login: str) -> dict | None:
        """The Nightbot channel record for a Twitch login, or None if the
        channel never registered with Nightbot."""
        name = urllib.parse.quote(twitch_login.lower(), safe="")
        data = self._request(
            f"{self.api_url}/channels/t/{name}",
            f"channels/t/{name}",
            [("general", NIGHTBOT_INTERVAL)],
        )
        return None if data is None else data.get("channel")

    def commands(self, channel_id: str) -> list[dict]:
        """Every custom command defined on a Nightbot channel.

        The channel id travels in a header, not the path — which is why the
        cache key has to name it explicitly, or every channel would collide
        on one entry.
        """
        data = self._request(
            f"{self.api_url}/commands",
            f"commands?channel={channel_id}",
            [("general", NIGHTBOT_INTERVAL)],
            headers={"Nightbot-Channel": channel_id, "Accept": "application/json"},
        )
        if data is None:
            return []
        return data.get("commands", [])
