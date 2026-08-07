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
    Postgres DSN for Neon. Use the POOLED endpoint (-pooler in the host) for
    the Lambdas: they scale horizontally and would otherwise exhaust Neon's
    direct connection limit. Stored in Secrets Manager, never in state as
    plaintext output.
  EOT
  type        = string
  sensitive   = true
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
  description = "Emails seeded as Cognito admin users. They receive a temporary password."
  type        = list(string)
  default     = []
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
