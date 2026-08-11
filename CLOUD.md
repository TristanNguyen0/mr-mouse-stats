# Cloud integration and deployment

How the hosted stack fits together and how to stand it up. `MIGRATION.md`
explains *why* the architecture is shaped this way; this document is the
runbook.

Nothing here has been applied yet. The Terraform is `validate`- and
`fmt`-clean, and every application code path was verified locally (including
the admin Lambda invoked through the Lambda Runtime Interface Emulator with a
real API Gateway v2 event), but no AWS account has seen it. Treat the first
`terraform plan` as a review, not a formality.

---

## 1. The services and how they connect

```
                    ┌──────────────────────────────────────────┐
                    │  CloudFront ──► S3 (private, OAC)        │
   browser ────────►│  Next.js static export                   │
                    └───────┬──────────────────────┬───────────┘
                            │ fetch                │ PKCE login
                            ▼                      ▼
              ┌─────────────────────────┐   ┌──────────────┐
              │   API Gateway (HTTP)    │   │   Cognito    │
              │                         │◄──┤  user pool   │
              │  ANY /{proxy+}          │   └──────────────┘
              │    └─► public Lambda    │      JWT authorizer
              │  ANY /admin/{proxy+}    │      validates here,
              │    └─► admin Lambda     │      before invoke
              └───────────┬─────────────┘
                          │
                          ▼
                    ┌───────────┐        ┌────────────────────────────┐
                    │   Neon    │◄───────┤  ECS Fargate (ARM64)       │
                    │ Postgres  │        │  collector + timed scrape  │
                    └───────────┘        └────────┬───────────────────┘
                                                  │
                                    Twitch IRC ───┴─── Liquipedia API
```

| Service | Role | Defined in |
|---|---|---|
| **Neon** | Postgres. External to Terraform; you create the project. | — |
| **Secrets Manager** | Holds the Neon DSN. Fargate reads it via the execution role; the Lambdas read it at cold start. | `infra/main.tf` |
| **ECR** | Two repositories: `-api` (both Lambdas) and `-collector`. | `infra/main.tf` |
| **Lambda** | `public-api` and `admin-api`, same image, different `image_config.command`. | `infra/api.tf` |
| **API Gateway** | One HTTP API, two routes. The authorizer is on `/admin/*` only. | `infra/api.tf` |
| **Cognito** | User pool + PKCE public client. Admin-create-only, no self-signup. | `infra/cognito.tf` |
| **ECS Fargate** | One always-on ARM64 task: IRC collector + Liquipedia scrape thread. | `infra/collector.tf` |
| **S3 + CloudFront** | Static frontend. Bucket is private; CloudFront reaches it via OAC. | `infra/frontend.tf` |

### One image, two Lambdas

`Dockerfile.lambda` builds a single image. Each function overrides the
handler:

- public → `mr_mouse_stats.api.lambda_handlers.public_handler`
- admin → `mr_mouse_stats.api.lambda_handlers.admin_handler`

Nothing app-specific is baked into the image, so one build and one push
updates both.

### Where the security boundary actually is

API Gateway's JWT authorizer rejects unauthorized `/admin/*` requests
**before the admin Lambda is invoked**. `deps.require_admin` in the app is
defence in depth — it reads the claims API Gateway already validated and
attached, and does not re-verify the token. Re-verifying would mean a second,
weaker verification path.

This matters operationally: both mechanisms return 401, so a misconfigured
authorizer is invisible from the client side. §5 below is the check that
distinguishes them, and it is not optional.

---

## 2. Prerequisites

```sh
# On this machine you already have: docker, node 22, uv.
# Missing:
#   - aws CLI v2
#   - terraform >= 1.9   (or keep using: docker run --rm -v "$PWD":/w -w /w hashicorp/terraform)
```

**Cross-building ARM64 on this machine needs QEMU.** Your host is `x86_64`
and buildx currently advertises only `linux/amd64`, so `scripts/deploy.sh`
(which builds `--platform linux/arm64`) will fail until you install the
emulation layer:

```sh
docker run --privileged --rm tonistiigi/binfmt --install arm64
docker buildx create --use --name mms   # a builder that supports multi-platform
docker buildx inspect --bootstrap | grep Platforms   # expect linux/arm64 listed
```

