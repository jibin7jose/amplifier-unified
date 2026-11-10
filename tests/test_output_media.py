"""Synthetic parser fixtures, not playback evidence or the reporter's input."""
import hashlib
import os
import struct

import pytest

from amplifier_web.output_media import (
    MAX_BYTES, MediaRangeError, byte_range, read_verified, validate_media,
)


def gif(width=1, height=1, frames=2, delays=(7, 19), loop=1):
    # Two-entry palette and a complete one-pixel LZW stream. Larger logical
    # screens intentionally exercise composited-pixel admission, not decoding.
    header = b"GIF89a" + struct.pack("<HHBBB", width, height, 0x80, 0, 0)
    palette = b"\xff\x00\x00\x00\x00\xff"
    application = b"\x21\xff\x0bNETSCAPE2.0\x03\x01" + struct.pack("<H", loop) + b"\x00"
    rows = []
    for index in range(frames):
        rows.append(b"\x21\xf9\x04\x04" + struct.pack("<H", delays[index % len(delays)]) + b"\x00\x00")
        rows.append(b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00")
    return header + palette + application + b"".join(rows) + b"\x3b"


def atom(kind, body):
    return struct.pack(">I4s", len(body) + 8, kind) + body


def mp4():
    # Minimal structural fixture; deliberately not claimed as playable video.
    tkhd = bytearray(84)
    tkhd[76:84] = struct.pack(">II", 320 << 16, 240 << 16)
    hdlr = b"\x00" * 8 + b"vide" + b"\x00" * 12
    sample = bytearray(78)
    sample[24:28] = struct.pack(">HH", 320, 240)
    stsd = b"\x00" * 4 + struct.pack(">I", 1) + atom(b"avc1", bytes(sample))
    mdia = atom(b"hdlr", hdlr) + atom(b"minf", atom(b"stbl", atom(b"stsd", stsd)))
    return atom(b"ftyp", b"isom\x00\x00\x00\x00isommp42") + atom(b"moov", atom(b"trak", atom(b"tkhd", bytes(tkhd)) + atom(b"mdia", mdia))) + atom(b"mdat", b"\x00")


def test_gif_metadata_preserves_nonuniform_delay_and_loop():
    data = gif()
    result = validate_media(data)
    assert result == {"mimeType": "image/gif", "width": 1, "height": 1,
                      "frameCount": 2, "durationMs": 260, "loopCount": 1,
                      "decodedPixelBytesEstimate": 8}
    assert data == gif()  # no rewriting or transcoding


@pytest.mark.parametrize("data", [
    b"", b"GIF89a", gif()[:-1], gif() + b"trailing",
    gif().replace(b"\x21\xf9\x04", b"\x21\xf9\x03", 1),
    gif().replace(b"\x02\x02\x44\x01\x00", b"\x01\x02\x44\x01\x00", 1),
    gif(width=0), gif(width=4097), gif(width=2048, height=2048),
    gif(frames=501), gif(width=1920, height=802, frames=88),
])
def test_gif_damaged_or_over_budget_refused(data):
    with pytest.raises(ValueError):
        validate_media(data)


def test_original_shape_estimate_admitted_without_native_memory_claim():
    result = validate_media(gif(width=1280, height=720, frames=26))
    assert result["decodedPixelBytesEstimate"] == 95_846_400
    assert result["frameCount"] == 26


def test_mp4_requires_sized_video_track_and_sample_description():
    result = validate_media(mp4())
    assert result["mimeType"] == "video/mp4"
    assert (result["width"], result["height"]) == (320, 240)
    assert (result["codedWidth"], result["codedHeight"]) == (320, 240)
    for data in (mp4()[:-1], mp4().replace(b"vide", b"soun"),
                 mp4().replace(b"avc1", b"xxxx"), atom(b"ftyp", b"isom"),
                 b"\x00\x00\x00\x00moov", b"\x00\x00\x00\x01moov"):
        with pytest.raises(ValueError):
            validate_media(data)


def test_mp4_coded_dimensions_cannot_hide_behind_small_display():
    original = mp4()
    offset = original.index(b"avc1") + 4 + 24
    for width, height in ((0, 240), (4097, 240), (2048, 2048)):
        data = original[:offset] + struct.pack(">HH", width, height) + original[offset + 4:]
        with pytest.raises(ValueError, match="dimensions"):
            validate_media(data)


def test_mp4_later_video_track_never_bypasses_validation():
    original = mp4()
    position = original.index(b"trak") - 4
    size = int.from_bytes(original[position:position + 4], "big")
    track = original[position:position + size]
    data = atom(b"ftyp", b"isom\x00\x00\x00\x00isommp42") + atom(b"moov", track + track) + atom(b"mdat", b"\x00")
    with pytest.raises(ValueError, match="one video track"):
        validate_media(data)
    oversized = bytearray(track)
    offset = oversized.index(b"avc1") + 4 + 24
    oversized[offset:offset + 4] = struct.pack(">HH", 4097, 240)
    data = atom(b"ftyp", b"isom\x00\x00\x00\x00isommp42") + atom(b"moov", track + oversized) + atom(b"mdat", b"\x00")
    with pytest.raises(ValueError, match="dimensions"):
        validate_media(data)


def test_gif_tiny_comment_blocks_have_bounded_parser_allocation():
    import tracemalloc
    original = gif()
    # Python parser overhead only, not native decoder memory qualification.
    data = original[:19] + b"\x21\xfe" + b"\x01x" * 200_000 + b"\x00" + original[19:]
    tracemalloc.start()
    try:
        result = validate_media(data)
        _, peak = tracemalloc.get_traced_memory()
        assert result["frameCount"] == 2
        assert peak < 1024 * 1024
    finally:
        tracemalloc.stop()


def test_encoded_limit_remains_media_only():
    with pytest.raises(ValueError, match="32"):
        validate_media(b"x" * (MAX_BYTES + 1))
    from amplifier_web.outputs import MAX_FILE
    assert MAX_FILE == 8 * 1024 * 1024


def test_exact_regular_file_no_symlink_even_inside_workspace(tmp_path):
    data = gif()
    path = tmp_path / "clip.gif"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    saved, name, metadata = read_verified(tmp_path, "clip.gif", digest)
    assert saved == data and name == "clip.gif" and metadata["frameCount"] == 2
    (tmp_path / "alias.gif").symlink_to(path)
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "clip.gif").write_bytes(data)
    (tmp_path / "alias-dir").symlink_to(tmp_path / "dir", target_is_directory=True)
    for value in ("alias.gif", "alias-dir/clip.gif", "../clip.gif", "."):
        with pytest.raises((ValueError, OSError)):
            read_verified(tmp_path, value, digest)
    with pytest.raises(ValueError, match="SHA"):
        read_verified(tmp_path, "clip.gif", "0" * 64)


def test_file_modified_during_read_refused(tmp_path, monkeypatch):
    path = tmp_path / "clip.gif"
    path.write_bytes(gif())
    real = os.fstat
    calls = 0

    def changed(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(gif(frames=3))
        return real(fd)

    monkeypatch.setattr(os, "fstat", changed)
    with pytest.raises(ValueError, match="changed"):
        read_verified(tmp_path, "clip.gif", hashlib.sha256(gif()).hexdigest())


def test_workspace_ancestor_symlink_is_not_followed(tmp_path):
    parent = tmp_path / "real"
    workspace = parent / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "clip.gif").write_bytes(gif())
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    with pytest.raises(OSError):
        read_verified(alias / "workspace", "clip.gif", hashlib.sha256(gif()).hexdigest())


@pytest.mark.parametrize("header, expected", [
    (None, None), ("bytes=2-4", (2, 4)), ("bytes=2-", (2, 9)),
    ("bytes=-3", (7, 9)), ("bytes=-30", (0, 9)), ("bytes=0-99", (0, 9)),
])
def test_single_exact_byte_range(header, expected):
    assert byte_range(header, 10) == expected


@pytest.mark.parametrize("header, status", [
    ("bytes=10-", 416), ("bytes=4-2", 416), ("bytes=-0", 416),
    ("bytes=0-1,3-4", 400), ("items=0-1", 400), ("bytes=-", 400),
    ("bytes= 0-1", 400), ("bytes=" + "9" * 100 + "-", 400),
])
def test_range_refusal_policy_explicit(header, status):
    with pytest.raises(MediaRangeError) as error:
        byte_range(header, 10)
    assert error.value.status == status


@pytest.fixture
async def media_app(tmp_path):
    from amplifier_web.service import AppService
    from test_service import Runtime
    app = AppService(tmp_path / 'app', workspace=tmp_path, runtime=Runtime())
    await app.dispatch('session.create', {'title': 'Synthetic media'})
    sid = app._session()['id']
    # Production event handling allocates the ID and commits assistant role.
    await app.on_runtime_event('assistant.message', {
        'sessionId': sid, 'text': 'Retain this animation.', 'generationId': 'fixture-generation',
    })
    try:
        yield app
    finally:
        for queue in list(app.queue_clients):
            app.unsubscribe(queue)
        await app.close()


def media_args(app):
    from pathlib import Path
    session = app._session()
    data = gif()
    (Path(session['workspace']) / 'clip.gif').write_bytes(data)
    return {'sessionId': session['id'], 'messageId': session['messages'][-1]['id'],
            'title': 'Synthetic animation', 'path': 'clip.gif',
            'expectedSha256': hashlib.sha256(data).hexdigest()}


async def test_media_actual_assistant_commit_receipt_revision_and_source_removal(media_app):
    from pathlib import Path
    args = media_args(media_app)
    record = (await media_app.dispatch('outputs.attachMedia', args, command_id='media-one'))['result']
    session = media_app._session()
    assert session['mediaRevision'] == 1
    assert 'path' not in record and 'data' not in record and record['media']['frameCount'] == 2
    Path(session['workspace'], 'clip.gif').unlink()
    duplicate = (await media_app.dispatch('outputs.attachMedia', args, command_id='media-one'))['result']
    assert duplicate['id'] == record['id'] and duplicate['duplicate']
    assert session['mediaRevision'] == 1
    media_app.clients.attach('media-client')
    with media_app.clients.bind('media-client'):
        descriptors = await media_app.outputs.media.describe(args['sessionId'], args['messageId'])
        assert descriptors['items'][0]['id'] == record['id']
        tuple_args = {**descriptors['items'][0], 'sessionId': args['sessionId'], 'messageId': args['messageId']}
        assert await media_app.outputs.media.bytes(tuple_args) == gif()
    await media_app.dispatch('outputs.unlink', {'sessionId': args['sessionId'], 'id': record['id'], 'expectedRevision': 1})
    assert session['mediaRevision'] == 2
    with media_app.clients.bind('media-client'):
        with pytest.raises(ValueError, match='linked'):
            await media_app.outputs.media.bytes(tuple_args)


async def test_media_role_unknown_foreign_hash_and_command_forgery(media_app):
    from amplifier_web.service import AppError
    args = media_args(media_app)
    with pytest.raises(AppError, match='assistant'):
        await media_app.dispatch('outputs.attachMedia', {**args, 'messageId': 'unknown'})
    await media_app.on_runtime_event('assistant.message', {
        'sessionId': args['sessionId'], 'text': 'Later message.', 'generationId': 'later-generation',
    })
    # Older saved assistant messages are explicit valid targets.
    await media_app.dispatch('outputs.attachMedia', args, command_id='original')
    with pytest.raises(AppError, match='different'):
        await media_app.dispatch('outputs.attachMedia', {**args, 'expectedSha256': '0' * 64}, command_id='original')
    with pytest.raises(AppError, match='SHA'):
        await media_app.dispatch('outputs.attachMedia', {**args, 'expectedSha256': '0' * 64})
    with pytest.raises(AppError):
        await media_app.dispatch('outputs.attachMedia', {**args, 'sourceGenerationId': 'forged'})
    media_app._session()['messages'][0]['role'] = 'user'
    with pytest.raises(AppError, match='assistant'):
        await media_app.dispatch('outputs.attachMedia', args)


async def test_media_role_recheck_after_worker_read(media_app, monkeypatch):
    import asyncio
    import threading
    from amplifier_web import output_media
    from amplifier_web.service import AppError
    args = media_args(media_app)
    entered, release = threading.Event(), threading.Event()
    real = output_media.read_verified

    def blocked(*values):
        entered.set()
        assert release.wait(5)
        return real(*values)

    monkeypatch.setattr(output_media, 'read_verified', blocked)
    task = asyncio.create_task(media_app.dispatch('outputs.attachMedia', args))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        async with media_app.lock:
            media_app._session()['messages'][0]['role'] = 'user'
        release.set()
        with pytest.raises(AppError, match='assistant'):
            await task
        assert media_app.outputs.store.list(args['sessionId'])['items'] == []
    finally:
        release.set()
        if not task.done():
            await task


async def test_media_cached_bytes_detect_corruption_and_client_scope(media_app):
    from pathlib import Path
    from amplifier_web.resource_files import root
    args = media_args(media_app)
    record = (await media_app.dispatch('outputs.attachMedia', args))['result']
    media_app.clients.attach('scope-one')
    media_app.clients.attach('scope-two')
    values = {'sessionId': args['sessionId'], 'messageId': args['messageId'],
              'id': record['id'], 'version': record['version'], 'sha256': record['sha256']}
    with media_app.clients.bind('scope-one'):
        assert await media_app.outputs.media.bytes(values) == gif()
        assert await media_app.outputs.media.bytes(values) == gif()
        internal = media_app.outputs.record(args['sessionId'], record['id'])
        assert 'body' not in record  # Never widen a public receipt to fix a fixture.
        path = root(media_app.db) / (internal['body']['$resource'] + '.json')
        path.write_text('{"encoding":"base64","data":"corrupt"}')
        with pytest.raises(ValueError, match='identity'):
            await media_app.outputs.media.bytes(values)
    media_app.clients.records['scope-two']['selectedSessionId'] = None
    with media_app.clients.bind('scope-two'):
        with pytest.raises(ValueError, match='client'):
            await media_app.outputs.media.bytes(values)


@pytest.mark.parametrize('in_memory', [False, True])
async def test_warm_cache_never_bypasses_changed_index(tmp_path, in_memory):
    import sqlite3
    from types import SimpleNamespace
    import asyncio
    from amplifier_web.output_media import Media, _prepare_media
    from amplifier_web.resource_files import put_prepared_async, root
    db = sqlite3.connect(':memory:' if in_memory else tmp_path / 'index.sqlite3')
    db.execute('CREATE TABLE state_resources(id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    session = {'id': 'own', 'workspace': 'synthetic'}
    app = SimpleNamespace(db=db, lock=asyncio.Lock(), _session=lambda sid, **kwargs: session)
    media = Media(SimpleNamespace(app=app))
    body = await put_prepared_async(db, _prepare_media(gif()))
    db.commit()
    record = {'body': body, 'sha256': hashlib.sha256(gif()).hexdigest()}
    media.guard = lambda values, proof=None: record

    async def indexed_identity(*args, **kwargs):
        return {'fact': ('saved-assistant', 'assistant'), 'snapshot': session}

    media.identity = indexed_identity
    values = {'sessionId': 'own', 'messageId': 'saved-assistant'}
    try:
        assert await media.bytes(values) == gif()
        before = None if in_memory else (root(db) / (body['$resource'] + '.json')).stat()
        for wrong in ['{"$blob":"' + '0' * 64 + '"}', '{}', '[]', 'null',
                      '{"$blob":"' + body['$resource'] + '","foreign":true}']:
            db.execute('UPDATE state_resources SET value=? WHERE id=?', (wrong, body['$resource']))
            db.commit()
            with pytest.raises(ValueError, match='index|identity'):
                await media.bytes(values)
            if before is not None:
                assert (root(db) / (body['$resource'] + '.json')).stat() == before
    finally:
        db.close()


async def test_actual_large_media_publication_when_maintenance_is_due(media_app, monkeypatch):
    import base64
    import io
    import threading
    from pathlib import Path
    from amplifier_web import resource_files as files
    from amplifier_web.host.storage import SessionStore
    app = media_app
    task = getattr(app, '_storage_maintenance_task', None)
    if task:
        await task
    args = media_args(app)
    # Valid large synthetic comment payload. Not native decoding evidence.
    data = gif()[:-1] + b'\x21\xfe' + (b'\xff' + b'x' * 255) * 75_000 + b'\x00\x3b'
    Path(app._session()['workspace'], 'clip.gif').write_bytes(data)
    args['expectedSha256'] = hashlib.sha256(data).hexdigest()
    expected = files.prepare({'encoding': 'base64', 'data': base64.b64encode(data).decode('ascii')})
    stale = files.put(app.db, {'data': 'actually unreferenced'})['$resource']
    leaf = files.put(app.db, {'data': 'nested retained evidence'})
    parent = files.put(app.db, {'nested': [leaf]})
    app._session()['artifactRefs'] = [parent]
    app._save()
    app.db.commit()
    owner = threading.get_ident()
    reads, writes, json_threads, dump_threads, sql_threads, small_writes = [], [], [], [], [], []
    real_open, real_io_open, real_loads, real_dumps = files.os.open, io.open, files.json.loads, files.json.dumps
    real_atomic = SessionStore._atomic
    directory = files.root(app.db)
    large_path = directory / (expected.identity + '.json')

    def opened(path, *values, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(path) == large_path:
            reads.append((str(path), threading.get_ident()))
        return real_open(path, *values, **kwargs)

    def io_opened(path, *values, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(path) == large_path:
            reads.append((str(path), threading.get_ident()))
        return real_io_open(path, *values, **kwargs)

    def atomic(path, text):
        # Observe the atomic large write by final filename AND canonical content.
        # Temporary-file opens and ordinary small state publication are distinct.
        if Path(path) == large_path:
            assert text == expected.text
            writes.append((str(path), len(text.encode()), threading.get_ident()))
        elif Path(path).parent == directory:
            small_writes.append((Path(path).name, len(text.encode()), threading.get_ident()))
        return real_atomic(path, text)

    def loaded(text, *values, **kwargs):
        encoded = text.encode() if isinstance(text, str) else text
        if hashlib.sha256(encoded).hexdigest() == expected.identity:
            json_threads.append(threading.get_ident())
        return real_loads(text, *values, **kwargs)

    def dumped(value, *values, **kwargs):
        text = real_dumps(value, *values, **kwargs)
        if len(text) == len(expected.text) and hashlib.sha256(text.encode()).hexdigest() == expected.identity:
            dump_threads.append(threading.get_ident())
        return text

    monkeypatch.setattr(files.os, 'open', opened)
    monkeypatch.setattr(io, 'open', io_opened)
    monkeypatch.setattr(files.json, 'loads', loaded)
    monkeypatch.setattr(files.json, 'dumps', dumped)
    monkeypatch.setattr(SessionStore, '_atomic', staticmethod(atomic))
    app.db.set_trace_callback(lambda sql: sql_threads.append(threading.get_ident()))
    app._last_storage_sweep = 0
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    await app._storage_maintenance_task
    internal = app.outputs.record(args['sessionId'], record['id'])
    assert internal['body'] == {'$resource': expected.identity, 'bytes': expected.size}
    assert large_path.stat().st_size == expected.size > 19_000_000
    assert record['bytes'] == len(data) and record['sha256'] == hashlib.sha256(data).hexdigest()
    assert app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (internal['body']['$resource'],)).fetchone()
    assert not app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (stale,)).fetchone()
    assert not (directory / (stale + '.json')).exists()
    for reference in (parent, leaf):
        assert app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (reference['$resource'],)).fetchone()
        assert (directory / (reference['$resource'] + '.json')).is_file()
    assert json_threads and owner not in json_threads
    assert dump_threads and owner not in dump_threads
    assert reads and all(thread != owner for _, thread in reads)
    assert writes and all(thread != owner for _, _, thread in writes)
    # Explicit counter-witness: owner-thread metadata publication is allowed,
    # but never classified as off-owner large media IO.
    assert small_writes and any(thread == owner for _, _, thread in small_writes)
    assert all(size < 1024 * 1024 for _, size, _ in small_writes)
    assert sql_threads and set(sql_threads) == {owner}
    assert app._resource_scan_status['blocked'] is False
    app.db.set_trace_callback(None)


async def test_media_http_auth_head_exact_ranges_download_and_no_unscoped_bypass(authenticated_client, tmp_path, monkeypatch):
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, preload_providers=False, background_updates=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'HTTP media'})
    sid = service._session()['id']
    await service.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'Animation.'})
    args = media_args(service)
    record = (await service.dispatch('outputs.attachMedia', args))['result']
    service.clients.attach('http-media')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'http-media'
    query = {'sessionId': sid, 'messageId': args['messageId'], 'version': str(record['version']), 'sha256': record['sha256']}
    path = '/api/outputs/' + record['id'] + '/media'
    real_bytes = service.outputs.media.bytes

    async def forbidden_body(*args):
        pytest.fail('HEAD opened/decoded the media body')

    monkeypatch.setattr(service.outputs.media, 'bytes', forbidden_body)
    response = await client.head(path, params=query)
    assert response.status == 200 and await response.read() == b''
    assert int(response.headers['Content-Length']) == len(gif())
    ranged_head = await client.head(path, params=query, headers={'Range': 'bytes=2-4'})
    assert ranged_head.status == 206 and await ranged_head.read() == b''
    assert ranged_head.headers['Content-Length'] == '3'
    monkeypatch.setattr(service.outputs.media, 'bytes', real_bytes)
    for header, expected, bounds in [('bytes=2-4', gif()[2:5], (2, 4)),
                                     ('bytes=2-', gif()[2:], (2, len(gif()) - 1)),
                                     ('bytes=-3', gif()[-3:], (len(gif()) - 3, len(gif()) - 1))]:
        response = await client.get(path, params=query, headers={'Range': header})
        assert response.status == 206 and await response.read() == expected
        assert response.headers['Content-Length'] == str(len(expected))
        assert response.headers['Content-Range'] == f'bytes {bounds[0]}-{bounds[1]}/{len(gif())}'
    for header, code in [('bytes=99999-', 416), ('bytes=0-1,3-4', 400), ('bytes=-', 400)]:
        response = await client.get(path, params=query, headers={'Range': header})
        assert response.status == code
        if code == 416:
            assert response.headers['Content-Range'] == 'bytes */' + str(len(gif()))
    response = await client.get(path, params={**query, 'download': '1'})
    assert response.status == 200 and await response.read() == gif()
    assert response.headers['Content-Disposition'].startswith('attachment;')
    assert (await client.get('/api/outputs/' + record['id'] + '/content')).status == 403
    from amplifier_web import output_media
    real_guard = service.outputs.media.guarded
    rejected = []

    async def witnessed_guard(values):
        try:
            return await real_guard(values)
        except ValueError as error:
            rejected.append(str(error))
            raise

    async def forbidden_rejected_body(*args):
        pytest.fail('A rejected tuple opened/decoded the media body')

    def forbidden_rejected_range(*args):
        pytest.fail('A rejected tuple reached Range parsing')

    monkeypatch.setattr(service.outputs.media, 'guarded', witnessed_guard)
    monkeypatch.setattr(service.outputs.media, 'bytes', forbidden_rejected_body)
    monkeypatch.setattr(output_media, 'byte_range', forbidden_rejected_range)
    for key, value in [('sessionId', 'foreign'), ('messageId', 'foreign'), ('version', '99'), ('sha256', '0' * 64)]:
        rejected.clear()
        wrong = await client.get(path, params={**query, key: value}, headers={'Range': 'malformed'})
        assert wrong.status == 400  # Existing boundaries middleware maps ValueError to 400.
        assert len(rejected) == 1
        assert await wrong.json() == {'accepted': False, 'error': 'Invalid request: ' + rejected[0]}
    rejected.clear()
    client.session.headers['Authorization'] = ''
    client.session.cookie_jar.clear()
    response = await client.get(path, params=query, headers={'Range': 'malformed'}, allow_redirects=False)
    assert response.status in {401, 403}
    assert rejected == []  # Authentication fails before tuple authority, body or Range.


