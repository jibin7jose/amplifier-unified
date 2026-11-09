"""Advisory saved/rendered feedback through the real control and agent paths."""
from copy import deepcopy
import json

import pytest

from amplifier_web.canvas_feedback import summary
from amplifier_web.service import AppError, AppService


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'data', workspace=tmp_path)
    await service.dispatch('session.create', {})
    service._message(service._session(), 'user', 'Synthetic artifact')
    service.clients.attach('one')
    service.clients.attach('two')
    yield service
    await service.close()


async def call(app, action, args=None, client='one'):
    with app.clients.bind(client):
        return (await app.dispatch(action, args or {}, include_state=False)).get('result')


def view(app, client='one'):
    with app.clients.bind(client):
        return deepcopy(app.canvas_views.summary('primary'))


def target(current):
    return {key: current[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}


async def report(app, current, status, *, part='preview', message='', client='one', **detail):
    return await call(app, 'canvas.views.command', {
        **target(current), 'action': 'canvas.report',
        'args': {'part': part, 'status': status, 'message': message, **detail}}, client)


async def inspect(app, saved, version=1, client='one'):
    return await call(app, 'canvas.versions.inspect', {'id': saved['id'], 'version': version}, client)


async def test_save_pending_error_correction_preserve_identity_and_history(app):
    saved = await call(app, 'canvas.show', {'kind': 'markdown', 'content': '# Plan\n\n```mermaid\nbroken\n```'})
    assert saved['saved'] and saved['quality']['status'] == 'not_checked'
    assert saved['readback']['args']['version'] == 1
    current = view(app)
    # Mount success and outer Markdown ready alone cannot qualify its diagrams.
    await call(app, 'canvas.views.status', {**target(current), 'status': 'ready', 'message': 'Renderer mounted'})
    await report(app, current, 'ready')
    assert (await inspect(app, saved))['quality']['status'] == 'not_checked'
    await report(app, current, 'ready', parts=['fence-8'], partsComplete=True)
    assert (await inspect(app, saved))['quality']['status'] == 'pending'
    await report(app, current, 'pending', part='fence-8', sourceLine=3)
    await report(app, current, 'error', part='fence-8', sourceLine=3, message='Parse error on line 2')
    inspected = await inspect(app, saved)
    assert inspected['saved'] and inspected['reference'] == saved['reference']
    assert inspected['quality']['status'] == 'issues'
    assert inspected['quality']['diagnostics'][0]['sourceLine'] == 3
    corrected = await call(app, 'canvas.versions.revise', {
        'id': saved['id'], 'expectedRevision': 1, 'content': '# Corrected'})
    assert corrected['id'] == saved['id'] and corrected['revision'] == 2
    assert corrected['quality']['status'] == 'not_checked'
    with pytest.raises(AppError, match='changed'):
        await report(app, current, 'error', message='Late old failure')
    assert (await inspect(app, saved, 2))['quality']['diagnostics'] == []
    await report(app, view(app), 'ready', parts=[], partsComplete=True)
    checked = await inspect(app, saved, 2)
    assert checked['quality']['status'] == 'rendered'
    assert checked['quality']['sourceValidation'] == 'not_checked'
    # Reports do not rewrite version history, nor qualify an unobserved old version.
    assert len(checked['versions']) == 2
    assert (await inspect(app, saved, 1))['quality']['status'] == 'not_checked'


async def test_reports_do_not_bleed_between_artifacts_clients_or_renderer_generations(app):
    first = await call(app, 'canvas.show', {'kind': 'text', 'content': 'First'})
    initial = view(app)
    await report(app, initial, 'error', message='First failed')
    second = await call(app, 'canvas.show', {'kind': 'text', 'content': 'Second'})
    assert second['quality']['status'] == 'not_checked'
    with pytest.raises(AppError, match='changed'):
        await report(app, initial, 'ready')
    await call(app, 'canvas.select', {'id': first['id']}, 'two')
    assert (await inspect(app, first, client='two'))['quality']['status'] == 'not_checked'
    await report(app, view(app, 'two'), 'ready', client='two')
    assert (await inspect(app, first, client='two'))['quality']['status'] == 'rendered'
    assert (await inspect(app, first))['quality']['status'] == 'not_checked'
    await call(app, 'canvas.select', {'id': first['id']})
    old = view(app)
    await report(app, old, 'error', message='Previous renderer')
    await call(app, 'canvas.views.recover', target(old))
    assert (await inspect(app, first))['quality']['status'] == 'not_checked'
    with pytest.raises(AppError, match='changed'):
        await report(app, old, 'ready')


async def test_retained_html_report_does_not_qualify_new_saved_version(app):
    saved = await call(app, 'canvas.show', {'kind': 'html', 'content': '<h1>One</h1>'})
    running = view(app)
    await report(app, running, 'ready', message='HTML document loaded')
    changed = await call(app, 'canvas.versions.revise', {
        'id': saved['id'], 'expectedRevision': 1, 'content': '<h1>Two</h1>'})
    assert changed['quality']['status'] == 'not_checked'
    assert (await inspect(app, saved, 1))['quality']['status'] == 'rendered'
    assert (await inspect(app, saved, 2))['quality']['status'] == 'not_checked'
    assert view(app)['runningVersion'] == 1


async def test_diagnostics_are_bounded_redacted_and_not_clipboard_quality(app, monkeypatch):
    monkeypatch.setenv('SYNTHETIC_API_KEY', 'synthetic-secret-value-123')
    saved = await call(app, 'canvas.show', {'kind': 'markdown', 'content': '# Diagnostic'})
    current = view(app)
    parts = [f'fence-{i}' for i in range(12)]
    await report(app, current, 'ready', parts=parts, partsComplete=True)
    for part in parts:
        await report(app, current, 'error', part=part,
                     message='synthetic-secret-value-123 Bearer synthetic-token <script>untrusted()</script> ' + 'x' * 1800)
    quality = (await inspect(app, saved))['quality']
    assert len(quality['diagnostics']) == 10 and quality['truncated']
    assert all(len(issue['message']) <= 600 and issue['untrustedData'] for issue in quality['diagnostics'])
    assert 'synthetic-secret-value-123' not in json.dumps(quality)
    assert 'synthetic-token' not in json.dumps(quality)
    assert '<script>' in quality['diagnostics'][0]['message']  # Data; UI must escape it.
    clean = await call(app, 'canvas.show', {'kind': 'text', 'content': 'Clean'})
    await report(app, view(app), 'ready')
    await report(app, view(app), 'error', part='clipboard', message='Clipboard denied')
    assert (await inspect(app, clean))['quality']['status'] == 'rendered'


async def test_unfenced_report_and_detached_agent_never_claim_rendering(app):
    saved = await call(app, 'canvas.show', {'kind': 'text', 'content': 'Original'})
    await call(app, 'canvas.report', {'id': saved['id'], 'part': 'preview', 'status': 'ready'})
    assert (await inspect(app, saved))['quality']['status'] == 'not_checked'
    sid = app._session()['id']
    result = await app.app_bridge('dispatch', {'action': 'canvas.versions.inspect',
                                  'args': {'id': saved['id'], 'version': 1}}, sid)
    assert result['result']['saved']
    assert result['result']['quality']['status'] == 'not_checked'
    with pytest.raises(AppError):
        await call(app, 'canvas.versions.revise', {'id': saved['id'], 'expectedRevision': 99, 'content': 'Stale'})
    assert (await inspect(app, saved))['revision'] == 1


def test_unknown_or_incomplete_inventory_and_unknown_status_do_not_pass():
    binding = {'resourceId': 'example'}
    def reports(status, **detail):
        return {'preview': {'binding': binding, 'status': status, **detail}}
    assert summary('markdown', reports('ready'), binding=binding)['status'] == 'not_checked'
    assert summary('markdown', reports('ready', parts=[], partsComplete=False), binding=binding)['status'] == 'not_checked'
    assert summary('text', reports('unverified'), binding=binding)['status'] == 'not_checked'
    assert summary('text', reports('unexpected'), binding=binding)['status'] == 'not_checked'
    assert summary('text', reports('ready'), binding={'resourceId': 'different'})['status'] == 'not_checked'
    assert summary('browser', reports('ready'), binding=binding)['status'] == 'not_checked'


@pytest.mark.parametrize('status', ['ready', 'error'])
async def test_reselect_and_unchanged_file_reuse_only_same_mount_feedback(app, tmp_path, status):
    path = tmp_path / 'document.md'
    path.write_text('# Same document')
    saved = await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)})
    current = view(app)
    await report(app, current, status, parts=[], partsComplete=True, message='Observed')
    expected = 'rendered' if status == 'ready' else 'issues'
    await call(app, 'canvas.select', {'id': saved['id']})
    assert (await inspect(app, saved))['quality']['status'] == expected
    assert target(view(app)) == target(current)
    reopened = await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)})
    assert reopened['id'] == saved['id'] and reopened['revision'] == 1
    assert reopened['quality']['status'] == expected
    path.write_text('# Different')
    changed = await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)})
    assert changed['revision'] == 2 and changed['quality']['status'] == 'not_checked'


