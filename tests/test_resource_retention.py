"""Reachability must not depend on which client triggers a normal save."""
import json
import sqlite3
from types import MappingProxyType

from amplifier_web.client_views import ClientState
from amplifier_web.resource_files import collect, put, references, remove_files, root
from amplifier_web.service import AppService
from amplifier_web.state_storage import resource


def test_references_walk_client_state_and_nested_mappings():
    shared = {'canvasArtifacts': [{'body': MappingProxyType({'$resource': 'shared'})}]}
    local = {'canvas': {'mcpState': {'$resource': 'local'}}}
    assert set(references(ClientState(shared, local))) == {'shared', 'local'}


def test_collection_retains_persisted_clients_before_client_views_load(tmp_path):
    with sqlite3.connect(tmp_path / 'app.sqlite3') as db:
        db.execute('CREATE TABLE state_resources(id TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('CREATE TABLE client_views(id TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('CREATE TABLE smart_tool_operations(id TEXT PRIMARY KEY,value TEXT NOT NULL)')
        nested = put(db, {'dynamic': 'retained'})
        client_only = put(db, {'child': nested})
        receipt_only = put(db, {'result': 'retained'})
        stale = put(db, {'orphan': True})
        db.execute('INSERT INTO client_views VALUES (?,?)', ('disconnected', json.dumps({'canvas': {'mcpState': client_only}})))
        db.execute('INSERT INTO smart_tool_operations VALUES (?,?)', ('receipt', json.dumps({'result': receipt_only})))
        removed = collect(db, {})
        assert removed == [stale['$resource']]
        db.commit()
        remove_files(db, removed)
        assert resource(db, nested['$resource']) == {'dynamic': 'retained'}
        assert resource(db, client_only['$resource']) == {'child': nested}
        assert resource(db, receipt_only['$resource']) == {'result': 'retained'}
        assert not (root(db) / (stale['$resource'] + '.json')).exists()


async def test_bound_save_retains_shared_other_client_and_nested_resources_on_disk(tmp_path):
    home = tmp_path / 'app'
    service = AppService(home, workspace=tmp_path)
    try:
        service.clients.attach('browser-a')
        service.clients.attach('browser-b')
        nested = put(service.db, {'nested': 'retained'})
        body = put(service.db, {'content': '<h1>Retained</h1>', 'child': nested})
        dynamic = put(service.db, {'revision': 12, 'view': 'read-only'})
        shared_view = put(service.db, {'shared': 'hidden by bound client'})
        other_client = put(service.db, {'draft': 'other client only'})
        orphan = put(service.db, {'unreferenced': True})
        service._state['canvasArtifacts'] = [{'id': 'surface', 'kind': 'mcp-app', 'body': body, 'mcpState': dynamic}]
        service._state['view']['retainedResource'] = shared_view
        service.clients.records['browser-b']['attachments'] = {'draft': [other_client]}
        service._last_storage_sweep = -1e30
        with service.clients.bind('browser-a'):
            service._save()
            await service._storage_maintenance_task
        kept = (nested, body, dynamic, shared_view, other_client)
        for ref in kept:
            assert resource(service.db, ref['$resource'])
            assert (root(service.db) / (ref['$resource'] + '.json')).is_file()
        assert service.db.execute('SELECT 1 FROM state_resources WHERE id=?', (orphan['$resource'],)).fetchone() is None
        assert not (root(service.db) / (orphan['$resource'] + '.json')).exists()
    finally:
        await service.close()
    restored = AppService(home, workspace=tmp_path)
    try:
        for ref in kept:
            assert resource(restored.db, ref['$resource'])
        assert restored.clients.records['browser-b']['attachments']['draft'] == [other_client]
    finally:
        await restored.close()


async def test_maintenance_retains_in_memory_clients_even_before_their_next_save(tmp_path):
    from amplifier_web.storage_migration import maintenance
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        service.clients.attach('inactive')
        value = put(service.db, {'unsaved': 'retained'})
        parent = put(service.db, {'nested': value})
        orphan = put(service.db, {'orphan': True})
        service.clients.records['inactive']['attachments'] = {'draft': [parent]}
        service.db.commit()
        service._last_storage_sweep = -1e30
        maintenance(service)
        await service._storage_maintenance_task
        assert resource(service.db, value['$resource']) == {'unsaved': 'retained'}
        assert resource(service.db, parent['$resource']) == {'nested': value}
        assert not service._resource_scan_status['blocked']
        assert service.db.execute('SELECT 1 FROM state_resources WHERE id=?',
                                  (orphan['$resource'],)).fetchone() is None
        assert not (root(service.db) / (orphan['$resource'] + '.json')).exists()
    finally:
        await service.close()


async def test_exact_resource_recovery_preserves_artifact_and_survives_cleanup(tmp_path):
    from amplifier_web.management import Management
    from amplifier_web.storage_migration import maintenance
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        value = {'content': '<h1>Exact original content</h1>'}
        ref = put(service.db, value)
        service._state['canvasArtifacts'] = [{'id': 'original-artifact', 'kind': 'mcp-app', 'body': ref}]
        service._save()
        service.db.execute('DELETE FROM state_resources WHERE id=?', (ref['$resource'],))
        service.db.commit()
        (root(service.db) / (ref['$resource'] + '.json')).unlink()
        manager = Management(service)
        await manager.perform('maintenance.restoreResource', {'id': ref['$resource'], 'value': value})
        assert service.state['maintenance']['resourceRecovery'] == {'id': ref['$resource'], 'restored': True}
        assert service._state['canvasArtifacts'][0]['id'] == 'original-artifact'
        assert service._state['canvasArtifacts'][0]['body'] == ref
        service._last_storage_sweep = -1e30
        maintenance(service)
        assert resource(service.db, ref['$resource']) == value
        await manager.perform('maintenance.restoreResource', {'id': ref['$resource'], 'value': value})
        assert service.state['maintenance']['resourceRecovery']['restored'] is False
    finally:
        await service.close()


def test_resource_recovery_rejects_changed_unreferenced_or_conflicting_content(tmp_path):
    import pytest
    from amplifier_web.resource_files import restore
    with sqlite3.connect(tmp_path / 'app.sqlite3') as db:
        db.execute('CREATE TABLE state_resources(id TEXT PRIMARY KEY,value TEXT NOT NULL)')
        value = {'content': 'original'}
        ref = put(db, value)
        with pytest.raises(ValueError, match='does not match'):
            restore(db, ref, ref['$resource'], {'content': 'changed'})
        with pytest.raises(ValueError, match='existing saved reference'):
            restore(db, {}, ref['$resource'], value)
        path = root(db) / (ref['$resource'] + '.json')
        path.write_text('{"content":"conflict"}')
        with pytest.raises(ValueError, match='conflicts'):
            restore(db, ref, ref['$resource'], value)
        db.execute('DELETE FROM state_resources')
        with pytest.raises(ValueError, match='conflicts'):
            restore(db, ref, ref['$resource'], value)
        assert path.read_text() == '{"content":"conflict"}'
        path.unlink()
        assert restore(db, ref, ref['$resource'], value)['restored']
        path.unlink()  # A surviving index with a missing file is recoverable too.
        assert restore(db, ref, ref['$resource'], value)['restored']
        assert resource(db, ref['$resource']) == value
