from copy import deepcopy
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.canvas_views import PROFILE


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    await service.dispatch('session.create', {})
    service.clients.attach('one')
    service.clients.attach('two')
    yield service
    await service.close()


async def command(app, name, args=None, client='one', **kwargs):
    with app.clients.bind(client):
        return await app.dispatch(name, args or {}, **kwargs)


def view(app, identity='primary', client='one'):
    with app.clients.bind(client):
        return deepcopy(app.canvas_views.summary(identity))


def target(value):
    return {key: value[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}


async def show(app, kind='json', content='[{"name":"alpha"},{"name":"beta"}]'):
    await command(app, 'canvas.show', {'kind': kind, 'content': content})
    return view(app)


async def test_mount_evidence_does_not_rewrite_history_and_identical_reports_are_noops(app, monkeypatch):
    current = await show(app)
    original = deepcopy(app._state['sessions'])
    original_save = app._save
    monkeypatch.setattr(app, '_save', lambda: pytest.fail('Mount evidence must not persist the whole app'))
    args = {**target(current), 'status': 'ready', 'message': 'Sandbox document loaded'}
    first = await command(app, 'canvas.views.status', args, command_id='mounted', include_state=False)
    second = await command(app, 'canvas.views.status', args, command_id='mounted-again', include_state=False)
    assert first['revision'] == second['revision']
    assert app._state['sessions'] == original
    assert view(app)['activation']['status'] == 'ready'
    with pytest.raises(AppError):
        await command(app, 'canvas.views.status', {**args, 'generation': -1}, include_state=False)
    monkeypatch.setattr(app, '_save', original_save)


async def update(app, current, patch, **kwargs):
    return await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.view', 'args': {'patch': patch}}, **kwargs)


async def renderer(app, **extra):
    manifest = {'id': 'example.reader', 'label': 'Example reader', 'version': '1.0.0', 'apiVersion': '1.0',
                'profile': PROFILE, 'stateSchema': 'canvas-view-v1', 'resourceKinds': ['json', 'markdown'],
                'capabilities': ['canvas.resource.read', 'canvas.view.update']}
    manifest.update(extra)
    staged = await command(app, 'shell.packages.stage', {'manifest': manifest, 'source': 'export default ({React})=>()=>React.createElement("p",null,"Reader")'})
    digest = staged['result']['digest']
    return digest


def validate_fixture(app, digest):
    record = app.shell.get('package', digest)
    record['validation'] = {'status': 'passed', 'hostFingerprint': app.shell.host_fingerprint()}
    app.shell.put('package', digest, record)


async def test_single_view_retains_artifacts_and_ignores_legacy_split_state(app):
    first = await show(app)
    with app.clients.bind('one'):
        record = app.canvas_views.record()
        record['secondary'] = first['resourceId']
        record['preferences']['secondary:' + first['resourceId']] = {'dirty': True, 'view': {'query': 'saved'}}
    second = await show(app, 'text', 'Another artifact')
    with app.clients.bind('one'):
        assert [v['resourceId'] for v in app.canvas_views.project()['views']] == [second['resourceId']]
    await command(app, 'canvas.close')
    assert len(app.state['canvasArtifacts']) == 2
    await command(app, 'canvas.select', {'id': first['resourceId']})
    assert view(app)['resourceId'] == first['resourceId']
    for action in ('canvas.views.open', 'canvas.views.close'):
        with pytest.raises(AppError):
            await command(app, action, {'resourceId': first['resourceId']})
    with pytest.raises(AppError):
        await update(app, {**view(app), 'viewId': 'secondary'}, {'query': 'not a live view'})
    assert app.clients.records['two']['canvas'].get('kind') is None



async def test_renderer_must_be_validated_compatible_and_in_its_own_slot(app):
    current = await show(app)
    digest = await renderer(app)
    with pytest.raises(AppError, match='validation'):
        await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    validate_fixture(app, digest)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    selected = view(app)
    assert selected['renderer'] == digest
    assert selected['resourceRevision'] == current['resourceRevision']
    with pytest.raises(AppError, match='changed'):
        await update(app, current, {'query': 'late old renderer'})
    await update(app, selected, {'customSize': 20})
    assert view(app)['view']['customSize'] == 20
    bad = await renderer(app, id='example.image', resourceKinds=['image'])
    validate_fixture(app, bad)
    with pytest.raises(AppError, match='format'):
        await command(app, 'canvas.views.renderer', {**target(view(app)), 'renderer': bad})
    with pytest.raises(AppError, match='navigation package'):
        await command(app, 'shell.changes.prepare', {'clientId': 'one', 'expectedRevision': 0,
            'composition': {'instances': [{'id': 'wrong-slot', 'package': digest, 'slot': 'navigation'}], 'presentation': {}}})


async def test_dirty_and_incompatible_replacements_defer_and_recovery_retains_artifact(app):
    current = await show(app)
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': True})
    deferred = await command(app, 'canvas.views.renderer', {**target(current), 'renderer': current['renderer']})
    assert deferred['result']['status'] == 'deferred'
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': False})
    incompatible = await renderer(app, stateSchema='another-shape')
    validate_fixture(app, incompatible)
    result = await command(app, 'canvas.views.renderer', {**target(current), 'renderer': incompatible})
    assert result['result']['status'] == 'deferred'
    await command(app, 'canvas.views.recover', target(current))
    assert view(app)['resourceId'] == current['resourceId']
    assert view(app)['renderer'] == current['renderer']
    assert app.state['canvasArtifacts'][0]['id'] == current['resourceId']


async def test_missing_package_preserves_choice_resource_and_allows_default_recovery(app):
    current = await show(app)
    digest = await renderer(app)
    validate_fixture(app, digest)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    (app.shell.directory / (digest + '.mjs')).unlink()
    missing = view(app)
    assert not missing['available'] and missing['renderer'] == digest
    assert missing['resourceId'] == current['resourceId']
    await command(app, 'canvas.views.recover', target(missing))
    assert view(app)['available']


async def test_closed_reopened_view_rejects_old_actions_and_does_not_delete_content(app):
    old = await show(app)
    await update(app, old, {'query': 'saved filter'})
    await command(app, 'canvas.close')
    await command(app, 'canvas.reopen')
    assert view(app)['view']['query'] == 'saved filter'
    with pytest.raises(AppError, match='changed'):
        await update(app, old, {'query': 'delayed'})
    assert len(app.state['canvasArtifacts']) == 1



async def test_resource_revision_rejects_stale_actions(app):
    primary = await show(app)
    old = view(app)
    from amplifier_web.resource_files import put
    row = app.canvas_views.artifact(primary['resourceId'])
    row['body'] = put(app.db, {'content': '[{"name":"new revision"}]'})
    assert view(app)['resourceRevision'] != old['resourceRevision']
    with pytest.raises(AppError, match='changed'):
        await update(app, old, {'query': 'old content'})


async def test_view_preferences_survive_restart_and_cloned_client_does_not_share_them(app):
    current = await show(app)
    await update(app, view(app), {'query': 'kept'})
    await app.close()
    restored = AppService(app.data_dir, workspace=app.default_workspace)
    try:
        assert view(restored)['view']['query'] == 'kept'
        restored.clients.attach('clone', 'one')
        await update(restored, view(restored, client='clone'), {'query': 'clone only'}, client='clone')
        assert view(restored)['view']['query'] == 'kept'
    finally:
        await restored.close()


async def test_renderer_capabilities_cannot_dispatch_unrelated_operations_or_retarget(app):
    current = await show(app)
    with pytest.raises(AppError, match='capability'):
        await command(app, 'canvas.views.command', {**target(current), 'action': 'session.create', 'args': {}})
    with pytest.raises(AppError, match='retarget'):
        await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.view', 'args': {'id': 'other', 'patch': {}}})
    digest = await renderer(app, capabilities=['canvas.resource.read'])
    validate_fixture(app, digest)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    with pytest.raises(AppError, match='capability'):
        await update(app, view(app), {'customSize': 40})


async def test_returning_to_primary_artifact_invalidates_earlier_mount(app):
    old = await show(app)
    await show(app, 'text', 'Other artifact')
    await command(app, 'canvas.select', {'id': old['resourceId']})
    assert view(app)['generation'] > old['generation']
    with pytest.raises(AppError, match='changed'):
        await update(app, old, {'query': 'late from previous mount'})


async def test_failed_renderer_replacement_clears_activation_and_limits_total_state(app):
    current = await show(app)
    await command(app, 'canvas.views.status', {**target(current), 'status': 'error', 'message': 'Old failure'})
    digest = await renderer(app)
    validate_fixture(app, digest)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    assert view(app)['activation'] is None
    current = view(app)
    await update(app, current, {'first': 'x' * 9000})
    with pytest.raises(AppError, match='Accumulated'):
        await update(app, current, {'second': 'x' * 9000})
    assert 'second' not in view(app)['view']


async def test_missing_legacy_secondary_does_not_block_current_view(app):
    current = await show(app)
    with app.clients.bind('one'):
        app.canvas_views.record()['secondary'] = 'missing-artifact'
        assert len(app.canvas_views.project()['views']) == 1
    await command(app, 'canvas.close')
    assert app.state['canvasArtifacts'][0]['id'] == current['resourceId']



async def test_html_view_controls_only_the_addressed_client(app):
    first = await show(app, 'html', '<input value="initial">')
    await command(app, 'canvas.select', {'id': first['resourceId']}, client='two')
    second = view(app, client='two')
    document = {'text': '', 'controls': [{'id': 'field', 'tag': 'input', 'type': 'text', 'label': 'Note', 'value': 'initial', 'disabled': False}]}
    for current, client in ((first, 'one'), (second, 'two')):
        await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.snapshot', 'args': {'document': document}}, client=client)
    await command(app, 'canvas.views.command', {**target(second), 'action': 'canvas.interact', 'args': {'controlId': 'field', 'event': 'input', 'value': 'Second client only'}}, client='two')
    with app.clients.bind('two'):
        assert app.canvas_views.canvas('primary')['interaction']['value'] == 'Second client only'
    with app.clients.bind('one'):
        assert 'interaction' not in app.canvas_views.canvas('primary')



async def test_large_resource_is_loaded_only_on_explicit_source_read(app):
    path = Path(app.default_workspace) / 'large.html'
    path.write_text('<p>' + 'large ' * 180000 + '</p>')
    await command(app, 'canvas.show', {'kind': 'html', 'path': str(path)})
    current = view(app)
    from amplifier_web.canvas_documents import raw_source
    with app.clients.bind('one'):
        canvas = app.canvas_views.canvas('primary')
        assert 'content' not in canvas
        assert len(raw_source(canvas, app.db)) > 1_000_000
        result, effects = app.canvas_views.command('canvas.views.command', {**target(view(app)), 'action': 'canvas.copy', 'args': {}}, 'browser')
        assert effects[0]['type'] == 'clipboard.url'


async def test_babylon_export_retains_standalone_document_behavior(app):
    current = await show(app, 'babylon', '<script>const scene = true;</script>')
    response = await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.download', 'args': {}})
    assert response['effects'][0]['type'] == 'download.url'
    assert response['effects'][0]['filename'] == 'canvas-3d.html'


async def test_view_mutation_retry_does_not_replace_twice(app):
    current = await show(app)
    args = {**target(current), 'renderer': current['renderer']}
    first = await command(app, 'canvas.views.renderer', args, command_id='renderer-retry')
    generation = view(app)['generation']
    retry = await command(app, 'canvas.views.renderer', args, command_id='renderer-retry')
    assert first['result'] == retry['result']
    assert view(app)['generation'] == generation


async def test_reopened_canvas_invalidates_previous_mount(app):
    current = await show(app)
    previous = [view(app, identity) for identity in ('primary',)]
    await command(app, 'canvas.close')
    await command(app, 'canvas.reopen')
    for old in previous:
        with pytest.raises(AppError, match='changed'):
            await update(app, old, {'query': 'from before the close'})


async def test_agent_bridge_can_explicitly_address_a_view_without_retargeting_another_client(app):
    current = await show(app)
    sid = current['resource']['sessionId']
    observed = await app.app_bridge('dispatch', {'action': 'canvas.views.inspect', 'args': {'clientId': 'one'}}, sid)
    assert observed['result']['views'][0]['resourceId'] == current['resourceId']
    await app.app_bridge('dispatch', {'action': 'canvas.views.command', 'args': {'clientId': 'one', **target(current), 'action': 'canvas.view', 'args': {'patch': {'query': 'agent choice'}}}}, sid)
    assert view(app)['view']['query'] == 'agent choice'
    assert app.clients.records['two']['canvas'].get('kind') is None
    with pytest.raises(AppError, match='different client'):
        await command(app, 'canvas.views.inspect', {'clientId': 'one'}, client='two')
    with pytest.raises(AppError, match='Attach this client'):
        await app.dispatch('canvas.views.inspect', {'clientId': 'missing'})


async def test_bounded_choice_menu_keeps_an_older_selected_renderer(app):
    current = await show(app)
    selected = await renderer(app)
    validate_fixture(app, selected)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': selected})
    for number in range(51):
        digest = await renderer(app, id=f'example.newer-{number}')
        validate_fixture(app, digest)
    current = view(app)
    assert current['available']
    assert current['renderer'] == selected
    assert selected in {choice['id'] for choice in current['choices']}
    assert len(current['choices']) == 51


async def test_agent_download_reaches_only_its_bound_browser_and_diagnostics_follow_artifact(app, monkeypatch):
    current = await show(app, 'text', 'Source content')
    selected = current['resource']['sessionId']
    recorded = []
    monkeypatch.setattr(app.diagnostics, 'record', lambda stream, data, **scope: recorded.append((stream, data, scope)))
    await app.app_bridge('dispatch', {'action': 'canvas.views.command', 'args': {'clientId': 'one', **target(view(app)), 'action': 'canvas.download', 'args': {}}}, selected)
    effect = app.clients.records['one']['deviceCommands'][-1]
    assert effect['type'] == 'download' and effect['content'] == 'Source content'
    assert not app.clients.records['two']['deviceCommands']
    assert recorded[-1][1]['data']['sessionId'] == current['resource']['sessionId']
    assert recorded[-1][1]['data']['artifactId'] == current['resourceId']
    assert app.clients.records['one']['selectedSessionId'] == selected


async def test_legacy_body_is_materialized_after_restart(app):
    current = await show(app, 'markdown', '# A saved document')
    with app.clients.bind('one'):
        row = app.canvas_views.artifact(current['resourceId'])
        row['contentResource'] = row['body']
        app._save()
    await app.close()
    restored = AppService(app.data_dir, workspace=app.default_workspace)
    try:
        with restored.clients.bind('one'):
            canvas = restored.canvas_views.canvas('primary')
            assert canvas['content'] == '# A saved document'
            assert 'contentResource' not in canvas
            assert canvas['id'] == current['resourceId']
            assert canvas['resourceRevision'] == current['resourceRevision']
    finally:
        await restored.close()


@pytest.mark.parametrize('action', [
    'canvas.close', 'canvas.select', 'canvas.tabClose', 'canvas.show',
    'session.select', 'session.create', 'session.fork', 'message.edit',
    'workspace.select', 'workspace.add', 'workspace.create', 'workspace.remove', 'smartTools.open',
])
async def test_dirty_primary_blocks_parent_transitions_before_any_side_effect(app, action):
    other = await show(app, 'text', 'Other saved source')
    current = await show(app)
    sid = current['resource']['sessionId']
    await command(app, 'session.create', client='two')
    other_sid = app.clients.records['two']['selectedSessionId']
    await command(app, 'view.update', {'patch': {'draft': 'Keep the composer target'}})
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': True})
    args = {
        'canvas.close': {}, 'canvas.select': {'id': other['resourceId']},
        'canvas.tabClose': {'id': current['resourceId']},
        'canvas.show': {'kind': 'text', 'content': 'Never published'},
        'session.select': {'id': other_sid}, 'session.create': {},
        'session.fork': {'id': sid}, 'session.delete': {'id': sid},
        'message.edit': {'sessionId': sid, 'messageId': 'missing', 'text': 'Never sent'},
        'workspace.select': {'id': 'other'}, 'workspace.remove': {'id': 'other'},
        'workspace.add': {'path': str(app.data_dir / 'never-created')},
        'workspace.create': {'path': str(app.data_dir / 'never-created')},
        'smartTools.open': {'id': 'missing', 'tool': 'never-opened'},
    }[action]
    before = deepcopy(app.clients.records['one'])
    artifacts = deepcopy(app.state['canvasArtifacts'])
    sessions = deepcopy(app.state['sessions'])
    with pytest.raises(AppError, match='primary viewer edit'):
        await command(app, action, args)
    assert app.clients.records['one'] == before
    assert app.state['canvasArtifacts'] == artifacts
    assert app.state['sessions'] == sessions
    assert not (app.data_dir / 'never-created').exists()
    assert target(view(app)) == target(current)
    # The refusal isn't a successful receipt: retry after finishing the edit.
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': False})
    await command(app, 'canvas.select', {'id': other['resourceId']})
    assert view(app)['resourceId'] == other['resourceId']


async def test_dirty_primary_blocks_shared_chat_deletion(app):
    await command(app, 'session.create', {'location': {'kind': 'managed'}})
    current = await show(app)
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': True})
    sid = current['resource']['sessionId']
    reviewed = (await command(app, 'session.deletePreview', {'id': sid}, client='two'))['result']
    with pytest.raises(AppError, match='primary viewer edit'):
        await command(app, 'session.delete', {'id': sid, 'confirmationToken': reviewed['confirmationToken']}, client='two')
    await command(app, 'canvas.views.recover', target(current))
    await command(app, 'canvas.close')



async def test_dirty_primary_allows_background_publication_and_unrelated_client_navigation(app):
    current = await show(app)
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': True})
    await command(app, 'session.create', client='two')
    sid = app.clients.records['two']['selectedSessionId']
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Background source', 'sessionId': sid})
    await command(app, 'session.select', {'id': current['resource']['sessionId']})
    assert target(view(app)) == target(current)
    assert view(app)['dirty']
    assert len(app.state['canvasArtifacts']) == 2


async def test_mcp_open_rechecks_dirty_primary_after_resource_io(app, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from amplifier_web.smart_canvas import SmartCanvas

    current = await show(app)
    started, finish = asyncio.Event(), asyncio.Event()

    async def read_app(*args):
        started.set()
        await finish.wait()
        return {'html': '<p>Tool app</p>', 'tools': []}

    monkeypatch.setattr(app, 'smart_tools', SimpleNamespace(read_app=read_app, close=AsyncMock()))
    app.state.setdefault('smartTools', {})['servers'] = [{'id': 'test', 'name': 'Test', 'tools': [
        {'name': 'open', '_meta': {'ui': {'resourceUri': 'ui://test'}}}]}]
    with app.clients.bind('one'):
        task = asyncio.create_task(SmartCanvas(app).open({'id': 'test', 'tool': 'open'}))
    await started.wait()
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': True})
    finish.set()
    with pytest.raises(AppError, match='primary viewer edit'):
        await task
    assert target(view(app)) == target(current)
    assert len(app.state['canvasArtifacts']) == 1


@pytest.mark.parametrize('kind', ['html', 'babylon'])
async def test_load_version_refuses_agent_stale_missing_and_dirty_before_replacement(app, kind):
    current = await show(app, kind, '<input value="One">')
    await command(app, 'canvas.versions.revise', {
        'id': current['resourceId'], 'expectedRevision': 1, 'content': '<input value="Two">'})
    args = {**target(current), 'version': 2}
    saved = deepcopy(app.state['canvasArtifacts'])
    original = deepcopy(app.clients.records['one']['canvas'])
    with pytest.raises(AppError, match='explicit user control'):
        await command(app, 'canvas.views.loadVersion', args, origin='agent')
    for stale in ({'resourceId': 'other'}, {'resourceRevision': 'stale'}, {'generation': current['generation'] + 1}):
        with pytest.raises(AppError, match='changed'):
            await command(app, 'canvas.views.loadVersion', {**args, **stale})
        assert app.clients.records['one']['canvas'] == original
        assert target(view(app)) == target(current)
    with pytest.raises(AppError, match='unavailable'):
        await command(app, 'canvas.views.loadVersion', {**args, 'version': 999})
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': True})
    with pytest.raises(AppError, match='preview edit') as rejected:
        await command(app, 'canvas.views.loadVersion', args)
    assert rejected.value.code == 'canvas_view_dirty'
    assert app.clients.records['one']['canvas'] == original
    assert target(view(app)) == target(current)
    assert app.state['canvasArtifacts'] == saved
    assert view(app)['dirty'] is True
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': False})
    loaded = (await command(app, 'canvas.views.loadVersion', args))['result']
    assert loaded['runningVersion'] == 2
    with pytest.raises(AppError, match='changed'):
        await command(app, 'canvas.views.loadVersion', args)


@pytest.mark.parametrize('kind', ['html', 'babylon'])
async def test_same_artifact_select_requires_ui_reset_for_different_executable_version(app, kind):
    current = await show(app, kind, '<input value="One">')
    await command(app, 'canvas.versions.revise', {
        'id': current['resourceId'], 'expectedRevision': 1, 'content': '<input value="Two">'})
    for selection in ({'id': current['resourceId']}, {'id': current['resourceId'], 'version': 2}):
        original = deepcopy(app.clients.records['one']['canvas'])
        with pytest.raises(AppError) as refused:
            await command(app, 'canvas.select', selection)
        assert refused.value.code == 'canvas_preview_reset_required'
        with pytest.raises(AppError, match='explicit user control'):
            await command(app, 'canvas.select', {**selection, 'resetPreview': True}, origin='agent')
        assert app.clients.records['one']['canvas'] == original
        assert target(view(app)) == target(current)
    # Selecting the already-running definition intentionally pins it, without
    # remounting or clearing its DOM evidence.
    await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.snapshot',
        'args': {'document': {'text': 'Running One', 'controls': []}}})
    await command(app, 'canvas.select', {'id': current['resourceId'], 'version': 1})
    assert view(app)['selectedVersion'] == 1
    assert target(view(app)) == target(current)
    assert view(app)['renderIdentity'] == current['renderIdentity']
    assert app.clients.records['one']['canvas']['document']['text'] == 'Running One'
    await command(app, 'canvas.select', {'id': current['resourceId'], 'version': 1})
    assert target(view(app)) == target(current)
    # Reset is never a bypass for a declared dirty edit.
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': True})
    with pytest.raises(AppError, match='viewer edit'):
        await command(app, 'canvas.select', {'id': current['resourceId'], 'resetPreview': True})
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': False})
    await command(app, 'canvas.select', {'id': current['resourceId'], 'resetPreview': True})
    latest = view(app)
    assert latest['selectedVersion'] is None
    assert latest['runningVersion'] == 2 and latest['previewRetained'] is False
    assert latest['renderIdentity'] != current['renderIdentity']
    # Reselecting Latest already running the newest body is also a no-op.
    await command(app, 'canvas.select', {'id': current['resourceId']})
    assert view(app)['renderIdentity'] == latest['renderIdentity']


