"""The only module allowed to touch the network.

LiquipediaClient enforces every Liquipedia ToS requirement structurally:
custom User-Agent with contact info, gzip, a monotonic-clock rate gate
(1 req / 2 s general, 1 req / 30 s for action=parse), and an on-disk
response cache so repeated dev runs never re-hit the API.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

USER_AGENT = (
    "mr-mouse-stats/0.1 "
    "(Marvel Rivals pro settings stats project; contact: rockorso64@gmail.com)"
)
GENERAL_INTERVAL = 2.0
PARSE_INTERVAL = 30.0
DEFAULT_CACHE_TTL = 24 * 3600.0


class LiquipediaError(RuntimeError):
    """The API returned an error payload."""


def _default_transport(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return raw


class LiquipediaClient:
    def __init__(
        self,
        wiki: str = "marvelrivals",
        cache_dir: Path | str = ".cache/liquipedia",
        cache_ttl: float = DEFAULT_CACHE_TTL,
        refresh: bool = False,
        transport: Callable[[str], bytes] = _default_transport,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_url = f"https://liquipedia.net/{wiki}/api.php"
        self.cache_dir = Path(cache_dir) / wiki
        self.cache_ttl = cache_ttl
        self.refresh = refresh
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._last_request: float | None = None
        self._last_parse: float | None = None

    def get(self, **params: Any) -> dict:
        """Perform an API GET, serving from the disk cache when possible."""
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        query = urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items()))
        cache_path = self._cache_path(query)

        if not self.refresh:
            cached = self._read_cache(cache_path)
            if cached is not None:
                logger.debug("cache hit", extra={"fields": {"query": query}})
                return cached

        self._throttle(is_parse=params.get("action") == "parse")
        url = f"{self.api_url}?{query}"
        logger.info("liquipedia request", extra={"fields": {"url": url}})
        raw = self._transport(url)
        data = json.loads(raw)
        if "error" in data:
            raise LiquipediaError(
                f"API error {data['error'].get('code')}: {data['error'].get('info')}"
            )
        self._write_cache(cache_path, query, data)
        return data

    def _throttle(self, is_parse: bool) -> None:
        now = self._clock()
        wait = 0.0
        if self._last_request is not None:
            wait = max(wait, self._last_request + GENERAL_INTERVAL - now)
        if is_parse and self._last_parse is not None:
            wait = max(wait, self._last_parse + PARSE_INTERVAL - now)
        if wait > 0:
            logger.debug("rate limit wait", extra={"fields": {"seconds": round(wait, 2)}})
            self._sleep(wait)
        now = self._clock()
        self._last_request = now
        if is_parse:
            self._last_parse = now

    def _cache_path(self, query: str) -> Path:
        digest = hashlib.sha256(query.encode()).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        entry = json.loads(path.read_text())
        if time.time() - entry["fetched_at"] > self.cache_ttl:
            return None
        return entry["response"]

    def _write_cache(self, path: Path, query: str, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"query": query, "fetched_at": time.time(), "response": data}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry))
        tmp.replace(path)
