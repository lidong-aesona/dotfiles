"""Offline multi-machine regressions. No test may reach ssh or hc."""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import estate as E
import frontier
import cleanup
import dispatch as D


@pytest.fixture(autouse=True)
def no_external_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(f'unfaked external process: {args}')
    monkeypatch.setattr(E.subprocess, 'run', forbidden)


def agent(name, status='done', machine='', terminal='t1', **extra):
    """An agent as `E.agents` lists it."""
    return dict(name=name, agent_status=status, machine=machine, terminal=terminal,
                address=E.address(machine, terminal), cwd='/home', **extra)


def hc_ls(*terminals, mac='connected'):
    return json.dumps({'machines': [{'name': 'mac', 'state': mac}], 'terminals': list(terminals)})


@pytest.fixture
def cfg(tmp_path):
    return dict(_root=tmp_path, tracker='a/b', agent_prefix='w1-ticket-', holds=[],
                labels={'dispatch': 'fleet:w1', 'surface': 'ready-for-human'},
                watcher={'machines': ['', 'mac']},
                machines={'mac': {'ssh': 'herdr-mac', 'home': '/Users/test',
                                  'agent_prefix': 'mac-', 'profile': 'sol',
                                  'gate_lock': 'shared Mac gate lock',
                                  'dispatch_requires': 'ready-for-agent',
                                  'repos': {'app': '/Users/test/app'}}},
                machine_labels={'fleet:mac': 'mac', 'fleet:w1': ''},
                routing={'types': {'implementation': {}}},
                repos=[dict(name='app', path='/vm/app', slug='a/b', base='main')],
                agents={'active': 'sol', 'profiles': {'sol': {
                    'kind': 'pi', 'suffix': 'o', 'args': ['--model', 'gpt-6.1-sol'],
                    'crashed': ['Error: fetch failed'], 'exhausted': ['quota']}}})


def snapshot(cfg, agents, trees, items=None, record=False):
    tracker = Mock(slug='a/b', open=items or {7: dict(pr=False, title='Mac work', labels=['fleet:mac', 'ready-for-agent'])})
    tracker.blockers.return_value = ([], [])
    out = io.StringIO()
    with patch.object(E, 'load', return_value=cfg), patch.object(frontier, 'Tracker', return_value=tracker), \
         patch.object(E, 'agents', side_effect=agents), patch.object(E, 'worktrees', side_effect=trees), \
         patch('sys.argv', ['frontier.py', str(cfg['_root'])] + (['--record'] if record else [])), contextlib.redirect_stdout(out):
        try:
            frontier.main()
        except SystemExit:
            pass
    return json.loads(out.getvalue())


@pytest.mark.parametrize('eligibility,expected', [([], 'hold'), (['needs-triage'], 'hold'),
    (['ready-for-human'], 'surface'), (['ready-for-agent'], 'dispatch'),
    (['ready-for-agent', 'ready-for-human'], 'surface')])
def test_mac_requires_configured_label_and_human_always_surfaces(cfg, eligibility, expected):
    for trees in ([], ['/tree']):
        wanted = 'orphaned' if expected == 'dispatch' and trees else expected
        assert frontier.action(cfg, ['fleet:mac', *eligibility], [], trees, 1323) == wanted


def test_vm_action_is_identical_to_original_policy(cfg):
    import itertools
    def original(labels, mine, trees, number):
        if number in cfg.get('holds', []):
            return 'hold'
        if mine:
            return 'leave' if any(a.get('agent_status') == 'working' for a in mine) else 'read'
        dispatch, surface = cfg['labels']['dispatch'], cfg['labels']['surface']
        if dispatch in labels and surface in labels:
            return 'hold'
        if surface in labels:
            return 'surface'
        if dispatch in labels:
            return 'orphaned' if trees else 'dispatch'
        return 'hold'
    cfg['holds'] = [8]
    labels = ['fleet:w1', 'ready-for-agent', 'ready-for-human', 'needs-triage']
    for bits in itertools.product([False, True], repeat=len(labels)):
        selected = [label for label, include in zip(labels, bits) if include]
        for agents in ([], [{'agent_status': 'working'}], [{'agent_status': 'idle'}]):
            for trees in ([], ['/tree']):
                for n in (7, 8):
                    assert frontier.action(cfg, selected, agents, trees, n) == original(selected, agents, trees, n)
    assert frontier.action(cfg, ['ready-for-agent'], [], [], 7) == 'hold'
    assert frontier.action(cfg, ['fleet:w1'], [], [], 7) == 'dispatch'


def test_vm_dispatch_requires_gates_fresh_dispatch_only(cfg):
    cfg['labels']['dispatch_requires'] = 'ready-for-agent'
    assert frontier.action(cfg, ['fleet:w1'], [], [], 7) == 'hold'
    assert frontier.action(cfg, ['fleet:w1', 'ready-for-agent'], [], [], 7) == 'dispatch'
    assert frontier.action(cfg, ['fleet:w1'], [], ['/wt/7'], 7) == 'orphaned'
    assert frontier.action(cfg, ['fleet:w1'], [{'name': 'w1-ticket-7c', 'agent_status': 'idle'}], [], 7) == 'read'
    assert frontier.action(cfg, ['fleet:w1', 'ready-for-human'], [], [], 7) == 'hold'


def test_dual_fleet_conflict_holds_without_refusing_whole_tick(cfg):
    for agents in ([], [{'agent_status': 'working'}]):
        assert frontier.action(cfg, ['fleet:w1', 'fleet:mac', 'ready-for-agent'], agents, [], 7) == 'hold'
    result = snapshot(cfg, [[], []], [[], []], {7: dict(pr=False, title='conflict', labels=['fleet:w1', 'fleet:mac'])})
    assert result['frontier'][0]['action'] == 'hold'
    assert not result['machine_errors']


def test_mac_required_label_is_configured_not_hardcoded(cfg):
    cfg['machines']['mac']['dispatch_requires'] = 'mac-ready'
    assert frontier.action(cfg, ['fleet:mac', 'ready-for-agent'], [], [], 7) == 'hold'
    assert frontier.action(cfg, ['fleet:mac', 'mac-ready'], [], [], 7) == 'dispatch'
    with patch.object(D, 'ticket_labels', return_value=['fleet:mac', 'mac-ready']):
        assert D.target(cfg, 7) == 'mac'


