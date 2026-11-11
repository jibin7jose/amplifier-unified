"""A removed connection must be repairable without submitting saved work."""
import copy
import json
import sys
from types import SimpleNamespace

import pytest

from amplifier_web.host.model_selection import ProviderSelectionError
from amplifier_web.management import Management
from amplifier_web.provider_recovery import catalog
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.service import AppService


WANTED = {'instance': 'astra', 'model': 'gpt-6-astra', 'effort': 'xhigh'}
REPLACEMENT = {**WANTED, 'instance': 'openai'}


def saved_files(home, session):
    identity = session.get('runtimeSessionId') or session['id']
    control = home / 'sessions' / identity / 'control-state.json'
    control.parent.mkdir(parents=True, exist_ok=True)
    control.write_text(json.dumps({'selection': WANTED}))
    mounted = home / 'runtime-reports' / identity / 'mounted.json'
    mounted.parent.mkdir(parents=True, exist_ok=True)
    mounted.write_text(json.dumps({'session_id': identity, 'workspace': session['workspace'],
        'provider_choices': [{'id': 'openai', 'provider': 'openai', 'display_name': 'OpenAI',
                              'model': 'gpt-6-sol', 'api_key': 'never-public'}],
        'module_load_failures': []}))
    return control, mounted


def test_recovery_catalog_retains_choice_and_uses_runtime_identity(tmp_path):
    session = {'id': 'app-id', 'runtimeSessionId': 'native-id', 'workspace': str(tmp_path),
               'status': 'error', 'failure': {'category': 'worker_startup'}}
    control, mounted = saved_files(tmp_path, session)
    before = control.read_bytes()
    result = catalog(tmp_path, session)
    assert result['effective'] == result['selection'] == WANTED
    assert result['providers'][0]['id'] == 'openai'
    assert 'never-public' not in json.dumps(result)
    assert control.read_bytes() == before
    for patch in [{'status': 'running'}, {'failure': {'category': 'authentication'}},
                  {'workspace': '/another/project'}, {'runtimeSessionId': '../wrong'}]:
        assert catalog(tmp_path, {**session, **patch}) is None
    control.write_text(json.dumps({'selection': {'instance': 'openai', 'model': 'gpt-6-astra'}}))
    assert catalog(tmp_path, session) is None
    control.write_text(json.dumps({'selection': {'instance': 123, 'model': 'gpt-6-astra'}}))
    assert catalog(tmp_path, session) is None


@pytest.mark.asyncio
async def test_explicit_replacement_wins_over_saved_pin_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    provider = SimpleNamespace(get_info=lambda: {'id': 'openai', 'defaults': {'model': 'gpt-6-sol'}})
    loop = SimpleNamespace(root_provider=None)
    class Coordinator:
        config = {'providers': []}
        session_state = {}
        def get(self, name): return {'providers': {'openai': provider}, 'orchestrator': loop}.get(name)
        def get_capability(self, name): return None
        def register_capability(self, *args): pass
    controls = RuntimeControls(SimpleNamespace(session_id='chat', coordinator=Coordinator()),
                               SimpleNamespace(generation=None, queued_inputs=0))
    controls.state_path().parent.mkdir(parents=True)
    controls.state_path().write_text(json.dumps({'selection': WANTED}))
    try:
        with pytest.raises(ProviderSelectionError):
            await controls.restore()
        assert json.loads(controls.state_path().read_text())['selection'] == WANTED
        with pytest.raises(ProviderSelectionError):
            await controls.restore(selection_override={**REPLACEMENT, 'instance': 'also-deleted'})
        assert json.loads(controls.state_path().read_text())['selection'] == WANTED
        await controls.restore(selection_override=REPLACEMENT)
        assert controls.selection == REPLACEMENT
        assert loop.root_provider.original is provider
        assert json.loads(controls.state_path().read_text())['selection'] == REPLACEMENT
        await controls.restore()
        assert controls.selection == REPLACEMENT
    finally:
        await controls.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_model_control_repairs_startup_without_sending_saved_message(tmp_path, monkeypatch, legacy):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    calls = []
    class Runtime:
        async def start(self, session, emit):
            calls.append(('start', copy.deepcopy(session)))
            assert session['selection'] == REPLACEMENT
            assert session['replaceSavedSelection'] is True
        async def control(self, sid, operation, args):
            calls.append((operation, copy.deepcopy(args)))
            if operation == 'provider.select': return {'selection': REPLACEMENT}
            if operation == 'configuration.providers':
                return {'providers': [], 'selection': REPLACEMENT, 'effective': REPLACEMENT, 'pinned': True}
            raise AssertionError(operation)
        async def close(self): pass
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    app.management = Management(app)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        session.update(status='error', failure={'category': 'worker_startup'})
        app._message(session, 'user', 'Retained uncertain request', inputId='prior-attempt', delivery={'status': 'unknown'})
        saved_files(tmp_path, session)
        history = copy.deepcopy(session['messages'])
        # The shared user/agent read path must work even though start would fail.
        await app.management.perform('runtime.control', {'sessionId': session['id'], 'operation': 'configuration.providers', 'args': {}})
        assert not calls
        assert app.state['runtimeControl'][session['id']]['configuration.providers']['selection'] == WANTED
        selected = {**REPLACEMENT}
        if legacy: selected['provider'] = selected.pop('instance')
        await app.management.perform('runtime.control', {'sessionId': session['id'], 'operation': 'provider.select', 'args': selected})
        assert session['selection'] == REPLACEMENT
        assert 'replaceSavedSelection' not in session
        assert session['messages'] == history
        assert calls[0][0] == 'start'
        assert [row[0] for row in calls] == ['start', 'provider.select', 'configuration.providers']
    finally:
        await app.close()


