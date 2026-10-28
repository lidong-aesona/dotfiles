#!/usr/bin/env python3
"""One metrics row for an epic: how much the owner had to step in, and how long it took.

    python3 metrics.py <estate-dir> <repo-slug> <epic-number> [--approved <ISO time>] [--append]

Reads the epic's native sub-issues (cross-repository ones included). Prints one Markdown table
row for <estate>/metrics.md; --append also adds it there, once per epic. Refuses while any
sub-issue is open.

- approved: the first time the estate's `labels.spec_approved` label went on the epic. An epic
  approved before the label existed needs --approved; the baseline row (research #210) used the
  epic's creation, passed as --approved.
- done: the last sub-issue's close.
- interventions: acts by `owner_login` on the epic and its sub-issues after approval: comments,
  plus closed, reopened, labeled and unlabeled events. The approval label itself is not counted.
  Owner chat asks are not on GitHub and are not counted, so compare rows by this definition only.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import estate as E  # noqa: E402

ACTS = {"closed", "reopened", "labeled", "unlabeled"}


def pages(path):
    """Every item of a paginated list endpoint, raising rather than returning a partial list."""
    raw = E.call("gh", "api", "--paginate", "--slurp", path)
    try:
        return [item for page in json.loads(raw) for item in page]
    except (ValueError, TypeError) as e:
        raise E.Refused(f"{path} unreadable ({e}): {raw[:200]!r}")


def when(text):
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as e:
        raise E.Refused(f"not an ISO time: {text!r}") from e


def approval(timeline, label, given):
    """(time, source) the spec was approved, from the label event, else --approved."""
    for e in timeline:
        if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == label:
            return when(e["created_at"]), e, "label"
    if given is None:
        raise E.Refused(f"no {label!r} label event on the epic; pass --approved <ISO time>")
    return when(given), None, "given"


def owner_acts(events, owner, start, skip):
    return sum(1 for e in events
               if e is not skip
               and (e.get("actor") or e.get("user") or {}).get("login") == owner
               and e.get("event") in ACTS | {"commented"}
               and e.get("created_at") and when(e["created_at"]) >= start)


def row(root, slug, epic, given):
    cfg = E.load(root)
    owner = cfg.get("owner_login")
    label = cfg["labels"].get("spec_approved")
    if not owner or not label:
        raise E.Refused("estate.toml needs owner_login and [labels].spec_approved")

    timeline = pages(f"repos/{slug}/issues/{epic}/timeline")
    start, label_event, source = approval(timeline, label, given)

    subs = pages(f"repos/{slug}/issues/{epic}/sub_issues")
    if not subs:
        raise E.Refused(f"{slug}#{epic} has no sub-issues")
    still_open = [s["html_url"] for s in subs if s.get("state") != "closed"]
    if still_open:
        raise E.Refused(f"still open: {' '.join(still_open)}")
    done = max(when(s.get("closed_at")) for s in subs)

    acts = owner_acts(timeline, owner, start, label_event)
    for s in subs:
        sub_slug = s["repository_url"].split("/repos/", 1)[1]
        acts += owner_acts(pages(f"repos/{sub_slug}/issues/{s['number']}/timeline"), owner, start, None)
    hours = (done - start).total_seconds() / 3600
    return (f"| {slug}#{epic} | {start:%Y-%m-%dT%H:%MZ} ({source}) | {done:%Y-%m-%dT%H:%MZ} | "
            f"{hours:.1f} | {len(subs)} | {acts} | {acts / len(subs):.2f} |")


def main(argv):
    append = "--append" in argv
    args = [a for a in argv if a != "--append"]
    given = None
    if "--approved" in args:
        i = args.index("--approved")
        if i + 1 >= len(args):
            sys.exit(__doc__)
        given = args[i + 1]
        del args[i:i + 2]
    if len(args) != 3 or not args[2].isdigit():
        sys.exit(__doc__)
    root = Path(args[0])
    try:
        line = row(root, args[1], int(args[2]), given)
    except E.Refused as e:
        sys.exit(f"!! {e}")
    print(line)
    if append:
        table = root / "metrics.md"
        if not table.exists():
            sys.exit(f"!! {table} is missing; restore its header before appending")
        epic_cell = line.split(" | ", 1)[0] + " | "
        if any(l.startswith(epic_cell) for l in table.read_text().splitlines()):
            sys.exit(f"!! {table} already has a row for {args[1]}#{args[2]}")
        with open(table, "a") as f:
            f.write(line + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