def test_estate_snapshot_preserves_vm_dispatch_and_original_owner_comment():
    import tomllib
    path = Path(__file__).resolve().parents[2] / 'estate.toml'
    if not path.exists():
        pytest.skip('runs only beside a live estate.toml snapshot')
    text = path.read_text()
    estate = tomllib.loads(text)
    assert estate['labels']['dispatch'] == 'fleet:w1'
    assert estate['machines']['mac']['dispatch_requires'] == 'ready-for-agent'
    assert '# 2026-10-01 owner expanded dispatch: needs-triage AND ready-for-human tickets are eligible.\n' in text
    assert '# The tick requires ready-for-agent for fleet:mac; human-labelled Mac work is dispatched by hand.' in text
    assert 'Historical 2026-10-01 expansion is superseded' not in text


def test_missing_mac_required_label_configuration_holds(cfg):
    del cfg['machines']['mac']['dispatch_requires']
    assert frontier.action(cfg, ['fleet:mac', 'ready-for-agent'], [], [], 7) == 'hold'
    with patch.object(D, 'ticket_labels', return_value=['fleet:mac', 'ready-for-agent']), pytest.raises(E.Refused):
        D.target(cfg, 7)


def test_sleeping_mac_quarantines_history_but_vm_dispatch_proceeds(cfg):
    previous = dict(frontier=[dict(number=8, machine='', agents={'mac-8-codex': 'idle'},
        agent_machines={'mac-8-codex': 'mac'}, worktrees=[], worktree_machines={}),
        dict(number=9, machine='', agents={}, agent_machines={}, worktrees=['/mac/9-work'],
             worktree_machines={'/mac/9-work': 'mac'})], blocked=[], unreadable=[],
                    agents={'mac:mac-11-codex': 'done'})
    (cfg['_root'] / 'last-tick.json').write_text(json.dumps(previous))
    items = {n: dict(pr=False, title=str(n), labels=['fleet:w1', 'ready-for-agent']) for n in (8, 9, 10, 11)}
    items[7] = dict(pr=False, title='Mac', labels=['fleet:mac', 'ready-for-agent'])
    for _ in range(2):  # recording a failed read must not erase prior remote ownership
        result = snapshot(cfg, [[], E.Refused('sleeping')], [[]], items, record=True)
        actions = {row['number']: row['action'] for row in result['frontier']}
        assert actions == {7: 'unknown', 8: 'unknown', 9: 'unknown', 10: 'dispatch', 11: 'unknown'}
        assert 'mac' in result['machine_errors']
    result = snapshot(cfg, [[], []], [[], []], items, record=True)
    assert all(row['action'] == 'dispatch' for row in result['frontier'])


def test_sleeping_mac_does_not_stop_vm_retirement_or_erase_closed_owner_guard(cfg, capsys):
    baseline = dict(frontier=[], blocked=[], unreadable=[], agents={},
                    unknown_ownership={'mac': [8]})
    (cfg['_root'] / 'last-tick.json').write_text(json.dumps(baseline))
    local_tree = dict(tree(cfg, ''), branch='build/10-work', path='/vm/10-work')
    guarded_tree = dict(tree(cfg, ''), branch='build/8-work', path='/vm/8-work')
    local = agent('w1-ticket-10o')
    guarded_agent = agent('w1-ticket-8o', terminal='t8')
    with patch.object(E, 'agents', side_effect=[[local, guarded_agent], E.Refused('Mac sleeping'), [local]]), \
         patch.object(E, 'worktrees', return_value=[local_tree, guarded_tree]), \
         patch.object(cleanup, 'retirable', return_value='merged'), patch.object(cleanup, 'APPLY', True), \
         patch.object(cleanup, 'archive_tail', return_value=Path('/report')), \
         patch.object(E, 'run', return_value=Mock(returncode=0)) as run:
        closed, kept = cleanup.retire_agents(cfg, Mock(closed=Mock(return_value=True)))
        assert len(closed) == len(kept) == 1
        assert [c.args for c in run.call_args_list] == [('hc', 'kill', 't1'), ('hc', 'rm', 't1')]
    with patch.object(E, 'agents', side_effect=[[], E.Refused('Mac sleeping')]), \
         patch.object(E, 'worktrees', return_value=[local_tree, guarded_tree]), patch.object(E, 'terminals', return_value=[]), \
         patch.object(cleanup, 'retirable', return_value='merged'), patch.object(cleanup, 'APPLY', True), \
         patch.object(E, 'run', return_value=Mock(returncode=0)) as run:
        removed, kept = cleanup.remove_worktrees(cfg, Mock(closed=Mock(return_value=True)))
        assert len(removed) == len(kept) == 1
        assert run.call_count == 1 and run.call_args.args == ('git', 'worktree', 'remove', '/vm/10-work')
    assert 'Mac sleeping' in capsys.readouterr().out


@pytest.mark.parametrize('labels', [['fleet:mac'], ['fleet:mac', 'needs-triage'],
                                  ['fleet:mac', 'ready-for-human'],
                                  ['fleet:mac', 'ready-for-agent', 'ready-for-human']])
def test_direct_mac_launch_also_requires_agent_eligibility(cfg, labels):
    with patch.object(D, 'ticket_labels', return_value=labels), pytest.raises(E.Refused, match='eligible'):
        D.target(cfg, 1323)


def test_readable_baseline_records_closed_ticket_owners_before_later_outage(cfg):
    items = {10: dict(pr=False, title='VM work', labels=['fleet:w1', 'ready-for-agent'])}
    agents = [[], [{'name': 'w1-ticket-8o', 'agent_status': 'done', 'cwd': '/mac/8-work'},
                   {'name': 'w2:p1', 'agent_status': 'done', 'cwd': '/mac/11-work'}]]
    trees = [[], [{'path': '/mac/9-work', 'branch': 'build/9-work'}]]
    snapshot(cfg, agents, trees, items, record=True)  # #8/#9 are closed, absent from tracker.open
    items.update({n: dict(pr=False, title=str(n), labels=['fleet:w1', 'ready-for-agent']) for n in (8, 9, 11)})
    result = snapshot(cfg, [[], E.Refused('sleeping')], [[]], items, record=True)
    assert {r['number']: r['action'] for r in result['frontier']} == {8: 'unknown', 9: 'unknown', 10: 'dispatch', 11: 'unknown'}


