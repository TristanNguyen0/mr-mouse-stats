terraform {
  # >= 1.11 for `use_lockfile` below: S3-native state locking via conditional
  # writes, which replaces the DynamoDB lock table the old S3 backend needed.
  required_version = ">= 1.11"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Remote state, so CI can read the outputs. `scripts/deploy.sh` and the
  # deploy workflow both resolve every AWS identifier through
  # `terraform output` — with state on one laptop, a runner cannot deploy.
  #
  # The bucket is created out of band (scripts/bootstrap-tfstate.sh), not
  # managed here: Terraform cannot hold the state that describes its own
  # state bucket without a chicken-and-egg on the very first apply.
  backend "s3" {
    bucket       = "mr-mouse-stats-tfstate"
    key          = "infra/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  name = var.project

  # The frontend origin, used for CORS on the public API and as the Cognito
  # callback. Falls back to the CloudFront domain when no custom domain is set.
  site_origin = var.site_domain != "" ? "https://${var.site_domain}" : "https://${aws_cloudfront_distribution.site.domain_name}"
}

# The DSN lives in Secrets Manager; tasks and functions read it at start.
# Putting it in plain environment variables would expose it in the console
# and in `terraform show`.
resource "aws_secretsmanager_secret" "database" {
  name                    = "${local.name}/database-dsn"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "database" {
  secret_id     = aws_secretsmanager_secret.database.id
  secret_string = var.neon_dsn
}

# Separate secret, separate reader: only the deploy role can read this one,
# and only to run `migrate`. Neither the Lambdas nor the collector are given
# access — they connect through the pooler, and a runtime that can reach the
# direct endpoint would quietly exhaust Neon's connection limit.
resource "aws_secretsmanager_secret" "database_direct" {
  name                    = "${local.name}/database-direct-dsn"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "database_direct" {
  secret_id     = aws_secretsmanager_secret.database_direct.id
  secret_string = var.neon_direct_dsn
}

resource "aws_ecr_repository" "api" {
  name                 = "${local.name}-api"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "collector" {
  name                 = "${local.name}-collector"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

# Keep the last 30 images; untagged layers otherwise accumulate forever.
# 30 rather than 10 because the deploy workflow tags every image with its
# commit SHA: this is exactly how far back a rollback can reach.
resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "collector" {
  repository = aws_ecr_repository.collector.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}
