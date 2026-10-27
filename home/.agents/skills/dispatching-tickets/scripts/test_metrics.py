"""Offline metrics.py regressions. No test may reach gh."""
import pytest
import estate as E
import metrics

OWNER = 'owner'
LABEL = 'spec-approved'


@pytest.fixture
def cfg(monkeypatch):
    c = dict(owner_login=OWNER, labels={'spec_approved': LABEL})
    monkeypatch.setattr(E, 'load', lambda root: c)
    return c


def ev(event, at, who=OWNER, **extra):
    return dict(event=event, created_at=at, actor={'login': who}, **extra)


def fake(monkeypatch, replies):
    monkeypatch.setattr(metrics, 'pages', lambda path: replies[path])


def sub(n, state='closed', closed='2026-10-02T12:00:00Z', slug='a/b'):
    return dict(number=n, state=state, closed_at=closed, html_url=f'https://x/{n}',
                repository_url=f'https://api.github.com/repos/{slug}')


def test_counts_owner_acts_after_approval_but_not_the_approval_label(cfg, monkeypatch):
    approve = ev('labeled', '2026-10-01T00:00:00Z', label={'name': LABEL})
    fake(monkeypatch, {
        'repos/a/b/issues/1/timeline': [ev('commented', '2026-09-30T00:00:00Z'), approve,
                                        ev('commented', '2026-10-01T01:00:00Z')],
        'repos/a/b/issues/1/sub_issues': [sub(2), sub(3, slug='a/c')],
        'repos/a/b/issues/2/timeline': [ev('closed', '2026-10-02T12:00:00Z'),
                                        ev('commented', '2026-10-02T00:00:00Z', who='bot')],
        'repos/a/c/issues/3/timeline': [ev('assigned', '2026-10-02T00:00:00Z')],
    })
    assert metrics.row('/e', 'a/b', 1, None) == (
        '| a/b#1 | 2026-10-01T00:00Z (label) | 2026-10-02T12:00Z | 36.0 | 2 | 2 | 1.00 |')


def test_refuses_without_label_event_unless_given(cfg, monkeypatch):
    fake(monkeypatch, {'repos/a/b/issues/1/timeline': [],
                       'repos/a/b/issues/1/sub_issues': [sub(2)],
                       'repos/a/b/issues/2/timeline': []})
    with pytest.raises(E.Refused, match='--approved'):
        metrics.row('/e', 'a/b', 1, None)
    assert '(given)' in metrics.row('/e', 'a/b', 1, '2026-10-01T00:00:00Z')


def test_refuses_open_sub_issue_and_missing_config(cfg, monkeypatch):
    fake(monkeypatch, {'repos/a/b/issues/1/timeline': [],
                       'repos/a/b/issues/1/sub_issues': [sub(2), sub(3, state='open', closed=None)]})
    with pytest.raises(E.Refused, match='still open'):
        metrics.row('/e', 'a/b', 1, '2026-10-01T00:00:00Z')
    cfg['labels'] = {}
    with pytest.raises(E.Refused, match='spec_approved'):
        metrics.row('/e', 'a/b', 1, '2026-10-01T00:00:00Z')


def test_pages_raises_on_unreadable_reply(monkeypatch):
    monkeypatch.setattr(E, 'call', lambda *a, **k: 'not json')
    with pytest.raises(E.Refused):
        metrics.pages('repos/a/b/issues/1/timeline')
