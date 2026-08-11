"""AWS Lambda entry points.

Two separate functions from one image/bundle; each API Gateway route targets
one of them. Mangum adapts the ASGI apps and exposes the original event as
`scope["aws.event"]`, which is where `deps.require_admin` reads the JWT
claims API Gateway attached.
"""

from __future__ import annotations

from mangum import Mangum

from .. import config, log
from .admin import app as admin_app
from .public import app as public_app

log.setup()

# The gateway routes `ANY /{proxy+}` to public and `ANY ${admin_base_path}/
# {proxy+}` to admin (infra/api.tf), so an admin request arrives with the path
# `/admin/overview` while the app declares `/overview`. Mangum strips the
# prefix back off; without this every admin route 404s in AWS while working
# locally, where the app is served at the root.
#
# The value comes from Terraform, which writes the same local into the route
# key and the env var — so the two cannot disagree in a deployed stack.
ADMIN_BASE_PATH = config.admin_base_path()

public_handler = Mangum(public_app, lifespan="off")
admin_handler = Mangum(admin_app, lifespan="off", api_gateway_base_path=ADMIN_BASE_PATH)
