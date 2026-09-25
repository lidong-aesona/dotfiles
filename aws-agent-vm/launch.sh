#!/usr/bin/env bash
# The live stack is updated by deploy.sh. This name remains so older notes still work.
echo "Updating the existing stack. This refuses instance replacement." >&2
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy.sh" "$@"
