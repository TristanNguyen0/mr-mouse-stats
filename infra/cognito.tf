# Cognito user pool backing the admin console.
#
# Deliberately closed: no self-signup, no unauthenticated identity pool.
# Admins are created explicitly via var.admin_emails or the console.

resource "aws_cognito_user_pool" "admin" {
  name = "${local.name}-admin"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration {
    enabled = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_domain" "admin" {
  domain       = "${local.name}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.admin.id
}

# Public client: a static site cannot hold a secret, so this uses the
# authorization-code flow with PKCE (see frontend/lib/auth.ts) rather than
# the implicit flow, which would put tokens in the URL fragment.
resource "aws_cognito_user_pool_client" "web" {
  name         = "${local.name}-web"
  user_pool_id = aws_cognito_user_pool.admin.id

  generate_secret = false

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = compact([
    "${local.site_origin}/auth/callback/",
    "http://localhost:3000/auth/callback/", # local development
  ])
  logout_urls = compact([local.site_origin, "http://localhost:3000"])

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
}

resource "aws_cognito_user" "admins" {
  for_each     = toset(var.admin_emails)
  user_pool_id = aws_cognito_user_pool.admin.id
  username     = each.value

  attributes = {
    email          = each.value
    email_verified = true
  }
}
