"""How the database DSN is resolved, and where it is allowed to come from.

The DSN is the only secret this project has. Every runtime except the Lambdas
gets it as a plain environment variable; the Lambdas get the ARN of a Secrets
Manager secret instead, because a value there is readable in the console and
in `aws lambda get-function-configuration`. These tests pin that precedence
and the Terraform side that depends on it.

Nothing here talks to AWS: `_dsn_from_secret` is stubbed, so boto3 is never
imported.
"""

import re
from pathlib import Path

import pytest

from mr_mouse_stats import config

POOLED = "postgresql://u:p@ep-x-pooler.aws.neon.tech/db?sslmode=require"
DIRECT = "postgresql://u:p@ep-x.aws.neon.tech/db?sslmode=require"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No inherited DSN, and no cached secret leaking between tests."""
    monkeypatch.delenv(config.ENV_DB, raising=False)
    monkeypatch.delenv(config.ENV_DB_SECRET_ARN, raising=False)
    config.reset_dsn_cache()
    yield
    config.reset_dsn_cache()


@pytest.fixture
def secret(monkeypatch):
    """Stand in for Secrets Manager, counting how often it is read."""

    class Stub:
        def __init__(self):
            self.calls = []
            self.value = POOLED

        def __call__(self, arn: str) -> str:
            self.calls.append(arn)
            return self.value

    stub = Stub()
    monkeypatch.setattr(config, "_dsn_from_secret", stub)
    return stub


def test_env_var_is_returned_verbatim(monkeypatch, secret):
    monkeypatch.setenv(config.ENV_DB, DIRECT)
    assert config.db() == DIRECT
    assert secret.calls == []


def test_env_var_wins_over_the_secret(monkeypatch, secret):
    """The migration path sets both: the CLI runs with the direct DSN exported
    while the ARN may still be in the environment. The explicit value wins, and
    Secrets Manager is not called at all."""
    monkeypatch.setenv(config.ENV_DB, DIRECT)
    monkeypatch.setenv(config.ENV_DB_SECRET_ARN, "arn:aws:secretsmanager:::secret:db")
    assert config.db() == DIRECT
    assert secret.calls == []


def test_secret_is_read_when_only_the_arn_is_set(monkeypatch, secret):
    arn = "arn:aws:secretsmanager:us-east-1:1:secret:mr-mouse-stats/database-dsn"
    monkeypatch.setenv(config.ENV_DB_SECRET_ARN, arn)
    assert config.db() == POOLED
    assert secret.calls == [arn]


def test_secret_is_fetched_once_per_process(monkeypatch, secret):
    """The cache is what keeps warm invocations off the network."""
    monkeypatch.setenv(config.ENV_DB_SECRET_ARN, "arn:aws:secretsmanager:::secret:db")
    assert config.db() == POOLED
    assert config.db() == POOLED
    assert len(secret.calls) == 1


def test_reset_forces_a_refetch(monkeypatch, secret):
    monkeypatch.setenv(config.ENV_DB_SECRET_ARN, "arn:aws:secretsmanager:::secret:db")
    config.db()
    config.reset_dsn_cache()
    config.db()
    assert len(secret.calls) == 2


def test_falls_back_to_the_dev_default(secret):
    """An untouched checkout still points at the local dev container."""
    assert config.db() == config.DEFAULT_DB
    assert secret.calls == []


def test_a_failed_fetch_raises_rather_than_falling_back(monkeypatch):
    """Falling back to DEFAULT_DB would bury an IAM or secret-name error under
    a connection refused to localhost, from a Lambda, in CloudWatch."""

    def boom(arn: str) -> str:
        raise RuntimeError("AccessDeniedException")

    monkeypatch.setattr(config, "_dsn_from_secret", boom)
    monkeypatch.setenv(config.ENV_DB_SECRET_ARN, "arn:aws:secretsmanager:::secret:db")
    with pytest.raises(RuntimeError):
        config.db()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("arn:aws:secretsmanager:us-east-1:1:secret:mr-mouse-stats/database-dsn", "us-east-1"),
        ("arn:aws:secretsmanager:eu-west-2:1:secret:db-AbCdEf", "eu-west-2"),
        # Not an ARN: let botocore resolve the region however it normally does.
        ("mr-mouse-stats/database-dsn", None),
        ("arn:aws:secretsmanager", None),
        ("arn:aws:secretsmanager:::secret:db", None),
    ],
)
def test_region_comes_from_the_arn(value, expected):
    """botocore honours AWS_DEFAULT_REGION and not AWS_REGION; Lambda sets both,
    and this is what keeps that coincidence from mattering."""
    assert config._region_from_arn(value) == expected


# --- the Terraform side -----------------------------------------------------

API_TF = (Path(__file__).resolve().parent.parent / "infra" / "api.tf").read_text()


def test_lambdas_get_the_arn_not_the_dsn():
    """A plain `MR_MOUSE_STATS_DB = var.neon_dsn` here would deploy fine and
    silently put the secret in the function configuration — the regression this
    whole indirection exists to prevent."""
    assert re.search(
        rf"{config.ENV_DB_SECRET_ARN}\s*=\s*aws_secretsmanager_secret\.database\.arn", API_TF
    ), "infra/api.tf must pass the secret ARN to the Lambdas"
    assert not re.search(rf"^\s*{config.ENV_DB}\s*=", API_TF, re.M), (
        "infra/api.tf must not set MR_MOUSE_STATS_DB: it would take precedence "
        "over the secret and expose the DSN in the function configuration"
    )
