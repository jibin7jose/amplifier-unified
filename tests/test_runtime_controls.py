import os
from pathlib import Path
import subprocess
import unittest
import pytest

from amplifier_web.runtime_controls import public_config, restore_redactions, validate_plan


@pytest.mark.asyncio
async def test_explicit_recovery_control_only_arms_with_host_binding(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from test_task_continuity import controls
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    control = controls('recovery')
    recovery = SimpleNamespace(request_recovery=AsyncMock(return_value={'status': 'armed'}),
                               recovery_status=lambda: {'status': 'armed'})
    control.coordinator.register_capability('web.continuity.recovery', recovery)
    try:
        with pytest.raises(ValueError, match='original host command'):
            await control.perform('context.recovery.request', {})
        assert await control.perform('context.recovery.request', {
            '_recoveryCommand': {'id': 'exact-command', 'interruptionRevision': 3},
            'targetBytes': 5000}) == {'status': 'armed'}
        recovery.request_recovery.assert_awaited_once_with(
            'exact-command', 3, target_bytes=5000)
        control.runtime.generation = {'id': 'busy'}
        with pytest.raises(ValueError, match='current response'):
            await control.perform('context.recovery.request', {
                '_recoveryCommand': {'id': 'another', 'interruptionRevision': 3}})
        assert await control.perform('context.recovery.status') == {'status': 'armed'}
        assert control.coordinator.session_state.get('goal') is None
    finally:
        await control.close()


@pytest.mark.parametrize('preserve', [False, True])
async def test_cancelled_restore_retains_controls_and_releases_persistence_guard(
        tmp_path, monkeypatch, preserve):
    import asyncio
    import json
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from test_task_continuity import controls, create
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    original, restored = controls('cold-task'), controls('cold-task')
    started, release = asyncio.Event(), asyncio.Event()
    original.coordinator.session_state['mode_discovery'] = SimpleNamespace(
        find=lambda name: SimpleNamespace(name=name), list_modes=lambda: [])
    original.coordinator.hooks = SimpleNamespace(emit=AsyncMock())
    await create(original, maxTurns=8)
    await original.perform('mode.set', {'name': 'review-only'})
    before = original.state_path().read_bytes()
    restored.preserve_configuration = preserve
    restored.coordinator.session_state['mode_discovery'] = original.coordinator.session_state['mode_discovery']
    async def activation(*args):
        started.set()
        await release.wait()
    restored.coordinator.hooks = SimpleNamespace(emit=activation)
    pending = asyncio.create_task(restored.restore())
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert restored.state_path().read_bytes() == before
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert restored.state_path().read_bytes() == before
        assert restored.preserve_configuration is preserve
        assert not restored._restoring
        # A later successful preparation uses the same saved values. No replay
        # of the cancelled task input or automatic limit change is implied.
        release.set()
        await restored.restore()
        assert json.loads(restored.state_path().read_text())['mode'] == 'review-only'
        assert restored.preserve_configuration is preserve
        if preserve:
            assert restored.state_path().read_bytes() == before
        else:
            await restored.perform('task.update', {
                'commandId': 'ordinary-after-restore', 'expectedRevision': 1, 'maxTurns': 12})
            assert json.loads(restored.state_path().read_text())['task']['maxTurns'] == 12
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await original.close()
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['unique', 'ambiguous', 'different-model', 'different-family', 'existing'])
async def test_saved_selection_requires_exact_connection_id(tmp_path, monkeypatch, case):
    import json
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    class Provider:
        def __init__(self, family='openai', model='saved-model'):
            self.family, self.model = family, model
        async def get_info(self):
            return {'id':self.family, 'defaults':{'model':self.model}}
    providers = {'terra':Provider(), 'other':Provider(model='another-model')}
    if case == 'ambiguous': providers['duplicate'] = Provider()
    if case == 'different-model': providers['terra'] = Provider(model='changed-model')
    if case == 'different-family': providers['terra'] = Provider(family='openai-chatgpt')
    if case == 'existing': providers['openai'] = Provider(model='different-default')
    loop = SimpleNamespace(root_provider=None)
    class Coordinator:
        config = {'providers':[]}
        session_state = {}
        def get(self, name): return {'providers':providers, 'orchestrator':loop}.get(name)
        def get_capability(self, name): return None
        def register_capability(self, *args): pass
    controls = RuntimeControls(SimpleNamespace(session_id='restored', coordinator=Coordinator()),
                               SimpleNamespace(generation=None, queued_inputs=0))
    selection = {'instance':'openai', 'model':'saved-model', 'effort':'high'}
    controls.state_path().parent.mkdir(parents=True)
    controls.state_path().write_text(json.dumps({'selection':selection}))
    if case == 'existing':
        await controls.restore()
        expected = selection
        assert controls.selection == expected
        assert loop.root_provider.original is providers[expected['instance']]
        assert loop.root_provider.selection == expected
        assert json.loads(controls.state_path().read_text())['selection'] == expected
        # Restart keeps the exact connection even when its default model differs.
        await controls.restore()
        assert controls.selection == expected
    else:
        with pytest.raises(ValueError, match='available provider instance'):
            await controls.restore()
        assert loop.root_provider is None
        assert json.loads(controls.state_path().read_text())['selection'] == selection
    await controls.close()


