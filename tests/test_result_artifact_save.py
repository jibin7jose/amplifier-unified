"""Explicit artifact consumption through the public action and exact disk bytes."""
import asyncio
import copy
import json
import os
import threading
import tracemalloc

import pytest

from amplifier_web.service import AppError, ACTION_DEFINITIONS
from test_collaborative_workspaces import app, agent_action, finish, generation
from test_result_artifacts import setup, declare, read
from test_retained_large_files import large, digest


@pytest.fixture
def writable(app, monkeypatch):
    async def observe(sid, paths):
        return {'status': 'observed', 'paths': {path: {'status': 'allowed'} for path in paths}}
    monkeypatch.setattr(app.runtime, 'resource_access', observe, raising=False)


async def save(app, source, args, path='saved.bin', **patch):
    return (await agent_action(app, source, 'coordination.artifact.save', {
        'sessionId': source['id'], 'requestId': args['requestId'], 'reference': args['references'][0],
        'responseRevision': 1, 'path': path, **patch}, 'save-artifact'))['result']


async def test_binary_save_action_regression(app, tmp_path, writable):
    """Run unchanged against predecessor: Unknown action, not a synthetic absence."""
    source, peer, path, args = await large(app, tmp_path, 40)
    expected, size = digest(path), path.stat().st_size
    declaration = (await declare(app, peer, args))['result']
    await finish(app, peer, args['requestId'])
    preview = await read(app, source, args)
    assert preview['binary'] and preview['downloadUrl'].startswith('/api/outputs/')
    with pytest.raises(AppError, match='another conversation|caller|calling'):
        await agent_action(app, source, 'outputs.read', {'sessionId': source['id'],
            'id': declaration['artifacts'][0]['outputId']}, 'peer-library-not-a-bypass')
    own = tmp_path / 'caller-execution'
    own.mkdir()
    source['workingDirectory'], source['executionRevision'] = str(own), 8
    path.unlink()
    before = copy.deepcopy(app.state['view'])
    tracemalloc.start()
    try:
        result = await save(app, source, args)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    target = own / 'saved.bin'
    assert result['status'] == 'saved'
    assert result['bytes'] == size == target.stat().st_size > 40 * 1024 * 1024
    assert result['sha256'] == expected == digest(target)
    assert result['path'] == str(target) and result['executionRevision'] == 8
    assert result['requestId'] == args['requestId'] and result['responseRevision'] == 1
    assert result['qualified'] and not result['independentArtifactVerification']
    assert result['provenance']['generationId'] == declaration['generationId']
    assert result['provenance']['executionRevision'] == 3
    assert peak < 32 * 1024 * 1024, peak  # Existing retained chunk-reader budget, not file-size resident.
    assert app.state['view'] == before
    assert not list(own.glob('.artifact-*'))
    assert (await read(app, source, args))['sha256'] == expected
    print(json.dumps({'savedBytes': size, 'sha256': expected, 'savePeakBytes': peak}))


@pytest.mark.parametrize('content', [b'', b'hello', b'\x00\xff\r\n\x1a' * 100])
async def test_small_save_and_verified_reuse_without_source_transfer(app, tmp_path, writable, monkeypatch, content):
    source, peer, path, args = await setup(app, tmp_path, content)
    await declare(app, peer, args)
    first = await save(app, source, args)
    assert (tmp_path / 'saved.bin').read_bytes() == content
    monkeypatch.setattr(app.outputs, 'content_async', lambda *_: pytest.fail('Same-byte reuse must not transfer retained bytes'))
    second = await save(app, source, args)
    assert second == {**first, 'status': 'reused'}
    assert path.read_bytes() == content


@pytest.mark.parametrize('path', ['../escape', '/escape', 'C:/escape', 'C:escape', '\\escape',
    'a/../b', 'a//b', './a', 'a\\b', 'x:stream', 'NUL', 'con.txt', 'COM1', 'LPT1.txt', 'file.', 'file ', '.git/config'])
