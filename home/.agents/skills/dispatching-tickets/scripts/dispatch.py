#!/usr/bin/env python3
"""Mac launch and file-based recovery delivery.

    dispatch.py <estate> <n> <repo-name> <slug>
    dispatch.py <estate> <n> --read <agent>
    dispatch.py <estate> <n> --send <agent> <local-prompt-file>

The model still writes the bounded head and decides recovery, as on the VM.
Never retries mutations or resets an existing worktree.
"""
import json
import re
import shlex
import sys
from pathlib import Path

import estate as E
import prompt as P


def ticket_labels(cfg, n):
    slug, number = E.locate(cfg, n)
    labels = E.parse(E.call('gh', 'issue', 'view', str(number), '--repo', slug,
                            '--json', 'state,labels'))
    if labels.get('state') != 'OPEN':
        raise E.Refused(f'#{n} is not open')
    return [label['name'] for label in labels['labels']]


def target(cfg, n):
    labels = ticket_labels(cfg, n)
    from frontier import action
    if action(cfg, labels, [], [], n) != 'dispatch':
        raise E.Refused(f'#{n} is not eligible for agent dispatch')
    machine = E.machine_for(cfg, labels)
    if not machine:
        raise E.Refused('use DISPATCH.md for local VM dispatch')
    if n in cfg.get('holds', []):
        raise E.Refused(f'#{n} is held')
    return machine


def ticket_inventory(cfg, n, machine=None):
    errors = {}
    listed, trees = E.inventory(cfg, errors=errors)
    unknown = E.unknown_ownership(cfg, errors, listed, trees, E.baseline(cfg))
    if machine in errors or any(n in numbers for numbers in unknown.values()):
        raise E.Refused(f'#{n} ownership unknown on an unreadable machine: {errors}')
    return listed, trees, errors


# Linux caps one argument at 128 KiB; hc carries the text, never a shell command line.
MAX_PROMPT = 100_000


def agent_argv(profile, skill):
    """The agent's command, run through a login shell so it finds what a person's shell finds."""
    command = [profile['kind'], *profile['args'], profile.get('skill_flag', '--append-system-prompt'), skill]
    return ['zsh', '-lic', 'exec ' + shlex.join(command)]


def send_file(cfg, machine, name, path):
    """Paste a prompt file into an agent. The text travels inside hc, never through ssh."""
    path = Path(path)
    if not path.is_file() or not path.read_text().strip():
        raise E.Refused(f'empty/missing prompt: {path}')
    n = E.ticket_of_agent(cfg, name)
    if n is None:
        # Unnamed agents are addressed by terminal ID, with ownership
        # resolved by inventory before this helper is reached.
        errors = {}
        listed, _ = E.inventory(cfg, errors=errors)
        if machine in errors:
            raise E.Refused(f'{machine} ownership unreadable')
        owner = next((a for a in listed if a['machine'] == machine and a.get('name') == name), None)
        numbers = E.agent_tickets(cfg, owner) if owner else set()
        if len(numbers) != 1:
            raise E.Refused('cannot determine unique ticket for remote delivery')
        n = next(iter(numbers))
    preamble = P.machine_preamble(cfg, n, machine)
    if not path.read_text().startswith(preamble):
        wrapped = path.with_name(f'{path.stem}.mac-delivery.md')
        wrapped.write_text(preamble + path.read_text())
        path = wrapped
    text = path.read_text()
    if len(text.encode()) > MAX_PROMPT:
        raise E.Refused(f'prompt is over {MAX_PROMPT} bytes: {path}')
    # A blocked agent refuses the paste: answering its dialog is a person's call.
    E.call(*E.hc('send', E.address(machine, name), text), tries=1)


def read_tail(cfg, machine, name):
    text = E.call(*E.hc('read', E.address(machine, name), '--lines', '150'))
    if not text.strip():
        raise E.Refused(f'{machine}:{name} tail is unreadable')
    profile = cfg['agents']['profiles'][cfg['machines'][machine]['profile']]
    # A prior error in scrollback is not proof that the current turn crashed.
    ending = '\n'.join(text.rstrip().splitlines()[-40:])
    state = 'stopped'
    if any(marker in ending for marker in profile.get('exhausted', [])):
        state = 'exhausted'
    elif any(marker in ending for marker in profile.get('crashed', [])):
        state = 'crashed'
    elif 'Ready for review' in ending or 'Ready for re-review' in ending:
        state = 'ready-for-review'
    return dict(machine=machine, agent=name, classification=state, tail=text)


