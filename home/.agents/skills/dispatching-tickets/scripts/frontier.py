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
  unknown   routed to, or previously owned on, an unreadable machine: no action
  hold      an explicit estate holds entry (overrides labels, agents and worktrees),
            or anything else (needs-triage, unlabeled, both labels at once)

Blocked tickets get an action when held or when agents already exist for them.
Tickets whose blockers cannot be read go to `unreadable` and get no action at all.
Unreadable machines are reported separately and quarantine their routed/last-known owners.
Exit status is nonzero for unreadable tracker/baseline or invalid estate state.
"""
import json, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import estate as E
import freeze as F

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
        # The estate's other trackers, read this same tick: their open lists answer for
        # their issues, so a cross-tracker blocker reads the state the frontier rows do.
        self.peers = {}
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
        if slug in self.peers and n in self.peers[slug].open:
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


def is_key(cfg, n):
    """True for a canonical estate key: a positive main-tracker int or a configured prefix key."""
    try:
        return type(n) in (int, str) and E.parse_key(cfg, n) == n
    except E.Refused:
        return False


def label(cfg, ref, tracker):
    slug, n = ref
    return E.key_for(cfg, slug, n) if slug == tracker else f"{slug}#{n}"


def action(cfg, labels, mine, trees, number=None):
    if E.machine_conflict(cfg, labels):
        return 'hold'
    machine = E.machine_for(cfg, labels)
    if machine:
        if number in cfg.get('holds', []):
            return 'hold'
        if mine:
            return 'leave' if any(a.get('agent_status') == 'working' for a in mine) else 'read'
        if cfg['labels']['surface'] in labels:
            return 'surface'
        required = cfg.get('machines', {}).get(machine, {}).get('dispatch_requires')
        if not required or required not in labels:
            return 'hold'
        return 'orphaned' if trees else 'dispatch'
    # The original VM decision path below is unchanged.
    if number in cfg.get("holds", []):
        return "hold"
    if mine:
        return "leave" if any(a.get("agent_status") == "working" for a in mine) else "read"
    dispatch, surface = cfg["labels"]["dispatch"], cfg["labels"]["surface"]
    if dispatch in labels and surface in labels:
        return "hold"
    if surface in labels:
        return "surface"
    if dispatch in labels:
        # A fresh dispatch beside an existing worktree abandons whatever is in it.
        if trees:
            return "orphaned"
        # Optional approval gate: a breakdown files sub-tickets with the dispatch label and its
        # reviewer adds this one on approve. Recovery of started work (orphaned) is not gated.
        required = cfg["labels"].get("dispatch_requires")
        return "hold" if required and required not in labels else "dispatch"
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
        "closed": sorted((set(before) | unread_before) - set(after) - unread_after, key=E.order),
        "entered_frontier": sorted((r["number"] for r in cur["frontier"] if r["number"] not in front_before), key=E.order),
        "action_changed": {
            n: [before[n].get("action"), r["action"]]
            for n, r in after.items() if "action" in r and "action" in before.get(n, {}) and before[n]["action"] != r["action"]
        },
        "newly_unreadable": sorted(unread_after - unread_before, key=E.order),
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
        holds = cfg.get("holds", [])
        if not isinstance(holds, list) or not all(is_key(cfg, n) for n in holds):
            raise E.Refused("holds must list main-tracker issue numbers or prefixed keys such as \"r235\"")
        freeze = F.state(cfg)
        trackers = [Tracker(slug) for slug, _ in E.trackers(cfg)]
        for t in trackers:
            t.peers = {p.slug: p for p in trackers if p is not t}
        items = {E.key_for(cfg, t.slug, n): (t, n, item) for t in trackers for n, item in t.open.items()}
        previous = E.baseline(cfg)
        machine_errors = {}
        listed, all_trees = E.inventory(cfg, errors=machine_errors)
        unknown_ownership = E.unknown_ownership(cfg, machine_errors, listed, all_trees, previous)
        unknown_numbers = {n for numbers in unknown_ownership.values() for n in numbers}
        ownership = E.ownership_numbers(cfg, listed, all_trees)
        for machine, numbers in unknown_ownership.items():
            ownership[machine] = sorted(set(ownership[machine]) | set(numbers))
        trees = {}
        for w in all_trees:
            n = E.ticket_of_worktree(cfg, w.get("branch"), w["path"])
            if n is not None:
                trees.setdefault(n, []).append(w)
        for _, _, item in items.values():
            if not E.machine_conflict(cfg, item['labels']):
                E.machine_for(cfg, item['labels'])
    except E.Refused as e:
        print(json.dumps({"error": str(e), "dispatch": "none this tick"}, indent=2))
        sys.exit(2)

    out = {"frontier": [], "blocked": [], "unreadable": [], "agents": {},
           "holds": holds, "freeze": freeze, "machine_errors": machine_errors,
           "unknown_ownership": unknown_ownership, "ownership": ownership}
    for n in sorted((k for k, (_, _, v) in items.items() if not v["pr"]), key=E.order):
        tracker, number, item = items[n]
        try:
            declared, open_blockers = tracker.blockers(number)
        except E.Refused as e:
            out["unreadable"].append({"number": n, "title": item["title"], "error": str(e)})
            continue
        mine = E.ticket_agents(cfg, listed, n)
        conflict = E.machine_conflict(cfg, item['labels'])
        row = {
            "number": n,
            "title": item["title"],
            "labels": item["labels"],
            "agents": {a["name"]: a.get("agent_status") for a in sorted(mine, key=lambda a: a["name"])},
            "machine": None if conflict else E.machine_for(cfg, item['labels']),
            "agent_machines": {a['name']: a['machine'] for a in mine},
            "worktrees": [w['path'] for w in trees.get(n, [])],
            "worktree_machines": {w['path']: w['machine'] for w in trees.get(n, [])},
        }
        unknown = not conflict and (row['machine'] in machine_errors or n in unknown_numbers)
        if open_blockers:
            row["open_blockers"] = [label(cfg, r, tracker.slug) for r in open_blockers]
            if mine or n in holds or unknown or conflict:
                # Explicit holds override agent recovery too. Other blocked live agents
                # still crash and finish like any other.
                row["action"] = 'unknown' if unknown else action(cfg, item["labels"], mine, trees.get(n), n)
            out["blocked"].append(row)
        else:
            row["declared_blockers"] = [label(cfg, r, tracker.slug) for r in declared]
            row["action"] = 'unknown' if unknown else action(cfg, item["labels"], mine, trees.get(n), n)
            out["frontier"].append(row)

    out["agents"] = {f"{a['machine'] or 'local'}:{a['name']}": a.get("agent_status")
                     for a in listed if E.ticket_of_agent(cfg, a.get('name')) is not None}

    baseline = cfg["_root"] / "last-tick.json"
    out["changes"] = changes(previous, out)
    shown = out
    if "--brief" in sys.argv[2:]:
        # Tickets nobody acts on this tick (hold, leave, blocked with no agent) and that did not
        # change cost the tick model tokens for nothing; list their numbers only.
        delta = out['changes'] or {'action_changed': {}, 'entered_frontier': []}
        moved = set(delta["action_changed"]) | set(delta["entered_frontier"])
        act = lambda row: row.get("action") not in (None, "hold", "leave") or row["number"] in moved
        shown = dict(out)
        for key in ("frontier", "blocked"):
            shown[key] = [row for row in out[key] if act(row)]
            shown["quiet_" + key] = sorted((row["number"] for row in out[key] if not act(row)), key=E.order)
    print(json.dumps(shown, indent=2))
    if record:
        tmp = baseline.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: v for k, v in out.items() if k != "changes"}, indent=2))
        os.replace(tmp, baseline)


if __name__ == "__main__":
    main()
