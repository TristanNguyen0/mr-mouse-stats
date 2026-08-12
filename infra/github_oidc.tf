# The identity GitHub Actions deploys as.
#
# OIDC rather than an IAM user with an access key: the workflow exchanges a
# short-lived GitHub token for temporary credentials, so there is no long-lived
# secret in the repository to leak or rotate. This is the main reason CI/CD
# lives on GitHub Actions rather than somewhere that would need static keys.

# No thumbprint_list: since 2023 AWS validates this endpoint against its own
# trust store and ignores the value, and a hardcoded fingerprint only rots.
#
# If the account already has a provider for this URL (any other repository
# using GitHub OIDC would have created one), this apply fails with
# EntityAlreadyExists. Import it rather than making a second one — the URL is
# unique per account:
#
#   terraform import aws_iam_openid_connect_provider.github \
#     arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

# The subject claim GitHub actually presents, which is *not* the plain
# `repo:OWNER/REPO:...` form most examples show. GitHub interpolates the
# numeric account and repository IDs after each name:
#
#   repo:OWNER@<owner-id>/REPO@<repo-id>:environment:production
#
# IAM compares `sub` as an opaque string, so a policy written against the
# name-only form matches nothing and every deploy fails with "Not authorized to
# perform sts:AssumeRoleWithWebIdentity" — the role is found, the condition
# just never evaluates true. Verify the live value from a failed attempt rather
# than assuming this shape:
#
#   aws cloudtrail lookup-events --max-results 1 \
#     --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity \
#     --query 'Events[0].Username' --output text
#
# Matching the IDs is also what makes the condition rename-proof: if this
# account or repository is ever renamed and someone else registers the old
# name, their tokens carry different IDs and are refused.
locals {
  github_owner = split("/", var.github_repository)[0]
  github_name  = split("/", var.github_repository)[1]

  github_oidc_sub = join("", [
    "repo:${local.github_owner}@${var.github_owner_id}",
    "/${local.github_name}@${var.github_repository_id}",
    ":environment:production",
  ])
}

# The trust condition binds to the *environment*, not the branch.
#
# A job that declares `environment: production` gets a token whose `sub` ends
# in `:environment:production`, and GitHub only issues it after the
# environment's required reviewers have approved. So the approval gate is not
# merely a UI convention: without it, no token that can assume this role is
# ever minted. A `ref:refs/heads/main` condition would be weaker — every push
# to main could assume the role unattended.
resource "aws_iam_role" "github_deploy" {
  name        = "${local.name}-github-deploy"
  description = "Assumed by the GitHub Actions deploy workflow via OIDC."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = local.github_oidc_sub
        }
      }
    }]
  })
}

# Exactly what scripts/deploy.sh does, and nothing else. In particular there is
# no iam:PassRole: `update-service --force-new-deployment` reuses the existing
# task definition, so no role is ever passed at deploy time. Adding
# RegisterTaskDefinition later would change that.
resource "aws_iam_role_policy" "github_deploy" {
  name = "deploy"
  role = aws_iam_role.github_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*" # the API takes no resource for this call
      },
      {
        Sid    = "EcrPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:DescribeImages",
        ]
        Resource = [
          aws_ecr_repository.api.arn,
          aws_ecr_repository.collector.arn,
        ]
      },
      {
        Sid    = "RollLambdas"
        Effect = "Allow"
        Action = [
          "lambda:UpdateFunctionCode",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
        ]
        Resource = [
          aws_lambda_function.public.arn,
          aws_lambda_function.admin.arn,
        ]
      },
      {
        Sid      = "RestartCollector"
        Effect   = "Allow"
        Action   = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = aws_ecs_service.collector.id
      },
      {
        Sid      = "SyncSite"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = aws_s3_bucket.site.arn
      },
      {
        Sid      = "SyncSiteObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.site.arn}/*"
      },
      {
        Sid      = "InvalidateCache"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
        Resource = aws_cloudfront_distribution.site.arn
      },
      # The direct DSN, for the migrate step. Scoped to that one secret: the
      # pooled DSN next to it is the runtimes' credential and the deploy role
      # has no reason to hold it.
      {
        Sid      = "ReadDirectDsn"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_secretsmanager_secret.database_direct.arn
      },
      # Read-only on the state bucket. The workflow runs `terraform output` to
      # resolve names; it never applies, so it needs no write and no lock.
      {
        Sid      = "ReadTerraformState"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::${var.tfstate_bucket}"
      },
      {
        Sid      = "ReadTerraformStateObject"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "arn:aws:s3:::${var.tfstate_bucket}/*"
      },
    ]
  })
}