def test_explicit_machine_qualified_baseline_accepts_other_estate_prefix(cfg):
    previous = {'agents': {'mac:w1-ticket-8o': 'done'}}
    assert E.unknown_ownership(cfg, {'mac': 'sleeping'}, [], [], previous) == {'mac': [8]}


def test_sleeping_mac_quarantines_mac_routed_local_leftovers(cfg):
    local = dict(tree(cfg, ''), branch='build/7-work')
    tickets = Mock(closed=Mock(return_value=True), machine=Mock(return_value='mac'))
    with patch.object(E, 'agents', side_effect=[[agent('w1-ticket-7o')], E.Refused('sleeping')]), \
         patch.object(E, 'worktrees', return_value=[local]), patch.object(cleanup, 'APPLY', True), \
         patch.object(cleanup, 'retirable', return_value='merged'), patch.object(cleanup, 'archive_tail') as archive:
        assert cleanup.retire_agents(cfg, tickets)[0] == []
        archive.assert_not_called()
    with patch.object(E, 'agents', side_effect=[[], E.Refused('sleeping')]), \
         patch.object(E, 'worktrees', return_value=[local]), patch.object(cleanup, 'APPLY', True), \
         patch.object(E, 'terminals', return_value=[]), patch.object(cleanup, 'retirable', return_value='merged'):
        assert cleanup.remove_worktrees(cfg, tickets)[0] == []


def test_remote_git_timeout_after_inventory_keeps_vm_retirement_running(cfg):
    remote_tree = tree(cfg)
    local_tree = dict(tree(cfg, ''), branch='build/10-work', path='/vm/10-work')
    tickets = Mock(closed=Mock(return_value=True), machine=Mock(return_value=''))
    def run(machine_cfg, machine, *args, **kwargs):
        if machine:
            raise E.Refused('Mac probe timed out')
        return Mock(returncode=0, stdout='')
    with patch.object(E, 'inventory', return_value=([], [remote_tree, local_tree])), \
         patch.object(cleanup, 'APPLY', True), patch.object(E, 'machine_run', side_effect=run), \
         patch.object(cleanup, 'landed', return_value='merged'), patch.object(E, 'terminals', return_value=[]):
        removed, kept = cleanup.remove_worktrees(cfg, tickets)
    assert len(removed) == len(kept) == 1
    assert removed[0][0].startswith('local:')


def test_cleanup_reads_and_caches_closed_ticket_machine_labels(cfg):
    issue = {'state': 'CLOSED', 'labels': [{'name': 'fleet:mac'}]}
    with patch.object(E, 'call', return_value=json.dumps(issue)) as call:
        tickets = cleanup.Tickets('a/b')
        assert tickets.closed(7) and tickets.machine(7, cfg) == 'mac'
        assert call.call_count == 1
        assert 'state,labels' in call.call_args.args
    for raw in ('garbage', '{"state":"CLOSED"}', '{"state":"CLOSED","labels":[{}]}'):
        with patch.object(E, 'call', return_value=raw):
            assert not cleanup.Tickets('a/b').closed(7)


@pytest.mark.parametrize('phase', ['archive', 'recheck', 'close'])
def test_late_mac_terminal_failure_does_not_stop_vm_retirement(cfg, phase):
    remote = agent('mac-7-codex', machine='mac')
    local = agent('w1-ticket-10o', terminal='t2')
    local_tree = dict(tree(cfg, ''), branch='build/10-work', path='/vm/10-work')
    tickets = Mock(closed=Mock(return_value=True), machine=Mock(side_effect=lambda n, c: 'mac' if n == 7 else ''))
    archive = [E.Refused('Mac tail timed out'), Path('/report')] if phase == 'archive' else [Path('/report')] * 2
    recheck = ([local],) if phase == 'archive' else ([E.Refused('Mac status timed out'), [local]] if phase == 'recheck' else [[remote], [local]])
    closes = ([E.Refused('Mac kill timed out')] if phase == 'close' else []) + [Mock(returncode=0)] * 2
    with patch.object(E, 'inventory', return_value=([remote, local], [tree(cfg), local_tree])), \
         patch.object(cleanup, 'APPLY', True), patch.object(cleanup, 'retirable', return_value='merged'), \
         patch.object(cleanup, 'archive_tail', side_effect=archive), patch.object(E, 'agents', side_effect=recheck), \
         patch.object(E, 'run', side_effect=closes):
        closed, kept = cleanup.retire_agents(cfg, tickets)
    assert len(closed) == len(kept) == 1
    assert closed[0][0] == 'w1-ticket-10o'


def test_late_mac_failure_quarantines_historical_local_tree_while_other_vm_tree_retires(cfg):
    (cfg['_root'] / 'last-tick.json').write_text(json.dumps({'ownership': {'mac': [8]}}))
    guarded = dict(tree(cfg, ''), branch='build/8-work', path='/vm/8-work')
    independent = dict(tree(cfg, ''), branch='build/10-work', path='/vm/10-work')
    def run(machine_cfg, machine, *args, **kwargs):
        if machine:
            raise E.Refused('Mac timed out after inventory')
        return Mock(returncode=0, stdout='')
    with patch.object(E, 'inventory', return_value=([], [tree(cfg), guarded, independent])), \
         patch.object(cleanup, 'APPLY', True), patch.object(E, 'machine_run', side_effect=run), \
         patch.object(cleanup, 'landed', return_value='merged'), patch.object(E, 'terminals', return_value=[]):
        removed, kept = cleanup.remove_worktrees(cfg, Mock(closed=Mock(return_value=True), machine=Mock(return_value='')))
    assert len(removed) == 1 and len(kept) == 2
    assert removed[0][0].endswith('/10-work')


