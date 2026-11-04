import copy
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from amplifier_web.canvas_downloads import filename
from amplifier_web.canvas_versions import command as versions_command
from amplifier_web.service import AppError, AppService
from test_canvas_views import app, command, target, view


@pytest.mark.parametrize(('canvas', 'expected'), [
    ({'kind': 'markdown', 'path': '/private/project/Plan été.md'}, 'Plan été.md'),
    ({'kind': 'code', 'path': r'C:\project\run.py'}, 'run.py'),
    ({'kind': 'text', 'path': '/project/.gitignore'}, '.gitignore'),
    ({'kind': 'markdown', 'path': '/project/bad\r\n"name.md'}, 'bad___name.md'),
    ({'kind': 'markdown', 'path': '/project/..'}, 'canvas.md'),
    ({'kind': 'markdown', 'title': 'Design notes'}, 'Design notes.md'),
    ({'kind': 'markdown', 'title': 'Design notes.md'}, 'Design notes.md'),
    ({'kind': 'html', 'title': 'Team report'}, 'Team report.html'),
    ({'kind': 'markdown', 'title': '../unsafe/title'}, '_unsafe_title.md'),
    ({'kind': 'markdown', 'title': '...'}, 'canvas.md'),
    ({'kind': 'markdown', 'title': 'Friendly title', 'path': '/project/actual.md'}, 'actual.md'),
    ({'kind': 'babylon'}, 'canvas-3d.html'),
    ({'kind': 'image', 'path': '/project/photo.png'}, 'canvas.txt'),
])
def test_safe_source_filename(canvas, expected):
    assert filename(canvas) == expected


@pytest.fixture
def metadata_service(monkeypatch):
    from amplifier_web import canvas_library, state_storage

    def no_body_read(*args, **kwargs):
        pytest.fail('Metadata inspection must not load or mount a saved body')

    monkeypatch.setattr(state_storage, 'resource', no_body_read)
    monkeypatch.setattr(canvas_library, 'load', no_body_read)
    owner = {'id': 'chat', 'workspace': '/project', 'workingDirectory': '/worktrees/current',
             'messages': [{'id': 'message', 'role': 'user', 'content': 'Keep history'}]}
    first = {'version': 1, 'title': 'Saved only', 'kind': 'html', 'body': {'$resource': 'first'}}
    second = {'version': 2, 'title': 'Latest', 'kind': 'html', 'body': {'$resource': 'second'},
              'path': '/worktrees/current/report.html', 'workspacePath': '/old/base'}
    row = {**second, 'id': 'saved', 'sessionId': 'chat', 'revision': 2, 'versions': [first, second]}
    state = {'sessions': [owner], 'selectedSessionId': 'chat', 'canvasArtifacts': [row],
             'canvas': {'id': 'unrelated', 'open': False}, 'view': {'draft': 'Keep draft'}}
    return SimpleNamespace(state=state, db=object(), _session=lambda sid, hydrate=False: owner)


def test_inspect_exact_metadata_preserves_history_and_never_loads_body(metadata_service):
    service = metadata_service
    before = copy.deepcopy(service.state)
    for requested, expected in (({}, 2), ({'version': 1}, 1), ({'version': 2}, 2)):
        result = versions_command(service, 'canvas.versions.inspect', {'id': 'saved', **requested}, 'app')
        assert result['version'] == expected
        assert result['revision'] == 2
        assert result['reference'].endswith('version=' + str(expected))
        assert result['download'] == {
            'url': '/api/canvas/saved/download?version=' + str(expected),
            'filename': 'Saved only.html' if expected == 1 else 'report.html'}
        assert 'source' not in result
        assert result['filePaths'] == ({} if expected == 1 else {
            'absolute': '/worktrees/current/report.html', 'relative': 'report.html',
            'base': '/worktrees/current', 'workspace': '/project'})
    # Version 1 has no recorded path: it must not inherit version 2's metadata.
    assert service.state == before


def test_inspect_historical_path_and_filename_do_not_inherit_latest_metadata(metadata_service):
    row = metadata_service.state['canvasArtifacts'][0]
    row['versions'][0].update(path='/retired/original.html', workspacePath='/retired')
    before = copy.deepcopy(metadata_service.state)
    result = versions_command(metadata_service, 'canvas.versions.inspect', {'id': 'saved', 'version': 1}, 'app')
    assert result['filePaths'] == {
        'absolute': '/retired/original.html', 'base': '/worktrees/current', 'workspace': '/project'}
    assert result['download'] == {'url': '/api/canvas/saved/download?version=1', 'filename': 'original.html'}
    assert metadata_service.state == before


