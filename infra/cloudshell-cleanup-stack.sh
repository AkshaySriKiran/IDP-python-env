#!/usr/bin/env bash
# CloudShell helper: remove a stuck omniparse-idp stack (e.g. ROLLBACK_FAILED).
# If LogGroup delete is denied, retains it so the stack can still be removed.
#
# Usage:
#   ./infra/cloudshell-cleanup-stack.sh

set -euo pipefail

STACK_NAME="${STACK_NAME:-omniparse-idp}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-north-1}}"
export AWS_DEFAULT_REGION="$AWS_REGION"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need aws

if ! aws cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
  echo "Stack $STACK_NAME not found in $AWS_REGION — already clean."
  exit 0
fi

STATUS="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query 'Stacks[0].StackStatus' \
  --output text)"
echo "==> Stack $STACK_NAME status: $STATUS"

echo "==> Attempting normal delete..."
aws cloudformation delete-stack --stack-name "$STACK_NAME" || true

if aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" 2>/dev/null; then
  echo "Stack deleted."
  exit 0
fi

echo "==> Normal delete did not finish. Checking DELETE_FAILED resources..."
FAILED="$(aws cloudformation describe-stack-events \
  --stack-name "$STACK_NAME" \
  --query "StackEvents[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" \
  --output text 2>/dev/null | tr '\t' '\n' | sort -u | tr '\n' ' ' || true)"
FAILED="$(echo "$FAILED" | xargs)"

if [[ -z "$FAILED" ]]; then
  # Common stuck case for this pilot
  FAILED="LogGroup"
fi

echo "==> Retaining resources and deleting stack: $FAILED"
# shellcheck disable=SC2086
aws cloudformation delete-stack \
  --stack-name "$STACK_NAME" \
  --retain-resources $FAILED

echo "==> Waiting for delete (retained resources are left in the account)..."
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
echo "Stack $STACK_NAME removed. Retained: $FAILED"
echo "IT can later delete orphan log group /ecs/omniparse-idp if present."
echo "New stacks use /ecs/omniparse-idp-api so create can proceed without deleting the orphan."
