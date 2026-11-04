#!/usr/bin/env python3
"""Name a ticket's space and an agent's tab in the Herdr app.

    herdr_names.py <estate> <key> <agent>

Unnamed, the app calls a space after its folder (`build-r322-recipe-gcp`) and a tab
by its place (`1`). This names the space `<key> <ticket title>` and the agent's tab
`author <agent>` or `reviewer <agent>`. `herdr_app` in estate.toml names the machine
whose app shows this estate ("" is this host); without it this does nothing.

Names are only what the person sees: a failure here is reported, never a reason to stop
a dispatch. Renaming again is harmless, so a recovery reruns it for the new agent.
"""
import json
import sys
import time

import estate as E

MAX_NAME = 72


def role(cfg, machine, name, key):
    prefix = cfg.get('machines', {}).get(machine, {}).get('agent_prefix') if machine else None
    return 'reviewer' if name.startswith(f"{prefix or cfg['agent_prefix']}{key}r") else 'author'


def find(spaces, key, terminal, exact):
    """The (space id, tab id) holding the terminal, in the ticket's space. One or none."""
    found = [(space['id'], tab['id']) for space in spaces
             if space['id'] == f'ticket-{key}' or space['id'].endswith(f'/ticket-{key}')
             for tab in space['tabs'] for t in tab['terminals']
             if (t['terminal'] == terminal if exact else t['terminal'].endswith(f'/{terminal}'))]
    if len(found) > 1:
        raise E.Refused(f'{terminal} shows in {len(found)} tabs of ticket-{key}')
    return found[0] if found else None


def name(cfg, key, agent, tries=6):
    app = cfg.get('herdr_app')
    if app is None:
        return None
    slug, number = E.locate(cfg, key)
    title = E.parse(E.call('gh', 'issue', 'view', str(number), '--repo', slug, '--json', 'title'), 'title')
    got = E.parse(E.call(*E.hc('get', '--json', agent)))
    machine, _, terminal = str(got.get('address', '')).rpartition('/')
    # Seen from the app, a terminal of its own machine has a bare address. Any other
    # carries a prefix that machine's hc chose, so match on the address after it.
    exact = machine == app
    for attempt in range(tries):
        # The app adds the space once it sees the labelled terminal, which can lag the start.
        found = find(E.parse(E.machine_read(cfg, app, 'herdr-app', 'ls'), 'spaces'), key, terminal, exact)
        if found or attempt == tries - 1:
            break
        time.sleep(2)
    if not found:
        raise E.Refused(f'{agent} is not in a ticket-{key} space in the app')
    space, tab = found
    agent = got.get('name') or agent
    names = {space: f'{key} {title}'[:MAX_NAME], tab: f'{role(cfg, machine, agent, key)} {agent}'}
    for what, target in (('space', space), ('tab', tab)):
        E.machine_call(cfg, app, 'herdr-app', 'do', f'{what}.rename', '--id', target, '--name', names[target])
    return names


def main():
    cfg = E.load(sys.argv[1])
    try:
        print(json.dumps(name(cfg, E.parse_key(cfg, sys.argv[2]), sys.argv[3])))
    except E.Refused as e:
        sys.exit(f'!! names not set: {e}')


if __name__ == '__main__':
    main()
