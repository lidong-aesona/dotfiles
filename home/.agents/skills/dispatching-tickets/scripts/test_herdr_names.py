"""Offline regressions for naming a ticket's space and tabs. No test may reach ssh, gh or hc."""
import json
from unittest.mock import patch

import pytest
import estate as E
import herdr_names as H


@pytest.fixture(autouse=True)
def no_external_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(f'unfaked external process: {args}')
    monkeypatch.setattr(E.subprocess, 'run', forbidden)


@pytest.fixture
def cfg(tmp_path):
    return dict(_root=tmp_path, tracker='a/b', agent_prefix='w1-ticket-', herdr_app='mac',
                trackers=[dict(slug='a/r', prefix='r')],
                machines={'mac': {'agent_prefix': 'mac-'}})


def space(sid, *tabs):
    return dict(id=sid, tabs=[dict(id=tid, terminals=[dict(terminal=t) for t in terms]) for tid, terms in tabs])


VIEW = json.dumps({'spaces': [
    space('sp1', ('tb0', ['t3'])),
    space('agent-vm-w1/ticket-r322', ('agent-vm-w1/tb1', ['agent-vm-w1/t18q']),
          ('agent-vm-w1/tb2', ['agent-vm-w1/t19'])),
    space('ticket-7', ('tb3', ['t18q'])),
]})


def calls(address, name, title='Recipe dispatches on Google Cloud'):
    def call(*args, **kwargs):
        if args[:3] == ('gh', 'issue', 'view'):
            return json.dumps({'title': title})
        if args[:2] == ('hc', 'get'):
            return json.dumps({'address': address, 'name': name})
        raise AssertionError(f'unexpected call {args}')
    return call


def run(cfg, key, agent, address, name, view=VIEW, **kwargs):
    with patch.object(E, 'call', side_effect=calls(address, name, **kwargs)), \
         patch.object(E, 'machine_read', return_value=view) as reads, \
         patch.object(E, 'machine_call', return_value='{"ok": true}') as renames, \
         patch.object(H.time, 'sleep'):
        names = H.name(cfg, key, agent)
    return names, reads, [c.args for c in renames.call_args_list]


def test_names_a_remote_authors_space_and_tab_through_the_app(cfg):
    names, _, renames = run(cfg, 'r322', 'w1-ticket-r322c', 't18q', 'w1-ticket-r322c')
    assert renames == [
        (cfg, 'mac', 'herdr-app', 'do', 'space.rename', '--id', 'agent-vm-w1/ticket-r322',
         '--name', 'r322 Recipe dispatches on Google Cloud'),
        (cfg, 'mac', 'herdr-app', 'do', 'tab.rename', '--id', 'agent-vm-w1/tb1',
         '--name', 'author w1-ticket-r322c')]
    assert names['agent-vm-w1/tb1'] == 'author w1-ticket-r322c'


def test_a_reviewer_tab_says_reviewer(cfg):
    _, _, renames = run(cfg, 'r322', 'w1-ticket-r322rc', 't19', 'w1-ticket-r322rc')
    assert renames[1][-1] == 'reviewer w1-ticket-r322rc'


def test_a_terminal_on_the_apps_own_machine_matches_its_bare_address(cfg):
    # `mac/t18q` is `t18q` to the Mac's app; the VM's t18q in ticket-r322 must not match.
    _, _, renames = run(cfg, 7, 'mac/mac-7-codex', 'mac/t18q', 'mac-7-codex', title='Mac work')
    assert [r[6] for r in renames] == ['ticket-7', 'tb3']
    assert renames[0][-1] == '7 Mac work' and renames[1][-1] == 'author mac-7-codex'


def test_a_long_title_is_cut(cfg):
    _, _, renames = run(cfg, 'r322', 'w1-ticket-r322c', 't18q', 'w1-ticket-r322c', title='x' * 200)
    assert len(renames[0][-1]) == H.MAX_NAME


def test_without_an_app_nothing_is_read_or_renamed(cfg):
    del cfg['herdr_app']
    with patch.object(E, 'call') as call, patch.object(E, 'machine_call') as renames:
        assert H.name(cfg, 'r322', 'w1-ticket-r322c') is None
    call.assert_not_called()
    renames.assert_not_called()


def test_waits_for_the_app_to_show_the_space_then_refuses_without_renaming(cfg):
    empty = json.dumps({'spaces': []})
    with pytest.raises(E.Refused, match='not in a ticket-r322 space'):
        _, reads, renames = run(cfg, 'r322', 'w1-ticket-r322c', 't18q', 'w1-ticket-r322c', view=empty)
    with patch.object(E, 'call', side_effect=calls('t18q', 'w1-ticket-r322c')), \
         patch.object(E, 'machine_read', side_effect=[empty, VIEW]) as reads, \
         patch.object(E, 'machine_call', return_value='{"ok": true}'), patch.object(H.time, 'sleep'):
        assert H.name(cfg, 'r322', 'w1-ticket-r322c')
    assert reads.call_count == 2


def test_a_terminal_in_two_tabs_of_the_space_is_refused(cfg):
    view = json.dumps({'spaces': [space('agent-vm-w1/ticket-r322', ('a', ['agent-vm-w1/t18q']),
                                        ('b', ['agent-vm-w1/t18q']))]})
    with patch.object(E, 'machine_call') as renames, pytest.raises(E.Refused, match='2 tabs'):
        run(cfg, 'r322', 'w1-ticket-r322c', 't18q', 'w1-ticket-r322c', view=view)
    renames.assert_not_called()
