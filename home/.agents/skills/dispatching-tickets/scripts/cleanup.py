#!/usr/bin/env python3
"""Retire the tabs and worktrees of finished ticket work.

    python3 cleanup.py <estate-dir>            # report only
    python3 cleanup.py <estate-dir> --apply    # archive reports, close tabs, remove worktrees

Run it AFTER the tick's dispatch, never before: a worktree created for an agent that has
not started yet has nothing protecting it but the agent that is about to exist.

A session is finished when its ticket is CLOSED *and* its branch has landed. Anything that
fails a check is kept and the reason printed. This script never forces.

Tabs go first, then worktrees, because an agent sitting in a worktree's workspace
protects that worktree until its tab is closed.
"""
import sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import estate as E

APPLY = "--apply" in sys.argv[2:]


class Tickets:
    def __init__(self, slug):
        self.slug, self._state = slug, {}

    def closed(self, n):
        # A failed lookup is cached as unknown, and unknown is not closed.
        if n not in self._state:
            r = E.run("gh", "issue", "view", str(n), "--repo", self.slug, "--json", "state", "--jq", ".state")
            self._state[n] = r.stdout.strip() if r.returncode == 0 else "UNKNOWN"
        return self._state[n] == "CLOSED"


def archive_tail(cfg, n, name):
    """Save an agent's terminal before its tab closes. Returns the path, or None.

    The terminal summary carries findings the ticket's closing comment does not, and
    closing the tab destroys it. The tail is read twice a few seconds apart: agent status
    lags, and an agent listed `done` may still be writing.
    """
    def read(lines):
        r = E.run("herdr", "agent", "read", name, "--source", "recent-unwrapped",
                  "--lines", str(lines))
        return r.stdout if r.returncode == 0 and r.stdout.strip() else None
    # "Is it still writing?" is asked of a SHALLOW window, and only the archive is deep.
    # A deep read of an idle pane is not byte-stable: herdr re-renders scrollback, so lines
    # drift off the top and back on between two reads seconds apart. Comparing 400 lines
    # never matched for any agent with more than a screenful of history, so no report was
    # archived and no tab closed - and the live agent then kept its worktree too.
    # Measured on the laptop fleet 2026-09-22: 40 lines stable, 150 and 400 not, on both
    # sources. A 40-line window still changes while an agent writes.
    first = read(40)
    time.sleep(5)
    second = read(40)
    if first is None or second is None or first != second:
        return None
    d = cfg["_root"] / "tickets" / str(n)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"report-{name}-{datetime.now():%Y%m%dT%H%M%S}.txt"
    # The deep read is what gets saved; by here the agent has been shown to be quiet.
    path.write_text(read(400) or second)
    return path


def close_tabs(cfg, tickets):
    closed, kept = [], []
    for a in E.agents():
        name = a.get("name") or ""
        n = E.ticket_of_agent(cfg, name)
        if n is None:
            continue
        if a.get("agent_status") not in ("idle", "done"):
            continue                                   # working or blocked: never close under it
        if not tickets.closed(n):
            kept.append((name, f"#{n} is still open"))
            continue
        if not APPLY:
            closed.append((name, f"#{n} closed, agent {a.get('agent_status')}"))
            continue
        saved = archive_tail(cfg, n, name)
        if saved is None:
            kept.append((name, "tail unreadable or still changing; report not archived"))
            continue
        r = E.run("herdr", "tab", "close", a["tab_id"])
        (closed if r.returncode == 0 else kept).append(
            (name, f"#{n} closed, report at {saved}" if r.returncode == 0 else "tab close failed"))
    return closed, kept


def landed(repo, branch, root):
    """Why this branch counts as landed, or None if it does not.

    A merged pull request is the primary check because it is the only one that survives a
    squash merge: a squashed branch's tip is an ancestor of nothing. Ancestry of a landing
    branch is the fallback for work merged without a PR. The merged PR's head must still be
    the branch tip, or commits made after the merge would be retired with it.
    """
    tip = E.run("git", "rev-parse", branch, cwd=root).stdout.strip()
    r = E.run("gh", "pr", "list", "--repo", repo["slug"], "--head", branch, "--state", "merged",
              "--json", "number,headRefOid", "--jq", ".[] | \"\\(.number) \\(.headRefOid)\"")
    if r.returncode == 0:
        for line in r.stdout.split("\n"):
            if line.strip():
                number, head = line.split()
                if tip and head == tip:
                    return f"PR #{number} merged"
    for base in repo.get("landed", [repo["base"]]):
        if tip and E.run("git", "merge-base", "--is-ancestor", tip, f"origin/{base}", cwd=root).returncode == 0:
            return f"tip is an ancestor of origin/{base}"
    return None


def remove_worktrees(cfg, tickets):
    listed = E.agents()
    live_spaces = {a.get("workspace_id") for a in listed}
    # A ticket spanning two repositories has a second worktree with no herdr workspace of
    # its own, which the workspace check cannot see. Removing one out from under a working
    # agent destroys uncommitted work. So a ticket number held by ANY agent protects every
    # worktree carrying that number, in every repository.
    live_tickets = {n for a in listed if (n := E.ticket_of_agent(cfg, a.get("name"))) is not None}
    removed, kept = [], []
    for repo in cfg["repos"]:
        root = repo["path"]
        E.run("git", "fetch", "-q", "origin", cwd=root)
        for w in E.worktrees(repo):
            path, branch, ws = w["path"], w.get("branch"), w.get("open_workspace_id")
            label = f"{repo['name']}/{Path(path).name}"
            if ws and ws in live_spaces:
                kept.append((label, "an agent is live in its workspace"))
                continue
            n = E.ticket_of_worktree(branch, path)
            if n is None:
                kept.append((label, f"neither {branch!r} nor the directory names a ticket"))
                continue
            if n in live_tickets:
                kept.append((label, f"an agent for #{n} is live, possibly in another repository"))
                continue
            if not tickets.closed(n):
                kept.append((label, f"#{n} is not known to be closed"))
                continue
            dirty = E.run("git", "status", "--porcelain", cwd=path)
            if dirty.returncode != 0 or dirty.stdout.strip():
                kept.append((label, "uncommitted files" if dirty.returncode == 0 else "git status failed"))
                continue
            why = landed(repo, branch, root)
            if why is None:
                kept.append((label, f"{branch} has no merged PR at its tip and is not an ancestor of a landing branch"))
                continue
            if not APPLY:
                removed.append((label, f"#{n} closed, {why}"))
                continue
            if ws:
                ok = E.run("herdr", "worktree", "remove", "--workspace", ws, "--trust-repository").returncode == 0
            else:
                ok = E.run("git", "worktree", "remove", path, cwd=root).returncode == 0
            (removed if ok else kept).append((label, f"#{n} closed, {why}" if ok else "removal failed"))
    return removed, kept


def show(title, rows):
    print(f"== {title} ({len(rows)})")
    for what, why in sorted(rows):
        print(f"   {what:<52} {why}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cfg = E.load(sys.argv[1])
    tickets = Tickets(cfg["tracker"])
    try:
        tabs_closed, tabs_kept = close_tabs(cfg, tickets)
        removed, kept = remove_worktrees(cfg, tickets)
    except E.Refused as e:
        # Unknown agent or worktree state must never read as "nothing to protect".
        sys.exit(f"!! {e}; refusing to retire anything further")
    show("tabs closed" if APPLY else "tabs to close", tabs_closed)
    show("tabs kept", tabs_kept)
    show("worktrees removed" if APPLY else "worktrees to remove", removed)
    show("worktrees kept", kept)


if __name__ == "__main__":
    main()
