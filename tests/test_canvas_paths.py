from pathlib import Path

import pytest

from amplifier_web.canvas_paths import paths
from amplifier_web.service import AppError
from test_canvas_views import app, command, view, target, renderer, validate_fixture


async def test_copy_paths_retains_original_workspace_after_restart_and_deleted_file(app):
    root = Path(app.default_workspace)
    source = root / 'notes' / 'hello world.md'
    source.parent.mkdir()
    source.write_text('# Preserved file')
    await command(app, 'canvas.show', {'kind': 'markdown', 'path': str(source)})
    current = view(app)
    source.unlink()
    assert current['filePaths'] == {'absolute': str(source.resolve()), 'relative': 'notes/hello world.md',
                                    'base': str(root), 'workspace': str(root)}
    history = list(app.state['sessions'][0]['messages'])
    copied = await command(app, 'canvas.copyPath', {'id': current['resourceId'], 'format': 'relative'})
    assert copied['result']['path'] == 'notes/hello world.md'
    assert copied['effects'][0]['content'] == 'notes/hello world.md'
    assert app.state['sessions'][0]['messages'] == history
    await app.close()
    from amplifier_web.service import AppService
    restored = AppService(app.data_dir, workspace=root)
    try:
        assert view(restored)['filePaths'] == current['filePaths']
    finally:
        await restored.close()


async def test_agent_copy_uses_shared_target_and_only_intended_client(app):
    source = Path(app.default_workspace) / 'file.txt'
    source.write_text('content')
    await command(app, 'canvas.show', {'kind': 'text', 'path': str(source)})
    current = view(app)
    digest = await renderer(app, resourceKinds=['text'])
    validate_fixture(app, digest)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    current = view(app)
    response = await app.app_bridge('dispatch', {'action': 'canvas.views.command', 'args': {
        'clientId': 'one', **target(current), 'action': 'canvas.copyPath', 'args': {'format': 'absolute'}}}, current['resource']['sessionId'])
    assert response['result']['path'] == str(source.resolve())
    assert app.clients.records['one']['deviceCommands'][-1]['content'] == str(source.resolve())
    assert not app.clients.records['two']['deviceCommands']
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Inline'})
    with pytest.raises(AppError, match='changed'):
        await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.copyPath', 'args': {'format': 'absolute'}})
    with pytest.raises(AppError, match='no saved'):
        await command(app, 'canvas.copyPath', {'id': view(app)['resourceId'], 'format': 'absolute'})


def test_unknown_or_outside_workspace_does_not_invent_a_relative_path():
    assert paths({}, {'path': '/other/file.txt', 'workspacePath': '/workspace'}) == {
        'absolute': '/other/file.txt', 'base': '/workspace', 'workspace': '/workspace'}
    assert paths({}, {'path': 'relative/legacy.txt'}) == {}
    assert paths({}, {}) == {}


def test_paths_use_current_owner_working_folder_without_reading_files(monkeypatch):
    def no_filesystem(*args, **kwargs):
        pytest.fail('Saved path metadata must not consult the filesystem')

    owner = {'id': 'chat', 'workspace': '/project', 'workingDirectory': '/worktrees/task'}
    state = {'sessions': [owner]}
    saved = {'sessionId': 'chat', 'path': '/worktrees/task/generated/report.html', 'workspacePath': '/old/base'}
    with monkeypatch.context() as patch:
        for name in ('resolve', 'stat', 'open'):
            patch.setattr(Path, name, no_filesystem)
        assert paths(state, saved) == {
            'absolute': saved['path'], 'relative': 'generated/report.html',
            'base': '/worktrees/task', 'workspace': '/project'}
        # Moving the owner cannot move the historical file or invent ../ paths.
        owner['workingDirectory'] = '/worktrees/replacement'
        assert paths(state, saved) == {
            'absolute': saved['path'], 'base': '/worktrees/replacement', 'workspace': '/project'}
        assert paths(state, {'sessionId': 'chat'}) == {}


def test_paths_fallback_uses_owner_workspace_then_recorded_workspace_only_without_owner():
    saved = {'sessionId': 'chat', 'path': '/project/plan.md', 'workspacePath': '/old/base'}
    assert paths({'sessions': [{'id': 'chat', 'workspace': '/project'}]}, saved) == {
        'absolute': '/project/plan.md', 'relative': 'plan.md', 'base': '/project', 'workspace': '/project'}
    # An owner with unknown placement must not inherit an old saved base.
    assert paths({'sessions': [{'id': 'chat'}]}, saved) == {'absolute': '/project/plan.md'}
    assert paths({}, {**saved, 'workspacePath': '/project'}) == {
        'absolute': '/project/plan.md', 'relative': 'plan.md', 'base': '/project', 'workspace': '/project'}


