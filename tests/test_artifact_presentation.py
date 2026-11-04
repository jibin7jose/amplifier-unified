"""One saved definition, client-local presentation, no implicit duplicate view."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppError
from test_canvas_views import app, command, show, target, view
from test_inline_artifacts import published


def choices(app, client='one'):
    with app.clients.bind(client):
        return deepcopy(app.canvas_views.project().get('presentations', []))


async def test_show_inline_preserves_unrelated_dirty_canvas_and_draft(app):
    original = await published(app, '<input value="unfinished">', 'html')
    await command(app, 'canvas.views.dirty', {**target(original), 'dirty': True})
    with app.clients.bind('one'):
        app.clients.draft(app.state['selectedSessionId'], 'Keep the parent draft')
        before = deepcopy(app.state['canvas'])
    saved = await command(app, 'canvas.show', {'kind': 'markdown', 'title': 'Short review',
                                             'content': '# Review', 'presentation': 'inline'})
    identity = saved['result']['id']
    with app.clients.bind('one'):
        assert app.state['canvas'] == before
        assert app.state['view']['draft'] == 'Keep the parent draft'
    assert any(c['id'] == identity and c['version'] == 1 and c['presentation'] == 'inline' for c in choices(app))
    assert choices(app, 'two') == []


async def test_exact_inline_selection_hide_and_reload_do_not_create_versions(app):
    first = await published(app, '# Version one', 'markdown')
    await command(app, 'canvas.versions.revise', {'id': first['resourceId'], 'expectedRevision': 1, 'content': '# Version two'})
    with app.clients.bind('one'):
        original = deepcopy(app.state['canvasArtifacts'])
    args = {'id': first['resourceId'], 'version': 1, 'messageId': 'inline-message'}
    await command(app, 'canvas.select', {**args, 'presentation': 'inline'})
    assert any(c['id'] == first['resourceId'] and c['version'] == 1 and c['presentation'] == 'inline' for c in choices(app))
    await command(app, 'canvas.select', {**args, 'presentation': 'saved'})
    app.clients.attach('reload', resume='one')
    assert any(c['id'] == first['resourceId'] and c['presentation'] == 'saved' for c in choices(app, 'reload'))
    assert app.state['canvasArtifacts'] == original


@pytest.mark.parametrize('kind', ['html', 'babylon'])
@pytest.mark.parametrize('presentation', ['inline', 'saved'])
async def test_new_page_preserves_disposed_primary_and_exact_presentation(app, kind, presentation):
    first = await published(app, '<input value="saved one">', kind)
    identity = first['resourceId']
    args = {'id': identity, 'version': 1, 'messageId': 'inline-message'}
    await command(app, 'canvas.select', args)
    await command(app, 'canvas.versions.revise', {
        'id': identity, 'expectedRevision': 1, 'content': '<input value="saved two">'})
    await command(app, 'canvas.select', {**args, 'presentation': 'inline', 'resetPreview': True})
    lease_args = {'resourceId': identity, 'version': 1, 'messageId': 'inline-message',
                  'previewId': 'old-page', 'presentationRequest': True}
    old = (await command(app, 'canvas.views.inline', lease_args))['result']
    await command(app, 'canvas.views.dirty', {**target(old), 'dirty': True})
    await command(app, 'view.update', {'patch': {'draft': 'Keep the unsent draft'}})

    # A transport reconnect is still the same page: retain its dirty lease.
    app.clients.attach('one')
    assert view(app, old['viewId'])['dirty']
    assert app.clients.records['one']['canvas']['_presentationDisposed']
    if presentation == 'saved':
        await command(app, 'canvas.views.dirty', {**target(old), 'dirty': False})
        await command(app, 'canvas.select', {**args, 'presentation': 'saved', 'resetPreview': True})
    before_choices = choices(app)
    before_canvas = deepcopy(app.clients.records['one']['canvas'])
    before_artifacts = deepcopy(app.state['canvasArtifacts'])

    # A browser reload resumes the private choice but must allocate fresh leases.
    app.clients.attach('reload', resume='one')
    reloaded = app.clients.records['reload']
    assert choices(app, 'reload') == before_choices
    assert reloaded['canvas']['_presentationDisposed']
    assert not reloaded['canvas']['open']
    assert reloaded['canvas']['selectedVersion'] == reloaded['canvas']['runningVersion'] == 1
    assert reloaded['canvas']['latestVersion'] == 2
    assert reloaded['view']['draft'] == 'Keep the unsent draft'
    assert not reloaded['canvasViews'].get('inline')
    assert old['viewId'] + ':' + identity not in reloaded['canvasViews']['preferences']
    assert app.clients.records['one']['canvas'] == before_canvas
    assert choices(app, 'two') == []

    if presentation == 'inline':
        opened = (await command(app, 'canvas.views.inline',
            {**lease_args, 'previewId': 'new-page'}, client='reload'))['result']
        assert opened['viewId'] != old['viewId']
        assert not opened['dirty']
        assert view(app, old['viewId'])['dirty']  # The old client was not reset.
        with app.clients.bind('reload'):
            assert app.canvas_views.canvas(opened['viewId'])['content'] == '<input value="saved one">'
    else:
        with pytest.raises(AppError, match='presentation changed'):
            await command(app, 'canvas.views.inline',
                {**lease_args, 'previewId': 'new-page'}, client='reload')
    assert app.state['canvasArtifacts'] == before_artifacts


async def test_stateful_transition_refusal_retains_frame_and_explicit_ui_reset(app):
    first = await published(app, '<input value="saved">', 'html')
    await command(app, 'canvas.views.status', {**target(first), 'status': 'ready', 'message': 'Synthetic mounted frame'})
    args = {'id': first['resourceId'], 'version': 1, 'messageId': 'inline-message', 'presentation': 'inline'}
    with pytest.raises(AppError) as refused:
        await command(app, 'canvas.select', args)
    assert refused.value.code == 'canvas_preview_reset_required'
    assert view(app)['generation'] == first['generation']
    with pytest.raises(AppError, match='explicit user'):
        await command(app, 'canvas.select', {**args, 'resetPreview': True}, origin='agent')
    await command(app, 'canvas.select', {**args, 'resetPreview': True}, origin='ui')
    opened = (await command(app, 'canvas.views.inline', {'resourceId': first['resourceId'],
        'version': 1, 'messageId': 'inline-message', 'previewId': 'stateful', 'presentationRequest': True}))['result']
    with pytest.raises(AppError) as refused:
        await command(app, 'canvas.select', {**args, 'presentation': 'canvas'})
    assert refused.value.code == 'canvas_preview_reset_required'
    assert view(app, opened['viewId'])['resourceId'] == first['resourceId']
    await command(app, 'canvas.select', {**args, 'presentation': 'canvas', 'resetPreview': True}, origin='ui')
    with pytest.raises(AppError, match='Unknown'):
        view(app, opened['viewId'])


async def test_late_inline_mount_does_not_recreate_released_presentation(app):
    first = await published(app)
    args = {'id': first['resourceId'], 'version': 1, 'messageId': 'inline-message'}
    await command(app, 'canvas.select', {**args, 'presentation': 'inline'})
    await command(app, 'canvas.select', {**args, 'presentation': 'canvas'})
    with pytest.raises(AppError, match='presentation changed'):
        await command(app, 'canvas.views.inline', {'resourceId': first['resourceId'], 'version': 1,
             'messageId': 'inline-message', 'previewId': 'late', 'presentationRequest': True})
    with app.clients.bind('one'):
        assert not app.canvas_views.record().get('inline')
    with pytest.raises(AppError, match='published'):
        await command(app, 'canvas.select', {**args, 'messageId': 'wrong', 'presentation': 'inline'})


async def test_canvas_transition_refuses_dirty_inline_before_changing_selection(app):
    first = await published(app, 'Saved body', 'text')
    await command(app, 'canvas.select', {'id': first['resourceId'], 'version': 1,
                                       'messageId': 'inline-message', 'presentation': 'inline'})
    opened = (await command(app, 'canvas.views.inline', {'resourceId': first['resourceId'],
              'version': 1, 'messageId': 'inline-message', 'previewId': 'mounted'}))['result']
    await command(app, 'canvas.views.dirty', {**target(opened), 'dirty': True})
    with app.clients.bind('one'):
        before = deepcopy(app.state['canvas'])
    for presentation in ('canvas', 'saved'):
        with pytest.raises(AppError, match='edit'):
            await command(app, 'canvas.select', {'id': first['resourceId'], 'version': 1,
                          'messageId': 'inline-message', 'presentation': presentation})
    with app.clients.bind('one'):
        assert app.state['canvas'] == before
        assert app.canvas_views.canvas(opened['viewId'])['content'] == 'Saved body'


async def test_default_show_and_explicit_saved_are_distinct(app):
    first = await published(app, '<p>Canvas default</p>', 'html')
    with app.clients.bind('one'):
        before = deepcopy(app.state['canvas'])
    saved = await command(app, 'canvas.show', {'kind': 'code', 'content': 'pass', 'presentation': 'saved'})
    with app.clients.bind('one'):
        assert app.state['canvas'] == before
        assert app.state['canvas']['open']
    assert saved['result']['id'] != first['resourceId']
    assert any(c['id'] == saved['result']['id'] and c['presentation'] == 'saved' for c in choices(app))


async def test_unsupported_inline_kind_is_rejected_without_partial_publication(app):
    await published(app)
    original = deepcopy(app.state['canvasArtifacts'])
    with pytest.raises(AppError, match='Canvas'):
        await command(app, 'canvas.show', {'kind': 'browser', 'url': 'https://example.test', 'presentation': 'inline'})
    assert app.state['canvasArtifacts'] == original


async def test_canonical_file_presentation_refusal_does_not_publish_again(app, tmp_path):
    await published(app)
    path = tmp_path / 'example.html'
    path.write_text('<input value="retained">')
    saved = await command(app, 'canvas.show', {'kind': 'auto', 'path': str(path), 'presentation': 'inline'})
    identity = saved['result']['id']
    await command(app, 'canvas.views.inline', {'resourceId': identity, 'version': 1,
        'messageId': 'inline-message', 'previewId': 'canonical', 'presentationRequest': True})
    with app.clients.bind('one'):
        app._session(app.state['selectedSessionId'])['messages'].append(
            {'id': 'second-message', 'role': 'user', 'text': 'Keep the saved example'})
        before = deepcopy(app.state['canvasArtifacts'])
        tabs = deepcopy(app.state['canvasTabs'])
    for action, args in (
        ('canvas.show', {'kind': 'auto', 'path': str(path)}),
        ('canvas.openFile', {'sessionId': saved['state']['selectedSessionId'],
                             'workspace': str(tmp_path), 'path': str(path)}),
    ):
        with pytest.raises(AppError) as refused:
            await command(app, action, args)
        assert refused.value.code == 'canvas_preview_reset_required'
        assert app.state['canvasArtifacts'] == before
        with app.clients.bind('one'):
            assert app.state['canvasTabs'] == tabs


async def test_chat_return_preserves_inline_choice_and_disposed_primary(app):
    first = await published(app)
    with app.clients.bind('one'):
        sid = app.state['selectedSessionId']
    await command(app, 'canvas.select', {'id': first['resourceId'], 'version': 1,
        'messageId': 'inline-message', 'presentation': 'inline'})
    await command(app, 'session.create')
    await command(app, 'session.select', {'id': sid})
    with app.clients.bind('one'):
        assert app.state['canvas']['_presentationDisposed']
        assert not app.state['canvas']['open']
    assert any(c['presentation'] == 'inline' for c in choices(app))


async def test_primary_html_without_mount_telemetry_still_requires_explicit_reset(app):
    first = await published(app, '<input value="not yet reported">', 'html')
    with app.clients.bind('one'):
        before = deepcopy(app.clients.record())
    with pytest.raises(AppError) as refused:
        await command(app, 'canvas.select', {'id': first['resourceId'], 'version': 1,
            'messageId': 'inline-message', 'presentation': 'inline'}, origin='agent')
    assert refused.value.code == 'canvas_preview_reset_required'
    with app.clients.bind('one'):
        assert app.clients.record()['canvas'] == before['canvas']
        assert app.canvas_views.presentation(first['resourceId'], 1)['presentation'] == 'canvas'


async def test_warm_visibility_reopen_clears_disposed_snapshot_without_another_action(app):
    first = await published(app)
    await command(app, 'canvas.select', {'id': first['resourceId'], 'version': 1,
        'messageId': 'inline-message', 'presentation': 'inline'})
    with app.clients.bind('one'):
        cached = app.browser_state()
        assert cached['canvas']['_presentationDisposed']
        sid = app.state['selectedSessionId']
    result = await command(app, 'canvas.visibility', {'sessionId': sid,
        'canvasId': first['resourceId'], 'open': True})
    assert result['state']['canvas']['open']
    assert not result['state']['canvas'].get('_presentationDisposed')
    assert next(c for c in result['state']['canvasWorkspace']['presentations']
                if c['id'] == first['resourceId'])['presentation'] == 'canvas'
