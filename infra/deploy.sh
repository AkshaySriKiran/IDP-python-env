#!/usr/bin/env bash
# Deploy OmniParse IDP to AWS (S3/CloudFront UI + ECS Fargate API).
#
# Prerequisites:
#   - aws CLI v2 configured (credentials + default region)
#   - docker running
#
# Usage:
#   export AWS_REGION=us-east-1
#   export GEMINI_API_KEY=your_key   # optional; UI can still paste a key
#   ./infra/deploy.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STACK_NAME="${STACK_NAME:-omniparse-idp}"
PROJECT_NAME="${PROJECT_NAME:-omniparse-idp}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"
GEMINI_API_KEY="${GEMINI_API_KEY:-}"

export AWS_DEFAULT_REGION="$AWS_REGION"
export GEMINI_API_KEY GEMINI_MODEL

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need aws
need docker

echo "==> Region: $AWS_REGION | Stack: $STACK_NAME | Image tag: $IMAGE_TAG"

stack_exists() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1
}

cfn_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text
}

deploy_stack() {
  local desired="$1"
  local tag="$2"
  local params=(
    "ProjectName=$PROJECT_NAME"
    "DesiredCount=$desired"
    "ImageTag=$tag"
    "GeminiModel=$GEMINI_MODEL"
  )
  if [[ -n "$GEMINI_API_KEY" ]]; then
    params+=("GeminiApiKey=$GEMINI_API_KEY")
  fi

  echo "==> CloudFormation deploy (DesiredCount=$desired, ImageTag=$tag)..."
  aws cloudformation deploy \
    --stack-name "$STACK_NAME" \
    --template-file "$ROOT/infra/cloudformation.yml" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides "${params[@]}"
}

# ----- 1) Create stack (ECR/ALB/CF) with 0 tasks if missing -----
if ! stack_exists; then
  echo "==> First-time stack create (DesiredCount=0 until image exists)..."
  deploy_stack 0 latest
else
  echo "==> Stack already exists."
fi

ECR_URI="$(cfn_output EcrRepositoryUri)"
UI_BUCKET="$(cfn_output UiBucketName)"
CLUSTER="$(cfn_output EcsClusterName)"
SERVICE="$(cfn_output EcsServiceName)"
CF_URL="$(cfn_output CloudFrontUrl)"
DIST_ID="$(cfn_output CloudFrontDistributionId)"
SECRET_ARN="$(cfn_output SecretArn)"

echo "==> ECR: $ECR_URI"
echo "==> UI bucket: $UI_BUCKET"

# ----- 2) Build & push API image (before pointing ECS at the new tag) -----
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "==> ECR login..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

echo "==> Building API image..."
docker build -t "$PROJECT_NAME-api:$IMAGE_TAG" -f "$ROOT/backend/Dockerfile" "$ROOT/backend"
docker tag "$PROJECT_NAME-api:$IMAGE_TAG" "$ECR_URI:$IMAGE_TAG"
docker tag "$PROJECT_NAME-api:$IMAGE_TAG" "$ECR_URI:latest"

echo "==> Pushing $ECR_URI:$IMAGE_TAG ..."
docker push "$ECR_URI:$IMAGE_TAG"
docker push "$ECR_URI:latest"

# ----- 3) Roll ECS onto the new image -----
deploy_stack 1 "$IMAGE_TAG"

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

# ----- 4) Sync UI + invalidate -----
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
echo "Deploy complete."
echo "  UI + API:  $CF_URL"
echo "  Health:    ${CF_URL}/api/health"
echo
echo "Note: CloudFront origin timeout max is 180s. Use page ranges for long OCR jobs,"
echo "      or later add a custom domain + HTTPS ALB to bypass that limit."
