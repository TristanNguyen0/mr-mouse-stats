import os
from pathlib import Path

import pytest

from mr_mouse_stats import db

FIXTURES = Path(__file__).parent / "fixtures"

# Tests run against a real Postgres — there is no in-memory equivalent.
# See README "Development" for the one-line docker command.
#
# Deliberately a DIFFERENT database from the dev default in config.py: the
# fixtures below TRUNCATE every table, so pointing this at a database with
# real data destroys it.
DEFAULT_TEST_DSN = (
    "postgresql://postgres:postgres@localhost:55432/mr_mouse_stats_test"
)
TEST_DSN = os.environ.get("MR_MOUSE_STATS_TEST_DSN", DEFAULT_TEST_DSN)

# Truncated between tests. RESTART IDENTITY keeps generated ids predictable
# per test; CASCADE covers the self-reference on twitch_messages.
_TABLES = (
    "settings_observations",
    "twitch_messages",
    "roster_entries",
    "social_accounts",
    "players",
    "teams",
    "tournaments",
    "channel_join_status",
)


@pytest.fixture
def fixture_text():
    def load(name: str) -> str:
        return (FIXTURES / name).read_text()

    return load


def _guard_not_the_dev_database(target: str) -> None:
    """The fixtures TRUNCATE everything, so refuse to run against a database
    that isn't obviously a throwaway. Opt out with MR_MOUSE_STATS_TEST_DSN
    pointing at something ending in _test."""
    name = target.rsplit("/", 1)[-1].split("?")[0]
    if not name.endswith("_test"):
        pytest.exit(
            f"refusing to run tests against database {name!r}: the fixtures "
            "TRUNCATE every table. Point MR_MOUSE_STATS_TEST_DSN at a "
            "database whose name ends in '_test'.",
            returncode=1,
        )


@pytest.fixture(scope="session")
def dsn():
    """Session-scoped: apply migrations once, hand out the DSN."""
    _guard_not_the_dev_database(TEST_DSN)
    try:
        conn = db.connect(TEST_DSN)
    except Exception as exc:  # pragma: no cover - environment problem
        pytest.exit(
            f"cannot reach test Postgres at {TEST_DSN}: {exc}\n"
            "start one with:\n"
            "  docker run -d --name mr-mouse-pg -e POSTGRES_PASSWORD=postgres "
            "-e POSTGRES_DB=mr_mouse_stats -p 55432:5432 postgres:16",
            returncode=1,
        )
    db.apply_migrations(conn)
    conn.close()
    return TEST_DSN


@pytest.fixture
def conn(dsn):
    """A connection to an empty database."""
    connection = db.connect(dsn)
    connection.execute(
        "TRUNCATE " + ", ".join(_TABLES) + " RESTART IDENTITY CASCADE"
    )
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def store(conn):
    return db.Store(conn)
