"""Real large files, bounded reads and existing ownership/GC/HTTP boundaries."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import threading
import time
import tracemalloc
import zipfile

import pytest

from amplifier_web import retained_files, resource_files
from amplifier_web.server import create_app
from amplifier_web.service import AppError
from test_collaborative_workspaces import app, Runtime, finish, agent_action
from test_result_artifacts import setup, declare, read
from test_integration_validation import project, validate, collect


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def wheel(path, megabytes=40):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
        with archive.open('large_fixture/data.bin', 'w') as output:
            for value in range(megabytes):
                output.write(bytes([value]) * retained_files.CHUNK)
        archive.writestr('large_fixture-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nTag: py3-none-any\n')
        archive.writestr('large_fixture-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: large_fixture\nVersion: 1.0\n')


async def large(app, tmp_path, megabytes=40):
    source, peer, path, args = await setup(app, tmp_path)
    path.unlink()
    path = path.with_name('large_fixture-1.0-py3-none-any.whl')
    wheel(path, megabytes)
    args['references'] = [path.name + '@sha256:' + digest(path)]
    return source, peer, path, args


@pytest.mark.parametrize('megabytes', [2, 40])
async def test_large_wheel_and_png_survive_gc_restart_and_verified_http_download(app, tmp_path, authenticated_client, megabytes):
    source, peer, path, args = await large(app, tmp_path, megabytes)
    png = path.with_name('evidence.png')
    png.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX7sAAAAASUVORK5CYII='))
    args['references'].append(png.name + '@sha256:' + digest(png))
    size, expected = path.stat().st_size, digest(path)
    tracemalloc.start()
    started = time.perf_counter()
    try:
        result = (await declare(app, peer, args))['result']
        value = await read(app, source, args)
        _, peak = tracemalloc.get_traced_memory()
        capture_seconds = time.perf_counter() - started
    finally:
        tracemalloc.stop()
    assert peak < 32 * 1024 * 1024, peak
    assert size > megabytes * 1024 * 1024 and value['binary']
    assert result['artifactVerification'] == 'verified'
    assert [item['status'] for item in result['artifacts']] == ['retained', 'retained']
    assert not result['qualified']
    await finish(app, peer, args['requestId'])
    record = app.outputs.store.read(value['outputId'])
    manifest = app.state_resource(record['body']['$resource'])
    protected = {record['body']['$resource'], *(row['$resource'] for row in manifest['chunks'])}
    assert not protected.intersection(resource_files.collect(app.db, app.state))
    path.unlink(); png.unlink()
    app._save_changes()
    await app.close()
    runtime = Runtime()
    server = await create_app(app.data_dir, workspace=tmp_path, runtime=runtime,
        voice=False, background_updates=False, preload_providers=False)
    reopened = runtime.app = server['service']
    assert (await read(reopened, reopened._session(source['id']), args))['qualified']
    client = await authenticated_client(server)
    token = client.session.headers.pop('Authorization')
    try:
        refused = await client.get(value['downloadUrl'])
        assert refused.status == 401
        await refused.read()
    finally:
        client.session.headers['Authorization'] = token
    tracemalloc.start()
    started = time.perf_counter()
    try:
        response = await client.get(value['downloadUrl'])
        assert response.status == 200, await response.text()
        assert int(response.headers['Content-Length']) == size
        actual, received = hashlib.sha256(), 0
        async for block in response.content.iter_chunked(65536):
            actual.update(block); received += len(block)
        _, download_peak = tracemalloc.get_traced_memory()
        download_seconds = time.perf_counter() - started
    finally:
        tracemalloc.stop()
    assert download_peak < 32 * 1024 * 1024, download_peak
    assert actual.hexdigest() == expected and received == size
    assert response.headers.get('Cache-Control') == 'no-store'
    assert response.headers.get('X-Content-Type-Options') == 'nosniff'
    assert response.headers.get('Referrer-Policy') == 'no-referrer'
    print(json.dumps({'fileBytes': size, 'captureAndPreviewPeakBytes': peak,
        'captureAndPreviewSeconds': capture_seconds, 'downloadPeakBytes': download_peak,
        'downloadSeconds': download_seconds, 'sha256': expected}))


async def test_large_unicode_preview_is_bounded_and_keeps_character_offsets(app, tmp_path):
    source, peer, path, args = await setup(app, tmp_path)
    unit = 'ab🙂cd\n'
    with path.open('w') as stream:
        for _ in range(20):
            stream.write(unit * 131073)
    args['references'] = [path.name + '@sha256:' + digest(path)]
    result = (await declare(app, peer, args))['result']
    offset, limit = 1048573, 37
    expected = (unit * 8)[offset % len(unit):offset % len(unit) + limit]
    preview = await read(app, source, args, offset=offset, limit=limit)
    assert preview['text'] == expected and preview['nextOffset'] == offset + limit
    own = (await agent_action(app, peer, 'outputs.read', {'sessionId': peer['id'],
        'id': result['artifacts'][0]['outputId'], 'offset': offset, 'limit': limit}, 'large-preview'))['result']
    assert own['text'] == expected


async def test_retained_multi_piece_png_supports_existing_image_inspection(app, tmp_path):
    import random
    import struct
    import zlib
    source, peer, path, args = await setup(app, tmp_path)
    path.unlink()
    path = path.with_name('evidence.png')
    pixels = random.Random(1234).randbytes(1024 * 1024 * 3)
    rows = b''.join(b'\0' + pixels[offset:offset + 3072] for offset in range(0, len(pixels), 3072))
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 1024, 1024, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    expected = digest(path)
    args['references'] = ['evidence.png@sha256:' + expected]
    result = (await declare(app, peer, args))['result']
    record = app.outputs.store.read(result['artifacts'][0]['outputId'])
    assert record['bodyFormat'] == retained_files.FORMAT
    path.unlink()
    image = await app.outputs.image_async(source['id'], record['id'], expected)
    assert image['width'] == image['height'] == 1024
    assert hashlib.sha256(base64.b64decode(image['_image'])).hexdigest() == expected


@pytest.mark.parametrize('changed', ['chunk', 'manifest'])
async def test_tampered_large_file_fails_before_download_and_never_reads_source(app, tmp_path, changed):
    source, peer, path, args = await large(app, tmp_path, 17)
    result = (await declare(app, peer, args))['result']
    record = app.outputs.store.read(result['artifacts'][0]['outputId'])
    manifest = app.state_resource(record['body']['$resource'])
    identity = manifest['chunks'][-1]['$resource'] if changed == 'chunk' else record['body']['$resource']
    artifact = resource_files.root(app.db) / (identity + '.json')
    raw = artifact.read_bytes()
    artifact.write_bytes(raw.replace(b'base64', b'base63', 1) if changed == 'chunk' else raw + b' ')
    assert (await read(app, source, args))['status'] == 'unavailable'
    entered = False
    with pytest.raises(ValueError):
        async with retained_files.download(app, record):
            entered = True
    assert not entered and path.exists()


async def test_capture_cancellation_settles_worker_and_removes_scratch(app, tmp_path, monkeypatch):
    from amplifier_web import result_artifacts
    _, peer, _, args = await large(app, tmp_path, 17)
    entered, release, directories = threading.Event(), threading.Event(), []
    original = result_artifacts.prepare_files
    def blocked(*values):
        batch = original(*values)
        directories.append(Path(batch.scratch.name))
        entered.set()
        assert release.wait(5)
        return batch
    monkeypatch.setattr(result_artifacts, 'prepare_files', blocked)
    task = asyncio.create_task(declare(app, peer, args))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(not directory.exists() for directory in directories)
        assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0
        assert not app.collaboration.receipt(args['requestId']).get('response')
    finally:
        release.set()


async def test_valid_chunk_resources_still_require_original_whole_file_hash(app, tmp_path):
    source, peer, _, args = await large(app, tmp_path, 17)
    result = (await declare(app, peer, args))['result']
    record = app.outputs.store.read(result['artifacts'][0]['outputId'])
    manifest = app.state_resource(record['body']['$resource'])
    manifest['chunks'][0] = manifest['chunks'][1]
    record['body'] = resource_files.put(app.db, manifest)
    app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(record), record['id']))
    app.db.commit()
    assert (await read(app, source, args))['status'] == 'unavailable'
    with pytest.raises(ValueError, match='evidence hash'):
        async with retained_files.download(app, record):
            pytest.fail('No download body may escape before whole-file verification')


async def test_large_save_failure_rolls_back_manifest_chunks_and_current_report(app, tmp_path, monkeypatch):
    _, peer, _, args = await large(app, tmp_path, 17)
    original = app.outputs.store.create
    adopted = []
    def failed(*values, **options):
        manifest = app.state_resource(values[1]['body']['$resource'])
        adopted.extend([values[1]['body']['$resource'], *(row['$resource'] for row in manifest['chunks'])])
        original(*values, **options)
        raise OSError('injected output persistence failure')
    monkeypatch.setattr(app.outputs.store, 'create', failed)
    with pytest.raises(OSError, match='injected output'):
        await declare(app, peer, args)
    assert adopted and all(not app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone()
                           for identity in adopted)
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0
    assert not app.collaboration.receipt(args['requestId']).get('response')
    monkeypatch.setattr(app.outputs.store, 'create', original)
    assert (await declare(app, peer, args))['result']['artifactVerification'] == 'verified'


@pytest.mark.parametrize('corrupted', ['chunk', 'manifest', 'small_body'])
@pytest.mark.parametrize('indexed', [False, True])
async def test_preexisting_same_size_corruption_refuses_correction_and_preserves_prior_report(app, tmp_path, corrupted, indexed):
    from amplifier_web import result_artifacts
    from test_coordination_revisions import subscribe
    source, peer, path, args = await setup(app, tmp_path)
    args['outcome'] = 'failed'
    first = (await declare(app, peer, args))['result']
    await subscribe(app, args['requestId'])
    if corrupted == 'small_body':
        path.write_text('Corrected result with different content')
    else:
        path = path.with_name('corrected.whl')
        wheel(path, 2)
    corrected = {**args, 'expectedResponseRevision': 1, 'outcome': 'success',
                 'text': 'Correction', 'references': [path.name + '@sha256:' + digest(path)]}
    async with result_artifacts.prepare_files(path.parent, corrected['references']) as prepared:
        item, = prepared
        value = item['pieces'][0].load() if corrupted == 'chunk' else item['resource']
        encoded = value.text.encode()
        # Change valid JSON without changing its length, so size-only adoption
        # incorrectly accepts it. The immutable hash must catch the conflict.
        damaged = encoded.replace(b'base64', b'base63', 1) if corrupted == 'chunk' else encoded
        if corrupted == 'manifest':
            body = json.loads(encoded)
            body['sha256'] = 'f' * 64 if body['sha256'] != 'f' * 64 else 'e' * 64
            damaged = resource_files.prepare(body).text.encode()
        elif corrupted == 'small_body':
            damaged = encoded.replace(b'Corrected', b'Corrupted', 1)
        assert len(damaged) == len(encoded) and damaged != encoded
        if indexed:
            resource_files.put_prepared(app.db, value)
            app.db.commit()
        artifact = resource_files.root(app.db) / (value.identity + '.json')
        artifact.write_bytes(damaged)
        attempted = {item['resource'].identity, *(piece.identity for piece in item['pieces'])}
    # Keep the conflicted source referenced: unrelated background collection
    # must not legitimately remove an unreferenced fixture before adoption.
    app._state['fixtureCorruption'] = {'$resource': value.identity}
    before = {identity: encoded for identity, encoded in app.db.execute('SELECT id,value FROM state_resources')
              if identity in attempted}
    with pytest.raises(AppError, match='identity'):
        await agent_action(app, peer, 'coordination.reply', corrected, 'corrupt-correction')
    assert artifact.read_bytes() == damaged
    assert {identity: encoded for identity, encoded in app.db.execute('SELECT id,value FROM state_resources')
            if identity in attempted} == before
    assert app.collaboration.receipt(args['requestId'])['response'] == first
    assert app.collaboration.receipt('artifact-declaration')['result'] == first
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 1
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('corrupt-correction',)).fetchone()
    await finish(app, peer, args['requestId'])
    request = app.collaboration.receipt(args['requestId'])
    assert not request['response']['qualified'] and request['responseRevision'] == 1
    assert not app.collaboration.receipt(request['subscription']['continuationId'])['dependencySatisfied']
    assert (await read(app, source, args))['text'] == 'Original exact bytes'


@pytest.mark.parametrize('content', [b'', b'ok'])
async def test_valid_empty_and_tiny_files_remain_verified_and_readable(app, tmp_path, content):
    source, peer, path, args = await setup(app, tmp_path, content)
    result = (await declare(app, peer, args))['result']
    assert result['artifactVerification'] == 'verified'
    assert result['artifacts'][0]['bytes'] == len(content)
    assert not result['qualified']
    path.unlink()
    assert (await read(app, source, args))['text'] == content.decode()
    await finish(app, peer, args['requestId'])
    assert (await read(app, source, args))['qualified']


async def test_disk_pressure_keeps_report_explicitly_incomplete(app, tmp_path, monkeypatch):
    from types import SimpleNamespace
    _, peer, _, args = await large(app, tmp_path, 17)
    monkeypatch.setattr(retained_files.shutil, 'disk_usage', lambda *_: SimpleNamespace(free=1))
    result = (await declare(app, peer, args))['result']
    assert result['artifactVerification'] == 'incomplete'
    assert result['artifacts'][0]['status'] == 'unavailable'
    await finish(app, peer, args['requestId'])
    assert not app.collaboration.receipt(args['requestId'])['response']['qualified']


async def test_combined_integration_retains_more_than_sixteen_mib(project):
    app, sid, roots, _ = project
    results = []
    for index, root in enumerate(roots):
        await validate(app, sid, root, 'run-' + str(index))
        path = root / 'result.txt'
        with path.open('wb') as stream:
            for _ in range(9):
                stream.write(b'x' * retained_files.CHUNK)
        results.append({'repositoryPath': str(root), 'references': ['result.txt@sha256:' + digest(path)]})
    result = await collect(app, sid, ['run-0', 'run-1'], results=results)
    assert result['status'] == 'passed'
    assert sum(row['artifacts'][0]['bytes'] for row in result['repositories']) == 18 * retained_files.CHUNK
    assert all(row['artifacts'][0]['status'] == 'retained' for row in result['repositories'])