@pytest.mark.parametrize('phase', ['revision', 'ancestry'])
def test_late_remote_branch_probe_failure_is_scoped(cfg, phase):
    good = Mock(returncode=0, stdout='', stderr='')
    revision = Mock(returncode=0, stdout='sha', stderr='')
    failed = Mock(returncode=255, stdout='', stderr='SSH disconnected')
    results = [good, good, failed] if phase == 'revision' else [good, good, revision, failed]
    errors = {}
    with patch.object(E, 'machine_run', side_effect=results), \
         patch.object(E, 'run', return_value=Mock(returncode=0, stdout='')):
        assert cleanup.retirable(cfg, tree(cfg), errors) is None
    assert 'mac' in errors


def test_remote_agent_prevents_duplicate_when_vm_empty(cfg):
    result = snapshot(cfg, [[], [{'name': 'mac-7-codex', 'agent_status': 'working'}]], [[], []])
    assert result['frontier'][0]['action'] == 'leave'
    assert result['frontier'][0]['machine'] == 'mac'


def test_remote_tree_is_orphan_not_dispatch(cfg):
    result = snapshot(cfg, [[], []], [[], [{'path': '/mac/tree', 'branch': 'build/7-work'}]])
    assert result['frontier'][0]['action'] == 'orphaned'


def test_remote_failure_never_means_empty(cfg):
    result = snapshot(cfg, [[], E.Refused('mac unreachable')], [[], []])
    assert result['frontier'][0]['action'] == 'unknown'
    assert 'mac' in result['machine_errors']


def test_label_routes_from_config_and_conflict_refuses(cfg):
    assert E.machine_for(cfg, ['fleet:mac']) == 'mac'
    assert E.machine_for(cfg, ['fleet:w1']) == ''
    with pytest.raises(E.Refused):
        E.machine_for(cfg, ['fleet:w1', 'fleet:mac'])


def test_malformed_remote_lists_refuse(cfg):
    good = {'address': 'mac/t1', 'state': 'live', 'cwd': '/mac/x', 'status': 'idle'}
    for terminals in (None, {}, 'empty', [None], [{'address': 'mac/t1'}],
                      [dict(good, address='')], [dict(good, address=None)],
                      [dict(good, state=None)], [dict(good, cwd='relative')], [dict(good, cwd=None)]):
        raw = json.dumps({'machines': [{'name': 'mac', 'state': 'connected'}], 'terminals': terminals})
        with patch.object(E, 'call', return_value=raw):
            with pytest.raises(E.Refused):
                E.agents('mac')
    for raw in ('not json', json.dumps({'terminals': []}), hc_ls(good, mac='disconnected'),
                json.dumps({'machines': [], 'terminals': [good]})):
        with patch.object(E, 'call', return_value=raw), pytest.raises(E.Refused):
            E.agents('mac')


def test_agents_are_named_or_reporting_terminals_on_one_machine(cfg):
    raw = hc_ls({'address': 't1', 'state': 'live', 'cwd': '/vm/build-5-x', 'status': 'working'},
                {'address': 't2', 'state': 'live', 'cwd': '/home'},                       # a plain shell
                {'address': 't3', 'state': 'finished', 'cwd': '/vm/x', 'name': 'w1-ticket-6o'},
                {'address': 't4', 'state': 'finished', 'cwd': '/vm/build-9-x'},           # unnamed and gone
                {'address': 'mac/t1', 'state': 'live', 'cwd': '/mac/x', 'name': 'mac-7-codex', 'status': 'idle'})
    with patch.object(E, 'call', return_value=raw):
        local, remote = E.agents(''), E.agents('mac')
        shells = E.terminals('')
    assert [(a['name'], a['agent_status'], a['address']) for a in local] == [('t1', 'working', 't1'), ('w1-ticket-6o', 'exited', 't3')]
    assert E.agent_tickets(cfg, local[0]) == {5}
    assert [(a['name'], a['terminal'], a['address']) for a in remote] == [('mac-7-codex', 't1', 'mac/t1')]
    assert [t['address'] for t in shells] == ['t1', 't2']


def test_stop_mark_is_status_time_and_refuses_when_unreadable():
    live = {'address': 'mac/t1', 'agent_status': 'idle'}
    with patch.object(E, 'call', return_value=json.dumps({'status_ms': 17, 'turn': 0})) as call:
        assert E.stop_mark(live) == 17
    assert call.call_args.args == ('hc', 'get', '--json', 'mac/t1')
    with patch.object(E, 'call') as call:
        assert E.stop_mark(dict(live, agent_status='exited')) is None
    call.assert_not_called()
    for raw in (json.dumps({'turn': 0}), json.dumps({'status_ms': None})):
        with patch.object(E, 'call', return_value=raw), pytest.raises(E.Refused):
            E.stop_mark(live)


def test_ignored_matches_configured_names_and_labelled_terminals():
    with patch.object(E, 'call', return_value=json.dumps({'terminals': [{'address': 't5'}]})) as call:
        skip = E.ignored({'ignore': ['w1-operator']})
    assert call.call_args.args == ('hc', 'ls', '--json', '--all', '--label', 'dispatch.ignore=1')
    assert skip(agent('w1-operator', terminal='t9')) and skip(agent('t5', terminal='t5'))
    assert not skip(agent('t6', terminal='t6')) and not skip(agent('t5', machine='mac', terminal='t5'))
    with patch.object(E, 'call', return_value='{"terminals": null}'), pytest.raises(E.Refused):
        E.ignored({})


def test_unnamed_agent_is_addressed_by_terminal_and_tracks_numbered_cwd(cfg):
    raw = hc_ls({'address': 'mac/t3', 'cwd': '/mac/app-7', 'state': 'live', 'status': 'working'})
    with patch.object(E, 'call', return_value=raw):
        listed = E.agents('mac')
    assert listed[0]['name'] == 't3' and listed[0]['address'] == 'mac/t3'
    assert E.ticket_agents(cfg, listed, 7) == listed
    assert E.agent_tickets(cfg, dict(name='mac-6-codex', cwd='/mac/app-7')) == {6, 7}


@pytest.mark.parametrize('path', ['/mac/build-7-work', '/mac/app-7', '/mac/7-work'])
def test_detached_worktree_directory_carries_ticket(cfg, path):
    assert E.ticket_of_worktree(cfg, None, path) == 7


@pytest.fixture
def research(cfg):
    return dict(cfg, trackers=[dict(slug='a/research', prefix='r')])