async def test_media_report_origin_exact_tuple_and_monotonic_order(media_app):
    from amplifier_web.service import AppError
    args = media_args(media_app)
    record = (await media_app.dispatch('outputs.attachMedia', args))['result']
    media_app.clients.attach('reported-client')
    values = {'sessionId': args['sessionId'], 'messageId': args['messageId'], 'id': record['id'],
              'version': record['version'], 'sha256': record['sha256'],
              'generation': 1, 'sequence': 1, 'renderState': 'loading', 'playback': 'paused'}
    with media_app.clients.bind('reported-client'):
        queue = media_app.subscribe()
        with pytest.raises(AppError, match='UI'):
            await media_app.dispatch('outputs.mediaReport', values, origin='agent',
                                     caller_session_id=args['sessionId'])
        await media_app.dispatch('outputs.mediaReport', values, origin='ui')
        with pytest.raises(AppError, match='stale'):
            await media_app.dispatch('outputs.mediaReport', values, origin='ui')
        await media_app.dispatch('outputs.mediaReport', {**values, 'sequence': 2, 'renderState': 'ready', 'playback': 'playing'}, origin='ui')
        result = (await media_app.dispatch('outputs.read', {'sessionId': args['sessionId'], 'id': record['id']}))['result']
        assert result['saved'] and result['attached'] and result['playback'] == 'playing'
        assert result['browserObservation']['evidence'] == 'authenticated-ui-report'
        assert result['browserObservation']['modelAttestation'] is False
        media_app.unsubscribe(queue)
        disconnected = (await media_app.dispatch('outputs.read', {'sessionId': args['sessionId'], 'id': record['id']}))['result']
        assert disconnected['playback'] == 'unknown'
        assert disconnected['browserObservation']['status'] == 'disconnected'
        assert 'presentedFrames' not in disconnected['browserObservation']


async def test_media_report_expiry_unbound_and_agent_read_are_not_never_loaded(media_app, monkeypatch):
    from amplifier_web import output_media
    app = media_app
    args = media_args(app)
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    app.clients.attach('observer-one')
    app.clients.attach('observer-two')
    values = {'sessionId': args['sessionId'], 'messageId': args['messageId'], 'id': record['id'],
              'version': record['version'], 'sha256': record['sha256'], 'generation': 10, 'sequence': 1,
              'renderState': 'ready', 'playback': 'paused', 'presentedFrames': 1, 'mediaTime': 0}
    internal = app.outputs.record(args['sessionId'], record['id'])
    with app.clients.bind('observer-one'):
        queue = app.subscribe()
        await app.dispatch('outputs.mediaReport', values, origin='ui')
        result = await app.app_bridge('dispatch', {'action': 'outputs.read',
            'args': {'id': record['id']}, '_inputClients': ['observer-one']}, args['sessionId'])
        assert result['result']['browserObservation']['status'] == 'observed'
        assert result['result']['browserObservation']['presentedFrames'] == 1
        stamp = result['result']['browserObservation']['serverTimestamp']
        monkeypatch.setattr(output_media.time, 'time', lambda: stamp + 31)
        expired = app.outputs.media.observation(internal)
        assert expired['status'] == 'expired' and 'playback' not in expired
        app.unsubscribe(queue)
    with app.clients.bind('observer-two'):
        assert app.outputs.media.observation(internal)['status'] == 'not_observed'
        app.clients.records['observer-two']['selectedSessionId'] = None
        assert app.outputs.media.observation(internal)['status'] == 'unbound'
    assert app.outputs.media.observation(internal)['status'] == 'unbound'


async def test_media_unlinked_workspace_restart_preserves_exact_retained_bytes(media_app):
    import copy
    from pathlib import Path
    from amplifier_web.service import AppService
    from test_service import Runtime
    app = media_app
    args = media_args(app)
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    sid = args['sessionId']
    messages = copy.deepcopy(app._session(sid)['messages'])
    Path(app._session(sid)['workspace'], args['path']).unlink()
    directory, workspace = app.data_dir, app._session(sid)['workspace']
    await app.close()
    reopened = AppService(directory, workspace=workspace, runtime=Runtime())
    try:
        reopened.clients.attach('restart-media')
        with reopened.clients.bind('restart-media'):
            await reopened.dispatch('session.select', {'id': sid})
            items = (await reopened.outputs.media.describe(sid, args['messageId']))['items']
            assert items[0]['sha256'] == record['sha256']
            assert await reopened.outputs.media.bytes({**items[0], 'messageId': args['messageId']}) == gif()
            assert reopened.outputs.media.observation(reopened.outputs.record(sid, record['id']))['status'] == 'not_observed'
        assert reopened._session(sid)['messages'] == messages
    finally:
        await reopened.close()


async def test_explicit_fork_keeps_assistant_media_origin_and_canonical_context(media_app):
    import copy
    from amplifier_web.host.storage import SessionStore
    app = media_app
    args = media_args(app)
    sid = args['sessionId']
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    # Native history is a normal saved context, not an invented UI message ID.
    store = SessionStore.for_app(app.data_dir, app._session(sid)['workspace'])
    native = [{'role': 'system', 'content': 'Keep system context.'},
              {'role': 'user', 'content': 'Keep this animation.'},
              {'role': 'assistant', 'content': app._session(sid)['messages'][-1]['text']}]
    store.save(sid, native, {}, preserve_system=True)
    raw = (store.directory(sid) / 'transcript.jsonl').read_bytes()
    messages = copy.deepcopy(app._session(sid)['messages'])
    await app.dispatch('session.fork', {'id': sid})
    fork = app._session()
    rows = app.outputs.store.list(fork['id'])['items']
    media = next(row for row in rows if row['kind'] == 'media')
    assert media['forkSource'] == {'sessionId': sid, 'outputId': record['id'], 'sha256': record['sha256']}
    assert media['origin']['messageId'] == args['messageId']
    assert media['sha256'] == record['sha256'] and media['id'] != record['id']
    assert store.load(fork['id'])[0] == native
    assert (store.directory(sid) / 'transcript.jsonl').read_bytes() == raw
    assert app._session(sid)['messages'] == messages
    app.clients.attach('fork-media')
    with app.clients.bind('fork-media'):
        await app.dispatch('session.select', {'id': fork['id']})
        assert await app.outputs.media.bytes({**media, 'messageId': args['messageId']}) == gif()
        with pytest.raises(ValueError):
            await app.outputs.media.bytes({**media, 'sessionId': sid, 'messageId': args['messageId']})


def canonical_corpus(app):
    """Real Foundation transcript corpus, not normal-turn delivery evidence."""
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_files import project_slug
    from amplifier_web.automatic_history import directory, read_transcript, display_message
    session = app._session()
    corpus = [{'role': 'user' if i % 2 == 0 else 'assistant',
               'content': f'Canonical message {i}: ' + ('**雪** [sample](https://example.org)\n' * 1200 if i == 1 else 'saved')}
              for i in range(240)]
    store = SessionStore.for_app(app.data_dir, session['workspace'])
    store.save(session['id'], corpus, {
        'bundle_name': session['bundle'], 'working_dir': session['workspace']}, preserve_system=True)
    session.update(nativeProject=project_slug(session['workspace']), nativeIdentity=session['id'],
                   runtimeSessionId=session['id'], historyManaged=True, status='idle')
    page = read_transcript(session)
    session.update(messages=page['messages'], nativeRevision=page['revision'],
                   sharedHistoryOffset=page['offset'], sharedHistoryTotal=page['total'],
                   sharedHistoryUserTurnOffset=page['userOffset'])
    assert page['total'] == 240 and len(session['messages']) == 100
    old = display_message(corpus[1], 1, session)['id']
    assert old not in {row['id'] for row in session['messages']}
    return session, corpus, store, directory(session), old


def invalidate_media_indexes(monkeypatch, change, *, fail_at=(1,), boundary='before-projection'):
    """Invalidate real Foundation indexes; never manufacture the retry error."""
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.jsonl import TranscriptIndex
    acquired, conflicts = [], []
    real_index, real_project, real_check = (
        SessionHistoryStore.indexed_messages, TranscriptIndex.project, TranscriptIndex.check)

    def indexed(reader):
        rows = real_index(reader)
        assert isinstance(rows, TranscriptIndex)
        acquired.append(rows)
        if len(acquired) in fail_at and boundary != 'during-projection':
            change(len(acquired))
            if boundary == 'acquisition':
                rows.check()
        return rows

    def project(rows, key, projector):
        changed = False

        def racing(value, position):
            nonlocal changed
            fact = projector(value, position)
            if not changed:
                changed = True
                change(len(acquired))
            return fact

        return real_project(rows, key, racing if (
            boundary == 'during-projection' and len(acquired) in fail_at) else projector)

    def check(rows):
        try:
            return real_check(rows)
        except ValueError as exc:
            conflicts.append(exc)
            raise

    monkeypatch.setattr(SessionHistoryStore, 'indexed_messages', indexed)
    monkeypatch.setattr(TranscriptIndex, 'project', project)
    monkeypatch.setattr(TranscriptIndex, 'check', check)
    return acquired, conflicts


def append_media_unrelated(directory, attempt):
    import json
    with (directory / 'transcript.jsonl').open('ab') as stream:
        stream.write(json.dumps({'role': 'user', 'content': f'Unrelated append {attempt}.'}).encode() + b'\n')


@pytest.mark.parametrize('boundary', ['acquisition', 'before-projection', 'during-projection'])
@pytest.mark.parametrize('shape', ['canonical', 'cold-alias'])
async def test_media_index_conflict_recaptures_fresh_proof_and_warm_descriptor(
        media_app, monkeypatch, boundary, shape):
    import asyncio
    import copy
    import threading
    from amplifier_foundation.session import history as foundation_history
    from amplifier_foundation.session.jsonl import TranscriptIndex
    from amplifier_web import automatic_history, media_message_identity, media_saved_aliases
    app = media_app
    task = getattr(app, '_storage_maintenance_task', None)
    if task:
        await task
    if shape == 'cold-alias':
        session, corpus, _, directory, old = cold_alias_corpus(app)
        session, _ = await freeze_alias_display(app, session)
        requested = 'saved-web-assistant'
    else:
        session, corpus, _, directory, old = canonical_corpus(app)
        requested = old
    resident = copy.deepcopy(dict.get(session, 'messages'))
    initial = automatic_history.revision(session)
    owner = threading.get_ident()
    revisions, hashes, proofs = [], [], []
    real_revision, real_hash, real_resolve = (
        automatic_history.revision, automatic_history.display_identity, media_message_identity.resolve)

    def revision(value):
        assert threading.get_ident() != owner
        result = real_revision(value)
        revisions.append(result)
        return result

    def display_identity(*args):
        assert threading.get_ident() != owner
        hashes.append(threading.get_ident())
        return real_hash(*args)

    def resolve(*args):
        proof = real_resolve(*args)
        proofs.append(proof)
        return proof

    def no_bodies(*args, **kwargs):
        pytest.fail('Media read/hydrated whole transcript or display bodies')

    acquired, conflicts = invalidate_media_indexes(
        monkeypatch, lambda attempt: append_media_unrelated(directory, attempt), boundary=boundary)
    monkeypatch.setattr(automatic_history, 'revision', revision)
    monkeypatch.setattr(automatic_history, 'display_identity', display_identity)
    monkeypatch.setattr(media_message_identity, 'resolve', resolve)
    monkeypatch.setattr(automatic_history, 'read_transcript', no_bodies)
    monkeypatch.setattr(TranscriptIndex, 'read_positions', no_bodies)
    monkeypatch.setattr(app.cold_display, 'hydrate', no_bodies)
    app.clients.attach('index-recovery-browser')
    with app.clients.bind('index-recovery-browser'):
        assert (await app.outputs.media.describe(session['id'], requested))['items'] == []
        assert len(acquired) == 2 and acquired[0] is not acquired[1]
        assert len(conflicts) == 1
        assert type(conflicts[0]) is ValueError
        assert conflicts[0].args == ('Session history changed during indexed read',)
        fresh = await asyncio.to_thread(real_revision, session)
        appended_id = await asyncio.to_thread(real_hash, session, 240, 'user', 'Unrelated append 1.')
        assert revisions[:2] == [initial, fresh] and initial != fresh
        proof = proofs[0]
        assert proof['revision'] == fresh
        assert proof['fact'] == (old, 'assistant', True, 1, None, None)
        assert len(proof['canonicalIds']) == 241 and appended_id in proof['canonicalIds']
        assert proof['snapshot']['nativeRevision'] == initial
        # Canonical and detached display caches remain lazy on the next lookup.
        with monkeypatch.context() as warm:
            warm.setattr(foundation_history, '_decode', no_bodies)
            warm.setattr(media_saved_aliases.DisplayJSON, 'string', no_bodies)
            assert (await app.outputs.media.describe(session['id'], requested))['items'] == []
        assert len(acquired) == 3 and acquired[2] is acquired[1] and len(conflicts) == 1
        assert proofs[1] == proof
    assert hashes and owner not in hashes
    assert dict.get(session, 'messages') == resident
    assert corpus[1]['role'] == 'assistant'


@pytest.mark.parametrize('boundary', ['descriptor', 'attach', 'final-attach', 'final-comparison'])
async def test_media_index_conflict_budget_and_attachment_publication_fail_closed(
        media_app, monkeypatch, boundary):
    app = media_app
    task = getattr(app, '_storage_maintenance_task', None)
    if task:
        await task
    session, _, _, directory, old = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': old}
    tables = ('output_records', 'output_receipts', 'state_resources')
    before = [app.db.execute(f'SELECT * FROM {table} ORDER BY id').fetchall() for table in tables]
    initial_revision = session.get('mediaRevision', 0)
    publications, writes = [], []
    real_publish = app._publish_changes

    def publish(*args, **kwargs):
        publications.append((args, kwargs))
        return real_publish(*args, **kwargs)

    fail_at = (3,) if boundary == 'final-comparison' else (
        (3, 4) if boundary == 'final-attach' else (1, 2))
    acquired, conflicts = invalidate_media_indexes(
        monkeypatch, lambda attempt: append_media_unrelated(directory, attempt), fail_at=fail_at)
    monkeypatch.setattr(app, '_publish_changes', publish)
    if boundary.startswith('final-'):
        from amplifier_web import resource_files
        real_put = resource_files.put_prepared_async

        async def put(db, prepared):
            assert app.lock.locked() and db.in_transaction
            result = await real_put(db, prepared)
            writes.append(result)
            return result

        monkeypatch.setattr(resource_files, 'put_prepared_async', put)
    app.clients.attach('index-refusal-browser')
    with app.clients.bind('index-refusal-browser'):
        with pytest.raises(ValueError) as error:
            if boundary == 'descriptor':
                await app.outputs.media.describe(session['id'], old)
            else:
                await app.outputs.media.attach(args, 'ui', 'index-refusal-attach')
    assert len(acquired) == (4 if boundary.startswith('final-') else 2)
    assert len(conflicts) == len(fail_at)
    assert acquired[-1] is not acquired[-2]
    assert all(type(exc) is ValueError and exc.args == (
        'Session history changed during indexed read',) for exc in conflicts)
    if boundary == 'final-comparison':
        assert 'identity changed before media commit' in str(error.value)
    else:
        assert error.value is conflicts[-1]
    assert bool(writes) == boundary.startswith('final-')
    assert publications == [] and not app.db.in_transaction
    assert session.get('mediaRevision', 0) == initial_revision
    assert [app.db.execute(f'SELECT * FROM {table} ORDER BY id').fetchall() for table in tables] == before


@pytest.mark.parametrize('defect', [
    'remove', 'replace', 'role', 'observation', 'duplicate-claimant', 'namespace-collision', 'corrupt-resource',
])
@pytest.mark.parametrize('boundary', ['descriptor', 'attach'])
async def test_media_index_retry_never_rescues_unsafe_second_snapshot(
        media_app, monkeypatch, defect, boundary):
    from amplifier_web.automatic_history import display_message
    app = media_app
    task = getattr(app, '_storage_maintenance_task', None)
    if task:
        await task
    session, corpus, store, directory, old = canonical_corpus(app)
    claimant = {**display_message(corpus[1], 1, session),
                'id': 'retained-index-assistant', 'source': 'amplifier', 'nativeMessageId': old}
    if defect == 'corrupt-resource':
        from amplifier_web.resource_files import root
        session.update(historyManaged=False, messages=[claimant, *session['messages']])
        session, reference = await freeze_alias_display(app, session)
        path = root(app.db) / (reference['$resource'] + '.json')
    args = {**media_args_without_hydration(session), 'messageId': old}
    records = app.db.execute('SELECT * FROM output_records ORDER BY id').fetchall()
    receipts = app.db.execute('SELECT * FROM output_receipts ORDER BY id').fetchall()
    publications = []
    real_publish = app._publish_changes

    def publish(*args, **kwargs):
        publications.append((args, kwargs))
        return real_publish(*args, **kwargs)

    def change(attempt):
        if defect in ('remove', 'replace', 'role', 'observation'):
            if defect == 'remove':
                del corpus[1]
            elif defect == 'replace':
                corpus[1]['content'] = 'A different saved assistant.'
            elif defect == 'role':
                corpus[1]['role'] = 'user'
            else:
                corpus[1]['metadata'] = {'amplifier_input': {
                    'version': 1, 'kind': 'service', 'id': 'synthetic-observation', 'source': 'local'}}
            store.save(session['id'], corpus, {}, preserve_system=True)
        else:
            append_media_unrelated(directory, attempt)
            if defect == 'duplicate-claimant':
                session['messages'].extend([claimant, {**claimant, 'id': 'competing-index-assistant'}])
            elif defect == 'namespace-collision':
                session['messages'].append({**claimant, 'id': display_message(corpus[0], 0, session)['id']})
            else:
                path.write_bytes(b'[]')  # Valid JSON, wrong exact resource identity.

    acquired, conflicts = invalidate_media_indexes(monkeypatch, change)
    monkeypatch.setattr(app, '_publish_changes', publish)
    app.clients.attach('unsafe-index-browser')
    with app.clients.bind('unsafe-index-browser'):
        with pytest.raises(ValueError):
            if boundary == 'descriptor':
                await app.outputs.media.describe(session['id'], old)
            else:
                await app.outputs.media.attach(args, 'ui', 'unsafe-index-attach')
    assert len(acquired) == 2 and acquired[0] is not acquired[1] and len(conflicts) == 1
    assert type(conflicts[0]) is ValueError
    assert conflicts[0].args == ('Session history changed during indexed read',)
    assert publications == [] and not app.db.in_transaction
    assert session.get('mediaRevision', 0) == 0
    assert app.db.execute('SELECT * FROM output_records ORDER BY id').fetchall() == records
    assert app.db.execute('SELECT * FROM output_receipts ORDER BY id').fetchall() == receipts


@pytest.mark.parametrize('kind', ['other-value', 'substring', 'extra-arg', 'subclass', 'os-error'])
async def test_media_index_retry_requires_exact_plain_source_error(media_app, monkeypatch, kind):
    from amplifier_web import conversation_navigation
    session, _, _, _, old = canonical_corpus(media_app)

    class DerivedValueError(ValueError):
        pass

    message = 'Session history changed during indexed read'
    failure = {'other-value': ValueError('Different failure'),
               'substring': ValueError(message + ' later'),
               'extra-arg': ValueError(message, 'extra'),
               'subclass': DerivedValueError(message),
               'os-error': OSError(message)}[kind]
    calls = []

    def source(value):
        calls.append(value)
        raise failure

    monkeypatch.setattr(conversation_navigation, 'source', source)
    with pytest.raises(type(failure)) as error:
        await media_app.outputs.media.identity(session['id'], old)
    assert error.value is failure and len(calls) == 1


@pytest.mark.parametrize('boundary', ['revision', 'claimants', 'aliases', 'complete', 'validate'])
async def test_media_index_retry_does_not_catch_later_or_revision_errors(
        media_app, monkeypatch, boundary):
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_web import automatic_history, media_message_identity
    app = media_app
    session, _, _, _, old = cold_alias_corpus(app) if boundary == 'aliases' else canonical_corpus(app)
    if boundary == 'aliases':
        session, _ = await freeze_alias_display(app, session)
    failure = ValueError('Session history changed during indexed read')
    acquired = []
    real_index = SessionHistoryStore.indexed_messages

    def indexed(reader):
        rows = real_index(reader)
        acquired.append(rows)
        return rows

    def rejected(*args, **kwargs):
        raise failure

    monkeypatch.setattr(SessionHistoryStore, 'indexed_messages', indexed)
    if boundary == 'revision':
        monkeypatch.setattr(automatic_history, 'revision', rejected)
    elif boundary == 'aliases':
        monkeypatch.setattr(app.outputs.media.saved_aliases, 'resolve', rejected)
    else:
        monkeypatch.setattr(media_message_identity,
                            'check_claimants' if boundary == 'claimants' else boundary, rejected)
    with pytest.raises(ValueError) as error:
        await app.outputs.media.identity(session['id'], old)
    assert error.value is failure and len(acquired) == (0 if boundary == 'revision' else 1)


async def test_media_index_retry_does_not_retry_changes_after_projection(media_app, monkeypatch):
    from amplifier_web import conversation_navigation
    app = media_app
    session, _, _, directory, old = canonical_corpus(app)
    calls = []
    real_source = conversation_navigation.source

    def source(value):
        result = real_source(value)
        calls.append(result)
        append_media_unrelated(directory, 1)
        return result

    monkeypatch.setattr(conversation_navigation, 'source', source)
    with pytest.raises(ValueError, match='assistant'):
        await app.outputs.media.identity(session['id'], old)
    assert len(calls) == 1


