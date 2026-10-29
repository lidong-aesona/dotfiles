"""Shared plumbing for the dispatch scripts: the estate file, and calls that fail closed.

Every script here answers a question whose wrong answer is dangerous in one direction
only: "not blocked", "nothing is running", "already merged". So every external call
either succeeds or raises. Nothing returns an empty answer on failure.
"""
import json, re, shlex, subprocess, sys, time, tomllib
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
    seen = {cfg["tracker"]: ""}
    for t in cfg.get("trackers", []):
        # A prefix keeps another tracker's #235 apart from this tracker's #235 in agent
        # names, branches, worktree directories and tickets/<key>/. Letters only: the
        # character before the number must say which tracker owns it.
        if not isinstance(t.get("slug"), str) or not re.fullmatch(r"[a-z]+", t.get("prefix") or ""):
            sys.exit(f"!! every [[trackers]] entry needs a slug and a lowercase-letter prefix in {path}")
        if t["slug"] in seen or t["prefix"] in seen.values():
            sys.exit(f"!! two trackers share a slug or prefix in {path}")
        seen[t["slug"]] = t["prefix"]
    cfg["_root"] = root
    return cfg


def trackers(cfg):
    """(slug, prefix) for every tracked repository; the main tracker's prefix is empty."""
    return [(cfg["tracker"], "")] + [(t["slug"], t["prefix"]) for t in cfg.get("trackers", [])]


def key_for(cfg, slug, n):
    """A ticket's estate key: the bare number on the main tracker, `<prefix><n>` elsewhere."""
    return _keyed(dict(trackers(cfg))[slug], n)


def locate(cfg, key):
    """The (slug, number) a key names. Raises on a key no tracker owns."""
    m = re.fullmatch(r"([a-z]*)(\d+)", str(key))
    prefixes = {p: s for s, p in trackers(cfg)}
    if not m or m[1] not in prefixes or int(m[2]) <= 0:
        raise Refused(f"{key!r} names no tracked issue")
    return prefixes[m[1]], int(m[2])


def parse_key(cfg, text):
    """A key typed on a command line (`#1254`, `1254`, `r235`) in its canonical form."""
    return key_for(cfg, *locate(cfg, str(text).lstrip("#")))


def order(key):
    """Sort keys of mixed trackers: main tracker numbers first, then by prefix and number."""
    m = re.fullmatch(r"([a-z]*)(\d+)", str(key))
    return (m[1], int(m[2])) if m else ("~", 0)


def _keyed(prefix, n):
    return f"{prefix}{n}" if prefix else int(n)


def prefix_pattern(cfg):
    # Longest first, so the empty main prefix is the last alternative tried.
    return "|".join(sorted((re.escape(p) for _, p in trackers(cfg)), key=len, reverse=True))


def run(*args, cwd=None, timeout=120):
    try:
        return subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refused(f"{args[0]} unreadable: {e}") from e


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


def hc(*args):
    """An hc command. hc on this host reaches every attached machine by address."""
    return ["hc", *args]


def address(machine, terminal):
    """The hc address of a terminal: bare locally, `<machine>/<terminal>` elsewhere."""
    return f"{machine}/{terminal}" if machine and "/" not in terminal else terminal


def machines(cfg):
    return list(dict.fromkeys(["", *cfg.get('watcher', {}).get('machines', [""])]))


def machine_conflict(cfg, labels):
    return len({target for label, target in cfg.get('machine_labels', {}).items() if label in labels}) > 1


def machine_for(cfg, labels):
    targets = {target for label, target in cfg.get('machine_labels', {}).items() if label in labels}
    if len(targets) > 1:
        raise Refused('conflicting machine labels')
    target = next(iter(targets), "")
    if target not in machines(cfg):
        raise Refused(f'machine {target!r} is not watched')
    return target