def test_prefixed_keys_never_read_as_main_tracker_numbers(cfg, research):
    assert E.ticket_of_worktree(research, 'build/r235-skills', '/w/build-r235-skills') == 'r235'
    assert E.ticket_of_worktree(research, None, '/w/build-r235-skills') == 'r235'
    assert E.ticket_of_agent(research, 'w1-ticket-r235o') == 'r235'
    assert E.ticket_of_agent(research, 'w1-ticket-235o') == 235
    # Without the tracker configured, a prefixed name carries no ticket rather than 235.
    assert E.ticket_of_worktree(cfg, 'build/r235-skills', '/w/build-r235-skills') is None
    assert E.ticket_of_agent(cfg, 'w1-ticket-r235o') is None
    assert E.ticket_of_worktree(research, 'build/v2-thing', '/w/build-v2-thing') is None
    assert E.ticket_of_worktree(research, None, '/w/r899-af181e83') is None
    assert E.ticket_of_worktree(research, 'codex/r12-x', '/w/r12-x') is None
    assert E.ticket_of_worktree(research, 'codex/1414-x', '/w/x') == 1414
    assert E.locate(research, 'r235') == ('a/research', 235)
    assert E.parse_key(research, '#1254') == 1254
    with pytest.raises(E.Refused):
        E.locate(research, 'x235')
    assert sorted(['r12', 1300, 'r3', 7], key=E.order) == [7, 1300, 'r3', 'r12']


@pytest.mark.parametrize('trackers', [[dict(slug='a/b', prefix='r')], [dict(slug='a/r', prefix='')],
                                      [dict(slug='a/r', prefix='r'), dict(slug='a/s', prefix='r')]])
def test_load_refuses_ambiguous_trackers(cfg, trackers):
    root = cfg['_root']
    (root / 'estate.toml').write_text('tracker = "a/b"\n' + ''.join(
        f'[[trackers]]\nslug = "{t["slug"]}"\nprefix = "{t["prefix"]}"\n' for t in trackers))
    with pytest.raises(SystemExit):
        E.load(root)


def test_frontier_lists_research_issues_under_their_keys(research):
    research = dict(research, holds=['r236'])
    main = Mock(slug='a/b', open={235: dict(pr=False, title='old aOS work', labels=['fleet:w1'])})
    other = Mock(slug='a/research', open={
        235: dict(pr=False, title='skills index', labels=['fleet:w1']),
        236: dict(pr=False, title='held', labels=['fleet:w1'])})
    for t in (main, other):
        t.blockers.return_value = ([('a/b', 9)], [])
    out = io.StringIO()
    with patch.object(E, 'load', return_value=research), \
         patch.object(frontier, 'Tracker', side_effect=lambda slug: {'a/b': main, 'a/research': other}[slug]), \
         patch.object(E, 'agents', return_value=[]), patch.object(E, 'worktrees', return_value=[]), \
         patch('sys.argv', ['frontier.py', str(research['_root'])]), contextlib.redirect_stdout(out):
        frontier.main()
    rows = {r['number']: r for r in json.loads(out.getvalue())['frontier']}
    assert list(rows) == [235, 'r235', 'r236']
    assert rows['r235']['action'] == 'dispatch' and rows['r236']['action'] == 'hold'
    assert rows[235]['declared_blockers'] == [9] and rows['r235']['declared_blockers'] == ['a/b#9']
    other.blockers.assert_any_call(235)
    assert main.peers == {'a/research': other}


def test_frontier_refuses_unowned_hold_keys(research):
    for holds in (['x1'], ['235'], [0], [True]):
        out = io.StringIO()
        with patch.object(E, 'load', return_value=dict(research, holds=holds)), \
             patch('sys.argv', ['frontier.py', str(research['_root'])]), contextlib.redirect_stdout(out), \
             pytest.raises(SystemExit):
            frontier.main()
        assert 'holds must list' in out.getvalue()


def test_cleanup_reads_a_prefixed_ticket_on_its_own_tracker(research):
    issue = {'state': 'OPEN', 'labels': []}
    with patch.object(E, 'call', return_value=json.dumps(issue)) as call:
        assert not cleanup.Tickets('a/b', research).closed('r235')
    assert call.call_args.args[3:6] == ('235', '--repo', 'a/research')


def test_remote_commands_explicit_and_shell_quoted(cfg):
    with patch.object(E, 'run', return_value=Mock(returncode=0, stdout='ok')) as run:
        E.machine_call(cfg, 'mac', 'git', 'status', cwd="/a path/it's")
    command = run.call_args.args
    assert command[:3] == ('ssh', '-n', '-o')
    assert 'herdr-mac' in command
    assert 'zsh -lic' in command[-1]
    with patch.object(E, 'call', return_value=hc_ls()) as call:
        assert E.agents('mac') == []
        assert call.call_args.args == ('hc', 'ls', '--json', '--all')


def tree(cfg, machine='mac'):
    return dict(path='/mac/tree', branch='build/7-work', is_detached=False,
                machine=machine, repo=E.machine_repos(cfg, machine)[0])


def test_failed_or_malformed_worktree_read_refuses(cfg):
    repo = E.machine_repos(cfg, 'mac')[0]
    for raw in ('not porcelain', '', 'worktree relative/path\nHEAD abc'):
        with patch.object(E, 'call', return_value=raw), pytest.raises(E.Refused):
            E.worktrees(cfg, repo, 'mac')
    with patch.object(E, 'call', side_effect=E.Refused('ssh failed')), pytest.raises(E.Refused):
        E.worktrees(cfg, repo, 'mac')


def test_worktrees_parse_porcelain_skip_main_and_keep(cfg):
    repo = dict(E.machine_repos(cfg, 'mac')[0], keep=['long-lived'])
    raw = ('worktree /Users/test/app\nHEAD aaa\nbranch refs/heads/main\n\n'
           'worktree /w/build-7-work\nHEAD bbb\nbranch refs/heads/build/7-work\n\n'
           'worktree /w/8-detached\nHEAD ccc\ndetached\n\n'
           'worktree /w/long-lived\nHEAD ddd\nbranch refs/heads/keep\n')
    with patch.object(E, 'call', return_value=raw) as call:
        listed = E.worktrees(cfg, repo, 'mac')
    assert call.call_args.args[:2] == ('ssh', '-n')
    assert "git -C /Users/test/app worktree list --porcelain" in call.call_args.args[-1]
    assert listed == [dict(path='/w/build-7-work', is_detached=False, branch='build/7-work'),
                      dict(path='/w/8-detached', is_detached=True, branch=None)]


