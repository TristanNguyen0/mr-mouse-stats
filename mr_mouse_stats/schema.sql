-- History-bearing tables (social_accounts, settings_observations) are
-- append-only: new observations are inserted with observed_at, never
-- updated in place. Keep DDL portable — Postgres is a possible later swap.

CREATE TABLE IF NOT EXISTS tournaments (
    id INTEGER PRIMARY KEY,
    liquipedia_page TEXT NOT NULL UNIQUE,
    name TEXT,
    series TEXT,
    tier TEXT,
    start_date TEXT,
    end_date TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS roster_entries (
    id INTEGER PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    role TEXT,
    is_sub INTEGER NOT NULL DEFAULT 0,
    is_staff INTEGER NOT NULL DEFAULT 0,
    played INTEGER,
    section TEXT,
    UNIQUE (tournament_id, team_id, player_id)
);

CREATE TABLE IF NOT EXISTS social_accounts (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id),
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    url TEXT,
    source TEXT NOT NULL DEFAULT 'liquipedia',
    observed_at TEXT NOT NULL,
    UNIQUE (player_id, platform, handle)
);

-- Populated in stage 2 (Twitch chat) and possibly from Liquipedia
-- {{Mouse settings table}}; created now so the model anticipates history.
CREATE TABLE IF NOT EXISTS settings_observations (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id),
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,  -- twitch_chat | liquipedia | manual
    channel TEXT,          -- twitch channel the observation came from
    raw_text TEXT,         -- verbatim bot response / template, for re-parsing
    dpi INTEGER,
    sensitivity REAL,
    windows_sens INTEGER,
    mouse_brand TEXT,
    mouse_model TEXT,
    pad_brand TEXT,
    pad_model TEXT,
    ref_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_roster_entries_tournament
    ON roster_entries (tournament_id);
CREATE INDEX IF NOT EXISTS idx_social_accounts_player
    ON social_accounts (player_id, platform);
CREATE INDEX IF NOT EXISTS idx_settings_observations_player
    ON settings_observations (player_id, observed_at);
