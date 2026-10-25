#!/usr/bin/env bash
# One fresh session, no scheduler activation. See tickets/1097/RUNNER.md.
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: tick.sh <estate>' >&2; exit 2; }
estate=$(realpath -- "$1")
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -f "$estate/estate.toml" ]] || { echo 'missing estate.toml' >&2; exit 2; }
cd -- "$estate"
exec 9> "$estate/tick.lock"
flock -n 9 || { echo 'tick busy: skipped' >&2; exit 0; }
umask 077
mkdir -p ticks
id=$(python3 -c 'import uuid; print(uuid.uuid4())')
log="$estate/ticks/$(date -u +%Y%m%dT%H%M%SZ)-$id.jsonl"
# A dead watcher means stops go unseen (2026-10-15 retro). Say so loudly and restart it.
hb="$estate/watcher-heartbeat"
if grep -q '^enabled = true' <(sed -n '/^\[watcher\]/,/^\[/p' estate.toml) && { [[ ! -f $hb ]] || (( $(date +%s) - $(stat -c %Y "$hb") > 600 )); }; then
  msg="$(date -u +%H:%MZ) WATCHER STALE: no heartbeat for 10+ min; restarting w1-watch.service"
  echo "$msg" >&2; echo "$msg" >> "$estate/watcher-events.log"
  systemctl --user restart w1-watch.service || echo 'WATCHER RESTART FAILED' >&2
fi
# Validate/report persistent holds/freezes before spending any model tokens.
python3 "$scripts/freeze.py" "$estate" > "${log%.jsonl}.state.json"
prompt=$(python3 - "$scripts/../SKILL.md" "$estate" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
m = re.search(r'^Run one dispatch tick:.*<absolute estate path>\.$', text, re.M)
if not m:
    sys.exit('tick prompt missing from SKILL.md')
print(m.group().replace('<absolute estate path>', sys.argv[2]))
PY
)
# Skip the model when the fleet has not changed since the last full tick (tick_gate.py).
gate=$(python3 "$scripts/tick_gate.py" "$estate" check) || gate="run gate failed"
printf '%s\n' "$gate" > "${log%.jsonl}.gate"
if [[ $gate == skip* ]]; then
  echo "tick $gate"
  exit 0
fi
profile_of() {
  python3 - "$estate/estate.toml" "$1" <<'PY'
import shlex, sys, tomllib
with open(sys.argv[1], 'rb') as f:
    cfg = tomllib.load(f)
prof = cfg['agents']['profiles'][sys.argv[2]]
a = prof['args']
if prof.get('kind', 'claude') == 'pi':
    print('pi', '-', shlex.join(a))
else:
    print('claude', a[a.index('--model') + 1], '-')
PY
}
# Safe mode keeps subscription OAuth, unlike --bare. No implicit history, memory,
# project instructions, hooks/plugins or MCP; files are explicitly read by the tick.
# No inherited agent pane identity: unattended ticks address workers explicitly.
unset HERDR_PANE_ID HERDR_TAB_ID HERDR_WORKSPACE_ID CLAUDECODE
export HERDR_ENV=1
export HERDR_SOCKET_PATH="${HERDR_SOCKET_PATH:-$HOME/.config/herdr/herdr.sock}"
# On a quota or rate-limit refusal, retry on the next [tick] fallback_profiles entry.
rc=3  # no usable profile
quota='429|rate.?limit|usage limit|hit your limit|out of (usage )?credits|insufficient_quota|quota exceeded'
profiles=$(python3 "$scripts/tick_gate.py" "$estate" profiles)
for name in $profiles; do
  read -r kind model pi_args < <(profile_of "$name")
  echo "tick profile $name" >&2
  rc=0
  if [[ $kind == pi ]]; then
    # A pi profile (Codex/Grok, estate [tick] profile): print mode, JSON events, no saved session.
    eval "set -- $pi_args"
    timeout --signal=TERM --kill-after=30s 90m pi -p "$prompt" "$@" --mode json --no-session \
      9>&- > "$log" 2> "${log%.jsonl}.stderr" < /dev/null || rc=$?
  else
    timeout --signal=TERM --kill-after=30s 90m claude -p "$prompt" \
      --safe-mode --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
      --setting-sources '' --model "$model" --effort medium \
      --permission-mode bypassPermissions --session-id "$id" \
      --output-format stream-json --verbose 9>&- > "$log" 2> "${log%.jsonl}.stderr" || rc=$?
  fi
  [[ $rc != 0 ]] || break
  grep -Eqi "$quota" "${log%.jsonl}.stderr" <(tail -c 3000 "$log") || break
  echo "tick profile $name refused for quota (rc=$rc); trying the next" >&2
  cp "$log" "${log%.jsonl}.$name.jsonl"; cp "${log%.jsonl}.stderr" "${log%.jsonl}.$name.stderr"
done
printf '%s\n' "$rc" > "${log%.jsonl}.exit"
# Print result records for journald/the owner; detailed events stay in the estate.
python3 - "$log" <<'PY'
import json, sys
last = None
for line in open(sys.argv[1]):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get('type') == 'result':
        print(json.dumps(event))
    last = event
# pi's JSON mode has no result record: print its last event so journald still shows the report.
if last is not None and last.get('type') != 'result':
    print(json.dumps(last)[-6000:])
PY
# This fleet state is the next tick's "nothing changed" baseline.
[[ $rc != 0 ]] || python3 "$scripts/tick_gate.py" "$estate" commit || true
exit "$rc"
