#!/usr/bin/env python3
"""The frontier of an estate: open tickets whose every declared blocker is closed.

    python3 frontier.py <estate-dir>            # print
    python3 frontier.py <estate-dir> --record   # print, then save as the next tick's baseline

Blockers come from two places and BOTH are read:
  1. the issue body's "## Blocked by" section  (often the only one filled in)
  2. GitHub's native dependency links          (empty means "no data", NOT "no blockers")
Reading only the native links dispatched nine blocked tickets in one tick.

A referenced issue blocks when its LIVE state is open. Inline prose such as "(closed)"
is never trusted: a stale annotation must not unblock anything. The one way a human
declares an open issue no longer blocking is to strike it through: `- ~~#95~~ Removed ...`.

Each ticket gets an `action`:
  leave     an agent for it is working
  read      agents exist and none is working: read the tail, the status may lag
  surface   carries the surface label: a human's to do, never an agent's
  dispatch  carries the dispatch label, has no agent and no worktree
  orphaned  carries the dispatch label and has no agent, but a worktree carries its number:
            an agent died, its tab closed, or a start failed. Its work is on disk.
  hold      anything else (needs-triage, unlabeled, both labels at once)

Blocked tickets get an action only when agents already exist for them (leave or read).
Tickets whose blockers cannot be read go to `unreadable` and get no action at all.
Exit status is nonzero when the tracker, the agent list or a repository's worktrees cannot be read.
"""
import json, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import estate as E

HEADING = re.compile(r"^#{1,6}[ \t]*Blocked by[ \t]*:?[ \t]*$", re.I | re.M)
NEXT_HEADING = re.compile(r"^#{1,6}[ \t]", re.M)
# The one-line form, `Blocked by: #12, #14`, which trackers without native links use.
INLINE = re.compile(r"^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?Blocked by(?:\*\*)?[ \t]*:(?:\*\*)?(.*)$", re.I | re.M)
BULLET_START = re.compile(r"\n(?=[ \t]*[-*+][ \t])")
NONE_BULLET = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*)?(?:None|Nothing)\b", re.I)
STRIKE = re.compile(r"~~.*?~~", re.S)
REF = re.compile(
    r"https://github\.com/(?P<url_slug>[\w.-]+/[\w.-]+)/(?:issues|pull)/(?P<url_n>\d+)"
    r"|\b(?P<slug>[\w.-]+/[\w.-]+)#(?P<slug_n>\d+)"
    r"|(?<![\w/&])#(?P<n>\d+)\b"
)


def refs_in(text, tracker):
    out = []
    for m in REF.finditer(text):
        if m["url_n"]:
            out.append((m.start(), (m["url_slug"], int(m["url_n"]))))
        elif m["slug_n"]:
            out.append((m.start(), (m["slug"], int(m["slug_n"]))))
        else:
            out.append((m.start(), (tracker, int(m["n"]))))
    return out


def unresolved(item, tracker):
    """References in one bullet or list item that the text itself does not resolve."""
    if not item.strip() or NONE_BULLET.match(item):
        # "- None (can start immediately). It is deliberately ahead of #70 ..." names
        # issues precisely to say they do not gate it. Only a leading None/Nothing
        # counts: "- #120: nothing ships until this lands" is a live blocker.
        return set()
    refs = refs_in(item, tracker)
    if not refs:
        return set()
    spans = [s.span() for s in STRIKE.finditer(item)]
    if any(a <= refs[0][0] < b for a, b in spans):
        # The FIRST reference is struck: a resolved blocker. Skip the whole item, because
        # the prose explaining the resolution usually cites the same number again ("what
        # remains on [#95](...) is ...") and that citation is not a live dependency.
        return set()
    return {ref for pos, ref in refs if not any(a <= pos < b for a, b in spans)}


def declared_in_body(body, tracker):
    """Blockers the body declares, from a Blocked-by section and `Blocked by:` lines.

    Prose elsewhere ("proved once #96 lands, which is blocked by #198") is not read: in
    one real estate a quarter of all bodies say "blocked by" in passing, and quarantining
    them would bury the unreadable list that fail-closed reading depends on.
    """
    found, rest = set(), body
    m = HEADING.search(body)
    if m:
        after = body[m.end():]
        nxt = NEXT_HEADING.search(after)
        section = after[: nxt.start()] if nxt else after
        for bullet in BULLET_START.split(section):
            found |= unresolved(bullet, tracker)
        rest = body[: m.start()] + (after[nxt.start():] if nxt else "")
    for line in INLINE.finditer(rest):
        for item in re.split(r"[,;]", line.group(1)):
            found |= unresolved(item, tracker)
    return found


