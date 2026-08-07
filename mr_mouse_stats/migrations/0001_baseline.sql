-- Baseline Postgres schema. Folds in every column that the old SQLite
-- _migrate() used to add at connect time, so a fresh database and a
-- migrated one have identical shape.
--
-- History-bearing tables (social_accounts, settings_observations) are
-- append-only: new observations are inserted with observed_at, never
-- updated in place.
--
-- observed_at is deliberately TEXT, not timestamptz: it carries two
-- precisions (ISO-8601 timestamps from Twitch, bare dates from
-- Liquipedia) and both sort correctly as strings, which is what the
-- site rollups rely on for "latest observation wins".

CREATE TABLE tournaments (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    liquipedia_page TEXT NOT NULL UNIQUE,
    name TEXT,
    series TEXT,
    tier TEXT,
    start_date TEXT,
    end_date TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE teams (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE players (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    liquipedia_page TEXT NOT NULL UNIQUE,
    player_id TEXT,
    real_name TEXT,
    romanized_name TEXT,
    country TEXT,
    roles TEXT,
    -- resolved | missing | not_player_page | skipped_staff
    resolution_status TEXT NOT NULL DEFAULT 'unresolved',
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE roster_entries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tournament_id BIGINT NOT NULL REFERENCES tournaments(id),
    team_id BIGINT NOT NULL REFERENCES teams(id),
    player_id BIGINT NOT NULL REFERENCES players(id),
    role TEXT,
    is_sub BOOLEAN NOT NULL DEFAULT FALSE,
    is_staff BOOLEAN NOT NULL DEFAULT FALSE,
    -- nullable on purpose: true / false / unknown
    played BOOLEAN,
    section TEXT,
    UNIQUE (tournament_id, team_id, player_id)
);

CREATE TABLE social_accounts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(id),
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    url TEXT,
    source TEXT NOT NULL DEFAULT 'liquipedia',
    observed_at TEXT NOT NULL,
    -- append-only handle correction: the old row is retired once,
    -- the corrected handle is appended as a new row
    retired_at TEXT,
    UNIQUE (player_id, platform, handle)
);

-- Raw capture of trigger commands and candidate responses observed in
-- Twitch chat. Append-only source of truth; settings_observations rows
-- are derived from it by the parse pass and can be re-derived as the
-- parser improves.
CREATE TABLE twitch_messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Twitch message uuid; dedupes reconnect overlap. NULLs are distinct
    -- in Postgres by default, so untagged messages never collide.
    msg_id TEXT UNIQUE,
    observed_at TEXT NOT NULL, -- from server-side tmi-sent-ts
    channel TEXT NOT NULL,
    login TEXT NOT NULL,
    display_name TEXT,
    user_id TEXT,
    badges TEXT,
    kind TEXT NOT NULL,        -- trigger | bot_response | broadcaster_response
    trigger_id BIGINT REFERENCES twitch_messages(id),
    text TEXT NOT NULL,
    -- admin-dismissed candidates (non-settings chatter); row is kept
    dismissed_at TEXT
);

CREATE TABLE settings_observations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(id),
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,  -- twitch_chat | liquipedia | manual
    channel TEXT,          -- twitch channel the observation came from
    raw_text TEXT,         -- verbatim bot response / template, for re-parsing
    dpi INTEGER,
    sensitivity DOUBLE PRECISION,
    windows_sens INTEGER,
    mouse_brand TEXT,
    mouse_model TEXT,
    pad_brand TEXT,
    pad_model TEXT,
    polling_rate INTEGER,
    zoom_sens DOUBLE PRECISION,
    ref_url TEXT,
    source_message_id BIGINT REFERENCES twitch_messages(id)
);

-- Collector join health, written by collect-twitch after its join grace
-- period. confirmed=false usually means the handle is stale (renamed or
-- suspended channel) — surfaced by the admin dashboard for manual fixing.
CREATE TABLE channel_join_status (
    channel TEXT PRIMARY KEY,
    confirmed BOOLEAN NOT NULL,
    last_checked_at TEXT NOT NULL
);

CREATE INDEX idx_twitch_messages_channel
    ON twitch_messages (channel, observed_at);
CREATE INDEX idx_roster_entries_tournament
    ON roster_entries (tournament_id);
CREATE INDEX idx_social_accounts_player
    ON social_accounts (player_id, platform);
CREATE INDEX idx_settings_observations_player
    ON settings_observations (player_id, observed_at);
-- The channel map and the admin failing-channels join both match on
-- LOWER(handle), which would otherwise force a sequential scan.
CREATE INDEX idx_social_accounts_lower_handle
    ON social_accounts (LOWER(handle));
