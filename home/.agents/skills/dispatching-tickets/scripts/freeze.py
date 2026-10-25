#!/usr/bin/env python3
"""Report estate freezes, or refuse a PR merge at an exact head (never merge).

    python3 freeze.py <estate>
    python3 freeze.py <estate> --repo aesona/aesonaOS --pr 123 --head <full-sha>

A repo:path entry matches a file/subtree; repo: (empty path) freezes the whole repo.
Until is a review date/condition, not automatic expiry. Clear the table only after
owner confirmation. A failed read, truncated file list or moving head refuses.
"""
import argparse
import json
import re
import sys
from pathlib import PurePosixPath

import estate as E


def state(cfg):
    freeze = cfg.get("freeze")
    if freeze is None:
        return None
    if not isinstance(freeze, dict):
        raise E.Refused("freeze must be a table")
    repos, paths = freeze.get("repos"), freeze.get("paths")
    known = {r["slug"] for r in cfg["repos"]}
    if not isinstance(repos, list) or not repos or any(not isinstance(r, str) or r not in known for r in repos):
        raise E.Refused("freeze.repos must name estate repository slugs")
    if not isinstance(paths, list) or not paths:
        raise E.Refused("freeze.paths must contain repo:path entries")
    covered = set()
    for entry in paths:
        if not isinstance(entry, str) or ":" not in entry:
            raise E.Refused("freeze.paths must contain repo:path entries")
        repo, path = entry.split(":", 1)
        if (repo not in repos or path.startswith("/") or ".." in PurePosixPath(path).parts
                or "\\" in path or any(c in path for c in "*?[]") or "//" in path or "." in path.split("/")):
            raise E.Refused(f"invalid freeze path: {entry!r}")
        covered.add(repo)
    if covered != set(repos):
        raise E.Refused("each frozen repo must have a path")
    for key in ("reason", "until"):
        if not isinstance(freeze.get(key), str) or not freeze[key].strip():
            raise E.Refused(f"freeze.{key} must be nonempty text")
    return freeze


def matches(freeze, repo, files):
    if freeze is None or repo not in freeze["repos"]:
        return []
    paths = [p.split(":", 1)[1] for p in freeze["paths"] if p.split(":", 1)[0] == repo]
    return sorted({name for f in files for name in (f["filename"], f.get("previous_filename"))
                   if name and any(p == "" or name == p.rstrip("/") or name.startswith(p.rstrip("/") + "/") for p in paths)})


def check(cfg, repo, pr, head):
    freeze = state(cfg)
    if repo not in {r["slug"] for r in cfg["repos"]} or pr <= 0 or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise E.Refused("merge check requires an estate repo, positive PR and full head SHA")
    endpoint = f"repos/{repo}/pulls/{pr}"
    before = E.parse(E.call("gh", "api", endpoint))
    if before["head"]["sha"] != head or before["state"] != "open":
        raise E.Refused("PR is not open at the expected head")
    files = E.gh_lines(endpoint + "/files?per_page=100", "--jq", ".[] | {filename, previous_filename}")
    if not files or len(files) != before["changed_files"]:
        raise E.Refused("PR file list empty or truncated; refusing merge")
    after = E.parse(E.call("gh", "api", endpoint))
    if after["head"]["sha"] != head or after["state"] != "open" or after["base"]["sha"] != before["base"]["sha"]:
        raise E.Refused("PR head/base/state changed during freeze check")
    hits = matches(freeze, repo, files)
    if hits:
        raise E.Refused(f"freeze blocks {repo}#{pr}: {', '.join(hits)}; {freeze['reason']}; until {freeze['until']}")
    return {"merge": "allowed by freeze only", "repo": repo, "pr": pr, "head": head, "freeze": freeze}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("estate")
    parser.add_argument("--repo")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--head")
    args = parser.parse_args()
    cfg = E.load(args.estate)
    try:
        if any(v is not None for v in (args.repo, args.pr, args.head)):
            if not all(v is not None for v in (args.repo, args.pr, args.head)):
                raise E.Refused("--repo, --pr and --head are required together")
            result = check(cfg, args.repo, args.pr, args.head)
        else:
            result = {"freeze": state(cfg), "holds": cfg.get("holds", [])}
        print(json.dumps(result, indent=2))
    except (E.Refused, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"merge": "refused", "error": str(exc)}, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