class Tracker:
    def __init__(self, slug):
        self.slug = slug
        self.open = {}
        # The full paginated list of open issues AND pull requests. A `--limit` would
        # silently truncate, and a blocker past the limit would read as closed.
        for item in E.gh_lines(
            f"repos/{slug}/issues?state=open&per_page=100",
            "--jq", ".[] | {number, title, pr: (.pull_request != null), labels: [.labels[].name]}",
        ):
            self.open[item["number"]] = item
        self._state = {}

    def is_open(self, ref):
        slug, n = ref
        if slug == self.slug and n in self.open:
            return True
        # Absent from the open list is not proof of closed: `#1234` typed for `#123` names
        # an issue that does not exist, and that must not unblock anything. Ask, and let a
        # failed lookup quarantine the ticket.
        if ref not in self._state:
            self._state[ref] = E.call("gh", "api", f"repos/{slug}/issues/{n}", "--jq", ".state").strip()
        return self._state[ref] == "open"

    def blockers(self, n):
        body = E.call("gh", "api", f"repos/{self.slug}/issues/{n}", "--jq", ".body // \"\"")
        if not body.strip():
            raise E.Refused(f"#{n} body came back empty; refusing to call it unblocked")
        refs = declared_in_body(body, self.slug)
        # Native links carry their own repository and state, so a cross-repository
        # blocker is never mistaken for the tracker's issue with the same number.
        for line in E.gh_lines(
            f"repos/{self.slug}/issues/{n}/dependencies/blocked_by?per_page=100",
            "--jq", ".[] | {url: .repository_url, number, state}",
        ):
            slug = line["url"].split("/repos/", 1)[1]
            refs.add((slug, line["number"]))
            if slug != self.slug:
                self._state[(slug, line["number"])] = line["state"]
        refs.discard((self.slug, n))
        return sorted(refs), sorted(r for r in refs if self.is_open(r))


def label(ref, tracker):
    slug, n = ref
    return n if slug == tracker else f"{slug}#{n}"


def action(cfg, labels, mine, trees):
    if mine:
        return "leave" if any(a.get("agent_status") == "working" for a in mine) else "read"
    dispatch, surface = cfg["labels"]["dispatch"], cfg["labels"]["surface"]
    if dispatch in labels and surface in labels:
        return "hold"
    if surface in labels:
        return "surface"
    if dispatch in labels:
        # A fresh dispatch beside an existing worktree abandons whatever is in it.
        return "orphaned" if trees else "dispatch"
    return "hold"


def changes(prev, cur):
    """What moved since the recorded baseline, so the report can say only that."""
    if prev is None:
        return None
    def rows(snap):
        return {r["number"]: r for key in ("frontier", "blocked") for r in snap[key]}
    before, after = rows(prev), rows(cur)
    unread_before = {u["number"] for u in prev["unreadable"]}
    unread_after = {u["number"] for u in cur["unreadable"]}
    front_before = {r["number"] for r in prev["frontier"]}
    return {
        "closed": sorted((set(before) | unread_before) - set(after) - unread_after),
        "entered_frontier": sorted(r["number"] for r in cur["frontier"] if r["number"] not in front_before),
        "action_changed": {
            n: [before[n].get("action"), r["action"]]
            for n, r in after.items() if "action" in r and "action" in before.get(n, {}) and before[n]["action"] != r["action"]
        },
        "newly_unreadable": sorted(unread_after - unread_before),
        "agents_changed": {
            name: [prev["agents"].get(name), status]
            for name, status in cur["agents"].items() if prev["agents"].get(name) != status
        } | {name: [status, None] for name, status in prev["agents"].items() if name not in cur["agents"]},
    }


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cfg = E.load(sys.argv[1])
    record = "--record" in sys.argv[2:]
    try:
        tracker = Tracker(cfg["tracker"])
        listed = E.agents()
        trees = {}
        for repo in cfg["repos"]:
            for w in E.worktrees(repo):
                n = E.ticket_of_worktree(w.get("branch"), w["path"])
                if n is not None:
                    trees.setdefault(n, []).append(w["path"])
    except E.Refused as e:
        print(json.dumps({"error": str(e), "dispatch": "none this tick"}, indent=2))
        sys.exit(2)

    out = {"frontier": [], "blocked": [], "unreadable": [], "agents": {}}
    for n in sorted(k for k, v in tracker.open.items() if not v["pr"]):
        item = tracker.open[n]
        try:
            declared, open_blockers = tracker.blockers(n)
        except E.Refused as e:
            out["unreadable"].append({"number": n, "title": item["title"], "error": str(e)})
            continue
        mine = E.ticket_agents(cfg, listed, n)
        row = {
            "number": n,
            "title": item["title"],
            "labels": item["labels"],
            "agents": {a["name"]: a.get("agent_status") for a in sorted(mine, key=lambda a: a["name"])},
            "worktrees": trees.get(n, []),
        }
        if open_blockers:
            row["open_blockers"] = [label(r, tracker.slug) for r in open_blockers]
            if mine:
                # A blocker added after dispatch does not stop a live agent, and its agent
                # still crashes and finishes like any other.
                row["action"] = action(cfg, item["labels"], mine, trees.get(n))
            out["blocked"].append(row)
        else:
            row["declared_blockers"] = [label(r, tracker.slug) for r in declared]
            row["action"] = action(cfg, item["labels"], mine, trees.get(n))
            out["frontier"].append(row)

    prefix = cfg["agent_prefix"]
    out["agents"] = {a["name"]: a.get("agent_status") for a in listed if (a.get("name") or "").startswith(prefix)}

    baseline = cfg["_root"] / "last-tick.json"
    prev = json.loads(baseline.read_text()) if baseline.is_file() else None
    out["changes"] = changes(prev, out)
    print(json.dumps(out, indent=2))
    if record:
        tmp = baseline.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: v for k, v in out.items() if k != "changes"}, indent=2))
        os.replace(tmp, baseline)


if __name__ == "__main__":
    main()