def test_inspect_historical_app_metadata_does_not_load_body_or_state(metadata_service):
    row = metadata_service.state['canvasArtifacts'][0]
    row['kind'] = 'canvas-app'
    row['app'] = {'revision': 2, 'manifest': {}, 'versions': [
        {'version': 1, 'title': 'Historical app', 'body': {'$resource': 'app-body'},
         'stateResource': {'$resource': 'app-state'}}]}
    before = copy.deepcopy(metadata_service.state)
    result = versions_command(metadata_service, 'canvas.versions.inspect', {'id': 'saved', 'version': 1}, 'app')
    assert result['version'] == 1
    assert result['title'] == 'Historical app'
    assert 'download' not in result
    assert metadata_service.state == before


@pytest.mark.parametrize('kind', ['text', 'markdown', 'code', 'html', 'mermaid', 'dot', 'json', 'jsonl', 'babylon'])
def test_inspect_download_metadata_for_supported_saved_text(metadata_service, kind):
    row = metadata_service.state['canvasArtifacts'][0]
    row['kind'] = row['versions'][1]['kind'] = kind
    result = versions_command(metadata_service, 'canvas.versions.inspect', {'id': 'saved', 'version': 2}, 'app')
    assert result['download'] == {'url': '/api/canvas/saved/download?version=2', 'filename': filename(row)}


@pytest.mark.parametrize('kind', ['browser', 'settings', 'mcp-app', 'gif', 'a2ui', 'image', 'pdf', 'canvas-app', 'unknown'])
def test_inspect_does_not_offer_unsupported_downloads(metadata_service, kind):
    row = metadata_service.state['canvasArtifacts'][0]
    row['kind'] = row['versions'][1]['kind'] = kind
    result = versions_command(metadata_service, 'canvas.versions.inspect', {'id': 'saved', 'version': 2}, 'app')
    assert 'download' not in result


def test_inspect_missing_version_and_other_chat_refuse_before_exposing_metadata(metadata_service):
    before = copy.deepcopy(metadata_service.state)
    for args in ({'id': 'saved', 'version': 999}, {'id': 'saved', 'sessionId': 'other-chat'}, {'id': 'absent'}):
        with pytest.raises(AppError, match='unavailable'):
            versions_command(metadata_service, 'canvas.versions.inspect', args, 'app')
    assert metadata_service.state == before


async def test_public_inspect_keeps_preview_selection_and_history_unchanged(app):
    saved = (await command(app, 'canvas.show', {
        'kind': 'html', 'content': '<script>throw new Error("Do not execute")</script>',
        'presentation': 'saved', 'title': 'Download only'}))['result']
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Keep this preview'})
    before_history = copy.deepcopy(app.state['sessions'])
    before_artifacts = copy.deepcopy(app.state['canvasArtifacts'])
    keys = ('selectedSessionId', 'selectedWorkspaceId', 'canvas', 'canvasViews', 'canvasTabs', 'view', 'deviceCommands')
    before_clients = {identity: {key: copy.deepcopy(record.get(key)) for key in keys}
                      for identity, record in app.clients.records.items()}
    response = await command(app, 'canvas.versions.inspect', {'id': saved['id'], 'version': 1}, include_state=False)
    assert response['effects'] == []
    assert response['result']['filePaths'] == {}
    assert response['result']['download'] == {
        'url': f'/api/canvas/{saved["id"]}/download?version=1', 'filename': 'Download only.html'}
    assert app.state['sessions'] == before_history
    assert app.state['canvasArtifacts'] == before_artifacts
    assert {identity: {key: record.get(key) for key in keys}
            for identity, record in app.clients.records.items()} == before_clients


async def test_running_preview_metadata_copy_and_download_do_not_follow_latest(app, tmp_path):
    path = tmp_path / 'running.html'
    original, newer = '<h1>Running v1</h1>\r\n', '<h1>Saved v2</h1>\r\n'
    path.write_bytes(original.encode())
    await command(app, 'canvas.show', {'kind': 'html', 'path': str(path)})
    before = view(app)
    await command(app, 'view.update', {'patch': {'draft': 'Keep unfinished draft'}})
    path.write_bytes(newer.encode())
    await command(app, 'canvas.show', {'kind': 'html', 'path': str(path), 'presentation': 'saved'})
    current = view(app)
    assert current['selectedVersion'] is None
    assert current['runningVersion'] == 1 and current['latestVersion'] == 2
    assert target(current) == target(before)
    history = copy.deepcopy(app.state['sessions'])
    metadata = await command(app, 'canvas.versions.inspect', {
        'id': current['resourceId'], 'version': current['runningVersion']}, include_state=False)
    assert metadata['effects'] == []
    assert metadata['result']['version'] == 1 and metadata['result']['revision'] == 2
    assert 'source' not in metadata['result']
    assert metadata['result']['filePaths']['absolute'] == str(path)
    assert metadata['result']['download'] == {
        'url': f'/api/canvas/{current["resourceId"]}/download?version=1', 'filename': path.name}
    for action in ('canvas.copy', 'canvas.download'):
        result = await command(app, 'canvas.views.command', {**target(current), 'action': action, 'args': {}})
        assert result['effects'][0]['content'] == original
    assert target(view(app)) == target(current)
    assert view(app)['runningVersion'] == 1 and view(app)['latestVersion'] == 2
    assert app.clients.records['one']['view']['draft'] == 'Keep unfinished draft'
    assert app.state['sessions'] == history and path.read_bytes() == newer.encode()
    latest = await command(app, 'canvas.versions.inspect', {
        'id': current['resourceId'], 'version': 2, 'includeSource': True})
    assert latest['result']['source']['content'] == newer


