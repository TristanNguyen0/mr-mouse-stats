# Two Lambdas behind one HTTP API, on separate routes.
#
# The public routes are open; every /admin/* route carries the Cognito JWT
# authorizer, so API Gateway rejects unauthorized requests BEFORE the admin
# Lambda is invoked. That is the security boundary — the app-level check in
# deps.require_admin is defence in depth, not the primary control.

resource "aws_iam_role" "lambda" {
  name = "${local.name}-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_secrets" {
  name = "read-database-dsn"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.database.arn
    }]
  })
}

locals {
  # The one place the admin prefix is written. It drives three things that
  # must agree or every admin route 404s: the gateway route key, the base URL
  # the frontend is built against, and the prefix Mangum strips back off
  # before FastAPI routes the request. Change it here and all three follow.
  admin_base_path = "/admin"

  # The DSN is deliberately not a value here. Lambda cannot resolve a secret
  # reference into an env var natively, and a plain value would be readable in
  # the console and in `aws lambda get-function-configuration`. So the
  # functions get the ARN and `config.db()` resolves it through the role grant
  # above, once per cold start. Fargate reaches the same secret through the
  # ECS `secrets` block, which does the resolution for it.
  lambda_environment = {
    MR_MOUSE_STATS_DB_SECRET_ARN = aws_secretsmanager_secret.database.arn
    MR_MOUSE_STATS_CORS_ORIGINS  = local.site_origin
    TZ                           = "UTC"
  }
}

resource "aws_lambda_function" "public" {
  function_name = "${local.name}-public-api"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.api.repository_url}:latest"
  architectures = ["arm64"]
  timeout       = 15
  memory_size   = 512

  image_config {
    command = ["mr_mouse_stats.api.lambda_handlers.public_handler"]
  }

  environment {
    variables = local.lambda_environment
  }

  # The ARN alone is not enough: without a version, the first cold start
  # fails on ResourceNotFoundException.
  depends_on = [aws_secretsmanager_secret_version.database]
}

resource "aws_lambda_function" "admin" {
  function_name = "${local.name}-admin-api"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.api.repository_url}:latest"
  architectures = ["arm64"]
  timeout       = 15
  memory_size   = 512

  image_config {
    command = ["mr_mouse_stats.api.lambda_handlers.admin_handler"]
  }

  # Admin-only: the public function is routed at the root and has no prefix
  # to strip.
  environment {
    variables = merge(local.lambda_environment, {
      MR_MOUSE_STATS_ADMIN_BASE_PATH = local.admin_base_path
    })
  }

  depends_on = [aws_secretsmanager_secret_version.database]
}

resource "aws_cloudwatch_log_group" "public" {
  name              = "/aws/lambda/${aws_lambda_function.public.function_name}"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "admin" {
  name              = "/aws/lambda/${aws_lambda_function.admin.function_name}"
  retention_in_days = 90 # longer: this is the audit trail for every write
}

resource "aws_apigatewayv2_api" "main" {
  name          = local.name
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = [local.site_origin]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.main.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${local.name}-cognito"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.web.id]
    issuer   = "https://${aws_cognito_user_pool.admin.endpoint}"
  }
}

resource "aws_apigatewayv2_integration" "public" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.public.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "admin" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.admin.invoke_arn
  payload_format_version = "2.0"
}

# Public: everything not under /admin.
resource "aws_apigatewayv2_route" "public" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.public.id}"
}

# Admin: authorizer attached. Requests without a valid Cognito token are
# rejected here, and the admin Lambda is never invoked.
resource "aws_apigatewayv2_route" "admin" {
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = "ANY ${local.admin_base_path}/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.admin.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_lambda_permission" "public" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.public.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "admin" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.admin.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = 30
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    # authorizer.error lets you confirm rejections happen at the gateway.
    format = jsonencode({
      requestId      = "$context.requestId"
      httpMethod     = "$context.httpMethod"
      path           = "$context.path"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      authorizerErr  = "$context.authorizer.error"
      integrationErr = "$context.integrationErrorMessage"
    })
  }
}