async def test_portable_path_rejection_before_write(app, tmp_path, writable, path):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    before = set(tmp_path.iterdir())
    with pytest.raises(AppError, match='relative file path'):
        await save(app, source, args, path)
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize('kind', ['different', 'same_size', 'directory', 'symlink', 'hardlink', 'parent_symlink'])
async def test_existing_or_aliased_destination_never_clobbered(app, tmp_path, writable, kind):
    source, peer, original, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    target = tmp_path / 'saved.bin'
    other = tmp_path / 'other.bin'
    other.write_bytes(original.read_bytes())
    path = target.name
    if kind == 'different': target.write_bytes(b'keep')
    elif kind == 'same_size': target.write_bytes(b'Z' * original.stat().st_size)
    elif kind == 'directory': target.mkdir()
    elif kind == 'hardlink': os.link(other, target)
    elif kind == 'symlink': target.symlink_to(other)
    else:
        target.symlink_to(original.parent, target_is_directory=True)
        path += '/new.bin'
    with pytest.raises(AppError):
        await save(app, source, args, path)
    assert other.read_bytes() == original.read_bytes()
    if kind in {'different', 'same_size'}:
        assert target.read_bytes() == (b'keep' if kind == 'different' else b'Z' * original.stat().st_size)
    assert not list(tmp_path.glob('.artifact-*'))
    assert not (original.parent / 'new.bin').exists()


@pytest.mark.parametrize('change', ['participant', 'session', 'request', 'reference', 'revision', 'workspace'])
async def test_save_retains_exact_request_authority(app, tmp_path, writable, change):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    patch = {}
    if change == 'participant':
        source = app._new_session({'title': 'Unrelated'})
        app.state['sessions'].append(source)
        await generation(app, source, 'outsider', ['outsider'])
    elif change == 'session': patch['sessionId'] = peer['id']
    elif change == 'request': patch['requestId'] = 'artifact-declaration'
    elif change == 'reference': patch['reference'] = 'unrelated.bin@sha256:' + 'a' * 64
    elif change == 'revision': patch['responseRevision'] = 9
    else: peer['workspace'] += '/moved'
    with pytest.raises(AppError):
        await save(app, source, args, **patch)
    assert not (tmp_path / 'saved.bin').exists()


@pytest.mark.parametrize('status', ['denied', 'unknown', 'tool-dependent'])
async def test_save_requires_confirmed_mounted_write_permission(app, tmp_path, monkeypatch, status):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    async def refuse(sid, paths):
        return {'status': 'observed', 'paths': {p: {'status': status} for p in paths}}
    monkeypatch.setattr(app.runtime, 'resource_access', refuse, raising=False)
    with pytest.raises(AppError, match='write access'):
        await save(app, source, args)
    assert not (tmp_path / 'saved.bin').exists()
    assert not list(tmp_path.glob('.artifact-*'))


async def test_child_cannot_borrow_root_save_policy(app, tmp_path, writable):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    with pytest.raises(AppError, match='child|root'):
        await app.app_bridge('dispatch', {'action': 'coordination.artifact.save', 'args': {
            'sessionId': source['id'], 'requestId': args['requestId'], 'reference': args['references'][0],
            'responseRevision': 1, 'path': 'saved.bin'},
            '_runtimeSessionId': 'unadmitted-child', '_generationId': source['collaborationGeneration']['id'],
            '_inputBindings': []}, source['id'])
    assert not (tmp_path / 'saved.bin').exists()


async def test_exact_historical_response_and_changed_provenance(app, tmp_path, writable):
    source, peer, _, args = await setup(app, tmp_path)
    declaration = (await declare(app, peer, args))['result']
    request = app.collaboration.receipt(args['requestId'])
    request.update(responseRevision=2, response={'responseRevision': 2, 'references': [], 'artifacts': []})
    app.collaboration.save(args['requestId'], request)
    result = await save(app, source, args)
    assert result['status'] == 'saved' and (tmp_path / 'saved.bin').read_bytes() == b'Original exact bytes'
    record = app.outputs.store.read(declaration['artifacts'][0]['outputId'])
    record['collaborationResult']['requestId'] = 'different-request'
    app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(record), record['id']))
    app.db.commit()
    with pytest.raises(AppError, match='unavailable'):
        await save(app, source, args, 'other.bin')
    assert not (tmp_path / 'other.bin').exists()