async def test_returned_agent_readback_preserves_explicit_client(app):
    sid = app._session()['id']
    saved = (await app.app_bridge('dispatch', {
        'action': 'canvas.show',
        'args': {'kind': 'text', 'content': 'Explicit client', 'clientId': 'one'}}, sid))['result']
    await report(app, view(app), 'ready')
    assert saved['readback']['args']['clientId'] == 'one'
    checked = await app.app_bridge('dispatch', saved['readback'], sid)
    assert checked['result']['quality']['status'] == 'rendered'
    assert app.clients.records['two']['canvas'].get('id') != saved['id']


async def test_missing_stored_source_remains_actionable_and_cannot_be_rendered(app):
    saved = await call(app, 'canvas.show', {'kind': 'markdown', 'content': '# Retained'})
    original = next(row for row in app.state['canvasArtifacts'] if row['id'] == saved['id'])
    reference = deepcopy(original['body'])
    await report(app, view(app), 'ready', parts=[], partsComplete=True)
    app.db.execute('DELETE FROM state_resources WHERE id=?', (reference['$resource'],))
    # A live body's normal pre-selection save would repair the removed record.
    # Model reopening after source loss, not deleting an in-memory recoverable copy.
    with app.clients.bind('one'):
        app.state['canvas'] = {'id': 'old-client', 'open': False}
    await call(app, 'canvas.select', {'id': saved['id']})
    checked = await inspect(app, saved)
    assert checked['saved'] and checked['quality']['status'] == 'issues'
    assert checked['quality']['basis'] == 'source_unavailable'
    assert 'source is unavailable' in checked['quality']['diagnostics'][0]['message']
    assert original['body'] == reference


