"""Admin API: the four reads and the four writes, plus authentication.

The write scenarios are ported from the Flask dashboard's tests — the
mechanism changed (JSON, no sessions), the behaviour it specifies did not.
"""

import pytest
from fastapi.testclient import TestClient

from mr_mouse_stats import db
from mr_mouse_stats.api import deps
from mr_mouse_stats.api.admin import create_app
from mr_mouse_stats.models import TournamentMeta

# Shape API Gateway's JWT authorizer uses when it attaches verified claims.
GATEWAY_EVENT = {
    "requestContext": {
        "authorizer": {"jwt": {"claims": {"sub": "u-1", "email": "admin@example.com"}}}
    }
}


@pytest.fixture
def seeded(conn):
    store = db.Store(conn)
    tid = store.upsert_tournament(
        "T", TournamentMeta("Test Cup", None, None, None, None), "t0"
    )
    team = store.get_or_create_team("Testers")

    good = store.upsert_player_stub("GoodPlayer", "resolved", "t0")
    store.record_social_account(good, "twitch", "goodplayer", None, "t0")
    stale = store.upsert_player_stub("StalePlayer", "resolved", "t0")
    store.record_social_account(stale, "twitch", "oldhandle", None, "t0")
    store.record_social_account(stale, "twitter", "stale_tw", None, "t0")
    no_twitch = store.upsert_player_stub("NoTwitch", "resolved", "t0")
    store.record_social_account(no_twitch, "bilibili", "12345", None, "t0")
    ghost = store.upsert_player_stub("GhostPage", "not_player_page", "t0")
    store.upsert_roster_entry(tid, team, ghost, "sup", False, False, None, "Main")

    store.upsert_channel_join_status("oldhandle", False, "t1")
    store.upsert_channel_join_status("goodplayer", True, "t1")

    trig = store.record_twitch_message(
        "m-1", "2026-08-01T00:00:00+00:00", "goodplayer", "viewer",
        "Viewer", "1", "", "trigger", "!dpi",
    )
    message_id = store.record_twitch_message(
        "m-2", "2026-08-01T00:00:05+00:00", "goodplayer", "nightbot",
        "Nightbot", "2", "bot-badge/1", "bot_response",
        "some unparseable answer", trigger_id=trig,
    )
    store.commit()

    account_id = conn.execute(
        "SELECT id FROM social_accounts WHERE handle = 'oldhandle'"
    ).fetchone()["id"]
    return {
        "stale_player": stale, "no_twitch": no_twitch,
        "old_account_id": account_id, "message_id": message_id,
    }


@pytest.fixture
def app(conn):
    application = create_app()
    application.dependency_overrides[deps.get_conn] = lambda: conn
    return application


CLAIMS = GATEWAY_EVENT["requestContext"]["authorizer"]["jwt"]["claims"]


@pytest.fixture
def client(app):
    """Authenticated caller. The real require_admin is exercised separately
    below; here we stand in for what API Gateway would have provided."""
    app.dependency_overrides[deps.require_admin] = lambda: CLAIMS
    with TestClient(app) as c:
        yield c


@pytest.fixture
def anonymous(app):
    """No override: runs the real require_admin."""
    with TestClient(app) as c:
        yield c


# --- authentication ---------------------------------------------------------

def test_gateway_claims_are_trusted():
    """API Gateway validated the token before invoking us, so claims it
    attached are accepted without re-verification."""
    from starlette.requests import Request

    scope = {
        "type": "http", "method": "GET", "path": "/overview",
        "headers": [], "aws.event": GATEWAY_EVENT,
    }
    assert deps.require_admin(Request(scope)) == CLAIMS


def test_missing_claims_rejected(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    monkeypatch.delenv(deps.ENV_DEV_AUTH, raising=False)
    for event in ({}, {"requestContext": {}}, {"requestContext": {"authorizer": {}}}):
        scope = {
            "type": "http", "method": "GET", "path": "/overview",
            "headers": [], "aws.event": event,
        }
        with pytest.raises(HTTPException) as excinfo:
            deps.require_admin(Request(scope))
        assert excinfo.value.status_code == 401

@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/overview"), ("get", "/handles"),
        ("get", "/unresolved"), ("get", "/candidates"),
        ("post", "/handles/replace"), ("post", "/handles/1/retire"),
        ("post", "/candidates/1/observation"), ("post", "/candidates/1/dismiss"),
    ],
)
def test_every_route_requires_auth(anonymous, method, path):
    kwargs = {"json": {}} if method == "post" else {}
    response = getattr(anonymous, method)(path, **kwargs)
    assert response.status_code == 401
    assert "Bearer" in response.headers.get("WWW-Authenticate", "")


def test_health_is_unauthenticated(anonymous):
    assert anonymous.get("/health").json() == {"status": "ok", "service": "admin"}


def test_dev_auth_escape_hatch(anonymous, monkeypatch, seeded):
    monkeypatch.setenv(deps.ENV_DEV_AUTH, "1")
    assert anonymous.get("/overview").status_code == 200


# --- reads ------------------------------------------------------------------

