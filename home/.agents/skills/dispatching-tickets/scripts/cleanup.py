#!/usr/bin/env python3
"""Retire the agents and worktrees of finished ticket work.

    python3 cleanup.py <estate-dir>            # report only
    python3 cleanup.py <estate-dir> --apply    # archive reports, remove agents, remove worktrees

Run it AFTER the tick's dispatch, never before: a worktree created for an agent that has
not started yet has nothing protecting it but the agent that is about to exist.

A session is finished when its ticket is CLOSED *and* its branch has landed. Anything that
fails a check is kept and the reason printed. This script never forces.

Agents go first, then worktrees, because any terminal whose cwd is inside a worktree
protects that worktree until it is removed.
"""
import sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import estate as E

APPLY = "--apply" in sys.argv[2:]
# A named agent whose process ended is `exited`: nothing runs under it any more.
FINISHED = ("idle", "done", "exited")


class Tickets:
    def __init__(self, slug, cfg=None):
        self.slug, self.cfg, self._issues = slug, cfg, {}

    def issue(self, n):
        if n not in self._issues:
            try:
                # A prefixed key ("r235") lives on its own tracker: asking the main tracker
                # for #235 would read an unrelated issue, often closed, and retire live work.
                slug, number = E.locate(self.cfg, n) if self.cfg else (self.slug, n)
                raw = E.call('gh', 'issue', 'view', str(number), '--repo', slug,
                             '--json', 'state,labels')
                issue = E.parse(raw)
                if not isinstance(issue, dict) or not isinstance(issue.get('labels'), list):
                    raise E.Refused('ticket labels unreadable')
                if any(not isinstance(label, dict) or not isinstance(label.get('name'), str)
                       for label in issue['labels']):
                    raise E.Refused('ticket label name unreadable')
                self._issues[n] = issue
            except E.Refused:
                self._issues[n] = None
        return self._issues[n]

    def closed(self, n):
        issue = self.issue(n)
        return issue is not None and issue.get('state') == 'CLOSED'

    def machine(self, n, cfg):
        issue = self.issue(n)
        return E.machine_for(cfg, [label['name'] for label in issue['labels']]) if issue else None


def archive_tail(cfg, n, name, machine="", terminal=None):
    """Save an agent's terminal before it is removed. Returns the path, or None.

    The terminal summary carries findings the ticket's closing comment does not, and
    removing the terminal destroys it. The tail is read twice a few seconds apart: agent
    status lags, and an agent listed `done` may still be writing.
    """
    def read(lines):
        r = E.run(*E.hc("read", E.address(machine, terminal or name), "--lines", str(lines)))
        if r.returncode:
            raise E.Refused(f'{machine or "local"} tail read failed: {r.stderr}')
        return r.stdout if r.stdout.strip() else None
    # "Is it still writing?" is asked of a SHALLOW window, and only the archive is deep.
    # A deep read of an idle pane was not byte-stable under herdr: it re-rendered scrollback, so lines
    # drift off the top and back on between two reads seconds apart. Comparing 400 lines
    # never matched for any agent with more than a screenful of history, so no report was
    # archived and no agent removed - and the live agent then kept its worktree too.
    # Measured on the laptop fleet 2026-09-22: 40 lines stable, 150 and 400 not, on both
    # sources. A 40-line window still changes while an agent writes.
    first = read(40)
    time.sleep(5)
    second = read(40)
    if first is None or second is None or first != second:
        return None
    d = cfg["_root"] / "tickets" / str(n)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"report-{name}.md"
    if path.exists():
        path = d / f"report-{name}-{datetime.now():%Y%m%dT%H%M%S%f}.md"
    # The deep read is what gets saved; by here the agent has been shown to be quiet.
    path.write_text(read(400) or second)
    return path


def retirement_inventory(cfg):
    errors = {}
    listed, trees = E.inventory(cfg, errors=errors)
    previous = E.baseline(cfg)
    for machine, error in errors.items():
        print(f'!! {machine or "local"} ownership unknown; retirement skipped there: {error}')
    return listed, trees, errors, previous


def machine_failed(errors, machine, error):
    errors[machine] = str(error)
    print(f'!! {machine or "local"} ownership unknown; retirement skipped there: {error}')


def ticket_unknown(cfg, tickets, numbers, errors, listed, trees, previous):
    unknown = E.unknown_ownership(cfg, errors, listed, trees, previous)
    historical = {n for owners in unknown.values() for n in owners}
    try:
        return bool(numbers & historical) or any(tickets.machine(n, cfg) in errors for n in numbers)
    except E.Refused:
        return True


