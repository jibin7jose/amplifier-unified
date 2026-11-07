from copy import deepcopy
import json

import pytest

from amplifier_web.service import AppService, AppError


async def test_chat_canvas_roundtrip_restart_and_client_isolation(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'One'})
        one = app.state['selectedSessionId']
        app.clients.attach('browser')
        app.clients.attach('other')
        app.clients.save()
        other = deepcopy(app.clients.records['other'])
        with app.clients.bind('browser'):
            await app.dispatch('canvas.show', {'kind': 'text', 'title': 'First', 'content': 'unique saved body'})
            first = app.state['canvas']['id']
            await app.dispatch('canvas.show', {'kind': 'text', 'title': 'Second', 'content': 'second body'})
            await app.dispatch('canvas.select', {'id': first, 'version': 1})
            await app.dispatch('canvas.view', {'id': first, 'patch': {'zoom': 1.5, 'source': True}})
            await app.dispatch('view.update', {'patch': {'canvasWidth': 680, 'canvasControlsPinned': True, 'canvasDraft': {'filter': 'First', 'library': True}}})
            await app.dispatch('session.create', {'title': 'Two'})
            two = app.state['selectedSessionId']
            assert not app.state['canvas']['open']
            assert 'canvasWidth' not in app.state['view']
            assert 'canvasDraft' not in app.state['view']
            await app.dispatch('canvas.reopen', {})
            await app.dispatch('view.update', {'patch': {'canvasWidth': 420}})
            await app.dispatch('canvas.close', {})
            await app.dispatch('session.select', {'id': one})
            assert app.state['canvas']['open']
            assert app.state['canvas']['id'] == first
            assert app.state['canvas']['selectedVersion'] == 1
            assert app.state['canvas']['view']['zoom'] == 1.5
            assert app.state['view']['canvasWidth'] == 680
            assert app.state['view']['canvasDraft']['filter'] == 'First'
            assert app.state['view']['canvasControlsPinned']
            await app.dispatch('session.select', {'id': two})
            assert not app.state['canvas']['open']
            assert app.state['view']['canvasWidth'] == 420
            with pytest.raises(AppError, match='chat changed'):
                await app.dispatch('view.update', {'sessionId': one, 'patch': {'canvasWidth': 1000}})
            stored = json.loads(app.db.execute("SELECT value FROM client_views WHERE id='browser'").fetchone()[0])
            assert 'unique saved body' not in json.dumps(stored['chatCanvas'])
            assert 'chatCanvas' not in app.browser_state()
        assert app.clients.records['other'] == other
        await app.close()
        app = AppService(tmp_path / 'app', workspace=tmp_path)
        app.clients.attach('resumed', resume='browser')
        with app.clients.bind('resumed'):
            assert not app.state['canvas']['open']
            await app.dispatch('session.select', {'id': one})
            assert app.state['canvas']['id'] == first
            assert app.state['canvas']['open']
            assert app.state['view']['canvasWidth'] == 680
            assert app.state['canvas']['view']['zoom'] == 1.5
            await app.dispatch('session.draft', {})
            assert not app.state['canvas']['open']
            assert 'canvasWidth' not in app.state['view']
    finally:
        await app.close()


async def test_legacy_selected_canvas_is_saved_before_first_navigation(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        one = app.state['selectedSessionId']
        await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Legacy'})
        app.clients.attach('browser')
        with app.clients.bind('browser'):
            app.clients.record().pop('chatCanvas', None)
            await app.dispatch('session.create', {})
            assert not app.state['canvas']['open']
            await app.dispatch('session.select', {'id': one})
            assert app.state['canvas']['open']
            assert app.state['canvas']['content'] == 'Legacy'
    finally:
        await app.close()


async def test_context_inspector_native_tab_reuse_scope_and_no_prompt_storage(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'Inspected'})
        sid = app.state['selectedSessionId']
        app.clients.attach('browser')
        with app.clients.bind('browser'):
            await app.dispatch('canvas.show', {'kind':'text','content':'Keep this document'})
            document = app.state['canvas']['id']
            await app.dispatch('view.update', {'patch':{'panel':'session-details'}})
            await app.dispatch('canvas.show', {'kind':'context-inspector','sessionId':sid})
            inspector = app.state['canvas']['id']
            assert app.state['view']['panel'] is None
            assert app.state['canvas']['sessionId'] == sid
            assert app.state['canvas']['title'] == 'Context inspector'
            assert not any(k in app.state['canvas'] for k in ('content','path','url','surface'))
            await app.dispatch('canvas.show', {'kind':'context-inspector','sessionId':sid})
            assert app.state['canvas']['id'] == inspector
            assert len([r for r in app.state['canvasArtifacts'] if r['kind']=='context-inspector']) == 1
            with pytest.raises(AppError, match='live app view'):
                await app.dispatch('canvas.show', {'kind':'context-inspector','content':'not allowed'})
            with pytest.raises(AppError, match='opens in Canvas'):
                await app.dispatch('canvas.show', {'kind':'context-inspector','presentation':'inline'})
            await app.dispatch('canvas.tabClose', {'id':inspector})
            await app.dispatch('canvas.select', {'id':document})
            assert app.state['canvas']['content'] == 'Keep this document'
            assert app.state['selectedSessionId'] == sid
    finally:
        await app.close()


@pytest.mark.parametrize('resume', [False, True])
async def test_attached_client_retires_only_legacy_inspector_modal(tmp_path, resume):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        app.clients.attach('browser')
        app.clients.attach('other')
        with app.clients.bind('browser'):
            await app.dispatch('canvas.show', {'kind':'text','content':'Keep selected document'})
            document = app.state['canvas']['id']
            await app.dispatch('view.update', {'patch':{'panel':'context-inspector','canvasWidth':710}})
        other = deepcopy(app.clients.records['other'])
        restored = app.clients.attach('resumed', resume='browser') if resume else app.clients.attach('browser')
        assert restored['view']['panel'] is None
        assert restored['view']['canvasWidth'] == 710
        assert restored['selectedSessionId'] == sid
        assert restored['canvas']['id'] == document
        assert not any(r['kind']=='context-inspector' for r in app.state['canvasArtifacts'])
        assert app.clients.records['other'] == other
        restored['view']['panel'] = 'session-details'
        app.clients.attach('resumed' if resume else 'browser')
        assert restored['view']['panel'] == 'session-details'
    finally:
        await app.close()
