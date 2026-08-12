"""Public read-only API backing the stats site.

No authentication and no writes — it imports the rollups from
`site.queries` and nothing that can mutate. Deployed as its own Lambda on
its own API Gateway route, ideally with a read-only database role.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .. import config, db
from ..site import queries
from .deps import get_conn

router = APIRouter()


class Player(BaseModel):
    id: int
    liquipedia_page: str
    display_name: str
    team: str | None
    role: str | None
    country: str | None
    dpi: int | None
    sensitivity: float | None
    edpi: float | None
    mouse: str | None
    device: str | None
    observations: int
    last_observed_at: str | None


class HistoryEntry(BaseModel):
    """A stint of consecutive identical readings, collapsed."""

    first_seen_at: str
    last_seen_at: str
    times_seen: int
    source: str
    dpi: int | None
    sensitivity: float | None
    windows_sens: int | None
    mouse: str | None
    raw_text: str | None


class Bucket(BaseModel):
    label: str
    count: int


class RoleStat(BaseModel):
    role: str
    players_with_edpi: int
    median_edpi: float | None


class Metric(BaseModel):
    """Centre and spread of one setting, for the headline figures."""

    count: int
    median: float | None
    mean: float | None
    low: float | None
    high: float | None


class Stats(BaseModel):
    total_players: int
    covered_players: int
    total_teams: int
    total_observations: int
    dpi_distribution: list[Bucket]
    edpi_distribution: list[Bucket]
    mouse_popularity: list[Bucket]
    edpi: Metric
    dpi: Metric
    roles: list[RoleStat]


def _player(summary) -> Player:
    return Player(
        id=summary.db_id,
        liquipedia_page=summary.liquipedia_page,
        display_name=summary.display_name,
        team=summary.team,
        role=summary.role,
        country=summary.country,
        dpi=summary.dpi,
        sensitivity=summary.sensitivity,
        edpi=summary.edpi,
        mouse=summary.mouse,
        device=summary.device,
        observations=summary.observations,
        last_observed_at=summary.last_observed_at,
    )


@router.get("/players", response_model=list[Player])
def list_players(
    covered_only: bool = False, conn: db.Connection = Depends(get_conn)
) -> list[Player]:
    """All resolved players. `covered_only=true` drops those with no observations."""
    summaries = queries.player_summaries(conn)
    if covered_only:
        summaries = [s for s in summaries if s.observations]
    return [_player(s) for s in summaries]


@router.get("/players/{player_id}", response_model=Player)
def get_player(player_id: int, conn: db.Connection = Depends(get_conn)) -> Player:
    for summary in queries.player_summaries(conn):
        if summary.db_id == player_id:
            return _player(summary)
    raise HTTPException(status_code=404, detail="unknown player")


@router.get("/players/{player_id}/history", response_model=list[HistoryEntry])
def get_history(
    player_id: int, conn: db.Connection = Depends(get_conn)
) -> list[HistoryEntry]:
    """Settings history oldest-first, consecutive identical readings collapsed."""
    entries = queries.player_history(conn, player_id)
    return [HistoryEntry(**vars(entry)) for entry in entries]


@router.get("/stats", response_model=Stats)
def stats(conn: db.Connection = Depends(get_conn)) -> Stats:
    summaries = queries.player_summaries(conn)
    return Stats(
        total_players=len(summaries),
        covered_players=sum(1 for s in summaries if s.observations),
        total_teams=len({s.team for s in summaries if s.team}),
        total_observations=sum(s.observations for s in summaries),
        edpi=Metric(**vars(queries.edpi_metric(summaries))),
        dpi=Metric(**vars(queries.dpi_metric(summaries))),
        dpi_distribution=[
            Bucket(label=label, count=n) for label, n in queries.dpi_distribution(summaries)
        ],
        edpi_distribution=[
            Bucket(label=label, count=n)
            for label, n in queries.edpi_distribution(summaries)
        ],
        mouse_popularity=[
            Bucket(label=label, count=n) for label, n in queries.mouse_popularity(summaries)
        ],
        roles=[
            RoleStat(role=role, players_with_edpi=n, median_edpi=median)
            for role, n, median in queries.role_comparison(summaries)
        ],
    )


@router.get("/attribution")
def attribution() -> dict:
    """Liquipedia content is CC-BY-SA 3.0; consumers must surface this."""
    return {
        "source": "Liquipedia",
        "source_url": "https://liquipedia.net/marvelrivals/",
        "license": "CC-BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
    }


def create_app() -> FastAPI:
    app = FastAPI(
        title="mr-mouse-stats public API",
        description=(
            "Mouse settings and settings history for pro Marvel Rivals players. "
            "Data from Liquipedia, CC-BY-SA 3.0."
        ),
        version="1.0.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins(),
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok", "service": "public"}

    app.include_router(router)
    return app


app = create_app()