def test_vm_label_also_protected_by_remote_owner(cfg):
    assert frontier.action(cfg, ['fleet:w1'], [{'name': 'mac-7-codex', 'agent_status': 'working'}], [], 7) == 'leave'
    result = snapshot(cfg, [[], []], [[], E.Refused('worktrees unreadable')])
    assert result['frontier'][0]['action'] == 'unknown'


def test_file_delivery_pastes_wrapped_text_through_hc_never_ssh(cfg):
    path = cfg['_root'] / 'prompt.md'
    text = "quotes ' \" $HOME\n" * 1000
    path.write_text(text)
    with patch.object(E, 'machine_call') as remote, patch.object(E, 'call', return_value='') as call:
        D.send_file(cfg, 'mac', 'mac-7-codex', path)
    remote.assert_not_called()
    assert call.call_args.args[:3] == ('hc', 'send', 'mac/mac-7-codex')
    assert call.call_args.kwargs['tries'] == 1
    sent = call.call_args.args[3]
    assert sent.endswith(text) and 'Offline only' in sent
    assert path.with_name('prompt.mac-delivery.md').read_text() == sent


def test_oversized_prompt_refuses_before_sending(cfg):
    path = cfg['_root'] / 'long.md'
    path.write_text('x' * (D.MAX_PROMPT + 1))
    with patch.object(E, 'call') as call, pytest.raises(E.Refused, match='over'):
        D.send_file(cfg, 'mac', 'mac-7-codex', path)
    call.assert_not_called()


@pytest.mark.parametrize('tail,state', [('Error: fetch failed', 'crashed'), ('quota', 'exhausted'),
                                      ('Question: continue?', 'stopped'), ('Ready for review: PR #1', 'ready-for-review')])
def test_remote_recovery_tail_classification(cfg, tail, state):
    with patch.object(E, 'call', return_value=tail) as call:
        assert D.read_tail(cfg, 'mac', 'mac-7-codex')['classification'] == state
    assert call.call_args.args == ('hc', 'read', 'mac/mac-7-codex', '--lines', '150')
    with patch.object(E, 'call', return_value=''), pytest.raises(E.Refused):
        D.read_tail(cfg, 'mac', 'mac-7-codex')


def launch_fakes(cfg):
    settings = cfg['machines']['mac']
    settings.update(skill='/Users/test/.claude/skills/implement/SKILL.md', gate_lock='shared Mac gate lock')
    cfg['prompt'] = {'blocks': ['estate.md']}
    ticket = cfg['_root'] / 'tickets/7'
    ticket.mkdir(parents=True)
    (ticket / 'head.md').write_text('Type: implementation\nWork ticket #7.\n')
    (cfg['_root'] / 'blocks').mkdir()
    (cfg['_root'] / 'blocks/estate.md').write_text('Shared estate rules.')
    tracker = Mock()
    tracker.blockers.return_value = ([], [])
    return tracker


def test_handover_assembly_keeps_mac_safety_preamble(cfg):
    launch_fakes(cfg)
    handover = cfg['_root'] / 'tickets/7/handover.md'
    handover.write_text('Measured existing changes. Preserve them.')
    result = D.P.assemble(cfg, 7, ['--profile', 'sol', '--preface', str(handover), '--machine', 'mac'])
    body = result.read_text()
    assert 'Offline only' in body and 'shared Mac gate lock' in body
    assert 'Measured existing changes' in body and 'Shared estate rules.' in body
    assert 'preserve existing work on recovery' in body


MAC_TREE = '/Users/test/.herdr/worktrees/app/build-7-work'


def launch_hc(status='idle'):
    """Fake hc replies for a launch: run starts the agent, get reports its status."""
    def call(*args, **kwargs):
        if args[:2] == ('hc', 'run'):
            return json.dumps({'address': 'mac/t7', 'state': 'live'})
        if args[:2] == ('hc', 'get'):
            return json.dumps({'status': status and {'app': 'pi', 'state': status}})
        raise AssertionError(f'unexpected hc call {args}')
    return call


def test_launch_fetches_verifies_starts_and_delivers_assembled_prompt(cfg):
    tracker = launch_fakes(cfg)
    created = dict(tree(cfg), branch='build/7-work', path=MAC_TREE)
    def remote(*args, **kwargs):
        return 'a' * 40 if 'rev-parse' in args else ''
    with patch.object(D, 'target', return_value='mac'), patch.object(E, 'inventory', return_value=([], [])), \
         patch.object(frontier, 'Tracker', return_value=tracker), patch.object(E, 'worktrees', return_value=[created]), \
         patch.object(E, 'machine_call', side_effect=remote) as commands, patch.object(E, 'call', side_effect=launch_hc()) as calls, \
         patch.object(E, 'run', return_value=Mock(returncode=0)) as waits, \
         patch.object(D, 'send_file') as send, patch.object(D, 'read_tail', return_value={'classification': 'stopped'}):
        D.launch(cfg, 7, 'app', 'work')
    prompt = send.call_args.args[-1].read_text()
    assert 'mac-7-codex' in prompt and 'Offline only' in prompt and 'shared Mac gate lock' in prompt
    assert 'Shared estate rules.' in prompt and 'Work ticket #7.' in prompt
    git = [c.args[2:] for c in commands.call_args_list]
    assert git[0] == ('git', 'fetch', 'origin')
    assert git[2] == ('git', 'worktree', 'add', '-b', 'build/7-work', MAC_TREE, 'a' * 40)
    start = calls.call_args_list[0]
    assert start.kwargs['tries'] == 1
    argv = start.args
    assert argv[:5] == ('hc', 'run', '--on', 'mac', '--bg')
    assert argv[argv.index('--name') + 1] == 'mac-7-codex' and argv[argv.index('--cwd') + 1] == MAC_TREE
    assert argv[argv.index('--label') + 1] == 'herdr.space=ticket-7'
    assert {'--no-parent', '--outlive-parent'} <= set(argv)
    assert argv[argv.index('--') + 1:-1] == ('zsh', '-lic')
    assert argv[-1] == "exec pi --model gpt-6.1-sol --append-system-prompt /Users/test/.claude/skills/implement/SKILL.md"
    assert waits.call_args.args[:3] == ('hc', 'wait', 'mac/mac-7-codex')
    wait = int(waits.call_args.args[-1])
    assert waits.call_args.kwargs['timeout'] > wait  # the subprocess outlives hc's own wait
    assert 'Machine: mac; agent: mac-7-codex; space: ticket-7' in (cfg['_root'] / 'tickets/7/head.md').read_text()


