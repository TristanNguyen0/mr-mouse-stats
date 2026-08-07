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
