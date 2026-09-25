#!/usr/bin/env bash
# Update the live agent-vm stack, then optionally apply dotfiles over SSM.
# Refuses any resource replacement. A new AL2023 AMI or a UserData edit replaces
# the instance and drops the root disk; that requires ALLOW_INSTANCE_REPLACEMENT=1.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGION="${AWS_REGION:-us-west-1}"
STACK="${STACK_NAME:-agent-vm-w1}"
APPLY=0

if [[ -z "${GITHUB_ACTIONS:-}" ]]; then
  export AWS_PROFILE="${AWS_PROFILE:-aesona}"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

need() { command -v "$1" >/dev/null || { echo "install $1 first" >&2; exit 1; }; }
need aws
need jq

aws cloudformation validate-template \
  --region "$REGION" \
  --template-body "file://${DIR}/cloudformation.yaml" >/dev/null

PARAMS=(
  "ParameterKey=SSHPublicKey,UsePreviousValue=true"
  "ParameterKey=AllowedCidr,UsePreviousValue=true"
  "ParameterKey=InstanceType,UsePreviousValue=true"
  "ParameterKey=VolumeSizeGiB,UsePreviousValue=true"
  "ParameterKey=VpcId,UsePreviousValue=true"
  "ParameterKey=SubnetId,UsePreviousValue=true"
  "ParameterKey=TailscaleAuthKey,UsePreviousValue=true"
  "ParameterKey=TailscaleHostname,UsePreviousValue=true"
  "ParameterKey=WorkloadAccountId,UsePreviousValue=true"
  "ParameterKey=ShellcheckVersion,UsePreviousValue=true"
  "ParameterKey=ActionlintVersion,UsePreviousValue=true"
  "ParameterKey=MoshVersion,UsePreviousValue=true"
  "ParameterKey=MoshSha256,UsePreviousValue=true"
)
if [[ -n "${AMI_ID:-}" ]]; then
  PARAMS+=("ParameterKey=LatestAmiId,ParameterValue=$AMI_ID")
else
  PARAMS+=("ParameterKey=LatestAmiId,UsePreviousValue=true")
fi

CHANGE_SET="agent-vm-$(date +%Y%m%d%H%M%S)-$$"
cleanup() {
  aws cloudformation delete-change-set \
    --region "$REGION" --stack-name "$STACK" --change-set-name "$CHANGE_SET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

set +e
err="$(aws cloudformation create-change-set \
  --region "$REGION" \
  --stack-name "$STACK" \
  --change-set-name "$CHANGE_SET" \
  --change-set-type UPDATE \
  --template-body "file://${DIR}/cloudformation.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters "${PARAMS[@]}" 2>&1)"
status=$?
set -e
if [[ $status -ne 0 ]]; then
  echo "$err" >&2
  exit "$status"
fi

for _ in $(seq 1 30); do
  st="$(aws cloudformation describe-change-set \
    --region "$REGION" --stack-name "$STACK" --change-set-name "$CHANGE_SET" \
    --query Status --output text)"
  case "$st" in
    CREATE_COMPLETE|FAILED) break ;;
  esac
  sleep 2
done

reason="$(aws cloudformation describe-change-set \
  --region "$REGION" --stack-name "$STACK" --change-set-name "$CHANGE_SET" \
  --query StatusReason --output text)"
if [[ "$st" == FAILED && "$reason" == *"didn't contain changes"* ]]; then
  echo "CloudFormation: no infrastructure changes"
else
  if [[ "$st" != CREATE_COMPLETE ]]; then
    echo "change set $st: $reason" >&2
    exit 1
  fi
  replacements="$(aws cloudformation describe-change-set \
    --region "$REGION" --stack-name "$STACK" --change-set-name "$CHANGE_SET" \
    --query "Changes[?ResourceChange.Replacement=='True'].ResourceChange.LogicalResourceId" \
    --output text)"
  if [[ -n "$replacements" && "$replacements" != None ]]; then
    echo "Refusing to replace: $replacements" >&2
    echo "That drops the live root disk. Set ALLOW_INSTANCE_REPLACEMENT=1 to proceed." >&2
    aws cloudformation describe-change-set \
      --region "$REGION" --stack-name "$STACK" --change-set-name "$CHANGE_SET" \
      --query "Changes[].ResourceChange.{Id:LogicalResourceId,Action:Action,Replacement:Replacement}" \
      --output table >&2
    if [[ "${ALLOW_INSTANCE_REPLACEMENT:-}" != 1 ]]; then
      exit 1
    fi
  fi
  echo "Executing change set $CHANGE_SET"
  aws cloudformation execute-change-set \
    --region "$REGION" --stack-name "$STACK" --change-set-name "$CHANGE_SET"
  trap - EXIT
  aws cloudformation wait stack-update-complete --region "$REGION" --stack-name "$STACK"
  echo "CloudFormation update complete"
fi

if [[ "$APPLY" == 1 ]]; then
  "$DIR/apply-config.sh"
fi
