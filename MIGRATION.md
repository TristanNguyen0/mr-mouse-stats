# Migration: SQLite/CLI → Neon Postgres + AWS

Survey of the current codebase against the target architecture. Written before any code
changes; every file and line reference is against the tree at commit `e01c8a6`.

**Target:** Neon Postgres; an always-on ECS Fargate task running both the Twitch collector
(continuous) and the Liquipedia scrape (on an internal timer); two FastAPI Lambdas (public
read-only, admin write-owning) behind separate API Gateway routes, admin gated by a Cognito
JWT authorizer; Next.js static export on S3 behind CloudFront, absorbing the review
dashboard as an authenticated admin view.

**Decisions already taken** (both reverse earlier recommendations in this document; the
rationale for each is kept in place so the reasoning stays auditable):

- **`observed_at` stays `TEXT`.** See §1.4.
- **No scraper Lambda.** The Liquipedia scrape rides along in the Fargate task on a timer
  rather than becoming a scheduled Lambda. See §2.2 and §2.3.

**Progress: Phases 0–2 are done.** Config is environment-driven (`mr_mouse_stats/config.py`),
the DAL runs on psycopg3 against Postgres, `schema.sql` and `_migrate()` are replaced by
versioned migrations in `mr_mouse_stats/migrations/` applied by `mr-mouse-stats migrate`,
and `scripts/import_from_sqlite.py` moved all 1,671 legacy rows across with primary keys
intact. 127 tests green; `build-site` reproduces the committed site byte-for-byte apart
from its generated-on date. Two findings that emerged during execution are recorded in
§7. Next up is Phase 3.

---

## 1. Every place SQLite is touched

### 1.1 Connection handling — `mr_mouse_stats/db.py`

| Line | What |
|---|---|
| `db.py:10` | `import sqlite3` |
| `db.py:22-31` | `connect()` — the only connection factory |
| `db.py:24` | `Path(path).parent.mkdir(...)` — filesystem side effect |
| `db.py:25` | `sqlite3.connect(path)` |
| `db.py:26` | `conn.row_factory = sqlite3.Row` |
| `db.py:27` | `PRAGMA foreign_keys = ON` — no Postgres equivalent needed; FKs are always enforced |
| `db.py:28-29` | `resources.files(...).read_text()` + `conn.executescript(schema)` |

**The most important thing here:** `connect()` runs the *entire* schema DDL and the
migration pass on **every single call**. That is invisible against a local file. Against
Neon it means every Lambda cold start, and — because `admin/app.py:19-21` connects
per-request — potentially every HTTP request issues a dozen `CREATE TABLE IF NOT EXISTS`
statements plus three catalog introspections over the network. It also forces the runtime
role to hold DDL privileges, which it should not.

Schema management must move out of the request path entirely: a versioned migration tool
(Alembic, or plain numbered `.sql` files applied by a deploy step) run by CI/deploy under
a separate role, with the runtime role holding DML only.

### 1.2 The ad-hoc migration mechanism — `db.py:34-62`

`_migrate()` is pure SQLite: three `PRAGMA table_info(...)` introspections
(`db.py:38, 51, 58`) driving conditional `ALTER TABLE ... ADD COLUMN`. It backfills three
columns onto `settings_observations` (`source_message_id`, `polling_rate`, `zoom_sens`),
`retired_at` onto `social_accounts`, and `dismissed_at` onto `twitch_messages`.

This whole function is **deleted**, not ported. Its accumulated effect gets folded into
the baseline Postgres schema, and future changes go through real migrations. The two
tests that exercise it (§4) get deleted with it.

### 1.3 Schema DDL — `mr_mouse_stats/schema.sql`

