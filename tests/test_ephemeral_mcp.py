"""Transient connector settings follow native worker admission and cleanup."""
import asyncio
import copy
import json
import sys
from types import SimpleNamespace

import pytest

from amplifier_web.execution_owner import ExecutionOwner
from amplifier_web.host.initialization import initialization_hooks
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService


SERVERS = {"context": {"type": "http", "url": "http://127.0.0.1/context/mcp",
                       "headers": {"Authorization": "Bearer synthetic-private-lease"}}}
WORKER = r'''
import json,sys
pending=None
for line in sys.stdin:
    row=json.loads(line)
    if row['op']=='start':
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
    elif row['op']=='send':
        pending=row['id']
        print(json.dumps({'type':'input.delivered','input_id':row['input_id']}),flush=True)
    elif row['op']=='dependencies':
        if pending:
            print(json.dumps({'op':'reply','id':pending,'result':{'accepted':True}}),flush=True)
            pending=None
        print(json.dumps({'op':'reply','id':row['id'],'result':{}}),flush=True)
    elif row['op']=='control':
        print(json.dumps({'op':'reply','id':row['id'],'result':{'operation':row['operation']}}),flush=True)
    elif row['op']=='stop':break
'''


async def test_initialization_supplement_is_not_session_configuration():
    capabilities, order = {}, []
    session = SimpleNamespace(config={"tools": [{"module": "tool-mcp"}]},
        coordinator=SimpleNamespace(register_capability=capabilities.__setitem__))
    hooks = initialization_hooks(isolation=None, decision_accounts=SimpleNamespace(records=[]),
        routing=None, retain=lambda value: order.append(value), ephemeral_servers=SERVERS)
    await hooks['before_initialize'](session)
    assert order == [session]  # Partial initialization has an owner before mount can fail.
    assert capabilities['mcp.ephemeral_servers'] == SERVERS
    assert 'synthetic-private-lease' not in json.dumps(session.config)


async def test_metadata_attachment_keeps_credentials_out_of_durable_owner(tmp_path):
    runtime = RuntimeManager(command=[sys.executable, '-u', '-c', WORKER])
    app = AppService(tmp_path/'app', runtime, workspace=tmp_path)
    owner = ExecutionOwner(app, workspace=tmp_path, bundle='foundation', origin='ui', enable_mcp=True)
    try:
        identity = await owner.create(str(tmp_path), mcp_servers=SERVERS)
        assert not runtime.workers
        assert 'synthetic-private-lease' not in json.dumps(app.state)
        assert 'synthetic-private-lease' not in '\n'.join(app.db.iterdump())
        await owner.release(identity)
        assert identity not in runtime._ephemeral_mcp
        assert await owner.attach(identity, str(tmp_path), mcp_servers=SERVERS) == identity
        assert not runtime.workers
        assert 'synthetic-private-lease' not in '\n'.join(app.db.iterdump())
    finally:
        await owner.close()
    assert not runtime._ephemeral_mcp


async def test_release_keeps_admitted_work_but_prevents_live_credential_reuse(tmp_path):
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', WORKER], startup_timeout=3,
                             retention={'prewarm_on_select': False})
    manager.home = tmp_path/'app'
    session = {'id': 'owned', 'workspace': str(tmp_path), 'bundle': 'fixture'}
    events, writes = [], []
    delivered = asyncio.Event()
    async def emit(kind, payload):
        events.append((kind, payload))
        if kind == 'runtime.status' and payload.get('event') == 'input.delivered':delivered.set()
    original = manager._write
    async def write(row, value):
        writes.append(copy.deepcopy(value))
        await original(row, value)
    manager._write = write
    servers = copy.deepcopy(SERVERS)
    await manager.bind_ephemeral_mcp('owned', 'attachment', servers)
    servers['context']['headers']['Authorization'] = 'caller mutated'
    pending = asyncio.create_task(manager.send(session, 'work', 'input', emit))
    try:
        await asyncio.wait_for(delivered.wait(), 4)
        assert writes[0]['session']['ephemeralMcpServers'] == SERVERS
        assert 'synthetic-private-lease' not in json.dumps(manager.workers['owned']['start_session'])
        assert 'synthetic-private-lease' not in json.dumps(session)
        manager.release_ephemeral_mcp('owned', 'foreign')
        assert 'owned' in manager._ephemeral_mcp
        manager.release_ephemeral_mcp('owned', 'attachment')
        assert not pending.done()
        assert not any(row['op'] == 'stop' for row in writes)
        await manager._request('owned', 'dependencies')
        assert await pending == {'accepted': True}
        with pytest.raises(RuntimeError, match='released'):
            await manager.send(session, 'new work', 'new-input', emit)
        assert await manager.control('owned', 'capacity.read', {}) == {'operation':'capacity.read'}
        with pytest.raises(RuntimeError, match='released'):
            await manager.control('owned', 'tool.invoke', {'name':'context_read'})
        with pytest.raises(RuntimeError, match='released'):
            await manager.message_worker('owned', 'child', 'new input', 'child-input')
        with pytest.raises(ValueError, match='another MCP attachment'):
            await manager.bind_ephemeral_mcp('owned', 'next', SERVERS)
        assert [row['op'] for row in writes].count('send') == 1
        assert 'synthetic-private-lease' not in json.dumps(events)
        await manager.stop('owned')
        await manager.bind_ephemeral_mcp('owned', 'next', SERVERS)
        await manager.start(session, emit)
        assert len([row for row in writes if row['op']=='start']) == 2
    finally:
        await manager.close()
        await asyncio.gather(pending, return_exceptions=True)
    assert not manager._ephemeral_mcp


async def test_release_during_startup_never_sends_transient_settings(tmp_path):
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', WORKER], startup_timeout=3)
    manager.home = tmp_path/'app'
    entered, proceed = asyncio.Event(), asyncio.Event()
    writes=[]
    original=manager._write
    async def write(row,value):
        writes.append(copy.deepcopy(value));await original(row,value)
    manager._write=write
    async def emit(kind,payload):
        if kind=='runtime.status' and payload.get('status')=='starting':
            entered.set();await proceed.wait()
    await manager.bind_ephemeral_mcp('owned','attachment',SERVERS)
    pending=asyncio.create_task(manager.start({'id':'owned','workspace':str(tmp_path),'bundle':'fixture'},emit))
    try:
        await asyncio.wait_for(entered.wait(),3)
        manager.release_ephemeral_mcp('owned','attachment');proceed.set()
        with pytest.raises(RuntimeError,match='released'):
            await asyncio.wait_for(pending,5)
        assert all(row['op']!='start' for row in writes)
        assert not manager.workers
    finally:
        proceed.set();await manager.close()
        await asyncio.gather(pending,return_exceptions=True)