@pytest.mark.parametrize('path', ['/workspace/../outside.txt', '/workspace-old/file.txt', '/elsewhere/file.txt'])
def test_relative_paths_refuse_lexical_escape_and_sibling_prefixes(path):
    assert paths({}, {'path': path, 'workspacePath': '/workspace'}) == {
        'absolute': path, 'base': '/workspace', 'workspace': '/workspace'}


async def test_generated_file_preview_uses_working_directory_and_keeps_boundary(app, tmp_path_factory):
    root = Path(app.default_workspace)
    working = tmp_path_factory.mktemp('canvas-worktree')
    source = working / 'generated' / 'report.html'
    source.parent.mkdir(parents=True)
    source.write_text('<h1>Generated in the worktree</h1>')
    app._session()['workingDirectory'] = str(working)
    result = await command(app, 'canvas.show', {'kind': 'auto', 'path': 'generated/report.html'})
    current = view(app)
    assert current['filePaths'] == {
        'absolute': str(source), 'relative': 'generated/report.html',
        'base': str(working), 'workspace': str(root)}
    # Both absolute and relative spellings resolve to the same saved artifact.
    again = await command(app, 'canvas.show', {'kind': 'auto', 'path': str(source)})
    assert again['result']['id'] == result['result']['id']
    outside = root / 'outside.html'
    outside.write_text('<h1>Outside the current working folder</h1>')
    (working / 'escape.html').symlink_to(outside)
    for path in ('../outside.html', str(outside), 'escape.html'):
        with pytest.raises(AppError, match='inside the selected workspace'):
            await command(app, 'canvas.show', {'kind': 'auto', 'path': path})
    assert view(app)['resourceId'] == current['resourceId']


async def test_workspace_files_keeps_workspace_boundary_with_worktree(app, tmp_path_factory):
    root = Path(app.default_workspace)
    working = tmp_path_factory.mktemp('file-browser-worktree')
    session = app._session()
    session['workingDirectory'] = str(working)
    path = root / 'workspace-report.md'
    path.write_text('# Workspace report')
    workspace_id = app.state['selectedWorkspaceId']
    await command(app, 'view.update', {'patch': {
        'workSurface': 'workspace', 'workWorkspaceId': workspace_id, 'workWorkspaceTab': 'files'}})
    result = await command(app, 'canvas.openFile', {
        'sessionId': session['id'], 'workspace': str(root), 'path': str(path), 'showConversation': True})
    assert result['result']['status'] == 'opened'
    assert view(app)['filePaths'] == {
        'absolute': str(path), 'base': str(working), 'workspace': str(root)}


async def test_reveal_binds_exact_view_and_owning_host_without_browser_path_guess(app,monkeypatch):
    from unittest.mock import AsyncMock
    from amplifier_web import canvas_reveal
    source=Path(app.default_workspace)/'$(touch malicious).txt';source.write_text('fixture')
    await command(app,'canvas.show',{'kind':'text','path':str(source)})
    current=view(app)
    host=current['fileReveal']['host']['id'];instance=current['fileReveal']['instanceId']
    original=canvas_reveal.status
    monkeypatch.setattr(canvas_reveal,'status',lambda id:{**original(id),'available':True})
    monkeypatch.setattr(canvas_reveal.platform,'system',lambda:'Darwin')
    child=type('Child',(),{'returncode':0,'wait':AsyncMock(return_value=0)})()
    spawn=AsyncMock(return_value=child);monkeypatch.setattr(canvas_reveal.asyncio,'create_subprocess_exec',spawn)
    args={**target(current),'hostId':host,'instanceId':instance}
    result=await command(app,'canvas.views.reveal',args)
    assert result['result']['host']['id']==host
    assert spawn.call_args.args==('/usr/bin/open','-R',str(source))
    assert not (source.parent/'malicious').exists()
    with pytest.raises(AppError,match='host changed'):
        await command(app,'canvas.views.reveal',{**args,'hostId':'another-browser-device'})
    source.unlink()
    with pytest.raises(AppError,match='no longer exists'):
        await command(app,'canvas.views.reveal',args)
    await command(app,'canvas.show',{'kind':'text','content':'new selection'})
    with pytest.raises(AppError,match='changed'):
        await command(app,'canvas.views.reveal',args)
    assert spawn.await_count==1