async def test_inline_report_stays_in_its_exact_version_and_client(app):
    saved = await call(app, 'canvas.show', {'kind': 'text', 'content': 'Inline', 'presentation': 'inline'})
    row = next(row for row in app.state['canvasArtifacts'] if row['id'] == saved['id'])
    inline = await call(app, 'canvas.views.inline', {
        'resourceId': saved['id'], 'version': 1,
        'messageId': row['publications'][0]['messageId'], 'previewId': 'test-preview',
        'presentationRequest': True})
    await report(app, inline, 'ready')
    assert (await inspect(app, saved))['quality']['status'] == 'rendered'
    assert (await inspect(app, saved, client='two'))['quality']['status'] == 'not_checked'
    changed = await call(app, 'canvas.versions.revise', {
        'id': saved['id'], 'expectedRevision': 1, 'content': 'Next'})
    assert changed['quality']['status'] == 'not_checked'
    assert (await inspect(app, saved, 1))['quality']['status'] == 'rendered'


async def test_resumed_client_cannot_inherit_another_clients_render_proof(app):
    saved = await call(app, 'canvas.show', {'kind': 'markdown', 'content': '# Resumed'})
    await report(app, view(app), 'ready', parts=[], partsComplete=True)
    original = (await inspect(app, saved))['quality']
    assert original['status'] == 'rendered' and original['view']['clientId'] == 'one'
    app.clients.attach('clone', 'one')
    cloned = await inspect(app, saved, client='clone')
    assert cloned['quality']['status'] == 'not_checked'
    await report(app, view(app, 'clone'), 'ready', client='clone', parts=[], partsComplete=True)
    assert (await inspect(app, saved, client='clone'))['quality']['status'] == 'rendered'
    assert (await inspect(app, saved))['quality'] == original


def test_stored_tool_state_failure_is_distinct_from_browser_evidence():
    result = summary('mcp-app', tool_state_error={
        'status': 'error', 'message': 'The saved tool state is unavailable. No tool work was replayed.'})
    assert result['status'] == 'issues'
    assert result['basis'] == 'tool_state_unavailable'
    assert result['diagnostics'][0]['part'] == 'stored-tool-state'
    assert summary('text', tool_state_error={'status': 'ready'})['status'] == 'not_checked'