def test_media_index_retry_leaves_ui_only_identity_source_free(monkeypatch):
    from amplifier_web import automatic_history, conversation_navigation, media_message_identity

    def forbidden(*args):
        pytest.fail('UI-only identity attempted canonical filesystem work')

    monkeypatch.setattr(automatic_history, 'revision', forbidden)
    monkeypatch.setattr(conversation_navigation, 'source', forbidden)
    session = {'id': 'synthetic-ui', 'messages': [{'id': 'saved-ui', 'role': 'assistant', 'text': 'Saved'}]}
    value = media_message_identity.snapshot(session, 'saved-ui')
    assert media_message_identity.resolve(value, 'saved-ui') == {
        'snapshot': value, 'fact': ('saved-ui', 'assistant'), 'revision': None}


async def test_canonical_240_tail_100_old_window_empty_attach_http_and_body_bounds(
        authenticated_client, tmp_path, monkeypatch):
    import copy
    import threading
    from amplifier_foundation.session import history as foundation_history
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.jsonl import TranscriptIndex
    from amplifier_web import automatic_history, conversation_navigation, media_message_identity
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, preload_providers=False, background_updates=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Canonical authority corpus'})
    session, corpus, store, directory, old = canonical_corpus(service)
    raw = (directory / 'transcript.jsonl').read_bytes()
    tail = copy.deepcopy(session['messages'])
    reads, threads = [], []
    owner = threading.get_ident()
    real_positions, real_index = TranscriptIndex.read_positions, SessionHistoryStore.indexed_messages

    def positions(index, requested):
        requested = list(requested)
        reads.append(len(requested))
        return real_positions(index, requested)

    def indexed(reader):
        threads.append(threading.get_ident())
        return real_index(reader)

    monkeypatch.setattr(TranscriptIndex, 'read_positions', positions)
    # Warm the actual navigator seam; old article lives only in its 60-row
    # window, not in the canonical 100-row tail.
    page = conversation_navigation.query(session, message_id=old, window=True)
    assert len(page['messages']) == 60 and old in {row['id'] for row in page['messages']}
    assert reads == [60]
    monkeypatch.setattr(SessionHistoryStore, 'indexed_messages', indexed)

    def no_hydration(*args, **kwargs):
        pytest.fail('Media hydrated a transcript/display body')

    monkeypatch.setattr(automatic_history, 'read_transcript', no_hydration)
    service.clients.attach('canonical-browser')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'canonical-browser'
    sid = session['id']
    query = {'sessionId': sid, 'messageId': old}
    real_decode, real_resolve = foundation_history._decode, media_message_identity.resolve
    media_worker = threading.local()

    def resolve(*values):
        media_worker.active = True
        try:
            return real_resolve(*values)
        finally:
            media_worker.active = False

    def decode(*values):
        if getattr(media_worker, 'active', False):
            pytest.fail('Warm media identity decoded a transcript body instead of cached facts')
        return real_decode(*values)

    monkeypatch.setattr(media_message_identity, 'resolve', resolve)
    with monkeypatch.context() as cold:
        cold.setattr(foundation_history, '_decode', decode)
        cold.setattr(service.cold_display, 'hydrate', no_hydration)
        empty = await client.get('/api/outputs/media', params=query)
        assert empty.status == 200 and (await empty.json())['items'] == []
        args = {**media_args_without_hydration(session), 'messageId': old}
        # The ordinary dispatch preflight is not a media read boundary. This
        # corpus attach uses the owned method; delivery proof remains the
        # browser fixture's normal generation/assistant event/host bridge.
        receipt = await service.outputs.media.attach(args, 'ui', 'canonical-attach')
        record = receipt['result']
        fresh = await client.get('/api/outputs/media', params=query)
        assert fresh.status == 200 and (await fresh.json())['items'][0]['id'] == record['id']
        exact = {**query, 'version': str(record['version']), 'sha256': record['sha256']}
        route = '/api/outputs/' + record['id'] + '/media'
        response = await client.get(route, params=exact, headers={'Range': 'bytes=0-5'})
        assert response.status == 206 and await response.read() == gif()[:6]
        head = await client.head(route, params=exact)
        assert head.status == 200 and head.headers['Content-Length'] == str(len(gif()))
        observed = await client.post('/api/actions', json={
            'action': 'outputs.mediaReport', 'args': {
                **query, 'id': record['id'], 'version': record['version'], 'sha256': record['sha256'],
                'generation': 1, 'sequence': 1, 'renderState': 'ready', 'playback': 'paused'}})
        assert observed.status == 200 and (await observed.json())['accepted']
        for invalid in ('missing', '\x00malformed', 'x' * 201,
                        automatic_history.display_message(corpus[0], 0, session)['id']):
            refused = await client.get('/api/outputs/media', params={**query, 'messageId': invalid})
            assert refused.status == 400
    assert reads == [60]  # Warm descriptor/bytes/report never read message bodies.
    assert threads and owner not in threads
    assert dict.get(session, 'messages') == tail
    assert (directory / 'transcript.jsonl').read_bytes() == raw
    # Disappearance invalidates canonical authority even while the tail is
    # untouched. No empty-success response disguises the missing origin.
    store.save(sid, corpus[2:], {}, preserve_system=True)
    assert (await client.get('/api/outputs/media', params=query)).status == 400
    assert (await client.get(route, params=exact)).status == 400


def media_args_without_hydration(session):
    from pathlib import Path
    data = gif()
    Path(session['workspace'], 'clip.gif').write_bytes(data)
    return {'sessionId': session['id'], 'title': 'Canonical corpus animation',
            'path': 'clip.gif', 'expectedSha256': hashlib.sha256(data).hexdigest()}


def test_media_cold_snapshot_never_resolves_unrelated_display_resources():
    from amplifier_web.cold_display import ColdRecord
    from amplifier_web.media_message_identity import snapshot

    class NoDisplayIO:
        def execute(self, *args):
            pytest.fail('Identity snapshot opened a cold display body')

    session = ColdRecord({'id': 'cold-canonical', 'nativeProject': 'synthetic-project',
        'historyManaged': True, '_coldFields': {
            'messages': {'$resource': 'a' * 64}, 'execution': {'$resource': 'b' * 64},
            'configuration': {'$resource': 'c' * 64}}}, NoDisplayIO())
    value = snapshot(session, 'd' * 32)
    assert value['rows'] == [] and value['coldMessages'] == {'$resource': 'a' * 64}
    assert not dict.__contains__(session, 'messages') and not dict.__contains__(session, 'execution')


async def test_retained_web_alias_and_native_forgery_ambiguity(media_app):
    import copy
    from amplifier_web.automatic_history import display_message
    app = media_app
    session, corpus, _, _, old = canonical_corpus(app)
    canonical = display_message(corpus[1], 1, session)
    alias = {**canonical, 'id': 'retained-web-assistant', 'source': 'amplifier',
             'nativeMessageId': old}
    session.update(historyManaged=False, messages=[alias, *session['messages']])
    app.clients.attach('retained-browser')
    with app.clients.bind('retained-browser'):
        assert (await app.outputs.media.describe(session['id'], alias['id']))['items'] == []
        session['messages'].append({**alias, 'id': 'competing-web-assistant'})
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], alias['id'])
        session['messages'].pop()
        session['messages'].insert(0, copy.deepcopy(alias))
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], alias['id'])
        session['messages'].pop(0)
        session.update(historyManaged=True, messages=[{**canonical, 'id': 'forged-native-id'}])
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], 'forged-native-id')


async def test_hidden_canonical_assistant_id_does_not_grant_media_authority(media_app):
    from amplifier_web.automatic_history import display_identity
    app = media_app
    session, corpus, store, _, _ = canonical_corpus(app)
    internal = display_identity(session, 3, 'assistant', corpus[3]['content'])
    corpus[3]['metadata'] = {'ephemeral': True}
    store.save(session['id'], corpus, {}, preserve_system=True)
    app.clients.attach('hidden-canonical-browser')
    with app.clients.bind('hidden-canonical-browser'):
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], internal)


async def test_old_canonical_media_restart_reopen_preserves_origin_without_display_hydration(media_app):
    import copy
    from pathlib import Path
    from amplifier_web.service import AppService
    from test_service import Runtime
    app = media_app
    session, _, _, directory, old = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': old}
    saved = (await app.outputs.media.attach(args, 'ui', 'canonical-reopen-attach'))['result']
    sid, workspace, data_dir = session['id'], session['workspace'], app.data_dir
    raw = (directory / 'transcript.jsonl').read_bytes()
    Path(workspace, args['path']).unlink()
    await app.close()
    reopened = AppService(data_dir, workspace=workspace, runtime=Runtime())
    try:
        reopened.clients.attach('canonical-reopen-browser')
        with reopened.clients.bind('canonical-reopen-browser'):
            await reopened.dispatch('session.select', {'id': sid})
            await reopened.history.refresh_session(sid)
            source = reopened._session(sid, hydrate=False)
            shown = reopened.history.windows.project(source)
            assert len(shown['messages']) == 100
            assert shown['messageWindow']['coverage']['kind'] == 'native-saved'
            assert all(row['displayOnly'] for row in shown['messages'])
            markers = copy.deepcopy(dict.get(source, '_coldFields', {}))
            resident_messages = copy.deepcopy(dict.get(source, 'messages', []))
            items = (await reopened.outputs.media.describe(sid, old))['items']
            assert len(items) == 1 and items[0]['id'] == saved['id']
            assert await reopened.outputs.media.bytes({**items[0], 'messageId': old}) == gif()
            if 'messages' in markers:
                assert not dict.__contains__(source, 'messages')
                assert dict.get(source, '_coldFields', {}).get('messages') == markers['messages']
            else:
                assert dict.get(source, 'messages', []) == resident_messages
                assert resident_messages == []
        assert (directory / 'transcript.jsonl').read_bytes() == raw
    finally:
        await reopened.close()


async def test_cold_retained_web_alias_requires_bounded_saved_alias_descriptor(media_app):
    """ROOT acceptance discriminator: cold alias descriptor seam is missing.

    The canonical ID can qualify without hydrating display storage, but an
    older retained web ID also needs its exact saved canonical binding. Do not
    promote rejection of that genuine alias to acceptance or hydrate all bodies.
    """
    from amplifier_web.automatic_history import display_message
    from amplifier_web.cold_display import ColdRecord
    from amplifier_web.resource_files import put
    app = media_app
    session, corpus, _, _, old = canonical_corpus(app)
    alias = {**display_message(corpus[1], 1, session), 'id': 'cold-retained-assistant',
             'nativeMessageId': old, 'source': 'amplifier'}
    session.update(historyManaged=False, messages=[alias, *session['messages']])
    app.clients.attach('cold-retained-browser')
    async with app.lock:
        reference = put(app.db, session['messages'])
        app.db.commit()
        metadata = {key: value for key, value in session.items() if key != 'messages'}
        chilled = ColdRecord({**metadata, '_coldFields': {'messages': reference}}, app.db)
        position = app.state['sessions'].index(session)
        app.state['sessions'][position] = chilled
    with app.clients.bind('cold-retained-browser'):
        assert (await app.outputs.media.describe(session['id'], old))['items'] == []
        assert not dict.__contains__(chilled, 'messages')
        assert (await app.outputs.media.describe(session['id'], alias['id']))['items'] == []
        assert not dict.__contains__(chilled, 'messages')


@pytest.mark.parametrize('change', ['role', 'generation', 'execution', 'namespace'])
async def test_canonical_bytes_revalidate_entire_operation_scope(media_app, monkeypatch, change):
    import asyncio
    import threading
    from amplifier_web import output_media
    app = media_app
    session, corpus, store, _, old = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': old}
    record = (await app.outputs.media.attach(args, 'ui', 'scope-attach'))['result']
    app.clients.attach('scope-browser')
    entered, release = threading.Event(), threading.Event()
    real = output_media._saved_bytes

    def blocked(*values):
        entered.set()
        assert release.wait(10)
        return real(*values)

    monkeypatch.setattr(output_media, '_saved_bytes', blocked)
    with app.clients.bind('scope-browser'):
        pending = asyncio.create_task(app.outputs.media.bytes({**record, 'messageId': old}))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            async with app.lock:
                if change == 'role':
                    corpus[1]['role'] = 'user'
                    store.save(session['id'], corpus, {}, preserve_system=True)
                elif change == 'generation':
                    session['collaborationGeneration'] = {'id': 'next-generation'}
                elif change == 'execution':
                    session['executionRevision'] = session.get('executionRevision', 0) + 1
                else:
                    session['nativeIdentity'] = 'different-canonical-session'
            release.set()
            with pytest.raises(ValueError):
                await pending
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)


async def test_canonical_old_media_fork_requires_exact_surviving_origin(media_app):
    """Acceptance discriminator for ROOT's missing transcript index-map seam.

    No xfail or same-text substitute: this source return does not implement
    canonical remapping across the outside-owned fork boundary.
    """
    from amplifier_web.automatic_history import display_message
    app = media_app
    session, corpus, _, directory, old = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': old}
    record = (await app.outputs.media.attach(args, 'ui', 'old-fork-attach'))['result']
    raw = (directory / 'transcript.jsonl').read_bytes()
    sid = session['id']
    await app.dispatch('session.fork', {'id': sid})
    target = app._session()
    rows = [row for row in app.outputs.store.list(target['id'])['items'] if row['kind'] == 'media']
    assert len(rows) == 1, 'Canonical media lost across the exact preserved fork boundary'
    kept = next(row for row in target['messages']
                if row.get('nativeIndex') == 1 and row['role'] == 'assistant')
    assert rows[0]['origin']['messageId'] == kept['id']
    assert kept['id'] == display_message(corpus[1], 1, target)['id']
    assert rows[0]['forkSource']['outputId'] == record['id']
    app.clients.attach('fork-old-browser')
    with app.clients.bind('fork-old-browser'):
        await app.dispatch('session.select', {'id': target['id']})
        assert await app.outputs.media.bytes({**rows[0], 'messageId': kept['id']}) == gif()
    assert (directory / 'transcript.jsonl').read_bytes() == raw


@pytest.mark.parametrize('change', ['role', 'transfer-fence'])
async def test_canonical_commit_revalidates_after_owned_resource_file_await(media_app, monkeypatch, change):
    import asyncio
    import threading
    from amplifier_web import resource_files as files
    app = media_app
    session, corpus, store, directory, old = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': old}
    entered, release = threading.Event(), threading.Event()
    real_write = files.write_prepared

    def blocked(root, prepared):
        if threading.current_thread() is not threading.main_thread() and '"encoding":"base64"' in prepared.text:
            entered.set()
            assert release.wait(10)
        return real_write(root, prepared)

    monkeypatch.setattr(files, 'write_prepared', blocked)
    pending = asyncio.create_task(app.outputs.media.attach(args, 'ui', 'late-canonical-change'))
    marker = directory / 'transfer-fence.json'
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert app.lock.locked() and app.db.in_transaction
        # External canonical writers/fence files do not acquire the web lock.
        if change == 'role':
            corpus[1]['role'] = 'user'
            store.save(session['id'], corpus, {}, preserve_system=True)
        else:
            marker.write_text('{"synthetic":true}')
        release.set()
        with pytest.raises(ValueError):
            await pending
        assert not app.db.in_transaction and session.get('mediaRevision', 0) == 0
        assert app.outputs.store.list(session['id'])['items'] == []
        assert app.db.execute('SELECT 1 FROM output_receipts WHERE id=?', ('late-canonical-change',)).fetchone() is None
        assert (await app.dispatch('shell.query', {'clientId': 'after-canonical-change',
                                                 'instanceId': 'chats'}))['accepted']
    finally:
        release.set()
        marker.unlink(missing_ok=True)
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('cancel_media', [False, True])
@pytest.mark.parametrize('overlap_cleanup', [False, True], ids=['ordinary', 'maintenance-overlap'])
async def test_owned_media_writer_excludes_real_shell_http_and_receipts(
        authenticated_client, tmp_path, monkeypatch, cancel_media, overlap_cleanup):
    import asyncio
    from collections import deque
    from pathlib import Path
    import threading
    from amplifier_web import resource_files as files
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, preload_providers=False, background_updates=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Transaction owner corpus'})
    await service.on_runtime_event('assistant.message', {'sessionId': service._session()['id'],
                                                        'text': 'Owned animation.'})
    args = media_args(service)
    for identity in ('owner-browser', 'other-browser', 'fresh-browser'):
        service.clients.attach(identity)
    for identity in ('owner-browser', 'other-browser'):
        await service.dispatch('shell.query', {'clientId': identity, 'instanceId': 'chats'})
    assert service.shell.get('client', 'fresh-browser') is None
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    cleanup_entered, cleanup_release = threading.Event(), threading.Event()
    cleanup_task = None
    pending = None
    observer = asyncio.current_task()
    waiting = asyncio.Event()
    waiters, transactions, body_ids = [], [], []
    timeline, task_ids = deque(maxlen=256), {}
    media_release = []
    real_write = files.write_prepared

    original_acquire = service.lock.acquire
    original_release = service.lock.release

    def observe(event):
        task = asyncio.current_task()
        if task not in task_ids:
            task_ids[task] = len(task_ids) + 1
        timeline.append({
            'event': event, 'task': task_ids[task],
            'coroutine': task.get_coro().__qualname__ if task else None,
            'transaction': service.db.in_transaction,
            'locked': service.lock.locked(),
        })

    async def acquire():
        observe('lock.wait')
        if asyncio.current_task() is observer and cleanup_entered.is_set():
            observe('cleanup.observation-waits')
            cleanup_release.set()
        if (service.lock.locked() and entered.is_set() and not release.is_set()
                and service.clients.current.get() in {'owner-browser', 'other-browser', 'fresh-browser'}):
            waiters.append(asyncio.current_task())
            if len(waiters) >= 5:
                waiting.set()
        result = await original_acquire()
        observe('lock.acquired')
        return result

    def unlock():
        observe('lock.release')
        if asyncio.current_task() is pending and entered.is_set():
            # Capture before queued shell writes can commit a leaked media
            # transaction. Never assert here and prevent the lock release.
            media_release.append((finished.is_set(), service.db.in_transaction))
        return original_release()

    monkeypatch.setattr(service.lock, 'acquire', acquire)
    monkeypatch.setattr(service.lock, 'release', unlock)

    def blocked(directory, prepared):
        if threading.current_thread() is not threading.main_thread() and '"encoding":"base64"' in prepared.text:
            body_ids.append(prepared.identity)
            entered.set()
            assert release.wait(10)
            try:
                return real_write(directory, prepared)
            finally:
                finished.set()
        return real_write(directory, prepared)

    def trace(sql):
        operation = sql.strip().split(None, 1)[0].upper()
        if operation in {'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE', 'INSERT', 'UPDATE', 'DELETE'}:
            # Operation classes only: never retain expanded SQL or its values.
            observe('sql.' + operation)
        if entered.is_set() and not release.is_set() and sql.strip().upper().startswith(('COMMIT', 'ROLLBACK')):
            transactions.append(sql)

    monkeypatch.setattr(files, 'write_prepared', blocked)
    service.db.set_trace_callback(trace)
    client = await authenticated_client(app)
    pending = asyncio.create_task(service.outputs.media.attach(args, 'ui', 'owned-media-command'))
    requests = []
    cancelled = None
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert service.lock.locked() and service.db.in_transaction

        async def post(identity, action, values, command_id):
            return await client.post('/api/actions', headers={'X-Amplifier-Client': identity},
                json={'action': action, 'args': {'clientId': identity, **values}, 'id': command_id})

        requests = [
            asyncio.create_task(client.get('/api/shell', params={'clientId': 'fresh-browser', 'navigationResources': '[]'})),
            asyncio.create_task(post('owner-browser', 'shell.report',
                {'revision': 0, 'instances': {'chats': 'ready', 'workspaces': 'ready'}}, 'owned-report')),
            asyncio.create_task(post('other-browser', 'shell.query', {'instanceId': 'chats'}, 'owned-query')),
            asyncio.create_task(post('owner-browser', 'shell.view.update',
                {'instanceId': 'chats', 'patch': {'navFilter': 'owner-only'}}, 'owned-view')),
        ]
        with service.clients.bind('owner-browser'):
            cancelled = asyncio.create_task(service.shell.dispatch('shell.query',
                {'clientId': 'owner-browser', 'instanceId': 'chats'}, 'ui', 'cancelled-waiter'))
        await asyncio.wait_for(waiting.wait(), 10)
        assert all(not request.done() for request in requests)
        assert service.shell.get('client', 'fresh-browser') is None
        assert transactions == [] and service.db.in_transaction
        # This authenticated reader does not initialize or commit shell state.
        assert (await client.get('/api/actions')).status == 200
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert service.shell.get('command', 'cancelled-waiter') is None
        if cancel_media:
            observe('media.cancel')
            pending.cancel()
            await asyncio.sleep(0)  # Give cancellation to the owned worker wait.
            assert not pending.done() and not finished.is_set() and service.db.in_transaction
        release.set()
        if cancel_media:
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            assert (await pending)['accepted']
        assert finished.is_set()
        assert media_release == [(True, False)], list(timeline)
        observe('media.settled')
        responses = await asyncio.wait_for(asyncio.gather(*requests), 10)
        observe('http.settled')
        assert all(response.status == 200 for response in responses)
        first, report, query, view = [await response.json() for response in responses]
        assert first['revision'] == 0 and report['accepted'] and query['accepted'] and view['accepted']
        assert service.shell.client('owner-browser')['views']['chats']['view']['navFilter'] == 'owner-only'
        assert service.shell.client('other-browser')['views'].get('chats', {}).get('view', {}).get('navFilter', '') == ''
        assert service.shell.client('fresh-browser')['views'] == {}
        for identity in ('owned-report', 'owned-query', 'owned-view'):
            assert service.shell.get('command', identity)['receipt']['accepted']
        for identity in ('owner-browser', 'other-browser'):
            assert service.shell.client(identity)['views'].get('chats', {}).get('generation', 0) == 0
        if overlap_cleanup:
            # Exercise maintenance's real file-deletion boundary with a known
            # obsolete resource. Do not race its optimistic graph traversal
            # against the independent history controller's legitimate saves.
            task = getattr(service, '_storage_maintenance_task', None)
            if task:
                await task
            async with service.lock:
                garbage = files.put(service.db, {'unreferenced': 'cleanup overlap'})
                service.db.commit()
            garbage_path = files.root(service.db) / (garbage['$resource'] + '.json')
            real_unlink = Path.unlink

            def unlink(path, *values, **options):
                if path == garbage_path and threading.current_thread() is not threading.main_thread():
                    cleanup_entered.set()
                    assert cleanup_release.wait(10)
                return real_unlink(path, *values, **options)

            monkeypatch.setattr(Path, 'unlink', unlink)
            async def cleanup():
                async with service.lock:
                    service.db.execute('DELETE FROM state_resources WHERE id=?', (garbage['$resource'],))
                    service.db.commit()
                    await files.remove_files_async(service.db, [garbage['$resource']])

            cleanup_task = asyncio.create_task(cleanup())
            assert await asyncio.to_thread(cleanup_entered.wait, 10)
            assert service.lock.locked() and service.db.in_transaction
            observe('cleanup.blocked')
        # HTTP completion does not settle other owners. Observe under the same
        # lock used by media, shell mutations and resource maintenance.
        async with service.lock:
            observe('final.locked')
            assert not service.db.in_transaction, list(timeline)
            if cancel_media:
                assert service._session(args['sessionId']).get('mediaRevision', 0) == 0
                assert service.outputs.store.list(args['sessionId'])['items'] == []
                assert service.db.execute('SELECT 1 FROM output_receipts WHERE id=?',
                                          ('owned-media-command',)).fetchone() is None
                assert service.db.execute('SELECT 1 FROM state_resources WHERE id=?', (body_ids[0],)).fetchone() is None
                # The writer has settled before normal reconciliation can retire
                # its unindexed file; a surviving orphan is a required failure.
                cursor = None
                while True:
                    cursor, result = files.sweep_unindexed(service.db, service._state, cursor)
                    assert not result['blocked']
                    if result['complete']:
                        break
                assert not (files.root(service.db) / (body_ids[0] + '.json')).exists()
        if cleanup_task:
            await asyncio.wait_for(cleanup_task, 10)
            assert not garbage_path.exists()
        again = await service.dispatch('shell.query',
            {'clientId': 'owner-browser', 'instanceId': 'chats'}, command_id='after-owned-write')
        assert again['accepted']
    finally:
        release.set()
        cleanup_release.set()
        service.db.set_trace_callback(None)
        if cancelled and not cancelled.done():
            cancelled.cancel()
        await asyncio.gather(pending, *requests, *([cancelled] if cancelled else []), return_exceptions=True)
        if cleanup_task:
            await cleanup_task


