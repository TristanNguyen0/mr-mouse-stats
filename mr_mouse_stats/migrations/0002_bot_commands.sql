-- Chat-bot command definitions read from a bot's public command page.
--
-- This is the same shape as twitch_messages: an append-only raw capture
-- table plus a re-runnable parse pass into settings_observations, so that
-- improving the settings parser never requires re-fetching, and a parser
-- bug can never lose a capture.
--
-- The difference from chat capture is provenance. A chat response is only
-- dated by when we happened to be listening; a command carries the bot's
-- own updated_at, which is when the streamer last edited the text. That
-- makes it both the observation date and the version key.

CREATE TABLE bot_commands (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bot TEXT NOT NULL,             -- nightbot
    channel TEXT NOT NULL,         -- twitch login, lowercase
    bot_channel_id TEXT,           -- the bot's own id for the channel
    command_id TEXT NOT NULL,      -- the bot's own id for the command
    name TEXT NOT NULL,            -- normalized, without the leading '!'
    message TEXT NOT NULL,         -- the response text, verbatim
    -- The bot's own last-edited timestamp. Doubles as the version key:
    -- a player changing their DPI edits the command, which moves
    -- updated_at, which appends a row rather than colliding with the old
    -- one. Nullable because not every bot reports it.
    updated_at TEXT,
    first_fetched_at TEXT NOT NULL,
    -- admin-dismissed candidates (a !mouse command that is a joke, say);
    -- the row is kept, exactly as in twitch_messages
    dismissed_at TEXT,
    UNIQUE (bot, command_id, updated_at)
);

-- Postgres counts NULLs as distinct, so the constraint above does not
-- dedupe a bot that reports no timestamp — every run would append the same
-- command again. Nightbot always sends one; the bots that do not (Fossabot,
-- Moobot) have only the text to go on, so for those the text is the version.
CREATE UNIQUE INDEX idx_bot_commands_undated
    ON bot_commands (bot, command_id, message)
    WHERE updated_at IS NULL;

-- Which channels have been checked, and what was found. Separate from
-- bot_commands because "checked, has no Nightbot" and "checked, has
-- Nightbot but no settings commands" are both useful answers that leave
-- no command rows behind.
CREATE TABLE bot_channel_status (
    bot TEXT NOT NULL,
    channel TEXT NOT NULL,
    registered BOOLEAN NOT NULL,
    bot_channel_id TEXT,
    commands_seen INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT NOT NULL,
    PRIMARY KEY (bot, channel)
);

-- Mirrors settings_observations.source_message_id: the raw row a parsed
-- observation was derived from, so the parse pass can find what it has
-- already done and the admin UI can show the text behind a number.
ALTER TABLE settings_observations
    ADD COLUMN source_command_id BIGINT REFERENCES bot_commands(id);

CREATE INDEX idx_bot_commands_channel ON bot_commands (bot, channel, name);
CREATE INDEX idx_settings_observations_source_command
    ON settings_observations (source_command_id);