If you'd rather not emulate, switch both `architectures = ["arm64"]` in
`infra/api.tf` and `cpu_architecture = "ARM64"` in `infra/collector.tf` to
x86_64 and drop `--platform` from `deploy.sh`. It costs roughly 20% more.

### Neon

Create a project, then take **two** connection strings:

- the **pooled** one (`-pooler` in the hostname) — for the Lambdas, which
  scale horizontally and would otherwise exhaust the direct connection limit;
- the **direct** one — for `migrate` and the one-off data import.

Both need `?sslmode=require`.

Only the pooled one goes to AWS, as `TF_VAR_neon_dsn` below. The direct one is
a hand-operated migration credential: keep it in your own environment or
password manager and never put it in Terraform.

**Quote it.** A Neon DSN contains `&` (`?sslmode=require&channel_binding=require`).
Unquoted in a `.env`, the shell splits the line at the `&`, backgrounds the
first half, and the variable silently never gets set:

```sh
NEON_DIRECT_DSN="postgresql://…?sslmode=require&channel_binding=require"
```

Then load it with `set -a; . ./.env; set +a` and **check it before every step
below**:

```sh
[ -n "$NEON_DIRECT_DSN" ] || { echo "NEON_DIRECT_DSN unset"; return 1; }
```

This is not pedantry. `config.db()` falls back to the local dev Postgres when
`MR_MOUSE_STATS_DB` is empty, so §3.4 with an unset variable *succeeds* —
against your laptop. `psql ""` likewise falls back to the local socket. Both
failure modes look like something other than "the variable is empty".

---

## 3. First deploy

The order matters in one place: the Lambdas reference `:latest` in ECR, and
Terraform cannot create a function whose image does not exist. So create the
registries first, push, then apply everything.

### 3.1 Registries only

```sh
cd infra
terraform init

# Put the pooled DSN and the admin list in `secrets.auto.tfvars` — gitignored,
# and auto-loaded by every terraform command, so no apply below can miss them.
# Not `-var=`, which lands in shell history and in `ps` output.
$EDITOR secrets.auto.tfvars

terraform apply \
  -target=aws_ecr_repository.api \
  -target=aws_ecr_repository.collector
```

### 3.2 Build and push both images

```sh
cd ..
REGION=us-east-1
API_REPO=$(terraform -chdir=infra output -raw ecr_api_repository)
COLLECTOR_REPO=$(terraform -chdir=infra output -raw ecr_collector_repository)

aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin "${API_REPO%%/*}"

docker buildx build --platform linux/arm64 --provenance=false --sbom=false -f Dockerfile.lambda -t "$API_REPO:latest" --push .
docker buildx build --platform linux/arm64 --provenance=false --sbom=false -f Dockerfile        -t "$COLLECTOR_REPO:latest" --push .
```

`--provenance=false --sbom=false` is required, not hygiene. Buildx attaches
attestations by default, which makes the push an OCI *image index* wrapping the
real manifest plus an attestation manifest. Lambda takes a single image
manifest and rejects the index:

```
InvalidParameterValueException: The image manifest, config or layer media type
for the source image ... is not supported.
```

Fargate accepts either form, so the collector only carries the flags to keep
both images the same shape. To check what actually landed, the media type
should be `...image.manifest.v1+json`, never `...image.index.v1+json`:

```sh
aws ecr batch-get-image --repository-name mr-mouse-stats-api \
  --image-ids imageTag=latest --region $REGION \
  --query 'images[0].imageManifest' --output text | head -3
```

### 3.3 Everything else

```sh
cd infra
terraform apply
```

`admin_emails` comes from `secrets.auto.tfvars` and is deliberately required:
the users are a `for_each` over that list, so an apply that omits it would
destroy every existing admin user rather than fail.

Cognito emails each address in `admin_emails` a temporary password, valid for
3 days.

### 3.4 Schema

The runtime roles hold DML only, on purpose — schema changes are a deliberate
step, never something a request triggers. Use the **direct** DSN:

```sh
MR_MOUSE_STATS_DB="$NEON_DIRECT_DSN" uv run mr-mouse-stats migrate
```

Confirm it landed *on Neon*, not on localhost:

```sh
psql "$NEON_DIRECT_DSN" -c "\dt"   # expect 9 tables
```