async def test_agent_inspect_cannot_read_another_chats_download_metadata(app):
    owner = app._session()['id']
    saved = (await command(app, 'canvas.show', {'kind': 'text', 'content': 'Owned'}))['result']
    await command(app, 'session.create', client='two')
    other = app.clients.records['two']['selectedSessionId']
    for extra in ({}, {'sessionId': owner}):
        with pytest.raises(AppError, match='unavailable|calling conversation'):
            await app.app_bridge('dispatch', {
                'action': 'canvas.versions.inspect', 'args': {'id': saved['id'], **extra}}, other)


async def test_both_download_actions_keep_original_name_and_snapshot(app, tmp_path):
    path = tmp_path / 'Plan été.md'
    original = '# Saved\r\n\nOriginal content.'
    path.write_bytes(original.encode())
    await command(app, 'canvas.show', {'kind': 'auto', 'path': str(path)})
    current = view(app)
    identity = current['resourceId']
    path.unlink()
    with app.clients.bind('one'):
        before = copy.deepcopy(app.state['sessions'])
    for action, args in [
        ('canvas.download', {'id': identity}),
        ('canvas.views.command', {**target(current), 'action': 'canvas.download', 'args': {}}),
    ]:
        result = await command(app, action, args)
        effect = result['effects'][0]
        assert effect['filename'] == path.name
        assert effect['content'] == original
    with app.clients.bind('one'):
        assert app.state['sessions'] == before


async def test_name_survives_restart(tmp_path):
    path = tmp_path / 'saved.md'
    path.write_text('# Saved')
    first = AppService(tmp_path / 'app', workspace=tmp_path)
    await first.dispatch('session.create', {})
    await first.dispatch('canvas.show', {'kind': 'auto', 'path': str(path)})
    identity = first.state['canvas']['id']
    await first.close()
    path.unlink()
    restored = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await restored.dispatch('canvas.select', {'id': identity})
        result = await restored.dispatch('canvas.download', {'id': identity})
        assert result['effects'][0]['filename'] == 'saved.md'
        assert result['effects'][0]['content'] == '# Saved'
    finally:
        await restored.close()


