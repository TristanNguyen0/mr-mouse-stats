output "api_base_url" {
  description = "Public API base. Build the frontend with NEXT_PUBLIC_API_BASE set to this."
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "admin_api_base_url" {
  description = "Admin API base — NEXT_PUBLIC_ADMIN_API_BASE."
  value       = "${aws_apigatewayv2_api.main.api_endpoint}${local.admin_base_path}"
}

output "cognito_domain" {
  description = "NEXT_PUBLIC_COGNITO_DOMAIN"
  value       = "${aws_cognito_user_pool_domain.admin.domain}.auth.${var.region}.amazoncognito.com"
}

output "cognito_client_id" {
  description = "NEXT_PUBLIC_COGNITO_CLIENT_ID"
  value       = aws_cognito_user_pool_client.web.id
}

output "cognito_region" {
  description = "NEXT_PUBLIC_COGNITO_REGION"
  value       = var.region
}

output "site_url" {
  description = "Where the frontend is served."
  value       = local.site_origin
}

output "site_bucket" {
  description = "Sync the Next.js export here."
  value       = aws_s3_bucket.site.id
}

output "cloudfront_distribution_id" {
  description = "Needed to invalidate after a deploy."
  value       = aws_cloudfront_distribution.site.id
}

output "ecr_api_repository" {
  value = aws_ecr_repository.api.repository_url
}

output "ecr_collector_repository" {
  value = aws_ecr_repository.collector.repository_url
}