class ControlsTests(unittest.TestCase):
    def test_redacted_secrets_restore_by_instance_after_reordering(self):
        old={'providers':[{'module':'provider-test','id':'a','config':{'api_key':'private-a','max_tokens':400}},
                          {'module':'provider-test','id':'b','config':{'api_key':'private-b','max_tokens':500}}]}
        public=public_config(old)
        self.assertNotIn('private',str(public))
        self.assertEqual(public['providers'][0]['config']['max_tokens'],400)
        public['providers'].reverse()
        restored=restore_redactions(public,old)
        self.assertEqual(restored['providers'][0]['config']['api_key'],'private-b')

    def test_invalid_mount_changes_fail_before_persistence(self):
        plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},
              'providers':[{'module':'provider-test'}]}
        validate_plan(plan)
        plan['providers'][0]['enabled']=False
        with self.assertRaisesRegex(ValueError,'provider'):
            validate_plan(plan)


@pytest.mark.parametrize('bound', [False, True])
async def test_disabled_agent_persistence_retains_exact_edited_and_added_definitions(tmp_path, monkeypatch, bound):
    """Public Foundation toggles, including cold restore; no agent invocation."""
    import copy
    import json
    from types import SimpleNamespace
    from amplifier_core.testing import MockCoordinator
    from amplifier_foundation import SessionConfigurator
    from amplifier_foundation.bundle import Bundle
    from amplifier_web.provider_environment import credential_bindings, rebind_provider_credentials
    from amplifier_web.runtime_controls import RuntimeControls

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    original = {'instruction': 'Initial definition', 'api_key': 'synthetic-agent-metadata'}
    edited = {**original, 'instruction': 'Edited before disabling',
        'providers': [{'module': 'provider-fixture', 'source': 'fixture-source', 'id': 'fixture',
                       'config': {'api_key': 'synthetic-nested-key', 'model': 'fixture'}}]}
    added = {'instruction': 'Added after preparation', 'values': {'nested': [1, 2, 3]}}

    async def make(plan, initial_disabled=False):
        coordinator = MockCoordinator()
        coordinator.config.clear()
        coordinator.config.update(copy.deepcopy(plan))
        coordinator.session_state.clear()
        await coordinator.mount('orchestrator', SimpleNamespace(root_provider=None))
        session = SimpleNamespace(session_id='agent-persistence', coordinator=coordinator)
        prepared = SimpleNamespace(bundle=Bundle(name='fixture', agents=copy.deepcopy(plan.get('agents', {}))))
        configurator = SessionConfigurator(session, prepared)
        coordinator.register_capability('web.configurator', configurator)
        coordinator.register_capability('web.provider_credentials_bound', bound)
        if initial_disabled:
            # Same pre-shared-settings seam as the host; no prepared-plan fallback.
            coordinator.register_capability('web.agent_definitions', copy.deepcopy(plan['agents']))
            configurator.agent_disable('original')
        controls = RuntimeControls(session, SimpleNamespace(generation=None, queued_inputs=0))
        return controls, configurator

    controls, configurator = await make({'agents': {'original': original}}, initial_disabled=True)
    restored = None
    try:
        controls.persist()
        effective_path = controls.state_path().with_name('effective-configuration.json')
        assert json.loads(effective_path.read_text())['agents']['original'] == (
            {'instruction': original['instruction']} if bound else original)
        await controls.perform('configuration.toggle', {'section': 'agents', 'name': 'original', 'enabled': True})
        controls.coordinator.config['agents']['original'] = copy.deepcopy(edited)
        controls.coordinator.config['agents']['added'] = copy.deepcopy(added)
        for name in ('original', 'added'):
            await controls.perform('configuration.toggle', {'section': 'agents', 'name': name, 'enabled': False})
        assert controls.coordinator.config['agents'] == {}
        expected = {'agents': {'original': edited, 'added': added}}
        saved = json.loads(effective_path.read_text())
        assert saved == (credential_bindings(expected) if bound else expected)
        assert json.loads(controls.state_path().read_text())['configurator']['disabled']['agents'] == ['original', 'added']
        # Saved declarations must not alias a mutable live or retained mapping.
        controls.persist()
        assert json.loads(effective_path.read_text()) == saved
        if bound:
            restored_plan, was_bound = rebind_provider_credentials(saved, expected)
            restored_expected = copy.deepcopy(expected)
            restored_expected['agents']['original'].pop('api_key')
            assert was_bound and restored_plan == restored_expected
            changed = copy.deepcopy(expected)
            changed['agents']['original']['providers'][0]['source'] = 'different-source'
            with pytest.raises(ValueError, match='instance/source'):
                rebind_provider_credentials(saved, changed)
        else:
            restored_plan = saved
            restored_expected = expected
        restored, cold_configurator = await make(restored_plan)
        await restored.restore()
        assert restored.coordinator.config['agents'] == {}
        assert cold_configurator.snapshot()['agents']['disabled'] == ['original', 'added']
        assert json.loads(effective_path.read_text()) == saved
        for name, definition in restored_expected['agents'].items():
            await restored.perform('configuration.toggle', {'section': 'agents', 'name': name, 'enabled': True})
            assert restored.coordinator.config['agents'][name] == definition
        # Removing an enabled definition must not resurrect the retained copy.
        restored.coordinator.config['agents'].pop('added')
        restored.persist()
        assert 'added' not in json.loads(effective_path.read_text())['agents']
    finally:
        await controls.close()
        if restored:
            await restored.close()