async def test_image_download_is_exact_binary_version(authenticated_client, tmp_path):
    import base64
    from amplifier_web.server import create_app
    from test_output_images import png
    from test_service import Runtime
    host = await create_app(tmp_path / 'app', preload_providers=False, workspace=tmp_path,
                            runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(host)
    service = host['service']
    await service.dispatch('session.create', {})
    image = png(2, 2)
    await service.dispatch('canvas.show', {'kind': 'image', 'content': 'data:image/png;base64,' + base64.b64encode(image).decode()})
    identity = service.state['canvas']['id']
    effect = (await service.dispatch('canvas.download', {'id': identity}))['effects'][0]
    assert effect['filename'] == 'canvas.png'
    await service.dispatch('canvas.versions.revise', {'id': identity, 'expectedRevision': 1,
        'content': 'data:image/png;base64,' + base64.b64encode(png(3, 2)).decode()})
    response = await client.get(effect['url'])
    assert response.status == 200
    assert response.content_type == 'image/png'
    assert await response.read() == image


async def test_http_download_name_and_body_survive_source_deletion(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    host = await create_app(tmp_path / 'app', preload_providers=False, workspace=tmp_path,
                            runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(host)
    service = host['service']
    await service.dispatch('session.create', {})
    path = tmp_path / 'résumé.html'
    body = '<h1>Original</h1>\r\n'
    path.write_bytes(body.encode())
    await service.dispatch('canvas.show', {'kind': 'auto', 'path': str(path)})
    identity = service.state['canvas']['id']
    path.unlink()
    response = await client.get(f'/api/canvas/{identity}/download')
    assert response.status == 200
    assert response.headers['Content-Disposition'] == "attachment; filename*=UTF-8''" + quote(path.name, safe='')
    assert await response.read() == body.encode()


@pytest.mark.parametrize('large', [False, True])
async def test_inspected_download_survives_later_revision_and_source_deletion(authenticated_client, tmp_path, large):
    from amplifier_web.server import create_app
    from test_service import Runtime
    host = await create_app(tmp_path / 'app', preload_providers=False, workspace=tmp_path,
                            runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(host)
    service = host['service']
    await service.dispatch('session.create', {})
    path = tmp_path / 'saved report.html'
    body = '<h1>Original saved report</h1>\r\n' + ('<!--' + 'x' * 1_100_000 + '-->' if large else '')
    path.write_bytes(body.encode())
    saved = (await service.dispatch('canvas.show', {
        'kind': 'auto', 'path': str(path), 'presentation': 'saved'}))['result']
    inspected = (await service.dispatch('canvas.versions.inspect', {'id': saved['id']}))['result']
    assert inspected['version'] == 1
    assert inspected['download'] == {
        'url': f'/api/canvas/{saved["id"]}/download?version=1', 'filename': path.name}
    await service.dispatch('canvas.versions.revise', {
        'id': saved['id'], 'expectedRevision': 1, 'content': '<h1>Later revision</h1>'})
    path.unlink()
    latest = (await service.dispatch('canvas.versions.inspect', {'id': saved['id']}))['result']
    assert latest['version'] == latest['revision'] == 2
    assert latest['download']['url'].endswith('?version=2')
    historical = (await service.dispatch('canvas.versions.inspect', {'id': saved['id'], 'version': 1}))['result']
    assert historical['download'] == inspected['download']
    assert historical['filePaths']['absolute'] == str(path)
    for details, expected in ((inspected, body), (latest, '<h1>Later revision</h1>')):
        response = await client.get(details['download']['url'])
        assert response.status == 200
        assert response.headers['Content-Disposition'] == "attachment; filename*=UTF-8''" + quote(path.name, safe='')
        assert await response.read() == expected.encode()
    response = await client.get(f'/api/canvas/{saved["id"]}/download?version=999')
    assert response.status == 404


async def test_exact_download_and_source_urls_do_not_follow_later_revisions(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    host = await create_app(tmp_path/'app', preload_providers=False, workspace=tmp_path,
                            runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(host)
    service = host['service']
    await service.dispatch('session.create', {})
    content = '<h1>Original</h1><!--' + 'x' * 1_100_000 + '-->'
    path = tmp_path/'large.html'
    path.write_text(content)
    result = await service.dispatch('canvas.show', {'kind':'auto','path':str(path)})
    identity = result['result']['id']
    effects = [(await service.dispatch(action, {'id':identity}))['effects'][0] for action in ('canvas.copy','canvas.download')]
    await service.dispatch('canvas.versions.revise', {'id':identity,'expectedRevision':1,'content':'<h1>Newer</h1>'})
    for effect in effects:
        assert effect['url'].endswith('?version=1')
        response = await client.get(effect['url'])
        assert response.status == 200
        assert await response.text() == content
    for version in ('999','invalid','0'):
        response = await client.get(f'/api/canvas/{identity}/source?version={version}')
        assert response.status in {400,404}


async def test_source_and_download_preserve_small_large_small_file_versions(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    host = await create_app(tmp_path/'app', preload_providers=False, workspace=tmp_path,
                            runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(host)
    service = host['service']
    await service.dispatch('session.create', {})
    path = tmp_path/'versions.html'
    contents = ['<h1>First small</h1>', '<h1>Large</h1><!--' + 'x' * 1_100_000 + '-->', '<h1>Second small</h1>']
    for content in contents:
        path.write_text(content)
        result = await service.dispatch('canvas.show', {'kind': 'auto', 'path': str(path)})
    identity = result['result']['id']
    path.unlink()
    for version, content in enumerate(contents, 1):
        await service.dispatch('canvas.select', {'id': identity, 'version': version, 'resetPreview': True})
        document = await client.get(f'/api/canvas/{identity}/document')
        assert document.status == 200
        assert (await document.text()).endswith(content)
        for endpoint in ('source', 'download'):
            # Explicit chat links are immutable; unversioned URLs mean latest.
            for suffix in ('', '?version=' + str(version)):
                response = await client.get(f'/api/canvas/{identity}/{endpoint}' + suffix)
                assert response.status == 200
                assert await response.text() == (content if suffix else contents[-1])
