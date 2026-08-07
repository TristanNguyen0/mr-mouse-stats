#!/usr/bin/env bash
# Build and push both images, build the frontend, sync it to S3, invalidate.
#
# Reads every identifier from terraform outputs, so it cannot drift from the
# deployed stack. Run from the repository root after `terraform apply`.
#
#   ./scripts/deploy.sh            # everything
#   ./scripts/deploy.sh frontend   # just the static site
#   ./scripts/deploy.sh images     # just the containers

set -euo pipefail

TARGET="${1:-all}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

tf() { terraform -chdir=infra output -raw "$1"; }

REGION="$(tf cognito_region)"
API_REPO="$(tf ecr_api_repository)"
COLLECTOR_REPO="$(tf ecr_collector_repository)"
REGISTRY="${API_REPO%%/*}"

if [[ "$TARGET" == "all" || "$TARGET" == "images" ]]; then
  echo "==> logging in to ECR"
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY"

  # Both images are ARM64: Graviton Lambda and Fargate are ~20% cheaper and
  # nothing in this codebase is architecture-sensitive.
  echo "==> building api image (arm64)"
  docker buildx build --platform linux/arm64 -f Dockerfile.lambda \
    -t "$API_REPO:latest" --push .

  echo "==> building collector image (arm64)"
  docker buildx build --platform linux/arm64 -f Dockerfile \
    -t "$COLLECTOR_REPO:latest" --push .

  echo "==> rolling the lambdas onto the new image"
  for fn in public-api admin-api; do
    aws lambda update-function-code --region "$REGION" \
      --function-name "mr-mouse-stats-$fn" \
      --image-uri "$API_REPO:latest" >/dev/null
  done

  echo "==> restarting the collector"
  aws ecs update-service --region "$REGION" \
    --cluster mr-mouse-stats --service mr-mouse-stats-collector \
    --force-new-deployment >/dev/null
fi

if [[ "$TARGET" == "all" || "$TARGET" == "frontend" ]]; then
  echo "==> building frontend"
  # Baked in at build time: a static export has no server to read env at runtime.
  NEXT_PUBLIC_API_BASE="$(tf api_base_url)" \
  NEXT_PUBLIC_ADMIN_API_BASE="$(tf admin_api_base_url)" \
  NEXT_PUBLIC_COGNITO_DOMAIN="$(tf cognito_domain)" \
  NEXT_PUBLIC_COGNITO_CLIENT_ID="$(tf cognito_client_id)" \
  NEXT_PUBLIC_COGNITO_REGION="$REGION" \
    npm --prefix frontend run build

  BUCKET="$(tf site_bucket)"
  echo "==> syncing to s3://$BUCKET"
  # Hashed assets cache forever; HTML must not, or a deploy is invisible.
  aws s3 sync frontend/out "s3://$BUCKET" --delete \
    --exclude "*.html" --cache-control "public,max-age=31536000,immutable"
  aws s3 sync frontend/out "s3://$BUCKET" --delete \
    --exclude "*" --include "*.html" --cache-control "public,max-age=0,must-revalidate"

  echo "==> invalidating cloudfront"
  aws cloudfront create-invalidation \
    --distribution-id "$(tf cloudfront_distribution_id)" \
    --paths "/*" >/dev/null
fi

echo "==> done: $(tf site_url)"