def test_real_core_controls_and_tool_approval():
    python=os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON to test public Core/Foundation session controls')
    script=Path(__file__).parent/'fixtures/standalone_controls_probe.py'
    completed=subprocess.run([python,str(script)],capture_output=True,text=True,timeout=45)
    assert completed.returncode==0,completed.stdout+completed.stderr
    assert '"approval_enforced": true' in completed.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [False, True])
async def test_effective_snapshot_sanitizes_only_bound_children_and_restores_exact_selection(
        tmp_path, monkeypatch, bound):
    import copy
    import json
    from types import SimpleNamespace
    from amplifier_web.provider_environment import HOST_CREDENTIAL, credential_bindings
    from amplifier_web.runtime_controls import RuntimeControls

    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    plan = {
        "session": {"orchestrator": {"module": "loop-live"},
                    "context": {"module": "context-simple"}},
        "providers": [
            {"module": "provider-fixture", "instance_id": "alternate", "source": "alternate-source",
             "config": {"api_key": "synthetic-alternate-key", "model": "alternate-model",
                        "reasoning_effort": "low", "priority": 20}},
            {"module": "provider-fixture", "instance_id": "fable", "source": "fable-source",
             "config": {"api_key": "synthetic-fable-key", "model": "default-model",
                        "reasoning_effort": "high", "priority": 1}},
        ],
        "agents": {"worker": {
            "providers": [{"module": "provider-fixture", "instance_id": "fable", "source": "worker-source",
                           "config": {"api_key": "synthetic-worker-key", "model": "worker-model"}}],
            "agents": {"deep": {
                "providers": [{"module": "provider-fixture", "instance_id": "fable",
                               "config": {"api_key": "${EXACT_NESTED_KEY}", "model": "deep-model"}}],
            }},
        }},
        "tools": [], "hooks": [],
    }
    original = copy.deepcopy(plan)

    class Info(SimpleNamespace):
        def model_copy(self, *, update):
            return Info(**{**vars(self), **update})

    providers = {
        row["instance_id"]: SimpleNamespace(
            priority=row["config"]["priority"],
            get_info=lambda row=row: Info(id="fixture", defaults={
                "model": row["config"]["model"], "reasoning_effort": row["config"]["reasoning_effort"]},
                config_fields=[]))
        for row in plan["providers"]
    }
    loop = SimpleNamespace(root_provider=None)
    loop._select_provider = lambda mounted: loop.root_provider or min(
        mounted.values(), key=lambda provider: provider.priority)
    capabilities = {"web.provider_credentials_bound": bound}
    coordinator = SimpleNamespace(
        config=plan, session_state={},
        get=lambda name: {"providers": providers, "orchestrator": loop}.get(name),
        get_capability=capabilities.get,
        register_capability=lambda name, value: capabilities.update({name: value}))
    session = SimpleNamespace(session_id="inherited-child", coordinator=coordinator, config=plan)
    runtime = SimpleNamespace(generation=None, queued_inputs=0)
    selection = {"instance": "fable", "model": "exact-pinned-model", "effort": "xhigh"}
    controls = RuntimeControls(session, runtime)
    controls.state_path().parent.mkdir(parents=True)
    controls.state_path().write_text(json.dumps({"selection": selection}))
    try:
        await controls.restore()
        controls.persist()
        effective_path = controls.state_path().with_name("effective-configuration.json")
        effective = json.loads(effective_path.read_text())
        assert effective == (credential_bindings(plan) if bound else plan)
        assert coordinator.config == original
        if bound:
            assert "synthetic-" not in effective_path.read_text()
            assert effective["providers"][1]["config"]["api_key"] == HOST_CREDENTIAL
            assert effective["agents"]["worker"]["providers"][0]["config"]["api_key"] == HOST_CREDENTIAL
        assert effective["agents"]["worker"]["agents"]["deep"]["providers"][0]["config"]["api_key"] == "${EXACT_NESTED_KEY}"
        assert json.loads(controls.state_path().read_text())["selection"] == selection
        current = await controls.perform("configuration.providers")
        assert current["selection"] == current["effective"] == selection
        assert current["pinned"]
        assert loop.root_provider.original is providers["fable"]
        assert [row["config"]["priority"] for row in effective["providers"]] == [20, 1]
        # A same-family account with an identical model is never a restart substitute.
        providers["alternate"].get_info = providers["fable"].get_info
        loop.root_provider = None
        await controls.restore()
        assert loop.root_provider.original is providers["fable"]
        assert controls.selection == selection
        assert providers["fable"].get_info().defaults["model"] == "default-model"
    finally:
        await controls.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('module_default',[None,24000])
