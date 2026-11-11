"""One root scheduler, including actual mounted community hooks when available."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from amplifier_core import HookRegistry, HookResult
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_web.host.naming import LiveSessionNaming, claim_root_lifecycle
from amplifier_web.host.prompt_events import install
from amplifier_web.host.storage import SessionStore


def coordinator(hooks, rows):
    return SimpleNamespace(hooks=hooks, config={'hooks': rows})


def row(instance=None):
    return {'module': 'hooks-session-naming', 'config': {'model_role': None},
            **({'instance_id': instance} if instance else {})}


def test_legacy_handoff_removes_known_scheduler_but_keeps_other_hooks_and_drain():
    hooks = HookRegistry()
    async def handler(event, data): return HookResult()
    hooks.register('prompt:complete', handler, name='session-naming')
    hooks.register('session:end', handler, name='session-naming-drain')
    hooks.register('prompt:complete', handler, name='other-completion')
    claim_root_lifecycle(coordinator(hooks, [row()]))
    assert hooks.list_handlers() == {'prompt:complete': ['other-completion'],
        'session:end': ['session-naming-drain']}


@pytest.mark.parametrize('unknown', ['missing-drain', 'different-event', 'different-name', 'too-many'])
def test_unknown_legacy_lifecycle_is_not_modified(unknown):
    hooks = HookRegistry()
    async def handler(event, data): return HookResult()
    hooks.register('other:complete' if unknown == 'different-event' else 'prompt:complete',
        handler, name='custom-naming' if unknown == 'different-name' else 'session-naming')
    if unknown != 'missing-drain':
        hooks.register('session:end', handler, name='session-naming-drain')
    if unknown == 'too-many': hooks.register('prompt:complete', handler, name='session-naming')
    before = hooks.list_handlers()
    with pytest.raises(ValueError, match='recognized lifecycle'):
        claim_root_lifecycle(coordinator(hooks, [row()]))
    assert hooks.list_handlers() == before


@pytest.fixture
def community():
    return pytest.importorskip('amplifier_module_hooks_session_naming',
        reason='Actual community module supplied by the runtime qualification environment')


@pytest.fixture
def native_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / '.amplifier'))
    return tmp_path


async def mounted(community, home, identity, *, count=1, automatic=True, manual=False):
    calls = []
    started, release = asyncio.Event(), asyncio.Event()
    class Provider:
        name = 'fixture'
        response_name = 'Generated project'
        priority = 1
        def get_info(self): return SimpleNamespace(id='fixture', defaults={'model': 'offline'})
        async def complete(self, request, **kwargs):
            calls.append(request)
            started.set()
            await release.wait()
            return ChatResponse(content=[TextBlock(text=json.dumps({'action': 'set',
                'name': self.response_name, 'description': 'Orbit work'}))],
                usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
    class Context:
        messages = [{'role': 'user', 'content': 'Build orbits'},
            {'role': 'assistant', 'content': 'Added camera controls'}]
        async def get_messages(self): return self.messages
    context, provider, hooks, capabilities = Context(), Provider(), HookRegistry(), {}
    co = coordinator(hooks, [row(str(n)) for n in range(count)])
    co.config['project_dir'] = str(home)
    co.session_id = identity
    co.mount_points = {'context': context}
    co.get = lambda key: {'providers': {'fixture': provider}, 'context': context}.get(key)
    co.get_capability = capabilities.get
    co.register_capability = capabilities.__setitem__
    co.register_cleanup = lambda callback: None
    store = SessionStore.for_app(home / 'app', home)
    store.save(identity, await context.get_messages(), {'turn_count': 1,
        'name': 'My title' if manual else 'Hi!', 'name_source': 'manual' if manual else 'fallback'})
    metadata = SessionMetadataStore(store.directory(identity))
    metadata.update({'name_auto': automatic, 'name_policy_revision': 1,
        'name_auto_revision': metadata.read().get('name_revision', 0)})
    for declaration in co.config['hooks']: await community.mount(co, declaration['config'])
    install(co)
    return SimpleNamespace(co=co, calls=calls, started=started, release=release, metadata=metadata, context=context, provider=provider)


async def prompt(host):
    await host.co.hooks.emit('prompt:submit', {'session_id': host.co.session_id, 'prompt': 'Add planets'})
    await host.co.hooks.emit('orchestrator:complete', {'session_id': host.co.session_id, 'status': 'complete'})


@pytest.mark.parametrize('instances', [0, 1, 2])
@pytest.mark.parametrize('automatic,manual', [(True, False), (False, False), (False, True)])
async def test_actual_mounted_parent_has_one_or_zero_calls(native_home, community, instances, automatic, manual):
    host = await mounted(community, native_home, 'root', count=instances, automatic=automatic, manual=manual)
    if instances > 1 and not host.co.get_capability('session.naming.lifecycle'):
        # Old Core cannot reliably unregister duplicate names. Normal host
        # singleton composition removes such duplication before mounting; a
        # bypassed/unknown plan must not start an additional app scheduler.
        with pytest.raises(ValueError, match='recognized lifecycle'):
            LiveSessionNaming(host.co, native_home / 'app', lambda event: None, ['old'])
        assert len(host.co.hooks.list_handlers()['prompt:complete']) == instances + 1
        return
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda event: None, ['old'])
    assert namer.hook is not None, namer.unavailable
    namer.observe({'type': 'input.delivered', 'input_id': 'new', 'source': 'user'})
    await prompt(host)
    namer.observe({'type': 'generation.finished', 'input_ids': ['new']})
    if automatic: await asyncio.wait_for(host.started.wait(), 1)
    await asyncio.sleep(.02)  # Expose any duplicate background scheduler.
    assert len(host.calls) == int(automatic)
    host.release.set()
    if namer.pending: await namer.pending
    await host.co.hooks.emit('session:end', {'session_id': 'root'})
    assert host.metadata.read()['name'] == ('Generated project' if automatic else 'My title' if manual else 'Hi!')
    assert host.metadata.read()['name_auto'] is automatic
    await namer.close()


@pytest.mark.parametrize('duplicate', [False, True])
async def test_worker_rejects_uncontrolled_naming_before_execute(tmp_path, monkeypatch, duplicate):
    import sys
    from unittest.mock import AsyncMock, Mock
    from amplifier_web.runtime_worker import Worker
    from amplifier_foundation.session.activation import ActivationGate
    hooks = HookRegistry()
    async def scheduler(event, data): raise AssertionError('Uncontrolled model call')
    count = 2 if duplicate else 1
    for _ in range(count):
        hooks.register('prompt:complete', scheduler, name='session-naming' if duplicate else 'unknown-naming')
        hooks.register('session:end', scheduler, name='session-naming-drain')
    co = coordinator(hooks, [row(str(i)) for i in range(count)])
    caps = {}
    co.session_id = 'blocked'
    co.get_capability = caps.get
    co.register_capability = caps.__setitem__
    co.get = lambda key: None
    session = SimpleNamespace(coordinator=co, execute=AsyncMock())
    runtime = SimpleNamespace(session_id='blocked')
    controls = SimpleNamespace(capacity=SimpleNamespace(guard=None), restore=AsyncMock(), persist=Mock())
    monkeypatch.setattr('amplifier_web.runtime_bootstrap.bootstrap_app_package', lambda: None)
    monkeypatch.setattr('amplifier_web.host.config.app_home', lambda: tmp_path / 'app')
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager', AsyncMock(return_value=(session, runtime, {})))
    def make_controls(session_arg, runtime_arg, telemetry, *, preserve_configuration, tool_invocation):
        assert session_arg is session
        assert runtime_arg is runtime
        assert preserve_configuration is False
        assert tool_invocation == worker.tool_invocation
        return controls
    monkeypatch.setattr('amplifier_web.runtime_controls.RuntimeControls', make_controls)
    monkeypatch.setattr('amplifier_web.runtime_qualification.active_install_overrides', lambda *args: None)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending', lambda *args: None)
    monkeypatch.setattr('amplifier_web.app_guidance.install_app_access', AsyncMock())
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.runtime',
        SimpleNamespace(Runtime=lambda **kwargs: runtime))
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker = Worker()
    worker.shared_store = SimpleNamespace()
    worker.shared_handle = SimpleNamespace()
    worker.activation_gate = ActivationGate()
    await worker.start({'id': 'blocked', 'workspace': str(tmp_path)}, recover_bundle=False)
    session.execute.assert_not_called()
    session.execute.assert_not_awaited()
    assert worker.execution is None and worker.shutdown.is_set()
    assert not any(event['type'] == 'runtime.ready' for event in events)
    assert any(event['type'] == 'runtime.error' and 'recognized lifecycle' in event['error'] for event in events)


async def test_actual_mounted_parent_late_result_preserves_manual_rename(native_home, community):
    host = await mounted(community, native_home, 'root')
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda event: None, ['old'])
    namer.observe({'type': 'input.delivered', 'input_id': 'new', 'source': 'user'})
    await prompt(host)
    namer.observe({'type': 'generation.finished', 'input_ids': ['new']})
    await asyncio.wait_for(host.started.wait(), 1)
    host.metadata.set_name('Keep my newer name')
    host.release.set()
    await namer.pending
    assert len(host.calls) == 1
    assert host.metadata.read()['name'] == 'Keep my newer name'
    await namer.close()


async def test_parent_handoff_leaves_separate_child_lifecycle_operational(native_home, community):
    root = await mounted(community, native_home, 'root')
    child = await mounted(community, native_home, 'child')
    child.co.parent_id = 'root'
    namer = LiveSessionNaming(root.co, native_home / 'app', lambda event: None)
    await prompt(root)
    await prompt(child)
    await asyncio.wait_for(child.started.wait(), 1)
    assert not root.calls and len(child.calls) == 1
    child.release.set()
    await child.co.hooks.emit('session:end', {'session_id': 'child'})
    assert child.metadata.read()['name'] == 'Generated project'
    await namer.close()


@pytest.mark.parametrize('snapshot', [False, True])
async def test_missing_bundle_naming_still_supports_explicit_names_without_plan_or_history_changes(native_home, community, snapshot):
    from copy import deepcopy
    host = await mounted(community, native_home, 'saved-root', count=0, automatic=False, manual=True)
    if snapshot:
        from amplifier_foundation import Bundle
        from amplifier_web.bundles import SNAPSHOT_VERSION
        from amplifier_web.host.config import HostConfig
        from amplifier_web.host.session import compose_configured_bundle
        bundle = Bundle(name='saved', version=SNAPSHOT_VERSION,
            session={'orchestrator': {'module':'loop-live', 'source':'git+https://github.com/microsoft/amplifier-module-loop-live@main'}, 'context': {'module':'context-simple'}})
        before_plan = deepcopy(bundle.to_mount_plan())
        configured = HostConfig(native_home / 'app', native_home, {'bundle': {'app': []}}, native_home / 'registry')
        composed = await compose_configured_bundle(None, bundle, configured)
        assert composed.to_mount_plan() == before_plan
        host.co.config = {**composed.to_mount_plan(), 'project_dir':str(native_home)}
    plan = deepcopy(host.co.config)
    before = host.metadata.read()
    transcript = host.metadata.history.session_dir / 'transcript.jsonl'
    history = transcript.read_bytes()
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda event: None, ['old'])
    assert namer.hook is not None, namer.unavailable
    # Initializing or observing recovered generations must not replay work.
    namer.observe({'type': 'generation.finished', 'input_ids': ['old']})
    assert not host.calls and namer.pending is None
    host.release.set()
    suggestion = await namer.suggest()
    assert suggestion['name'] == 'Generated project'
    assert len(host.calls) == 1
    assert host.co.config == plan
    assert host.metadata.read() == before
    assert transcript.read_bytes() == history
    await namer.close()


async def test_absent_bundle_hook_auto_off_and_late_result_respect_manual_choice(native_home, community):
    host = await mounted(community, native_home, 'minimal', count=0)
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda event: None, ['old'])
    namer.observe({'type': 'input.delivered', 'input_id': 'new', 'source': 'user'})
    namer.observe({'type': 'generation.finished', 'input_ids': ['new']})
    await asyncio.wait_for(host.started.wait(), 1)
    host.metadata.set_name('Keep my manual choice')
    host.release.set()
    await namer.pending
    assert host.metadata.read()['name'] == 'Keep my manual choice'
    await namer.close()


def test_naming_is_an_included_dependency_without_changing_saved_declarations():
    from amplifier_web.host.session import required_components, module_source
    components = required_components()
    assert 'hooks-session-naming' in components.installed
    source = 'git+https://github.com/microsoft/amplifier-foundation@saved#subdirectory=modules/hooks-session-naming'
    original = {'hooks': [{'module': 'hooks-session-naming', 'source': source, 'config': {'model_role': None}}]}
    assert components.normalize(original) == original
    installed = module_source(SimpleNamespace(), True, 'hooks-session-naming', source, components)
    assert (Path(installed) / '__init__.py').is_file()


async def test_default_app_naming_uses_current_selected_provider_not_priority_or_role(native_home, community):
    from amplifier_web.host.session import SelectedProvider
    host = await mounted(community, native_home, 'selected', count=0, automatic=False)
    selected = host.co.get('providers')['fixture']
    class Unwanted:
        priority = -100
        async def complete(self, request, **kwargs):
            raise AssertionError('Naming must not borrow another provider')
    providers = {'other': Unwanted(), 'chosen': selected}
    loop = SimpleNamespace(root_provider=SelectedProvider(selected, {'model': 'chosen-model'}))
    original_get = host.co.get
    host.co.get = lambda key: providers if key == 'providers' else loop if key == 'orchestrator' else original_get(key)
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda event: None)
    assert namer.hook.config.model_role is None
    assert namer.hook._select_session_provider(providers) == ('chosen', selected)
    host.release.set()
    result = await namer.suggest()
    assert result['name'] == 'Generated project' and len(host.calls) == 1
    assert host.calls[0].model == 'chosen-model'
    loop.root_provider = SelectedProvider(Unwanted(), {'model': 'missing'})
    with pytest.raises(ValueError, match='no longer mounted'):
        namer.hook._select_session_provider(providers)
    await namer.close()


def test_naming_dependency_is_in_both_app_and_worker_manifests():
    import tomllib
    root = Path(__file__).resolve().parents[1]
    app = tomllib.loads((root / 'pyproject.toml').read_text())
    worker = tomllib.loads((root / 'amplifier_web/runtime_deps/pyproject.toml').read_text())
    name = 'amplifier-module-hooks-session-naming'
    assert any(value.startswith(name + ' @ ') for value in app['project']['dependencies'])
    assert name in worker['project']['dependencies']
    assert worker['tool']['uv']['sources'][name] == {'git':'https://github.com/microsoft/amplifier-foundation',
        'rev':'main', 'subdirectory':'modules/hooks-session-naming'}
    assert not any('amplifier-app-cli' in value for value in app['project']['dependencies'] + worker['project']['dependencies'])


async def test_first_exchange_and_periodic_refresh_change_titles_without_idle_calls(native_home, community):
    host = await mounted(community, native_home, 'cadence', count=0)
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda event: None)
    host.release.set()
    async def turn(number):
        host.context.messages.append({'role': 'user', 'content': f'New scope {number}'})
        namer.observe({'type': 'input.delivered', 'input_id': str(number), 'source': 'user'})
        namer.observe({'type': 'generation.finished', 'input_ids': [str(number)]})
        if namer.pending: await namer.pending
    await turn(1)
    assert len(host.calls) == 1
    assert host.metadata.read()['name'] == 'Generated project'
    host.provider.response_name = 'Expanded project'
    for number in range(2, 6): await turn(number)
    assert len(host.calls) == 3  # Initial, early refinement, then interval review.
    assert host.metadata.read()['name'] == 'Expanded project'
    assert 'early clarification' in str(host.calls[1])
    assert 'periodic review' in str(host.calls[2])
    assert '"keep"' in str(host.calls[1])
    before = len(host.calls)
    namer.refresh_seconds = .01
    namer.observe({'type': 'generation.started'})
    await asyncio.sleep(.04)
    assert len(host.calls) == before  # unchanged sample, no paid polling
    host.context.messages.append({'role': 'assistant', 'content': 'A new design emerged'})
    await asyncio.sleep(.04)
    assert len(host.calls) == before + 1
    namer.observe({'type': 'session.idle'})
    host.context.messages.append({'role': 'assistant', 'content': 'Idle must not invoke providers'})
    await asyncio.sleep(.03)
    assert len(host.calls) == before + 1
    await namer.close()
    assert namer.timer is None


async def test_manual_name_snapshot_remains_stable_while_context_changes(native_home, community):
    host = await mounted(community, native_home, 'concurrent', count=0)
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda event: None)
    pending = asyncio.create_task(namer.suggest())
    await host.started.wait()
    captured = str(host.calls[0])
    host.context.messages.append({'role': 'user', 'content': 'A later concurrent message'})
    host.release.set()
    suggestion = await pending
    assert 'A later concurrent message' not in captured
    assert 'Build orbits' in captured
    assert suggestion['name'] == 'Generated project'
    assert host.metadata.read()['name'] == 'Hi!'
    await namer.close()


async def test_close_and_auto_off_prevent_periodic_calls(native_home, community):
    host = await mounted(community, native_home, 'off', count=0, automatic=False)
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda event: None, ['old'])
    namer.refresh_seconds = .01
    namer.observe({'type': 'generation.started'})
    await asyncio.sleep(.04)
    assert not host.calls
    await namer.close()
    namer.observe({'type': 'generation.started'})
    await asyncio.sleep(.02)
    assert not host.calls
