"""Admin API — the only service that writes.

Every mutation here is append-only or a one-way soft-retirement; nothing
deletes and nothing overwrites a value, matching the storage model. The
four write endpoints correspond exactly to the four mutating Store methods
(`retire_social_account`, `record_social_account`, `add_settings_observation`,
`dismiss_twitch_message`). The scraper- and collector-owned Store methods are
deliberately not reachable from HTTP.

Authentication is enforced by API Gateway's Cognito JWT authorizer, which
rejects unauthorized requests before this Lambda is invoked. `require_admin`
is applied at the router level so a new endpoint cannot forget it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..admin import queries
from ..db import Store, now_utc
from .deps import caller_label, get_conn, require_admin

logger = logging.getLogger(__name__)


class ReplaceHandle(BaseModel):
    player_id: int
    new_handle: str
    old_account_id: int | None = None


class ManualObservation(BaseModel):
    """Every field optional: the point is recording whatever the reviewer
    could read off the raw message. All-empty is rejected, matching the
    dashboard's behaviour."""

    dpi: int | None = None
    sensitivity: float | None = None
    windows_sens: int | None = None
    polling_rate: int | None = None
    mouse_brand: str | None = None
    mouse_model: str | None = None

    def fields(self) -> dict[str, object]:
        values = self.model_dump()
        for key in ("mouse_brand", "mouse_model"):
            if isinstance(values[key], str):
                values[key] = values[key].strip() or None
        return values


class Written(BaseModel):
    ok: bool = True
    detail: str
    changed: dict = Field(default_factory=dict)


# Router-level dependency: authentication cannot be omitted per-endpoint.
router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/overview")
def overview(conn: db.Connection = Depends(get_conn)) -> dict:
    return {
        "counts": queries.counts(conn),
        "observations_by_source": queries.observations_by_source(conn),
        "messages_by_kind": queries.messages_by_kind(conn),
    }


@router.get("/handles")
def handles(conn: db.Connection = Depends(get_conn)) -> dict:
    return {
        "failing": queries.failing_channels(conn),
        "missing": queries.players_without_twitch(conn),
    }


@router.get("/unresolved")
def unresolved(conn: db.Connection = Depends(get_conn)) -> list[dict]:
    return queries.unresolved_players(conn)


@router.get("/candidates")
def candidates(conn: db.Connection = Depends(get_conn)) -> list[dict]:
    return queries.unparsed_candidates(conn)


@router.post("/handles/replace", response_model=Written)
def replace_handle(
    body: ReplaceHandle,
    conn: db.Connection = Depends(get_conn),
    claims: dict = Depends(require_admin),
) -> Written:
    """Retire the old handle (if given) and append the corrected one."""
    handle = body.new_handle.strip().lstrip("@#")
    if not handle:
        raise HTTPException(status_code=422, detail="handle must not be empty")
    store = Store(conn)
    now = now_utc()
    if body.old_account_id is not None:
        store.retire_social_account(body.old_account_id, now)
    recorded = store.record_social_account(
        body.player_id, "twitch", handle,
        f"https://www.twitch.tv/{handle}", now, source="manual",
    )
    store.commit()
    logger.info(
        "handle replaced",
        extra={"fields": {
            "by": caller_label(claims), "player_id": body.player_id,
            "handle": handle, "retired": body.old_account_id, "new_row": recorded,
        }},
    )
    return Written(
        detail=f"handle '{handle}' recorded; the collector joins it within a few minutes",
        changed={"handle": handle, "newly_recorded": recorded,
                 "retired_account_id": body.old_account_id},
    )


@router.post("/handles/{account_id}/retire", response_model=Written)
def retire_handle(
    account_id: int,
    conn: db.Connection = Depends(get_conn),
    claims: dict = Depends(require_admin),
) -> Written:
    store = Store(conn)
    store.retire_social_account(account_id, now_utc())
    store.commit()
    logger.info(
        "handle retired",
        extra={"fields": {"by": caller_label(claims), "account_id": account_id}},
    )
    return Written(detail="handle retired (kept as history)",
                   changed={"account_id": account_id})


@router.post("/candidates/{message_id}/observation", response_model=Written)
def manual_observation(
    message_id: int,
    body: ManualObservation,
    conn: db.Connection = Depends(get_conn),
    claims: dict = Depends(require_admin),
) -> Written:
    """Record settings a reviewer read off a message the parser could not."""
    store = Store(conn)
    row = queries.message_by_id(conn, message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown message")
    player_id = store.player_ids_by_twitch_channel().get(row["channel"])
    if player_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"no player known for channel '{row['channel']}' — fix handles first",
        )
    fields = body.fields()
    if all(value is None for value in fields.values()):
        raise HTTPException(status_code=422, detail="no values supplied")

    observation_id = store.add_settings_observation(
        player_id,
        row["observed_at"],  # inherited from the message, not "now"
        "manual",
        channel=row["channel"],
        raw_text=row["text"],
        source_message_id=message_id,
        **fields,
    )
    store.commit()
    logger.info(
        "manual observation recorded",
        extra={"fields": {"by": caller_label(claims), "message_id": message_id,
                          "player_id": player_id, "observation_id": observation_id}},
    )
    return Written(detail="manual observation recorded",
                   changed={"observation_id": observation_id, "player_id": player_id})


@router.post("/candidates/{message_id}/dismiss", response_model=Written)
def dismiss_candidate(
    message_id: int,
    conn: db.Connection = Depends(get_conn),
    claims: dict = Depends(require_admin),
) -> Written:
    store = Store(conn)
    store.dismiss_twitch_message(message_id, now_utc())
    store.commit()
    logger.info(
        "candidate dismissed",
        extra={"fields": {"by": caller_label(claims), "message_id": message_id}},
    )
    return Written(detail="candidate dismissed (row kept)",
                   changed={"message_id": message_id})


def create_app() -> FastAPI:
    app = FastAPI(
        title="mr-mouse-stats admin API",
        description="Owns every write. Behind a Cognito JWT authorizer.",
        version="1.0.0",
    )

    @app.get("/health", include_in_schema=False)
    def health() -> dict:  # unauthenticated: used by deploy smoke checks
        return {"status": "ok", "service": "admin"}

    app.include_router(router)
    return app


app = create_app()
