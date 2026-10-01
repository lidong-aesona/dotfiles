#!/usr/bin/env python3
"""Choose the agent profile for one ticket, from the estate's routing table and quota ledger.

    python3 route.py <estate-dir> <type>                         # a ticket of this type
    python3 route.py <estate-dir> <type> --author <provider>     # a review: never the author's provider

Prints JSON: the chosen profile, its provider, the skill file it starts with, the full argument
list for `herdr agent start ... --`, and every candidate's pace so the choice can be reported.

Candidates are the type's `profiles`, minus exhausted ones, minus the author's provider for a
review. A fixed type takes the first that remains. A fungible type takes the one whose provider
is furthest behind its pace: the fraction of its quota period elapsed minus the fraction of its
allowance used, read from quota.toml, less `routing.agent_weight` for each live agent on that
provider. A provider with no reading, or one older than `routing.stale_hours`, counts as on
pace, so it neither wins nor loses on a guess. Ties go to list order.

Live agents count because a reading only moves when someone reads it: without them, every
fungible ticket in one tick would go to the same provider. An unreadable agent list refuses,
like every other unknown in this skill.

An estate with no [routing] table routes everything to agents.active, as before routing existed.
"""
import json, re, sys, tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import estate as E

SKILLS = Path("~/.agents/skills").expanduser()


def when(text):
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def pace(ledger, provider, now, stale_hours):
    """Elapsed share of the period minus used share; 0.0 when there is no fresh reading."""
    r = ledger.get(provider)
    if not r or "read_at" not in r or "period_start" not in r or "used" not in r:
        return 0.0, "no reading"
    if now - when(r["read_at"]) > timedelta(hours=stale_hours):
        return 0.0, f"reading from {r['read_at']} is stale"
    period = timedelta(days=r.get("period_days", 7))
    elapsed = ((now - when(r["period_start"])) % period) / period
    return elapsed - float(r["used"]), f"{elapsed:.0%} of period elapsed, {float(r['used']):.0%} used"


def live_per_provider(cfg):
    """Live agents per provider, matched by the profile suffix after the ticket number.
    A reviewer is `<prefix><n>r<suffix>`."""
    by_suffix = {p.get("suffix", ""): p.get("provider") for p in cfg["agents"]["profiles"].values()}
    counts = {}
    for a in E.agents():
        m = re.match(rf"^{re.escape(cfg['agent_prefix'])}\d+(.*)$", a.get("name") or "")
        if not m:
            continue
        suffix = m.group(1)
        if suffix not in by_suffix and suffix.startswith("r"):
            suffix = suffix[1:]
        provider = by_suffix.get(suffix)
        if provider:
            counts[provider] = counts.get(provider, 0) + 1
    return counts


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    cfg = E.load(args[0])
    kind = args[1]
    author = args[args.index("--author") + 1] if "--author" in args else None
    profiles = cfg["agents"]["profiles"]
    routing = cfg.get("routing")

    if not routing:
        name = cfg["agents"]["active"]
        print(json.dumps({"profile": name, "provider": profiles[name].get("provider"),
                          "skill": None, "args": profiles[name].get("args", []),
                          "reason": "no [routing] in estate.toml: agents.active"}, indent=2))
        return

    types = routing.get("types", {})
    if kind not in types:
        sys.exit(f"!! no routing.types.{kind}; known types: {', '.join(sorted(types))}")
    t = types[kind]
    if author and not t.get("review"):
        sys.exit(f"!! --author is for review types; routing.types.{kind} has no `review = true`")
    if t.get("review") and not author:
        sys.exit(f"!! routing.types.{kind} is a review type: pass --author <the diff author's provider>")

    skipped, candidates = [], []
    for name in t["profiles"]:
        p = profiles[name]
        if p.get("exhausted_until"):
            skipped.append(f"{name}: exhausted until {p['exhausted_until']}")
        elif author and p["provider"] == author:
            skipped.append(f"{name}: same provider as the author")
        else:
            candidates.append(name)
    if not candidates:
        sys.exit("!! no candidate profile is left (" + "; ".join(skipped) +
                 "). Ask the user, or record a fallback in this type's profiles")

    ledger_path = cfg["_root"] / "quota.toml"
    ledger = tomllib.loads(ledger_path.read_text()) if ledger_path.is_file() else {}
    now = datetime.now(timezone.utc)
    stale = routing.get("stale_hours", 12)
    paces = {n: pace(ledger, profiles[n]["provider"], now, stale) for n in candidates}
    if t.get("fungible") and len(candidates) > 1:
        try:
            live = live_per_provider(cfg)
        except E.Refused as e:
            sys.exit(f"!! cannot read the agent list, so cannot spread this tick's load: {e}")
        weight = routing.get("agent_weight", 0.05)
        for n in candidates:
            k = live.get(profiles[n]["provider"], 0)
            if k:
                v, why = paces[n]
                paces[n] = (v - weight * k, f"{why}; {k} live agents")

    if t.get("fungible"):
        chosen = max(candidates, key=lambda n: (paces[n][0], -candidates.index(n)))
        reason = "fungible: furthest behind pace"
    else:
        chosen = candidates[0]
        reason = "fixed: first available profile"

    p = profiles[chosen]
    skill = None
    argv = list(p.get("args", []))
    if t.get("skill"):
        skill = SKILLS / t["skill"] / "SKILL.md"
        if not skill.is_file():
            sys.exit(f"!! routing.types.{kind}.skill names {skill}, which does not exist")
        if not p.get("skill_flag"):
            sys.exit(f"!! profile {chosen!r} has no `skill_flag`, so it cannot be started with {skill}")
        argv += [p["skill_flag"], str(skill)]

    print(json.dumps({
        "type": kind, "profile": chosen, "provider": p["provider"], "kind": p["kind"],
        "suffix": p.get("suffix", ""), "skill": str(skill) if skill else None, "args": argv,
        "reason": reason,
        "candidates": {n: {"provider": profiles[n]["provider"], "pace": round(paces[n][0], 3),
                           "why": paces[n][1]} for n in candidates},
        "skipped": skipped,
    }, indent=2))


if __name__ == "__main__":
    main()