### 3.5 Data

Your local Postgres is now the source of truth (the SQLite file is frozen).
Either dump/restore:

```sh
docker exec mr-mouse-pg pg_dump \
  "postgresql://postgres:postgres@localhost:5432/mr_mouse_stats" \
  --data-only --no-owner --exclude-table=schema_migrations \
  | psql "$NEON_DIRECT_DSN" -v ON_ERROR_STOP=1 --single-transaction
```

Three details, each of which is a failed restore otherwise:

- **`pg_dump` runs inside the container.** Neon is Postgres 16; this host's
  client is 17, and a 17 dump opens with `SET transaction_timeout = 0`, which
  16 rejects outright (`unrecognized configuration parameter`). The container's
  client matches the server. Any pg_dump ≤ the Neon major version works.
- **`--exclude-table=schema_migrations`.** §3.4 already inserted the baseline
  row on the target; dumping the source's copy collides on the primary key.
  Migration bookkeeping belongs to the destination.
- **`ON_ERROR_STOP=1 --single-transaction`.** Without it `psql` shrugs off
  errors and exits 0, leaving a half-loaded database that looks fine.

`pg_dump` warns about "circular foreign-key constraints" on `twitch_messages`.
Ignore it. The cycle is `trigger_id`'s self-reference, and each table restores
as a single `COPY` whose row triggers fire only at statement end — every row is
already present by then. The suggested `--disable-triggers` needs superuser,
which Neon does not grant, so taking the hint would fail where the warning
does not.

…or re-import from the original SQLite, which preserves primary keys and
advances the identity sequences:

```sh
uv run python scripts/import_from_sqlite.py \
  --sqlite data/mr_mouse_stats.sqlite3 --db "$NEON_DIRECT_DSN"
```

Prefer the dump: the local Postgres has the 514 observations
`parse-observations` derived, which the SQLite file never had.

Verify by comparing both ends, not by trusting the exit code:

```sh
psql "$NEON_DIRECT_DSN" -c \
  "select 'twitch_messages' t, count(*) from twitch_messages
   union all select 'settings_observations', count(*) from settings_observations
   union all select 'players', count(*) from players"
```

The identity sequences come across in the same dump (`setval` per table), so
the first insert on Neon continues the local numbering rather than colliding
with row 1.

### 3.6 Frontend

```sh
cd .. && ./scripts/deploy.sh frontend
```

The Cognito and API values are **baked in at build time** — a static export
has no server to read environment variables at runtime. `deploy.sh` reads
them from terraform outputs so they cannot drift:

| Terraform output | Frontend build var |
|---|---|
| `api_base_url` | `NEXT_PUBLIC_API_BASE` |
| `admin_api_base_url` | `NEXT_PUBLIC_ADMIN_API_BASE` |
| `cognito_domain` | `NEXT_PUBLIC_COGNITO_DOMAIN` |
| `cognito_client_id` | `NEXT_PUBLIC_COGNITO_CLIENT_ID` |
| `cognito_region` | `NEXT_PUBLIC_COGNITO_REGION` |

Changing any of them means rebuilding and re-syncing, not just restarting
something.

---

## 4. Configuration reference

### Terraform variables (`infra/variables.tf`)

| Variable | Default | Notes |
|---|---|---|
| `neon_dsn` | — | Required. Pooled endpoint. Stored in Secrets Manager; pass it as `TF_VAR_neon_dsn`, not `-var=`. |
| `region` | `us-east-1` | Cost figures in `MIGRATION.md` are us-east-1 list prices. |
| `admin_emails` | `[]` | Seeded Cognito users. |
| `tournaments` | Mid Season Finals | Pages the scheduled scrape refreshes. |
| `scrape_interval_seconds` | `86400` | Daily. See §6 before lowering it. |
| `site_domain` / `acm_certificate_arn` | `""` | Custom domain; certificate must be in us-east-1. |
| `collector_cpu` / `collector_memory` | `256` / `512` | 0.25 vCPU is already generous. |

### Runtime environment

