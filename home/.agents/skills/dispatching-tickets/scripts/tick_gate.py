#!/usr/bin/env python3
"""Decide, before any model tokens, whether a headless tick has work, and on which profiles.

usage: tick_gate.py <estate> check        -> prints "run <reason>" or "skip <reason>"
       tick_gate.py <estate> profiles     -> the tick's profile, then its fallbacks, one per line
       tick_gate.py <estate> commit       -> after a successful tick, save this fleet digest

A tick that only re-reads an unchanged fleet costs ~0.7M-2.5M tokens (2026-10-15 retro: 7 of 11
ticks changed nothing). The digest covers every ticket's labels, action, agents and worktrees,
and every watched agent's status and completion count, so any stop, label, close or new ticket
runs the model. A dispatch or orphaned action always runs, and so does the first tick after
[tick] max_skip_minutes. Unknown always runs: a read failure is not "nothing changed".
An unreachable machine is the exception, as in frontier.py: its tickets are already `unknown`
and a tick can do nothing for them, so it enters the digest as unreadable. Going unreachable or
coming back changes the digest and runs; staying unreachable skips until max_skip_minutes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import estate as E  # noqa: E402
ALWAYS_RUN = {"dispatch", "orphaned"}


def fleet_digest(estate: Path, config: dict) -> tuple[str, set[str]]:
    frontier = json.loads(
        subprocess.run(
            [sys.executable, str(SCRIPTS / "frontier.py"), str(estate)],
            capture_output=True, text=True, timeout=300, check=True,
        ).stdout
    )
    if frontier.get("unreadable"):
        raise RuntimeError("unreadable tickets")
    # Research keys ("r235") sort after main-tracker numbers; ints keep their old order.
    tickets = sorted(
        ((t["number"], t.get("labels"), t.get("action"), t.get("agents"), t.get("worktrees"))
         for t in frontier["frontier"] + frontier["blocked"]),
        key=lambda t: (isinstance(t[0], str), t[0]),
    )
    actions = {str(t.get("action")) for t in frontier["frontier"] + frontier["blocked"]}
    agents = []
    watcher = config.get("watcher", {})
    skip = E.ignored(watcher)
    for machine in watcher.get("machines", [""]):
        try:
            # The stop mark moves on every stop, so a turn that ran entirely between two
            # ticks still changes the digest.
            agents += sorted(
                (machine, a["name"], a["agent_status"], E.stop_mark(a))
                for a in E.agents(machine)
                if not skip(a)
            )
        except E.Refused as error:
            if not machine:
                raise
            agents.append((machine, "unreadable", type(error).__name__, None))
    blob = json.dumps(
        [tickets, agents, frontier.get("holds"), frontier.get("freeze"),
         sorted(frontier.get("machine_errors") or {})],
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest(), actions


def main() -> int:
    estate, verb = Path(sys.argv[1]).resolve(), sys.argv[2]
    with (estate / "estate.toml").open("rb") as f:
        config = tomllib.load(f)
    tick = config.get("tick", {})
    state_file = estate / "tick-digest.json"
    if verb == "profiles":
        now = datetime.now(timezone.utc)
        names = [tick.get("profile", "claude"), *tick.get("fallback_profiles", [])]
        for name in dict.fromkeys(names):
            until = config["agents"]["profiles"][name].get("exhausted_until")
            if until and datetime.fromisoformat(until.replace("Z", "+00:00")) > now:
                continue
            print(name)
        return 0
    try:
        digest, actions = fleet_digest(estate, config)
    except Exception as error:  # fail open to a full tick
        print(f"run fleet unreadable ({type(error).__name__})")
        return 0
    if verb == "commit":
        (estate / "tick-digest.tmp").write_text(json.dumps({"digest": digest, "at": time.time()}))
        (estate / "tick-digest.tmp").replace(state_file)
        return 0
    saved = json.loads(state_file.read_text()) if state_file.exists() else {}
    age = (time.time() - saved.get("at", 0)) / 60
    if actions & ALWAYS_RUN:
        print(f"run actions {sorted(actions & ALWAYS_RUN)}")
    elif saved.get("digest") != digest:
        print("run fleet changed")
    elif age >= tick.get("max_skip_minutes", 120):
        print(f"run last full tick {age:.0f} min ago")
    else:
        print(f"skip nothing changed since the full tick {age:.0f} min ago")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
