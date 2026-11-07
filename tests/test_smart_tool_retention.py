"""Retention accounting stays bounded and never reads or replays tool bodies."""
import json

import pytest

from amplifier_web import storage_cleanup
from test_smart_tools import manager


def insert(manager, identity, **fields):
    value = {'id': identity, 'status': 'completed', **fields}
    manager.service.db.execute('INSERT INTO smart_tool_operations VALUES(?,?)', (identity, json.dumps(value)))
    manager.service.db.commit()


async def test_accounting_reports_receipts_and_shared_body_references_without_hydration(manager, monkeypatch):
    monkeypatch.setattr(manager, 'operation', lambda *_: pytest.fail('Full operation read'))
    from amplifier_web import state_storage
    monkeypatch.setattr(state_storage, 'resource', lambda *_: pytest.fail('Resource hydration'))
    insert(manager, 'one', result={'$resource': 'same-body', 'bytes': 12345}, arguments={'value': 'hi'})
    insert(manager, 'two', result={'$resource': 'same-body', 'bytes': 12345})
    manager.service.smart_tools = manager
    before = list(manager.service.db.execute('SELECT * FROM smart_tool_operations'))
    page = (await storage_cleanup.dispatch(manager.service, 'storage.cleanup.evidence', {'owner': 'smartTools'}, 'ui', None))['result']
    assert len(page['items']) == 2 and page['unaccountedCount'] == 0
    assert page['payloadBytes'] == 24690 + len('{"value":"hi"}'.encode())
    assert page['receiptBytes'] > 0 and page['recordBytes'] > page['receiptBytes']
    assert 'not allocated disk' in page['note'] and 'Shared references' in page['note']
    assert before == list(manager.service.db.execute('SELECT * FROM smart_tool_operations'))
    assert not manager.service.published


async def test_accounting_pages_legacy_large_and_incomplete_records_without_false_zero(manager):
    insert(manager, 'legacy-large', result='not projected' * 30000)
    insert(manager, 'missing-size', result={'$resource': 'unknown-size'})
    for index in range(32):
        insert(manager, str(index), resultExpired=True)
    first = manager.retention_accounting()
    assert len(first['items']) == 32 and first['payloadBytes'] == 0 and first['unaccountedCount'] == 0
    insert(manager, 'newest', result={'later': True})
    second = manager.retention_accounting(first['nextBefore'])
    assert [item['id'] for item in second['items']] == ['missing-size', 'legacy-large']
    assert second['nextBefore'] is None and second['unaccountedCount'] == 2
    assert all(item['resultBytes'] is None for item in second['items'])
    assert second['items'][1]['receiptBytes'] is None
    assert second['items'][1]['recordBytes'] > 262144
    assert len(json.dumps(second)) < 2000


@pytest.mark.parametrize('before', [True, 0, -1, 1.5, '1', 2**53])
async def test_accounting_rejects_invalid_cursors(manager, before):
    with pytest.raises(ValueError, match='cursor'):
        manager.retention_accounting(before)


async def test_periodic_expiry_is_bounded_and_keeps_exact_receipts_and_uncertain_work(manager, monkeypatch):
    import time
    from amplifier_web.storage_migration import RESULT_EXPIRY_BATCH
    now = time.time()
    for status in ('running', 'queued', 'interrupted', 'unknown', 'outcome_unknown'):
        insert(manager, status, status=status, updatedAt=now-100*86400, arguments={'keep': status})
    insert(manager, 'legacy-no-time', result='keep')
    insert(manager, 'recent', updatedAt=now, result='keep')
    for index in range(RESULT_EXPIRY_BATCH + 1):
        insert(manager, 'old-' + str(index), updatedAt=now-31*86400-index, result='expire', arguments={'key': index})
    manager.state['inspectedOperation'] = manager.operation('old-0')
    monkeypatch.setattr(manager, 'execute', lambda *_args, **_kwargs: pytest.fail('Replayed tool'))
    assert await manager.expire_results() == RESULT_EXPIRY_BATCH
    assert manager.operation('old-0')['resultExpired'] is True
    assert manager.state['inspectedOperation']['resultExpired'] is True
    assert await manager.expire_results() == 1
    publications = len(manager.service.published)
    assert await manager.expire_results() == 0
    assert len(manager.service.published) == publications
    for status in ('running', 'queued', 'interrupted', 'unknown', 'outcome_unknown'):
        assert manager.operation(status)['arguments'] == {'keep': status}
    assert manager.operation('legacy-no-time')['result'] == 'keep'
    assert manager.operation('recent')['result'] == 'keep'
    with pytest.raises(ValueError, match='already has a receipt'):
        await manager.command('smartTools.call', {'id': 'never-start', 'name': 'no-replay'}, 'old-0')