async def test_legacy_automatic_budget_restores_without_overriding_module_default(tmp_path,monkeypatch,module_default):
    import json
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    loop=SimpleNamespace(max_iterations=10)
    context=SimpleNamespace(max_tokens=module_default)
    class Coordinator:
        config={'session':{'context':{'module':'context-simple'}},'providers':[]}
        session_state={}
        def get(self,name):return {'orchestrator':loop,'context':context}.get(name)
        def get_capability(self,name):return None
        def register_capability(self,*args):pass
    controls=RuntimeControls(SimpleNamespace(session_id='legacy-session',coordinator=Coordinator()),SimpleNamespace(generation=None,queued_inputs=0))
    controls.state_path().parent.mkdir(parents=True)
    controls.state_path().write_text(json.dumps({'budget':{'maxIterations':-1,'contextTokens':None}}))
    await controls.restore()
    assert context.max_tokens==module_default and loop.max_iterations==-1
    assert (await controls.perform('configuration.inspect'))['plan']['session']['context']['module']=='context-simple'
    controls.persist()
    saved=json.loads(controls.state_path().read_text())
    assert saved['budget'].get('contextTokens')==module_default
    if module_default is None:assert 'contextTokens' not in saved['budget']


@pytest.mark.asyncio
async def test_unpin_preserves_output_budget_and_restores_automatic_provider(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    class Copyable(SimpleNamespace):
        def model_copy(self, update):
            return Copyable(**{**vars(self), **update})
    class Provider:
        request = None
        def __init__(self, model): self.model = model
        def get_info(self): return Copyable(defaults={'model':self.model})
        async def complete(self, request, **kwargs): self.request = request
    default, pinned = Provider('default-model'), Provider('custom-default')
    providers = {'default':default, 'other':pinned}
    loop = SimpleNamespace(root_provider=None,max_iterations=10)
    loop._select_provider = lambda mounted: loop.root_provider or next(iter(mounted.values()))
    context = SimpleNamespace(max_tokens=None)
    class Coordinator:
        config={'session':{},'providers':[]}
        session_state={}
        def get(self,name): return {'orchestrator':loop,'context':context,'providers':providers}.get(name)
        def get_capability(self,name): return None
        def register_capability(self,*args): pass
    coordinator = Coordinator()
    controls = RuntimeControls(SimpleNamespace(session_id='selection-test',coordinator=coordinator),SimpleNamespace(generation=None,queued_inputs=0))
    await controls.perform('provider.select',{'instance':'other','model':'pinned-model','effort':'high'})
    await controls.perform('budget.set',{'maxOutputTokens':321})
    assert (await controls.perform('configuration.providers'))['effective']['model']=='pinned-model'
    await controls.perform('provider.reset')
    result = await controls.perform('configuration.providers')
    assert result['selection'] is None and not result['pinned']
    assert result['effective']=={'instance':'default','model':'default-model','effort':None}
    await loop.root_provider.complete(Copyable(model=None,max_output_tokens=None))
    assert default.request.max_output_tokens==321
    assert default.request.model is None  # Provider default, not the former pin.
    saved=json.loads(controls.state_path().read_text())
    assert saved['selection'] is None and saved['budget']['maxOutputTokens']==321
    # Persisted unpin survives a restart; output budget continues to apply.
    loop.root_provider=None
    restored=RuntimeControls(SimpleNamespace(session_id='selection-test',coordinator=coordinator),SimpleNamespace(generation=None,queued_inputs=0))
    await restored.restore()
    assert not (await restored.perform('configuration.providers'))['pinned']
    await loop.root_provider.complete(Copyable(model=None,max_output_tokens=None))
    assert default.request.max_output_tokens==321
    providers.clear()
    assert (await restored.perform('configuration.providers'))['providers']==[]
    await restored.perform('provider.reset')
    assert loop.root_provider is None
    with pytest.raises(ValueError,match='No provider'):
        await restored.perform('budget.set',{'maxOutputTokens':432,'maxIterations':123})
    assert loop.max_iterations == 10


@pytest.mark.parametrize('identity_field', ['instance_id', 'id'])
def test_team_pulse_redaction_restores_reordered_instances_without_hiding_benign_keys(
        identity_field):
    import copy
    from amplifier_web.runtime_controls import REDACTED

    original = {'tools': [
        {'module': 'tool-team-pulse', identity_field: 'first', 'config': {
            'key': 'SYNTHETIC_PULSE_FIRST', 'url': 'https://first.example.invalid'}},
        {'module': 'tool-team-pulse', identity_field: 'second', 'config': {
            'key': 'SYNTHETIC_PULSE_SECOND', 'url': 'https://second.example.invalid'}},
        {'module': 'tool-keymap-fixture', 'config': {'key': 'F6', 'read_only': True}},
    ]}
    before = copy.deepcopy(original)
    visible = public_config(original)
    visible['tools'][:2] = reversed(visible['tools'][:2])
    edited = copy.deepcopy(visible)
    restored = restore_redactions(visible, original)
    assert restored['tools'] == [original['tools'][1], original['tools'][0], original['tools'][2]]
    assert original == before and visible == edited
    assert [row['config']['key'] for row in visible['tools']] == [REDACTED, REDACTED, 'F6']
    assert [row['config']['url'] for row in visible['tools'][:2]] == [
        'https://second.example.invalid', 'https://first.example.invalid']


@pytest.mark.parametrize('orphan', ['instance', 'field'])
def test_redacted_team_pulse_placeholder_requires_an_exact_prior_value(orphan):
    import copy
    from amplifier_web.runtime_controls import REDACTED

    original = {'tools': [{'module': 'tool-team-pulse', 'instance_id': 'known',
                          'config': {'key': 'SYNTHETIC_PRIOR_KEY'}}]}
    before = copy.deepcopy(original)
    edited = {'tools': [{'module': 'tool-team-pulse', 'instance_id': 'known',
                        'config': {'key': REDACTED}}]}
    if orphan == 'instance':
        edited['tools'][0]['instance_id'] = 'unknown'
    else:
        edited['tools'][0]['config'] = {'new_credential': REDACTED}
    with pytest.raises(ValueError, match='A redacted value cannot be used for a new setting'):
        restore_redactions(edited, original)
    assert original == before


@pytest.mark.asyncio
@pytest.mark.parametrize('surface', ['info', 'configSchema'])
async def test_configuration_providers_preserves_metadata_but_omits_declared_secret_values(
        tmp_path, monkeypatch, surface):
    """Exercise the public runtime control with offline, serialization-real metadata."""
    import copy
    import json
    from types import SimpleNamespace
    from amplifier_web.provider_probe import public as public_provider_metadata
    from amplifier_web.runtime_controls import RuntimeControls

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'unified'))
    fields = [
        {'id': 'connection', 'display_name': 'Connection', 'field_type': 'secret',
         'required': True, 'value': 'SYNTHETIC_METADATA_VALUE',
         'default': 'SYNTHETIC_METADATA_DEFAULT'},
        {'id': 'region', 'display_name': 'Region', 'field_type': 'text',
         'required': False, 'value': 'west', 'default': 'west'},
    ]
    info = {'id': 'fixture', 'display_name': 'Offline fixture',
            'defaults': {'model': 'fixture-model', 'reasoning_effort': 'low'},
            'config_fields': copy.deepcopy(fields)}
    schema = {'fields': copy.deepcopy(fields)}
    before = copy.deepcopy((info, schema))
    calls = []

    class Provider:
        def get_info(self):
            calls.append('get_info')
            return copy.deepcopy(info)

        def get_config_schema(self):
            calls.append('get_config_schema')
            return copy.deepcopy(schema)

        async def list_models(self):
            pytest.fail('Metadata inspection must not query models')

        async def complete(self, *args, **kwargs):
            pytest.fail('Metadata inspection must not invoke inference')

    provider = Provider()
    providers = {'fixture-instance': provider}
    selections = []

    def select(mounted):
        selections.append(mounted)
        assert mounted is providers
        return provider

    loop = SimpleNamespace(root_provider=None, config={}, _select_provider=select)
    plan = {'providers': [{'module': 'provider-fixture', 'instance_id': 'fixture-instance',
                           'config': {'model': 'fixture-model'}}]}
    capabilities = {}
    coordinator = SimpleNamespace(
        config=plan, session_state={},
        get=lambda name: {'providers': providers, 'orchestrator': loop}.get(name),
        get_capability=capabilities.get,
        register_capability=capabilities.__setitem__)
    controls = RuntimeControls(
        SimpleNamespace(session_id='synthetic-provider-metadata', coordinator=coordinator, config=plan),
        SimpleNamespace(generation=None, queued_inputs=0))
    try:
        result = await controls.perform('configuration.providers')
        assert calls == ['get_info', 'get_config_schema', 'get_info']
        assert selections == [providers]
        assert (info, schema) == before
        assert result['effective'] == {
            'instance': 'fixture-instance', 'model': 'fixture-model', 'effort': 'low'}
        assert result['delegationRouting'] == {
            'modelInheritance': 'bundle', 'crossProviderRestriction': 'not_enforced',
            'resolverActive': False}
        assert len(result['providers']) == 1
        row = result['providers'][0]
        assert row['id'] == 'fixture-instance' and row['module'] == 'provider-fixture'
        expected = copy.deepcopy(info if surface == 'info' else schema)
        field_key = 'config_fields' if surface == 'info' else 'fields'
        del expected[field_key][0]['value']
        del expected[field_key][0]['default']
        # Match the existing semantic secret-metadata rule, independently
        # spelling out the expected safe record rather than only using an oracle.
        assert public_provider_metadata(info if surface == 'info' else schema) == expected
        assert row[surface] == expected
        assert 'SYNTHETIC_METADATA_' not in json.dumps(row[surface])
    finally:
        await controls.close()