@pytest.mark.parametrize('outer_transaction', [False, True])
@pytest.mark.parametrize('failure', ['cancel', 'write-error', 'cancel-and-write-error'])
async def test_media_failure_preserves_transaction_owner_and_allows_reuse(
        media_app, monkeypatch, outer_transaction, failure):
    import asyncio
    import sqlite3
    import threading
    from amplifier_web import resource_files as files
    app = media_app
    task = getattr(app, '_storage_maintenance_task', None)
    if task:
        await task
    args = media_args(app)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    real_write = files.write_prepared
    error = OSError('synthetic media writer failure')
    body_ids = []
    if outer_transaction:
        async with app.lock:
            app.db.execute('BEGIN IMMEDIATE')
            app.db.execute('INSERT INTO state_resources VALUES (?, ?)', ('caller-owned', '{}'))

    def write(directory, prepared):
        if '"encoding":"base64"' not in prepared.text:
            return real_write(directory, prepared)
        body_ids.append(prepared.identity)
        entered.set()
        assert release.wait(10)
        try:
            real_write(directory, prepared)
            if failure != 'cancel':
                raise error
        finally:
            finished.set()

    # Resolve the actual service database rather than relying on its filename.
    db_path = next(row[2] for row in app.db.execute('PRAGMA database_list') if row[1] == 'main')
    observer = sqlite3.connect(db_path)
    pending = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(files, 'write_prepared', write)
            pending = asyncio.create_task(app.outputs.media.attach(args, 'ui', 'reuse-media'))
            assert await asyncio.to_thread(entered.wait, 10)
            assert app.lock.locked() and app.db.in_transaction
            if failure.startswith('cancel'):
                pending.cancel()
                await asyncio.sleep(0)
                pending.cancel()
                await asyncio.sleep(0)
                assert not pending.done() and not finished.is_set()
            release.set()
            if failure.startswith('cancel'):
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                with pytest.raises(OSError) as caught:
                    await pending
                assert caught.value is error
            assert finished.is_set()
            async with app.lock:
                assert app.db.in_transaction is outer_transaction
                assert app._session().get('mediaRevision', 0) == 0
                assert app.outputs.store.list(args['sessionId'])['items'] == []
                assert app.db.execute('SELECT 1 FROM output_receipts WHERE id=?',
                                      ('reuse-media',)).fetchone() is None
                assert app.db.execute('SELECT 1 FROM state_resources WHERE id=?',
                                      (body_ids[0],)).fetchone() is None
                assert bool(app.db.execute('SELECT 1 FROM state_resources WHERE id=?',
                                           ('caller-owned',)).fetchone()) is outer_transaction
                assert observer.execute('SELECT 1 FROM state_resources WHERE id=?',
                                        ('caller-owned',)).fetchone() is None
                if outer_transaction:
                    # Only the caller settles its sentinel, after proving media
                    # neither rolled it back nor committed it.
                    app.db.commit()
                    assert observer.execute('SELECT 1 FROM state_resources WHERE id=?',
                                            ('caller-owned',)).fetchone() is not None
                    app.db.execute('DELETE FROM state_resources WHERE id=?', ('caller-owned',))
                    app.db.commit()
        # A failed/cancelled command has no receipt. A deliberate later call can
        # adopt its exact immutable file; subsequent receipt reuse adds nothing.
        result = await app.outputs.media.attach(args, 'ui', 'reuse-media')
        again = await app.outputs.media.attach(args, 'ui', 'reuse-media')
        assert result['accepted'] and again['result']['id'] == result['result']['id']
        async with app.lock:
            assert not app.db.in_transaction
            assert app._session()['mediaRevision'] == 1
            assert len(app.outputs.store.list(args['sessionId'])['items']) == 1
    finally:
        release.set()
        if pending:
            await asyncio.gather(pending, return_exceptions=True)
        observer.close()


def cold_alias_corpus(app, *, native_message_id=True):
    from amplifier_web.automatic_history import display_message
    session, corpus, store, directory, canonical = canonical_corpus(app)
    alias = {**display_message(corpus[1], 1, session),
             'id': 'saved-web-assistant', 'source': 'amplifier'}
    if native_message_id:
        alias['nativeMessageId'] = canonical
    session.update(historyManaged=False, messages=[alias, *session['messages']])
    return session, corpus, store, directory, canonical


@pytest.mark.parametrize('cold', [False, True])
@pytest.mark.parametrize('spelling', ['canonical', 'retained'])
async def test_private_block_projection_keeps_actual_media_attachment(media_app, monkeypatch, cold, spelling):
    from amplifier_foundation.session.jsonl import TranscriptIndex
    from amplifier_web.automatic_history import display_message
    app = media_app
    session, corpus, store, directory, _ = canonical_corpus(app)
    corpus[1]['content'] = [
        {'type': 'text', 'text': 'Public reply'},
        {'type': 'text', 'text': 'Private detail', 'visibility': 'internal'},
        {'type': 'text', 'text': 'Developer detail', 'visibility': 'developer'},
    ]
    store.save(session['id'], corpus, {}, preserve_system=True)
    displayed = display_message(corpus[1], 1, session)
    canonical = displayed['id']
    if spelling == 'retained':
        displayed = {**displayed, 'id': 'retained-public-reply', 'source': 'amplifier'}
    session.update(historyManaged=spelling == 'canonical', messages=[displayed])
    before = (directory / 'transcript.jsonl').read_bytes()
    if cold:
        session, _ = await freeze_alias_display(app, session)
    args = {**media_args_without_hydration(session), 'messageId': displayed['id']}
    record = (await app.outputs.media.attach(args, 'ui', 'public-block-media'))['result']
    assert app.outputs.store.read(record['id'])['origin']['messageId'] == displayed['id']
    app.clients.attach('public-block-browser')
    with app.clients.bind('public-block-browser'):
        proof = await app.outputs.media.identity(session['id'], displayed['id'], scoped=True)
        assert proof['fact'][0] == canonical and proof['fact'][5] != canonical
        with monkeypatch.context() as warm:
            warm.setattr(TranscriptIndex, 'read_positions', lambda *a, **k: pytest.fail('Media reread canonical bodies'))
            descriptions = await app.outputs.media.describe(session['id'], displayed['id'])
            assert descriptions['items'][0]['id'] == record['id']
            assert await app.outputs.media.bytes({**record, 'messageId': displayed['id']}) == gif()
    assert (directory / 'transcript.jsonl').read_bytes() == before


@pytest.mark.parametrize('view', ['raw', 'public'])
@pytest.mark.parametrize('change', [None, 'text', 'binding', 'index', 'duplicate', 'native-alias',
                                    'rewrite-private', 'unbound-alias', 'unbound-rewrite'])
def test_media_public_projection_qualifies_body_and_preserves_raw_guards(tmp_path, monkeypatch, view, change):
    import copy
    import io
    import json
    from amplifier_web.automatic_history import display_message
    from amplifier_web.conversation_navigation import source
    from amplifier_web.media_message_identity import snapshot, resolve
    from amplifier_web.media_saved_aliases import discover
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    directory = tmp_path / 'projects/project/sessions/native'
    directory.mkdir(parents=True)
    raw = {'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'Public reply'},
        {'type': 'text', 'text': 'Private detail', 'visibility': 'internal'},
    ]}
    scope = {'id': 'conversation', 'nativeIdentity': 'native', 'nativeProject': 'project', 'historyManaged': True}
    displayed = display_message(raw, 0, scope, include_internal=view == 'raw')
    requested = displayed['id']
    rows = [copy.deepcopy(displayed)]
    if change == 'text':
        rows[0]['text'] += ' forged'
    elif change == 'binding':
        rows[0]['nativeMessageId'] = '0' * 32
    elif change == 'index':
        rows[0]['nativeIndex'] = 7
    elif change == 'duplicate':
        rows.append(copy.deepcopy(rows[0]))
    elif change == 'native-alias':
        rows[0]['id'] = requested = 'forged-native-spelling'
    elif change in ('unbound-alias', 'unbound-rewrite'):
        rows[0].update(id='retained-alias', source='amplifier')
        rows[0].pop('nativeMessageId', None)
        requested = 'retained-alias'
        scope['historyManaged'] = False
    if change in ('rewrite-private', 'unbound-rewrite'):
        raw['content'][1]['text'] = 'Private rewrite'
    path = directory / 'transcript.jsonl'
    path.write_text(json.dumps(raw) + '\n')
    before = path.read_bytes()
    facts, _ = source({**scope, 'historyManaged': True})
    session = {**scope, 'messages': rows}
    if change and not (change == 'unbound-alias' and view == 'raw'):
        with pytest.raises(ValueError):
            resolve(snapshot(session, requested), requested)
        with pytest.raises(ValueError):
            discover(io.BytesIO(json.dumps(rows).encode()), scope, requested, facts)
    else:
        assert resolve(snapshot(session, requested), requested)['fact'][0] == displayed['id']
        assert discover(io.BytesIO(json.dumps(rows).encode()), scope, requested, facts)[0][0] == displayed['id']
    assert path.read_bytes() == before


async def freeze_alias_display(app, session, *, rows=None, hints=False):
    """Synthetic legacy reference, not a substitute for real producer tests."""
    from amplifier_web.cold_display import ColdRecord
    from amplifier_web.media_saved_aliases import binding_hint
    from amplifier_web.resource_files import put
    async with app.lock:
        body = dict.get(session, 'messages') if rows is None else rows
        reference = put(app.db, body)
        if hints:
            reference = binding_hint(session, reference, body)
        app.db.commit()
        metadata = dict(dict.items(session))
        metadata.pop('messages', None)
        metadata['_coldFields'] = {**metadata.get('_coldFields', {}), 'messages': reference}
        chilled = ColdRecord(metadata, app.db)
        app._state['sessions'][app._state['sessions'].index(session)] = chilled
    return chilled, reference


def test_media_root_default_is_only_missing_not_falsey():
    from amplifier_web.media_message_identity import snapshot, validate
    session = {'id': 'synthetic-root', 'messages': [
        {'id': 'saved-assistant', 'role': 'assistant', 'text': 'Retained'}]}
    before = snapshot(session, 'saved-assistant')
    assert before['sessionKind'] == 'root'
    session['sessionKind'] = 'root'
    assert snapshot(session, 'saved-assistant') == before
    proof = {'snapshot': before}
    assert validate(session, 'saved-assistant', proof)['role'] == 'assistant'
    for invalid in (None, '', False, 'worker', 'internal'):
        session['sessionKind'] = invalid
        assert snapshot(session, 'saved-assistant')['sessionKind'] == invalid
        with pytest.raises(ValueError, match='identity changed'):
            validate(session, 'saved-assistant', proof)
    session['sessionKind'] = 'root'
    for field, changed in (
        ('id', 'different-root'), ('workspace', '/synthetic/other'),
        ('parentId', 'different-parent'), ('nativeRevision', [1, 2]),
        ('executionRevision', 2), ('collaborationGeneration', {'id': 'different'})):
        session[field] = changed
        with pytest.raises(ValueError, match='identity changed'):
            validate(session, 'saved-assistant', proof)
        session.pop(field)
        if field == 'id':
            session['id'] = 'synthetic-root'


@pytest.mark.parametrize('producer', ['cold-display', 'session-projection'])
async def test_real_cold_alias_producers_and_reopen(media_app, monkeypatch, producer):
    from contextlib import closing
    import copy
    import sqlite3
    from amplifier_web import cold_display, state_records
    from amplifier_web.service import AppService
    from test_service import Runtime
    app = media_app
    session, _, _, directory, canonical = cold_alias_corpus(app, native_message_id=False)
    original_messages = copy.deepcopy(dict.get(session, 'messages'))
    sid = session['id']
    raw = (directory / 'transcript.jsonl').read_bytes()
    home, workspace = app.data_dir, session['workspace']
    database = next(row[2] for row in app.db.execute('PRAGMA database_list') if row[1] == 'main')
    if producer == 'cold-display':
        # Real retirement must release the list, not just create a descriptor.
        app._state['selectedSessionId'] = None
        monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
        assert sid in app.cold_display.retire(force=True)
        assert not dict.__contains__(session, 'messages')
        reference = copy.deepcopy(dict.get(session, '_coldFields')['messages'])
    else:
        app._save(session_ids={sid}, record_only=True)
        pointer = next(row['$viewPayload'] for row in state_records.load(app.db)['sessions']
                       if row['id'] == sid)
        saved = cold_display.load(app.db, pointer)
        reference = saved['_coldFields']['messages']
        assert dict.__contains__(session, 'messages'), 'storage projection must not evict live aliases'
        assert dict.get(session, 'messages') == original_messages
    assert cold_display.load(app.db, reference) == original_messages
    hint = reference['mediaBindings']
    assert hint['resource'] == reference['$resource'] and hint['scope'] == session['nativeIdentity']
    assert hint['revision'] == session['nativeRevision']
    assert hint['project'] == session['nativeProject']
    assert all('text' not in row for row in hint['bindings'])
    assert any(row['id'] == 'saved-web-assistant' and row['nativeIndex'] == 1 for row in hint['bindings'])
    app._save(session_ids={sid}, record_only=True)
    pointer = next(row['$viewPayload'] for row in state_records.load(app.db)['sessions']
                   if row['id'] == sid)
    saved = cold_display.load(app.db, pointer)
    assert saved['_coldFields']['messages'] == reference
    assert cold_display.load(app.db, saved['_coldFields']['messages']) == original_messages
    await app.close()
    # The committed SQLite pointer, not view.json or a reconstructed expected
    # reference, owns the complete producer body across shutdown.
    with closing(sqlite3.connect(database)) as saved_db:
        pointer = next(row['$viewPayload'] for row in state_records.load(saved_db)['sessions']
                       if row['id'] == sid)
        saved = cold_display.load(saved_db, pointer)
        assert saved['_coldFields']['messages'] == reference
        assert cold_display.load(saved_db, saved['_coldFields']['messages']) == original_messages
    reopened = AppService(home, workspace=workspace, runtime=Runtime())
    try:
        reopened.clients.attach('producer-reopen-browser')
        reopened.clients.records['producer-reopen-browser']['selectedSessionId'] = sid
        source = reopened._session(sid, hydrate=False)
        # Startup legitimately consumes messages for delivery reconciliation.
        # Prove its entire resident body and durable reference before re-cooling.
        assert dict.__contains__(source, 'messages')
        assert dict.get(source, 'messages') == original_messages
        assert dict.get(source, '_coldFields', {}).get('messages') is None
        pointer = next(row['$viewPayload'] for row in state_records.load(reopened.db)['sessions']
                       if row['id'] == sid)
        saved = cold_display.load(reopened.db, pointer)
        assert saved['_coldFields']['messages'] == reference
        assert cold_display.load(reopened.db, saved['_coldFields']['messages']) == original_messages
        assert source['nativeIdentity'] == hint['scope']
        assert source['nativeRevision'] == hint['revision']
        assert source['nativeProject'] == hint['project']
        # Deep-copy baselines/inline comparisons above borrow no mutable live
        # messages or nested rows. Preserve all normal ownership/protection gates.
        reopened._state['selectedSessionId'] = None
        monkeypatch.setattr(reopened.cold_display, 'RECENT_LIMIT', 0)
        assert sid in reopened.cold_display.retire(force=True), (
            'Real reopened retirement must pass ownership, demand, persisting, '
            'progress and minimum-body-size gates without overriding them')
        markers = copy.deepcopy(dict.get(source, '_coldFields'))
        assert markers['messages'] == reference
        with reopened.clients.bind('producer-reopen-browser'):
            assert (await reopened.outputs.media.describe(sid, canonical))['items'] == []
            assert (await reopened.outputs.media.describe(sid, 'saved-web-assistant'))['items'] == []
        assert dict.get(source, '_coldFields') == markers
        assert not dict.__contains__(source, 'messages')
        assert (directory / 'transcript.jsonl').read_bytes() == raw
    finally:
        await reopened.close()


@pytest.mark.parametrize('native_message_id', [False, True])
async def test_legacy_cold_canonical_spelling_plus_unique_retained_alias(media_app, native_message_id):
    from amplifier_web.automatic_history import display_message
    app = media_app
    session, corpus, _, _, canonical = cold_alias_corpus(app, native_message_id=native_message_id)
    rows = [*session['messages'], display_message(corpus[1], 1, session)]
    chilled, reference = await freeze_alias_display(app, session, rows=rows)
    assert set(reference) == {'$resource', 'bytes'}, 'legacy bare references stay supported'
    app.clients.attach('legacy-pair-browser')
    with app.clients.bind('legacy-pair-browser'):
        for identity in (canonical, 'saved-web-assistant'):
            assert (await app.outputs.media.describe(session['id'], identity))['items'] == []
    assert dict.get(chilled, '_coldFields')['messages'] == reference
    assert not dict.__contains__(chilled, 'messages')


@pytest.mark.parametrize('defect', [
    'duplicate-id', 'duplicate-id-other-index', 'competing-alias', 'conflicting-text', 'wrong-native-id',
    'wrong-index', 'bool-index', 'user', 'malformed-role', 'malformed-text', 'observation', 'observation-id',
    'malformed-id', 'forged-native',
])
async def test_cold_saved_alias_conflicts_fail_closed(media_app, defect):
    app = media_app
    session, corpus, _, _, canonical = cold_alias_corpus(app)
    rows = [dict(row) for row in session['messages']]
    if defect == 'duplicate-id':
        rows.append(dict(rows[0]))
    elif defect == 'duplicate-id-other-index':
        from amplifier_web.automatic_history import display_message
        other = display_message(corpus[3], 3, session)
        rows.append({**other, 'id': rows[0]['id'], 'source': 'amplifier',
                     'nativeMessageId': other['id']})
    elif defect == 'competing-alias':
        rows.append({**rows[0], 'id': 'competing-saved-alias'})
    elif defect == 'conflicting-text':
        rows[0]['text'] += 'not canonical'
    elif defect == 'wrong-native-id':
        rows[0]['nativeMessageId'] = '0' * 32
    elif defect == 'wrong-index':
        rows[0]['nativeIndex'] = 3
    elif defect == 'bool-index':
        rows[0]['nativeIndex'] = True
    elif defect == 'user':
        rows[0]['role'] = 'user'
    elif defect == 'malformed-role':
        rows[0]['role'] = {'malformed': True}
    elif defect == 'malformed-text':
        rows[0]['text'] = {'malformed': True}
    elif defect == 'observation':
        rows[0]['observation'] = {'kind': 'tool'}
    elif defect == 'observation-id':
        rows[0]['observation_id'] = 'observation'
    elif defect == 'malformed-id':
        rows[0]['id'] = '\x00malformed'
    else:
        session['historyManaged'] = True
        rows[0]['source'] = 'native'
    chilled, _ = await freeze_alias_display(app, session, rows=rows, hints=True)
    app.clients.attach('invalid-alias-browser')
    with app.clients.bind('invalid-alias-browser'):
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], rows[0]['id'])
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], canonical)
    assert not dict.__contains__(chilled, 'messages')


async def test_untrusted_compact_alias_hint_cannot_replace_saved_body(media_app):
    app = media_app
    session, _, _, _, canonical = cold_alias_corpus(app)
    chilled, reference = await freeze_alias_display(app, session, rows=session['messages'][1:], hints=True)
    reference['mediaBindings']['bindings'].insert(0, {
        'id': 'hint-only-alias', 'role': 'assistant', 'nativeIndex': 1, 'nativeMessageId': canonical})
    reference['mediaBindings'].update(scope='forged-scope', revision=[0], resource='0' * 64)
    app.clients.attach('hint-browser')
    with app.clients.bind('hint-browser'):
        assert (await app.outputs.media.describe(session['id'], canonical))['items'] == []
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], 'hint-only-alias')
    assert not dict.__contains__(chilled, 'messages')


