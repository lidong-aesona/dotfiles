"""Shared plumbing for the dispatch scripts: the estate file, and calls that fail closed.

Every script here answers a question whose wrong answer is dangerous in one direction
only: "not blocked", "nothing is running", "already merged". So every external call
either succeeds or raises. Nothing returns an empty answer on failure.
"""
import json, re, subprocess, sys, time, tomllib
from pathlib import Path


class Refused(Exception):
    """An external call did not succeed. Never treat this as an empty answer."""


def load(estate_dir):
    root = Path(estate_dir).expanduser().resolve()
    path = root / "estate.toml"
    if not path.is_file():
        sys.exit(f"!! no estate.toml in {root}")
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    for key in ("tracker", "agent_prefix", "repos", "labels", "agents"):
        if key not in cfg:
            sys.exit(f"!! {path} is missing `{key}`")
    for name, profile in cfg["agents"]["profiles"].items():
        # A suffix that starts with a digit makes ticket 11's agent `ticket-11` + `0`
        # indistinguishable from ticket 110's. Agent matching relies on the character
        # after the number being a non-digit.
        if re.match(r"\d", profile.get("suffix", "")):
            sys.exit(f"!! profile {name!r}: suffix must not start with a digit")
    profiles = cfg["agents"]["profiles"]
    if cfg["agents"]["active"] not in profiles:
        sys.exit(f"!! agents.active names no profile in {path}")
    suffixes = [p.get("suffix", "") for p in profiles.values()]
    if len(set(suffixes)) != len(suffixes):
        # Two profiles sharing a suffix make two agents on one ticket share one name.
        sys.exit(f"!! two profiles share a suffix in {path}")
    for tname, t in cfg.get("routing", {}).get("types", {}).items():
        for name in t.get("profiles", []):
            if name not in profiles:
                sys.exit(f"!! routing.types.{tname} names no profile {name!r}")
            if "provider" not in profiles[name]:
                sys.exit(f"!! profile {name!r} is routed but has no `provider`")
        if not t.get("profiles"):
            sys.exit(f"!! routing.types.{tname} lists no profiles")
    cfg["_root"] = root
    return cfg


def run(*args, cwd=None):
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd)


def call(*args, tries=4, cwd=None):
    """Run a command, retrying transient failures. Raise rather than return empty.

    The original loop captured stdout and ignored the return code. A transient network
    failure then emptied five issue bodies at once, each read as "no blockers", and the
    frontier jumped from 7 to 29 in one tick.
    """
    last = ""
    for attempt in range(tries):
        r = run(*args, cwd=cwd)
        if r.returncode == 0:
            return r.stdout
        last = (r.stderr or r.stdout or "").strip()
        if attempt < tries - 1:
            time.sleep(2 ** attempt)
    raise Refused(f"{' '.join(args)} failed after {tries} tries: {last}")


def gh_lines(*args):
    """A paginated `gh api ... --jq` call that emits one JSON value per line."""
    return [json.loads(line) for line in call("gh", "api", "--paginate", *args).splitlines() if line.strip()]


def parse(raw, *keys):
    """Dig `keys` out of a JSON reply, raising rather than guessing when the shape is off."""
    try:
        value = json.loads(raw)
        for k in keys:
            value = value[k]
        return value
    except (ValueError, KeyError, TypeError) as e:
        raise Refused(f"unreadable reply ({e}): {raw[:200]!r}")


def agents():
    """Every agent herdr knows about. Raises when the list cannot be read.

    Unknown agent state must never read as "nothing is running": to the frontier that
    means every ticket is undispatched and gets a second agent, and to cleanup it means
    every worktree is unguarded.
    """
    return parse(call("herdr", "agent", "list"), "result", "agents")


def ticket_agents(cfg, listed, n):
    """Agents that belong to ticket n: `<prefix><n>` plus any suffix not starting with a digit.

    `ticket-110`, `ticket-110c` (a replacement on another agent kind) and
    `ticket-110-followup` all belong to #110; `ticket-11` never claims them.
    """
    pat = re.compile(rf"^{re.escape(cfg['agent_prefix'])}{n}(?![0-9])")
    return [a for a in listed if pat.match(a.get("name") or "")]


def ticket_of_agent(cfg, name):
    m = re.match(rf"^{re.escape(cfg['agent_prefix'])}(\d+)", name or "")
    return int(m.group(1)) if m else None


def ticket_of_worktree(branch, path):
    """The ticket number from the branch (`build/107-three-stores`), else the directory
    (`130-no-standing-admin`). Older branches carry the number only in the directory, and
    both must resolve or that half of the tree is invisible to the loop."""
    m = re.match(r"^[\w.-]+/(\d+)-", branch or "")
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)-", Path(path).name)
    return int(m.group(1)) if m else None


def worktrees(repo):
    """Linked, attached worktrees of one estate repository, minus its keep list."""
    listed = parse(call("herdr", "worktree", "list", "--cwd", repo["path"]), "result", "worktrees")
    return [w for w in listed
            if w.get("is_linked_worktree") and not w.get("is_detached")
            and Path(w["path"]).name not in repo.get("keep", [])]
