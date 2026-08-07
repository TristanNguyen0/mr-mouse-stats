"""Long-running service entrypoint for the Fargate task.

Two independent concerns share one container:

- the Twitch collector, on the main thread, permanently connected;
- the Liquipedia scrape, on a timer, on its own thread.

The scrape MUST NOT run on the collector's thread. `_read_lines` is a
blocking recv with a 5s timeout and the HTTP rate gate sleeps ~2s per
request, so an inline scrape would stop the socket being read for ~10s and
delay the PONG reply, risking a server-side disconnect.

The rate gate stays correct by construction here: one process, one
long-lived LiquipediaClient, one monotonic clock. That is the whole reason
the scrape rides along instead of being a scheduled Lambda.
"""

from __future__ import annotations

import logging
import signal
import threading

from . import config, db, log
from .http import LiquipediaClient
from .liquipedia import api
from .liquipedia.player import parse_player
from .liquipedia.settings_tables import parse_mouse_settings
from .liquipedia.tournament import parse_tournament
from .twitch.irc import ReadOnlyIrcClient
from .twitch.runner import collect

logger = logging.getLogger(__name__)

SCRAPE_WATCHDOG = 120.0


class Scraper:
    """Timer-driven Liquipedia refresh.

    Holds one LiquipediaClient for the life of the process so the 1-req/2s
    gate is honoured across runs, and its own database connection because
    psycopg connections are not thread-safe.
    """

    def __init__(self, stop: threading.Event) -> None:
        self._stop = stop
        self._client = LiquipediaClient()  # constructed once, on purpose
        self._tournaments = config.tournaments()
        self._interval = config.scrape_interval()

    def run_forever(self) -> None:
        if not self._tournaments:
            logger.info(
                "no tournaments configured; scrape thread idle",
                extra={"fields": {"env": config.ENV_TOURNAMENTS}},
            )
            return
        # Wait one interval before the first run: startup already has enough
        # to do, and the roster does not change between restarts.
        while not self._stop.wait(self._interval):
            try:
                self.run_once()
            except Exception as exc:  # a failed scrape must not kill the task
                logger.exception(
                    "scheduled scrape failed; will retry next interval",
                    extra={"fields": {"error": str(exc)}},
                )

    def run_once(self) -> dict[str, int]:
        counts = {"tournaments": 0, "players": 0, "settings": 0}
        conn = db.connect(config.db())
        try:
            store = db.Store(conn)
            for page in self._tournaments:
                counts["players"] += self._refresh_tournament(store, page)
                counts["tournaments"] += 1
            counts["settings"] = self._ingest_settings(store)
            store.commit()
        finally:
            conn.close()
        logger.info("scheduled scrape complete", extra={"fields": counts})
        return counts

    def _refresh_tournament(self, store: db.Store, page_name: str) -> int:
        wiki_page = api.fetch_page(self._client, page_name)
        if wiki_page.missing:
            logger.error(
                "tournament page missing", extra={"fields": {"page": page_name}}
            )
            return 0
        meta, teams = parse_tournament(wiki_page.wikitext)
        now = db.now_utc()
        tournament_id = store.upsert_tournament(page_name, meta, now)

        names = sorted({p.name for team in teams for p in team.persons if not p.is_staff})
        pages = api.fetch_pages(self._client, names)
        player_ids: dict[str, int] = {}
        for team in teams:
            team_id = store.get_or_create_team(team.name)
            for person in team.persons:
                if person.is_staff:
                    continue
                if person.name not in player_ids:
                    wiki = pages[person.name]
                    info = None if wiki.missing else parse_player(wiki.title, wiki.wikitext)
                    status = (
                        "missing" if wiki.missing
                        else "not_player_page" if info is None
                        else "resolved"
                    )
                    pid = store.upsert_player_stub(wiki.title, status, now)
                    if info is not None:
                        store.update_player_resolved(pid, info, now)
                        for account in info.socials:
                            store.record_social_account(
                                pid, account.platform, account.handle,
                                account.url, now,
                            )
                    else:
                        store.mark_player_status(pid, status, now)
                    player_ids[person.name] = pid
                store.upsert_roster_entry(
                    tournament_id, team_id, player_ids[person.name], person.role,
                    person.is_sub, person.is_staff, person.played, team.section,
                )
        return len(player_ids)

    def _ingest_settings(self, store: db.Store) -> int:
        players = store.resolved_players()
        if not players:
            return 0
        pages = api.fetch_pages(self._client, [p["liquipedia_page"] for p in players])
        added = 0
        for player in players:
            page = pages[player["liquipedia_page"]]
            if page.missing or page.wikitext is None:
                continue
            for entry in parse_mouse_settings(page.wikitext):
                if entry.date is None:
                    continue
                if store.has_settings_observation(player["id"], "liquipedia", entry.date):
                    continue
                store.add_settings_observation(
                    player["id"], entry.date, "liquipedia",
                    dpi=entry.dpi, sensitivity=entry.sensitivity,
                    windows_sens=entry.windows, polling_rate=entry.polling,
                    zoom_sens=entry.zoom, mouse_brand=entry.brand,
                    mouse_model=entry.model, pad_brand=entry.pad_brand,
                    pad_model=entry.pad_model, ref_url=entry.ref_url,
                )
                added += 1
        return added


def run_service() -> int:
    """Collector on the main thread, scrape on a timer thread."""
    log.setup()
    stop = threading.Event()

    def handle_signal(signum, _frame):
        logger.info("signal received; shutting down",
                    extra={"fields": {"signal": signal.Signals(signum).name}})
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    conn = db.connect(config.db())
    store = db.Store(conn)
    channels = sorted(store.player_ids_by_twitch_channel())
    if not channels:
        logger.error("no twitch channels known; nothing to collect")
        return 1

    scraper = Scraper(stop)
    scrape_thread = threading.Thread(
        target=scraper.run_forever, name="liquipedia-scrape", daemon=True
    )
    scrape_thread.start()

    logger.info(
        "service starting",
        extra={"fields": {"channels": len(channels),
                          "scrape_interval": scraper._interval,
                          "tournaments": len(scraper._tournaments)}},
    )
    client = ReadOnlyIrcClient(channels)
    stats = collect(client, store, should_stop=stop.is_set)
    stop.set()
    conn.close()
    logger.info("service stopped", extra={"fields": dict(stats)})
    return 0