@pytest.mark.parametrize('change', ['reference', 'index', 'revision', 'canonical-role', 'scope'])
async def test_cold_alias_discovery_rechecks_after_worker_await(media_app, monkeypatch, change):
    import asyncio
    import threading
    from amplifier_web import media_saved_aliases, resource_files
    app = media_app
    session, corpus, store, _, _ = cold_alias_corpus(app)
    chilled, reference = await freeze_alias_display(app, session)
    entered, release = threading.Event(), threading.Event()
    real = media_saved_aliases.SavedAliases.resolve

    def blocked(instance, *args):
        entered.set()
        assert release.wait(10)
        return real(instance, *args)

    monkeypatch.setattr(media_saved_aliases.SavedAliases, 'resolve', blocked)
    pending = asyncio.create_task(app.outputs.media.identity(session['id'], 'saved-web-assistant'))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with app.lock:
            if change == 'reference':
                replacement = resource_files.put(app.db, [{'id': 'other', 'role': 'user', 'text': 'other'}])
                app.db.commit()
                dict.get(chilled, '_coldFields')['messages'] = replacement
            elif change == 'index':
                app.db.execute('UPDATE state_resources SET value=? WHERE id=?',
                               ('{"$blob":"' + 'f' * 64 + '"}', reference['$resource']))
                app.db.commit()
            elif change == 'revision':
                chilled['nativeRevision'] = [0, 0]
            elif change == 'canonical-role':
                corpus[1]['role'] = 'user'
                store.save(session['id'], corpus, {}, preserve_system=True)
            else:
                chilled['nativeIdentity'] = 'different-native-scope'
        release.set()
        with pytest.raises(ValueError):
            await pending
        assert not dict.__contains__(chilled, 'messages')
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('boundary', ['descriptor', 'guard', 'report'])
async def test_cold_alias_later_boundaries_recheck_detached_index(media_app, boundary):
    app = media_app
    session, _, _, _, _ = cold_alias_corpus(app)
    chilled, reference = await freeze_alias_display(app, session)
    record = (await app.outputs.media.attach({
        **media_args_without_hydration(session), 'messageId': 'saved-web-assistant'},
        'ui', 'saved-alias-boundary-attach'))['result']
    app.clients.attach('proof-boundary-browser')
    with app.clients.bind('proof-boundary-browser'):
        proof = await app.outputs.media.identity(session['id'], 'saved-web-assistant', scoped=True)
        values = {**record, 'messageId': 'saved-web-assistant', 'generation': 1, 'sequence': 1,
                  'renderState': 'ready', 'playback': 'paused'}
        async with app.lock:
            app.db.execute('UPDATE state_resources SET value=? WHERE id=?',
                           ('{"$blob":"' + 'f' * 64 + '"}', reference['$resource']))
            app.db.commit()
            with pytest.raises(ValueError, match='resource index changed'):
                if boundary == 'descriptor':
                    app.outputs.media.descriptors(session['id'], 'saved-web-assistant', proof)
                elif boundary == 'guard':
                    app.outputs.media.guard(values, proof=proof)
                else:
                    app.outputs.media.report(values, 'ui', proof=proof)
    assert not dict.__contains__(chilled, 'messages')


async def test_cold_alias_final_attach_rechecks_without_recursive_identity(media_app, monkeypatch):
    from amplifier_web import resource_files
    app = media_app
    session, _, _, _, _ = cold_alias_corpus(app)
    chilled, reference = await freeze_alias_display(app, session)
    real = resource_files.put_prepared_async

    async def change_index_after_write(db, prepared):
        body = await real(db, prepared)
        assert app.lock.locked() and db.in_transaction
        # A synchronous owner-thread hook changes the same transaction index.
        # Final publication must freshly detach, not recursively acquire lock.
        db.execute('UPDATE state_resources SET value=? WHERE id=?',
                   ('{"$blob":"' + 'f' * 64 + '"}', reference['$resource']))
        return body

    monkeypatch.setattr(resource_files, 'put_prepared_async', change_index_after_write)
    with pytest.raises(ValueError):
        await app.outputs.media.attach({
            **media_args_without_hydration(session), 'messageId': 'saved-web-assistant'},
            'ui', 'saved-alias-final-index')
    assert not app.db.in_transaction
    assert app.outputs.store.list(session['id'])['items'] == []
    assert app.db.execute('SELECT 1 FROM output_receipts WHERE id=?',
                          ('saved-alias-final-index',)).fetchone() is None
    assert not dict.__contains__(chilled, 'messages')


async def test_cold_alias_warm_http_descriptor_head_range_report_read_no_display_bodies(
        authenticated_client, tmp_path, monkeypatch):
    import asyncio
    import copy
    import threading
    from amplifier_foundation.session import history as foundation_history
    from amplifier_web import conversation_navigation, media_saved_aliases, resource_files
    from amplifier_web import storage_migration
    from amplifier_web.server import create_app
    from test_service import Runtime
    # This probe counts foreground media reads. Background catalog reconciliation
    # and resource collection legitimately read bodies and have separate tests.
    monkeypatch.setattr(storage_migration, 'maintenance', lambda service: None)
    server = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                              voice=False, preload_providers=False, background_updates=False)
    app = server['service']
    await app.history.close()
    await app.event_log_view.close()
    await app.dispatch('session.create', {'title': 'Saved alias HTTP corpus'})
    session, _, _, directory, canonical = cold_alias_corpus(app, native_message_id=False)
    chilled, reference = await freeze_alias_display(app, session)
    markers = copy.deepcopy(dict.get(chilled, '_coldFields'))
    raw = (directory / 'transcript.jsonl').read_bytes()
    # Warm canonical facts, not transcript rows. The media resolver uses the
    # same worker-only seam and must not invoke its returned body reader.
    await asyncio.to_thread(conversation_navigation.source, {
        **dict(dict.items(chilled)), 'historyManaged': True})
    path = resource_files.root(app.db) / (reference['$resource'] + '.json')
    info = path.stat()
    evidence = (info.st_dev, info.st_ino)
    counts = {'reads': 0, 'bytes': 0, 'displayDecode': 0, 'canonicalDecode': 0}
    threads, warm = [], False
    owner = threading.get_ident()
    real_fdopen = resource_files.os.fdopen
    real_string = media_saved_aliases.DisplayJSON.string
    real_decode = foundation_history._decode

    class CountingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            assert not warm, 'Warm media opened/read the saved display body'
            assert 0 <= size <= 64 * 1024, 'Display reads must be bounded'
            result = self.stream.read(size)
            counts['reads'] += 1
            counts['bytes'] += len(result)
            threads.append(threading.get_ident())
            return result

    def fdopen(fd, *args, **kwargs):
        current = os.fstat(fd)
        stream = real_fdopen(fd, *args, **kwargs)
        return CountingStream(stream) if (current.st_dev, current.st_ino) == evidence else stream

    def string(parser, *args, **kwargs):
        assert not warm, 'Warm media decoded saved display JSON'
        counts['displayDecode'] += 1
        return real_string(parser, *args, **kwargs)

    def decode(*args, **kwargs):
        counts['canonicalDecode'] += 1
        assert not warm, 'Warm media decoded canonical transcript bodies'
        return real_decode(*args, **kwargs)

    monkeypatch.setattr(resource_files.os, 'fdopen', fdopen)
    monkeypatch.setattr(media_saved_aliases.DisplayJSON, 'string', string)
    monkeypatch.setattr(foundation_history, '_decode', decode)
    app.clients.attach('saved-alias-http-browser')
    client = await authenticated_client(server)
    client.session.headers['X-Amplifier-Client'] = 'saved-alias-http-browser'
    sid = session['id']
    await app.outputs.media.identity(sid, canonical)
    record = (await app.outputs.media.attach({
        **media_args_without_hydration(session), 'messageId': 'saved-web-assistant'},
        'ui', 'saved-alias-http-attach'))['result']
    values = {**record, 'messageId': 'saved-web-assistant'}
    with app.clients.bind('saved-alias-http-browser'):
        assert await app.outputs.media.bytes(values) == gif()
    assert counts['reads'] > 0 and counts['bytes'] >= reference['bytes']
    assert counts['displayDecode'] > 0 and threads and owner not in threads
    before = dict(counts)
    warm = True
    query = {'sessionId': sid, 'messageId': 'saved-web-assistant'}
    exact = {**query, 'version': str(record['version']), 'sha256': record['sha256']}
    route = '/api/outputs/' + record['id'] + '/media'
    for sequence in (1, 2):
        descriptor = await client.get('/api/outputs/media', params=query)
        assert descriptor.status == 200
        assert (await descriptor.json())['items'][0]['id'] == record['id']
        canonical_descriptor = await client.get('/api/outputs/media', params={**query, 'messageId': canonical})
        assert canonical_descriptor.status == 200
        head = await client.head(route, params=exact)
        assert head.status == 200 and head.headers['Content-Length'] == str(len(gif()))
        response = await client.get(route, params=exact, headers={'Range': 'bytes=0-5'})
        assert response.status == 206 and await response.read() == gif()[:6]
        observed = await client.post('/api/actions', json={
            'action': 'outputs.mediaReport', 'args': {
                **query, 'id': record['id'], 'version': record['version'],
                'sha256': record['sha256'], 'generation': 1, 'sequence': sequence,
                'renderState': 'ready', 'playback': 'paused'}})
        assert observed.status == 200 and (await observed.json())['accepted']
    assert counts == before
    assert dict.get(chilled, '_coldFields') == markers and not dict.__contains__(chilled, 'messages')
    assert (directory / 'transcript.jsonl').read_bytes() == raw


@pytest.mark.parametrize('ensure_ascii', [False, True])
def test_saved_alias_incremental_full_text_hash_matches_default_json_semantics(tmp_path, ensure_ascii):
    import json
    from amplifier_web.automatic_history import display_identity
    from amplifier_web.media_saved_aliases import DisplayJSON, row_identity
    # Include multibyte boundary splits, JSON control escapes, surrogate pairs,
    # and default ", " separators. Only the existing text allocation is large.
    text = ('雪😀"\\\n\t\b\f\r\x00/' * 20000) + 'full trailing identity'
    row = {'text': text, 'id': 'saved-web-assistant', 'role': 'assistant', 'nativeIndex': 7}
    path = tmp_path / 'display.json'
    path.write_text(json.dumps([row], ensure_ascii=ensure_ascii), encoding='utf-8')
    value = {'id': 'synthetic', 'nativeIdentity': 'native-scope'}
    expected = display_identity(value, 7, 'assistant', text)
    with path.open('rb') as stream:
        rows = list(DisplayJSON(stream).rows())
        assert len(rows) == 1 and 'text' not in rows[0]
        assert row_identity(stream, value, rows[0]) == expected


def test_saved_alias_projection_bounded_memory_full_hash_and_streamed_history(tmp_path):
    import json
    import sqlite3
    import tracemalloc
    from amplifier_web.automatic_history import display_identity
    from amplifier_web.media_saved_aliases import SavedAliases
    from amplifier_web.resource_files import detach, put
    db = sqlite3.connect(tmp_path / 'state.sqlite')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic', 'nativeIdentity': 'native-scope'}
        text = '雪\\\n"' * 250000
        canonical = display_identity(value, 1, 'assistant', text)
        rows = [{'id': 'unrelated-' + str(i), 'role': 'user', 'text': 'small'} for i in range(30000)]
        rows.insert(15000, {'id': 'saved-web-assistant', 'role': 'assistant', 'nativeIndex': 1, 'text': text})
        reference = put(db, rows)
        db.commit()
        source = detach(db, reference['$resource'])
        value['coldMessages'] = reference
        del rows, text
        facts = [(canonical, 'assistant', True, 1, None, None)]
        aliases = SavedAliases()
        tracemalloc.start()
        try:
            fact, _ = aliases.resolve(source, value, 'saved-web-assistant', [1, 2], facts)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert fact == facts[0] and peak < 1024 * 1024
        assert len(aliases.cache) == 1
        assert 'text' not in repr(list(aliases.cache.values()))
        # A last-byte change matters even beyond the requested row/large text.
        path = source.directory / (source.identity + '.json')
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + b' ')
        with pytest.raises(ValueError):
            aliases.resolve(source, value, 'saved-web-assistant', [1, 2], facts)
    finally:
        db.close()


def test_detached_projection_validates_complete_hash_index_and_path_evidence(tmp_path):
    import json
    import sqlite3
    from amplifier_web.resource_files import Detached, detach, project_detached, put
    db = sqlite3.connect(tmp_path / 'state.sqlite')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        reference = put(db, ['prefix', 'suffix'])
        db.commit()
        source = detach(db, reference['$resource'])
        path = source.directory / (source.identity + '.json')
        assert project_detached(source, lambda stream: stream.read(1))[0] == b'['
        wrong_index = Detached(source.directory, source.identity, json.dumps({'$blob': 'f' * 64}))
        with pytest.raises(ValueError, match='index'):
            project_detached(wrong_index, lambda stream: stream.read(1))
        raw = path.read_bytes()
        path.write_bytes(raw[:-2] + b'x' + raw[-1:])
        with pytest.raises(ValueError, match='content'):
            project_detached(source, lambda stream: stream.read(1))
        path.write_bytes(raw)

        def replace_during_projection(stream):
            prefix = stream.read(1)
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(raw)
            replacement.replace(path)
            return prefix

        with pytest.raises(ValueError, match='changed'):
            project_detached(source, replace_during_projection)
        target = path.with_suffix('.target')
        path.rename(target)
        path.symlink_to(target)
        with pytest.raises((ValueError, OSError)):
            project_detached(source, lambda stream: stream.read(1))
    finally:
        db.close()


def test_saved_alias_cache_coalesces_workers_and_bounds_retained_descriptors(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    from amplifier_web.automatic_history import display_identity
    from amplifier_web import media_saved_aliases
    from amplifier_web.resource_files import detach, put
    db = sqlite3.connect(tmp_path / 'state.sqlite')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic', 'nativeIdentity': 'native-scope'}
        rows = [{'id': 'alias-' + str(i), 'role': 'assistant', 'nativeIndex': i, 'text': 'full text'}
                for i in range(40)]
        facts = [(display_identity(value, i, 'assistant', 'full text'), 'assistant', True, i, None, None)
                 for i in range(40)]
        reference = put(db, rows)
        db.commit()
        source = detach(db, reference['$resource'])
        value['coldMessages'] = reference
        aliases = media_saved_aliases.SavedAliases()
        real, projections = media_saved_aliases.project_detached, []

        def counted(*args):
            projections.append(args[0])
            return real(*args)

        monkeypatch.setattr(media_saved_aliases, 'project_detached', counted)
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(aliases.resolve, source, value, 'alias-0', [1, 2], facts)
                       for _ in range(8)]
            assert all(future.result()[0] == facts[0] for future in futures)
        assert len(projections) == 1, 'same concurrent proof must stream only once'
        for i in range(1, 40):
            assert aliases.resolve(source, value, 'alias-' + str(i), [1, 2], facts)[0] == facts[i]
        assert len(aliases.cache) == aliases.LIMIT == 32
    finally:
        db.close()


async def test_colliding_retained_alias_ids_do_not_share_service_authority(media_app, tmp_path):
    from amplifier_web.automatic_history import revision
    from amplifier_web.service import AppService
    from test_service import Runtime
    first = media_app
    one, _, _, _, _ = cold_alias_corpus(first)
    await freeze_alias_display(first, one)
    assert (await first.outputs.media.identity(one['id'], 'saved-web-assistant'))['fact'][1] == 'assistant'
    workspace = tmp_path / 'other-workspace'
    workspace.mkdir()
    second = AppService(tmp_path / 'other-app', workspace=workspace, runtime=Runtime())
    try:
        await second.dispatch('session.create', {'title': 'Colliding retained ID'})
        collision = second._session()
        collision.update(id=one['id'], runtimeSessionId=one['id'])
        second._state['selectedSessionId'] = one['id']
        two, corpus, store, _, _ = cold_alias_corpus(second)
        assert two['id'] == one['id']
        # Same public session/retained IDs/index, but a different exact owner.
        corpus[1]['content'] = 'Different canonical text in the other owner'
        store.save(two['id'], corpus, {}, preserve_system=True)
        two['nativeRevision'] = revision(two)
        await freeze_alias_display(second, two)
        assert first.outputs.media.saved_aliases is not second.outputs.media.saved_aliases
        with pytest.raises(ValueError, match='assistant'):
            await second.outputs.media.identity(two['id'], 'saved-web-assistant')
        assert (await first.outputs.media.identity(one['id'], 'saved-web-assistant'))['fact'][1] == 'assistant'
    finally:
        await second.close()


@pytest.mark.parametrize('raw', [
    b'[{"id":"saved","id":"duplicate","role":"assistant"}]',
    b'[{"id":"saved","role":"assistant","text":"unterminated}]',
    b'[{"id":"saved","text":"\\uD800"}]',
    b'[{"id":"saved","text":"\\x20"}]',
    b'[{"id":"saved","text":"\xff"}]',
    b'[{"id":"saved","unknown":[1,]}]',
    b'[{"id":"saved"}] trailing',
    b'[{"id":"saved","nativeIndex":01}]',
])
def test_saved_display_stream_rejects_malformed_json_without_partial_projection(raw):
    import io
    from amplifier_web.media_saved_aliases import DisplayJSON
    with pytest.raises((ValueError, UnicodeError)):
        list(DisplayJSON(io.BytesIO(raw)).rows())


@pytest.mark.parametrize('defect', ['duplicate-fact', 'hidden-fact', 'user-fact'])
def test_saved_alias_projection_requires_unique_visible_assistant_canonical_fact(tmp_path, defect):
    import sqlite3
    from amplifier_web.automatic_history import display_identity
    from amplifier_web.media_saved_aliases import SavedAliases
    from amplifier_web.resource_files import detach, put
    db = sqlite3.connect(tmp_path / 'state.sqlite')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic'}
        canonical = display_identity(value, 1, 'assistant', 'full text')
        reference = put(db, [{'id': 'saved-web-assistant', 'nativeIndex': 1,
                              'role': 'assistant', 'text': 'full text'}])
        db.commit()
        source = detach(db, reference['$resource'])
        facts = [(canonical, 'assistant', True, 1, None, None)]
        if defect == 'duplicate-fact':
            facts *= 2
        elif defect == 'hidden-fact':
            facts = [(canonical, 'assistant', False, 1, None, None)]
        else:
            facts = [(canonical, 'user', True, 1, None, None)]
        with pytest.raises(ValueError, match='assistant'):
            SavedAliases().resolve(source, value, 'saved-web-assistant', [1, 2], facts)
    finally:
        db.close()


def test_generic_inline_detached_projection_bounds_utf8_copy_and_preserves_byte_seek():
    import json
    import tracemalloc
    from amplifier_web.resource_files import Detached, project_detached
    body = json.dumps(['雪😀' * 300000, 'tail'], ensure_ascii=False)
    encoded = body.encode()
    source = Detached(None, hashlib.sha256(encoded).hexdigest(), body)
    expected = encoded[2:9]
    del encoded

    def projection(stream):
        stream.read(64 * 1024)
        stream.seek(2)
        selected = stream.read(7)
        stream.seek(0)
        assert stream.read(1) == b'['
        return selected

    tracemalloc.start()
    try:
        selected, _ = project_detached(source, projection)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert selected == expected and peak < 1024 * 1024


def _exact_media_fork_corpus(app, *, partial=False, retained_alias=False):
    from amplifier_web.automatic_history import display_message, revision
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_files import project_slug
    session = app._session()
    rows = [
        {'role': 'system', 'content': 'Synthetic system context'},
        {'role': 'developer', 'content': 'Synthetic developer context'},
        {'role': 'user', 'content': 'First media request'},
        {'role': 'user', 'content': 'First internal reference before origin',
         'metadata': {'amplifier_visible_reference': True}},
        {'role': 'user', 'content': 'Second internal reference before origin',
         'metadata': {'amplifier_visible_reference': True}},
        {'role': 'assistant', 'content': 'Starting a synthetic tool',
         'tool_calls': [{'id': 'pending-before-media-origin', 'name': 'bash'}]},
        {'role': 'assistant', 'content': 'Identical assistant media text'},
        {'role': 'user', 'content': 'Second media request'},
        {'role': 'assistant', 'content': 'Identical assistant media text'},
        {'role': 'user', 'content': 'Third media request'},
        {'role': 'assistant', 'content': 'Identical assistant media text'},
        {'role': 'user', 'content': 'Tail request'},
        {'role': 'assistant', 'content': 'Tail answer'},
    ]
    store = SessionStore.for_app(app.data_dir, session['workspace'])
    store.save(session['id'], rows, {
        'bundle_name': session['bundle'], 'working_dir': session['workspace']}, preserve_system=True)
    session.update(nativeProject=project_slug(session['workspace']), nativeIdentity=session['id'],
                   runtimeSessionId=session['id'], historyManaged=not retained_alias,
                   historyLoaded=True, status='idle')
    session['nativeRevision'] = revision(session)
    indexes = (7, 8, 9, 10, 11, 12) if partial else (11, 12)
    session['messages'] = [display_message(rows[index], index, session) for index in indexes]
    session.update(sharedHistoryOffset=3 if partial else 7, sharedHistoryTotal=9,
                   sharedHistoryUserTurnOffset=1 if partial else 3)
    if retained_alias:
        canonical = display_message(rows[6], 6, session)
        session['messages'].insert(0, {**canonical, 'id': 'proven-fork-media-ui-alias',
                                       'source': 'amplifier', 'nativeMessageId': canonical['id']})
    return session, rows, store


