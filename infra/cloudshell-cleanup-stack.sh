#!/usr/bin/env bash
# CloudShell helper: remove a stuck omniparse-idp stack (e.g. ROLLBACK_FAILED / DELETE_FAILED).
# If LogGroup (or other) delete is denied, retains only currently DELETE_FAILED resources.
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

current_delete_failed() {
  aws cloudformation describe-stack-resources \
    --stack-name "$STACK_NAME" \
    --query "StackResources[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" \
    --output text 2>/dev/null | tr '\t' '\n' | awk 'NF && $0 != "'"$STACK_NAME"'"' | sort -u | xargs || true
}

# Already stuck: skip a doomed normal delete and retain only live DELETE_FAILED resources.
if [[ "$STATUS" == "DELETE_FAILED" || "$STATUS" == "ROLLBACK_FAILED" ]]; then
  FAILED="$(current_delete_failed)"
  if [[ -z "$FAILED" ]]; then
    FAILED="LogGroup"
  fi
  echo "==> Retaining currently DELETE_FAILED resources: $FAILED"
  # shellcheck disable=SC2086
  aws cloudformation delete-stack \
    --stack-name "$STACK_NAME" \
    --retain-resources $FAILED
  echo "==> Waiting for delete..."
  aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
  echo "Stack $STACK_NAME removed. Retained: $FAILED"
  exit 0
fi

echo "==> Attempting normal delete..."
aws cloudformation delete-stack --stack-name "$STACK_NAME" || true

if aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" 2>/dev/null; then
  echo "Stack deleted."
  exit 0
fi

STATUS="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query 'Stacks[0].StackStatus' \
  --output text 2>/dev/null || true)"

if [[ -z "$STATUS" || "$STATUS" == "None" ]]; then
  echo "Stack deleted."
  exit 0
fi

FAILED="$(current_delete_failed)"
if [[ -z "$FAILED" ]]; then
  FAILED="LogGroup"
fi

echo "==> Normal delete stuck ($STATUS). Retaining: $FAILED"
# shellcheck disable=SC2086
aws cloudformation delete-stack \
  --stack-name "$STACK_NAME" \
  --retain-resources $FAILED

echo "==> Waiting for delete (retained resources are left in the account)..."
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
echo "Stack $STACK_NAME removed. Retained: $FAILED"
echo "Orphan log groups (if any) can stay; new stacks use /ecs/omniparse-idp-api."
