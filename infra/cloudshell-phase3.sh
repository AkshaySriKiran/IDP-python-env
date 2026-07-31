#!/usr/bin/env bash
# Run in AWS CloudShell AFTER Mac has pushed the API image to ECR.
# Scales ECS to 1, optionally sets Gemini secret, syncs UI from this checkout,
# invalidates CloudFront.
#
# Usage (CloudShell, from repo root):
#   export IMAGE_TAG=<tag-you-pushed-from-Mac>
#   export GEMINI_API_KEY=...   # optional
#   ./infra/cloudshell-phase3.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STACK_NAME="${STACK_NAME:-omniparse-idp}"
PROJECT_NAME="${PROJECT_NAME:-omniparse-idp}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-north-1}}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"
GEMINI_API_KEY="${GEMINI_API_KEY:-}"
IMAGE_TAG="${IMAGE_TAG:-}"

export AWS_DEFAULT_REGION="$AWS_REGION"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need aws

if [[ -z "$IMAGE_TAG" ]]; then
  echo "Set IMAGE_TAG to the tag pushed from Mac (e.g. export IMAGE_TAG=abc1234)." >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "==> CloudShell identity account=$ACCOUNT_ID region=$AWS_REGION image=$IMAGE_TAG"

params=(
  "ProjectName=$PROJECT_NAME"
  "DesiredCount=1"
  "ImageTag=$IMAGE_TAG"
  "GeminiModel=$GEMINI_MODEL"
)
if [[ -n "$GEMINI_API_KEY" ]]; then
  params+=("GeminiApiKey=$GEMINI_API_KEY")
fi

echo "==> CloudFormation deploy (DesiredCount=1, ImageTag=$IMAGE_TAG)..."
aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$ROOT/infra/cloudformation.yml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides "${params[@]}"

cfn_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text
}

ECR_URI="$(cfn_output EcrRepositoryUri)"
UI_BUCKET="$(cfn_output UiBucketName)"
CLUSTER="$(cfn_output EcsClusterName)"
SERVICE="$(cfn_output EcsServiceName)"
CF_URL="$(cfn_output CloudFrontUrl)"
DIST_ID="$(cfn_output CloudFrontDistributionId)"
SECRET_ARN="$(cfn_output SecretArn)"

echo "==> Forcing ECS deployment..."
aws ecs update-service \
  --cluster "$CLUSTER" \
  --service "$SERVICE" \
  --force-new-deployment \
  >/dev/null

if [[ -n "$GEMINI_API_KEY" ]]; then
  echo "==> Updating Secrets Manager Gemini key..."
  SECRET_JSON="$(python3 -c "import json,os; print(json.dumps({'GEMINI_API_KEY':os.environ['GEMINI_API_KEY'],'GEMINI_MODEL':os.environ.get('GEMINI_MODEL','gemini-3.5-flash')}))")"
  aws secretsmanager put-secret-value \
    --secret-id "$SECRET_ARN" \
    --secret-string "$SECRET_JSON" \
    >/dev/null
fi

echo "==> Syncing UI to s3://$UI_BUCKET ..."
aws s3 sync "$ROOT" "s3://$UI_BUCKET" \
  --exclude "*" \
  --include "index.html" \
  --include "app.js" \
  --include "styles.css" \
  --include "equipment_manifest.json" \
  --cache-control "public,max-age=60"

if [[ -n "$DIST_ID" && "$DIST_ID" != "None" ]]; then
  echo "==> Invalidating CloudFront $DIST_ID ..."
  aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" >/dev/null
fi

echo
echo "Deploy complete (Mac push + CloudShell finish)."
echo "  UI + API:  $CF_URL"
echo "  Health:    ${CF_URL}/api/health"
echo "  ECR image: $ECR_URI:$IMAGE_TAG"
echo "  Logs:      /ecs/${PROJECT_NAME}  (CloudWatch, region $AWS_REGION)"
echo
echo "Smoke-test: open CloudFront URL → header should show Python API Ready."
echo "Note: CloudFront origin timeout max is 180s — use page ranges for long OCR."
