#!/usr/bin/env bash
# Run on Mac (Docker Desktop). No AWS CLI login required.
# Uses an ECR password generated in AWS CloudShell (see cloudshell-phase1.sh).
#
# Usage:
#   export ECR_URI=912564796433.dkr.ecr.eu-north-1.amazonaws.com/omniparse-idp
#   export IMAGE_TAG=$(git rev-parse --short HEAD)
#   export ECR_PASSWORD='...'   # optional; otherwise prompts
#   ./infra/mac-push-ecr.sh
#
# Or pipe password:
#   pbpaste | ./infra/mac-push-ecr.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_NAME="${PROJECT_NAME:-omniparse-idp}"
AWS_REGION="${AWS_REGION:-eu-north-1}"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
ECR_URI="${ECR_URI:-}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need docker

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is not running. Start it, then retry." >&2
  exit 1
fi

if [[ -z "$ECR_URI" ]]; then
  echo "Set ECR_URI to the repo from CloudShell phase1 output." >&2
  echo "  export ECR_URI=ACCOUNT.dkr.ecr.eu-north-1.amazonaws.com/omniparse-idp" >&2
  exit 1
fi

REGISTRY="${ECR_URI%%/*}"

if [[ -z "${ECR_PASSWORD:-}" ]]; then
  if [[ -t 0 ]]; then
    echo "Paste ECR password from CloudShell (input hidden), then Enter:"
    # shellcheck disable=SC2162
    read -s ECR_PASSWORD
    echo
  else
    ECR_PASSWORD="$(cat)"
  fi
fi

if [[ -z "${ECR_PASSWORD}" ]]; then
  echo "Empty ECR password." >&2
  exit 1
fi

echo "==> docker login $REGISTRY"
echo "$ECR_PASSWORD" | docker login --username AWS --password-stdin "$REGISTRY"

echo "==> Building API image for linux/amd64 ($PROJECT_NAME-api:$IMAGE_TAG)..."
docker build --platform linux/amd64 -t "$PROJECT_NAME-api:$IMAGE_TAG" -f "$ROOT/backend/Dockerfile" "$ROOT/backend"
docker tag "$PROJECT_NAME-api:$IMAGE_TAG" "$ECR_URI:$IMAGE_TAG"
docker tag "$PROJECT_NAME-api:$IMAGE_TAG" "$ECR_URI:latest"

echo "==> Pushing $ECR_URI:$IMAGE_TAG ..."
docker push "$ECR_URI:$IMAGE_TAG"
docker push "$ECR_URI:latest"

echo
echo "Push complete."
echo "  Image: $ECR_URI:$IMAGE_TAG"
echo
echo "Back in CloudShell:"
echo "  export IMAGE_TAG=$IMAGE_TAG"
echo "  ./infra/cloudshell-phase3.sh"
