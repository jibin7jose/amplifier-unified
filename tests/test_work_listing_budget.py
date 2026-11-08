"""Fit one Work observation without changing its packet or paging contract.

Real SQLite/Work owners and synthetic commissioned transport are used here;
these checks do not claim native-worker or provider acceptance.
"""
import copy
import json
from contextlib import contextmanager

import pytest

from amplifier_web.collaboration import BINDING, PRINCIPAL
from amplifier_web.errors import AppError
from amplifier_web.work_projection import input_identity_bytes
from amplifier_web.workspace_work import CONTEXT_BYTES, EVENT, RECORD
from test_work_invocation import app, commissioned, prepared_work, transport
from test_workspace_work import create, read, workspace_id


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


@contextmanager
def bound(actor):
    principal = PRINCIPAL.set(actor['id'])
    binding = BINDING.set(transport(actor))
    try:
        yield
    finally:
        PRINCIPAL.reset(principal)
        BINDING.reset(binding)


def previous_packet(invocation, sid):
    """Previous repeated-listing algorithm as an observable compatibility oracle."""
    try:
        target = invocation.bound_root(sid)
        required, _ = invocation.required(sid)
        workspace = invocation.work.workspace({'sessionId': sid}, 'agent', sid)
        for limit in range(20, 0, -1):
            discovery = invocation.work.listing(workspace, {'limit': limit},
                                                owner=target['id'], context=True)
            packet = {'available': True, 'discovery': discovery, 'required': required}
            if len(encoded(packet)) - input_identity_bytes(required) <= CONTEXT_BYTES:
                return packet
        invocation.work.error('Work current projection exceeds its byte bound.', 413)
    except AppError as exc:
        return {'available': False, 'discovery': {'status': 'unavailable', 'reason': str(exc)},
                'required': {'status': 'unavailable', 'reason': str(exc), 'version': None,
                             'coverage': {'complete': False}, 'effectAuthorized': False}}


async def discovery_fixture(app):
    owner = app.state['sessions'][0]
    governing = await prepared_work(app, owner, 'governing')
    _, child = await commissioned(app, owner, governing)
    first = (await create(app, owner, command='discovery', title='界🙂' * 100,
                          nextAction='界🙂' * 100, blockedReason='界🙂' * 100))['result']['item']
    template = (await read(app, owner, first['id']))['item']
    # Isolate the indexed read from capture throughput, as existing Work tests do.
    for index in range(24):
        item = {**copy.deepcopy(template), 'id': f'work:page-{index:04d}',
                'ownerSessionId': child['id']}
        app.collaboration.insert(item['id'], 'fixture', {'commandAction': RECORD, 'result': item})
    app.db.commit()
    return owner, child, template


@pytest.mark.parametrize('observed_at', [1.0, 1794095999.1234567])
async def test_current_matches_previous_packet_bytes_and_cursor(app, monkeypatch, observed_at):
    _, child, _ = await discovery_fixture(app)
    # Exact DTO comparison includes timestamp, observation digest and cursor.
    monkeypatch.setattr('amplifier_web.workspace_work.time.time', lambda: observed_at)
    with bound(child):
        before = previous_packet(app.work_invocation, child['id'])
        after = app.work_invocation.current(child['id'])
    assert before['available'] and len(before['discovery']['items']) == 5
    assert encoded(after) == encoded(before)
    page = after['discovery']
    following = app.workspace_work.listing({'id': page['workspaceId']},
        {'after': page['nextAfter']}, owner=child['id'], context=True)
    assert following['items'][0]['id'] > page['items'][-1]['id']
    assert following['total'] == page['total'] == 25
    assert following['snapshotVersion'] == page['snapshotVersion']


