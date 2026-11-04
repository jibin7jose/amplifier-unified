"""Typed pixels reuse the authority of one exact retained conversation result."""
import asyncio
import base64
import copy
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from amplifier_web.service import AppError
from test_collaborative_workspaces import app, agent_action, generation, finish
from test_result_artifacts import setup, declare
from test_output_images import png
from test_output_image_pairs import Runtime, identities, pixels


async def retained(app, tmp_path):
    data = png()
    source, peer, file, args = await setup(app, tmp_path, data)
    response = (await declare(app, peer, args))['result']
    row = app.outputs.store.read(response['artifacts'][0]['outputId'])
    return source, peer, file, args, row, data


@pytest.mark.parametrize('pair', [False, True])
@pytest.mark.parametrize('cross_workspace', [False, True])
async def test_requester_inspects_exact_retained_pixels_through_actual_provider_boundary(app, tmp_path, pair, cross_workspace):
    if cross_workspace:
        (tmp_path/'producer-workspace').mkdir()
        app.state['sessions'][1]['workspace'] = str(tmp_path/'producer-workspace')
    source, peer, file, args, row, data = await retained(app, tmp_path)
    # The source is never reopened. Owning another output is not necessary.
    file.unlink()
    rows, expected = [row], [data]
    if pair:
        (tmp_path/'comparison.png').write_bytes(png(4, 2))
        own = (await app.dispatch('outputs.attach', {'sessionId': source['id'], 'kind': 'file',
            'title': 'Own comparison', 'path': 'comparison.png'}))['result']
        rows.append(own);expected.append(png(4, 2))
    before = copy.deepcopy(app.state['view'])
    runtime = await Runtime().setup(SimpleNamespace(app=app, sid=source['id'], rows=rows, data=expected))
    receipt = await runtime.read(single=not pair)
    assert receipt['success'] and '_image' not in json.dumps(receipt)
    request = runtime.request()
    assert pixels(await runtime.provider.request_budget(request)) == expected
    assert pixels(await runtime.provider.complete(request)) == expected
    assert app.outputs.store.read(row['id'])['sessionId'] == peer['id']
    assert app.state['view'] == before
    # This is one artifact read, not access to the producing library.
    with pytest.raises(AppError, match='another conversation'):
        await app.dispatch('outputs.read', {'sessionId': source['id'], 'id': row['id']})


async def test_original_reply_image_remains_exact_after_later_response_revision(app, tmp_path):
    source, peer, file, args, original, data = await retained(app, tmp_path)
    await finish(app, peer, args['requestId'])
    await generation(app, peer, 'follow-up', ['new-human-input'])
    newer = png(5, 2);file.write_bytes(newer)
    changed = {**args, 'expectedResponseRevision': 1, 'references': ['report.md@sha256:' + hashlib.sha256(newer).hexdigest()]}
    response = (await agent_action(app, peer, 'coordination.reply', changed, 'later-image'))['result']
    assert response['responseRevision'] == 2
    for row, expected in [(original, data), (app.outputs.store.read(response['artifacts'][0]['outputId']), newer)]:
        value = await app.app_bridge('outputs.image.read', identities([row])[0], source['id'])
        assert base64.b64decode(value['_image']) == expected


@pytest.mark.parametrize('change', ['unrelated_reader', 'wrong_hash', 'wrong_reply', 'forged_output', 'moved_workspace'])
async def test_request_relationship_does_not_grant_arbitrary_peer_pixels(app, tmp_path, change):
    source, peer, file, args, row, data = await retained(app, tmp_path)
    identity = identities([row])[0]
    if change == 'unrelated_reader':
        source = app._new_session({'title': 'Unrelated'})
        app.state['sessions'].append(source)
    elif change == 'wrong_hash':
        identity['sha256'] = '0'*64
    elif change == 'wrong_reply':
        row['collaborationResult']['commandId'] = args['requestId']
        app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(row), row['id']))
    elif change == 'forged_output':
        clone = app.outputs.store.create(peer['id'], {key: copy.deepcopy(value) for key, value in row.items()
            if key not in {'id', 'sessionId', 'revision', 'createdAt'}})
        identity = identities([clone])[0]
    else:
        peer['workspace'] = str(tmp_path/'elsewhere')
    with pytest.raises((AppError, ValueError)):
        await app.app_bridge('outputs.image.read', identity, source['id'])


@pytest.mark.parametrize('change', ['workspace', 'record', 'corrupt'])
async def test_off_lock_pair_read_rechecks_all_relationships_before_any_pixels(app, tmp_path, monkeypatch, change):
    source, peer, file, args, row, data = await retained(app, tmp_path)
    (tmp_path/'own.png').write_bytes(png(4, 2))
    own = (await app.dispatch('outputs.attach', {'sessionId': source['id'], 'kind': 'file',
        'title': 'Own image', 'path': 'own.png'}))['result']
    entered, release = threading.Event(), threading.Event()
    original = app.outputs.image_value
    def read(record, data):
        value = original(record, data)
        if record['id'] == own['id']:
            entered.set();assert release.wait(5)
        return value
    monkeypatch.setattr(app.outputs, 'image_value', read)
    task = asyncio.create_task(app.app_bridge('outputs.images.read', {'images': identities([row, own])}, source['id']))
    assert await asyncio.to_thread(entered.wait, 2)
    # Neither I/O nor PNG validation holds the app lock. Changes to the first
    # artifact while reading the second must invalidate the entire pair.
    async with asyncio.timeout(2):
        async with app.lock:
            if change == 'workspace':peer['workspace'] = str(tmp_path/'moved')
            elif change == 'record':
                row['collaborationResult']['commandId'] = 'missing-reply'
                app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(row), row['id']))
            else:
                row['sha256'] = '0'*64
                app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(row), row['id']))
    release.set()
    with pytest.raises((AppError, ValueError)):
        await task


async def test_corrupt_saved_bytes_are_not_substituted_from_original_file(app, tmp_path, monkeypatch):
    source, peer, file, args, row, data = await retained(app, tmp_path)
    original = app.outputs.decode_content
    def corrupt(record, value):
        if record['id'] == row['id']:
            value = {'encoding': 'base64', 'data': base64.b64encode(png(9, 2)).decode()}
        return original(record, value)
    monkeypatch.setattr(app.outputs, 'decode_content', corrupt)
    with pytest.raises(ValueError, match='evidence hash'):
        await app.app_bridge('outputs.image.read', identities([row])[0], source['id'])


async def test_existing_full_access_reader_is_rechecked_after_io(app, tmp_path, monkeypatch):
    source, peer, file, args, row, data = await retained(app, tmp_path)
    reader = app._new_session({'title': 'Existing observer'})
    app.state['sessions'].append(reader)
    async def access(mode):
        await app.dispatch('access.set', {'sessionId': reader['id'], 'scope': 'session', 'mode': mode,
            'expectedRevision': app.access_mode.snapshot(reader)['revision']})
    await access('full')
    value = await app.app_bridge('outputs.image.read', identities([row])[0], reader['id'])
    assert base64.b64decode(value['_image']) == data
    entered, release = threading.Event(), threading.Event()
    original = app.outputs.image_value
    def read(record, data):
        value = original(record, data)
        entered.set();assert release.wait(5)
        return value
    monkeypatch.setattr(app.outputs, 'image_value', read)
    task = asyncio.create_task(app.app_bridge('outputs.image.read', identities([row])[0], reader['id']))
    assert await asyncio.to_thread(entered.wait, 2)
    await access('ask')
    release.set()
    with pytest.raises(AppError, match='request and workspace'):
        await task