def launch(cfg, n, repo_name, slug):
    machine = target(cfg, n)
    listed, trees, _ = ticket_inventory(cfg, n, machine)  # local AND remote owners
    if E.ticket_agents(cfg, listed, n) or any(
            E.ticket_of_worktree(cfg, w.get('branch'), w['path']) == n for w in trees):
        raise E.Refused(f'#{n} already has an agent or worktree; recover, do not dispatch')
    # Recheck dependencies immediately before launch, not from a stale recorded frontier.
    from frontier import Tracker
    tracker, number = E.locate(cfg, n)
    if Tracker(tracker).blockers(number)[1]:
        raise E.Refused(f'#{n} has open blockers')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug):
        raise E.Refused('slug must be lowercase letters, numbers and hyphens')
    settings = cfg['machines'][machine]
    profile_name = settings['profile']
    profile = cfg['agents']['profiles'][profile_name]
    if profile.get('exhausted_until'):
        raise E.Refused(f'{profile_name} is exhausted; hand over only on an approved profile')
    repo = next((r for r in E.machine_repos(cfg, machine) if r['name'] == repo_name), None)
    if repo is None:
        raise E.Refused(f'unknown repo {repo_name}')
    prompt = P.assemble(cfg, n, ['--profile', profile_name, '--machine', machine])
    name = f"{settings['agent_prefix']}{n}-codex"
    branch = f'build/{n}-{slug}'
    path = f"{settings['home']}/.herdr/worktrees/{repo['name']}/build-{n}-{slug}"
    # Every agent of one ticket shares one space in the app.
    space = f'ticket-{n}'
    E.machine_call(cfg, machine, 'git', 'fetch', 'origin', cwd=repo['path'])
    base = E.machine_call(cfg, machine, 'git', 'rev-parse', f"origin/{repo['base']}", cwd=repo['path']).strip()
    E.machine_call(cfg, machine, 'git', 'worktree', 'add', '-b', branch, path, base, cwd=repo['path'])
    matches = [w for w in E.worktrees(cfg, repo, machine) if w.get('branch') == branch]
    if len(matches) != 1 or matches[0]['path'] != path:
        raise E.Refused('created worktree unreadable; next tick must recover')
    tree = matches[0]
    head = E.machine_call(cfg, machine, 'git', 'rev-parse', 'HEAD', cwd=tree['path']).strip()
    if not base or head != base:
        raise E.Refused('created worktree is not at fetched base; do not start or reset it')
    head_path = cfg['_root'] / 'tickets' / str(n) / 'head.md'
    with head_path.open('a') as f:
        f.write(f'\nMachine: {machine}; agent: {name}; space: {space}; '
                f'worktree: {tree["path"]}; branch: {branch}; base: {base}.\n')
    started = E.parse(E.call(*E.hc('run', '--on', machine, '--bg', '--json', '--name', name,
                                   '--cwd', tree['path'], '--no-parent', '--outlive-parent',
                                   '--label', f'herdr.space={space}', '--',
                                   *agent_argv(profile, settings['skill'])), tries=1))
    if not isinstance(started, dict) or started.get('state') != 'live':
        raise E.Refused(f'{name} did not start: {started}')
    # The first prompt goes in only once the agent is ready.
    if not E.wait_ready(E.address(machine, name), max(1, profile.get('timeout_ms', 180000) // 1000)):
        raise E.Refused(f'{name} is not ready for its prompt; read it, do not resend')
    send_file(cfg, machine, name, prompt)
    return read_tail(cfg, machine, name)


def main():
    cfg = E.load(sys.argv[1])
    n = E.parse_key(cfg, sys.argv[2])
    try:
        if sys.argv[3] in ('--read', '--send'):
            name = sys.argv[4]
            listed, _, errors = ticket_inventory(cfg, n)
            owners = [a for a in listed if a.get('name') == name and n in E.agent_tickets(cfg, a)]
            if len(owners) != 1 or not owners[0]['machine']:
                raise E.Refused('no unique remote owner; read frontier before recovery')
            machine = owners[0]['machine']
            if machine in errors:
                raise E.Refused(f'{machine} ownership unreadable')
            if sys.argv[3] == '--send':
                if n in cfg.get('holds', []):
                    raise E.Refused(f'#{n} is held')
                send_file(cfg, machine, name, sys.argv[5])
            print(json.dumps(read_tail(cfg, machine, name)))
        else:
            print(json.dumps(launch(cfg, n, sys.argv[3], sys.argv[4])))
    except E.Refused as e:
        sys.exit(f'!! {e}')


if __name__ == '__main__':
    main()