async def test_load_version_is_primary_executable_only_and_preserves_historical_intent(app):
    passive = await show(app, 'text', 'Passive')
    with pytest.raises(AppError, match='primary HTML or 3D'):
        await command(app, 'canvas.views.loadVersion', {**target(passive), 'version': 1})
    # Passive Latest still follows its new body normally.
    await command(app, 'canvas.versions.revise', {
        'id': passive['resourceId'], 'expectedRevision': 1, 'content': 'Passive new'})
    assert view(app)['latestVersion'] == 2
    assert view(app)['runningVersion'] is None and view(app)['previewRetained'] is False
    assert app.clients.records['one']['canvas']['content'] == 'Passive new'
    app._message(app._session(), 'user', 'Interactive preview')
    current = await show(app, 'html', '<p>One</p>')
    identity = current['resourceId']
    await command(app, 'canvas.select', {'id': identity, 'version': 1})
    await command(app, 'canvas.versions.revise', {'id': identity, 'expectedRevision': 1, 'content': '<p>Two</p>'})
    loaded = (await command(app, 'canvas.views.loadVersion', {**target(view(app)), 'version': 2}))['result']
    assert loaded['selectedVersion'] == loaded['runningVersion'] == 2
    assert not loaded['previewRetained']
    await command(app, 'canvas.versions.revise', {'id': identity, 'expectedRevision': 2, 'content': '<p>Three</p>'})
    assert view(app)['selectedVersion'] == 2 and view(app)['latestVersion'] == 3
    # Even a valid inline lease cannot use the primary-only replacement action.
    message = app._session()['messages'][-1]['id']
    inline = (await command(app, 'canvas.views.inline', {
        'resourceId': identity, 'version': 1, 'messageId': message, 'previewId': 'preview-one'}))['result']
    with pytest.raises(AppError, match='primary HTML or 3D'):
        await command(app, 'canvas.views.loadVersion', {**target(inline), 'version': 2})
