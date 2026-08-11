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
ENV_DB_SECRET_ARN = "MR_MOUSE_STATS_DB_SECRET_ARN"
ENV_CACHE_DIR = "MR_MOUSE_STATS_CACHE_DIR"
ENV_WIKI = "MR_MOUSE_STATS_WIKI"
ENV_CORS_ORIGINS = "MR_MOUSE_STATS_CORS_ORIGINS"
ENV_SCRAPE_INTERVAL = "MR_MOUSE_STATS_SCRAPE_INTERVAL"
ENV_TOURNAMENTS = "MR_MOUSE_STATS_TOURNAMENTS"
ENV_ADMIN_BASE_PATH = "MR_MOUSE_STATS_ADMIN_BASE_PATH"

# Matches the dev container documented in the README. Hosted runtimes are
# expected to supply their own (Neon) DSN — see db().
DEFAULT_DB = "postgresql://postgres:postgres@localhost:55432/mr_mouse_stats"
DEFAULT_CACHE_DIR = ".cache/liquipedia"
DEFAULT_WIKI = "marvelrivals"

# Mirrors `local.admin_base_path` in infra/api.tf, which sets the env var on
# the admin Lambda. Terraform is the source of truth; this default only
# covers running the admin app outside it (locally, and in tests).
# tests/test_lambda_handlers.py fails if the two drift.
DEFAULT_ADMIN_BASE_PATH = "/admin"


_dsn_cache: str | None = None


def _region_from_arn(arn: str) -> str | None:
    """The region field of an ARN, or None for anything that isn't one.

    Worth doing rather than leaving it to the environment: botocore reads
    AWS_DEFAULT_REGION and *not* AWS_REGION, and Lambda happens to set both —
    a coincidence this should not depend on. None lets botocore resolve it
    the usual way, which covers a bare secret name.
    """
    parts = arn.split(":")
    return parts[3] if arn.startswith("arn:") and len(parts) > 3 and parts[3] else None


def _dsn_from_secret(arn: str) -> str:
    """Read the DSN out of Secrets Manager.

    boto3 is imported here, not at module scope: only the Lambdas take this
    path, and the CLI and the Fargate task should not pay for the import.
    """
    import boto3

    client = boto3.client("secretsmanager", region_name=_region_from_arn(arn))
    return client.get_secret_value(SecretId=arn)["SecretString"].strip()


def db() -> str:
    """Postgres DSN, from the environment or from Secrets Manager.

    MR_MOUSE_STATS_DB wins when set — that covers the CLI, local development,
    tests, and the Fargate task, which has ECS resolve the secret for it.
    The Lambdas get MR_MOUSE_STATS_DB_SECRET_ARN instead, because a plain
    value there would be readable in the console and in
    `aws lambda get-function-configuration`.

    The fetch is one HTTPS round trip per execution environment, on the same
    cold start that already opens the psycopg connection; the result is cached
    at module scope so warm invocations pay nothing. A failed fetch raises
    rather than falling back to DEFAULT_DB: connecting to localhost from a
    Lambda would bury the real IAM or secret-name error under a refused
    connection.
    """
    global _dsn_cache
    explicit = os.environ.get(ENV_DB)
    if explicit:
        return explicit
    arn = os.environ.get(ENV_DB_SECRET_ARN)
    if not arn:
        return DEFAULT_DB
    if _dsn_cache is None:
        _dsn_cache = _dsn_from_secret(arn)
    return _dsn_cache


def reset_dsn_cache() -> None:
    """Drop the cached secret value. Used by tests."""
    global _dsn_cache
    _dsn_cache = None


def cache_dir() -> str:
    return os.environ.get(ENV_CACHE_DIR) or DEFAULT_CACHE_DIR


def wiki() -> str:
    return os.environ.get(ENV_WIKI) or DEFAULT_WIKI


def admin_base_path() -> str:
    """Path prefix the gateway routes to the admin function, and which Mangum
    strips before FastAPI sees the request. Set by Terraform from
    `local.admin_base_path`."""
    return os.environ.get(ENV_ADMIN_BASE_PATH) or DEFAULT_ADMIN_BASE_PATH


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
