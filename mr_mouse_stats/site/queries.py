"""Read-only rollups for the public stats site.

Derived at build time from the append-only tables; nothing here writes.
observed_at mixes plain dates (liquipedia) and ISO timestamps (twitch);
both sort correctly as strings, so "latest" is a plain string max.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median

from .. import db
from .devices import canonical_mouse


@dataclass(frozen=True)
class PlayerSummary:
    db_id: int
    liquipedia_page: str
    display_name: str
    team: str | None
    role: str | None  # primary role: first token of players.roles
    country: str | None
    dpi: int | None  # latest observation carrying the field
    sensitivity: float | None
    edpi: float | None  # dpi * sens from the latest observation carrying BOTH
    mouse: str | None  # "Brand Model" from the latest observation with a brand
    device: str | None  # `mouse` folded onto a canonical name; None if not a mouse
    observations: int
    last_observed_at: str | None


@dataclass(frozen=True)
class HistoryEntry:
    """A stint of consecutive identical observations, collapsed."""

    first_seen_at: str
    last_seen_at: str
    times_seen: int
    source: str
    dpi: int | None
    sensitivity: float | None
    windows_sens: int | None
    mouse: str | None
    raw_text: str | None


def _mouse(row: db.Row) -> str | None:
    """Either half alone is still a usable name: a dedicated !mouse command
    often answers with a bare model ("Viper V4 Pro") and no brand."""
    parts = [row["mouse_brand"], row["mouse_model"]]
    return " ".join(p for p in parts if p) or None


def _primary_role(roles: str | None) -> str | None:
    if not roles:
        return None
    return roles.split(",")[0].strip() or None


def _observations_by_player(
    conn: db.Connection,
) -> dict[int, list[db.Row]]:
    grouped: dict[int, list[db.Row]] = {}
    rows = conn.execute(
        "SELECT * FROM settings_observations ORDER BY observed_at, id"
    )
    for row in rows:
        grouped.setdefault(row["player_id"], []).append(row)
    return grouped


def player_summaries(conn: db.Connection) -> list[PlayerSummary]:
    """All resolved players (with or without observations), page order."""
    observations = _observations_by_player(conn)
    players = conn.execute(
        """
        SELECT p.id, p.liquipedia_page, p.player_id, p.country, p.roles,
               (SELECT t.name FROM roster_entries re
                JOIN teams t ON t.id = re.team_id
                WHERE re.player_id = p.id
                ORDER BY re.id DESC LIMIT 1) AS team
        FROM players p
        WHERE p.resolution_status = 'resolved'
        -- COLLATE "C" pins byte ordering so the rendered page does not
        -- depend on the server's locale (and matches the pre-Postgres site).
        ORDER BY p.liquipedia_page COLLATE "C"
        """
    ).fetchall()

    summaries = []
    for player in players:
        history = observations.get(player["id"], [])
        dpi = sensitivity = edpi = mouse = None
        for row in history:  # ascending, so later rows overwrite
            if row["dpi"] is not None:
                dpi = row["dpi"]
            if row["sensitivity"] is not None:
                sensitivity = row["sensitivity"]
            if row["dpi"] is not None and row["sensitivity"] is not None:
                edpi = round(row["dpi"] * row["sensitivity"], 1)
            if (named := _mouse(row)) is not None:
                mouse = named
        summaries.append(
            PlayerSummary(
                db_id=player["id"],
                liquipedia_page=player["liquipedia_page"],
                display_name=player["player_id"]
                or player["liquipedia_page"].rsplit("/", 1)[-1],
                team=player["team"],
                role=_primary_role(player["roles"]),
                country=player["country"],
                dpi=dpi,
                sensitivity=sensitivity,
                edpi=edpi,
                mouse=mouse,
                device=canonical_mouse(mouse),
                observations=len(history),
                last_observed_at=history[-1]["observed_at"] if history else None,
            )
        )
    return summaries


def player_history(conn: db.Connection, player_db_id: int) -> list[HistoryEntry]:
    """Observations oldest-first, consecutive identical readings collapsed
    into stints — so a change back to earlier settings stays visible."""
    rows = conn.execute(
        "SELECT * FROM settings_observations WHERE player_id = %s "
        "ORDER BY observed_at, id",
        (player_db_id,),
    ).fetchall()

    entries: list[HistoryEntry] = []
    for row in rows:
        entry = HistoryEntry(
            first_seen_at=row["observed_at"],
            last_seen_at=row["observed_at"],
            times_seen=1,
            source=row["source"],
            dpi=row["dpi"],
            sensitivity=row["sensitivity"],
            windows_sens=row["windows_sens"],
            mouse=_mouse(row),
            raw_text=row["raw_text"],
        )
        previous = entries[-1] if entries else None
        if previous is not None and (
            previous.source,
            previous.dpi,
            previous.sensitivity,
            previous.windows_sens,
            previous.mouse,
        ) == (entry.source, entry.dpi, entry.sensitivity, entry.windows_sens, entry.mouse):
            entries[-1] = HistoryEntry(
                first_seen_at=previous.first_seen_at,
                last_seen_at=entry.last_seen_at,
                times_seen=previous.times_seen + 1,
                source=previous.source,
                dpi=previous.dpi,
                sensitivity=previous.sensitivity,
                windows_sens=previous.windows_sens,
                mouse=previous.mouse,
                raw_text=previous.raw_text,
            )
        else:
            entries.append(entry)
    return entries


def dpi_distribution(summaries: list[PlayerSummary]) -> list[tuple[str, int]]:
    counts: dict[int, int] = {}
    for summary in summaries:
        if summary.dpi is not None:
            counts[summary.dpi] = counts.get(summary.dpi, 0) + 1
    return [(str(dpi), counts[dpi]) for dpi in sorted(counts)]


def edpi_distribution(
    summaries: list[PlayerSummary], bucket: int = 200
) -> list[tuple[str, int]]:
    counts: dict[int, int] = {}
    for summary in summaries:
        if summary.edpi is not None:
            low = int(summary.edpi // bucket) * bucket
            counts[low] = counts.get(low, 0) + 1
    return [(f"{low}–{low + bucket}", counts[low]) for low in sorted(counts)]


def mouse_popularity(summaries: list[PlayerSummary]) -> list[tuple[str, int]]:
    """Players per canonical mouse, most used first.

    Counts `device`, not the raw name: see `devices.canonical_mouse` for why
    the raw names cannot be counted directly.
    """
    counts: dict[str, int] = {}
    for summary in summaries:
        if summary.device is not None:
            counts[summary.device] = counts.get(summary.device, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


@dataclass(frozen=True)
class Metric:
    """Centre and spread of one numeric setting across players."""

    count: int
    median: float | None
    mean: float | None
    low: float | None
    high: float | None


def _metric(values: list[float]) -> Metric:
    if not values:
        return Metric(0, None, None, None, None)
    return Metric(
        count=len(values),
        median=round(median(values), 1),
        mean=round(mean(values), 1),
        low=min(values),
        high=max(values),
    )


def edpi_metric(summaries: list[PlayerSummary]) -> Metric:
    return _metric([s.edpi for s in summaries if s.edpi is not None])


def dpi_metric(summaries: list[PlayerSummary]) -> Metric:
    return _metric([float(s.dpi) for s in summaries if s.dpi is not None])


def role_comparison(
    summaries: list[PlayerSummary],
) -> list[tuple[str, int, float | None]]:
    """(role, players with eDPI, median eDPI), for the in-game roles."""
    by_role: dict[str, list[float]] = {}
    for summary in summaries:
        if summary.role in ("Duelist", "Vanguard", "Strategist"):
            by_role.setdefault(summary.role, [])
            if summary.edpi is not None:
                by_role[summary.role].append(summary.edpi)
    return [
        (role, len(values), round(median(values), 1) if values else None)
        for role, values in sorted(by_role.items())
    ]
