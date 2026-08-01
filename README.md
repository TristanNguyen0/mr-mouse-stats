# mr-mouse-stats

Mouse settings (DPI, in-game sensitivity, mouse model) for professional Marvel
Rivals players competing in the Ignite circuit — including **settings history
over time**, not just a snapshot.

Milestone 1: a CLI that, given a Liquipedia tournament page, stores the roster
of players with their Twitch (and other social) handles.

```sh
# Stage 1: roster + twitch handles from Liquipedia
uv run mr-mouse-stats fetch-roster "MR_Ignite/2026/Mid_Season_Finals" --dry-run
uv run mr-mouse-stats fetch-roster "MR_Ignite/2026/Mid_Season_Finals"

# Stage 2: passively observe settings-bot responses in players' chats
uv run mr-mouse-stats collect-twitch            # runs until Ctrl-C
uv run mr-mouse-stats collect-twitch --duration 3600 --dry-run

# Derive structured settings from stored raw messages (re-runnable)
uv run mr-mouse-stats parse-observations
```

## Development

```sh
uv sync
uv run pytest
```

## Data collection constraints

Read these before touching any code that performs network access.

**Liquipedia** — use the MediaWiki API at `liquipedia.net/marvelrivals/api.php`
only; automated access to rendered HTML pages is forbidden by their ToS. Rate
limits are 1 request / 2 s in general and 1 request / 30 s for `action=parse`,
so prefer `action=query&prop=revisions` (wikitext) and batch up to 50 titles per
request. Every request must send the project's custom User-Agent with contact
info and accept gzip. All network access goes through
`mr_mouse_stats/http.py::LiquipediaClient`, which enforces this and caches
responses on disk — never add another code path that hits the network.

**Twitch** (stage 2, not yet implemented) — read-only anonymous IRC
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