def machine_repos(cfg, machine=""):
    if not machine:
        return cfg['repos']
    settings = cfg.get('machines', {}).get(machine)
    if not settings:
        raise Refused(f'no configuration for machine {machine}')
    repos = []
    for repo in cfg['repos']:
        path = settings.get('repos', {}).get(repo['name'])
        if not path or not path.startswith('/'):
            raise Refused(f'{machine}: missing absolute path for {repo["name"]}')
        repos.append(dict(repo, path=path))
    return repos


def machine_argv(cfg, machine, *args, cwd=None):
    if not machine:
        return list(args)
    settings = cfg['machines'][machine]
    command = (f'cd -- {shlex.quote(str(cwd))} && ' if cwd else '') + shlex.join(args)
    return ['ssh', '-n', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
            settings['ssh'], 'zsh -lic ' + shlex.quote(command)]


def machine_run(cfg, machine, *args, cwd=None):
    # Locally cwd goes to the process; remotely it is a `cd` inside the ssh command.
    return run(*machine_argv(cfg, machine, *args, cwd=cwd), cwd=None if machine else cwd)


def machine_read(cfg, machine, *args):
    """A read-only command on a machine, retried like any other read."""
    return call(*machine_argv(cfg, machine, *args))


def machine_call(cfg, machine, *args, cwd=None):
    # Mutations must not be retried: a lost reply may have succeeded remotely.
    result = machine_run(cfg, machine, *args, cwd=cwd)
    if result.returncode:
        raise Refused(f'{machine or "local"}: {args[0]} failed: {result.stderr}')
    return result.stdout


def inventory(cfg, errors=None):
    """Read every machine; callers supplying errors can quarantine failures separately.

    Callers omitting errors retain strict refusal on any unknown ownership.
    Partial reads remain evidence of ownership, never evidence of absence.
    """
    listed, trees = [], []
    for machine in machines(cfg):
        try:
            listed.extend(dict(a, machine=machine) for a in agents(machine))
            for repo in machine_repos(cfg, machine):
                trees.extend(dict(w, machine=machine, repo=repo) for w in worktrees(cfg, repo, machine))
        except Refused as e:
            if errors is None:
                raise Refused(f'{machine or "local"} ownership unknown/busy: {e}') from e
            errors[machine] = str(e)
    return listed, trees


def baseline(cfg):
    path = cfg['_root'] / 'last-tick.json'
    try:
        return json.loads(path.read_text()) if path.is_file() else None
    except (OSError, ValueError) as e:
        raise Refused(f'ownership baseline unreadable: {e}') from e


def ownership_numbers(cfg, listed, trees):
    """Complete ownership, including closed tickets absent from frontier rows."""
    owned = {machine: set() for machine in machines(cfg)}
    for a in listed:
        owned[a['machine']].update(agent_tickets(cfg, a))
    for w in trees:
        n = ticket_of_worktree(cfg, w.get('branch'), w['path'])
        if n is not None:
            owned[w['machine']].add(n)
    return {machine: sorted(numbers, key=order) for machine, numbers in owned.items()}


def unknown_ownership(cfg, errors, listed, trees, previous):
    """Retain last-known owners until their machine is successfully read again.

    The explicit number ledger survives recorded ticks and ticket closure, which
    removes closed tickets from frontier rows before cleanup runs.
    """
    previous = previous or {}
    unknown = {machine: set(previous.get('unknown_ownership', {}).get(machine, [])) |
               set(previous.get('ownership', {}).get(machine, [])) for machine in errors}
    for machine in errors:
        prefix = cfg.get('machines', {}).get(machine, {}).get('agent_prefix') or (cfg['agent_prefix'] if not machine else None)
        for identity in previous.get('agents', {}):
            explicit = identity.startswith(f'{machine or "local"}:')
            name = identity.split(':', 1)[1] if explicit else identity
            if (explicit or (prefix and name.startswith(prefix))) and (n := ticket_of_agent(cfg, name)) is not None:
                unknown[machine].add(n)
        unknown[machine].update(n for a in listed if a['machine'] == machine for n in agent_tickets(cfg, a))
        unknown[machine].update(n for w in trees if w['machine'] == machine
                                if (n := ticket_of_worktree(cfg, w.get('branch'), w['path'])) is not None)
        for key in ('frontier', 'blocked'):
            for row in previous.get(key, []):
                owned = machine in row.get('agent_machines', {}).values() or machine in row.get('worktree_machines', {}).values()
                # Older snapshots may have names but no explicit machine map.
                owned |= bool(prefix and any(name.startswith(prefix) for name in row.get('agents', {})))
                if owned:
                    unknown[machine].add(row['number'])
    return {machine: sorted(numbers, key=order) for machine, numbers in unknown.items()}


