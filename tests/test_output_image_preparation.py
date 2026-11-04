"""Saved image reads stay atomic without per-image event-loop round trips."""
import asyncio
import base64
import hashlib
import struct
import threading
import zlib

import pytest

from test_output_image_pairs import pair, runtime, identities, pixels


@pytest.mark.parametrize('chunked', [False, True])
async def test_selected_set_uses_bounded_worker_turns_without_event_loop_decode(pair, monkeypatch, chunked):
    p = pair
    if chunked:
        rows = []
        for index, original in enumerate(p.data):
            payload = b'tEXt' + b'note\0' + b'x' * (1024 * 1024)
            chunk = struct.pack('>I', len(payload) - 4) + payload + struct.pack('>I', zlib.crc32(payload))
            data = original[:-12] + chunk + original[-12:]
            from amplifier_web import retained_files
            name = f'chunked-{index}.png'
            (p.path / name).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            async with retained_files.Batch() as batch:
                captured = await asyncio.to_thread(retained_files.capture, p.path, name, digest, batch)
                body = await retained_files.adopt(p.app.db, captured)
            assert captured['bodyFormat'] == retained_files.FORMAT
            rows.append(p.app.outputs.store.create(p.sid, {'kind': 'file', 'title': 'Chunked image',
                'sha256': digest, 'bytes': len(data), 'body': body, 'bodyFormat': captured['bodyFormat']}))
        p.rows = rows
    from amplifier_web import resource_files
    main_thread = threading.get_ident()
    original_read, original_image = resource_files.read_detached, p.app.outputs.image_value
    original_thread = asyncio.to_thread
    turns = []
    caller = asyncio.current_task()
    def read(*args, **kwargs):
        assert threading.get_ident() != main_thread
        return original_read(*args, **kwargs)
    def image(*args):
        assert threading.get_ident() != main_thread
        return original_image(*args)
    async def thread(function, *args, **kwargs):
        if asyncio.current_task() is caller:turns.append(function.__name__)
        return await original_thread(function, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(resource_files, 'read_detached', read)
        m.setattr(p.app.outputs, 'image_value', image)
        m.setattr(asyncio, 'to_thread', thread)
        actual = await p.app.app_bridge('outputs.images.read', {'images': identities(p.rows)}, p.sid)
    assert identities(actual['images']) == identities(p.rows)
    assert len(turns) <= (2 if chunked else 1), turns


async def test_busy_loop_yields_do_not_multiply_into_image_deadline(pair, monkeypatch):
    # Model a loaded loop taking 550 ms to resume each off-thread read. The old
    # sequential two-image/two-conversion path needs 4 resumes and exceeds 2 s.
    original = asyncio.to_thread
    caller = asyncio.current_task()
    async def delayed(function, *args, **kwargs):
        result = await original(function, *args, **kwargs)
        if asyncio.current_task() is caller:await asyncio.sleep(.55)
        return result
    with monkeypatch.context() as m:
        m.setattr(asyncio, 'to_thread', delayed)
        actual = await asyncio.wait_for(pair.app.app_bridge('outputs.images.read',
            {'images': identities(pair.rows)}, pair.sid), 2)
    assert [base64.b64decode(row['_image']) for row in actual['images']] == pair.data


@pytest.mark.parametrize('single', [False, True])
@pytest.mark.parametrize('changed', [False, True])
async def test_unavailable_context_is_not_new_input_and_current_authority_is_required(runtime, monkeypatch, single, changed):
    r = runtime
    # Establish the current identity, then inspect within that input.
    await r.provider.complete(r.request())
    await r.read(single=single)
    selected = identities(r.pair.rows[:1] if single else r.pair.rows)
    original = r.pair.app.app_bridge
    async def missing(operation, args, sid):
        if operation == 'context.prepare':raise TimeoutError('temporary host delay')
        if operation.startswith('outputs.image'):
            raise AssertionError('No pixels may be read without current input identity')
        return await original(operation, args, sid)
    with monkeypatch.context() as m:
        m.setattr(r.pair.app, 'app_bridge', missing)
        omitted = await r.provider.complete(r.request())
    assert not pixels(omitted)
    assert r.delivery.output_selection() == selected
    assert r.delivery.epoch == ['input-one']
    if changed:r.epoch[:] = ['later-input']
    next_request = await r.provider.complete(r.request())
    assert pixels(next_request) == ([] if changed else r.pair.data[:1] if single else r.pair.data)
    if changed:assert r.delivery.output_selection() == []


@pytest.mark.parametrize('single', [False, True])
async def test_never_observed_input_cannot_rebind_old_selection_to_a_later_input(runtime, monkeypatch, single):
    r = runtime
    assert r.delivery.epoch is None
    await r.read(single=single)
    original = r.pair.app.app_bridge
    async def missing(operation, args, sid):
        if operation == 'context.prepare':raise TimeoutError('first input never observed')
        return await original(operation, args, sid)
    with monkeypatch.context() as m:
        m.setattr(r.pair.app, 'app_bridge', missing)
        assert not pixels(await r.provider.complete(r.request()))
    r.epoch[:] = ['later-input']
    assert not pixels(await r.provider.complete(r.request()))
    await r.read(single=single)
    assert pixels(await r.provider.complete(r.request())) == (r.pair.data[:1] if single else r.pair.data)