@pytest.mark.parametrize('scenario', [
    'inserted-receipt', 'partial-reference-removal', 'bundle-reference-remap',
    'retained-alias-canonical-origin', 'retained-alias-ui-origin',
])
async def test_exact_media_fork_origin_descriptor_head_range_and_source_tuple(
        authenticated_client, tmp_path, scenario):
    import copy
    from pathlib import Path
    from amplifier_web.automatic_history import display_message
    from amplifier_web.server import create_app
    from amplifier_web.session_store import fork_session
    from test_service import Runtime
    server = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                              voice=False, preload_providers=False, background_updates=False)
    app = server['service']
    await app.dispatch('session.create', {'title': 'Exact fork origin corpus'})
    partial = scenario in {'partial-reference-removal', 'bundle-reference-remap'}
    retained = scenario.startswith('retained-alias-')
    source, rows, store = _exact_media_fork_corpus(app, partial=partial, retained_alias=retained)
    sid = source['id']
    raw = (store.directory(sid) / 'transcript.jsonl').read_bytes()
    original_messages = copy.deepcopy(source['messages'])
    saved = {}
    for index, data in ((6, gif()), (10, gif(frames=3))):
        canonical = display_message(rows[index], index, source)['id']
        origin = ('proven-fork-media-ui-alias'
                  if index == 6 and scenario == 'retained-alias-ui-origin' else canonical)
        filename = f'synthetic-fork-{index}.gif'
        Path(source['workspace'], filename).write_bytes(data)
        receipt = (await app.outputs.media.attach({
            'sessionId': sid, 'messageId': origin, 'title': f'Synthetic media at {index}',
            'path': filename, 'expectedSha256': hashlib.sha256(data).hexdigest(),
        }, 'ui', f'exact-fork-attach-{index}'))['result']
        saved[index] = (copy.deepcopy(app.outputs.record(sid, receipt['id'])), data)
        Path(source['workspace'], filename).unlink()
    if scenario == 'bundle-reference-remap':
        # session.fork has no bundle argument. Compose the existing transcript
        # owner and output cloner directly, without pretending to test that API.
        origins = {}
        async with app.lock:
            target = app._new_session({'title': 'Synthetic bundle fork',
                                       'workspace': source['workspace'], 'bundle': source['bundle']})
            target.update(fork_session(app.data_dir, source, target['id'], turn=2,
                                       bundle='synthetic-other-bundle', media_origins=origins))
            assert not any('media' in key.lower() for key in target)
            app.state['sessions'].insert(0, target)
            app.state['selectedSessionId'] = target['id']
            app.outputs.fork(sid, target, media_origins=origins)
            app.db.commit()
    else:
        await app.dispatch('session.fork', {'id': sid, **({'turn': 2} if partial else {})})
        target = app._session()
    target_rows = store.load(target['id'])[0]
    if partial:
        # Two reference deletions and one new receipt precede the origin.
        assert len(target_rows) == (6 if scenario == 'bundle-reference-remap' else 8)
        assert not any(row.get('metadata', {}).get('amplifier_visible_reference') for row in target_rows)
        first_index, decoy_index = ((3, 5) if scenario == 'bundle-reference-remap' else (5, 7))
        assert 'Third media request' not in str(target_rows)
    else:
        assert len(target_rows) == len(rows) + 1
        assert target_rows[:6] == rows[:6] and target_rows[7:] == rows[6:]
        first_index, decoy_index = 7, 9
    receipt = target_rows[first_index - 1]
    assert receipt['role'] == 'tool' and receipt['tool_call_id'] == 'pending-before-media-origin'
    import json
    assert json.loads(receipt['content'])['status'] == 'interrupted'
    if scenario == 'bundle-reference-remap':
        assert not any(row['role'] in {'system', 'developer'} for row in target_rows)
    cloned = [row for row in app.outputs.store.list(target['id'])['items'] if row['kind'] == 'media']
    expected_indexes = (6,) if partial else (6, 10)
    assert len(cloned) == len(expected_indexes)
    assert {row['forkSource']['outputId'] for row in cloned} == {saved[index][0]['id'] for index in expected_indexes}
    app.clients.attach('exact-fork-http-client')
    with app.clients.bind('exact-fork-http-client'):
        await app.dispatch('session.select', {'id': target['id']})
    client = await authenticated_client(server)
    client.session.headers['X-Amplifier-Client'] = 'exact-fork-http-client'
    for index in expected_indexes:
        original, data = saved[index]
        new_index = first_index if index == 6 else 11
        canonical_target = display_message(target_rows[new_index], new_index, target)['id']
        expected_id = 'proven-fork-media-ui-alias' if retained and index == 6 else canonical_target
        kept = next(row for row in target['messages'] if row.get('nativeIndex') == new_index)
        assert kept['id'] == expected_id and kept['text'] == rows[index]['content']
        if retained and index == 6:
            assert kept['nativeMessageId'] == canonical_target
            assert kept['id'] == original_messages[0]['id']
        clone = next(row for row in cloned if row['forkSource']['outputId'] == original['id'])
        internal = app.outputs.record(target['id'], clone['id'])
        assert internal['origin']['messageId'] == expected_id
        assert internal['id'] != original['id'] and internal['sessionId'] == target['id']
        assert internal['sha256'] == original['sha256'] == hashlib.sha256(data).hexdigest()
        assert internal['bytes'] == original['bytes'] == len(data)
        assert internal['body'] == original['body'] and internal['media'] == original['media']
        assert internal['forkSource'] == {'sessionId': sid, 'outputId': original['id'],
                                         'sha256': original['sha256']}
        query = {'sessionId': target['id'], 'messageId': expected_id}
        descriptor = await client.get('/api/outputs/media', params=query)
        assert descriptor.status == 200
        items = (await descriptor.json())['items']
        assert len(items) == 1 and items[0]['id'] == clone['id']
        assert items[0]['sha256'] == original['sha256'] and items[0]['bytes'] == len(data)
        assert 'body' not in items[0]
        exact = {**query, 'version': str(internal['version']), 'sha256': internal['sha256']}
        route = '/api/outputs/' + clone['id'] + '/media'
        head = await client.head(route, params=exact)
        assert head.status == 200 and await head.read() == b''
        assert head.headers['Content-Length'] == str(len(data))
        ranged = await client.get(route, params=exact, headers={'Range': 'bytes=2-7'})
        assert ranged.status == 206 and await ranged.read() == data[2:8]
        assert ranged.headers['Content-Length'] == '6'
        assert ranged.headers['Content-Range'] == f'bytes 2-7/{len(data)}'
        full = await client.get(route, params=exact)
        assert full.status == 200 and await full.read() == data
        wrong_session = {**exact, 'sessionId': sid, 'messageId': original['origin']['messageId']}
        assert (await client.head(route, params=wrong_session)).status == 400
        assert (await client.get(route, params=wrong_session, headers={'Range': 'bytes=2-7'})).status == 400
        # Bind to the source too: a refused source tuple must not depend only
        # on the current client's target selection.
        with app.clients.bind('exact-fork-http-client'):
            await app.dispatch('session.select', {'id': sid})
        assert (await client.get(route, params=wrong_session)).status == 400
        with app.clients.bind('exact-fork-http-client'):
            await app.dispatch('session.select', {'id': target['id']})
    decoy = next(row for row in target['messages'] if row.get('nativeIndex') == decoy_index)
    assert decoy['text'] == rows[6]['content']
    empty = await client.get('/api/outputs/media', params={'sessionId': target['id'], 'messageId': decoy['id']})
    assert empty.status == 200 and (await empty.json())['items'] == []
    assert source['messages'] == original_messages
    assert (store.directory(sid) / 'transcript.jsonl').read_bytes() == raw
    for original, _ in saved.values():
        assert app.outputs.record(sid, original['id']) == original


async def test_media_fork_refuses_live_alias_without_original_binding_despite_inferred_index(media_app):
    import copy
    from amplifier_web.automatic_history import display_message, revision
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_files import project_slug
    app = media_app
    source = app._session()
    args = media_args(app)
    attached = (await app.dispatch('outputs.attachMedia', args))['result']
    original_output = copy.deepcopy(app.outputs.record(source['id'], attached['id']))
    unbound = copy.deepcopy(source['messages'][0])
    unbound.pop('nativeIndex', None)
    unbound.pop('nativeMessageId', None)
    rows = [
        {'role': 'user', 'content': 'First saved request'},
        {'role': 'assistant', 'content': unbound['text']},
        {'role': 'user', 'content': 'Second saved request'},
        {'role': 'assistant', 'content': unbound['text']},
    ]
    store = SessionStore.for_app(app.data_dir, source['workspace'])
    store.save(source['id'], rows, {}, preserve_system=True)
    source.update(nativeProject=project_slug(source['workspace']), nativeIdentity=source['id'],
                  runtimeSessionId=source['id'], historyManaged=False, historyLoaded=True, status='idle',
                  sharedHistoryOffset=0, sharedHistoryUserTurnOffset=0)
    source['nativeRevision'] = revision(source)
    source['messages'] = [display_message(rows[0], 0, source), unbound,
                          display_message(rows[2], 2, source),
                          {'id': 'another-unbound-media-ui-id', 'role': 'assistant', 'text': unbound['text']}]
    original_messages = copy.deepcopy(source['messages'])
    raw = (store.directory(source['id']) / 'transcript.jsonl').read_bytes()
    await app.dispatch('session.fork', {'id': source['id']})
    target = app._session()
    kept = next(row for row in target['messages'] if row['id'] == args['messageId'])
    assert kept['nativeIndex'] == 1 and 'nativeIndex' not in original_messages[1]
    assert [row for row in app.outputs.store.list(target['id'])['items'] if row['kind'] == 'media'] == []
    app.clients.attach('unbound-fork-media-client')
    with app.clients.bind('unbound-fork-media-client'):
        await app.dispatch('session.select', {'id': target['id']})
        assert (await app.outputs.media.describe(target['id'], kept['id']))['items'] == []
    assert store.load(target['id'])[0] == rows
    assert source['messages'] == original_messages
    assert (store.directory(source['id']) / 'transcript.jsonl').read_bytes() == raw
    assert app.outputs.record(source['id'], attached['id']) == original_output


@pytest.mark.parametrize('message_spelling', ['canonical', 'retained'])
def test_saved_alias_warm_cache_rechecks_current_canonical_id_collisions(
        tmp_path, message_spelling):
    import sqlite3
    from amplifier_web.automatic_history import display_identity
    from amplifier_web.media_saved_aliases import SavedAliases
    from amplifier_web.resource_files import detach, put
    db = sqlite3.connect(tmp_path / 'synthetic.sqlite')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic-scope'}
        canonical = display_identity(value, 1, 'assistant', 'Exact saved text')
        reference = put(db, [{'id': 'retained-alias', 'role': 'assistant',
                              'nativeIndex': 1, 'text': 'Exact saved text'}])
        db.commit()
        value['coldMessages'] = reference
        source = detach(db, reference['$resource'])
        fact = (canonical, 'assistant', True, 1, None, None)
        requested = canonical if message_spelling == 'canonical' else 'retained-alias'
        aliases = SavedAliases()
        assert aliases.resolve(source, value, requested, [1, 2], [fact])[0] == fact
        # Same revision tuple is not permission to ignore changed index facts.
        changed = [fact, ('retained-alias', 'assistant', True, 7, None, None)]
        with pytest.raises(ValueError, match='assistant'):
            aliases.resolve(source, value, requested, [1, 2], changed)
        with pytest.raises(ValueError, match='assistant'):
            SavedAliases().resolve(source, value, requested, [1, 2], changed)
    finally:
        db.close()


def test_large_legacy_inline_saved_alias_warm_requests_require_no_body_reads(monkeypatch):
    """Unresolved inline freshness/cache discriminator; never xfail it.

    The file-backed cache does not retain a large inline index as its key. A
    compact exact-index freshness seam is still required for this warm path.
    """
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    from amplifier_web.automatic_history import display_identity
    from amplifier_web import media_saved_aliases, resource_files
    db = sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic-inline-scope'}
        text = 'Exact inline saved text 雪\n' * 5000
        canonical = display_identity(value, 1, 'assistant', text)
        reference = resource_files.put(db, [{'id': 'legacy-inline-alias',
            'role': 'assistant', 'nativeIndex': 1, 'text': text}])
        db.commit()
        source = resource_files.detach(db, reference['$resource'])
        assert source.directory is None and len(source.indexed) > 1024
        value['coldMessages'] = reference  # Bare legacy reference, no hints.
        facts = [(canonical, 'assistant', True, 1, None, None)]
        aliases = media_saved_aliases.SavedAliases()
        for requested in (canonical, 'legacy-inline-alias'):
            assert aliases.resolve(source, value, requested, [1, 2], facts)[0] == facts[0]
        reads, decodes = [], []
        read = resource_files._InlineStream.read
        rows = media_saved_aliases.DisplayJSON.rows

        def counted_read(stream, size):
            reads.append(size)
            return read(stream, size)

        def counted_rows(parser):
            decodes.append(True)
            yield from rows(parser)

        monkeypatch.setattr(resource_files._InlineStream, 'read', counted_read)
        monkeypatch.setattr(media_saved_aliases.DisplayJSON, 'rows', counted_rows)
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(aliases.resolve, source, value, requested, [1, 2], facts)
                       for requested in (canonical, 'legacy-inline-alias') * 2]
            assert all(future.result()[0] == facts[0] for future in futures)
        assert reads == [], 'Warm inline identity still reads the saved display body'
        assert decodes == [], 'Warm inline identity still decodes the saved display body'
        assert resource_files.detach(db, reference['$resource']) == source
        assert value['coldMessages'] == reference and 'messages' not in value
    finally:
        db.close()


@pytest.mark.parametrize('file_backed', [False, True])
@pytest.mark.parametrize('message_spelling', ['canonical', 'retained'])
def test_legacy_inline_alias_warm_cache_fresh_evidence_rejects_same_length_suffix_corruption(
        tmp_path, monkeypatch, file_backed, message_spelling):
    import sqlite3
    from amplifier_web.automatic_history import display_identity
    from amplifier_web import media_saved_aliases, resource_files
    db = sqlite3.connect(tmp_path / 'legacy.sqlite' if file_backed else ':memory:')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic-inline-scope'}
        text = 'Exact inline saved text 雪😀\n' * 5000
        canonical = display_identity(value, 1, 'assistant', text)
        prepared = resource_files.prepare([
            {'id': 'legacy-inline-alias', 'role': 'assistant', 'nativeIndex': 1, 'text': text},
            {'id': 'unrelated-suffix', 'role': 'user', 'text': 'tail-original'}])
        # Legacy SQLite inline rows also exist in file-backed databases. Do not
        # replace this discriminator with put()'s file-backed pointer form.
        db.execute('INSERT INTO state_resources VALUES (?, ?)', (prepared.identity, prepared.text))
        db.commit()
        reference = {'$resource': prepared.identity, 'bytes': prepared.size}
        value['coldMessages'] = reference
        source = resource_files.detach(db, prepared.identity)
        assert (source.directory is not None) == file_backed and len(source.indexed) > 1024
        fact = (canonical, 'assistant', True, 1, None, None)
        requested = canonical if message_spelling == 'canonical' else 'legacy-inline-alias'
        aliases = media_saved_aliases.SavedAliases()
        assert aliases.resolve(source, value, requested, [1, 2], [fact])[0] == fact
        assert len(aliases.cache) == 1
        key = next(iter(aliases.cache))
        assert key[:4] == (source.directory, prepared.identity, prepared.identity, prepared.size)
        assert source.indexed not in repr(aliases.cache) and text not in repr(aliases.cache)
        keys = tuple(aliases.cache)
        fingerprints = []
        real = media_saved_aliases._indexed_evidence

        def fingerprint(indexed):
            assert aliases.lock.locked()
            evidence = real(indexed)
            fingerprints.append(evidence)
            return evidence

        def forbidden(*args, **kwargs):
            raise AssertionError('Warm inline identity must not stream or decode the saved body')

        monkeypatch.setattr(media_saved_aliases, '_indexed_evidence', fingerprint)
        monkeypatch.setattr(resource_files._InlineStream, 'read', forbidden)
        monkeypatch.setattr(media_saved_aliases.DisplayJSON, 'rows', forbidden)
        for _ in range(2):
            fresh = resource_files.detach(db, prepared.identity)
            assert aliases.resolve(fresh, value, requested, [1, 2], [fact])[0] == fact
        assert fingerprints == [(prepared.identity, prepared.size)] * 2
        # Alter only a trailing, unrelated row; requested canonical facts and
        # resource ID, byte count, scope and revision all remain unchanged.
        changed = prepared.text.replace('tail-original', 'tail-tampered')
        assert changed != prepared.text and len(changed) == len(prepared.text)
        assert len(changed.encode('utf-8')) == prepared.size
        db.execute('UPDATE state_resources SET value=? WHERE id=?', (changed, prepared.identity))
        db.commit()
        fresh = resource_files.detach(db, prepared.identity)
        with pytest.raises(ValueError, match='Artifact content conflicts with its identity'):
            aliases.resolve(fresh, value, requested, [1, 2], [fact])
        assert len(fingerprints) == 3 and fingerprints[-1][0] != prepared.identity
        assert fingerprints[-1][1] == prepared.size and tuple(aliases.cache) == keys
        assert value['coldMessages'] == reference and 'messages' not in value
    finally:
        db.close()


def test_saved_alias_indexed_evidence_bounds_temporary_utf8_encoding():
    import tracemalloc
    from amplifier_web.media_saved_aliases import _indexed_evidence
    text = ('雪😀\\\n' * 300000) + 'complete trailing suffix'
    encoded = text.encode('utf-8')
    expected = (hashlib.sha256(encoded).hexdigest(), len(encoded))
    del encoded
    chunks = []

    class BoundedText(str):
        def encode(self, *args, **kwargs):
            raise AssertionError('The full indexed body must not be encoded at once')

        def __getitem__(self, key):
            assert isinstance(key, slice) and 0 < key.stop - key.start <= 8192
            chunk = super().__getitem__(key)
            chunks.append(len(chunk))
            return chunk

    indexed = BoundedText(text)
    del text
    # This measures temporary Python allocations after the captured string
    # exists, not SQLite capture or native heap use.
    tracemalloc.start()
    try:
        evidence = _indexed_evidence(indexed)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert evidence == expected
    assert sum(chunks) == len(indexed) and max(chunks) == 8192
    assert peak < 256 * 1024
    with pytest.raises(UnicodeEncodeError):
        _indexed_evidence('invalid lone surrogate \ud800')


@pytest.mark.parametrize('file_backed', [False, True])
def test_large_legacy_inline_alias_cache_coalesces_and_evicts_without_retaining_body(
        tmp_path, monkeypatch, file_backed):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    from amplifier_web.automatic_history import display_identity
    from amplifier_web import media_saved_aliases, resource_files
    db = sqlite3.connect(tmp_path / 'legacy-cache.sqlite' if file_backed else ':memory:')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic-inline-scope'}
        text = 'full text 雪😀' * 64
        rows = [{'id': 'alias-' + str(i), 'role': 'assistant', 'nativeIndex': i, 'text': text}
                for i in range(40)]
        facts = [(display_identity(value, i, 'assistant', text), 'assistant', True, i, None, None)
                 for i in range(40)]
        prepared = resource_files.prepare(rows)
        db.execute('INSERT INTO state_resources VALUES (?, ?)', (prepared.identity, prepared.text))
        db.commit()
        value['coldMessages'] = {'$resource': prepared.identity, 'bytes': prepared.size}
        source = resource_files.detach(db, prepared.identity)
        assert len(source.indexed) > 1024 and (source.directory is not None) == file_backed
        aliases = media_saved_aliases.SavedAliases()
        real_projection, real_fingerprint = media_saved_aliases.project_detached, media_saved_aliases._indexed_evidence
        projections, fingerprints = [], []

        def project(*args):
            projections.append(True)
            return real_projection(*args)

        def fingerprint(indexed):
            assert aliases.lock.locked()
            fingerprints.append(True)
            return real_fingerprint(indexed)

        monkeypatch.setattr(media_saved_aliases, 'project_detached', project)
        monkeypatch.setattr(media_saved_aliases, '_indexed_evidence', fingerprint)
        captures = [resource_files.detach(db, prepared.identity) for _ in range(8)]
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(aliases.resolve, captured, value, 'alias-0', [1, 2], facts)
                       for captured in captures]
            assert all(future.result()[0] == facts[0] for future in futures)
        assert len(projections) == 1 and len(fingerprints) == 8
        for i in range(1, 40):
            fresh = resource_files.detach(db, prepared.identity)
            assert aliases.resolve(fresh, value, 'alias-' + str(i), [1, 2], facts)[0] == facts[i]
        assert len(aliases.cache) == aliases.LIMIT == 32
        assert len(projections) == 40 and len(fingerprints) == 47

        def compact(item):
            assert not isinstance(item, resource_files.Detached)
            if isinstance(item, str):
                assert len(item) <= 1024, 'Cache retained an inline body rather than compact evidence'
            elif isinstance(item, dict):
                for key, value in item.items():
                    compact(key)
                    compact(value)
            elif isinstance(item, (tuple, list)):
                for value in item:
                    compact(value)

        compact(aliases.cache)
        assert prepared.text not in repr(aliases.cache)
        # alias-0 is actually evicted, not merely hidden by a reported cap.
        assert aliases.resolve(source, value, 'alias-0', [1, 2], facts)[0] == facts[0]
        assert len(projections) == 41 and len(fingerprints) == 48
        assert len(aliases.cache) == 32
    finally:
        db.close()


async def test_legacy_inline_alias_fresh_hashing_runs_off_owner_thread_on_warm_media(
        media_app, monkeypatch):
    import copy
    import threading
    from amplifier_web import media_saved_aliases, resource_files
    app = media_app
    session, _, _, _, canonical = cold_alias_corpus(app, native_message_id=False)
    sid = session['id']
    prepared = resource_files.prepare(dict.get(session, 'messages'))
    chilled, reference = await freeze_alias_display(app, session)
    assert reference['$resource'] == prepared.identity
    async with app.lock:
        # An unchanged content-addressed body in the legacy inline representation.
        app.db.execute('UPDATE state_resources SET value=? WHERE id=?', (prepared.text, prepared.identity))
        app.db.commit()
    source = resource_files.detach(app.db, prepared.identity)
    assert source.directory is not None and source.indexed == prepared.text
    markers = copy.deepcopy(dict.get(chilled, '_coldFields'))
    owner, threads = threading.get_ident(), []
    real = media_saved_aliases._indexed_evidence

    def fingerprint(indexed):
        thread = threading.get_ident()
        assert thread != owner, 'Fresh body-sized hashing must remain in the existing worker'
        threads.append(thread)
        return real(indexed)

    monkeypatch.setattr(media_saved_aliases, '_indexed_evidence', fingerprint)
    for requested in (canonical, 'saved-web-assistant'):
        assert (await app.outputs.media.identity(sid, requested))['fact'][0] == canonical
    assert len(threads) == 2

    def forbidden(*args, **kwargs):
        raise AssertionError('Warm media must not stream/decode the legacy inline saved body')

    monkeypatch.setattr(resource_files._InlineStream, 'read', forbidden)
    monkeypatch.setattr(media_saved_aliases.DisplayJSON, 'rows', forbidden)
    app.clients.attach('inline-worker-browser')
    with app.clients.bind('inline-worker-browser'):
        for requested in (canonical, 'saved-web-assistant'):
            assert (await app.outputs.media.describe(sid, requested))['items'] == []
    assert len(threads) == 4 and owner not in threads
    assert dict.get(chilled, '_coldFields') == markers and not dict.__contains__(chilled, 'messages')