@pytest.mark.parametrize('status', [None, 'working', 'blocked'])
def test_launch_never_sends_to_an_agent_that_is_not_idle(cfg, status):
    tracker = launch_fakes(cfg)
    created = dict(tree(cfg), branch='build/7-work', path=MAC_TREE)
    with patch.object(D, 'target', return_value='mac'), patch.object(E, 'inventory', return_value=([], [])), \
         patch.object(frontier, 'Tracker', return_value=tracker), patch.object(E, 'worktrees', return_value=[created]), \
         patch.object(E, 'machine_call', side_effect=lambda *a, **k: 'a' * 40 if 'rev-parse' in a else ''), \
         patch.object(E, 'call', side_effect=launch_hc(status)), patch.object(E, 'run', return_value=Mock(returncode=0)), \
         patch.object(D, 'send_file') as send, pytest.raises(E.Refused, match='do not resend'):
        D.launch(cfg, 7, 'app', 'work')
    send.assert_not_called()


@pytest.mark.parametrize('owners', [([{'name': 'mac-7-codex'}], []), ([], [{'path': '/mac/tree', 'branch': 'build/7-work'}])])
def test_launch_rechecks_all_machine_ownership(cfg, owners):
    with patch.object(D, 'target', return_value='mac'), patch.object(E, 'inventory', return_value=owners), \
         pytest.raises(E.Refused, match='already has'):
        D.launch(cfg, 7, 'app', 'work')


def test_launch_does_not_retry_or_start_after_create_failure(cfg):
    tracker = launch_fakes(cfg)
    with patch.object(D, 'target', return_value='mac'), patch.object(E, 'inventory', return_value=([], [])), \
         patch.object(frontier, 'Tracker', return_value=tracker), \
         patch.object(E, 'machine_call', side_effect=['', 'a' * 40, E.Refused('lost create reply')]) as remote, \
         patch.object(E, 'call') as call, pytest.raises(E.Refused):
        D.launch(cfg, 7, 'app', 'work')
    assert remote.call_count == 3
    call.assert_not_called()


@pytest.mark.parametrize('state', ['working', 'blocked', 'unknown'])
def test_cleanup_never_closes_live_mac_agent(cfg, state):
    live = agent('mac-7-codex', state, 'mac')
    with patch.object(E, 'inventory', return_value=([live], [tree(cfg)])), patch.object(cleanup, 'APPLY', True), \
         patch.object(E, 'terminals', return_value=[]):
        assert cleanup.retire_agents(cfg, Mock(closed=Mock(return_value=True)))[0] == []
        assert cleanup.remove_worktrees(cfg, Mock(closed=Mock(return_value=True)))[0] == []


@pytest.mark.parametrize('closed,landed', [(False, 'merged'), (True, None)])
def test_cleanup_closed_and_landed_are_both_required(cfg, closed, landed):
    with patch.object(E, 'inventory', return_value=([agent('mac-7-codex', machine='mac')], [tree(cfg)])), \
         patch.object(cleanup, 'retirable', return_value=landed), patch.object(cleanup, 'APPLY', True):
        assert cleanup.retire_agents(cfg, Mock(closed=Mock(return_value=closed)))[0] == []


@pytest.mark.parametrize('closed_second,landed_second', [(False, True), (True, False)])
def test_cleanup_preserves_agent_owning_another_open_or_unlanded_ticket(cfg, closed_second, landed_second):
    owner = dict(agent('mac-6-codex', machine='mac'), cwd='/mac/app-7')
    first = dict(tree(cfg), branch='build/6-work', path='/mac/6-work')
    tickets = Mock(closed=Mock(side_effect=lambda n: n == 6 or closed_second))
    def landed(tree_cfg, worktree, errors=None):
        return 'merged' if worktree['branch'] == 'build/6-work' or landed_second else None
    with patch.object(E, 'inventory', return_value=([owner], [first, tree(cfg)])), \
         patch.object(cleanup, 'retirable', side_effect=landed), patch.object(cleanup, 'APPLY', True), \
         patch.object(cleanup, 'archive_tail') as archive:
        assert cleanup.retire_agents(cfg, tickets)[0] == []
        archive.assert_not_called()


def test_cleanup_multi_ticket_archive_covers_every_owned_ticket(cfg):
    owner = dict(agent('mac-6-codex', machine='mac'), cwd='/mac/app-7')
    first = dict(tree(cfg), branch='build/6-work', path='/mac/6-work')
    tickets = Mock(closed=Mock(return_value=True))
    with patch.object(E, 'inventory', return_value=([owner], [first, tree(cfg)])), \
         patch.object(cleanup, 'retirable', return_value='merged'), patch.object(cleanup, 'APPLY', True), \
         patch.object(cleanup, 'archive_tail', return_value=Path('/saved')) as archive, \
         patch.object(E, 'agents', return_value=[owner]), patch.object(E, 'run', return_value=Mock(returncode=0)):
        assert len(cleanup.retire_agents(cfg, tickets)[0]) == 1
    assert [call.args[1] for call in archive.call_args_list] == [6, 7]