@pytest.mark.parametrize('changed', ['module', 'source', 'duplicate'])
def test_redacted_restore_cannot_borrow_another_source_or_ambiguous_identity(changed):
    import copy
    from amplifier_web.runtime_controls import REDACTED
    old = {'tools': [{'module': 'tool-team-pulse', 'id': 'pulse',
                     'source': 'https://example.invalid/original',
                     'config': {'key': 'SYNTHETIC_PRIOR'}}]}
    edited = copy.deepcopy(old)
    edited['tools'][0]['config']['key'] = REDACTED
    if changed == 'duplicate':
        old['tools'].append(copy.deepcopy(old['tools'][0]))
    else:
        edited['tools'][0][changed] = 'different'
    with pytest.raises(ValueError, match='redacted value cannot be used'):
        restore_redactions(edited, old)


@pytest.mark.parametrize('changed', ['unchanged', 'module', 'source'])
def test_dictionary_module_restore_requires_the_same_owner(changed):
    import copy
    old = {'session': {'context': {'module': 'context-fixture', 'source': 'original-source',
                                  'config': {'api_key': 'SYNTHETIC_CONTEXT_KEY'}}}}
    before = copy.deepcopy(old)
    edited = public_config(old)
    if changed == 'unchanged':
        assert restore_redactions(edited, old) == old
    else:
        edited['session']['context'][changed] = 'different'
        with pytest.raises(ValueError, match='redacted value cannot be used'):
            restore_redactions(edited, old)
    assert old == before