def test_overview(client, seeded):
    body = client.get("/overview").json()
    assert body["counts"]["players"] == 4
    assert body["counts"]["failing_channels"] == 1
    assert body["counts"]["players_without_twitch"] == 1
    assert body["counts"]["unresolved"] == 1
    assert body["counts"]["candidates"] == 1


def test_handles_lists_failing_and_missing(client, seeded):
    body = client.get("/handles").json()
    assert [r["channel"] for r in body["failing"]] == ["oldhandle"]
    assert body["failing"][0]["other_socials"] == "twitter:stale_tw"
    assert [r["liquipedia_page"] for r in body["missing"]] == ["NoTwitch"]
    assert body["missing"][0]["other_socials"] == "bilibili:12345"


def test_unresolved(client, seeded):
    rows = client.get("/unresolved").json()
    assert [r["liquipedia_page"] for r in rows] == ["GhostPage"]
    assert rows[0]["resolution_status"] == "not_player_page"
    assert rows[0]["team_name"] == "Testers"


def test_candidates(client, seeded):
    rows = client.get("/candidates").json()
    assert [r["text"] for r in rows] == ["some unparseable answer"]
    assert rows[0]["trigger_text"] == "!dpi"


# --- writes -----------------------------------------------------------------

def test_replace_handle_retires_and_appends(client, conn, seeded):
    response = client.post("/handles/replace", json={
        "player_id": seeded["stale_player"],
        "old_account_id": seeded["old_account_id"],
        "new_handle": "@NewHandle",
    })
    assert response.status_code == 200
    rows = conn.execute(
        "SELECT handle, retired_at, source FROM social_accounts "
        "WHERE player_id = %s AND platform = 'twitch' ORDER BY id",
        (seeded["stale_player"],),
    ).fetchall()
    assert rows[0]["handle"] == "oldhandle"
    assert rows[0]["retired_at"] is not None
    assert rows[1]["handle"] == "NewHandle"  # @ stripped
    assert rows[1]["source"] == "manual"
    assert rows[1]["retired_at"] is None
    assert client.get("/handles").json()["failing"] == []


def test_add_handle_for_player_without_twitch(client, seeded):
    client.post("/handles/replace", json={
        "player_id": seeded["no_twitch"], "new_handle": "freshhandle",
    })
    assert client.get("/handles").json()["missing"] == []


def test_empty_handle_rejected(client, seeded):
    response = client.post("/handles/replace", json={
        "player_id": seeded["stale_player"], "new_handle": "  @ ",
    })
    assert response.status_code == 422


def test_retire_handle(client, conn, seeded):
    response = client.post(f"/handles/{seeded['old_account_id']}/retire")
    assert response.status_code == 200
    row = conn.execute(
        "SELECT retired_at FROM social_accounts WHERE id = %s",
        (seeded["old_account_id"],),
    ).fetchone()
    assert row["retired_at"] is not None


def test_manual_observation_from_candidate(client, conn, seeded):
    response = client.post(
        f"/candidates/{seeded['message_id']}/observation",
        json={"dpi": 800, "sensitivity": 6, "mouse_brand": "Lamzu",
              "mouse_model": "Maya"},
    )
    assert response.status_code == 200
    row = conn.execute(
        "SELECT * FROM settings_observations WHERE source = 'manual'"
    ).fetchone()
    assert row["dpi"] == 800
    assert row["source_message_id"] == seeded["message_id"]
    assert row["raw_text"] == "some unparseable answer"
    # observed_at is inherited from the message, not set to now
    assert row["observed_at"] == "2026-08-01T00:00:05+00:00"
    assert client.get("/candidates").json() == []


def test_manual_observation_with_no_values_records_nothing(client, conn, seeded):
    response = client.post(
        f"/candidates/{seeded['message_id']}/observation", json={}
    )
    assert response.status_code == 422
    assert conn.execute(
        "SELECT COUNT(*) c FROM settings_observations"
    ).fetchone()["c"] == 0


def test_manual_observation_unknown_message(client, seeded):
    assert client.post("/candidates/999999/observation",
                       json={"dpi": 800}).status_code == 404


def test_manual_observation_unknown_channel(client, conn, seeded):
    """A message from a channel with no known player is a 409, not a redirect."""
    store = db.Store(conn)
    orphan = store.record_twitch_message(
        "m-9", "2026-08-01T01:00:00+00:00", "nobodyknowsme", "nightbot",
        "Nightbot", "2", "bot-badge/1", "bot_response", "800 dpi",
    )
    store.commit()
    response = client.post(f"/candidates/{orphan}/observation", json={"dpi": 800})
    assert response.status_code == 409
    assert "fix handles first" in response.json()["detail"]


def test_dismiss_candidate(client, conn, seeded):
    response = client.post(f"/candidates/{seeded['message_id']}/dismiss")
    assert response.status_code == 200
    row = conn.execute(
        "SELECT dismissed_at FROM twitch_messages WHERE id = %s",
        (seeded["message_id"],),
    ).fetchone()
    assert row["dismissed_at"] is not None  # kept, not deleted
    assert client.get("/candidates").json() == []
    # dismissed candidates are also excluded from the parse pass
    assert db.Store(conn).unparsed_response_messages() == []