async def test_expiry_publication_failure_restores_receipt_and_inspection(manager, monkeypatch):
    insert(manager, 'old', updatedAt=1, result={'keep': True})
    manager.state['inspectedOperation'] = manager.operation('old')
    before = manager.operation('old')
    def fail(**_):
        raise RuntimeError('Commit unavailable')
    monkeypatch.setattr(manager.service, '_publish_changes', fail)
    with pytest.raises(RuntimeError, match='Commit unavailable'):
        await manager.expire_results()
    assert manager.operation('old') == before
    assert manager.state['inspectedOperation'] == before
    manager.service.backup_in_progress = True
    assert await manager.expire_results() == 0
    manager.service.backup_in_progress = False
    manager.closed = True
    assert await manager.expire_results() == 0
    manager.closed = False


async def test_expiry_releases_only_bodies_without_any_other_owner(manager):
    from amplifier_web.resource_files import collect, put
    db = manager.service.db
    shared = put(db, {'text': 'durable artifact'})
    disposable = put(db, {'text': 'temporary result'})
    manager.service.state['canvasArtifacts'] = [{'body': shared}]
    insert(manager, 'shared', updatedAt=1, result=shared)
    insert(manager, 'disposable', updatedAt=1, result=disposable)
    assert await manager.expire_results() == 2
    assert collect(db, manager.service.state) == [disposable['$resource']]
    db.commit()
    assert db.execute('SELECT id FROM state_resources').fetchall() == [(shared['$resource'],)]
    assert manager.operation('shared')['resultExpired'] is True


async def test_periodic_expiry_uses_candidate_index_instead_of_receipt_history(manager, monkeypatch):
    from amplifier_web import storage_migration
    db = manager.service.db
    db.executemany('INSERT INTO smart_tool_operations VALUES(?,?)',
        [(str(i), json.dumps({'id': str(i), 'status': 'completed', 'resultExpired': True})) for i in range(5000)])
    db.commit()
    insert(manager, 'old', updatedAt=1, result='expire')
    plan = list(db.execute('EXPLAIN QUERY PLAN SELECT id FROM smart_tool_operations WHERE '
                          + storage_migration.RESULT_CANDIDATE + " ORDER BY json_extract(value,'$.updatedAt') DESC"))
    assert 'smart_tool_result_retention' in str(plan)
    assert 'TEMP B-TREE' not in str(plan)
    loads = json.loads
    decoded = []
    def counted(raw, *args, **kwargs):
        decoded.append(raw)
        return loads(raw, *args, **kwargs)
    monkeypatch.setattr(storage_migration.json, 'loads', counted)
    assert await manager.expire_results() == 1
    assert len(decoded) == 1  # Neither retired receipt history nor full tool bodies are loaded.


async def test_existing_periodic_maintenance_expires_tools_even_when_code_cleanup_disabled(manager, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from amplifier_web.updates import UpdateManager
    import asyncio
    db = manager.service.db
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    storage_cleanup.store(manager.service, storage_cleanup.POLICY, {**storage_cleanup.DEFAULTS, 'enabled': False})
    manager.service.smart_tools = manager
    insert(manager, 'old', updatedAt=1, result='expire')
    updates = SimpleNamespace(service=manager.service, closed=False, awaiting_restart=lambda: False, publish=AsyncMock())
    monkeypatch.setattr(asyncio, 'sleep', AsyncMock())
    await UpdateManager.retire_storage(updates)
    assert manager.operation('old')['resultExpired'] is True
    assert updates.publish.call_args.kwargs['storage']['status'] == 'disabled'
