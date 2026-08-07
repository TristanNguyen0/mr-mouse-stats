"""AWS Lambda entry points.

Two separate functions from one image/bundle; each API Gateway route targets
one of them. Mangum adapts the ASGI apps and exposes the original event as
`scope["aws.event"]`, which is where `deps.require_admin` reads the JWT
claims API Gateway attached.
"""

from __future__ import annotations

from mangum import Mangum

from .. import log
from .admin import app as admin_app
from .public import app as public_app

log.setup()

# api_gateway_base_path="/" because each app is mounted at a stage root.
public_handler = Mangum(public_app, lifespan="off")
admin_handler = Mangum(admin_app, lifespan="off")
