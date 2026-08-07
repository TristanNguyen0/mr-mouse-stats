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
  # Lambda cannot read Secrets Manager into an env var natively, so the DSN
  # is injected as a secret reference resolved by the Parameters and Secrets
  # extension. Simplest correct alternative: pass it directly and accept it
  # being visible in the function configuration.
  lambda_environment = {
    MR_MOUSE_STATS_DB           = var.neon_dsn
    MR_MOUSE_STATS_CORS_ORIGINS = local.site_origin
    TZ                          = "UTC"
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

  environment {
    variables = local.lambda_environment
  }
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
  route_key          = "ANY /admin/{proxy+}"
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