@pytest.mark.parametrize('boundary', ['commit', 'post-file-fence', 'final-fence'])
async def test_cold_saved_alias_attach_reaches_all_fences_without_display_hydration(
        media_app, monkeypatch, boundary):
    import copy
    import json
    from amplifier_web import cold_display, output_media, resource_files
    app = media_app
    session, _, _, directory, _ = cold_alias_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': 'saved-web-assistant'}
    session.update(
        execution={'nodes': [{'id': 'retained-tool', 'kind': 'tool', 'output': 'e' * 20000}], 'turns': []},
        historyActivity={'retained': 'h' * 20000},
        configuration={'plan': {'tools': []}, 'provenance': {}, 'retained': 'c' * 20000})
    app._state['selectedSessionId'] = None
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    assert session['id'] in app.cold_display.retire(force=True)
    markers = copy.deepcopy(dict.get(session, '_coldFields'))
    assert set(markers) == cold_display.SESSION_FIELDS
    reference_bytes = json.dumps(markers, sort_keys=True).encode()
    transcript = (directory / 'transcript.jsonl').read_bytes()
    marker = directory / 'transfer-fence.json'
    assert not marker.exists()
    checkpoints = []
    completed = {'file': False, 'resource': False}
    real_read, real_put = output_media.read_verified, resource_files.put_prepared_async
    real_context = app.portability.write_context

    def assert_cold():
        assert app._session(session['id'], hydrate=False) is session
        assert json.dumps(dict.get(session, '_coldFields'), sort_keys=True).encode() == reference_bytes
        assert all(not dict.__contains__(session, field) for field in cold_display.SESSION_FIELDS)

    def no_hydration(*args, **kwargs):
        pytest.fail('Media attachment hydrated a cold display field')

    def read(*values):
        result = real_read(*values)
        completed['file'] = True
        return result

    async def put(db, prepared):
        result = await real_put(db, prepared)
        completed['resource'] = True
        return result

    def write_context(sid):
        assert sid == session['id'] and app.lock.locked()
        assert_cold()
        checkpoints.append((completed['file'], completed['resource'], app.db.in_transaction))
        if ((boundary == 'post-file-fence' and len(checkpoints) == 2)
                or (boundary == 'final-fence' and len(checkpoints) == 3)):
            marker.write_bytes(b'malformed but present transfer evidence')
        try:
            return real_context(sid)
        finally:
            assert_cold()

    command = 'cold-fence-attach'
    try:
        with monkeypatch.context() as patch:
            patch.setattr(cold_display, 'load', no_hydration)
            patch.setattr(app.cold_display, 'hydrate', no_hydration)
            patch.setattr(output_media, 'read_verified', read)
            patch.setattr(resource_files, 'put_prepared_async', put)
            patch.setattr(app.portability, 'write_context', write_context)
            if boundary == 'commit':
                result = await app.outputs.media.attach(args, 'ui', command)
                assert result['accepted']
                record = app.outputs.record(session['id'], result['result']['id'])
                assert record['origin'] == {'source': 'ui', 'messageId': 'saved-web-assistant'}
                assert record['sha256'] == args['expectedSha256'] and record['bytes'] == len(gif())
                assert session['mediaRevision'] == 1
                assert app.db.execute('SELECT COUNT(*) FROM output_records WHERE session_id=?',
                                      (session['id'],)).fetchone()[0] == 1
                assert app.db.execute('SELECT COUNT(*) FROM output_receipts WHERE id=?',
                                      (command,)).fetchone()[0] == 1
            else:
                with pytest.raises(ValueError, match='fenced for transfer'):
                    await app.outputs.media.attach(args, 'ui', command)
                assert session.get('mediaRevision', 0) == 0
                assert app.outputs.store.list(session['id'])['items'] == []
                assert app.db.execute('SELECT 1 FROM output_receipts WHERE id=?', (command,)).fetchone() is None
            expected = [(False, False, False), (True, False, False), (True, True, True)]
            assert checkpoints == (expected[:2] if boundary == 'post-file-fence' else expected)
            assert_cold()
            assert not app.db.in_transaction
        assert (directory / 'transcript.jsonl').read_bytes() == transcript
    finally:
        marker.unlink(missing_ok=True)


async def assert_resident_media_refused(app, session, directory, identities, monkeypatch):
    """Actual attachment and descriptor boundaries, not an identity-only probe."""
    from amplifier_web.service import AppError
    task = getattr(app, '_storage_maintenance_task', None)
    if task:
        await task
    args = media_args_without_hydration(session)
    raw = (directory / 'transcript.jsonl').read_bytes()
    records = app.db.execute('SELECT * FROM output_records ORDER BY id').fetchall()
    receipts = app.db.execute('SELECT * FROM output_receipts ORDER BY id').fetchall()
    media_revision = session.get('mediaRevision', 0)
    app.clients.attach('resident-refusal-browser')
    publications = []
    real_publish = app._publish_changes

    def publish(*args, **kwargs):
        publications.append((args, kwargs))
        return real_publish(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(app, '_publish_changes', publish)
        with app.clients.bind('resident-refusal-browser'):
            for index, identity in enumerate(identities):
                with pytest.raises(ValueError, match='assistant'):
                    await app.outputs.media.describe(session['id'], identity)
                command = 'resident-refusal-' + str(index)
                with pytest.raises(AppError):
                    await app.dispatch('outputs.attachMedia', {**args, 'messageId': identity},
                                       command_id=command)
        assert publications == []
    assert app.db.execute('SELECT * FROM output_records ORDER BY id').fetchall() == records
    assert app.db.execute('SELECT * FROM output_receipts ORDER BY id').fetchall() == receipts
    assert session.get('mediaRevision', 0) == media_revision
    assert not app.db.in_transaction
    assert (directory / 'transcript.jsonl').read_bytes() == raw


@pytest.mark.parametrize('case', ['canonical-user-collision', 'absent-canonical-conflicting-alias'])
async def test_resident_root_authority_counterexamples_refuse_without_publication(
        media_app, monkeypatch, case):
    """The two frozen ROOT falsifiers stay synthetic and source-preserving."""
    from amplifier_web.automatic_history import display_message
    app = media_app
    session, corpus, _, directory, canonical = cold_alias_corpus(app)
    if case == 'canonical-user-collision':
        # Change only the alias spelling, not its valid assistant binding.
        session['messages'][0]['id'] = display_message(corpus[0], 0, session)['id']
        identities = [session['messages'][0]['id'], canonical]
    else:
        assert canonical not in {row['id'] for row in session['messages']}
        session['messages'].append({**session['messages'][0],
                                   'id': 'conflicting-retained-assistant',
                                   'text': 'Different retained claimant text.'})
        identities = ['saved-web-assistant', canonical]
    await assert_resident_media_refused(app, session, directory, identities, monkeypatch)


@pytest.mark.parametrize('defect', [
    'duplicate-id', 'duplicate-id-other-index', 'competing-alias', 'conflicting-text',
    'wrong-native-id', 'wrong-index', 'bool-index', 'user', 'malformed-role',
    'malformed-text', 'observation', 'observation-id', 'malformed-id', 'forged-native',
    'canonical-user-collision', 'canonical-other-assistant-collision',
    'repeated-text-other-index',
])
async def test_resident_saved_alias_conflicts_refuse_both_spellings(media_app, monkeypatch, defect):
    from amplifier_web.automatic_history import display_message, revision
    app = media_app
    session, corpus, store, directory, canonical = cold_alias_corpus(app)
    row = session['messages'][0]
    if defect == 'duplicate-id':
        session['messages'].append(dict(row))
    elif defect == 'duplicate-id-other-index':
        other = display_message(corpus[3], 3, session)
        session['messages'].append({**other, 'id': row['id'], 'source': 'amplifier',
                                    'nativeMessageId': other['id']})
    elif defect == 'competing-alias':
        session['messages'].append({**row, 'id': 'second-retained-alias'})
    elif defect == 'conflicting-text':
        row['text'] += 'not canonical'
    elif defect == 'wrong-native-id':
        row['nativeMessageId'] = '0' * 32
    elif defect == 'wrong-index':
        row['nativeIndex'] = 3
    elif defect == 'bool-index':
        row['nativeIndex'] = True
    elif defect == 'user':
        row['role'] = 'user'
    elif defect == 'malformed-role':
        row['role'] = {'malformed': True}
    elif defect == 'malformed-text':
        row['text'] = {'malformed': True}
    elif defect == 'observation':
        row['observation'] = {'kind': 'tool'}
    elif defect == 'observation-id':
        row['observation_id'] = 'observation'
    elif defect == 'malformed-id':
        row['id'] = '\x00malformed'
    elif defect == 'forged-native':
        session['historyManaged'] = True
        row['source'] = 'native'
    elif defect == 'canonical-user-collision':
        row['id'] = display_message(corpus[0], 0, session)['id']
    elif defect == 'canonical-other-assistant-collision':
        row['id'] = display_message(corpus[3], 3, session)['id']
    else:
        # Identical text at a different transcript index is not the same fact.
        corpus[3]['content'] = corpus[1]['content']
        store.save(session['id'], corpus, {}, preserve_system=True)
        session['nativeRevision'] = revision(session)
        other = display_message(corpus[3], 3, session)
        row.update(id=other['id'], nativeIndex=3)
        assert row['nativeMessageId'] == canonical and other['id'] != canonical
    await assert_resident_media_refused(app, session, directory, [row['id'], canonical], monkeypatch)


@pytest.mark.parametrize('native_message_id', [False, True])
@pytest.mark.parametrize('shape', ['retained-only', 'retained-and-canonical', 'canonical-only'])
async def test_resident_exact_alias_and_canonical_spelling_remain_valid(
        media_app, native_message_id, shape):
    import copy
    from amplifier_web.automatic_history import display_message
    app = media_app
    session, corpus, _, directory, canonical = cold_alias_corpus(app, native_message_id=native_message_id)
    if shape == 'retained-and-canonical':
        session['messages'].append(display_message(corpus[1], 1, session))
    elif shape == 'canonical-only':
        session['messages'][0]['id'] = canonical
    rows = copy.deepcopy(session['messages'])
    raw = (directory / 'transcript.jsonl').read_bytes()
    args = media_args_without_hydration(session)
    app.clients.attach('resident-positive-browser')
    with app.clients.bind('resident-positive-browser'):
        for index, identity in enumerate([session['messages'][0]['id'], canonical]):
            proof = await app.outputs.media.identity(session['id'], identity, scoped=True)
            assert proof['fact'][0] == canonical and proof['fact'][3] == 1
            assert len(proof['residentClaimants']) == (2 if shape == 'retained-and-canonical' else 1)
            assert await app.outputs.media.identity(session['id'], identity, scoped=True) == proof
            record = (await app.dispatch('outputs.attachMedia', {**args, 'messageId': identity},
                                        command_id='resident-positive-' + str(index)))['result']
            items = (await app.outputs.media.describe(session['id'], identity))['items']
            assert any(item['id'] == record['id'] for item in items)
            assert await app.outputs.media.bytes({**record, 'messageId': identity}) == gif()
    assert session['messages'] == rows
    assert (directory / 'transcript.jsonl').read_bytes() == raw


async def test_large_resident_alias_revision_and_full_text_hashing_stay_off_owner(
        media_app, monkeypatch):
    import threading
    from amplifier_web import automatic_history, media_message_identity
    app = media_app
    task = getattr(app, '_storage_maintenance_task', None)
    if task:
        await task
    session, corpus, store, directory, _ = cold_alias_corpus(app, native_message_id=False)
    text = 'Synthetic full-text resident alias 雪.\n' * 32768
    corpus[1]['content'] = text
    store.save(session['id'], corpus, {}, preserve_system=True)
    session['messages'][0]['text'] = text
    session['nativeRevision'] = automatic_history.revision(session)
    canonical = automatic_history.display_message(corpus[1], 1, session)['id']
    raw = (directory / 'transcript.jsonl').read_bytes()
    args = media_args_without_hydration(session)
    owner = threading.get_ident()
    phase = threading.local()
    revisions, hashes, captures = [], [], []
    real_revision, real_hash = automatic_history.revision, automatic_history.display_identity
    real_capture, real_complete = media_message_identity.resident_claimants, media_message_identity.complete

    def revision(value):
        thread = threading.get_ident()
        assert thread != owner, 'Canonical revision filesystem metadata ran on the owner'
        revisions.append((getattr(phase, 'completion', False), thread))
        return real_revision(value)

    def display_identity(value, index, role, full_text):
        thread = threading.get_ident()
        assert thread != owner, 'Full-text identity serialization/hash ran on the owner'
        hashes.append((getattr(phase, 'completion', False), len(full_text), thread))
        return real_hash(value, index, role, full_text)

    def capture(value, message_id, fact):
        assert threading.get_ident() == owner and app.lock.locked()
        rows = real_capture(value, message_id, fact)
        assert len(rows) == 1 and rows[0]['text'] is text
        captures.append(app.db.in_transaction)
        return rows

    def complete(proof, message_id, rows):
        assert threading.get_ident() != owner
        assert 'residentClaimants' not in proof
        phase.completion = True
        try:
            return real_complete(proof, message_id, rows)
        finally:
            phase.completion = False

    monkeypatch.setattr(automatic_history, 'revision', revision)
    monkeypatch.setattr(automatic_history, 'display_identity', display_identity)
    monkeypatch.setattr(media_message_identity, 'resident_claimants', capture)
    monkeypatch.setattr(media_message_identity, 'complete', complete)
    app.clients.attach('large-resident-browser')
    with app.clients.bind('large-resident-browser'):
        for index, spelling in enumerate((canonical, 'saved-web-assistant')):
            proof = await app.outputs.media.identity(session['id'], spelling, scoped=True)
            frozen = dict(proof)
            unfinished = {key: value for key, value in proof.items() if key != 'residentClaimants'}
            async with app.lock:
                with pytest.raises(ValueError, match='worker qualification'):
                    app.outputs.media.assistant(session, spelling, unfinished)
                assert 'residentClaimants' not in unfinished
                assert app.outputs.media.assistant(session, spelling, proof)['role'] == 'assistant'
                assert proof == frozen
            record = (await app.outputs.media.attach(
                {**args, 'messageId': spelling}, 'ui', 'large-resident-' + str(index)))['result']
            assert (await app.outputs.media.describe(session['id'], spelling))['items'][0]['id'] == record['id']
            exact = {**record, 'messageId': spelling}
            assert await app.outputs.media.bytes(exact) == gif()
            assert await app.outputs.media.head(exact) == len(gif())
            assert (await app.outputs.media.guarded(exact))['id'] == record['id']
            assert (await app.outputs.media.observe({
                **exact, 'generation': 1, 'sequence': 1,
                'renderState': 'ready', 'playback': 'paused'}, 'ui'))['accepted']
    completed_revisions = [thread for completed, thread in revisions if completed]
    completed_hashes = [thread for completed, size, thread in hashes if completed and size == len(text)]
    assert completed_revisions and len(completed_hashes) == len(completed_revisions)
    assert owner not in completed_revisions + completed_hashes
    assert any(captures), 'Final owned attachment must capture and qualify resident evidence too'
    assert session['messages'][0]['text'] is text
    assert (directory / 'transcript.jsonl').read_bytes() == raw


@pytest.mark.parametrize('change', ['alias-text', 'alias-id', 'claimant-insertion', 'canonical-revision'])
@pytest.mark.parametrize('spelling', ['retained', 'canonical'])
async def test_resident_authority_rechecks_after_projection_await(
        media_app, monkeypatch, change, spelling):
    import asyncio
    import threading
    from amplifier_web import media_message_identity
    app = media_app
    session, corpus, store, directory, canonical = cold_alias_corpus(app, native_message_id=False)
    raw = (directory / 'transcript.jsonl').read_bytes()
    entered, release = threading.Event(), threading.Event()
    real = media_message_identity.resolve

    def blocked(*values):
        proof = real(*values)  # Canonical projection has already resolved.
        entered.set()
        assert release.wait(10)
        return proof

    monkeypatch.setattr(media_message_identity, 'resolve', blocked)
    identity = canonical if spelling == 'canonical' else 'saved-web-assistant'
    pending = asyncio.create_task(app.outputs.media.identity(session['id'], identity))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with app.lock:
            if change == 'alias-text':
                session['messages'][0]['text'] += 'changed after projection'
            elif change == 'alias-id':
                # Preserve an explicit collision, including canonical spelling
                # when the changed retained row has no nativeMessageId.
                from amplifier_web.automatic_history import display_message
                session['messages'][0]['id'] = display_message(corpus[0], 0, session)['id']
                assert session['messages'][0]['id'] != 'saved-web-assistant'
            elif change == 'claimant-insertion':
                session['messages'].append({**session['messages'][0], 'id': 'late-retained-claimant'})
            else:
                corpus[1]['content'] += 'canonical revision changed after projection'
                store.save(session['id'], corpus, {}, preserve_system=True)
        release.set()
        with pytest.raises(ValueError):
            await pending
        assert app.outputs.store.list(session['id'])['items'] == []
        assert session.get('mediaRevision', 0) == 0
        assert app.db.execute('SELECT COUNT(*) FROM output_receipts').fetchone()[0] == 0
        if change != 'canonical-revision':
            assert (directory / 'transcript.jsonl').read_bytes() == raw
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('boundary', ['descriptor', 'guard', 'report'])
async def test_resident_index_only_claimant_evidence_is_frozen_for_later_boundaries(
        media_app, boundary):
    app = media_app
    session, _, _, directory, canonical = cold_alias_corpus(app, native_message_id=False)
    raw = (directory / 'transcript.jsonl').read_bytes()
    record = (await app.outputs.media.attach(
        {**media_args_without_hydration(session), 'messageId': canonical},
        'ui', 'resident-boundary-attach'))['result']
    app.clients.attach('resident-boundary-browser')
    with app.clients.bind('resident-boundary-browser'):
        proof = await app.outputs.media.identity(session['id'], canonical, scoped=True)
        assert proof['snapshot']['rows'] == [] and proof['snapshot']['claimants'] == []
        assert proof['residentClaimants'][0]['id'] == 'saved-web-assistant'
        values = {**record, 'messageId': canonical, 'generation': 1, 'sequence': 1,
                  'renderState': 'ready', 'playback': 'paused'}
        async with app.lock:
            # Still a valid unique alias, but not the captured authority.
            session['messages'][0]['id'] = 'different-valid-retained-alias'
            with pytest.raises(ValueError, match='identity changed'):
                if boundary == 'descriptor':
                    app.outputs.media.descriptors(session['id'], canonical, proof)
                elif boundary == 'guard':
                    app.outputs.media.guard(values, proof=proof)
                else:
                    app.outputs.media.report(values, 'ui', proof=proof)
        assert app.outputs.media.observations == {}
    assert (directory / 'transcript.jsonl').read_bytes() == raw


@pytest.mark.parametrize('kind', ['worker', 'internal'])
async def test_nonroot_descriptor_exception_is_scoped_and_identity_free(media_app, monkeypatch, kind):
    app = media_app
    session = app._session()
    session['sessionKind'] = kind  # Explicit whitelist discriminator, not native discovery evidence.
    session['mediaRevision'] = 7
    app.clients.attach('nonroot-descriptor-browser')
    app.clients.attach('nonroot-foreign-browser')
    app.clients.records['nonroot-foreign-browser']['selectedSessionId'] = None
    calls = []

    async def forbidden_identity(*args, **kwargs):
        pytest.fail('Non-root empty discovery reached attachment identity')

    original = app._session

    def session_lookup(sid=None, **kwargs):
        calls.append((app.lock.locked(), kwargs.get('hydrate')))
        return original(sid, **kwargs)

    monkeypatch.setattr(app.outputs.media, 'identity', forbidden_identity)
    with app.clients.bind('nonroot-descriptor-browser'):
        with monkeypatch.context() as patch:
            patch.setattr(app, '_session', session_lookup)
            assert await app.outputs.media.describe(session['id'], 'not-an-attachment') == {
                'items': [], 'overflow': False, 'mediaRevision': 7}
        assert calls and all(locked and hydrate is False for locked, hydrate in calls)
    with app.clients.bind('nonroot-foreign-browser'):
        with pytest.raises(ValueError, match='client'):
            await app.outputs.media.describe(session['id'], 'not-an-attachment')
    with pytest.raises(ValueError, match='client'):
        await app.outputs.media.describe(session['id'], 'not-an-attachment')


@pytest.mark.parametrize('kind', [None, '', False, 'unknown', [], {}, 1])
async def test_malformed_or_unknown_kind_never_gets_empty_descriptor_success(media_app, kind):
    session = media_app._session()
    missing = object()
    original = session.get('sessionKind', missing)
    session['sessionKind'] = kind
    try:
        media_app.clients.attach('invalid-kind-browser')
        with media_app.clients.bind('invalid-kind-browser'):
            with pytest.raises(ValueError, match='root conversation'):
                await media_app.outputs.media.describe(session['id'], session['messages'][0]['id'])
    finally:
        if original is missing:
            session.pop('sessionKind', None)
        else:
            session['sessionKind'] = original


async def test_missing_kind_keeps_root_identity_and_descriptor_errors(media_app):
    session = media_app._session()
    session.pop('sessionKind', None)
    # Ancestry hints are not a descriptor whitelist.
    session.update(nativeParentId='synthetic-parent', isTopLevelChat=False)
    media_app.clients.attach('missing-kind-browser')
    with media_app.clients.bind('missing-kind-browser'):
        assert (await media_app.outputs.media.describe(session['id'], session['messages'][0]['id']))['items'] == []
        with pytest.raises(ValueError, match='assistant'):
            await media_app.outputs.media.describe(session['id'], 'unknown')
        session['messages'][0]['role'] = 'user'
        with pytest.raises(ValueError, match='assistant'):
            await media_app.outputs.media.describe(session['id'], session['messages'][0]['id'])


async def test_discovered_worker_descriptor_http_empty_without_byte_report_or_mutation_authority(
        authenticated_client, tmp_path, monkeypatch):
    import uuid
    from amplifier_web import output_media
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, preload_providers=False, background_updates=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Worker discovery root'})
    sid = service._session()['id']
    await service.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'Root saved response.'})
    args = media_args(service)
    root_record = (await service.dispatch('outputs.attachMedia', args))['result']
    native = uuid.uuid4().hex
    SessionStore.for_app(service.data_dir, tmp_path).save(native, [
        {'role': 'user', 'content': 'Synthetic child input.'},
        {'role': 'assistant', 'content': 'Synthetic child saved response.'},
    ], {'name': 'Synthetic worker', 'parent_session_id': sid})
    await service.history.refresh()
    child = service._session(native, hydrate=False)
    assert child['sessionKind'] == 'worker'
    child_id = child['id']  # Never assume the native and public identities coincide.
    service.clients.attach('worker-http-browser')
    service.clients.attach('worker-http-foreign')
    with service.clients.bind('worker-http-browser'):
        await service.dispatch('session.select', {'id': child_id})
        await service.history.refresh_session(child_id)
        child = service._session(child_id, hydrate=False)
        shown = service.history.windows.project(child)
        assistants = [row for row in shown['messages'] if row.get('role') == 'assistant']
        assert len(assistants) == 1
        assert assistants[0]['text'] == 'Synthetic child saved response.'
        assert assistants[0]['displayOnly'] is True
        assert shown['messageWindow']['coverage']['kind'] == 'native-saved'
        assert dict.get(child, 'messages', []) == []
        message = assistants[0]
    # Synthetic legacy retained record: byte/link guards must refuse even a
    # complete tuple already owned by the worker. This is NOT attachment proof.
    internal = service.outputs.record(sid, root_record['id'])
    legacy = service.outputs.store.create(child_id, {
        'kind': 'media', 'title': 'Synthetic retained worker media', 'filename': 'clip.gif',
        'origin': {'source': 'ui', 'messageId': message['id']},
        'body': internal['body'], 'sha256': internal['sha256'], 'bytes': internal['bytes'],
        'media': internal['media'], 'versionEvidence': 'snapshot',
    })
    service.db.commit()
    records = service.db.execute('SELECT * FROM output_records ORDER BY id').fetchall()
    receipts = service.db.execute('SELECT * FROM output_receipts ORDER BY id').fetchall()
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'worker-http-browser'
    query = {'sessionId': child_id, 'messageId': message['id']}
    response = await client.get('/api/outputs/media', params=query)
    assert response.status == 200
    assert await response.json() == {'items': [], 'overflow': False, 'mediaRevision': child.get('mediaRevision', 0)}

    async def forbidden_body(*args, **kwargs):
        pytest.fail('Worker byte request reached media body IO')

    def forbidden_range(*args, **kwargs):
        pytest.fail('Worker byte request reached Range parsing')

    monkeypatch.setattr(service.outputs.media, 'bytes', forbidden_body)
    monkeypatch.setattr(service.outputs.media, 'head', forbidden_body)
    monkeypatch.setattr(output_media, 'byte_range', forbidden_range)
    exact = {**query, 'version': str(legacy['version']), 'sha256': legacy['sha256']}
    path = '/api/outputs/' + legacy['id'] + '/media'
    for method, headers in [('get', {}), ('head', {}), ('get', {'Range': 'bytes=0-1'})]:
        assert (await getattr(client, method)(path, params=exact, headers=headers)).status == 400
    for action, values in [
        ('outputs.attachMedia', {**args, **query}),
        ('outputs.unlink', {'sessionId': child_id, 'id': legacy['id'], 'expectedRevision': legacy['revision']}),
        ('outputs.relink', {'sessionId': child_id, 'id': legacy['id'], 'expectedRevision': legacy['revision']}),
        ('outputs.mediaReport', {**query, 'id': legacy['id'], 'version': legacy['version'],
                                'sha256': legacy['sha256'], 'generation': 1, 'sequence': 1,
                                'renderState': 'ready', 'playback': 'paused'}),
    ]:
        rejected = await client.post('/api/actions', json={'action': action, 'args': values})
        assert rejected.status == 409 and (await rejected.json())['accepted'] is False
    assert service.outputs.media.observations == {}
    assert service.db.execute('SELECT * FROM output_records ORDER BY id').fetchall() == records
    assert service.db.execute('SELECT * FROM output_receipts ORDER BY id').fetchall() == receipts
    assert child.get('mediaRevision', 0) == 0
    client.session.headers['X-Amplifier-Client'] = 'worker-http-foreign'
    assert (await client.get('/api/outputs/media', params=query)).status == 400
    client.session.headers['X-Amplifier-Client'] = 'worker-http-unattached'
    assert (await client.get('/api/outputs/media', params=query)).status == 409
    client.session.headers.pop('X-Amplifier-Client')
    assert (await client.get('/api/outputs/media', params=query)).status == 400
    client.session.headers['Authorization'] = ''
    client.session.cookie_jar.clear()
    assert (await client.get('/api/outputs/media', params=query, allow_redirects=False)).status in {401, 403}