def test_nested_configuration_arrays_and_null_round_trip_without_inventing_missing_values():
    import copy
    from amplifier_web.runtime_controls import REDACTED
    old = {'tools': [{'module': 'tool-fixture', 'config': {
        'headers': [{'authorization': 'SYNTHETIC_HEADER', 'label': 'first'},
                    {'authorization': None, 'label': 'second'}],
        'routes': [{'module': 'backend', 'authorization': 'SYNTHETIC_ROUTE_A'},
                   {'module': 'backend', 'authorization': 'SYNTHETIC_ROUTE_B'}],
        'api_key': None, 'modes': ['one', 'two']}}]}
    before = copy.deepcopy(old)
    visible = public_config(old)
    assert visible['tools'][0]['config']['api_key'] == REDACTED
    assert restore_redactions(visible, old) == before == old
    visible['tools'][0]['config']['new_secret'] = REDACTED
    with pytest.raises(ValueError, match='redacted value cannot be used'):
        restore_redactions(visible, old)


def test_secret_metadata_omission_does_not_change_generic_tool_result_data():
    from amplifier_core import ToolResult
    payload = {'field_type': 'secret', 'default': 'user-authored example', 'value': 'user text'}
    result = ToolResult(success=True, output=payload)
    # This is the same serializer used by RuntimeControls.invoke for arbitrary results.
    assert public_config(result)['output'] == payload
    assert public_config({'fields': [payload]}, metadata=True) == {
        'fields': [{'field_type': 'secret'}]}


@pytest.mark.parametrize('agent_name', ['config', 'module', 'worker'])
def test_agent_names_do_not_turn_nested_mounts_into_unbound_config_data(agent_name):
    original = {'agents': {agent_name: {'providers': [{
        'module': 'provider-fixture', 'instance_id': 'p', 'source': 'original-source',
        'config': {'api_key': 'SYNTHETIC_AGENT_KEY'}}]}}}
    visible = public_config(original)
    assert restore_redactions(visible, original) == original
    visible['agents'][agent_name]['providers'][0]['source'] = 'different-source'
    with pytest.raises(ValueError, match='redacted value cannot be used'):
        restore_redactions(visible, original)