| Variable | Consumed by | Notes |
|---|---|---|
| `MR_MOUSE_STATS_DB` | CLI, local, Fargate | The DSN itself. Fargate gets it from Secrets Manager via the ECS `secrets` block. Takes precedence over the ARN below when both are set. |
| `MR_MOUSE_STATS_DB_SECRET_ARN` | both Lambdas | ARN of the DSN secret, resolved by `config.db()` once per cold start. The Lambdas get this *instead of* the value, which would be readable in the function configuration. |
| `MR_MOUSE_STATS_CORS_ORIGINS` | public API | Set to the site origin by Terraform. |
| `MR_MOUSE_STATS_TOURNAMENTS` | Fargate scrape | Comma-separated. Empty leaves the thread idle. |
| `MR_MOUSE_STATS_SCRAPE_INTERVAL` | Fargate scrape | Seconds. |
| `MR_MOUSE_STATS_CACHE_DIR` | Liquipedia client | `/tmp/liquipedia-cache` on Fargate. |
| `MR_MOUSE_STATS_ADMIN_BASE_PATH` | admin API | Prefix the gateway routes to the admin function, stripped before FastAPI routes. Set by Terraform from `local.admin_base_path`; don't set it by hand. |
| `MR_MOUSE_STATS_ADMIN_DEV_AUTH` | admin API | **Local only.** `1` bypasses auth. Never set in AWS. |

---

## 5. Verification

Run all of these. The third is the one that cannot be skipped.

**Public API responds.**
```sh
API=$(terraform -chdir=infra output -raw api_base_url)
curl -s "$API/health"          # {"status":"ok","service":"public"}
curl -s "$API/stats" | head -c 200
```

**Admin API refuses anonymous callers.**
```sh
curl -i "$API/admin/overview"  # expect 401
```

**⚠️ The authorizer rejects *before invoke*.** A 401 alone proves nothing —
the application returns 401 too, and the entire design rests on the request
never reaching it. If the authorizer were misconfigured (wrong issuer, wrong
audience, attached to the wrong route) you would see identical 401s while
every anonymous request invoked the Lambda and opened a Neon connection.

```sh
curl -s "$API/admin/overview" >/dev/null

aws logs tail /aws/lambda/mr-mouse-stats-admin-api --since 5m
#   expect NO new entries  ← this is the actual assertion

aws logs tail /aws/apigateway/mr-mouse-stats --since 5m
#   expect an entry with a non-null authorizerErr
```

`authorizerErr` is in the access log format (`infra/api.tf`) specifically so
this is checkable rather than inferred. Pair it with the positive case: a
valid token should return 200 **and** produce an admin-function log entry. If
neither request logs, the route is misrouted rather than protected.

**Collector is running and writing.**
```sh
aws ecs describe-services --cluster mr-mouse-stats \
  --services mr-mouse-stats-collector --query 'services[0].runningCount'

psql "$NEON_DIRECT_DSN" -c \
  "select confirmed, count(*) from channel_join_status group by confirmed"
```
`channel_join_status` is written ~60s after startup. Rows appearing there is
proof of a live write path, not just a running container.

**Frontend.** Load the site, sign in, confirm the admin view lists failing
channels and candidates.

---

## 6. Operating it

### Routine deploys

```sh
./scripts/deploy.sh            # images + frontend
./scripts/deploy.sh images     # backend only
./scripts/deploy.sh frontend   # static site only
```

Images push, Lambdas roll onto the new image, and the ECS service gets
`--force-new-deployment`. HTML is synced with `max-age=0` and hashed assets
with `immutable`, then CloudFront is invalidated.

### Things not to change casually

**`desired_count = 1` on the collector.** Two tasks would double-join every
channel and both drive the Liquipedia rate gate. `twitch_messages` dedupes on
the Twitch message uuid so it would not corrupt data, but it doubles outbound
traffic for nothing.

**The scrape interval.** The in-process rate gate is correct by construction
*because* there is exactly one process holding one `LiquipediaClient`. Daily
is ample — rosters change on tournament boundaries, and the HTTP cache TTL is
24h, so a shorter interval mostly serves cache hits. Hourly would be ~120
requests/day against Liquipedia for near-zero new data.

**The collector's public subnet.** It has a public IP and no inbound rules.
Moving it to a private subnet requires a NAT Gateway at $32.85/month against
a task that costs ~$11. The client only connects outward and structurally
cannot accept connections or send a `PRIVMSG`.

### Monitoring