def _listing(machine, all_=False):
    """hc's terminals on one machine. Raises unless that machine is attached and connected."""
    raw = call(*hc("ls", "--json", *(["--all"] if all_ else [])))
    listed = parse(raw, "terminals")
    attached = parse(raw, "machines")
    if machine and not any(isinstance(m, dict) and m.get("name") == machine and m.get("state") == "connected"
                           for m in attached if isinstance(attached, list)):
        raise Refused(f"{machine} is not connected to hc")
    if not isinstance(listed, list) or any(not isinstance(t, dict) or
            not isinstance(t.get("address"), str) or not t["address"].strip() or
            not isinstance(t.get("state"), str) or
            not (isinstance(t.get("cwd"), str) and t["cwd"].startswith("/"))
            for t in listed):
        raise Refused(f"{machine or 'local'} terminal list is unreadable")
    here = [t for t in listed if (t["address"].startswith(f"{machine}/") if machine else "/" not in t["address"])]
    return [dict(t, terminal=t["address"].split("/", 1)[-1]) for t in here]


def terminals(machine=""):
    """Every live terminal on one machine, agent or not: each cwd occupies its worktree."""
    return [t for t in _listing(machine) if t["state"] == "live"]


def agents(machine=""):
    """Every agent on one machine. Raises when the list cannot be read.

    Unknown agent state must never read as "nothing is running": to the frontier that
    means every ticket is undispatched and gets a second agent, and to cleanup it means
    every worktree is unguarded.

    An agent is a live terminal hc reports a status for, or any named terminal: dispatch
    names every agent it starts. A named agent whose process ended stays listed as
    `exited`, so it keeps owning its ticket until cleanup removes it. Unnamed agents
    (started by hand) are identified by address and owned through their cwd.
    """
    listed = []
    for t in _listing(machine, all_=True):
        named = isinstance(t.get("name"), str) and t["name"].strip()
        live = t["state"] == "live"
        if not (named or (live and isinstance(t.get("status"), str))):
            continue
        status = (t.get("status") or "unknown") if live else "exited"
        listed.append(dict(t, name=t.get("name") or t["terminal"], agent_status=status))
    return listed


def wait_ready(address, seconds):
    """Wait up to `seconds` for a fresh agent's first idle; return whether it is idle.

    hc counts an agent that has not reported yet as working, so the wait ends when it
    first goes idle. A start dialog leaves it working or blocked, and then it is not ready.
    """
    run(*hc("wait", address, "--timeout", str(seconds)), timeout=seconds + 30)
    status = parse(call(*hc("get", "--json", address)), "status")
    return isinstance(status, dict) and status.get("state") == "idle"


def stop_mark(agent):
    """When the agent's status last changed: hc's `status_ms`, None once it exited.

    hc 0.1.13 keeps `turn` at 0, so it cannot tell two idle stops apart. A turn that
    starts and ends between two polls still moves `status_ms`, and `hc ls` omits it.
    """
    if agent["agent_status"] == "exited":
        return None
    mark = parse(call(*hc("get", "--json", agent["address"])), "status_ms")
    if not isinstance(mark, int):
        raise Refused(f"{agent['address']} status time is unreadable: {mark!r}")
    return mark