@pytest.mark.parametrize('saved,override,expected', [
    ({'selection': REPLACEMENT}, False, REPLACEMENT),
    ({'selection': None}, False, None),
    ({'selection': WANTED}, False, WANTED),
    ({'selection': WANTED}, True, REPLACEMENT),
    ({}, False, REPLACEMENT),
])
async def test_worker_mount_uses_saved_exact_id_or_explicit_replacement(tmp_path, monkeypatch, saved, override, expected):
    from amplifier_web.runtime_worker import Worker
    from amplifier_foundation.session.activation import ActivationGate
    home = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setattr('amplifier_web.runtime_bootstrap.bootstrap_app_package', lambda: None)
    monkeypatch.setattr('amplifier_web.host.config.app_home', lambda: home)
    monkeypatch.setattr('amplifier_web.runtime_qualification.active_install_overrides', lambda *args: None)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending', lambda *args: None)
    runtime = SimpleNamespace(session_id='chat')
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.runtime', SimpleNamespace(Runtime=lambda **kwargs: runtime))
    control = home / 'sessions/chat/control-state.json'
    control.parent.mkdir(parents=True)
    control.write_text(json.dumps(saved))
    before = control.read_bytes()
    observed = []
    async def capture_mount(*args, **kwargs):
        observed.append(kwargs['selection'])
        raise ProviderSelectionError('Synthetic stop before module initialization')
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager', capture_mount)
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker = Worker()
    worker.shared_store, worker.shared_handle = SimpleNamespace(), SimpleNamespace()
    worker.activation_gate = ActivationGate()
    await worker.start({'id': 'chat', 'workspace': str(tmp_path), 'selection': REPLACEMENT,
                        'replaceSavedSelection': override}, recover_bundle=False)
    assert observed == [expected]
    assert control.read_bytes() == before
    assert worker.execution is None and worker.shutdown.is_set()
    assert not any(event.get('type') in {'runtime.ready', 'generation.started', 'input.delivered'} for event in events)


async def test_rejected_replacement_keeps_saved_chat_and_delivery_state(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    class Runtime:
        async def start(self, session, emit):
            raise ProviderSelectionError('Replacement was also removed')
        async def control(self, *args):
            raise AssertionError('Failed preparation must not reach runtime controls')
        async def close(self): pass
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    app.management = Management(app)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        session.update(status='error', selection=WANTED, failure={'category': 'provider_selection'})
        app._message(session, 'user', 'Uncertain prior input', delivery={'status': 'unknown'})
        control, _ = saved_files(tmp_path, session)
        before, messages = control.read_bytes(), copy.deepcopy(session['messages'])
        with pytest.raises(ProviderSelectionError):
            await app.management.perform('runtime.control', {'sessionId': session['id'],
                'operation': 'provider.select', 'args': REPLACEMENT})
        assert control.read_bytes() == before
        assert session['selection'] == WANTED and session['messages'] == messages
        assert not session.get('configurationBusy')
    finally:
        await app.close()
