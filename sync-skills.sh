#!/usr/bin/env bash
# Copy agent skills both ways. Skills are not in this public repo.
# Newer files win. Nothing is deleted. Codex's machine-local synced/ bucket is skipped.
set -euo pipefail

REMOTE="${SYNC_REMOTE:-agent-vm-w1}"
RSYNC=(rsync -au --exclude synced/ --exclude .DS_Store --exclude '.git/')

mkdir -p "$HOME/.agents/skills" "$HOME/.pi/agent/skills"
"${RSYNC[@]}" "$HOME/.agents/skills/" "$REMOTE:.agents/skills/"
"${RSYNC[@]}" "$REMOTE:.agents/skills/" "$HOME/.agents/skills/"
"${RSYNC[@]}" "$HOME/.pi/agent/skills/" "$REMOTE:.pi/agent/skills/"
"${RSYNC[@]}" "$REMOTE:.pi/agent/skills/" "$HOME/.pi/agent/skills/"

if [[ -f "$HOME/.agents/.skill-lock.json" ]]; then
  rsync -au "$HOME/.agents/.skill-lock.json" "$REMOTE:.agents/.skill-lock.json"
  rsync -au "$REMOTE:.agents/.skill-lock.json" "$HOME/.agents/.skill-lock.json"
fi

echo "skills synced with $REMOTE"
