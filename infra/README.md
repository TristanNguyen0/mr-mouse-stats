# Infrastructure

Terraform for the hosted stack. **Never applied** — it was written and
validated (`terraform validate`, `terraform fmt`) in an environment with no
AWS credentials, so treat the first `terraform plan` as a review step, not a
formality.

```
Cognito user pool ──┐
                    ▼
CloudFront ─► S3    HTTP API ──► /{proxy+}       ──► public Lambda  ──┐
(frontend)          (api.tf)     /admin/{proxy+} ──► admin Lambda ───┤
                                 └── JWT authorizer on admin only    ├─► Neon
                                                                     │
ECS Fargate (ARM64, public subnet) ── collector + timed scrape ──────┘
```

## What is where

| File | Contents |
|---|---|
| `main.tf` | providers, ECR repositories, the DSN secret |
| `cognito.tf` | user pool, PKCE app client, seeded admin users |
| `api.tf` | both Lambdas, HTTP API, JWT authorizer, routes |
| `collector.tf` | ECS cluster, ARM64 task definition, service, alarm |
| `frontend.tf` | S3 (private) + CloudFront with Origin Access Control |

## First deploy

```sh
cd infra
terraform init
terraform apply -var="neon_dsn=postgresql://…-pooler…/mr_mouse_stats?sslmode=require" \
                -var='admin_emails=["you@example.com"]'
```

ECR repositories must exist before the Lambdas can pull, and the Lambdas
reference `:latest`. On a cold account, apply once (it will fail on the
Lambda image pull), push images, then apply again. Subsequent deploys are
just `scripts/deploy.sh`.

Then apply the schema — the runtime roles hold DML only, on purpose:

```sh
MR_MOUSE_STATS_DB="$NEON_DSN" uv run mr-mouse-stats migrate
```

## Deliberate choices worth not undoing

**The collector runs in a public subnet with a public IP and no inbound
rules.** A private subnet needs a NAT Gateway: $32.85/month against a task
that costs ~$11. The task only connects outward — the IRC client structurally
cannot accept connections or send a `PRIVMSG` — so a public subnet with an
empty ingress list is the same security posture for a third of the price.

**The JWT authorizer sits on the route, not in the app.** API Gateway rejects
unauthorized requests before the admin Lambda is invoked. `deps.require_admin`
is defence in depth. Verify by confirming a 401 produces *no* CloudWatch log
entry for the admin function — the access log's `authorizerErr` field is there
for exactly this.

**`desired_count = 1` on the collector, with `maximum_percent = 100`.** Two
collectors would double-join every channel and both drive the Liquipedia rate
gate. `twitch_messages` dedupes on the Twitch message uuid so it would not
corrupt data, but it doubles outbound traffic for nothing. The deployment
settings force stop-then-start rather than an overlapping rollout.

**Use Neon's pooled endpoint** (`-pooler` in the hostname) for the Lambdas.
They scale horizontally and would otherwise exhaust the direct connection
limit.

## What is not here

- **DNS.** Set `site_domain` and `acm_certificate_arn` (us-east-1) if you want
  a custom domain; otherwise the CloudFront domain is used.
- **A remote state backend.** Add an S3 backend before more than one person
  runs this.
- **WAF on the public API.** The data is public and read-only; add rate
  limiting if it ever gets attention.