async def test_full_observer_scope_is_not_peer_library_access(app, tmp_path, writable):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    observer = app._new_session({'title': 'Full observer'})
    app.state['sessions'].append(observer)
    await generation(app, observer, 'observer', ['observer'])
    # Use the existing standing-access owner, not forged result participants.
    current = (await app.dispatch('access.get', {'sessionId': observer['id']}))['result']
    await app.dispatch('access.set', {'sessionId': observer['id'], 'scope': 'session',
        'mode': 'full', 'expectedRevision': current['revision']})
    # Scripted runtime: emulate completion of its safe file-policy remount.
    observer.pop('fileAccessPending', None)
    observer.pop('configurationPending', None)
    result = await save(app, observer, args, 'observed.bin')
    assert result['status'] == 'saved'
    observer['workspace'] = str(tmp_path / 'other-workspace')
    os.mkdir(observer['workspace'])
    current = (await app.dispatch('access.get', {'sessionId': observer['id']}))['result']
    await app.dispatch('access.set', {'sessionId': observer['id'], 'scope': 'session',
        'mode': 'full', 'expectedRevision': current['revision']})
    observer.pop('fileAccessPending', None)
    observer.pop('configurationPending', None)
    with pytest.raises(AppError, match='scope'):
        await save(app, observer, args)
    assert (await save(app, observer, args, scope='all'))['status'] == 'saved'


@pytest.mark.parametrize('change', ['stop', 'placement', 'generation', 'response', 'permission', 'pending', 'cancel', 'parent', 'destination'])
async def test_transfer_fences_and_cancellation_settle_without_publication(app, tmp_path, writable, monkeypatch, change):
    from amplifier_web import result_artifact_save
    source, peer, _, args = await setup(app, tmp_path, b'\x00\xff' * 600000)
    await declare(app, peer, args)
    destination = tmp_path / 'destination'
    destination.mkdir()
    entered, release = threading.Event(), threading.Event()
    write = result_artifact_save.write_verified
    def blocked(*values):
        result = write(*values)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(result_artifact_save, 'write_verified', blocked)
    task = asyncio.create_task(save(app, source, args, 'destination/saved.bin'))
    renamed = tmp_path / 'old-destination'
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with asyncio.timeout(2):
            async with app.lock:
                if change == 'stop': source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
                elif change == 'placement': source['executionRevision'] = 1
                elif change == 'generation': source['collaborationGeneration']['id'] = 'replacement'
                elif change == 'response':
                    request = app.collaboration.receipt(args['requestId'])
                    request['response']['text'] = 'Changed report'
                    app.collaboration.save(args['requestId'], request)
                elif change == 'pending': source['fileAccessPending'] = True
                elif change == 'permission':
                    async def deny(sid, paths): return {'status': 'unknown', 'paths': {}}
                    monkeypatch.setattr(app.runtime, 'resource_access', deny)
                elif change == 'cancel': task.cancel()
                elif change == 'parent':
                    if os.name == 'nt':
                        with pytest.raises(OSError): destination.rename(renamed)
                        task.cancel()
                    else:
                        destination.rename(renamed)
                        destination.mkdir()
                else: (destination / 'saved.bin').write_bytes(b'other writer')
        if change == 'cancel':
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        with pytest.raises((AppError, asyncio.CancelledError)):
            await task
        if change == 'destination': assert (destination / 'saved.bin').read_bytes() == b'other writer'
        else: assert not (destination / 'saved.bin').exists()
        assert not list(destination.glob('.artifact-*'))
        if renamed.exists(): assert not list(renamed.iterdir())
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('failure', ['truncated', 'hash', 'write'])
async def test_integrity_and_partial_write_failure_preserve_originals(app, tmp_path, writable, monkeypatch, failure):
    from amplifier_web import resource_files, result_artifact_save
    source, peer, path, args = await large(app, tmp_path, 2)
    result = (await declare(app, peer, args))['result']
    record = app.outputs.store.read(result['artifacts'][0]['outputId'])
    if failure == 'write':
        def fail(stream, *values):
            stream.write(b'partial')
            raise OSError('injected disk error')
        monkeypatch.setattr(result_artifact_save, 'write_verified', fail)
    else:
        manifest = app.state_resource(record['body']['$resource'])
        if failure == 'truncated':
            chunk = resource_files.root(app.db) / (manifest['chunks'][-1]['$resource'] + '.json')
            chunk.write_bytes(b'partial')
        else:
            manifest['chunks'][0] = manifest['chunks'][1]
            record['body'] = resource_files.put(app.db, manifest)
            app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(record), record['id']))
            app.db.commit()
    with pytest.raises(AppError):
        await save(app, source, args)
    assert path.exists() and digest(path) == args['references'][0].split('@sha256:')[1]
    assert not (tmp_path / 'saved.bin').exists()
    assert not list(tmp_path.glob('.artifact-*'))
    assert app.outputs.store.read(record['id'])


