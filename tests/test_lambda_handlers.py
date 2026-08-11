"""Lambda entry points, driven with the events API Gateway actually sends.

The gateway routes `ANY /{proxy+}` to the public function and
`ANY /admin/{proxy+}` to the admin one (infra/api.tf), so admin requests
arrive with a `/admin` prefix the app itself does not declare. Everything
here is about that seam: it is invisible locally, where each app is served
at the root, and would 404 every admin route in AWS.
"""

import re
from pathlib import Path

import pytest

from mr_mouse_stats import config
from mr_mouse_stats.config import DEFAULT_ADMIN_BASE_PATH
from mr_mouse_stats.api import deps
from mr_mouse_stats.api.lambda_handlers import (
    ADMIN_BASE_PATH,
    admin_handler,
    public_handler,
)
from mr_mouse_stats.api.admin import app as admin_app

CLAIMS = {"sub": "u-1", "email": "admin@example.com"}
ADMIN_ROUTE_KEY = f"ANY {ADMIN_BASE_PATH}/{{proxy+}}"


def gateway_event(path: str, *, route_key: str, claims: dict | None = None) -> dict:
    """An API Gateway HTTP API (payload format 2.0) request."""
    request_context = {
        "accountId": "123456789012",
        "apiId": "abcdef",
        "domainName": "abcdef.execute-api.us-east-1.amazonaws.com",
        "http": {
            "method": "GET",
            "path": path,
            "protocol": "HTTP/1.1",
            "sourceIp": "203.0.113.1",
            "userAgent": "pytest",
        },
        "requestId": "req-1",
        "routeKey": route_key,
        "stage": "$default",
        "time": "01/Aug/2026:00:00:00 +0000",
        "timeEpoch": 1785542400000,
    }
    if claims is not None:
        request_context["authorizer"] = {"jwt": {"claims": claims, "scopes": None}}
    return {
        "version": "2.0",
        "routeKey": route_key,
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": request_context["domainName"]},
        "requestContext": request_context,
        "isBase64Encoded": False,
    }


@pytest.fixture
def admin_db(conn):
    """The module-level app the handler wraps, pointed at the test database."""
    admin_app.dependency_overrides[deps.get_conn] = lambda: conn
    yield
    admin_app.dependency_overrides.clear()


# --- the prefix is defined once, in Terraform -------------------------------

INFRA = Path(__file__).resolve().parent.parent / "infra"
API_TF = (INFRA / "api.tf").read_text()
OUTPUTS_TF = (INFRA / "outputs.tf").read_text()


def test_terraform_declares_the_prefix_once():
    """`local.admin_base_path` is the single source of truth."""
    declarations = re.findall(r'^\s*admin_base_path\s*=\s*"([^"]*)"', API_TF, re.M)
    assert declarations == [DEFAULT_ADMIN_BASE_PATH], (
        "infra/api.tf must declare local.admin_base_path exactly once, and it "
        f"must match config.DEFAULT_ADMIN_BASE_PATH ({DEFAULT_ADMIN_BASE_PATH!r}), "
        "which is what the app falls back to when the env var is unset."
    )


@pytest.mark.parametrize(
    "name,pattern,source",
    [
        # The gateway route the admin function is wired to.
        ("route key", r'route_key\s*=\s*"ANY \$\{local\.admin_base_path\}/\{proxy\+\}"', "api.tf"),
        # What the Lambda reads back at runtime to strip the prefix.
        (
            "lambda env var",
            r"MR_MOUSE_STATS_ADMIN_BASE_PATH\s*=\s*local\.admin_base_path",
            "api.tf",
        ),
        # The base URL the frontend is built against.
        ("admin base url output", r"\$\{local\.admin_base_path\}", "outputs.tf"),
    ],
)
def test_terraform_derives_every_use_from_the_local(name, pattern, source):
    """Each consumer must interpolate the local, never re-spell the literal.

    A hardcoded `/admin` here is the drift this whole seam is guarding
    against: it would deploy cleanly and 404 every admin route.
    """
    text = API_TF if source == "api.tf" else OUTPUTS_TF
    assert re.search(pattern, text), f"{source} no longer derives the {name} from local.admin_base_path"


def test_handler_uses_the_configured_prefix():
    """The constant tracks config, which Terraform sets on the function."""
    assert ADMIN_BASE_PATH == config.admin_base_path() == DEFAULT_ADMIN_BASE_PATH


def test_admin_strips_the_gateway_prefix(admin_db):
    """The regression: `/admin/overview` must reach the app's `/overview`."""
    response = admin_handler(
        gateway_event(f"{ADMIN_BASE_PATH}/overview", route_key=ADMIN_ROUTE_KEY, claims=CLAIMS),
        None,
    )
    assert response["statusCode"] == 200


def test_admin_health_behind_the_prefix():
    """Unauthenticated, and touches no database — pure routing evidence."""
    response = admin_handler(
        gateway_event(f"{ADMIN_BASE_PATH}/health", route_key=ADMIN_ROUTE_KEY), None
    )
    assert response["statusCode"] == 200


def test_admin_still_rejects_callers_without_claims(admin_db, monkeypatch):
    """Stripping the prefix must not hand anyone a way past the authorizer.

    In AWS the gateway rejects before invoke; this covers the defence in
    depth behind it.
    """
    monkeypatch.delenv(deps.ENV_DEV_AUTH, raising=False)
    response = admin_handler(
        gateway_event(f"{ADMIN_BASE_PATH}/overview", route_key=ADMIN_ROUTE_KEY), None
    )
    assert response["statusCode"] == 401


def test_trailing_slash_does_not_redirect_off_the_admin_prefix(admin_db):
    """Stripping the prefix also strips it from any redirect Starlette builds,
    so `/admin/overview/` would 307 to `/overview` — the public function.
    `redirect_slashes=False` turns that into a plain 404."""
    response = admin_handler(
        gateway_event(f"{ADMIN_BASE_PATH}/overview/", route_key=ADMIN_ROUTE_KEY, claims=CLAIMS),
        None,
    )
    assert response["statusCode"] == 404


def test_public_keeps_the_root_path():
    """The public route has no prefix, so nothing may be stripped from it."""
    response = public_handler(
        gateway_event("/health", route_key="ANY /{proxy+}"), None
    )
    assert response["statusCode"] == 200