def retire_agents(cfg, tickets):
    listed, trees, errors, previous = retirement_inventory(cfg)
    closed, kept = [], []
    for a in listed:
        name = a.get("name") or ""
        numbers = E.agent_tickets(cfg, a)
        if not numbers:
            continue
        if a['machine'] in errors or ticket_unknown(cfg, tickets, numbers, errors, listed, trees, previous):
            kept.append((name, 'ownership on an unreadable machine is unknown'))
            continue
        if a.get("agent_status") not in FINISHED:
            continue                                   # working or blocked: never close under it
        if not all(tickets.closed(number) for number in numbers):
            kept.append((name, f'owned tickets {sorted(numbers, key=E.order)} are not all known closed'))
            continue
        owned = [w for w in trees if E.ticket_of_worktree(cfg, w.get('branch'), w['path']) in numbers]
        proven = {E.ticket_of_worktree(cfg, w.get('branch'), w['path']) for w in owned}
        if proven != numbers:
            kept.append((name, 'not every owned ticket has a worktree to prove clean and landed'))
            continue
        if any(other.get('agent_status') not in FINISHED and
               E.agent_tickets(cfg, other) & numbers for other in listed):
            kept.append((name, 'another agent for this ticket is not finished'))
            continue
        reasons = [retirable(cfg, w, errors) for w in owned]
        if any(reason is None for reason in reasons):
            kept.append((name, 'ticket worktree dirty, unreadable or unlanded'))
            continue
        if not APPLY:
            closed.append((name, f"tickets {sorted(numbers, key=E.order)} closed, agent {a.get('agent_status')}"))
            continue
        try:
            reports = [archive_tail(cfg, number, name, a['machine'], a['terminal'])
                       for number in sorted(numbers, key=E.order)]
        except E.Refused as e:
            machine_failed(errors, a['machine'], e)
            kept.append((name, 'machine failed while archiving'))
            continue
        saved = reports[0]
        if any(report is None for report in reports):
            kept.append((name, "tail unreadable or still changing; report not archived"))
            continue
        # Recheck status after the quiet-tail probe, before destroying the terminal.
        try:
            fresh = E.agents(a['machine'])
        except E.Refused as e:
            machine_failed(errors, a['machine'], e)
            kept.append((name, 'machine failed during agent recheck'))
            continue
        current = next((x for x in fresh if x['address'] == a['address']), None)
        if (not current or current.get('agent_status') not in FINISHED
                or current.get('name') != name
                or E.agent_tickets(cfg, current) != numbers):
            kept.append((name, 'agent changed after archive'))
            continue
        target = a['address']
        try:
            # kill ends the agent; rm drops the finished terminal and its tab from the app.
            r = E.run(*E.hc("kill", target)) if current['agent_status'] != 'exited' else None
            if r is None or r.returncode == 0:
                r = E.run(*E.hc("rm", target))
        except E.Refused as e:
            machine_failed(errors, a['machine'], e)
            kept.append((name, 'terminal removal reply unreadable; recheck next tick'))
            continue
        if r.returncode:
            machine_failed(errors, a['machine'], r.stderr or 'terminal removal failed')
        (closed if r.returncode == 0 else kept).append(
            (name, f"tickets {sorted(numbers, key=E.order)} closed, report at {saved}" if r.returncode == 0 else "terminal removal failed"))
    return closed, kept


def landed(repo, branch, root, cfg=None, machine=""):
    """Why this branch counts as landed, or None if it does not.

    A merged pull request is the primary check because it is the only one that survives a
    squash merge: a squashed branch's tip is an ancestor of nothing. Ancestry of a landing
    branch is the fallback for work merged without a PR. The merged PR's head must still be
    the branch tip, or commits made after the merge would be retired with it.
    """
    if not branch:
        return None
    run = lambda *args, **kw: E.machine_run(cfg, machine, *args, **kw)
    revision = run("git", "rev-parse", branch, cwd=root)
    if revision.returncode:
        raise E.Refused(f'git rev-parse failed: {revision.stderr}')
    tip = revision.stdout.strip()
    r = E.run("gh", "pr", "list", "--repo", repo["slug"], "--head", branch, "--state", "merged",
              "--json", "number,headRefOid", "--jq", ".[] | \"\\(.number) \\(.headRefOid)\"")
    if r.returncode == 0:
        for line in r.stdout.split("\n"):
            if line.strip():
                number, head = line.split()
                if tip and head == tip:
                    return f"PR #{number} merged"
    for base in repo.get("landed", [repo["base"]]):
        if not tip:
            continue
        ancestry = run("git", "merge-base", "--is-ancestor", tip, f"origin/{base}", cwd=root)
        if ancestry.returncode not in (0, 1):
            raise E.Refused(f'git merge-base failed: {ancestry.stderr}')
        if ancestry.returncode == 0:
            return f"tip is an ancestor of origin/{base}"
    return None


