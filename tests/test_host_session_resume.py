"""Native resume/admission tests use real storage and no provider calls."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web.host import session as host
from amplifier_web.host.session import compose_configured_bundle
from amplifier_web.host.storage import SessionStore


@pytest.fixture
def mounted_host(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "app"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    monkeypatch.chdir(workspace)  # Also restore cwd after prepare_manager changes it.
    config = SimpleNamespace(workspace=workspace, home=home, registry_home=home,
        resolve_source=lambda value: value, registrations={}, active_bundle="anchors",
        app_bundles=[], settings={}, module_sources={}, config_home=None, settings_file=home/"settings.yaml")
    monkeypatch.setattr(host, "load_config", lambda _, **kwargs: config)
    runtime = SimpleNamespace(session_id="native-root", observer=None)
    context = SimpleNamespace(messages=[])
    async def set_messages(messages):
        context.messages = copy.deepcopy(messages)
    async def get_messages():
        return copy.deepcopy(context.messages)
    context.set_messages, context.get_messages = set_messages, get_messages
    provider = SimpleNamespace(get_info=lambda: SimpleNamespace(id="fixture", defaults={"model": "offline"}))
    loop = SimpleNamespace(_select_provider=lambda _: provider, load_failures=[])
    capabilities = {}
    coordinator = SimpleNamespace(get=lambda key: {"context": context, "orchestrator": loop,
        "providers": {"fixture": provider}, "tools": {}}.get(key), get_capability=capabilities.get,
        register_capability=lambda key, value: capabilities.update({key: value}),
        hooks=SimpleNamespace(register=Mock()), register_cleanup=Mock())
    session = SimpleNamespace(coordinator=coordinator, config={}, cleanup=AsyncMock(), execute=AsyncMock())
    prepared = SimpleNamespace(mount_plan={"session": {"orchestrator": {"module": "loop-live"}}},
                               create_session=AsyncMock(return_value=session))
    loaded = SimpleNamespace(hooks=[], to_mount_plan=lambda: copy.deepcopy(prepared.mount_plan),
                             prepare=AsyncMock(return_value=prepared))
    registry = SimpleNamespace(list_registered=lambda: {}, find=lambda _: None, register=Mock(), save=Mock(),
                               load=AsyncMock(return_value=loaded))
    configurator = SimpleNamespace(apply_saved_settings=AsyncMock(), take_snapshot=Mock())
    monkeypatch.setitem(sys.modules, "amplifier_foundation", SimpleNamespace(
        BundleRegistry=lambda *, persist=True, read_persisted=True, **_: registry, SessionConfigurator=lambda *_: configurator))
    monkeypatch.setitem(sys.modules, "amplifier_module_loop_live.runtime", SimpleNamespace(Runtime=lambda: runtime))
    monkeypatch.setitem(sys.modules, "amplifier_module_loop_live.job_store", SimpleNamespace(
        JobStore=lambda _: SimpleNamespace(rows=[], close=Mock())))
    monkeypatch.setitem(sys.modules, "amplifier_core", SimpleNamespace(HookResult=lambda: None))
    monkeypatch.setitem(sys.modules, "amplifier_web.host.children", SimpleNamespace(
        install_children=AsyncMock(), StandaloneHostAdapter=lambda: object()))
    monkeypatch.setattr(host, "compose_configured_bundle", AsyncMock(return_value=loaded))
    monkeypatch.setattr(host, "is_snapshot", lambda _: False)
    monkeypatch.setattr(host, "materialize_bundle_providers", AsyncMock())
    monkeypatch.setattr(host, "apply_provider_environment", lambda _: None)
    store = SessionStore.for_app(home, workspace)
    checkpoint = tmp_path / "common.json"
    writes = []
    def write(messages, **kwargs):
        payload = {"messages": copy.deepcopy(messages), **kwargs}
        writes.append(payload)
        checkpoint.write_text(json.dumps(payload))
    held = SimpleNamespace(write=write, check=Mock(), read=Mock(return_value=None))
    async def prepare(snapshot=None, **kwargs):
        return await host.prepare_manager(workspace, runtime=runtime, resume=True,
            shared_handle=held, shared_snapshot=snapshot, **kwargs)
    return SimpleNamespace(prepare=prepare, store=store, held=held, checkpoint=checkpoint,
        writes=writes, runtime=runtime, session=session, prepared=prepared, registry=registry,
        context=context, capabilities=capabilities, home=home, config=config, loaded=loaded,
        path=store.directory(runtime.session_id) / "transcript.jsonl")


def snapshot(messages):
    return {"messages": copy.deepcopy(messages), "metadata": {}, "bundle": "anchors"}


async def test_checkpoints_persist_tools_without_republishing_unchanged_message_anchors(mounted_host):
    h = mounted_host
    h.runtime.generation = {'id': 'generation-one', 'input_ids': ['input-one']}
    h.runtime.emit = AsyncMock()
    rows = [
        {'role': 'user', 'content': 'Synthetic input',
         'metadata': {'amplifier_input': {'id': 'input-one'}}},
        {'role': 'assistant', 'content': 'Inspecting the local files.'},
    ]
    await h.prepare(snapshot(rows))
    checkpoint = h.capabilities['live.checkpoint']
    assert h.runtime.emit.await_count == 1
    first = h.runtime.emit.call_args.kwargs['messageAnchors']
    # Parallel tools and repeated native checkpoints do not add assistant
    # anchors, but their transcript results must still be saved exactly.
    for number in range(6):
        h.context.messages.append({'role': 'tool', 'tool_call_id': f'tool-{number}',
                                   'content': f'Synthetic result {number}'})
        await checkpoint()
        saved = [json.loads(line) for line in h.path.read_text().splitlines()]
        assert saved[-1]['content'] == f'Synthetic result {number}'
    assert h.runtime.emit.await_count == 1
    h.context.messages.append({'role': 'assistant', 'content': 'Inspection completed.'})
    await checkpoint()
    assert h.runtime.emit.await_count == 2
    assert len(h.runtime.emit.call_args.kwargs['messageAnchors']) == len(first) + 1
    # Generation identity is authority even when the transcript is unchanged.
    h.runtime.generation['id'] = 'generation-two'
    await checkpoint()
    assert h.runtime.emit.await_count == 3
    assert h.runtime.emit.call_args.kwargs['generation_id'] == 'generation-two'
    # Reopening the native history establishes a fresh notification, even when
    # this process's previous mount had published the same exact references.
    await h.prepare()
    assert h.runtime.emit.await_count == 4


@pytest.mark.parametrize('failure', [RuntimeError('pipe failed'), asyncio.CancelledError()])
async def test_failed_anchor_notification_is_retried_on_next_checkpoint(mounted_host, failure):
    h = mounted_host
    await h.prepare()
    h.runtime.generation = {'id': 'generation-one', 'input_ids': ['input-one']}
    async def emit(*args, **kwargs):
        if h.runtime.emit.await_count == 1:
            raise failure
    h.runtime.emit = AsyncMock(side_effect=emit)
    h.context.messages = [
        {'role': 'user', 'content': 'Synthetic input',
         'metadata': {'amplifier_input': {'id': 'input-one'}}},
        {'role': 'assistant', 'content': 'Saved response.'},
    ]
    checkpoint = h.capabilities['live.checkpoint']
    with pytest.raises(type(failure)):
        await checkpoint()
    assert 'Saved response.' in h.path.read_text()
    await checkpoint()
    assert h.runtime.emit.await_count == 2
    await checkpoint()
    assert h.runtime.emit.await_count == 2


@pytest.mark.parametrize('connected', [False, True])
async def test_root_preparation_advertises_only_existing_human_transport(mounted_host, connected):
    ask = AsyncMock() if connected else None
    h = mounted_host
    await h.prepare(ask=ask)
    assert h.capabilities['approval.interactive'] is connected
    adapter = h.prepared.create_session.call_args.kwargs['approval_system']
    assert adapter.interactive is connected
    if connected:
        ask.assert_not_called()


@pytest.mark.parametrize('frozen', [False, True])
async def test_host_binds_instruction_paths_after_overrides_except_frozen_snapshots(mounted_host, monkeypatch, frozen):
    h = mounted_host
    observed = []
    class Root:
        instruction = 'Original root.'
        hooks = []
        def to_mount_plan(self):
            return {**copy.deepcopy(h.prepared.mount_plan), 'instruction': self.instruction}
        async def prepare(self, **kwargs):
            observed.append((self.instruction, dict(getattr(self, 'context', {}))))
            return h.prepared
    root = Root()
    h.registry.load.return_value = root
    host.compose_configured_bundle.return_value = root
    monkeypatch.setattr(host, 'is_snapshot', lambda _: frozen)
    from amplifier_web.runtime_controls import override_path
    path = override_path(h.runtime.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'session': {**h.prepared.mount_plan['session'], 'context': {'module': 'context-simple'}},
                               'providers': [{'module': 'provider-fixture'}],
                               'instruction': 'Edited root.'}))
    monkeypatch.setattr(host, '_apply_host_policy', lambda bundle, *args, **kwargs: bundle)
    await h.prepare()
    assert len(observed) == 1
    assert observed[0][0] == 'Edited root.'
    if frozen:
        assert observed[0][1] == {}
    else:
        assert list(observed[0][1].values()) == [
            h.config.settings_file.parent / 'AGENTS.md',
            h.config.workspace / '.amplifier/AGENTS.md',
            h.config.workspace / 'AGENTS.md',
        ]
    # The source object is not given the host's context paths.
    assert root.instruction == 'Edited root.'
    assert not hasattr(root, 'context')


async def test_prepared_new_chat_selection_reaches_public_controls_and_first_request(mounted_host):
    from amplifier_web.runtime_controls import RuntimeControls
    h = mounted_host
    coordinator = h.session.coordinator
    coordinator.config, coordinator.session_state = {}, {}
    loop = coordinator.get('orchestrator')
    provider = coordinator.get('providers')['fixture']
    class Info(SimpleNamespace):
        def model_copy(self, *, update):
            return Info(**{**vars(self), **update})
    provider.get_info = lambda: Info(id='fixture', defaults={'model': 'bundle-model', 'reasoning_effort': 'high'})
    provider.complete = AsyncMock(return_value='done')
    loop._select_provider = lambda mounted: getattr(loop, 'root_provider', None) or provider
    selection = {'instance': 'fixture', 'model': 'selected-model', 'effort': 'xhigh'}
    session, runtime, report = await h.prepare(selection=selection)
    session.session_id = runtime.session_id
    coordinator.get_capability('web.configurator').snapshot = lambda: {}
    controls = RuntimeControls(session, runtime)
    await controls.restore()
    controls.persist()
    current = await controls.perform('configuration.providers')
    assert current['selection'] == current['effective'] == report['selection'] == selection
    assert current['pinned']
    request = Info(model=None, reasoning_effort=None)
    await loop.root_provider.complete(request)
    actual = provider.complete.call_args
    assert actual.args[0].model == 'selected-model' and actual.args[0].reasoning_effort == 'xhigh'
    assert actual.kwargs == {'model': 'selected-model', 'reasoning_effort': 'xhigh'}
    assert request.model is None and request.reasoning_effort is None
    assert provider.get_info().defaults == {'model': 'bundle-model', 'reasoning_effort': 'high'}
    await controls.close()


async def test_module_preparation_uses_active_registry_cache_not_shared_history_home(mounted_host):
    h = mounted_host
    h.config.registry_home = h.home / "updates/releases/validated/foundation"
    shared = os.environ["AMPLIFIER_HOME"]
    await h.prepare()
    assert h.loaded.prepare.call_args.kwargs["cache_dir"] == h.config.registry_home / "cache"
    assert os.environ["AMPLIFIER_HOME"] == shared


async def test_app_recording_policy_reaches_root_preparation_and_installs_redaction(mounted_host):
    h = mounted_host
    path = h.home / 'diagnostics' / 'config.json'
    path.parent.mkdir(parents=True)
    path.write_text('{"providerRequests":true}')
    h.prepared.mount_plan['providers'] = [{'module':'provider-openai'}]
    h.prepared.mount_plan['agents'] = {'private': {'providers':[
        {'module':'provider-anthropic','config':{'raw':False}}]}}
    class Root:
        hooks = []
        def to_mount_plan(self):
            return copy.deepcopy({**h.prepared.mount_plan,
                'providers': getattr(self, 'providers', h.prepared.mount_plan['providers']),
                'agents': getattr(self, 'agents', h.prepared.mount_plan['agents'])})
        async def prepare(self, **kwargs):
            assert self.providers[0]['config']['raw'] is True
            assert self.agents['private']['providers'][0]['config']['raw'] is False
            return h.prepared
    h.registry.load.return_value = Root()
    host.compose_configured_bundle.return_value = h.registry.load.return_value
    await h.prepare()
    assert h.capabilities['web.provider_request_redaction'] is True
    registrations = h.session.coordinator.hooks.register.call_args_list
    assert any(call.args[0] == 'llm:request' and call.kwargs.get('priority') == -100 for call in registrations)


async def test_missing_saved_connection_publishes_choices_without_changing_history(mounted_host):
    from amplifier_web.host.model_selection import ProviderSelectionError
    from amplifier_web.provider_recovery import catalog
    h = mounted_host
    messages = [{'role': 'user', 'content': 'Already accepted work'}]
    h.store.save('native-root', messages, {'bundle': 'anchors'})
    before = h.path.read_bytes()
    wanted = {'instance': 'deleted', 'model': 'saved-model', 'effort': 'high'}
    with pytest.raises(ProviderSelectionError):
        await h.prepare(selection=wanted)
    result = catalog(h.home, {'id': 'native-root', 'workspace': str(h.config.workspace),
                             'status': 'error', 'failure': {'category': 'provider_selection'}})
    assert result['selection'] == wanted
    assert [row['id'] for row in result['providers']] == ['fixture']
    assert h.path.read_bytes() == before
    h.session.execute.assert_not_called()
    h.session.cleanup.assert_awaited_once()


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_initialization_failure_keeps_cleanup_ownership_before_session_returns(mounted_host, failure):
    h = mounted_host
    h.store.save("native-root", [{"role": "user", "content": "Retained work"}], {"bundle": "anchors"})
    before = h.path.read_bytes()
    async def create(**kwargs):
        await kwargs["before_initialize"](h.session)
        raise failure("Later account failed during initialization")
    h.prepared.create_session.side_effect = create
    with pytest.raises(failure):
        await h.prepare()
    h.session.cleanup.assert_awaited_once()
    h.session.execute.assert_not_called()
    assert h.path.read_bytes() == before


async def test_native_only_resume_keeps_complete_history_and_repairs_receipt_without_replay(mounted_host):
    h = mounted_host
    rows = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "second"},
            {"role": "tool", "tool_call_id": "call-1", "content": json.dumps(
                {"status": "queued", "job_id": "job-1", "call_id": "call-1"})}]
    h.store.save("native-root", rows, {"bundle": "anchors"})
    _, _, report = await h.prepare()
    assert h.context.messages[:3] == rows[:3]
    receipt = json.loads(h.context.messages[3]["content"])
    assert receipt["status"] == "interrupted" and receipt["effects"] == "not_rolled_back"
    assert h.writes == []
    h.held.check.assert_called()
    assert h.store.load("native-root")[0] == h.context.messages
    assert report["resumed"] and report["history_source"] == "native"
    assert h.prepared.create_session.call_args.kwargs["session_id"] == "native-root"
    h.session.execute.assert_not_called()


async def test_native_history_wins_over_stale_checkpoint_and_repairs_receipts(mounted_host):
    h = mounted_host
    rows = [{"role": "user", "content": "native"}, {"role": "assistant", "content": "CLI answer"}]
    h.store.save("native-root", rows, {})
    await h.prepare(snapshot([{"role": "user", "content": "stale"}]))
    assert h.context.messages == rows
    assert h.writes == []
    assert h.store.load("native-root")[0] == rows
    h.session.execute.assert_not_called()


async def test_discovered_native_bundle_wins_over_current_app_default(mounted_host):
    h = mounted_host
    rows = [{"role": "user", "content": "native conversation"}]
    h.store.save("native-root", rows, {"bundle": "native-recorded-bundle"})
    await h.prepare(bundle="native-recorded-bundle")
    h.registry.load.assert_awaited_once_with("native-recorded-bundle")
    assert h.store.load("native-root")[1]["bundle"] == "native-recorded-bundle"
    assert h.context.messages == rows


async def test_native_history_does_not_even_read_corrupt_common_checkpoint(mounted_host):
    h = mounted_host
    newer = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "CLI answer"}]
    h.store.save("native-root", newer, {"bundle": "anchors"})
    h.held.read.side_effect = ValueError("corrupt legacy checkpoint")
    await h.prepare()
    assert h.context.messages == newer and h.writes == []
    h.held.read.assert_not_called()


async def test_preparing_saved_chat_does_not_change_recency_until_actual_context_changes(mounted_host):
    from amplifier_web.native_history import NativeHistory
    h=mounted_host
    workspace=h.home.parent/'workspace'
    rows=[{'role':'user','content':'Saved question'},{'role':'assistant','content':'Saved answer'}]
    h.store.save('native-root',rows,{'working_dir':str(workspace),'bundle':'anchors','turn_count':1})
    os.utime(h.path,(100,100))
    index=NativeHistory(known_workspaces=[workspace])
    assert index.scan()['sessions'][0]['recentActivityAt']==100
    await h.prepare()
    assert h.path.stat().st_mtime==100
    assert index.scan()['sessions'][0]['recentActivityAt']==100
    # A real tool/model turn changes the context and must still update the
    # canonical transcript, its backup and its navigation activity time.
    h.context.messages.append({'role':'user','content':'A new question'})
    await h.capabilities['live.checkpoint']('in_progress')
    assert index.scan()['sessions'][0]['recentActivityAt']>100


@pytest.mark.parametrize("mutation", ["append", "delete", "replace", "symlink"])
async def test_out_of_band_native_change_blocks_later_checkpoint(mounted_host, mutation):
    h = mounted_host
    rows = [{"role": "user", "content": "first"}]
    h.store.save("native-root", rows, {})
    await h.prepare(snapshot(rows))
    assert not h.checkpoint.exists()
    original_metadata = (h.path.parent / "metadata.json").read_bytes()
    if mutation == "append":
        with h.path.open("a") as stream:
            stream.write('{"role":"assistant","content":"from CLI"}\n')
    elif mutation == "replace":
        replacement = h.path.with_suffix(".replacement")
        replacement.write_bytes(h.path.read_bytes())
        replacement.replace(h.path)
    elif mutation == "delete":
        h.path.unlink()
    else:
        target = h.path.with_suffix(".outside")
        target.write_bytes(h.path.read_bytes())
        h.path.unlink()
        h.path.symlink_to(target)
    native_after = h.path.read_bytes() if h.path.exists() else None
    h.context.messages.append({"role": "assistant", "content": "stale in-memory answer"})
    with pytest.raises(host.NativeTranscriptConflict):
        await h.capabilities["live.checkpoint"]("completed")
    assert (h.path.read_bytes() if h.path.exists() else None) == native_after
    assert not h.checkpoint.exists()
    assert (h.path.parent / "metadata.json").read_bytes() == original_metadata
    assert h.writes == []


async def test_missing_native_projection_uses_common_checkpoint_without_private_migration(mounted_host):
    h = mounted_host
    private = SessionStore(h.home / "sessions")
    private.directory("native-root").mkdir()
    (private.directory("native-root") / "checkpoint.json").write_text(json.dumps({
        "version": 1, "messages": [{"role": "user", "content": "stale private copy"}], "metadata": {}}))
    old_private = (private.directory("native-root") / "checkpoint.json").read_bytes()
    rows = [{"role": "user", "content": "authoritative common checkpoint"}]
    await h.prepare(snapshot(rows))
    assert h.context.messages == rows and h.store.load("native-root")[0] == rows
    assert (private.directory("native-root") / "checkpoint.json").read_bytes() == old_private


async def test_corrupt_native_transcript_never_becomes_empty_history(mounted_host):
    h = mounted_host
    h.path.parent.mkdir(parents=True)
    h.path.write_text('{"role":"user"\n')
    original = h.path.read_bytes()
    with pytest.raises(ValueError):
        await h.prepare(snapshot([]))
    assert h.path.read_bytes() == original
    assert not h.writes
    h.registry.load.assert_not_called()


async def test_native_backup_recovery_preserves_provider_fields_without_replay(mounted_host):
    h = mounted_host
    rows = [{"role": "user", "content": "first"}, {"role": "assistant", "content": [],
             "provider_state": {"encrypted_reasoning": "opaque-fixture", "namespace": "tools"}}]
    h.store.save("native-root", rows, {"bundle": "recorded-bundle", "unknown": {"keep": True}})
    h.path.with_name('transcript.jsonl.backup').write_bytes(h.path.read_bytes())
    h.path.write_text('{partial')
    await h.prepare(snapshot([]))
    assert h.context.messages == rows
    assert h.store.load('native-root')[1]['unknown'] == {'keep': True}
    h.registry.load.assert_awaited_once_with('recorded-bundle')
    assert h.writes == []


async def test_manual_rename_during_active_turn_survives_next_native_save(mounted_host):
    from amplifier_web.naming import persist
    h = mounted_host
    h.store.save('native-root', [{'role': 'user', 'content': 'fixture'}], {'bundle': 'anchors'})
    await h.prepare()
    persist(h.home, {'id': 'native-root', 'workspace': str(h.home.parent / 'workspace'),
                     'title': 'Manual name during work', 'titleSource': 'manual'}, shared_rename=True)
    await h.capabilities['live.checkpoint']('completed')
    assert h.store.load('native-root')[1]['name'] == 'Manual name during work'
    assert h.store.load('native-root')[1]['name_source'] == 'manual'
    assert h.writes == []


async def test_execution_checkout_keeps_native_history_home(mounted_host, tmp_path):
    h = mounted_host
    checkout = tmp_path / 'execution-checkout'; checkout.mkdir()
    messages = [{'role': 'user', 'content': 'Retain my saved source'}, {'role': 'assistant', 'content': 'Original evidence'}]
    h.store.save('native-root', messages, {'bundle': 'anchors'})
    original = h.path.read_bytes()
    _, _, report = await h.prepare(execution_workspace=checkout)
    assert h.prepared.create_session.call_args.kwargs['session_cwd'] == checkout
    assert Path.cwd() == checkout
    assert h.path.read_bytes() == original
    assert h.context.messages == messages
    assert report['execution_workspace'] == str(checkout)
    assert report['workspace'] != str(checkout)
    assert h.capabilities['web.history_workspace'] == report['workspace']
    h.session.execute.assert_not_called()


@pytest.mark.parametrize('route', ['direct', 'resolved', 'override'])
async def test_execution_write_policy_follows_checkout_on_all_prepare_routes(mounted_host, tmp_path, monkeypatch, route):
    h = mounted_host
    checkout = tmp_path / 'execution-checkout'; checkout.mkdir()
    h.config.providers = []
    h.config.settings = {'overrides': {'tool-filesystem': {'config': {'denied_write_paths': ['private']}}}}
    h.loaded.providers = [{'module': 'provider-fixture'}]
    h.loaded.tools = [{'module': 'tool-filesystem'}]
    h.loaded.hooks = [{'module': 'hook-context-intelligence'}]
    h.loaded.agents = {}
    h.loaded.session = {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}}
    h.loaded.to_mount_plan = lambda: copy.deepcopy({key: getattr(h.loaded, key)
        for key in ('providers', 'tools', 'hooks', 'agents', 'session')})
    monkeypatch.setattr(host, 'compose_configured_bundle', compose_configured_bundle)
    kwargs = {}
    if route == 'resolved':
        root = await host.load_root_bundle(h.config, 'anchors', execution_workspace=checkout)
        kwargs['resolved_root'] = host.ResolvedRoot(h.config, 'anchors', root, execution_workspace=checkout)
        h.registry.load.reset_mock()
    if route == 'override':
        from amplifier_web.runtime_controls import override_path
        path = override_path(h.runtime.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        edited = h.loaded.to_mount_plan()
        edited['tools'][0]['config'] = {'denied_write_paths': ['edited-private']}
        path.write_text(json.dumps(edited))
    messages = [{'role': 'user', 'content': 'Preserve original history'}]
    h.store.save('native-root', messages, {'bundle': 'anchors'})
    original = h.path.read_bytes()
    _, _, report = await h.prepare(execution_workspace=checkout, **kwargs)
    policy = h.loaded.tools[0]['config']
    assert policy['allowed_write_paths'] == [str(checkout)]
    assert str(checkout / 'private') in policy['denied_write_paths']
    if route == 'override':
        assert str(checkout / 'edited-private') in policy['denied_write_paths']
    if route == 'resolved':
        h.registry.load.assert_not_called()
    assert report['workspace'] == str(h.config.workspace) != str(checkout)
    assert report['execution_workspace'] == str(checkout)
    assert h.path.read_bytes() == original
    h.session.execute.assert_not_called()
