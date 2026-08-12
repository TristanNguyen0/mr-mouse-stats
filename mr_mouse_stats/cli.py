"""CLI entry point: `mr-mouse-stats fetch-roster <tournament page>`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config, db, log
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
    db_path: str,
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


def cmd_ingest_liquipedia_settings(args: argparse.Namespace) -> int:
    from .liquipedia.settings_tables import parse_mouse_settings

    conn = db.connect(args.db)
    store = db.Store(conn)
    players = store.resolved_players()
    if not players:
        logger.error("no resolved players in database: run fetch-roster first")
        return 1
    client = LiquipediaClient(
        wiki=args.wiki, cache_dir=args.cache_dir, refresh=args.refresh_cache
    )
    pages = api.fetch_pages(client, [p["liquipedia_page"] for p in players])
    counts = {"added": 0, "already_present": 0, "undated_skipped": 0}
    for player in players:
        page = pages[player["liquipedia_page"]]
        if page.missing or page.wikitext is None:
            continue
        for entry in parse_mouse_settings(page.wikitext):
            if entry.date is None:
                counts["undated_skipped"] += 1
                logger.warning(
                    "mouse settings table without date; skipped",
                    extra={"fields": {"page": page.title}},
                )
                continue
            if store.has_settings_observation(player["id"], "liquipedia", entry.date):
                counts["already_present"] += 1
                continue
            counts["added"] += 1
            logger.info(
                "liquipedia settings observation",
                extra={
                    "fields": {
                        "page": page.title, "date": entry.date, "dpi": entry.dpi,
                        "mouse": entry.brand, "dry_run": args.dry_run,
                    }
                },
            )
            if not args.dry_run:
                store.add_settings_observation(
                    player["id"], entry.date, "liquipedia",
                    dpi=entry.dpi,
                    sensitivity=entry.sensitivity,
                    windows_sens=entry.windows,
                    polling_rate=entry.polling,
                    zoom_sens=entry.zoom,
                    mouse_brand=entry.brand,
                    mouse_model=entry.model,
                    pad_brand=entry.pad_brand,
                    pad_model=entry.pad_model,
                    ref_url=entry.ref_url,
                )
    if not args.dry_run:
        store.commit()
    conn.close()
    print(
        f"{counts['added']} settings observations added, "
        f"{counts['already_present']} already present, "
        f"{counts['undated_skipped']} undated tables skipped"
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    return 0


def cmd_collect_twitch(args: argparse.Namespace) -> int:
    from .twitch.irc import ReadOnlyIrcClient
    from .twitch.runner import collect

    conn = db.connect(args.db)
    store = db.Store(conn)
    if args.channels:
        channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    else:
        channels = sorted(store.player_ids_by_twitch_channel())
    if not channels:
        logger.error("no twitch channels: run fetch-roster first or pass --channels")
        return 1
    logger.info(
        "starting passive collection",
        extra={"fields": {"channels": len(channels), "dry_run": args.dry_run}},
    )
    client = ReadOnlyIrcClient(channels)
    try:
        stats = collect(
            client, store=None if args.dry_run else store, duration=args.duration
        )
    except KeyboardInterrupt:
        stats = None
        print("\ninterrupted")
    conn.close()
    if stats is not None:
        print(
            f"observed {stats['messages_seen']} messages: "
            f"{stats['trigger']} triggers, {stats['bot_response']} bot responses, "
            f"{stats['broadcaster_response']} broadcaster responses"
            + (" (dry run, nothing written)" if args.dry_run else "")
        )
    if client.unconfirmed_joins:
        print(f"never joined (stale handles?): {', '.join(sorted(client.unconfirmed_joins))}")
    return 0


def cmd_fetch_nightbot(args: argparse.Namespace) -> int:
    from .http import NightbotClient
    from .nightbot import api as nightbot_api

    conn = db.connect(args.db)
    store = db.Store(conn)
    channel_players = store.player_ids_by_twitch_channel()
    if args.channels:
        channels = [c.strip().lower() for c in args.channels.split(",") if c.strip()]
    else:
        channels = sorted(channel_players)
    if not channels:
        logger.error("no twitch channels: run fetch-roster first or pass --channels")
        return 1

    client = NightbotClient(cache_dir=args.cache_dir, refresh=args.refresh_cache)
    now = db.now_utc()
    counts = {"channels": 0, "registered": 0, "commands": 0, "new": 0}
    for result in nightbot_api.fetch_channels(client, channels):
        counts["channels"] += 1
        counts["registered"] += int(result.registered)
        counts["commands"] += len(result.commands)
        logger.info(
            "nightbot channel",
            extra={
                "fields": {
                    "channel": result.channel,
                    "registered": result.registered,
                    "commands": len(result.commands),
                    "dry_run": args.dry_run,
                }
            },
        )
        if args.dry_run:
            for command in result.commands:
                logger.info(
                    "nightbot command",
                    extra={
                        "fields": {
                            "channel": result.channel,
                            "name": command.name,
                            "message": command.message,
                            "updated_at": command.updated_at,
                        }
                    },
                )
            continue
        for command in result.commands:
            row_id = store.record_bot_command(
                nightbot_api.BOT,
                command.channel,
                command.command_id,
                command.name,
                command.message,
                command.updated_at,
                now,
                bot_channel_id=command.bot_channel_id,
            )
            if row_id is not None:
                counts["new"] += 1
        store.upsert_bot_channel_status(
            nightbot_api.BOT,
            result.channel,
            result.registered,
            result.bot_channel_id,
            len(result.commands),
            now,
        )
        # Commit per channel: the run is long and rate-gated, and an
        # interrupt halfway through should keep what it already read.
        store.commit()
    conn.close()
    print(
        f"{counts['channels']} channels checked, {counts['registered']} with nightbot, "
        f"{counts['commands']} settings commands seen, {counts['new']} new versions stored"
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    return 0


def cmd_parse_bot_commands(args: argparse.Namespace) -> int:
    from .nightbot.api import BOT, PAGE_URL
    from .nightbot.parse import parse_command

    conn = db.connect(args.db)
    store = db.Store(conn)
    channel_players = store.player_ids_by_twitch_channel()
    counts = {"parsed": 0, "unparseable": 0, "unknown_channel": 0}
    for row in store.unparsed_bot_commands():
        parsed = parse_command(row["name"], row["message"])
        if parsed is None:
            counts["unparseable"] += 1
            continue
        player_id = channel_players.get(row["channel"])
        if player_id is None:
            counts["unknown_channel"] += 1
            logger.warning(
                "bot command in channel with no known player",
                extra={"fields": {"channel": row["channel"]}},
            )
            continue
        counts["parsed"] += 1
        logger.info(
            "settings observation",
            extra={
                "fields": {
                    "channel": row["channel"],
                    "command": row["name"],
                    "dpi": parsed.dpi,
                    "sensitivity": parsed.sensitivity,
                    "mouse": parsed.mouse_model or parsed.mouse_brand,
                    "dry_run": args.dry_run,
                }
            },
        )
        if not args.dry_run:
            store.add_settings_observation(
                player_id,
                # The bot's own last-edited time, not when we read it: this
                # is when the player actually changed the setting.
                row["updated_at"] or row["first_fetched_at"],
                row["bot"],
                channel=row["channel"],
                raw_text=row["message"],
                dpi=parsed.dpi,
                sensitivity=parsed.sensitivity,
                windows_sens=parsed.windows_sens,
                mouse_brand=parsed.mouse_brand,
                mouse_model=parsed.mouse_model,
                pad_brand=parsed.pad_brand,
                pad_model=parsed.pad_model,
                ref_url=(
                    PAGE_URL.format(channel=row["channel"])
                    if row["bot"] == BOT
                    else None
                ),
                source_command_id=row["id"],
            )
    if not args.dry_run:
        store.commit()
    conn.close()
    print(
        f"{counts['parsed']} observations parsed, "
        f"{counts['unparseable']} commands unparseable (kept for re-parse), "
        f"{counts['unknown_channel']} in unknown channels"
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    return 0


def cmd_parse_observations(args: argparse.Namespace) -> int:
    from .twitch.derive import derive_observations, format_counts

    conn = db.connect(args.db)
    store = db.Store(conn)
    counts = derive_observations(
        store, reparse=args.reparse, dry_run=args.dry_run
    )
    if not args.dry_run:
        store.commit()
    conn.close()
    print(format_counts(counts, dry_run=args.dry_run))
    return 0


def cmd_build_site(args: argparse.Namespace) -> int:
    from .site.build import build_site

    conn = db.connect(args.db)
    pages = build_site(conn, args.out, generated_at=db.now_utc()[:10])
    conn.close()
    print(f"wrote {pages} pages to {args.out}/")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .service import run_service

    return run_service()


def cmd_migrate(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    applied = db.apply_migrations(conn)
    conn.close()
    print(
        "applied: " + ", ".join(applied)
        if applied
        else "database already up to date"
    )
    return 0


def cmd_admin(args: argparse.Namespace) -> int:
    from .admin.app import create_app

    app = create_app(str(args.db))
    logger.info(
        "admin dashboard starting",
        extra={"fields": {"url": f"http://{args.host}:{args.port}/"}},
    )
    app.run(host=args.host, port=args.port, debug=False)
    return 0


def _add_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=config.db(),
        help=f"postgres DSN (env {config.ENV_DB})",
    )


def _add_liquipedia_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wiki",
        default=config.wiki(),
        help=f"liquipedia wiki (env {config.ENV_WIKI})",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(config.cache_dir()),
        help=f"on-disk API response cache (env {config.ENV_CACHE_DIR})",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="ignore cached API responses (still rate-limited)",
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
    _add_db_arg(fetch)
    _add_liquipedia_args(fetch)
    fetch.add_argument("--dry-run", action="store_true",
                       help="fetch and parse but write nothing to the database")
    fetch.set_defaults(func=cmd_fetch_roster)

    ingest = sub.add_parser(
        "ingest-liquipedia-settings",
        help="parse {{Mouse settings table}} from resolved players' pages",
    )
    _add_db_arg(ingest)
    _add_liquipedia_args(ingest)
    ingest.add_argument("--dry-run", action="store_true")
    ingest.set_defaults(func=cmd_ingest_liquipedia_settings)

    twitch = sub.add_parser(
        "collect-twitch",
        help="passively observe settings-bot responses in players' Twitch chats",
    )
    _add_db_arg(twitch)
    twitch.add_argument("--duration", type=float, default=0.0,
                        help="stop after N seconds (default: run until Ctrl-C)")
    twitch.add_argument("--channels",
                        help="comma-separated channel override (default: all "
                             "twitch handles in the database)")
    twitch.add_argument("--dry-run", action="store_true",
                        help="log capture events but write nothing")
    twitch.set_defaults(func=cmd_collect_twitch)

    parse = sub.add_parser(
        "parse-observations",
        help="derive settings_observations from stored raw twitch messages",
    )
    _add_db_arg(parse)
    parse.add_argument("--reparse", action="store_true",
                       help="rebuild every derived observation from the raw "
                            "text, not just messages never parsed before; use "
                            "after a parser fix (manual rows are left alone)")
    parse.add_argument("--dry-run", action="store_true",
                       help="show what would be parsed without writing")
    parse.set_defaults(func=cmd_parse_observations)

    nightbot = sub.add_parser(
        "fetch-nightbot",
        help="read players' Nightbot public command pages for settings commands",
    )
    _add_db_arg(nightbot)
    nightbot.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(config.nightbot_cache_dir()),
        help=f"on-disk API response cache (env {config.ENV_NIGHTBOT_CACHE_DIR})",
    )
    nightbot.add_argument("--refresh-cache", action="store_true",
                          help="ignore cached API responses (still rate-limited)")
    nightbot.add_argument("--channels",
                          help="comma-separated channel override (default: all "
                               "twitch handles in the database)")
    nightbot.add_argument("--dry-run", action="store_true",
                          help="fetch and log commands but write nothing")
    nightbot.set_defaults(func=cmd_fetch_nightbot)

    parse_commands = sub.add_parser(
        "parse-bot-commands",
        help="derive settings_observations from stored raw bot commands",
    )
    _add_db_arg(parse_commands)
    parse_commands.add_argument("--dry-run", action="store_true",
                                help="show what would be parsed without writing")
    parse_commands.set_defaults(func=cmd_parse_bot_commands)

    build = sub.add_parser(
        "build-site", help="render the public stats site as static HTML"
    )
    _add_db_arg(build)
    build.add_argument("--out", type=Path, default=Path("site"),
                       help="output directory (default: site/)")
    build.set_defaults(func=cmd_build_site)

    serve = sub.add_parser(
        "serve",
        help="run the long-lived service: twitch collector + timed liquipedia scrape",
    )
    serve.set_defaults(func=cmd_serve)

    migrate = sub.add_parser(
        "migrate", help="apply pending schema migrations (deploy step)"
    )
    _add_db_arg(migrate)
    migrate.set_defaults(func=cmd_migrate)

    admin = sub.add_parser(
        "admin", help="run the localhost admin dashboard (no auth — do not expose)"
    )
    _add_db_arg(admin)
    admin.add_argument("--host", default="127.0.0.1")
    admin.add_argument("--port", type=int, default=8177)
    admin.set_defaults(func=cmd_admin)

    args = parser.parse_args(argv)
    log.setup(args.log_level)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