The alarm in `infra/collector.tf` fires when `RunningTaskCount < 1`. That is
the signal that matters: **a crash-looping collector looks exactly like a
quiet chat from the outside.** Worth adding an SNS target — the alarm has
none wired up.

Also worth watching: `unwritten` and `dropped` in the collector's shutdown
log line. Non-zero means the capture spool could not drain, which is the one
failure mode that loses data permanently.

### Rollback

- **Frontend** — S3 versioning is on; restore the previous object versions and
  invalidate.
- **Lambdas** — `aws lambda update-function-code` with a previous image digest.
- **Collector** — `aws ecs update-service --task-definition <previous-revision>`.
- **Schema** — no down-migrations. Additive changes only; restore from a Neon
  branch if you need to go back.

### Cost

Roughly **$13–15/month** steady state: ~$11 Fargate (0.25 vCPU ARM64 always-on
plus the public IPv4 charge), a few dollars of CloudFront/S3/Secrets Manager,
and Lambda + API Gateway effectively free at this traffic. Neon is separate.

The failure mode to avoid is a NAT Gateway, which would add $32.85/month on
its own.

---

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `terraform apply` fails creating a Lambda | Image not in ECR. Do §3.1–3.2 first. |
| `exec format error` in Lambda or ECS logs | amd64 image on an arm64 function. Install QEMU (§2) and rebuild with `--platform linux/arm64`. |
| Admin API 401s with a valid token | Authorizer `audience` must be the **app client id** and `issuer` the user pool endpoint. Check `infra/api.tf`. |
| Admin API 404s every route with a valid token | The gateway prefix is not being stripped. The route key, the `MR_MOUSE_STATS_ADMIN_BASE_PATH` env var, and `admin_api_base_url` all derive from `local.admin_base_path` — check the deployed function actually has the env var. Locally the app is served at the root, so this only shows up in AWS. |
| Login redirects then fails | Cognito `callback_urls` must exactly match `${site_origin}/auth/callback/`, trailing slash included. |
| CORS errors in the browser | `MR_MOUSE_STATS_CORS_ORIGINS` and the gateway's `allow_origins` both derive from `site_origin`; a custom domain added later needs a re-apply. |
| Collector connects but records nothing | Expected if nobody types `!dpi`. Confirm liveness via `channel_join_status`, not observation counts. |
| Lambda times out on the first call | Neon scale-to-zero cold start. Raise the 15s timeout or keep the Neon endpoint warm. |
| `too many connections` on Neon | The direct endpoint is in the secret. Both functions read the same one, so it must be the pooled DSN — check with `aws secretsmanager get-secret-value --secret-id mr-mouse-stats/database-dsn`. |
| `psql: connection to server on socket "/var/run/postgresql/…" failed` | `$NEON_DIRECT_DSN` is empty, so `psql` fell back to a local socket that isn't there (the dev Postgres is a container on 55432). Quote the DSN in `.env` — see §2. |
| `ERROR: unrecognized configuration parameter "transaction_timeout"` | pg_dump 17 output replayed into Neon's Postgres 16. Dump with a client at or below the server's major version — §3.5. |
| `duplicate key value violates unique constraint "schema_migrations_pkey"` | The data dump includes `schema_migrations`, which §3.4 already populated. `--exclude-table=schema_migrations`. |
| `ResourceNotFoundException` / `AccessDeniedException` at cold start | The Lambda cannot read the DSN secret. Check `MR_MOUSE_STATS_DB_SECRET_ARN` on the function and the `read-database-dsn` policy on its role. |
| Lambda connects to `localhost:55432` | The deployed image predates the Secrets Manager fallback in `config.db()`, so it fell through to the dev default. Push the image, then apply. |

---

## 8. Still on the old stack

The Flask dashboard, `build-site`, and the systemd collector unit all still
work and are deliberately still in the tree. Retire them only once the hosted
stack is serving traffic — see `MIGRATION.md` §8 item 7. Run the Fargate
collector in parallel with the systemd unit for 48h first; `twitch_messages`
dedupes on the Twitch message uuid, so double-collection is harmless and
gives you a direct comparison.

Keep `build-site` past the cutover regardless. It is what caught the
collation change in `MIGRATION.md` §7.1, and diffing its output against a
known-good site is a cheap end-to-end check on the entire read path.
