"""Exact saved revisions, identity, drafts and no replay across client lifecycles."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.resource_files import collect
from amplifier_web.state_storage import resource
from test_scoped_state_records import committed_session_payloads


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path/'data', workspace=tmp_path)
    await service.dispatch('session.create', {})
    service._message(service._session(), 'user', 'First document')
    service.clients.attach('one'); service.clients.attach('two')
    yield service
    await service.close()


async def call(app, name, args=None, client='one', **kwargs):
    with app.clients.bind(client):
        return await app.dispatch(name, args or {}, **kwargs)


def canvas(app, client='one'):
    return app.clients.records[client]['canvas']


def row(app, identity):
    return next(r for r in app.state['canvasArtifacts'] if r['id'] == identity)


async def revise(app, identity, content, expected=1):
    return await call(app, 'canvas.versions.revise', {'id': identity, 'expectedRevision': expected, 'content': content})


async def test_file_identity_unchanged_reopen_changed_source_and_distinct_same_names(app, tmp_path):
    path = tmp_path/'plan.md'; path.write_text('# Original')
    original = (await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)}))['result']
    await call(app, 'canvas.show', {'kind': 'auto', 'path': './plan.md'})
    assert canvas(app)['id'] == original['id']
    assert row(app, original['id'])['revision'] == 1
    app._message(app._session(), 'user', 'Improve it')
    path.write_text('# Improved')
    await call(app, 'canvas.show', {'kind': 'markdown', 'path': str(path)})
    saved = row(app, original['id'])
    assert saved['revision'] == 2 and len(saved['versions']) == 2
    assert len(saved['publications']) == 2
    assert canvas(app)['id'] == original['id']
    (tmp_path/'other').mkdir(); other = tmp_path/'other'/'plan.md'; other.write_text('# Improved')
    await call(app, 'canvas.show', {'kind': 'auto', 'path': str(other)})
    assert canvas(app)['id'] != original['id']
    assert len(app.state['canvasArtifacts']) == 2


@pytest.mark.parametrize('transition', ['small-large', 'large-small'])
async def test_file_versions_replace_content_storage_mode_and_survive_restart(app, tmp_path, transition):
    from amplifier_web.canvas_documents import raw_source
    small = '<h1>Small saved version</h1>'
    large = '<h1>Large saved version</h1><!--' + 'x' * 1_100_000 + '-->'
    first, second = (small, large) if transition == 'small-large' else (large, small)
    path = tmp_path/'versions.html'
    path.write_text(first)
    identity = (await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)}))['result']['id']
    path.write_text(second)
    await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)})
    path.unlink()  # Saved versions must not depend on the source file.
    assert row(app, identity)['revision'] == 2
    assert bool(row(app, identity).get('contentResource')) == (second == large)
    for version, expected in ((1, first), (2, second), (None, second)):
        await call(app, 'canvas.select', {'id': identity, 'resetPreview': True, **({'version': version} if version else {})})
        assert raw_source(canvas(app), app.db) == expected
        with app.clients.bind('one'):
            assert raw_source(app.canvas_views.canvas('primary'), app.db) == expected
        inspected = (await call(app, 'canvas.versions.inspect', {'id': identity, 'version': version or 2, 'includeSource': True}))['result']
        assert inspected['source']['content'] == expected
    await call(app, 'canvas.select', {'id': identity, 'version': 1, 'resetPreview': True})
    app._save()
    await app.close()
    reopened = AppService(app.data_dir, workspace=tmp_path)
    try:
        reopened.clients.attach('one')
        assert raw_source(canvas(reopened), reopened.db) == first
        await call(reopened, 'canvas.select', {'id': identity, 'resetPreview': True})
        assert raw_source(canvas(reopened), reopened.db) == second
        await call(reopened, 'canvas.versions.restore', {'id': identity, 'expectedRevision': 2, 'version': 1})
        assert raw_source(canvas(reopened), reopened.db) == second
        assert row(reopened, identity)['revision'] == 3
        with reopened.clients.bind('one'):
            current = reopened.canvas_views.summary('primary')
        await call(reopened, 'canvas.views.loadVersion', {
            **{key: current[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}, 'version': 3})
        assert raw_source(canvas(reopened), reopened.db) == first
    finally:
        await reopened.close()


@pytest.mark.usefixtures('committed_session_payloads')
async def test_exact_links_latest_followers_cas_restore_and_restart(app, tmp_path):
    original = (await call(app, 'canvas.show', {'kind': 'markdown', 'title': 'Plan', 'content': '# One'}))['result']
    identity = original['id']
    await call(app, 'canvas.select', {'id': identity, 'version': 1}, 'two')
    await call(app, 'view.update', {'patch': {'draft': 'Keep unsent draft'}})
    app._message(app._session(), 'user', 'Second version')
    await revise(app, identity, '# Two')
    assert canvas(app)['content'] == '# Two'
    assert canvas(app, 'two')['content'] == '# One'
    with app.clients.bind('two'):
        assert app.canvas_views.canvas('primary')['content'] == '# One'
    with pytest.raises(AppError, match='changed'):
        await revise(app, identity, '# Stale', 1)
    original_versions = deepcopy(row(app, identity)['versions'])
    await call(app, 'canvas.versions.restore', {'id': identity, 'expectedRevision': 2, 'version': 1}, 'two')
    assert row(app, identity)['revision'] == 3
    assert row(app, identity)['versions'][:2] == original_versions
    assert canvas(app)['content'] == canvas(app, 'two')['content'] == '# One'
    await call(app, 'canvas.select', {'id': identity, 'version': 2}, 'two')
    assert canvas(app, 'two')['content'] == '# Two'
    app._save()
    from test_scoped_state_records import assert_only_superseded_session_payloads_collected
    assert_only_superseded_session_payloads_collected(app)
    await app.close()
    reopened = AppService(app.data_dir, workspace=tmp_path)
    try:
        reopened.clients.attach('one'); reopened.clients.attach('two')
        assert canvas(reopened, 'two')['content'] == '# Two'
        with reopened.clients.bind('two'):
            assert reopened.canvas_views.canvas('primary')['content'] == '# Two'
        assert reopened.clients.records['one']['view']['draft'] == 'Keep unsent draft'
        await call(reopened, 'canvas.select', {'id': identity})
        assert canvas(reopened)['content'] == '# One'
    finally:
        await reopened.close()


async def test_dirty_edit_blocks_selection_and_other_client_refinement(app):
    identity = (await call(app, 'canvas.show', {'kind':'text','content':'Original'}))['result']['id']
    with app.clients.bind('one'):
        view = app.canvas_views.summary('primary')
    target = {k: view[k] for k in ('viewId','resourceId','resourceRevision','generation')}
    await call(app, 'canvas.views.dirty', {**target, 'dirty': True})
    with pytest.raises(AppError, match='viewer edit'):
        await call(app, 'canvas.select', {'id':identity, 'version':1})
    with pytest.raises(AppError, match='artifact edit'):
        await call(app, 'canvas.versions.revise', {'id':identity,'expectedRevision':1,'content':'Replacement'}, 'two')
    assert row(app,identity)['revision'] == 1
    await call(app, 'canvas.views.recover', target)
    await revise(app,identity,'Replacement')
    with pytest.raises(AppError, match='changed'):
        await call(app, 'canvas.views.command', {**target,'action':'canvas.view','args':{'patch':{'source':True}}})


async def test_agent_actions_are_conversation_scoped_and_preserve_historical_views(app):
    sid = app._session()['id']
    created = await app.app_bridge('dispatch', {'action':'canvas.show','args':{'kind':'text','content':'Original'}},sid)
    identity = created['result']['id']
    await app.app_bridge('dispatch', {'action':'canvas.versions.revise','args':{'id':identity,'expectedRevision':1,'content':'Agent edit'}},sid)
    inspected = await app.app_bridge('dispatch', {'action':'canvas.versions.inspect','args':{'id':identity,'includeSource':True,'version':1}},sid)
    assert inspected['result']['source']['content'] == 'Original'
    await app.app_bridge('dispatch', {'action':'canvas.select','args':{'id':identity,'version':1,'clientId':'one'}},sid)
    assert canvas(app)['readOnlyVersion']
    await call(app, 'session.create', {}, 'two')
    other = app.clients.records['two']['selectedSessionId']
    with pytest.raises(AppError, match='unavailable'):
        await app.app_bridge('dispatch', {'action':'canvas.versions.inspect','args':{'id':identity}},other)
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action':'canvas.versions.revise','args':{'id':identity,'sessionId':sid,'expectedRevision':2,'content':'Attack'}},other)


async def test_earlier_fork_does_not_inherit_future_revisions(app):
    identity = (await call(app, 'canvas.show', {'kind':'markdown','content':'First turn'}))['result']['id']
    source = app._session()
    app._message(source,'assistant','Published one')
    app._message(source,'user','Next turn')
    await revise(app,identity,'Future version')
    app._message(source,'assistant','Published two')
    await call(app,'session.fork',{'id':source['id'],'turn':1})
    fork = app.clients.records['one']['selectedSessionId']
    saved = next(r for r in app.state['canvasArtifacts'] if r['sessionId'] == fork)
    assert saved['revision'] == 1
    assert len(saved['versions']) == 1
    assert resource(app.db,saved['body']['$resource'])['content'] == 'First turn'
    assert row(app,identity)['revision'] == 2


async def test_surface_history_retention_and_historical_state_is_read_only(app):
    spec = {'version':1,'stateSchema':{'type':'object'},'events':{},'requests':{}}
    created = (await call(app,'canvas.apps.create',{'title':'App','content':'<h1>One</h1>','manifest':spec,'initialState':{'n':1}}))['result']
    identity = created['id']
    await call(app,'canvas.apps.state',{'id':identity,'expectedRevision':1,'expectedStateRevision':0,'patch':{'n':2}})
    for revision in range(1,23):
        await call(app,'canvas.apps.revise',{'id':identity,'expectedRevision':revision,'expectedStateRevision':revision,'content':f'<h1>Revision {revision+1}</h1>'})
    assert len(row(app,identity)['app']['versions']) == 23
    await call(app,'canvas.select',{'id':identity,'version':1})
    assert canvas(app)['app']['state'] == {'n':1}
    with app.clients.bind('one'):
        selected = app.canvas_views.canvas('primary')
        assert resource(app.db,selected['contentResource']['$resource'])['content'] == '<h1>One</h1>'
    with pytest.raises(AppError,match='read-only'):
        await call(app,'canvas.apps.state',{'id':identity,'expectedRevision':23,'expectedStateRevision':23,'patch':{'n':3}})
    await call(app,'canvas.apps.restore',{'id':identity,'expectedRevision':23,'expectedStateRevision':23,'version':1})
    assert canvas(app).get('selectedVersion') is None
    assert row(app,identity)['app']['revision'] == 24
    assert row(app,identity)['app']['state'] == {'n':2}


def preview(app, client='one'):
    with app.clients.bind(client):
        return deepcopy(app.canvas_views.summary('primary'))


def preview_target(current):
    return {key: current[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}


@pytest.mark.parametrize('kind', ['html', 'babylon'])
async def test_executable_latest_discovers_versions_without_replacing_running_definition(app, kind):
    from amplifier_web.canvas_documents import raw_source

    first = '<input value="saved one"><p>One</p>'
    identity = (await call(app, 'canvas.show', {'kind': kind, 'content': first}))['result']['id']
    original = preview(app)
    await call(app, 'canvas.select', {'id': identity, 'version': 1}, 'two')
    pinned = preview(app, 'two')
    document = {'text': 'One', 'controls': [
        {'id': 'field', 'tag': 'input', 'type': 'text', 'label': 'Note', 'value': 'unfinished', 'disabled': False}]}
    await call(app, 'canvas.views.command', {**preview_target(original),
        'action': 'canvas.snapshot', 'args': {'document': document}})
    await call(app, 'view.update', {'patch': {'draft': 'Unsent composer'}})
    for version in (2, 3):
        await revise(app, identity, f'<input value="saved {version}"><p>{version}</p>', version - 1)
        inspected = (await call(app, 'canvas.views.inspect'))['result']['views'][0]
        assert inspected['selectedVersion'] is None
        assert inspected['runningVersion'] == 1
        assert inspected['latestVersion'] == version
        assert inspected['previewRetained'] is True
        assert [item['version'] for item in inspected['versions']] == list(range(1, version + 1))
        assert preview_target(inspected) == preview_target(original)
        assert inspected['renderIdentity'] == original['renderIdentity']
        assert canvas(app)['revision'] == 1
        assert raw_source(canvas(app), app.db) == first
        with app.clients.bind('one'):
            running = app.canvas_views.canvas('primary')
        assert raw_source(running, app.db) == first
        assert running['document']['controls'][0]['value'] == 'unfinished'
    # Reattaching the same live page and Refresh are metadata reads, not Load.
    app.clients.attach('one')
    assert preview_target(preview(app)) == preview_target(original)
    assert preview(app)['renderIdentity'] == original['renderIdentity']
    assert preview(app, 'two')['selectedVersion'] == 1
    assert preview(app, 'two')['runningVersion'] == 1
    assert preview(app, 'two')['latestVersion'] == 3
    assert preview(app, 'two')['previewRetained'] is False
    assert preview_target(preview(app, 'two')) == preview_target(pinned)
    assert app.clients.records['one']['view']['draft'] == 'Unsent composer'
    # A new page has no inherited JS memory; Latest starts at the latest saved definition.
    app.clients.attach('fresh', 'one')
    assert preview(app, 'fresh')['runningVersion'] == 3
    assert preview(app, 'fresh')['selectedVersion'] is None
    assert preview(app, 'fresh')['previewRetained'] is False
    assert 'document' not in canvas(app, 'fresh')
    app.clients.attach('fresh-pinned', 'two')
    assert preview(app, 'fresh-pinned')['runningVersion'] == 1
    assert preview(app, 'fresh-pinned')['selectedVersion'] == 1


@pytest.mark.parametrize('kind', ['html', 'babylon'])
async def test_ui_load_uses_exact_saved_version_and_preserves_latest_intent_and_preferences(app, kind):
    from amplifier_web.canvas_documents import raw_source

    identity = (await call(app, 'canvas.show', {'kind': kind, 'content': '<p>One</p>'}))['result']['id']
    original = preview(app)
    await call(app, 'canvas.views.command', {**preview_target(original),
        'action': 'canvas.view', 'args': {'patch': {'source': True}}})
    await revise(app, identity, '<p>Two</p>')
    await revise(app, identity, '<p>Three</p>', 2)
    history = deepcopy(row(app, identity))
    loaded = (await call(app, 'canvas.views.loadVersion', {
        **preview_target(original), 'version': 2}, command_id='load-exact-two'))['result']
    assert loaded['runningVersion'] == 2 and loaded['latestVersion'] == 3
    assert loaded['selectedVersion'] is None and loaded['previewRetained'] is True
    assert loaded['view']['source'] is True
    assert loaded['renderer'] == original['renderer']
    assert loaded['generation'] > original['generation']
    assert loaded['renderIdentity'] != original['renderIdentity']
    assert raw_source(canvas(app), app.db) == '<p>Two</p>'
    assert row(app, identity) == history
    replay = (await call(app, 'canvas.views.loadVersion', {
        **preview_target(original), 'version': 2}, command_id='load-exact-two'))['result']
    assert replay == loaded
    assert preview_target(preview(app)) == preview_target(loaded)
    same = (await call(app, 'canvas.views.loadVersion', {
        **preview_target(loaded), 'version': 2}))['result']
    assert same['renderIdentity'] == loaded['renderIdentity']
    # Version source, copy, and downloads all address the running saved body.
    for version, text in ((1, 'One'), (2, 'Two'), (3, 'Three')):
        inspected = (await call(app, 'canvas.versions.inspect', {
            'id': identity, 'version': version, 'includeSource': True}))['result']
        assert inspected['source']['content'] == f'<p>{text}</p>'
    for action in ('canvas.copy', 'canvas.download'):
        response = await call(app, 'canvas.views.command', {
            **preview_target(loaded), 'action': action, 'args': {}})
        effect = response['effects'][0]
        if 'url' in effect:
            assert effect['url'].endswith('?version=2')
        else:
            assert effect['content'] == '<p>Two</p>'
    assert row(app, identity)['revision'] == 3
    await call(app, 'canvas.select', {'id': identity, 'version': 2})
    unpinned = (await call(app, 'canvas.views.loadVersion', {
        **preview_target(preview(app)), 'version': 2, 'followLatest': True}))['result']
    assert unpinned['selectedVersion'] is None
    assert unpinned['runningVersion'] == 2 and unpinned['latestVersion'] == 3


@pytest.mark.parametrize('kind', ['html', 'babylon'])
async def test_same_path_publication_and_restore_retain_latest_and_pinned_previews(app, tmp_path, kind):
    from amplifier_web.canvas_documents import raw_source

    path = tmp_path / 'retained.html'
    path.write_text('<input value="One">')
    identity = (await call(app, 'canvas.show', {'kind': kind, 'path': str(path)}))['result']['id']
    original = preview(app)
    await call(app, 'canvas.select', {'id': identity, 'version': 1}, 'two')
    pinned = preview(app, 'two')
    path.write_text('<input value="Two">')
    published = (await call(app, 'canvas.show', {'kind': kind, 'path': str(path)}))['result']
    assert published['id'] == identity and published['revision'] == 2
    assert raw_source(canvas(app), app.db) == '<input value="One">'
    assert preview_target(preview(app)) == preview_target(original)
    # A publication in the pinned client is also not an implicit selection.
    path.write_text('<input value="Three">')
    await call(app, 'canvas.show', {'kind': kind, 'path': str(path)}, 'two')
    assert preview_target(preview(app, 'two')) == preview_target(pinned)
    assert canvas(app, 'two')['selectedVersion'] == 1
    path.unlink()
    before = deepcopy(row(app, identity)['versions'])
    await call(app, 'canvas.versions.restore', {'id': identity, 'expectedRevision': 3, 'version': 2}, 'two')
    assert row(app, identity)['revision'] == 4
    assert row(app, identity)['versions'][:3] == before
    assert preview_target(preview(app)) == preview_target(original)
    assert preview_target(preview(app, 'two')) == preview_target(pinned)
    assert preview(app)['latestVersion'] == 4 and preview(app)['previewRetained']
    assert raw_source(canvas(app), app.db) == '<input value="One">'
    # Persistence must use the running body's resource, never Latest's resource.
    app._save()
    await app.close()
    reopened = AppService(app.data_dir, workspace=tmp_path)
    try:
        reopened.clients.attach('one')
        assert raw_source(canvas(reopened), reopened.db) == '<input value="One">'
        assert preview(reopened)['runningVersion'] == 1
        assert preview(reopened)['latestVersion'] == 4
        assert preview(reopened)['renderIdentity'] == original['renderIdentity']
        inspected = (await call(reopened, 'canvas.views.inspect'))['result']['views'][0]
        assert preview_target(inspected) == preview_target(original)
    finally:
        await reopened.close()
