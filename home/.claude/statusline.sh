#!/usr/bin/env bash
# Claude Code status line: dir | branch | model | context usage.
# Reads the status JSON on stdin (schema: Claude Code statusLine docs).
set -uo pipefail

{
  read -r dir
  read -r model
  read -r ctx
} < <(jq -r '
  (.workspace.current_dir // .cwd // "?"),
  (.model.display_name // "?"),
  (.context_window.used_percentage // -1 | floor)
')

# Starship-style path: ~ for home, at most the last three components.
short_dir=${dir/#"$HOME"/\~}
IFS=/ read -r -a parts <<<"$short_dir"
n=${#parts[@]}
if [ "$n" -gt 3 ]; then
  short_dir="…/${parts[$((n - 3))]}/${parts[$((n - 2))]}/${parts[$((n - 1))]}"
fi

branch=$(git -C "$dir" branch --show-current 2>/dev/null)
[ -n "$branch" ] || branch=$(git -C "$dir" rev-parse --short HEAD 2>/dev/null)

# Context percentage turns yellow past half, red past 80%.
if [ "$ctx" -lt 0 ]; then
  ctx_txt=$'\033[90mctx --\033[0m'
else
  if   [ "$ctx" -lt 50 ]; then ctx_color=$'\033[32m'
  elif [ "$ctx" -lt 80 ]; then ctx_color=$'\033[33m'
  else                         ctx_color=$'\033[31m'
  fi
  ctx_txt="${ctx_color}ctx ${ctx}%"$'\033[0m'
fi

sep=$' \033[90m|\033[0m '
out=$'\033[36m'"$short_dir"$'\033[0m'
[ -n "$branch" ] && out+="${sep}"$'\033[35m '"$branch"$'\033[0m'
out+="${sep}"$'\033[34m'"$model"$'\033[0m'
out+="${sep}${ctx_txt}"

printf '%s' "$out"