async def test_current_projects_one_page_with_retained_and_other_owner_history(app, monkeypatch):
    owner, child, template = await discovery_fixture(app)
    for index in range(128):
        item = {**copy.deepcopy(template), 'id': f'work:other-{index}', 'links': [],
                'ownerSessionId': owner['id']}
        app.collaboration.insert(item['id'], 'fixture', {'commandAction': RECORD, 'result': item})
        app.collaboration.insert(f'history-{index}', 'fixture', {'commandAction': EVENT,
            'workItemId': 'unrelated', 'sequence': index, 'source': {'text': 'z' * 4000}})
    app.db.commit()
    statements, projected = [], []
    summary = app.workspace_work.summary

    def observe(item):
        projected.append(item['id'])
        return summary(item)

    monkeypatch.setattr(app.workspace_work, 'summary', observe)
    app.db.set_trace_callback(statements.append)
    try:
        with bound(child):
            packet = app.work_invocation.current(child['id'])
    finally:
        app.db.set_trace_callback(None)
    assert packet['available'] and packet['discovery']['total'] == 25
    assert len(projected) == 20 and len(set(projected)) == 20
    assert sum(s.startswith('SELECT count(*) FROM commands WHERE ') for s in statements) == 1
    assert sum(s.startswith('SELECT receipt FROM commands WHERE ') and 'ORDER BY id LIMIT' in s
               for s in statements) == 1


async def test_large_complete_identity_arrays_do_not_consume_content_budget(app, monkeypatch):
    _, child, _ = await discovery_fixture(app)
    monkeypatch.setattr('amplifier_web.workspace_work.time.time', lambda: 1794095999.1234567)
    with bound(child):
        header, _ = app.work_invocation.required(child['id'])
        ordinary = app.work_invocation.current(child['id'])
        # Fitting-only fixture: authority/lineage validation remains in required().
        # Preserve a full synthetic identity set and diagnostics, never trim it.
        ids = ['input-' + str(index) + '界' * 100 for index in range(100)]
        header = copy.deepcopy(header)
        header['inputIds'] = ids
        header['associationRequestIds'] = ['request-' + identity for identity in ids]
        header['coverage']['supportedInputIds'] = ids
        monkeypatch.setattr(app.work_invocation, 'required', lambda sid: (header, []))
        before = previous_packet(app.work_invocation, child['id'])
        after = app.work_invocation.current(child['id'])
    assert after['available'] and encoded(after) == encoded(before)
    assert after['discovery'] == ordinary['discovery']
    assert after['required'] == header
    assert len(encoded(after)) > CONTEXT_BYTES
    assert len(encoded(after)) - input_identity_bytes(header) <= CONTEXT_BYTES


async def test_one_item_overflow_and_canonical_empty_page_keep_existing_distinction(app, monkeypatch):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    workspace = {'id': workspace_id(app, owner)}
    monkeypatch.setattr('amplifier_web.workspace_work.time.time', lambda: 1794095999.1234567)
    monkeypatch.setattr(app.work_invocation, 'bound_root', lambda sid, **kw: owner)
    # A large non-identity header leaves too little space for one ordinary item.
    header = {'status': 'unavailable', 'reason': '', 'coverage': {'complete': False}}
    one = app.workspace_work.listing(workspace, {}, owner=owner['id'], context=True)
    packet = {'available': True, 'discovery': one, 'required': header}
    header['reason'] = 'x' * (CONTEXT_BYTES - len(encoded(packet)) + 1)
    monkeypatch.setattr(app.work_invocation, 'required', lambda sid: (header, []))
    before = previous_packet(app.work_invocation, owner['id'])
    after = app.work_invocation.current(owner['id'])
    assert not after['available'] and 'byte bound' in after['required']['reason']
    assert encoded(after) == encoded(before)

    # The standalone owner historically trims an intrinsically oversized row to
    # zero. A reduced enclosing budget must preserve that different behavior.
    original = app.workspace_work.summary
    monkeypatch.setattr(app.workspace_work, 'summary',
                        lambda value: {**original(value), 'candidate': 'x' * CONTEXT_BYTES})
    ordinary = app.workspace_work.listing(workspace, {}, owner=owner['id'], context=True)
    assert ordinary['items'] == [] and ordinary['total'] == 1
    assert ordinary['nextAfter'] is None and ordinary['truncated']
    before = previous_packet(app.work_invocation, owner['id'])
    after = app.work_invocation.current(owner['id'])
    assert after['available'] and encoded(after) == encoded(before)
    assert after['discovery'] == ordinary
    # Non-context public list is unaffected by the private context byte budget.
    assert app.workspace_work.listing(workspace, {}, context_bytes=0)['items'][0]['id'] == item['id']