def retirable(cfg, tree, errors=None):
    """A fresh successful fetch, clean tree and landed exact tip, on its own machine."""
    repo, machine = tree['repo'], tree['machine']
    run = lambda *args, **kw: E.machine_run(cfg, machine, *args, **kw)
    try:
        fetched = run('git', 'fetch', '-q', 'origin', cwd=repo['path'])
        if fetched.returncode:
            raise E.Refused(f'git fetch failed: {fetched.stderr}')
        dirty = run('git', 'status', '--porcelain', cwd=tree['path'])
        if dirty.returncode:
            raise E.Refused(f'git status failed: {dirty.stderr}')
        if dirty.stdout.strip():
            return None
        return landed(repo, tree.get('branch'), repo['path'], cfg, machine)
    except E.Refused as e:
        machine_failed(errors if errors is not None else {}, machine, e)
        return None


def remove_worktrees(cfg, tickets):
    listed, trees, errors, previous = retirement_inventory(cfg)
    # Any live terminal inside a worktree, agent or shell, is using it.
    occupied = {}
    for machine in E.machines(cfg):
        if machine in errors:
            continue
        try:
            occupied[machine] = [t['cwd'] for t in E.terminals(machine)]
        except E.Refused as e:
            machine_failed(errors, machine, e)
    # A ticket spanning two repositories has a second worktree no agent sits in, which the
    # cwd check cannot see. Removing one out from under a working agent destroys
    # uncommitted work. So a ticket number held by ANY agent protects every worktree
    # carrying that number, in every repository.
    live_tickets = {n for a in listed for n in E.agent_tickets(cfg, a)}
    removed, kept = [], []
    for w in trees:
        repo, machine = w['repo'], w['machine']
        path, branch = w['path'], w.get('branch')
        label = f"{machine or 'local'}:{repo['name']}/{Path(path).name}"
        if any(cwd == path or cwd.startswith(path.rstrip('/') + '/') for cwd in occupied.get(machine, [])):
            kept.append((label, 'a terminal is open inside it'))
            continue
        n = E.ticket_of_worktree(cfg, branch, path)
        if machine in errors or ticket_unknown(cfg, tickets, {n} if n is not None else set(), errors, listed, trees, previous):
            kept.append((label, 'ownership on an unreadable machine is unknown'))
            continue
        if n is None or n in live_tickets:
            kept.append((label, 'no ticket number, or an agent owns this ticket on a machine'))
            continue
        if not tickets.closed(n):
            kept.append((label, f'#{n} is not known to be closed'))
            continue
        why = retirable(cfg, w, errors)
        if why is None:
            kept.append((label, 'dirty, unreadable, detached or unlanded worktree'))
            continue
        if not APPLY:
            removed.append((label, f'#{n} closed, {why}'))
            continue
        try:
            ok = E.machine_run(cfg, machine, 'git', 'worktree', 'remove', path, cwd=repo['path']).returncode == 0
        except E.Refused as e:
            machine_failed(errors, machine, e)
            kept.append((label, 'removal reply unreadable; recheck next tick'))
            continue
        if not ok:
            machine_failed(errors, machine, 'worktree removal failed')
        (removed if ok else kept).append((label, f'#{n} closed, {why}' if ok else 'removal failed'))
    return removed, kept


def show(title, rows):
    print(f"== {title} ({len(rows)})")
    for what, why in sorted(rows):
        print(f"   {what:<52} {why}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cfg = E.load(sys.argv[1])
    tickets = Tickets(cfg["tracker"], cfg)
    try:
        agents_removed, agents_kept = retire_agents(cfg, tickets)
        removed, kept = remove_worktrees(cfg, tickets)
    except E.Refused as e:
        # Unknown agent or worktree state must never read as "nothing to protect".
        sys.exit(f"!! {e}; refusing to retire anything further")
    show("agents removed" if APPLY else "agents to remove", agents_removed)
    show("agents kept", agents_kept)
    show("worktrees removed" if APPLY else "worktrees to remove", removed)
    show("worktrees kept", kept)


if __name__ == "__main__":
    main()
