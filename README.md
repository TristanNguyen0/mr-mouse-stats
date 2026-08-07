# mr-mouse-stats

Mouse settings (DPI, in-game sensitivity, mouse model) for professional Marvel
Rivals players competing in the Ignite circuit — including **settings history
over time**.

CLI that, given a Liquipedia tournament page, stores the roster
of players with their Twitch (and other social) handles.

```sh
# Apply schema migrations (deploy step; safe to re-run)
uv run mr-mouse-stats migrate

# Stage 1: roster + twitch handles from Liquipedia
uv run mr-mouse-stats fetch-roster "MR_Ignite/2026/Mid_Season_Finals" --dry-run
uv run mr-mouse-stats fetch-roster "MR_Ignite/2026/Mid_Season_Finals"

# Stage 2: passively observe settings-bot responses in players' chats
uv run mr-mouse-stats collect-twitch            # runs until Ctrl-C
uv run mr-mouse-stats collect-twitch --duration 3600 --dry-run

# Derive structured settings from stored raw messages (re-runnable)
uv run mr-mouse-stats parse-observations

# Settings history already published on Liquipedia player pages
uv run mr-mouse-stats ingest-liquipedia-settings

# Admin dashboard: stale/missing twitch handles, unresolved players,
# unparsed candidates — with append-only manual fixes (localhost, no auth)
uv run mr-mouse-stats admin   # http://127.0.0.1:8177/

# Render the public stats site (static HTML + inline SVG) into site/
uv run mr-mouse-stats build-site
```

## Architecture

```
Liquipedia API ─┐
                ├─► Postgres (Neon) ─┬─► public API  ─┐
Twitch IRC ─────┘                    │   (read-only)  ├─► Next.js static site
                                     │                │   (S3 + CloudFront)
                                     └─► admin API ───┘
                                         (owns every write,
                                          Cognito JWT authorizer)
```

The collector and the Liquipedia scrape share one always-on ECS Fargate task
(`mr-mouse-stats serve`): the IRC client stays connected continuously while
the scrape runs on a timer on its own thread. Both APIs are Lambdas behind
one HTTP API Gateway on separate routes.

Run the two APIs locally:

```sh
uv run uvicorn mr_mouse_stats.api.public:app --port 8000
MR_MOUSE_STATS_ADMIN_DEV_AUTH=1 uv run uvicorn mr_mouse_stats.api.admin:app --port 8001
```

`MR_MOUSE_STATS_ADMIN_DEV_AUTH=1` bypasses authentication for local work.
Without it the admin API returns 401 to everything but `/health`; in AWS the
Cognito authorizer rejects requests before the Lambda is invoked.

The frontend lives in [`frontend/`](frontend/) (`npm run dev`), and the
infrastructure in [`infra/`](infra/) — see
[`infra/README.md`](infra/README.md) for the deploy runbook.

### Legacy paths, still present

The Flask dashboard (`mr-mouse-stats admin`), the static site renderer
(`mr-mouse-stats build-site`), and the systemd unit in
[`deploy/`](deploy/mr-mouse-stats-collect.service) all still work. They are
superseded by the React admin view, the public API, and the Fargate service
respectively, but are kept until that stack is actually deployed.

## Configuration

Everything is read from the environment, with local-development defaults:

| Variable | Default | Purpose |
|---|---|---|
| `MR_MOUSE_STATS_DB` | `postgresql://postgres:postgres@localhost:55432/mr_mouse_stats` | Postgres DSN |
| `MR_MOUSE_STATS_CACHE_DIR` | `.cache/liquipedia` | on-disk API response cache |
| `MR_MOUSE_STATS_WIKI` | `marvelrivals` | Liquipedia wiki |

Every CLI subcommand also takes `--db` / `--cache-dir` / `--wiki` to override.

## Development

Storage is Postgres. Bring up a local one, then migrate:

```sh
docker run -d --name mr-mouse-pg \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=mr_mouse_stats \
  -p 55432:5432 postgres:16

uv sync
uv run mr-mouse-stats migrate
```

Tests need a **separate** database — the fixtures `TRUNCATE` every table
between tests, and refuse to run against a database whose name doesn't end
in `_test`:

```sh
docker exec mr-mouse-pg psql -U postgres -c "CREATE DATABASE mr_mouse_stats_test"
uv run pytest
```

Override with `MR_MOUSE_STATS_TEST_DSN` if your Postgres lives elsewhere.

### Importing the old SQLite database

```sh
uv run python scripts/import_from_sqlite.py --sqlite data/mr_mouse_stats.sqlite3
```

Primary keys are preserved (`settings_observations.source_message_id` and
`twitch_messages.trigger_id` depend on them) and identity sequences are
advanced past the imported maximum. Pass `--truncate` to replace existing rows.

## Data collection constraints

**Liquipedia** — use the MediaWiki API at `liquipedia.net/marvelrivals/api.php`
only; automated access to rendered HTML pages is forbidden by their ToS. Rate
limits are 1 request / 2 s in general and 1 request / 30 s for `action=parse`,
so prefer `action=query&prop=revisions` (wikitext) and batch up to 50 titles per
request. Every request must send the project's custom User-Agent with contact
info and accept gzip. All network access goes through
`mr_mouse_stats/http.py::LiquipediaClient`, which enforces this and caches
responses on disk — never add another code path that hits the network.

**Twitch** — read-only anonymous IRC
(`justinfan`, no OAuth, no account). The client must never send a message: we
passively parse bot responses triggered by other viewers, never trigger them
ourselves.

**Other sources** — prosettings.net and similar aggregators are never scraped as
a data source; at most they serve as a manual validation set.

Settings history is append-only: `social_accounts` and `settings_observations`
rows are inserted with `observed_at` and never updated in place.

## Attribution

Tournament and player data are sourced from
[Liquipedia](https://liquipedia.net/marvelrivals/) and licensed
[CC-BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Any published
output derived from this data must credit Liquipedia.
