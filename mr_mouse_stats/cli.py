"""CLI entry point: `mr-mouse-stats fetch-roster <tournament page>`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import db, log
from .http import LiquipediaClient
from .liquipedia import api
from .liquipedia.player import parse_player
from .liquipedia.tournament import parse_tournament
from .models import PlayerInfo, RosterPerson, TeamEntry, WikiPage

logger = logging.getLogger(__name__)


def _mediawiki_title(name: str) -> str:
    """Best-effort local normalization (first letter uppercased), used only
    for names we deliberately don't fetch (staff)."""
    return name[:1].upper() + name[1:] if name else name


def cmd_fetch_roster(args: argparse.Namespace) -> int:
    client = LiquipediaClient(
        wiki=args.wiki,
        cache_dir=args.cache_dir,
        refresh=args.refresh_cache,
    )

    page = api.fetch_page(client, args.page)
    if page.missing:
        logger.error("tournament page missing", extra={"fields": {"page": args.page}})
        return 1
    meta, teams = parse_tournament(page.wikitext)
    if not teams:
        logger.error("no participants found", extra={"fields": {"page": args.page}})
        return 1
    logger.info(
        "parsed tournament",
        extra={"fields": {"name": meta.name, "teams": len(teams)}},
    )

    to_resolve = sorted(
        {p.name for team in teams for p in team.persons if not p.is_staff}
    )
    pages = api.fetch_pages(client, to_resolve)
    resolved: dict[str, PlayerInfo] = {}
    statuses: dict[str, str] = {}
    for name in to_resolve:
        wiki_page = pages[name]
        if wiki_page.missing:
            statuses[name] = "missing"
        else:
            info = parse_player(wiki_page.title, wiki_page.wikitext)
            if info is None:
                statuses[name] = "not_player_page"
            else:
                statuses[name] = "resolved"
                resolved[name] = info
        logger.info(
            "player resolution",
            extra={
                "fields": {
                    "name": name,
                    "page": wiki_page.title,
                    "status": statuses[name],
                    "twitch": next(
                        (s.handle for s in resolved[name].socials if s.platform == "twitch"),
                        None,
                    )
                    if name in resolved
                    else None,
                }
            },
        )

    if args.dry_run:
        logger.info("dry run: skipping database writes")
    else:
        _persist(args.db, args.page, meta, teams, pages, resolved, statuses)

    _print_summary(meta, teams, pages, resolved, statuses, args.dry_run)
    return 0


def _persist(
    db_path: Path,
    page_name: str,
    meta,
    teams: list[TeamEntry],
    pages: dict[str, WikiPage],
    resolved: dict[str, PlayerInfo],
    statuses: dict[str, str],
) -> None:
    conn = db.connect(db_path)
    store = db.Store(conn)
    now = db.now_utc()
    tournament_id = store.upsert_tournament(page_name, meta, now)

    player_ids: dict[str, int] = {}
    for team in teams:
        team_id = store.get_or_create_team(team.name)
        for person in team.persons:
            if person.is_staff:
                liquipedia_page = _mediawiki_title(person.name)
                status = "skipped_staff"
            else:
                liquipedia_page = pages[person.name].title
                status = statuses[person.name]
            if person.name not in player_ids:
                pid = store.upsert_player_stub(liquipedia_page, status, now)
                if person.name in resolved:
                    store.update_player_resolved(pid, resolved[person.name], now)
                else:
                    store.mark_player_status(pid, status, now)
                player_ids[person.name] = pid
            pid = player_ids[person.name]
            store.upsert_roster_entry(
                tournament_id,
                team_id,
                pid,
                person.role,
                person.is_sub,
                person.is_staff,
                person.played,
                team.section,
            )
            for account in resolved[person.name].socials if person.name in resolved else ():
                store.record_social_account(
                    pid, account.platform, account.handle, account.url, now
                )
    store.commit()
    conn.close()
    logger.info(
        "persisted roster",
        extra={"fields": {"db": str(db_path), "players": len(player_ids)}},
    )


def _print_summary(
    meta,
    teams: list[TeamEntry],
    pages: dict[str, WikiPage],
    resolved: dict[str, PlayerInfo],
    statuses: dict[str, str],
    dry_run: bool,
) -> None:
    twitch = {
        name: next((s.handle for s in info.socials if s.platform == "twitch"), None)
        for name, info in resolved.items()
    }
    print(f"{meta.name or '(unnamed tournament)'}")
    for team in teams:
        print(f"\n{team.name}  [{team.section}]")
        for person in team.persons:
            if person.is_staff:
                tag = f"staff:{person.role or '?'}"
            else:
                tag = person.role or "?"
                if person.is_sub:
                    tag += ", sub"
            if person.is_staff:
                detail = "(not fetched)"
            elif statuses.get(person.name) == "resolved":
                handle = twitch.get(person.name)
                detail = f"twitch:{handle}" if handle else "no twitch on record"
            else:
                detail = f"UNRESOLVED ({statuses.get(person.name)})"
            print(f"  {person.name:<16} {tag:<14} {detail}")

    players = [s for s in statuses.values()]
    n_resolved = sum(s == "resolved" for s in players)
    n_twitch = sum(1 for h in twitch.values() if h)
    print(
        f"\n{len(teams)} teams, {len(players)} players fetched: "
        f"{n_resolved} resolved, {n_twitch} with twitch, "
        f"{len(players) - n_resolved} unresolved"
        + (" (dry run, nothing written)" if dry_run else "")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mr-mouse-stats")
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser(
        "fetch-roster",
        help="fetch a tournament's roster + player socials from Liquipedia",
    )
    fetch.add_argument("page", help='tournament page, e.g. "MR_Ignite/2026/Mid_Season_Finals"')
    fetch.add_argument("--db", type=Path, default=Path("data/mr_mouse_stats.sqlite3"))
    fetch.add_argument("--wiki", default="marvelrivals")
    fetch.add_argument("--cache-dir", type=Path, default=Path(".cache/liquipedia"))
    fetch.add_argument("--refresh-cache", action="store_true",
                       help="ignore cached API responses (still rate-limited)")
    fetch.add_argument("--dry-run", action="store_true",
                       help="fetch and parse but write nothing to the database")
    fetch.set_defaults(func=cmd_fetch_roster)

    args = parser.parse_args(argv)
    log.setup(args.log_level)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