def gif_canvas_state(app):
    """Only mutation owners, not caches or intentionally changed test authority."""
    import copy
    return copy.deepcopy({
        'artifacts': app._state.get('canvasArtifacts', []),
        'globalCanvas': app._state.get('canvas'),
        'clients': {identity: {key: client.get(key) for key in
                    ('canvas', 'canvasTabs', 'canvasViews', 'drafts', 'attachments', 'view')}
                    for identity, client in app.clients.records.items()},
        'outputs': app.db.execute('SELECT * FROM output_records ORDER BY id').fetchall(),
        'outputReceipts': app.db.execute('SELECT * FROM output_receipts ORDER BY id').fetchall(),
        'commands': app.db.execute('SELECT * FROM commands ORDER BY id').fetchall(),
        'resources': app.db.execute('SELECT * FROM state_resources ORDER BY id').fetchall(),
    })


def gif_open_args(args, record):
    return {key: (args if key == 'messageId' else record)[key]
            for key in ('sessionId', 'messageId', 'id', 'version', 'sha256')}


async def test_gif_canvas_compact_reference_large_retention_replay_reload_and_peer(media_app):
    import copy
    import json
    from pathlib import Path
    from amplifier_web.service import AppService
    from amplifier_web.state_storage import resource
    from test_service import Runtime
    app = media_app
    args = media_args(app)
    # A valid >5 MB GIF body in the existing 32 MiB output path. This is
    # retention/admission evidence only; native pixels are the browser's job.
    data = gif()[:-1] + b'\x21\xfe' + (b'\xff' + b'x' * 255) * 25_000 + b'\x00\x3b'
    assert 5 * 1024 * 1024 < len(data) < MAX_BYTES
    Path(app._session()['workspace'], args['path']).write_bytes(data)
    args['expectedSha256'] = hashlib.sha256(data).hexdigest()
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    values = gif_open_args(args, record)
    app.clients.attach('gif-clicker')
    app.clients.attach('gif-peer')
    with app.clients.bind('gif-peer'):
        await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Peer artifact', 'title': 'Peer'})
        await app.dispatch('view.update', {'patch': {'draft': 'Peer unsent draft'}})
    peer = copy.deepcopy(app.clients.records['gif-peer'])
    peer_artifact = next(r for r in app.state['canvasArtifacts'] if r['id'] == peer['canvas']['id'])
    peer_body = copy.deepcopy(peer_artifact['body'])
    assert resource(app.db, peer_body['$resource']) == {'content': peer['canvas']['content']}
    original_outputs = app.db.execute('SELECT * FROM output_records ORDER BY id').fetchall()
    with app.clients.bind('gif-clicker'):
        await app.dispatch('view.update', {'patch': {'draft': 'Clicker unsent draft'}})
        before_count = len(app.state['canvasArtifacts'])
        app._last_storage_sweep = 0
        opened = await app.dispatch('canvas.openGif', values, command_id='gif-user-click')
        identity = opened['result']['id']
        canvas = app.state['canvas']
        row = next(row for row in app.state['canvasArtifacts'] if row['id'] == identity)
        body = resource(app.db, row['body']['$resource'])
        assert canvas['kind'] == row['kind'] == 'gif'
        assert canvas['id'] == identity and canvas['open']
        assert body == {'mediaReference': canvas['mediaReference']}
        assert len(json.dumps(body).encode()) < 8000 and row['body']['bytes'] < 8000
        assert 'base64' not in json.dumps(body) and app._session()['workspace'] not in json.dumps(body)
        assert 'path' not in body['mediaReference']['item'] and 'body' not in body['mediaReference']['item']
        assert 'content' not in body and 'surface' not in body
        assert {key: body['mediaReference']['item'][key] for key in
                ('sessionId', 'id', 'version', 'sha256')} == {
                    key: values[key] for key in ('sessionId', 'id', 'version', 'sha256')}
        assert body['mediaReference']['messageId'] == values['messageId']
        assert row['messageId'] is None and row['publications'] == []
        assert len(row['versions']) == row['revision'] == 1
        duplicate = await app.dispatch('canvas.openGif', values, command_id='gif-user-click')
        assert duplicate['duplicate'] and duplicate['result'] == opened['result']
        assert (await app.dispatch('canvas.openGif', values))['result']['id'] == identity
        await app.dispatch('canvas.select', {'id': identity, 'version': 1})
        assert app.state['canvas']['mediaReference'] == body['mediaReference']
        await app.dispatch('canvas.close', {})
        await app.dispatch('canvas.reopen')
        assert app.state['canvas']['id'] == identity and app.state['canvas']['open']
        assert len(app.state['canvasArtifacts']) == before_count + 1
        assert row['revision'] == 1 and len(row['versions']) == 1 and row['publications'] == []
        assert app.state['view']['draft'] == 'Clicker unsent draft'
        assert app.db.execute('SELECT * FROM output_records ORDER BY id').fetchall() == original_outputs
    assert app.clients.records['gif-peer']['canvas'] == peer['canvas']
    assert app.clients.records['gif-peer']['drafts'] == peer['drafts']
    assert app.clients.records['gif-peer']['canvasTabs'] == peer['canvasTabs']
    assert app._storage_maintenance_task is not None
    await app._storage_maintenance_task
    retained_body = copy.deepcopy(app.outputs.record(values['sessionId'], record['id'])['body'])
    for reference in (row['body'], retained_body):
        assert app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (reference['$resource'],)).fetchone()
    Path(app._session(values['sessionId'])['workspace'], args['path']).unlink()
    directory, workspace = app.data_dir, app._session(values['sessionId'])['workspace']
    await app.close()
    reopened = AppService(directory, workspace=workspace, runtime=Runtime())
    try:
        reopened.clients.attach('gif-reloaded', resume='gif-clicker')
        with reopened.clients.bind('gif-reloaded'):
            assert reopened.state['canvas']['id'] == identity
            assert reopened.state['canvas']['mediaReference'] == body['mediaReference']
            assert reopened.state['view']['draft'] == 'Clicker unsent draft'
            assert (await reopened.dispatch('canvas.openGif', values))['result']['id'] == identity
            assert await reopened.outputs.media.bytes(values) == data
            assert reopened.outputs.record(values['sessionId'], record['id'])['body'] == retained_body
            rows = [r for r in reopened.state['canvasArtifacts'] if r['kind'] == 'gif']
            assert len(rows) == 1 and rows[0]['revision'] == 1 and len(rows[0]['versions']) == 1
            assert rows[0]['publications'] == []
        # Persisted, unattached clients retain a compact body. Compare its
        # exact reference/content before normal attach restores that body.
        assert reopened.clients.records['gif-peer']['canvas']['contentResource'] == peer_body
        assert resource(reopened.db, peer_body['$resource']) == {'content': peer['canvas']['content']}
        reopened.clients.attach('gif-peer')
        assert reopened.clients.records['gif-peer']['canvas'] == peer['canvas']
        assert reopened.clients.records['gif-peer']['drafts'] == peer['drafts']
    finally:
        await reopened.close()


@pytest.mark.parametrize('case', [
    'agent', 'no-client', 'different-client', 'non-web-client', 'foreign-session',
    'wrong-message', 'wrong-output', 'wrong-version', 'wrong-hash', 'unlinked',
    'mp4', 'corrupt', 'changed-chat', 'user-role', 'worker',
])
async def test_gif_canvas_fail_closed_without_any_mutation(media_app, case):
    from pathlib import Path
    from amplifier_web.resource_files import root
    from amplifier_web.service import AppError
    app = media_app
    args = media_args(app)
    if case == 'mp4':
        Path(app._session()['workspace'], args['path']).write_bytes(mp4())
        args['expectedSha256'] = hashlib.sha256(mp4()).hexdigest()
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    values = gif_open_args(args, record)
    app.clients.attach('gif-refusal', kind='api' if case == 'non-web-client' else 'web')
    session = app._session(args['sessionId'])
    if case == 'different-client':
        values['clientId'] = 'another-client'
    elif case in {'foreign-session', 'wrong-message', 'wrong-output'}:
        await app.dispatch('session.create', {'title': 'Actual foreign GIF conversation'})
        foreign = app._session()
        await app.on_runtime_event('assistant.message', {'sessionId': foreign['id'], 'text': 'Foreign retained assistant GIF'})
        foreign_args = media_args(app)
        foreign_record = (await app.dispatch('outputs.attachMedia', foreign_args))['result']
        key, value = {'foreign-session': ('sessionId', foreign['id']),
                      'wrong-message': ('messageId', foreign_args['messageId']),
                      'wrong-output': ('id', foreign_record['id'])}[case]
        values[key] = value
    elif case in {'wrong-version', 'wrong-hash'}:
        key, value = {'wrong-version': ('version', 99), 'wrong-hash': ('sha256', '0' * 64)}[case]
        values[key] = value
    elif case == 'unlinked':
        await app.dispatch('outputs.unlink', {'sessionId': args['sessionId'], 'id': record['id'], 'expectedRevision': 1})
    elif case == 'corrupt':
        internal = app.outputs.record(args['sessionId'], record['id'])
        (root(app.db) / (internal['body']['$resource'] + '.json')).write_text('{"encoding":"base64","data":"corrupt"}')
    elif case == 'user-role':
        session['messages'][-1]['role'] = 'user'
    elif case == 'worker':
        session['sessionKind'] = 'worker'
    elif case == 'changed-chat':
        with app.clients.bind('gif-refusal'):
            await app.dispatch('session.create', {'title': 'Different selected chat'})
    task = getattr(app, '_storage_maintenance_task', None)
    if task:
        await task
    with app.clients.bind(None if case == 'no-client' else 'gif-refusal'):
        before = gif_canvas_state(app)
        with pytest.raises(AppError):
            await app.dispatch('canvas.openGif', values, origin='agent' if case == 'agent' else 'ui',
                               command_id='refused-gif-open', include_state=False)
        assert gif_canvas_state(app) == before


async def test_gif_canvas_dirty_primary_rejected_before_remember(media_app, monkeypatch):
    from amplifier_web import canvas_library
    from amplifier_web.service import AppError
    app = media_app
    args = media_args(app)
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    app.clients.attach('dirty-gif-client')
    with app.clients.bind('dirty-gif-client'):
        await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Unsaved local edit', 'title': 'Keep me'})
        view = app.canvas_views.summary('primary')
        target = {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
        await app.dispatch('canvas.views.dirty', {**target, 'dirty': True})
        before = gif_canvas_state(app)
        def forbidden(*args, **kwargs):
            pytest.fail('Dirty GIF opening reached remember/save/select')
        with monkeypatch.context() as action_patch:
            action_patch.setattr(canvas_library, 'remember', forbidden)
            with pytest.raises(AppError) as refused:
                await app.dispatch('canvas.openGif', gif_open_args(args, record), command_id='dirty-click')
            assert refused.value.status == 409 and refused.value.code == 'canvas_view_dirty'
            assert gif_canvas_state(app) == before


@pytest.mark.parametrize('change', ['role', 'chat', 'unlink', 'canonical-revision'])
async def test_gif_canvas_final_proof_locked_recheck_without_nested_lock(media_app, monkeypatch, change):
    import asyncio
    from amplifier_web.service import AppError
    app = media_app
    session, corpus, store, _, canonical = cold_alias_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': canonical}
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    app.clients.attach('gif-proof-client')
    values = gif_open_args(args, record)
    task = getattr(app, '_storage_maintenance_task', None)
    if task:
        await task
    real = app.outputs.media.identity
    calls = 0
    async def witnessed(*args, **kwargs):
        nonlocal calls
        assert not app.lock.locked(), 'Identity called from inside the action lock'
        proof = await real(*args, **kwargs)
        calls += 1
        # Preflight identity, bytes start/end identities, then the final proof.
        if calls == 4:
            async with app.lock:
                if change == 'role':
                    session['messages'][0]['role'] = 'user'
                elif change == 'chat':
                    app.clients.record()['selectedSessionId'] = None
                elif change == 'unlink':
                    app.outputs.store.link(record['id'], 1, False)
                else:
                    session['nativeRevision'] = ['changed-after-proof']
            assert not app.lock.locked()
        return proof
    monkeypatch.setattr(app.outputs.media, 'identity', witnessed)
    with app.clients.bind('gif-proof-client'):
        before = gif_canvas_state(app)
        with pytest.raises(AppError):
            await asyncio.wait_for(app.dispatch('canvas.openGif', values, command_id='proof-click', include_state=False), 10)
        assert calls == 4
        after = gif_canvas_state(app)
        if change == 'unlink':
            # The independent authority mutation is intentional, not an open
            # side effect. Everything else must remain byte-for-byte unchanged.
            before['outputs'] = after['outputs']
        assert after == before


@pytest.mark.parametrize('shape', ['canonical-tail-miss', 'cold-retained-alias'])
async def test_gif_canvas_canonical_origin_without_rewriting_or_hydrating(media_app, monkeypatch, shape):
    import copy
    from amplifier_web import cold_display
    app = media_app
    session, _, _, directory, canonical = cold_alias_corpus(app)
    message = canonical if shape == 'canonical-tail-miss' else 'saved-web-assistant'
    args = {**media_args_without_hydration(session), 'messageId': message}
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    if shape == 'canonical-tail-miss':
        session['messages'] = session['messages'][1:]
        assert canonical not in {row['id'] for row in session['messages']}
    else:
        session, reference = await freeze_alias_display(app, session, hints=True)
    app.clients.attach('gif-canonical-client')
    native = (directory / 'transcript.jsonl').read_bytes()
    metadata = copy.deepcopy(dict(dict.items(session)))
    if shape == 'cold-retained-alias':
        original = cold_display.load
        def forbidden(db, pointer):
            if pointer == reference:
                pytest.fail('GIF opening hydrated the cold display body')
            return original(db, pointer)
        monkeypatch.setattr(cold_display, 'load', forbidden)
    with app.clients.bind('gif-canonical-client'):
        result = await app.dispatch('canvas.openGif', gif_open_args(args, record), include_state=False)
        assert result['result']['status'] == 'opened'
        assert app.state['canvas']['mediaReference']['messageId'] == message
    assert dict(dict.items(session)) == metadata
    assert (directory / 'transcript.jsonl').read_bytes() == native
    if shape == 'cold-retained-alias':
        assert dict.get(session, '_coldFields')['messages'] == reference and not dict.__contains__(session, 'messages')


@pytest.mark.parametrize('action', ['canvas.copy', 'canvas.download'])
async def test_gif_canvas_direct_copy_download_refused_before_remember_save_or_effect(media_app, monkeypatch, action):
    import copy
    from amplifier_web import canvas_library, canvas_downloads
    from amplifier_web.service import AppError
    app = media_app
    args = media_args(app)
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    app.clients.attach('gif-direct-refusal')
    with app.clients.bind('gif-direct-refusal'):
        await app.dispatch('view.update', {'patch': {'draft': 'Keep this unsent GIF draft'}})
        identity = (await app.dispatch('canvas.openGif', gif_open_args(args, record)))['result']['id']
        task = getattr(app, '_storage_maintenance_task', None)
        if task:
            await task
        before = gif_canvas_state(app)
        effects = copy.deepcopy(app.state.get('deviceCommands', []))

        def forbidden(*args, **kwargs):
            pytest.fail('Direct GIF copy/download reached remember, save or effect construction')

        with monkeypatch.context() as patch:
            patch.setattr(canvas_library, 'remember', forbidden)
            patch.setattr(app.clients, 'remember_canvas', forbidden)
            patch.setattr(app, '_save', forbidden)
            patch.setattr(canvas_downloads, 'filename', forbidden)
            with pytest.raises(AppError) as refused:
                await app.dispatch(action, {'id': identity}, command_id='gif-direct-refused', include_state=False)
        assert refused.value.status == 409
        assert str(refused.value) == 'Use Download original on the retained GIF.'
        assert gif_canvas_state(app) == before
        assert app.state.get('deviceCommands', []) == effects
        assert app.state['view']['draft'] == 'Keep this unsent GIF draft'


async def test_gif_canvas_stale_alias_and_generic_forgery_refused(media_app):
    from amplifier_web.service import AppError
    app = media_app
    session, _, _, directory, _ = cold_alias_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': 'saved-web-assistant'}
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    app.clients.attach('gif-immutable-client')
    with app.clients.bind('gif-immutable-client'):
        identity = (await app.dispatch('canvas.openGif', gif_open_args(args, record), include_state=False))['result']['id']
        before = gif_canvas_state(app)
        for action, extra in [('canvas.versions.revise', {'content': '{"mediaReference":"forged"}'}),
                              ('canvas.versions.restore', {'version': 1})]:
            with pytest.raises(AppError, match='immutable'):
                await app.dispatch(action, {'id': identity, 'sessionId': session['id'], 'expectedRevision': 1, **extra})
            assert gif_canvas_state(app) == before
        with pytest.raises(AppError):
            await app.dispatch('canvas.show', {'kind': 'gif', 'content': 'forged'})
        assert gif_canvas_state(app) == before
        with pytest.raises(AppError):
            await app.dispatch('canvas.views.inline', {'resourceId': identity, 'version': 1,
                'messageId': args['messageId'], 'previewId': 'forged-inline'})
        assert gif_canvas_state(app) == before
        raw = (directory / 'transcript.jsonl').read_bytes()
        session['messages'][0]['text'] += 'stale alias'
        before = gif_canvas_state(app)
        with pytest.raises(AppError):
            await app.dispatch('canvas.openGif', gif_open_args(args, record), include_state=False)
        assert gif_canvas_state(app) == before
        with pytest.raises(ValueError):
            await app.outputs.media.bytes(gif_open_args(args, record))
        assert (directory / 'transcript.jsonl').read_bytes() == raw

def test_full_resolution_gif_admission_keeps_format_and_budgets():
    from amplifier_web.output_media import MAX_MP4_BYTES
    result = validate_media(gif(width=1920, height=802, frames=68))
    assert (result['width'], result['height'], result['frameCount']) == (1920,802,68)
    assert result['decodedPixelBytesEstimate'] == 1920 * 802 * 4 * 68
    # Same-size MP4 is still outside its independent 32 MiB contract.
    oversized = mp4() + b'x' * MAX_MP4_BYTES
    with pytest.raises(ValueError, match='MP4.*32 MiB'):
        validate_media(oversized)


async def test_media_discovery_requalifies_transient_revision_race(media_app, monkeypatch):
    from amplifier_web.media_message_identity import StaleMediaIdentityError
    from unittest.mock import AsyncMock
    session = media_app._session()
    media_app.clients.attach('race-browser')
    media = media_app.outputs.media
    original = media.identity
    calls = 0
    async def racing(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise StaleMediaIdentityError('The saved assistant identity changed during media access.')
        return await original(*args, **kwargs)
    monkeypatch.setattr(media, 'identity', racing)
    with media_app.clients.bind('race-browser'):
        assert (await media.describe(session['id'], session['messages'][0]['id']))['items'] == []
        assert calls == 2
        failed = AsyncMock(side_effect=StaleMediaIdentityError('still changing'))
        monkeypatch.setattr(media, 'identity', failed)
        with pytest.raises(StaleMediaIdentityError):
            await media.describe(session['id'], session['messages'][0]['id'])
        assert failed.await_count == 3
        denied = AsyncMock(side_effect=ValueError('identity collision'))
        monkeypatch.setattr(media, 'identity', denied)
        with pytest.raises(ValueError, match='collision'):
            await media.describe(session['id'], session['messages'][0]['id'])
        assert denied.await_count == 1
