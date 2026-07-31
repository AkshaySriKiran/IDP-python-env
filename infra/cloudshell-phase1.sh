#!/usr/bin/env bash
# Run in AWS CloudShell (browser) — region MUST be eu-north-1.
# Creates/updates the stack with DesiredCount=0, then prints an ECR login
# password for Mac Docker (no local AWS CLI login needed on the Mac).
#
# Usage (CloudShell):
#   git clone https://github.com/AkshaySriKiran/IDP-python-env.git
#   cd IDP-python-env
#   chmod +x infra/cloudshell-phase1.sh
#   ./infra/cloudshell-phase1.sh
#
# Then on Mac: follow printed docker login / build / push commands
# (or run infra/mac-push-ecr.sh after pasting the password).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STACK_NAME="${STACK_NAME:-omniparse-idp}"
PROJECT_NAME="${PROJECT_NAME:-omniparse-idp}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-north-1}}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"
GEMINI_API_KEY="${GEMINI_API_KEY:-}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

export AWS_DEFAULT_REGION="$AWS_REGION"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need aws

echo "==> Region: $AWS_REGION (must be eu-north-1 for this pilot)"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text)"
echo "==> CloudShell identity: account=$ACCOUNT_ID arn=$CALLER_ARN"

if [[ "$AWS_REGION" != "eu-north-1" ]]; then
  echo "WARNING: expected eu-north-1; switch CloudShell region or export AWS_REGION=eu-north-1" >&2
fi

params=(
  "ProjectName=$PROJECT_NAME"
  "DesiredCount=0"
  "ImageTag=$IMAGE_TAG"
  "GeminiModel=$GEMINI_MODEL"
)
if [[ -n "$GEMINI_API_KEY" ]]; then
  params+=("GeminiApiKey=$GEMINI_API_KEY")
fi

STATUS="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].StackStatus' --output text 2>/dev/null || true)"
if [[ "$STATUS" == "ROLLBACK_FAILED" || "$STATUS" == "DELETE_FAILED" ]]; then
  echo "Stack $STACK_NAME is $STATUS (often an orphaned LogGroup)." >&2
  echo "Clear it, then re-run this script:" >&2
  echo "  aws cloudformation delete-stack --stack-name $STACK_NAME --region $AWS_REGION --retain-resources LogGroup" >&2
  echo "  aws cloudformation wait stack-delete-complete --stack-name $STACK_NAME --region $AWS_REGION" >&2
  echo "Optional (needs logs:DeleteLogGroup):" >&2
  echo "  aws logs delete-log-group --log-group-name /ecs/${PROJECT_NAME} --region $AWS_REGION" >&2
  exit 1
fi

echo "==> CloudFormation deploy (DesiredCount=0 until Mac pushes the image)..."
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
CF_URL="$(cfn_output CloudFrontUrl)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo
echo "==> Fetching ECR login password (valid ~12h)..."
ECR_PASSWORD="$(aws ecr get-login-password --region "$AWS_REGION")"

echo
echo "========================================================================"
echo "MAC — Docker Desktop must be running. Paste the password into login:"
echo "========================================================================"
echo
echo "export AWS_REGION=$AWS_REGION"
echo "export ECR_URI=$ECR_URI"
echo "export IMAGE_TAG=<git-short-sha-or-manual-tag>"
echo
echo "# Option A: paste password interactively"
echo "aws_ecr_password='<PASTE_PASSWORD_BELOW>'"
echo "echo \"\$aws_ecr_password\" | docker login --username AWS --password-stdin $REGISTRY"
echo
echo "# Option B: helper script (from repo root on Mac)"
echo "#   export ECR_URI=$ECR_URI"
echo "#   export IMAGE_TAG=\$(git rev-parse --short HEAD)"
echo "#   ./infra/mac-push-ecr.sh   # prompts for password, or reads ECR_PASSWORD"
echo
echo "----- BEGIN ECR PASSWORD (copy entire line) -----"
echo "$ECR_PASSWORD"
echo "----- END ECR PASSWORD -----"
echo
echo "Then build & push:"
echo "  docker build -t ${PROJECT_NAME}-api:\$IMAGE_TAG -f backend/Dockerfile backend"
echo "  docker tag ${PROJECT_NAME}-api:\$IMAGE_TAG \$ECR_URI:\$IMAGE_TAG"
echo "  docker tag ${PROJECT_NAME}-api:\$IMAGE_TAG \$ECR_URI:latest"
echo "  docker push \$ECR_URI:\$IMAGE_TAG"
echo "  docker push \$ECR_URI:latest"
echo
echo "After push succeeds, back in CloudShell run:"
echo "  export IMAGE_TAG=<same-tag-you-pushed>"
echo "  ./infra/cloudshell-phase3.sh"
echo
echo "Stack outputs so far:"
echo "  ECR:      $ECR_URI"
echo "  UI bucket:$UI_BUCKET"
echo "  CF URL:   $CF_URL  (API not ready until phase3)"
echo "========================================================================"
