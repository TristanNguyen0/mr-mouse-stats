variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "mr-mouse-stats"
}

variable "region" {
  description = "AWS region. Cost figures in MIGRATION.md are us-east-1 list prices."
  type        = string
  default     = "us-east-1"
}

variable "neon_dsn" {
  description = <<-EOT
    Postgres DSN for Neon. Use the POOLED endpoint (-pooler in the host): the
    Lambdas scale horizontally and would otherwise exhaust Neon's direct
    connection limit. The direct endpoint is for `migrate` and the one-off
    import, run by hand, and has no business being in here.

    Goes into Secrets Manager; every runtime reads it from there. Set it in
    secrets.auto.tfvars, which is gitignored and auto-loaded by every
    terraform command — not -var=, which lands in shell history and in `ps`
    output, and not TF_VAR_neon_dsn, which is only set in the one shell that
    exported it. Note that `sensitive` only suppresses CLI output — the value
    is still plaintext in terraform.tfstate, which now lives in S3. That is
    why the state bucket is private, encrypted, and versioned, and why the
    deploy role gets read-only access to it.
  EOT
  type        = string
  sensitive   = true

  # Without this, an unset variable answered with an empty line at the
  # interactive prompt only fails partway through apply, as a 400 from
  # PutSecretValue ("You must provide either SecretString or SecretBinary").
  validation {
    condition     = can(regex("^postgres(ql)?://", var.neon_dsn))
    error_message = "neon_dsn must be a postgres:// or postgresql:// URL. Set it in secrets.auto.tfvars."
  }

  # secrets.auto.tfvars ships with a placeholder that satisfies the rule above.
  # Left unedited it would apply cleanly and put an unusable DSN in Secrets
  # Manager, moving the failure from apply to runtime.
  validation {
    condition     = !can(regex("REPLACE_ME", var.neon_dsn))
    error_message = "neon_dsn is still the placeholder from secrets.auto.tfvars. Put the real Neon pooled DSN there."
  }
}

variable "tournaments" {
  description = "Liquipedia tournament pages the scheduled scrape refreshes."
  type        = list(string)
  default     = ["MR_Ignite/2026/Mid_Season_Finals"]
}

variable "scrape_interval_seconds" {
  description = <<-EOT
    Seconds between Liquipedia refreshes inside the Fargate task. Daily by
    default: rosters change on tournament boundaries and the HTTP cache TTL
    is 24h, so anything more frequent mostly serves cache hits.
  EOT
  type        = number
  default     = 86400
}

variable "parse_interval_seconds" {
  description = <<-EOT
    Seconds between settings-derivation passes inside the Fargate task. Five
    minutes: the pass only looks at messages with no observation yet, so it
    costs nothing when chat has been quiet, and without it the task collects
    raw messages that never become readings on the site.
  EOT
  type        = number
  default     = 300
}

variable "site_domain" {
  description = "Optional custom domain for the frontend. Empty uses the CloudFront domain."
  type        = string
  default     = ""
}

variable "acm_certificate_arn" {
  description = "us-east-1 ACM certificate for site_domain. Required if site_domain is set."
  type        = string
  default     = ""
}

variable "admin_emails" {
  description = <<-EOT
    Emails seeded as Cognito admin users. They receive a temporary password.

    Deliberately has no default: `[]` is a valid value that silently destroys
    every existing admin user (they are a for_each over this list), so an
    apply that forgets to pass it must fail rather than proceed. Lives in
    secrets.auto.tfvars so every invocation sees it.
  EOT
  type        = list(string)
}

variable "collector_cpu" {
  description = "Fargate vCPU units. 256 = 0.25 vCPU, far more than this workload needs."
  type        = number
  default     = 256
}

variable "collector_memory" {
  description = "Fargate memory (MiB)."
  type        = number
  default     = 512
}

variable "github_repository" {
  description = <<-EOT
    owner/name of the GitHub repository allowed to assume the deploy role.
    Baked into the OIDC trust condition, so a typo here means the deploy
    workflow cannot authenticate — and nothing else can either.
  EOT
  type        = string
  default     = "TristanNguyen0/mr-mouse-stats"

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.github_repository))
    error_message = "github_repository must be in owner/name form."
  }
}

# GitHub now stamps the numeric account and repository IDs into the OIDC
# subject claim alongside the names, so both are needed to match it. They are
# the immutable half of the identity: names can be renamed and re-registered by
# someone else, IDs cannot. Read them from the public API:
#
#   curl -s https://api.github.com/repos/<owner>/<name> | jq '.id, .owner.id'
#
# (repository id first, owner id second).
variable "github_owner_id" {
  description = "Numeric GitHub account ID of the repository owner."
  type        = string
  default     = "99143948"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "github_owner_id must be all digits."
  }
}

variable "github_repository_id" {
  description = "Numeric GitHub ID of the repository itself."
  type        = string
  default     = "1318869120"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "github_repository_id must be all digits."
  }
}

variable "tfstate_bucket" {
  description = <<-EOT
    Bucket holding the remote state, granted read-only to the deploy role so
    the workflow can run `terraform output`.

    Must match the bucket in the `backend "s3"` block in main.tf. It cannot be
    derived from it: backend blocks are evaluated before variables exist and
    so cannot take expressions. Created by scripts/bootstrap-tfstate.sh.
  EOT
  type        = string
  default     = "mr-mouse-stats-tfstate"
}