def ignored(watcher):
    """Watcher-ignored agents, by name from `[watcher] ignore` or by the `dispatch.ignore=1` label.

    The label covers terminals hc cannot rename, such as an operator started without a name.
    """
    labelled = parse(call(*hc("ls", "--json", "--all", "--label", "dispatch.ignore=1")), "terminals")
    if not isinstance(labelled, list) or any(not isinstance(t, dict) or not isinstance(t.get("address"), str)
                                             for t in labelled):
        raise Refused("ignored terminal list is unreadable")
    names, addresses = set(watcher.get("ignore", [])), {t["address"] for t in labelled}
    return lambda a: a["name"] in names or a["address"] in addresses


def ticket_agents(cfg, listed, n):
    """Agents that belong to ticket n: `<prefix><n>` plus any suffix not starting with a digit.

    `ticket-110`, `ticket-110c` (a replacement on another agent kind) and
    `ticket-110-followup` all belong to #110; `ticket-11` never claims them.
    """
    return [a for a in listed if n in agent_tickets(cfg, a)]


def agent_tickets(cfg, agent):
    """Name and cwd can carry different tickets during stacked-work handoffs."""
    return {n for n in (ticket_of_agent(cfg, agent.get('name')),
                        ticket_of_worktree(cfg, None, agent.get('cwd') or '')) if n is not None}


def ticket_of_agent(cfg, name):
    """The key in `<agent prefix><key>`: `w1-ticket-110c` is 110, `w1-ticket-r235` is r235."""
    for prefix in [cfg['agent_prefix'], *[m['agent_prefix'] for m in cfg.get('machines', {}).values()]]:
        m = re.match(rf"^{re.escape(prefix)}({prefix_pattern(cfg)})(\d+)", name or "")
        if m:
            return _keyed(m.group(1), m.group(2))
    return None


def ticket_of_worktree(cfg, branch, path):
    """The ticket key from the branch (`build/107-three-stores`, `build/r235-setup`), else the
    directory (`130-no-standing-admin`). Older branches carry the number only in the directory,
    and both must resolve or that half of the tree is invisible to the loop. A prefixed key counts
    only in the names dispatch creates (`build/` branch, `build-` directory): an
    unrelated `r899-af181e83` worktree is not research ticket 899."""
    prefixed = prefix_pattern(cfg)
    for pattern, text in ((rf"^build/({prefixed})(\d+)-", branch),
                          (r"^[\w.-]+/()(\d+)-", branch),
                          (rf"^build-({prefixed})(\d+)(?:-|$)", Path(path).name),
                          (r"^(?:[\w.]+-)?()(\d+)(?:-|$)", Path(path).name)):
        m = re.match(pattern, text or "")
        if m:
            return _keyed(m.group(1), m.group(2))
    return None


def worktrees(cfg, repo, machine=""):
    """Linked worktrees of one estate repository, minus its keep list.

    Read from `git worktree list --porcelain` on the machine that owns the checkout.
    """
    raw = machine_read(cfg, machine, "git", "-C", repo["path"], "worktree", "list", "--porcelain")
    listed = []
    for block in raw.strip().split("\n\n"):
        fields = dict((line.split(" ", 1) + [""])[:2] for line in block.splitlines() if line)
        if "worktree" not in fields or not fields["worktree"].startswith("/"):
            raise Refused(f"{machine or 'local'} worktree list is unreadable: {block[:200]!r}")
        branch = fields.get("branch", "")
        listed.append({"path": fields["worktree"], "is_detached": "detached" in fields,
                       "branch": branch.removeprefix("refs/heads/") if branch else None})
    # The first entry is the main checkout. Detached linked trees also carry ownership,
    # even though cleanup cannot land them.
    return [w for w in listed[1:] if Path(w["path"]).name not in repo.get("keep", [])]
