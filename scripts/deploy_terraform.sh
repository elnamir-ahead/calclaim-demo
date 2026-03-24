#!/usr/bin/env bash
# Local deploy: build Lambda + Terraform apply (same flow as policy-agent/scripts/deploy.sh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TF_DIR="$PROJECT_ROOT/terraform"
REGION="${AWS_REGION:-us-east-1}"

chmod +x "$SCRIPT_DIR/build_lambda.sh"
"$SCRIPT_DIR/build_lambda.sh"

echo "==> Terraform..."
cd "$TF_DIR"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
TFSTATE_BUCKET="calclaim-tfstate-${ACCOUNT_ID}"
aws s3 mb "s3://${TFSTATE_BUCKET}" --region "$REGION" 2>/dev/null || true

terraform init -upgrade \
  -backend-config="bucket=${TFSTATE_BUCKET}" \
  -backend-config="key=calclaim-demo/terraform.tfstate" \
  -backend-config="region=${REGION}"

terraform apply -auto-approve

echo ""
echo "==> API base URL:"
terraform output -raw api_base_url
echo ""
echo "OpenAPI docs (if enabled): $(terraform output -raw api_base_url)docs"
echo ""
