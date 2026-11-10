"""Explicit own-history scope survives retained-image delivery, not authority loss."""
import asyncio
import hashlib
import threading
from types import SimpleNamespace

import pytest

from amplifier_web.service import AppError
from test_access_mode import save
from test_collaborative_workspaces import app
from test_output_image_pairs import Runtime, identities, pixels
from test_output_images import png
from test_result_artifacts import declare, setup


@pytest.fixture
async def observed(app, tmp_path):
    data = [png(3, 2), png(4, 2)]
    source, peer, file, args = await setup(app, tmp_path, data[0])
    file.rename(file.with_suffix('.png'))
    (file.parent/'second.png').write_bytes(data[1])
    args['references'] = [name+'@sha256:'+hashlib.sha256(raw).hexdigest()
                          for name, raw in zip(('report.png', 'second.png'), data)]
    result = (await declare(app, peer, args))['result']
    rows = [app.outputs.store.read(item['outputId']) for item in result['artifacts']]
    (tmp_path/'other-workspace').mkdir()
    observer = app._new_session({'title': 'Independent reviewer'})
    observer['workspace'] = str(tmp_path/'other-workspace')
    app.state['sessions'].append(observer)
    await save(app, observer)
    return SimpleNamespace(app=app, sid=observer['id'], observer=observer,
                           source=source, peer=peer, rows=rows, data=data, path=tmp_path)


@pytest.mark.parametrize('single', [True, False])
async def test_explicit_all_scope_delivers_exact_retained_pixels_through_budget_and_send(observed, single):
    p = observed;r = await Runtime().setup(p)
    action = next(row for row in p.app.get_actions()
                  if row['name'] == ('outputs.image' if single else 'outputs.images'))
    assert action['inputSchema']['properties']['scope']['enum'] == ['workspace', 'all']
    denied = await r.read(single=single)
    assert not denied['success'] and 'scope=all' in str(denied)
    assert r.delivery.output_selection() == []
    accepted = await r.read(single=single, scope='all')
    assert accepted['success'] and accepted['output']['result']['scope'] == 'all'
    assert '_image' not in str(accepted)
    expected = p.data[:1] if single else p.data
    request = r.request()
    assert pixels(await r.provider.request_budget(request)) == expected
    assert pixels(await r.provider.complete(request)) == expected
    selected = r.delivery.output_selection()
    denied = await r.read(single=single)
    assert not denied['success']
    if not single:
        assert denied['error']['imageSelectionUnchanged']
    assert r.delivery.output_selection() == selected
    assert pixels(await r.provider.complete(r.request())) == expected


@pytest.mark.parametrize('single', [True, False])
async def test_all_scope_does_not_survive_revoked_full_access(observed, single):
    p = observed;r = await Runtime().setup(p)
    assert (await r.read(single=single, scope='all'))['success']
    request = r.request()
    assert pixels(await r.provider.request_budget(request))
    await save(p.app, p.observer, mode='ask')
    assert pixels(await r.provider.complete(request)) == []
    denied = await r.read(single=single, scope='all')
    assert not denied['success'] and 'request and workspace' in str(denied)


async def test_all_scope_is_not_peer_output_library_access(observed):
    p = observed
    path = p.path/'unshared.png';path.write_bytes(p.data[0])
    row = (await p.app.dispatch('outputs.attach', {'sessionId': p.source['id'],
        'kind': 'file', 'title': 'Not shared', 'path': path.name}))['result']
    r = await Runtime().setup(p)
    denied = await r.read([row], single=True, scope='all')
    assert not denied['success'] and 'another conversation' in str(denied)
    assert r.delivery.output_selection() == []
    with pytest.raises(ValueError, match='registered-history scope'):
        await p.app.app_bridge('outputs.images.read',
            {'images': identities(p.rows), 'scope': 'everything'}, p.sid)


async def test_retained_image_rechecks_access_after_detached_io(observed, monkeypatch):
    from amplifier_web import resource_files
    p = observed;entered, release = threading.Event(), threading.Event()
    original = resource_files.read_detached
    image_bodies = {row['body']['$resource'] for row in p.rows}
    def blocked(*args, **kwargs):
        if args[0].identity in image_bodies:
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(resource_files, 'read_detached', blocked)
    task = asyncio.create_task(p.app.app_bridge('outputs.images.read',
        {'images': identities(p.rows), 'scope': 'all'}, p.sid))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await save(p.app, p.observer, mode='ask')
    finally:
        release.set()
    with pytest.raises((AppError, ValueError), match='request and workspace'):
        await task