**The good news first:** there is no `AUTOINCREMENT`, no `INSERT OR REPLACE`, and no
`INSERT OR IGNORE` anywhere in the repo — grepped, zero hits. The DAL already uses
standard `ON CONFLICT ... DO UPDATE` / `DO NOTHING` with `RETURNING`, which is Postgres
syntax that SQLite happens to also support. The header comment on `schema.sql:1-3` ("Keep
DDL portable — Postgres is a possible later swap") was honoured. This removes the single
largest category of expected work.

What does need changing:

**Surrogate keys.** Every table uses `id INTEGER PRIMARY KEY` (`schema.sql:6, 17, 22, 36,
49, 62, 85`). In SQLite that is a `rowid` alias and auto-assigns. In Postgres it is a
plain non-defaulting integer column and every insert would fail on a null id. All seven
become `GENERATED ALWAYS AS IDENTITY` (preferred over `SERIAL`).

**Booleans stored as integers.** `is_sub`, `is_staff`, `played` (`schema.sql:41-43`) and
`confirmed` (`schema.sql:103`). The DAL compensates with explicit casts at
`db.py:184-186` (`int(is_sub)`, `int(is_staff)`, `None if played is None else int(played)`)
and `db.py:307` (`int(confirmed)`). `played` is a deliberate three-state
true/false/unknown and must stay nullable.

Recommend converting these to real `BOOLEAN`, dropping the `int()` casts, and letting
psycopg adapt Python bools natively. If you do, two read sites must change in lockstep:
`admin/app.py:45` and `admin/app.py:99` both say `WHERE confirmed = 0`, which becomes
`WHERE NOT confirmed`. Leaving them as `INTEGER` also works and is a smaller diff — but
then the `int()` casts are load-bearing forever, because psycopg will bind a Python
`bool` as `boolean` and Postgres will *not* implicitly cast that to `integer` on insert.

**`REAL` → `double precision`** for `sensitivity` and `zoom_sens` (`schema.sql:69, 76`).

**Nullable-unique.** `msg_id TEXT UNIQUE` (`schema.sql:86`) relies on NULLs being
distinct so that untagged messages don't collide — asserted by
`test_db_twitch.py::test_null_msg_ids_do_not_collide`. Postgres defaults to `NULLS
DISTINCT`, so this behaviour carries over unchanged. Just don't opt into `NULLS NOT
DISTINCT` (PG15+) when writing the new DDL.

### 1.4 The `observed_at` typing problem — **decided: stays `TEXT`**

`observed_at` is `TEXT` and deliberately carries **two different precisions**:

- Twitch observations: full ISO-8601, e.g. `2026-08-01T09:35:42+00:00`
- Liquipedia observations: bare dates, e.g. `2023-03-29`, `2020-05-04`

Both are present in the live database right now (27 twitch rows, 3 liquipedia rows).
`site/queries.py:5-6` documents the assumption explicitly: *"observed_at mixes plain dates
(liquipedia) and ISO timestamps (twitch); both sort correctly as strings, so 'latest' is a
plain string max."* `player_summaries` (`queries.py:92-100`) and `player_history`
(`queries.py:124-128`) both depend on that ordering for "latest observation wins".

There is also a **correctness trap**: `has_settings_observation` (`db.py:323-331`) does
`WHERE ... AND observed_at = ?` with a bare date string, and it is the *only* idempotence
guard for `ingest-liquipedia-settings` (`cli.py:211`). If `observed_at` becomes
`timestamptz` and that comparison isn't updated, the guard silently stops matching and
every re-run duplicates every Liquipedia observation into an append-only table.

**Decision: keep `observed_at` as `TEXT`.** Postgres `text` sorts ISO-8601 lexicographically
in exactly the same order SQLite does, so every existing query — the string-max "latest
wins" in `player_summaries`, the `ORDER BY observed_at, id` in `player_history`, and the
`observed_at = ?` idempotence guard in `has_settings_observation` — carries over
**unchanged**. The trap described above is defused by not disturbing it.

What this buys: a materially smaller Phase 1 diff, no data conversion during the Neon
migration, the string-sort comment at `queries.py:5-6` stays accurate, and breakage class D
in §4.5 (51 fixture timestamps) disappears entirely.

What it costs, and should be revisited if either becomes painful:

- No date arithmetic in SQL. "Observations in the last 30 days" needs a
  `observed_at >= '2026-07-08'` string comparison rather than an interval. Works, reads
  poorly.
- No type safety on ingest. Postgres will accept `'not a date'` into the column just as
  SQLite does. If this matters, a `CHECK (observed_at ~ '^\d{4}-\d{2}-\d{2}')` regex
  constraint gets most of the protection for one line of DDL and no code changes — worth
  adding during Phase 1.
- The two-precision ambiguity stays implicit rather than modelled. The public API will need
  to infer "is this a date or a timestamp?" from string length when deciding how to render
  it. That inference is already what the Jinja templates do implicitly today.

*(The alternative considered and rejected: `timestamptz` plus an `observed_precision`
column, migrating bare dates to midnight UTC. Correct-by-construction ordering and real
date arithmetic, at the cost of a data conversion, updating `has_settings_observation` to
compare on `observed_at::date`, and fixing 51 test fixtures. Worth reconsidering only if
date arithmetic starts showing up in the public API's query patterns.)*

### 1.5 SQLite-specific SQL in queries

- **`GROUP_CONCAT(expr, sep)`** — `admin/app.py:90` and `admin/app.py:110`, both building
  an `other_socials` summary string. Postgres spells this `string_agg(expr, sep)`.
  Argument order is the same and both return `NULL` over an empty set, so the surrounding
  logic is unaffected. Note `GROUP_CONCAT` has no deterministic order in either engine —
  if the admin UI wants stable output, add `ORDER BY` inside the aggregate, which Postgres
  supports and SQLite does not.
- **`LOWER(handle)`** — `db.py:284`, `admin/app.py:96`. Portable, but note that
  `LOWER()` in a join predicate (`admin/app.py:96`) blocks index use in Postgres. Worth a
  functional index `ON social_accounts (LOWER(handle))` given this runs on every
  `/handles` page load.
- **`||` string concatenation** — portable.
- **`row["col"]` access** — `sqlite3.Row` supports both `row["name"]` and `row[1]`.
  psycopg3's `dict_row` factory gives dicts, which support name access only. The DAL and
  site queries use name access exclusively, so they port cleanly; two *tests* use
  positional access, and both die with the PRAGMA tests anyway (§4).

### 1.6 Cursor-result idioms

| Site | Idiom | Postgres |
|---|---|---|
| `db.py:236` | `cursor.lastrowid` | psycopg has no `lastrowid`; add `RETURNING id` and `fetchone()` |
| `db.py:264` | `cursor.lastrowid if cursor.rowcount == 1 else None` | `RETURNING id`; `fetchone()` returns `None` when `ON CONFLICT DO NOTHING` suppressed the insert — which is *cleaner* than the current form |
| `db.py:211` | `cursor.rowcount == 1` | `rowcount` is accurate for `DO NOTHING` in psycopg3; can stay, or switch to `RETURNING` for symmetry |

### 1.7 Parameter style

Every statement uses `?` placeholders. psycopg3 uses `%s`. This is a mechanical
find-and-replace across roughly 30 statements in `db.py`, `admin/app.py`,
`site/queries.py`, and `admin/actions.py` — but it is *silent* if missed in a rarely-hit
branch, so it's worth a grep for `?` inside string literals as a completion check rather
than trusting the tests to catch it.

### 1.8 Type-modules used for annotations

`site/queries.py:10`, `site/build.py:10`, and `admin/app.py:7` import `sqlite3` purely for
`sqlite3.Connection` / `sqlite3.Row` type hints. Replace with a protocol or psycopg types.
No behavioural change, but they're the reason `import sqlite3` shows up in modules that
otherwise contain no SQLite-specific code.

---

## 2. Long-lived-process and local-filesystem assumptions

### 2.1 The disk cache — `http.py:109-126` *(known)*

`_cache_path` / `_read_cache` / `_write_cache` write `.cache/liquipedia/<wiki>/<sha>.json`
via `mkdir(parents=True)` + write-temp + atomic `replace()`. TTL 24h (`http.py:30`).

CLAUDE.local.md makes this a **hard constraint**, not an optimization: *"Cache responses on
disk; dev iterations must not re-hit the API."*

**Under the scheduled-Lambda target this was a required fix**, because `/tmp` survives only
warm invocations on the same execution environment — a daily schedule is cold essentially
every time, giving a ~0% hit rate and re-hitting Liquipedia on every run.

**Running the scrape in the long-lived Fargate task largely defuses this too.** The
container filesystem persists for the life of the task, so the 24 h TTL behaves the same way
it does under systemd today. The cache is lost on task restart (deploys, crashes), which
costs at most one extra full refresh — 5 requests, ~10 seconds. Tolerable.

So the S3 backend drops from *required* to *nice-to-have*. It is still worth doing, and the
shape is unchanged: keep `LiquipediaClient`'s interface exactly as-is and inject a storage
backend (`get(key) -> dict | None` / `put(key, dict)`), with a local-filesystem
implementation for dev and an S3 implementation for the task. That preserves the existing
cache tests almost unchanged (§4), keeps the "one module touches the network" invariant
intact, and makes the cache survive restarts. **Do not** rewrite `LiquipediaClient` around
boto3 directly — the local backend is what keeps the dev-iteration constraint satisfiable
and the tests fast.

A mounted EFS volume would also work and needs no code change at all, but at this cache
size (140 KB today) it is not worth the VPC plumbing.

### 2.2 The rate gate — `http.py:66-67, 94-107` — **defused by the no-Lambda decision**

`_last_request` and `_last_parse` are **per-instance, in-memory, monotonic-clock** state.
`_throttle()` enforces 1 req/2s general and 1 req/30s for `action=parse` by sleeping.

Under a scheduled Lambda this was the highest-risk item in the migration: the gate does not
survive the process, so each invocation starts with `_last_request = None` and fires
immediately. Two overlapping invocations — from an EventBridge retry, a manual test invoke,
or a schedule firing while the previous run is still going — each believe they are alone
and collectively exceed the documented limit. The failure mode is external and
irreversible: the project's User-Agent gets blocked by Liquipedia, which violates a hard
constraint in CLAUDE.local.md and no rollback undoes it. It would also have needed
cross-invocation state (DynamoDB or a single-row Postgres table) plus reserved concurrency
of 1 to fix, and new test coverage — the only genuinely novel test the migration demanded.

**Running the scrape inside the long-lived Fargate task removes this entire risk category.**
One process, one `LiquipediaClient`, one monotonic clock — the gate is correct by
construction, exactly as it is today under systemd. No distributed coordination, no new
infrastructure, no new tests.

Two conditions to preserve that property:

- **One long-lived `LiquipediaClient`, constructed once at task startup**, not per timer
  fire. A fresh instance resets `_last_request` to `None`. In practice fires are hours
  apart so it wouldn't matter, but a shared client makes the invariant hold by
  construction instead of by luck.
- **Never run two scrapes concurrently.** A single timer thread is inherently serial; just
  don't add a second trigger path (e.g. an admin "refresh now" endpoint) without a lock.

`self._sleep(wait)` blocking for ~10 seconds is now free rather than billed — but see §2.3,
because *what thread it blocks* becomes the thing that matters.

### 2.3 The Twitch collector — `twitch/irc.py`, `twitch/runner.py` ← **not a Lambda**

- `irc.py:108-135` — `run()` is an infinite generator over a persistent TLS socket, with
  its own reconnect/backoff loop capped at 60s (`irc.py:32`).
- `irc.py:84-88` — JOINs are paced 15 per 10s. With 56 active channels that is **~40
  seconds of every cold start spent joining** before a single message can be observed.
- `runner.py:26-31` — `collect(duration=0)` means "run forever"; the systemd unit passes no
  duration.
- `runner.py:35, 93-96` — `trigger_row_ids` is an in-memory dict (capped at 1000) mapping
  trigger messages to their DB row ids.
- `capture.py:34` — `Correlator._windows` holds a 20-second per-channel correlation window.

The value proposition is *being present in chat at the moment a viewer types `!dpi`*. A
scheduled Lambda would miss everything between invocations and spend a large fraction of
each invocation on JOIN pacing. **Decision: ECS Fargate, ARM64, always-on, public subnet
with `assignPublicIp: ENABLED` and no inbound rules** (the client is outbound-only and
structurally cannot send a `PRIVMSG` — `irc.py:29, 67-75`). Public subnet specifically to
avoid a NAT Gateway, which at $32.85/month would cost 3× the ~$11/month task.

The in-memory correlation state is fine on Fargate — it's the same single-process model as
today. It is only fatal under a per-message invocation model.

#### Co-hosting the scrape with the collector

The Liquipedia scrape now runs on a timer **inside this same task**. The two are
independent concerns that happen to share a container: the IRC client stays connected
continuously and observes 24/7, while the timer fires the scrape every N hours. **The timer
does not gate, pause, or sample chat observation** — that is worth stating explicitly
because it is an easy thing to assume.

Four things do affect chat coverage, none of which is the timer:

1. **The scrape must run on its own thread.** This is the one real trap in co-hosting.
   `_read_lines()` (`irc.py:94-106`) is a blocking `recv` with a 5s timeout, and
   `_throttle()` (`http.py:103`) calls `time.sleep()`. Running the scrape inline in the
   `collect()` loop stops the socket read for ~10 seconds, delaying the PONG reply
   (`irc.py:141-143`) and risking a server-side disconnect. Run it on a separate thread
   with **its own database connection** — psycopg connections are not thread-safe, and
   `runner.collect()` already holds one.
2. **Restart gaps, ~40 s each.** `JOIN_BATCH = 15` per `JOIN_INTERVAL = 10.0`
   (`irc.py:30-31`) means 56 channels take 4 batches: ~30 s of JOIN pacing plus connect
   time before the first message can be observed. Every deploy, crash, or Fargate platform
   event costs that. Already true of the systemd unit; not worsened by this architecture,
   but it argues for infrequent deploys of this task and for `SIGTERM` handling that
   doesn't add restarts of its own.
3. **New handles are not joined until restart.** `cmd_collect_twitch` reads the channel
   list **once** at startup (`cli.py:256-259`); the admin flash message already concedes
   this (`actions.py:34-35`: "restart the collector to pick it up"). Co-hosting makes it
   sharper — the timer can discover a new player's Twitch handle at 03:00 and the collector
   won't be in that channel until something restarts it. Worth fixing here: `_send` already
   permits `JOIN` (`irc.py:29`), so periodically re-reading
   `player_ids_by_twitch_channel()` and incrementally joining new channels (respecting
   `JOIN_BATCH`/`JOIN_INTERVAL`) closes the loop between the two halves of the task.
4. **Coverage is opportunistic by design, and this dominates everything above.** A response
   only exists if a *viewer* types `!dpi` — the client never triggers one, by hard
   constraint. Current yield is 1,217 captured messages → 27 Twitch-sourced observations.
   Against that, tens of seconds lost to a restart is noise. Do not over-engineer items 2
   and 3.

### 2.4 Everything else that assumes local disk or a stable process

- **`db.py:24`** — `connect()` creates the parent directory of the DB file. Gone with
  SQLite.
- **`cli.py:379, 392, 403, 417, 425, 433`** — six subcommands default `--db` to the
  relative path `data/mr_mouse_stats.sqlite3`, resolved against the current working
  directory. Lambda's CWD is `/var/task` and read-only; the Fargate container's CWD depends
  on the image's `WORKDIR`. Neither should be relied on — all config comes from environment
  variables.
- **`http.py:52`** — `cache_dir` defaults to the relative `.cache/liquipedia`. Same issue.
- **`site/build.py:35-44`** — `out_dir.mkdir()` and `path.write_text(html)` write a
  directory tree of static HTML. Superseded by the Next.js frontend; the *rollups* in
  `site/queries.py` survive as the public API's query layer, but `build.py`, the Jinja
  templates, and `site/svg.py` are replaced by React.
- **`admin/app.py:17`** — `app.secret_key = os.urandom(16)`, regenerated per process. Flash
  messages break across instances. Moot once the admin surface is a JSON API with no
  server-side sessions, but it's a concrete symptom of single-process assumptions.
- **`admin/app.py:19-30`** — per-request `db.connect()` (which, again, runs the full schema
  DDL) with teardown-time `close()`. Needs to become a module-scope connection or pool
  initialized outside the handler, using Neon's **pooled** endpoint.
- **`db.py:28`** `resources.files("mr_mouse_stats")` and **`site/build.py:28`**
  `PackageLoader("mr_mouse_stats.site")` — both read package data (`schema.sql`, Jinja
  templates) at runtime. Fine, but the Lambda bundle/container must include non-`.py`
  package data; a naive "copy the .py files" packaging step breaks both.
- **`log.py:18`** — `time.localtime(record.created)` formats timestamps in **local** time.
  Set `TZ=UTC` in the Lambda and Fargate environments or logs will be inconsistent with
  everything else. (Minor, but free to fix.)
- **`runner.py:97`** — `store.commit()` fires **per captured message**. Against a local
  file that's free; against Neon it's a network round trip each time. At current volume
  (~1,200 messages total) this is not a problem, but it's worth batching if capture rates
  grow.
- **`cli.py:272`** — `KeyboardInterrupt` handling as the primary shutdown path. On Fargate,
  shutdown arrives as `SIGTERM`; add a handler so in-flight work commits cleanly.

---

## 3. Scraper runtime vs the 15-minute ceiling

**A full refresh fits with roughly three orders of magnitude to spare. No chunking is
needed.** This section was written against the scheduled-Lambda target; the conclusion is
what made the no-Lambda decision easy, so the analysis is kept as the justification.

The scrape is not I/O-bound or CPU-bound — it is bound almost entirely by the deliberate
2-second rate gate.

**Request count.** `api.fetch_pages` (`liquipedia/api.py:20-68`) batches **50 titles per
request** (`api.py:13`). For the current dataset:

| Command | Requests | Gated time |
|---|---|---|
| `fetch-roster` (1 tournament page + 80 non-staff players → 2 chunks) | 3 | ~4 s |
| `ingest-liquipedia-settings` (71 resolved players → 2 chunks) | 2 | ~2 s |
| **Full refresh** | **5** | **~10 s wall clock** |

**Payload is negligible.** Measured from the on-disk cache: a 50-page wikitext response is
**35 KB** of JSON; the largest single-page response is 17 KB. Average player wikitext is
~2.4 KB. There is no memory or response-size pressure anywhere near these sizes.

**Headroom.** At 2 s/request, 15 minutes allows ~450 gated requests — about **22,000
player titles**, or ~450 tournament pages. The Ignite circuit is ~86 people across 10
teams. Even a 10× expansion of scope finishes in well under a minute.

**`parse-observations`** (`cli.py:288-344`) is pure database work over 1,217 stored
messages — seconds, and it scales with capture volume rather than with API calls.

**What to actually watch, since runtime isn't the constraint:**

- **Don't parallelize.** The obvious "optimization" of concurrent fetches directly
  violates the rate limit. The 15-minute ceiling is not a reason to speed anything up.
- **`action=parse` would change the picture.** At 1 request/30 s, 15 minutes is only ~30
  requests. Nothing uses it today (correctly — CLAUDE.local.md prefers
  `action=query&prop=revisions`), and it should stay that way.
- **Bound the scrape anyway.** In the Fargate task there is no platform timeout to save
  you: a wedged scrape thread just sits there. Wrap the timer body in a watchdog (~2
  minutes) and log loudly on expiry — a run that exceeds that means the rate gate is stuck
  or Liquipedia is throttling.
- **A failed scrape must not kill the task.** The collector is the valuable half of this
  process. Catch and log exceptions at the timer-thread boundary so a Liquipedia outage
  costs one skipped refresh rather than a restart plus a 40 s JOIN gap (§2.3).
- **Timer interval: daily is ample.** Rosters change on tournament boundaries, not hourly.
  Given the 24 h cache TTL (`http.py:30`), anything more frequent mostly serves cache hits
  anyway. Hourly would be 120 requests/day against Liquipedia for near-zero new data —
  avoid it.
- **If scope ever grows past the ceiling,** the natural chunk boundary is already present:
  `fetch_pages`' 50-title chunks (`api.py:33`). Persist a cursor of resolved page titles and
  process N chunks per fire. Contingency, not planned work.

---

## 4. Test suite coverage and Postgres breakage

**129 tests across 17 files**, all green at `e01c8a6`. They split cleanly into "doesn't
touch a database" (73 tests — the majority, and unaffected) and "touches a database" (48
tests, needing a new fixture strategy), plus `test_http_client.py`'s 8 which are adjacent
(§4.7).

### 4.1 Unaffected — pure parser/protocol tests against fixtures

These never open a database and port with **zero changes**. They are also the bulk of the
project's real coverage:

| File | Tests | Covers |
|---|---|---|
| `test_settings_parse.py` | 18 | Twitch bot-response settings text parsing — DPI-grid heuristics, bare decimals, polling-rate confusion, ambiguity rejection |
| `test_twitch_frames.py` | 10 | IRC line/tag parsing against real captured frames |
| `test_twitch_capture.py` | 11 | Trigger detection and the per-channel correlation window |
| `test_twitch_irc.py` | 9 | Handshake, JOIN pacing, PING/PONG, reconnect backoff, and the structural refusal to send `PRIVMSG` |
| `test_tournament_parse.py` | 9 | Roster wikitext → teams/persons/roles |
| `test_player_parse.py` | 7 | Player infobox → socials, disambiguation pages |
| `test_settings_tables.py` (parser half) | 6 | `{{Mouse settings table}}` extraction |
| `test_api.py` | 3 | Title normalization, redirect following, 50-title chunking (uses `StubClient`) |

This is the good news: the domain logic — which is where the actual complexity lives — is
insulated from the storage layer and survives the migration untouched.

### 4.2 Breakage class A — in-memory databases

`db.connect(":memory:")` at `test_db.py:17`, `test_db_twitch.py:8`,
`test_channel_health.py:9`, `test_twitch_runner.py:45`. Postgres has no equivalent.

Fix: a session-scoped Postgres fixture (testcontainers, or a locally-running instance in
CI) with each test wrapped in a transaction that rolls back on teardown. Slower than
`:memory:` but keeps tests isolated. This is the single biggest structural change to the
suite.

### 4.3 Breakage class B — temp-file databases

`tmp_path / "*.sqlite3"` at `test_admin.py:10`, `test_site_queries.py:10`,
`test_site_build.py:11`, `test_cli.py:48`, `test_settings_tables.py:67`,
`test_twitch_runner.py:89, 122`, `test_channel_health.py:79`, `test_db_twitch.py:71`. Same
fix as class A.

`test_cli.py:65-66` additionally opens `sqlite3.connect(db_path)` **directly** to make
assertions, bypassing the DAL — that becomes a psycopg connection.

### 4.4 Breakage class C — tests of the migration mechanism itself (delete these)

`test_channel_health.py:76-90` and `test_db_twitch.py:68-82` each build an *old-shape*
SQLite database with raw `sqlite3`, then assert that `db.connect()` adds the missing
column, verifying via `PRAGMA table_info(...)` and positional row access (`r[1]`).

These test `_migrate()`, which §1.2 deletes. They should be **removed rather than ported** —
porting them would mean reimplementing an ad-hoc migration system that the Alembic/SQL
migration story replaces. Migration coverage moves to "the migration scripts run cleanly
against an empty database and against a restored snapshot."

### 4.5 Breakage class D — string timestamps in fixtures — **dissolved by the `TEXT` decision**

**51 occurrences** of `"t0"` / `"t1"` / `"t2"` used as timestamp values across
`test_admin.py`, `test_site_queries.py`, `test_site_build.py`, `test_db.py`,
`test_channel_health.py`, and `test_twitch_runner.py`.

Under a `timestamptz` migration every one of these would become a type error across six
files. **Because `observed_at` stays `TEXT` (§1.4), they all keep working untouched.** No
action required — recorded here so the saving is visible if the typing decision is ever
revisited, since it comes back the moment `observed_at` is retyped.

If the optional `CHECK (observed_at ~ '^\d{4}-\d{2}-\d{2}')` constraint from §1.4 is added,
these fixtures *would* start failing — so either skip the constraint or budget the same
six-file fixture fix. Recommend skipping it; the parsers that produce real values are
already well covered.

### 4.6 Breakage class E — the admin tests, rewritten wholesale

All 9 tests in `test_admin.py` drive the Flask app through `create_app()` and post
HTML form bodies, asserting on redirects, flash messages, and rendered HTML. Under FastAPI
with JSON request/response and no server-side sessions, **none of them survive as written**.

The *scenarios* they encode are worth preserving verbatim, because they're the real
specification of the admin surface: handle replacement retires the old row and appends a
new one; a player with no Twitch can have one added; a manual observation is recorded from
a candidate; a manual observation with no values recorded records nothing; a candidate can
be dismissed. Port the assertions, not the mechanism.

### 4.7 Adjacent — the HTTP cache tests

`test_http_client.py` (8 tests) doesn't touch the database and is unaffected by Postgres.
But four of its tests assert **disk-cache** behaviour specifically —
`test_cache_is_shared_across_client_instances`, `test_expired_cache_refetches`,
`test_refresh_bypasses_cache_read_but_rewrites`, `test_cache_serves_repeat_requests_without_transport`.

If §2.1's backend injection is done properly, these survive nearly unchanged by pointing at
an in-memory or local backend — which is a good argument for injecting a backend rather
than rewriting `LiquipediaClient` around boto3 directly.

The four rate-gate tests (`test_requests_are_spaced_two_seconds`, etc.) already inject a
`FakeClock` and keep working unchanged. They only prove the *in-process* gate — which,
after the no-Lambda decision (§2.2), is the only gate there is. The cross-invocation
coverage this migration would otherwise have needed is no longer required.

---

## 5. Every mutation the current dashboard can perform

Four POST endpoints, all in `mr_mouse_stats/admin/actions.py`. Every one is append-only or
a one-way soft-retirement; **nothing deletes and nothing overwrites a value**. This list is
the complete write surface the admin API must expose.

### 5.1 `POST /actions/replace-handle` — `actions.py:16-37`

Form: `player_id`, `new_handle`, optional `old_account_id`.
Writes **two** rows/statements:
1. `retire_social_account(old_account_id, now)` — `UPDATE social_accounts SET retired_at`
   (only if `old_account_id` was supplied; used when correcting a stale handle, omitted
   when adding a first handle to a player who has none)
2. `record_social_account(player_id, 'twitch', handle, url, now, source='manual')` —
   `INSERT` with `ON CONFLICT DO NOTHING`

Input handling: strips whitespace and leading `@`/`#`; empty handle is rejected with a
flash and no write. Synthesizes the URL as `https://www.twitch.tv/{handle}`.

### 5.2 `POST /actions/retire-handle` — `actions.py:40-46`

Form: `account_id`. Writes `UPDATE social_accounts SET retired_at = ? WHERE id = ? AND
retired_at IS NULL` (`db.py:289-294`). The `AND retired_at IS NULL` guard makes it
idempotent and prevents overwriting an earlier retirement timestamp — behaviour explicitly
covered by `test_channel_health.py::test_retire_does_not_overwrite_existing_retirement`.

### 5.3 `POST /actions/candidates/<message_id>/manual` — `actions.py:49-88`

The most complex one. Form: `dpi`, `sensitivity`, `windows_sens`, `polling_rate`,
`mouse_brand`, `mouse_model`.

Behaviour worth preserving exactly:
- Looks up the source message; unknown id → flash, no write.
- Resolves channel → player via `player_ids_by_twitch_channel()`; **unknown channel → flash
  and no write** ("fix handles first"). The admin API needs a real error response here, not
  a redirect.
- Numeric coercion is deliberately lenient: unparseable input becomes `None` rather than a
  validation error (`actions.py:63-68`). FastAPI/Pydantic's instinct would be to 422 on
  bad input — decide consciously whether to keep the lenient behaviour.
- **If all six fields are `None`, nothing is written** (`actions.py:78-80`) — covered by
  `test_manual_observation_with_no_values_records_nothing`.
- Otherwise `INSERT INTO settings_observations` with `source='manual'`, inheriting
  `observed_at`, `channel`, and `raw_text` from the *source message* (not from now), and
  setting `source_message_id` — which is what removes the row from the candidate queue.

### 5.4 `POST /actions/candidates/<message_id>/dismiss` — `actions.py:91-97`

Form: none beyond the path parameter. Writes `UPDATE twitch_messages SET dismissed_at = ?
WHERE id = ? AND dismissed_at IS NULL` (`db.py:310-315`). Same idempotence guard as retire.
The row is kept; dismissal only removes it from the candidate queue
(`db.py:272`, `admin/app.py:145`).

### 5.5 Underlying DAL writes — exactly four of the twelve

The admin API's write surface maps to precisely these `Store` methods:

| Method | Line | Effect |
|---|---|---|
| `retire_social_account` | `db.py:289` | soft-retire, idempotent |
| `record_social_account` | `db.py:191` | append, conflict-safe |
| `add_settings_observation` | `db.py:213` | append |
| `dismiss_twitch_message` | `db.py:310` | soft-dismiss, idempotent |

**The other eight `Store` write methods are scraper- and collector-owned and must NOT be
exposed by the admin API:** `upsert_tournament`, `get_or_create_team`,
`upsert_player_stub`, `update_player_resolved`, `mark_player_status`,
`upsert_roster_entry`, `record_twitch_message`, `upsert_channel_join_status`.

This is a clean split and worth enforcing structurally — the same way `LiquipediaClient`
and `ReadOnlyIrcClient` enforce their invariants. Consider separate DAL modules (or
distinct Postgres roles) for admin-writable versus pipeline-writable tables, so "the admin
API owns every write" doesn't quietly drift into "the admin API can write anything."

### 5.6 Read surface, for completeness

Four GET routes become the admin read API: `/` (`app.py:32`, aggregate counts),
`/handles` (`app.py:61`, failing channels + players missing Twitch), `/unresolved`
(`app.py:70`), `/candidates` (`app.py:74`). Their backing queries are `_failing_channels`,
`_players_without_twitch`, `_unresolved_players`, `_unparsed_candidates`
(`app.py:84-152`) — all portable modulo the `GROUP_CONCAT` and `confirmed = 0` changes
in §1.

---

## 6. Recommended phase ordering

Sequenced so each phase is independently verifiable and the irreversible step happens
late, after the reversible ones have shaken out bugs.

**Phase 0 — Configuration.** Move every path and connection string to environment
variables with the current values as defaults. No behaviour change; makes every later phase
a config change rather than a code change. Cheap, do it first.

**Phase 1 — Postgres, still local.** Rewrite `db.py` on psycopg3, rewrite `schema.sql` as a
baseline migration, delete `_migrate()`, fix parameter style, `GROUP_CONCAT`, booleans, and
`observed_at`. Port the test suite (§4). Run everything through the existing CLI against a
local Postgres. **Do this while the system is still a monolith you can run and debug on one
machine** — it is the largest diff in the migration and you want it isolated from
infrastructure variables.

**Phase 2 — Neon.** Point Phase 1 at Neon's pooled endpoint. One-shot migration script for
the existing data (~1,600 rows total across all tables — this takes seconds and is trivially
re-runnable from the SQLite file, which you should keep). Verify `build-site` output is
byte-identical to the current `site/` as an end-to-end correctness check on the data
migration.

**Phase 3 — Admin API.** FastAPI app exposing the four reads (§5.6) and the four writes
(§5.5), still run locally. Port `test_admin.py`'s scenarios. **Deploy it behind the Cognito
authorizer from its very first deploy** — there must never be a window where an
unauthenticated write endpoint is reachable.

**Phase 4 — Public read API.** FastAPI app over `site/queries.py`'s rollups, read-only DB
role. Lowest-risk phase: no writes, no auth, and correctness is checkable against the
current static site.

**Phase 5 — The Fargate task.** ⚠️ **Riskiest step — see below.** ARM64, public subnet,
`assignPublicIp: ENABLED`, no inbound rules, `SIGTERM` handling, `TZ=UTC`. Ships the
collector *and* the timer-driven scrape as one container, on separate threads with separate
DB connections (§2.3). Run it in parallel with the existing systemd unit against the same
Neon database for a few days — `twitch_messages` dedupes on the Twitch message uuid
(`db.py:259`), so double-collection is harmless and gives a direct comparison before
cutting over. Note the parallel run does mean two independent scrape timers; point the
systemd copy at `collect-twitch` only, or accept the doubled (still trivial) request volume.

**Phase 6 — Frontend.** Next.js static export → S3 → CloudFront, consuming Phase 4's API,
with the dashboard as a Cognito-authenticated view onto Phase 3. Retire `site/build.py`,
the Jinja templates, and `site/svg.py`.

### ⚠️ The riskiest step: Phase 5 — the collector's write path

The no-Lambda decision (§2.2) removed the previous top risk, which was getting the User-Agent
blocked by Liquipedia. What replaces it is smaller in blast radius but the same in kind: the
only remaining step whose failure loses something that **cannot be recovered**.

**Twitch chat is not re-fetchable.** Liquipedia can be re-scraped forever; a `!dpi` response
that scrolled past while the collector could not write to Neon is gone permanently. Every
other phase fails recoverably — a bad data migration restores from the SQLite file you keep,
a broken API rolls back, a crashed task is replaced.

The specific hazard is in `runner.py:79-97`. Today `store.record_twitch_message()` and
`store.commit()` fire per message against a local file, where failure is essentially
impossible. Against Neon they acquire a whole new failure surface: transient network errors,
Neon's scale-to-zero cold starts, pool exhaustion, connection timeouts. And there is **no
error handling anywhere on that path** — `collect()` has no try/except around the write, and
`cmd_collect_twitch` (`cli.py:268-274`) catches only `KeyboardInterrupt`. A single
`psycopg.OperationalError` propagates out of the generator loop and kills the process. Then
the restart costs another ~40 s JOIN gap (§2.3), and if the database is genuinely unhealthy
you get a crash loop that observes nothing at all.

The current test suite would not catch this: `test_twitch_runner.py` exercises `collect()`
with an in-process store that never fails.

Required before Phase 5 ships:

- **Wrap the write path in retry-with-backoff.** A transient Neon blip must not reach the
  IRC loop.
- **Never let a write error kill collection.** Catch at the `collect()` write boundary, log
  loudly, and keep reading the socket. Staying connected and losing one row beats
  disconnecting and losing forty seconds of rows.
- **Buffer locally on failure.** An in-memory (or `/tmp`-backed) spool drained on reconnect
  turns a database outage into delayed writes rather than lost observations. This is the
  single highest-value addition, because it is the only one that actually preserves data
  rather than merely degrading gracefully.
- **Batch the commits.** `runner.py:97` commits per message — one network round trip each
  against Neon. Commit per batch or on a short interval; this reduces both latency and the
  number of failure opportunities.
- **Alarm on collection going quiet.** A crash-looping or silently-failing collector looks
  identical to a quiet chat from the outside. `channel_join_status` (written after the join
  grace period, `runner.py:52-58`) is the natural health signal to monitor.

Phase 1 remains the *largest* diff, and Phase 3 carries the sharpest security requirement
(the Cognito authorizer must be present on the admin API's first deploy, never added
afterwards). Neither is the riskiest, because both fail recoverably.

---

## Verification

- **Phase 1 — done.** 127 tests green against local Postgres (129 minus the two deleted
  `_migrate()` tests). `build-site` output is identical to the committed `site/` across all
  13 pages except the generated-on date — see §7.1 for the one real difference this caught.
- **Phase 2 — done.** Row counts matched the source exactly on import: `tournaments` 1,
  `teams` 10, `players` 86, `roster_entries` 86, `social_accounts` 181,
  `settings_observations` 30, `channel_join_status` 56, and `twitch_messages` 1,221 (this
  last one grows continuously — compare against the source file at import time, not against
  a number written down here). Referential integrity confirmed post-import: all 27
  `source_message_id` and all 621 `trigger_id` values resolve, which they only can if
  primary keys were preserved. `has_settings_observation` returns True for all three
  existing Liquipedia observations and False for an unseen date, so the idempotence guard
  survived the move.
- **Phase 3:** ported admin scenarios pass; an unauthenticated request to any admin route
  is rejected by API Gateway **without invoking the Lambda** (confirm via absence of a
  CloudWatch log entry, not just the HTTP status).
- **Phase 5:** collector and systemd unit run in parallel against Neon for 48 h;
  `twitch_messages` shows no duplicate `msg_id` and comparable capture rates between the
  two. Separately, kill the Neon endpoint mid-collection and confirm the task **stays
  connected to Twitch**, buffers, and drains on recovery — rather than exiting. That is the
  Phase 5 acceptance test, and it needs a fault-injection case in
  `test_twitch_runner.py` (a store stub whose `record_twitch_message` raises) since nothing
  covers it today.
- **Phase 5 (scrape half):** the timer fires on its own thread without interrupting chat
  capture — assert PONG latency stays flat across a scrape window, and that a deliberately
  failing scrape logs and skips rather than killing the task.

## 7. Found during execution

Two things the survey did not predict. Both are fixed; both are worth knowing about
because they generalise to the phases still ahead.

### 7.1 Collation changes the rendered player order

SQLite orders `TEXT` by byte value, so uppercase sorts before lowercase: `CRAZMANG` before
`coopertastic`, `FMCL` before `fate`. Postgres in `en_US.utf8` sorts dictionary-style and
produces the opposite. `player_summaries` (`site/queries.py`) orders by
`p.liquipedia_page`, so the public players table silently reordered — same 71 players, no
data lost, but a visibly different page.

Fixed by pinning `ORDER BY p.liquipedia_page COLLATE "C"`, which both reproduces the
previous output and makes the build independent of the server's locale. That second
property is the one that matters going forward: **Neon's collation need not match the dev
container's**, so any ordering that reaches the rendered site should be pinned rather than
inherited. The other `ORDER BY`s are on ISO-8601 timestamps or internal to admin tooling,
where collation cannot change the result.

This is exactly what the "byte-identical site output" check existed to catch, and it would
have been very hard to spot by reading the diff.

### 7.2 The test fixtures will destroy a real database

The conftest fixtures `TRUNCATE` every table between tests. The first version pointed at
the same DSN as the dev default, so running `pytest` wiped the freshly imported production
data — noticed only because a follow-up query came back empty.

Fixed three ways: tests default to a separate `mr_mouse_stats_test` database, `conftest.py`
refuses to start against any database whose name doesn't end in `_test`, and the README
documents both. Worth carrying into later phases — **the Phase 5 parallel-run against Neon
is the same hazard with a much worse blast radius**, since Twitch captures cannot be
re-fetched (see the riskiest-step section). Any test or script that can truncate should
assert on the target database name first.

## Attribution

Liquipedia content is CC-BY-SA 3.0. The Next.js frontend must carry the same attribution
the Jinja templates do today — check `site/templates/base.html` before deleting it.