async def test_late_parent_replacement_preserves_files_and_reports_uncertainty(app, tmp_path, writable, monkeypatch):
    if os.name == 'nt': pytest.skip('Windows pins deny directory rename')
    from amplifier_web import test_origin_files
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    folder = tmp_path / 'destination'
    folder.mkdir()
    old = tmp_path / 'old'
    link = test_origin_files.PinnedDirectory.link
    def swap(parent, before, after):
        link(parent, before, after)
        folder.rename(old)
        folder.mkdir()
        (folder / 'saved.bin').write_bytes(b'replacement must survive')
    monkeypatch.setattr(test_origin_files.PinnedDirectory, 'link', swap)
    with pytest.raises(AppError, match='directory changed.*Publication may have occurred'):
        await save(app, source, args, 'destination/saved.bin')
    assert (folder / 'saved.bin').read_bytes() == b'replacement must survive'
    assert (old / 'saved.bin').read_bytes() == b'Original exact bytes'
    assert not list(old.glob('.artifact-*'))


async def test_same_size_write_at_publication_never_claims_verified_success(app, tmp_path, writable, monkeypatch):
    from amplifier_web import test_origin_files
    source, peer, path, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    link = test_origin_files.PinnedDirectory.link
    def mutate(parent, before, after):
        temporary = parent.path / before
        info = temporary.stat()
        temporary.write_bytes(b'Z' * info.st_size)
        os.utime(temporary, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
        link(parent, before, after)
    monkeypatch.setattr(test_origin_files.PinnedDirectory, 'link', mutate)
    with pytest.raises(AppError, match='Publication may have occurred'):
        await save(app, source, args)
    assert (tmp_path / 'saved.bin').read_bytes() == b'Z' * path.stat().st_size
    assert not list(tmp_path.glob('.artifact-*'))


async def test_final_http_admission_rechecked(app, tmp_path, writable):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    calls = 0
    def admission():
        nonlocal calls
        calls += 1
        if calls >= 4: raise AppError('revoked client', 403)
    with pytest.raises(AppError, match='revoked client'):
        await app.dispatch('coordination.artifact.save', {'sessionId': source['id'],
            'requestId': args['requestId'], 'reference': args['references'][0],
            'responseRevision': 1, 'path': 'saved.bin'}, admission_check=admission)
    assert calls >= 4 and not (tmp_path / 'saved.bin').exists()
    assert not list(tmp_path.glob('.artifact-*'))


def test_save_discovery_is_explicit_mutation():
    from amplifier_web.workspace_work_effects import SAFE_ACTIONS
    assert 'coordination.artifact.save' not in SAFE_ACTIONS
    description, schema = ACTION_DEFINITIONS['coordination.artifact.save']
    assert 'responseRevision' in schema['required'] and 'path' in schema['required']
    assert 'large binaries' in description and 'write access' in description


async def test_session_http_save_keeps_retained_download_and_passive_read(app, tmp_path, authenticated_client):
    from amplifier_web.server import create_app
    from test_collaborative_workspaces import Runtime
    source, peer, path, args = await large(app, tmp_path, 9)
    expected, size = digest(path), path.stat().st_size
    await declare(app, peer, args)
    await finish(app, peer, args['requestId'])
    app._save_changes()
    await app.close()
    runtime = Runtime()
    async def access(sid, paths):
        return {'status': 'observed', 'paths': {p: {'status': 'allowed'} for p in paths}}
    runtime.resource_access = access
    server = await create_app(app.data_dir, workspace=tmp_path, runtime=runtime,
        voice=False, background_updates=False, preload_providers=False)
    reopened = runtime.app = server['service']
    reopened.clients.attach('artifact-consumer')
    from amplifier_web.agent_canvas import scope
    client_record = reopened.clients.records['artifact-consumer']
    client_record['selectedSessionId'], client_record['selectedWorkspaceId'] = scope(reopened, source['id'])
    client = await authenticated_client(server)
    headers = {'X-Amplifier-Client': 'artifact-consumer'}
    values = {'requestId': args['requestId'], 'reference': args['references'][0], 'responseRevision': 1}
    endpoint = '/api/sessions/' + source['id'] + '/commands'
    response = await client.post(endpoint, headers=headers, json={
        'id': 'http-save', 'action': 'coordination.artifact.save', 'args': {**values, 'path': 'received.whl'}})
    assert response.status == 200, await response.text()
    result = (await response.json())['result']
    assert result['status'] == 'saved' and result['bytes'] == size
    assert digest(tmp_path / 'received.whl') == expected
    passive = await client.post(endpoint, headers=headers, json={
        'id': 'http-passive', 'action': 'coordination.artifact', 'args': values})
    assert passive.status == 200
    preview = (await passive.json())['result']
    assert preview['binary'] and preview['sha256'] == expected
    original = await client.get(preview['downloadUrl'], headers=headers)
    assert original.status == 200 and await original.read() == path.read_bytes()


async def test_cleanup_failure_keeps_original_error_and_names_owned_staging(app, tmp_path, writable, monkeypatch):
    from amplifier_web import result_artifact_save, test_origin_files
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    def fail(stream, *values):
        stream.write(b'partial')
        raise OSError(28, 'original write failure')
    def refuse(*values):
        raise OSError('cleanup refused')
    monkeypatch.setattr(result_artifact_save, 'write_verified', fail)
    monkeypatch.setattr(test_origin_files.PinnedDirectory, 'unlink', refuse)
    with pytest.raises(AppError, match='original write failure.*cleanup failed.*cleanup refused'):
        await save(app, source, args)
    assert not (tmp_path / 'saved.bin').exists()
    retained = list(tmp_path.glob('.artifact-*'))
    assert len(retained) == 1 and retained[0].read_bytes() == b'partial'


def fence_artifact_writer(app, source, tmp_path, monkeypatch, kind):
    """Change canonical authority without updating the session projection."""
    if kind == 'context':
        monkeypatch.setattr(app.portability, 'write_context', lambda sid: 'later-transfer')
    elif kind == 'native':
        import uuid
        from amplifier_foundation.session import SharedSessionStore
        store = SharedSessionStore(source['workspace'], source['id'])
        held = store.acquire(app='artifact-save-fence-test')
        try:
            held.fence_transfer(str(uuid.uuid4()), app.portability.node.identity['id'], role='source')
        finally:
            held.release()
        assert store.transfer_fence() is not None
    else:
        from amplifier_portability import TransferNode
        from amplifier_worktrees.git import atomic
        peer = TransferNode(tmp_path / 'transfer-peer', 'synthetic-peer')
        atomic(app.portability.node.directory / 'peers.json', {peer.identity['id']: peer.identity})
        row = app.portability.node.begin(source['id'], peer.identity['id'], 'artifact-save-transfer', {})
        assert row['phase'] == 'preparing' and app.portability.node.fenced(source['id'])


async def test_canonical_transfer_fence_refuses_initial_artifact_save(app, tmp_path, writable, monkeypatch):
    source, peer, original, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    fence_artifact_writer(app, source, tmp_path, monkeypatch, 'node')
    assert not source.get('ownership') and not source.get('configurationBusy')
    with pytest.raises(AppError, match='fenced for transfer'):
        await save(app, source, args)
    assert not (tmp_path / 'saved.bin').exists()
    assert not list(tmp_path.glob('.artifact-*'))
    assert original.read_bytes() == b'Original exact bytes'


@pytest.mark.parametrize('kind', ['node', 'native', 'context'])
@pytest.mark.parametrize('reuse', [False, True])
async def test_canonical_transfer_fence_rechecked_before_save_or_reuse(app, tmp_path, writable, monkeypatch, kind, reuse):
    from amplifier_web import result_artifact_save
    source, peer, original, args = await setup(app, tmp_path, b'\x00\xff' * 600000)
    await declare(app, peer, args)
    target = tmp_path / 'saved.bin'
    if reuse:
        target.write_bytes(original.read_bytes())
    previous = result_artifact_save.identity(target.stat()) if reuse else None
    expected = digest(original)
    captured = copy.deepcopy({key: source.get(key) for key in (
        'ownership', 'workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision', 'collaborationGeneration')})
    entered, release = threading.Event(), threading.Event()
    function = 'existing' if reuse else 'write_verified'
    operation = getattr(result_artifact_save, function)
    def blocked(*values):
        result = operation(*values)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(result_artifact_save, function, blocked)
    if reuse:
        monkeypatch.setattr(result_artifact_save, 'write_verified', lambda *_: pytest.fail('Reuse transferred bytes'))
        monkeypatch.setattr(result_artifact_save.retained_files, 'plan', lambda *_: pytest.fail('Reuse planned a transfer'))
        monkeypatch.setattr(app.outputs, 'content_async', lambda *_: pytest.fail('Reuse loaded retained bytes'))
    task = asyncio.create_task(save(app, source, args))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with app.lock:
            fence_artifact_writer(app, source, tmp_path, monkeypatch, kind)
            assert captured == {key: source.get(key) for key in captured}
        release.set()
        with pytest.raises(AppError, match='fenced for transfer|transfer.*changed'):
            await task
        assert not list(tmp_path.glob('.artifact-*'))
        if reuse:
            assert result_artifact_save.identity(target.stat()) == previous and digest(target) == expected
        else:
            assert not target.exists()
        assert digest(original) == expected
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('primary', [None, 'verification', 'cancelled'])
async def test_published_artifact_cleanup_failure_reports_both_paths(app, tmp_path, writable, monkeypatch, primary):
    from amplifier_web import test_origin_files
    source, peer, original, args = await setup(app, tmp_path, b'\x00\xff\n' * 1000)
    await declare(app, peer, args)
    expected = digest(original)
    target = tmp_path / 'saved.bin'
    cleanup_error = OSError(13, 'final staging unlink refused')
    def refuse(parent, name):
        assert name.startswith('.artifact-'), 'Never delete the public destination'
        assert target.exists()
        raise cleanup_error
    monkeypatch.setattr(test_origin_files.PinnedDirectory, 'unlink', refuse)
    if primary:
        def fail_sync(parent):
            if primary == 'cancelled':
                raise asyncio.CancelledError('post-link cancellation')
            raise OSError(5, 'post-link sync failure')
        monkeypatch.setattr(test_origin_files.PinnedDirectory, 'sync', fail_sync)
    error_type = asyncio.CancelledError if primary == 'cancelled' else AppError
    with pytest.raises(error_type) as caught:
        await save(app, source, args)
    staging, = list(tmp_path.glob('.artifact-*'))
    assert target.stat().st_nlink == 2 and staging.stat().st_ino == target.stat().st_ino
    assert digest(target) == digest(staging) == digest(original) == expected
    message = str(caught.value)
    assert str(target) in message and str(staging) in message
    assert 'Publication may have occurred' in message and 'inspect' in message and 'before retrying' in message
    assert 'final staging unlink refused' in message
    if primary:
        assert ('post-link cancellation' if primary == 'cancelled' else 'post-link sync failure') in message
    else:
        assert caught.value.__cause__.__cause__ is cleanup_error
    assert (await read(app, source, args))['sha256'] == expected


def test_existing_verification_is_bounded_even_when_file_grows(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from amplifier_web import result_artifact_save, test_origin_files
    path = tmp_path / 'existing.bin'
    path.write_bytes(b'hello')
    sizes = []
    original = test_origin_files.PinnedDirectory.open
    @contextmanager
    def growing(parent, name, **options):
        with original(parent, name, **options) as stream:
            class Reader:
                def fileno(self): return stream.fileno()
                def read(self, count):
                    sizes.append(count)
                    with path.open('ab') as other:
                        other.write(b'x' * 100)
                    return stream.read(count)
            yield Reader()
    monkeypatch.setattr(test_origin_files.PinnedDirectory, 'open', growing)
    with test_origin_files.directory(tmp_path) as parent:
        with pytest.raises(ValueError, match='changed identity'):
            result_artifact_save.existing(parent, path.name, {
                'bytes': 5, 'sha256': '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'})
    assert sizes == [5, 1]
    assert path.read_bytes().startswith(b'hello')


@pytest.mark.parametrize('allowed', [True, False])
async def test_save_uses_actual_mounted_filesystem_policy(app, tmp_path, monkeypatch, allowed):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import amplifier_module_tool_filesystem as filesystem
    from amplifier_web.resource_access import observe
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    tools = {}
    async def mount(kind, tool, name): tools[name] = tool
    coordinator = SimpleNamespace(mount=mount, get=lambda kind: tools if kind == 'tools' else None,
        get_capability=lambda key: str(tmp_path) if key == 'session.working_dir' else None,
        hooks=SimpleNamespace(emit=AsyncMock()))
    await filesystem.mount(coordinator, {'allowed_write_paths': [str(tmp_path)] if allowed else []})
    async def access(sid, paths):
        return await observe(SimpleNamespace(coordinator=coordinator, session_id=sid), paths)
    monkeypatch.setattr(app.runtime, 'resource_access', access, raising=False)
    if allowed:
        assert (await save(app, source, args))['status'] == 'saved'
        assert (tmp_path / 'saved.bin').read_bytes() == b'Original exact bytes'
    else:
        with pytest.raises(AppError, match='write access'):
            await save(app, source, args)
        assert not (tmp_path / 'saved.bin').exists()
