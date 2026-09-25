#!/usr/bin/env bash
# Apply origin/main dotfiles on the live instance. Does not touch CloudFormation.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGION="${AWS_REGION:-us-west-1}"
STACK="${STACK_NAME:-agent-vm-w1}"

if [[ -z "${GITHUB_ACTIONS:-}" ]]; then
  export AWS_PROFILE="${AWS_PROFILE:-aesona}"
fi

command -v aws >/dev/null || { echo "install aws first" >&2; exit 1; }
command -v jq >/dev/null || { echo "install jq first" >&2; exit 1; }

IID="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)"
if [[ -z "$IID" || "$IID" == None ]]; then
  echo "no InstanceId output on $STACK" >&2
  exit 1
fi

params="$(jq -n --rawfile script "$DIR/apply-on-instance.sh" '{commands:[$script]}')"
CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$IID" \
  --document-name AWS-RunShellScript \
  --timeout-seconds 900 \
  --comment "apply dotfiles main" \
  --parameters "$params" \
  --query Command.CommandId --output text)"
echo "SSM $CMD_ID on $IID"

deadline=$((SECONDS + 900))
while (( SECONDS < deadline )); do
  st="$(aws ssm get-command-invocation \
    --region "$REGION" --command-id "$CMD_ID" --instance-id "$IID" \
    --query Status --output text 2>/dev/null || echo Pending)"
  case "$st" in
    Success)
      aws ssm get-command-invocation \
        --region "$REGION" --command-id "$CMD_ID" --instance-id "$IID" \
        --query StandardOutputContent --output text
      exit 0
      ;;
    Failed|Cancelled|TimedOut|Cancelling)
      echo "SSM $st" >&2
      aws ssm get-command-invocation \
        --region "$REGION" --command-id "$CMD_ID" --instance-id "$IID" \
        --query '{stdout:StandardOutputContent,stderr:StandardErrorContent}' --output text >&2
      exit 1
      ;;
  esac
  sleep 5
done
echo "SSM timed out waiting for $CMD_ID" >&2
exit 1
