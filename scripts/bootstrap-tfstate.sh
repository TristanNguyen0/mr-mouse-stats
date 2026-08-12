#!/usr/bin/env bash
# Create the S3 bucket that holds the Terraform state.
#
# Run once, before the first `terraform init`. Not managed by Terraform: the
# bucket has to exist before there is anywhere to record that it exists.
#
# Idempotent — re-running it on an existing bucket reapplies the same settings.
# Afterwards:
#
#   terraform -chdir=infra init -migrate-state
#   terraform -chdir=infra plan     # must report no changes

set -euo pipefail

BUCKET="${TFSTATE_BUCKET:-mr-mouse-stats-tfstate}"
REGION="${AWS_REGION:-us-east-1}"

if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "==> bucket $BUCKET already exists, reapplying settings"
else
  echo "==> creating s3://$BUCKET in $REGION"
  # us-east-1 is the one region that rejects a LocationConstraint.
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
fi

# Versioning is the recovery path for a truncated or corrupted state file.
# Without it a bad apply is unrecoverable.
echo "==> enabling versioning"
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

# The state contains the Neon DSN in plaintext — see variables.tf.
echo "==> enabling encryption"
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'

echo "==> blocking public access"
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'

# Versions accumulate forever otherwise. 90 days is well past the point where
# rolling back to an old state is the right move.
echo "==> expiring noncurrent versions after 90 days"
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" \
  --lifecycle-configuration '{
    "Rules": [{
      "ID": "expire-noncurrent-state",
      "Status": "Enabled",
      "Filter": {"Prefix": ""},
      "NoncurrentVersionExpiration": {"NoncurrentDays": 90},
      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}
    }]
  }'

echo "==> done: s3://$BUCKET"
