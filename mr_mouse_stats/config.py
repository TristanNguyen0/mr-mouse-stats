"""Runtime configuration, read from the environment.

Every deployable — the CLI, the Fargate task, the API Lambdas — reads the
same names. Defaults reproduce the historical local-development paths, so
an existing checkout behaves exactly as before with no environment set.

Nothing here resolves paths relative to the current working directory at
import time; callers get the raw value and the CWD-dependence stays where
it already was. Hosted runtimes are expected to set every name explicitly.
"""

from __future__ import annotations

import os

ENV_DB = "MR_MOUSE_STATS_DB"
ENV_CACHE_DIR = "MR_MOUSE_STATS_CACHE_DIR"
ENV_WIKI = "MR_MOUSE_STATS_WIKI"
ENV_CORS_ORIGINS = "MR_MOUSE_STATS_CORS_ORIGINS"
ENV_SCRAPE_INTERVAL = "MR_MOUSE_STATS_SCRAPE_INTERVAL"
ENV_TOURNAMENTS = "MR_MOUSE_STATS_TOURNAMENTS"

# Matches the dev container documented in the README. Hosted runtimes are
# expected to set MR_MOUSE_STATS_DB to their own (Neon) DSN.
DEFAULT_DB = "postgresql://postgres:postgres@localhost:55432/mr_mouse_stats"
DEFAULT_CACHE_DIR = ".cache/liquipedia"
DEFAULT_WIKI = "marvelrivals"


def db() -> str:
    """Postgres DSN."""
    return os.environ.get(ENV_DB) or DEFAULT_DB


def cache_dir() -> str:
    return os.environ.get(ENV_CACHE_DIR) or DEFAULT_CACHE_DIR


def wiki() -> str:
    return os.environ.get(ENV_WIKI) or DEFAULT_WIKI


def cors_origins() -> list[str]:
    """Origins allowed to call the public API. Comma-separated; the default
    is permissive because the data is public and read-only."""
    raw = os.environ.get(ENV_CORS_ORIGINS, "").strip()
    return [o.strip() for o in raw.split(",") if o.strip()] or ["*"]


def scrape_interval() -> float:
    """Seconds between Liquipedia refreshes in the long-running task.

    Daily by default: rosters change on tournament boundaries, and the HTTP
    cache TTL is 24h, so anything more frequent mostly serves cache hits.
    """
    raw = os.environ.get(ENV_SCRAPE_INTERVAL, "").strip()
    return float(raw) if raw else 24 * 3600.0


def tournaments() -> list[str]:
    """Liquipedia tournament pages the scheduled scrape refreshes."""
    raw = os.environ.get(ENV_TOURNAMENTS, "").strip()
    return [t.strip() for t in raw.split(",") if t.strip()]