def test_mac_archive_then_kill_then_rm_then_remove(cfg):
    done = agent('mac-7-codex', machine='mac')
    tickets = Mock(closed=Mock(return_value=True))
    events = []
    def run(*args, **kwargs):
        events.append(args)
        if 'kill' in args:
            assert list((cfg['_root'] / 'tickets/7').glob('report-mac-7-codex.md'))
        return Mock(returncode=0, stdout='stable tail')
    with patch.object(E, 'inventory', side_effect=[([done], [tree(cfg)]), ([], [tree(cfg)])]), \
         patch.object(E, 'agents', return_value=[done]), patch.object(cleanup, 'retirable', return_value='PR merged'), \
         patch.object(E, 'terminals', return_value=[]), \
         patch.object(cleanup, 'APPLY', True), patch.object(cleanup.time, 'sleep'), patch.object(E, 'run', side_effect=run):
        assert len(cleanup.retire_agents(cfg, tickets)[0]) == 1
        assert len(cleanup.remove_worktrees(cfg, tickets)[0]) == 1
    assert events[0] == events[1] == ('hc', 'read', 'mac/t1', '--lines', '40')
    assert events[-3:-1] == [('hc', 'kill', 'mac/t1'), ('hc', 'rm', 'mac/t1')]
    assert events[-1][:2] == ('ssh', '-n') and 'git worktree remove /mac/tree' in events[-1][-1]


def test_exited_agent_is_removed_without_kill(cfg):
    gone = agent('mac-7-codex', 'exited', 'mac')
    with patch.object(E, 'inventory', return_value=([gone], [tree(cfg)])), patch.object(E, 'agents', return_value=[gone]), \
         patch.object(cleanup, 'retirable', return_value='merged'), patch.object(cleanup, 'APPLY', True), \
         patch.object(cleanup, 'archive_tail', return_value=Path('/saved')), \
         patch.object(E, 'run', return_value=Mock(returncode=0)) as run:
        assert len(cleanup.retire_agents(cfg, Mock(closed=Mock(return_value=True)))[0]) == 1
    assert [c.args for c in run.call_args_list] == [('hc', 'rm', 'mac/t1')]


@pytest.mark.parametrize('cwd', ['/mac/tree', '/mac/tree/sub'])
def test_live_terminal_inside_worktree_keeps_it(cfg, cwd):
    with patch.object(E, 'inventory', return_value=([], [tree(cfg)])), patch.object(cleanup, 'APPLY', True), \
         patch.object(E, 'terminals', side_effect=lambda m: [{'cwd': cwd}] if m == 'mac' else []), \
         patch.object(cleanup, 'retirable', return_value='merged'):
        removed, kept = cleanup.remove_worktrees(cfg, Mock(closed=Mock(return_value=True)))
    assert removed == [] and kept[0][1] == 'a terminal is open inside it'


@pytest.mark.parametrize('failure', ['fetch', 'dirty', 'status'])
def test_remote_git_checks_fail_closed(cfg, failure):
    def run(*args, **kwargs):
        if 'fetch' in args:
            return Mock(returncode=int(failure == 'fetch'), stdout='')
        return Mock(returncode=int(failure == 'status'), stdout=' M file' if failure == 'dirty' else '')
    with patch.object(E, 'machine_run', side_effect=run), patch.object(cleanup, 'landed') as landed:
        assert cleanup.retirable(cfg, tree(cfg)) is None
        landed.assert_not_called()


def test_unknown_remote_ownership_prevents_any_cleanup(cfg):
    with patch.object(E, 'inventory', side_effect=E.Refused('mac unknown')), pytest.raises(E.Refused):
        cleanup.retire_agents(cfg, Mock())


def test_landed_squash_tip_and_ancestry_on_mac(cfg):
    repo = E.machine_repos(cfg, 'mac')[0]
    with patch.object(E, 'machine_run', return_value=Mock(returncode=0, stdout='sha\n')) as remote, \
         patch.object(E, 'run', return_value=Mock(returncode=0, stdout='12 sha\n')):
        assert cleanup.landed(repo, 'build/7-work', repo['path'], cfg, 'mac') == 'PR #12 merged'
    assert remote.call_args.args[:3] == (cfg, 'mac', 'git')
    with patch.object(E, 'machine_run', return_value=Mock(returncode=0, stdout='new-sha\n')), \
         patch.object(E, 'run', return_value=Mock(returncode=0, stdout='12 old-sha\n')):
        assert 'ancestor' in cleanup.landed(repo, 'build/7-work', repo['path'], cfg, 'mac')


@pytest.mark.parametrize('labels,held', [(['fleet:w1'], False), (['fleet:mac'], True), (['fleet:w1', 'fleet:mac'], False)])
def test_launch_routing_and_holds_are_checked_before_mutation(cfg, labels, held):
    if held:
        cfg['holds'] = [7]
    with patch.object(D, 'ticket_labels', return_value=labels), pytest.raises(E.Refused):
        D.launch(cfg, 7, 'app', 'work')


def test_blocked_ticket_is_not_dispatched(cfg):
    tracker = Mock()
    tracker.blockers.return_value = ([], [('a/b', 8)])
    with patch.object(D, 'target', return_value='mac'), patch.object(E, 'inventory', return_value=([], [])), \
         patch.object(frontier, 'Tracker', return_value=tracker), pytest.raises(E.Refused, match='open blockers'):
        D.launch(cfg, 7, 'app', 'work')


def test_refused_send_is_never_retried(cfg):
    path = cfg['_root'] / 'prompt.md'
    path.write_text('Resume measured work.')
    with patch.object(E, 'run', return_value=Mock(returncode=1, stderr='terminal is blocked')) as run, \
         pytest.raises(E.Refused, match='blocked'):
        D.send_file(cfg, 'mac', 'mac-7-codex', path)
    assert run.call_count == 1


def test_wrong_created_head_never_starts_agent(cfg):
    tracker = launch_fakes(cfg)
    with patch.object(D, 'target', return_value='mac'), patch.object(E, 'inventory', return_value=([], [])), \
         patch.object(frontier, 'Tracker', return_value=tracker), \
         patch.object(E, 'worktrees', return_value=[dict(tree(cfg), path=MAC_TREE)]), \
         patch.object(E, 'machine_call', side_effect=['', 'expected', '', 'wrong']), patch.object(E, 'call') as call, \
         pytest.raises(E.Refused, match='not at fetched base'):
        D.launch(cfg, 7, 'app', 'work')
    call.assert_not_called()


def test_unstable_tail_never_closes(cfg):
    with patch.object(E, 'run', side_effect=[Mock(returncode=0, stdout='one'), Mock(returncode=0, stdout='two')]), \
         patch.object(cleanup.time, 'sleep'):
        assert cleanup.archive_tail(cfg, 7, 'mac-7-codex', 'mac') is None
