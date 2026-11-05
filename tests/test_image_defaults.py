"""Legacy intent migration, not host-owned provider ranking or image setup."""
import copy
import json

import pytest

from amplifier_web.image_migration import apply_legacy_image_intent
from amplifier_web.preferences import SettingsStore
from amplifier_web.shared_settings import read_settings


async def test_retired_automatic_probe_refuses_before_reading_credentials(tmp_path, monkeypatch):
    import runpy
    import sys
    from pathlib import Path

    class NoCredentials:
        def read(self, *args):
            pytest.fail('Retired probe must not read credentials')

    monkeypatch.setattr(sys, 'stdin', NoCredentials())
    probe = runpy.run_path(str(Path(__file__).parents[1] / 'scripts/work_profile/automatic_image_acceptance.py'))
    target = tmp_path / 'no-probe-output'
    with pytest.raises(RuntimeError, match='replaced by shared image routing'):
        await probe['main'](target)
    assert not target.exists()


def provider(identity='blue', image=None, **config):
    value = {'module': 'provider-fixture', 'id': identity, 'config': {
        'default_model': 'kept-chat', 'reasoning_effort': 'high',
        'api_key': '${FIXTURE_IMAGE_CREDENTIAL}', 'priority': 7,
        'opaque': {'keep': True}, **config,
    }}
    if image is not None:
        value['config']['image_generation'] = copy.deepcopy(image)
    return value


def plan_for(providers, **tool_config):
    return {'providers': copy.deepcopy(providers), 'tools': [
        {'module': 'tool-image', 'config': copy.deepcopy(tool_config)},
        {'module': 'tool-fixture', 'config': {'keep': True}},
    ], 'session': {'root_bundle': 'fixture-root'}}


@pytest.fixture(autouse=True)
def no_provider_probing(monkeypatch):
    from amplifier_web import provider_probe
    from amplifier_web.setup import SetupManager

    def forbidden(*args, **kwargs):
        pytest.fail('Migration must not construct, probe, rank, or invoke providers')

    async def forbidden_async(*args, **kwargs):
        forbidden()

    for name in ('provider_class', 'construct_provider', 'construct_schema_provider'):
        monkeypatch.setattr(provider_probe, name, forbidden)
    monkeypatch.setattr(provider_probe, 'query', forbidden_async)
    monkeypatch.setattr(SetupManager, 'probe', forbidden_async)
    monkeypatch.setattr(SetupManager, 'cached_probe', forbidden_async)


@pytest.mark.parametrize('settings', [{}, {'image_generation': {'mode': 'automatic'}}])
def test_fresh_plan_does_not_probe_rank_or_enable_a_provider(settings):
    plan = plan_for([
        provider('later', priority=9),
        provider('first', priority=1),
        {'module': 'provider-chat-only', 'config': {'priority': 0}},
        {**provider('disabled', priority=-1), 'enabled': False},
    ])
    before, source = copy.deepcopy(plan), copy.deepcopy(settings)
    receipt = apply_legacy_image_intent(plan, settings)
    assert settings == source
    assert plan['providers'] == before['providers']
    assert plan['tools'] == before['tools']
    assert plan['session'] == before['session']
    if settings:
        assert receipt == {'version': 1, 'sourceMode': 'automatic',
                           'source': [], 'result': 'preserved'}
        assert plan == {**before, 'image_routing_migration': receipt}
    else:
        assert receipt is None and plan == before


@pytest.mark.parametrize('mode', ['off', 'selected', 'automatic'])
def test_absent_image_behavior_is_not_injected_by_migration(mode):
    plan = plan_for([provider(image={'enabled': True, 'model': 'pinned'})])
    plan['tools'] = [plan['tools'][1]]
    settings = {'image_generation': {'mode': mode},
                'web_bundles': {'excluded': ['imagegen']}}
    before = copy.deepcopy((plan, settings))
    assert apply_legacy_image_intent(plan, settings) is None
    assert (plan, settings) == before


@pytest.mark.parametrize('scope', ['global', 'project', 'local'])
@pytest.mark.parametrize('intent', ['off', 'pin', 'backend'])
def test_scoped_intent_preserves_effective_pin_and_all_source_files(tmp_path, scope, intent):
    store = SettingsStore(tmp_path / 'app', shared_home=tmp_path / 'shared')
    base = {'image_generation': {'mode': 'selected'}, 'config': {
        'providers': [provider(image={'enabled': True, 'id': 'image-blue',
                                     'model': 'global-image', 'timeout': 420})],
        'tools': [{'module': 'tool-image', 'config': {'allow_paid': True, 'timeout': 420}}],
    }, 'routing': {'matrix': 'kept'}, 'web_bundles': {'excluded': ['other-feature']}}
    store.update(tmp_path, 'global', lambda settings: settings.update(copy.deepcopy(base)))
    for other in ('project', 'local'):
        store.update(tmp_path, other, lambda settings, other=other:
                     settings.update(fixture_scope=other))

    def set_intent(settings):
        if intent == 'off':
            settings['image_generation'] = {'mode': 'off'}
        elif intent == 'pin':
            rows = settings.setdefault('config', {}).setdefault('providers', [])
            row = next((row for row in rows if row.get('id') == 'blue'), None)
            if row is None:
                row = {'id': 'blue', 'module': 'provider-fixture', 'config': {}}
                rows.append(row)
            row['config'].setdefault('image_generation', {}).update(model=scope + '-image')
        else:
            settings.setdefault('config', {})['tools'] = [
                {'module': 'tool-image', 'config': {'backend': 'explicit-backend',
                                                   'allow_paid': True, 'timeout': 420}}]

    store.update(tmp_path, scope, set_intent)
    paths = [store.path(tmp_path, name) for name in ('global', 'project', 'local')]
    original_bytes = {path: path.read_bytes() for path in paths}
    effective = read_settings(tmp_path, shared_home=store.shared_home)
    original_settings = copy.deepcopy(effective)
    plan = copy.deepcopy(effective['config'])
    original_providers = copy.deepcopy(plan['providers'])
    receipt = apply_legacy_image_intent(plan, effective)
    image = plan['providers'][0]['config']['image_generation']
    tool = plan['tools'][0]['config']

    assert receipt['version'] == 1
    assert effective == original_settings
    assert {path: path.read_bytes() for path in paths} == original_bytes
    assert plan['providers'] == original_providers
    assert plan['providers'][0]['config']['default_model'] == 'kept-chat'
    assert tool['timeout'] == 420
    if intent == 'off':
        assert tool['allow_paid'] is False
        assert receipt['result'] == 'disabled'
        assert image['model'] == 'global-image'
    elif intent == 'pin':
        assert tool['backend'] == 'image-blue' and tool['allow_paid'] is True
        assert image['model'] == scope + '-image'
        assert receipt['result'] == 'legacy_backend_preserved'
    else:
        assert tool == original_settings['config']['tools'][0]['config']
        assert image['model'] == 'global-image'
        assert receipt['result'] == 'explicit_backend_preserved'


@pytest.mark.parametrize('mode', [None, 'automatic', 'selected', 'off'])
def test_explicit_tool_denial_is_never_broadened(mode):
    plan = plan_for([provider(image={'enabled': True, 'id': 'image-blue', 'model': 'pin'})],
                    allow_paid=False, backend='explicit-backend', timeout=420)
    settings = {} if mode is None else {'image_generation': {'mode': mode}}
    before = copy.deepcopy(plan)
    receipt = apply_legacy_image_intent(plan, settings)
    assert receipt['result'] == 'disabled'
    assert plan['tools'] == before['tools']
    assert plan['providers'] == before['providers']


@pytest.mark.parametrize('mode,rows', [
    ('selected', [provider()]),
    (None, [provider(image={'enabled': False})]),
    ('automatic', [provider(image={'enabled': False}), provider('other')]),
    (None, [provider(image={'enabled': True, 'model': 'one'}),
            provider('other', {'enabled': True, 'model': 'two'})]),
    ('automatic', [provider(image={'enabled': True, 'model': 'one'}),
                  provider('other', {'enabled': True, 'model': 'two'})]),
    ('selected', [{**provider(image={'enabled': True, 'model': 'pin'}), 'enabled': False}]),
    (None, [{**provider(image={'enabled': True, 'model': 'pin'}), 'enabled': False},
            provider('other')]),
], ids=['missing-selection', 'disabled-only', 'automatic-deselection',
        'multiple-pins', 'automatic-multiple-pins', 'disabled-selection', 'disabled-pin'])
def test_ambiguous_or_unavailable_legacy_selection_fails_closed(mode, rows):
    plan = plan_for(rows)
    settings = {} if mode is None else {'image_generation': {'mode': mode}}
    before = copy.deepcopy((plan, settings))
    receipt = apply_legacy_image_intent(plan, settings)
    assert plan['tools'][0]['config']['routing_blocked'] is True
    assert 'backend' not in plan['tools'][0]['config']
    assert receipt['result'] == 'ambiguous_requires_review'
    assert plan['providers'] == before[0]['providers']
    assert settings == before[1]


@pytest.mark.parametrize('image', [None, {'enabled': True, 'model': 'pin'}])
def test_unknown_legacy_mode_cannot_authorize_a_pin_or_new_account(image):
    plan = plan_for([provider(image=image)])
    settings = {'image_generation': {'mode': 'future-unsupported-mode'}}
    before = copy.deepcopy((plan, settings))
    receipt = apply_legacy_image_intent(plan, settings)
    assert plan['tools'][0]['config']['routing_blocked'] is True
    assert 'backend' not in plan['tools'][0]['config']
    assert receipt['result'] == 'unsupported_legacy_mode'
    assert plan['providers'] == before[0]['providers']
    assert settings == before[1]


def test_migration_receipt_retains_only_image_intent_and_exact_instance():
    row = provider(image={'enabled': True, 'id': 'custom-backend', 'model': 'pinned',
                          'api_key': 'hidden-image-key', 'opaque': {'token': 'hidden-token'}},
                   api_key='hidden-provider-key')
    row['instance_id'] = 'mounted-blue'
    plan = plan_for([row])
    before = copy.deepcopy(plan['providers'])
    receipt = apply_legacy_image_intent(plan, {'image_generation': {'mode': 'selected'}})
    assert receipt == {'version': 1, 'sourceMode': 'selected', 'result': 'legacy_backend_preserved',
                       'source': [{'providerInstance': 'mounted-blue',
                                   'image': {'enabled': True, 'id': 'custom-backend', 'model': 'pinned'}}]}
    assert 'hidden-' not in json.dumps(receipt)
    assert plan['providers'] == before
    receipt['source'][0]['image']['model'] = 'not-the-provider-model'
    assert plan['providers'] == before


def test_automatic_mode_cannot_discard_a_single_saved_pin():
    rows = [provider(image={'enabled': True, 'id': 'saved-blue', 'model': 'pin'}),
            provider('other', {'enabled': False})]
    plan = plan_for(rows, allow_paid=True)
    before = copy.deepcopy(plan['providers'])
    receipt = apply_legacy_image_intent(plan, {'image_generation': {'mode': 'automatic'}})
    assert plan['tools'][0]['config']['backend'] == 'saved-blue'
    assert receipt['result'] == 'legacy_backend_preserved'
    assert plan['providers'] == before


@pytest.mark.parametrize('disabled_by', ['module-override', 'instance-override', 'configurator'])
@pytest.mark.parametrize('mode', [None, 'automatic'])
@pytest.mark.parametrize('other_pinned', [False, True])
@pytest.mark.parametrize('pin_source', ['row', 'module-override', 'instance-override'])
def test_filtered_provider_keeps_unavailable_image_intent(
        tmp_path, disabled_by, mode, other_pinned, pin_source):
    from types import SimpleNamespace
    from amplifier_foundation import Bundle
    from amplifier_web.host.session import _apply_settings

    rows = [
        provider(image={'enabled': True, 'id': 'saved-blue', 'model': 'pinned',
                        'opaque': 'hidden-image-value'}),
        {'module': 'provider-other', 'id': 'other', 'config': {
            'default_model': 'other-chat',
            **({'image_generation': {'enabled': True, 'id': 'other-image', 'model': 'other-pin'}}
               if other_pinned else {}),
        }},
    ]
    settings = {} if mode is None else {'image_generation': {'mode': mode}}
    if disabled_by == 'configurator':
        settings['configurator'] = {'disabled': {'providers': ['blue']}}
    else:
        key = 'provider-fixture' if disabled_by == 'module-override' else 'blue'
        settings['overrides'] = {key: {'enabled': False}}
    if pin_source != 'row':
        key = 'provider-fixture' if pin_source == 'module-override' else 'blue'
        settings.setdefault('overrides', {}).setdefault(key, {})['config'] = {
            'image_generation': rows[0]['config'].pop('image_generation'),
            'api_key': 'hidden-override-credential',
        }
    original = copy.deepcopy((rows, settings))
    bundle = Bundle(name='fixture-root', providers=copy.deepcopy(rows),
                    tools=[{'module': 'tool-image', 'config': {'allow_paid': True}}])
    config = SimpleNamespace(settings=settings, providers=[], workspace=tmp_path)
    loaded = _apply_settings(bundle, config)
    assert [row['id'] for row in loaded.providers] == ['other']
    retained = loaded._host_unavailable_image_providers
    assert len(retained) == 1 and retained[0]['enabled'] is False
    assert retained[0]['config'] == {
        'image_generation': {'enabled': True, 'id': 'saved-blue', 'model': 'pinned'}
    }
    assert 'hidden-' not in json.dumps(retained) and 'api_key' not in json.dumps(retained)
    plan = loaded.to_mount_plan()
    receipt = apply_legacy_image_intent(plan, settings, unavailable_providers=retained)
    assert plan['tools'][0]['config']['routing_blocked'] is True
    assert 'backend' not in plan['tools'][0]['config']
    assert receipt['result'] == 'ambiguous_requires_review'
    assert any(row['providerInstance'] == 'blue' for row in receipt['source'])
    assert [row['id'] for row in plan['providers']] == ['other']
    migrated = copy.deepcopy(plan)
    assert apply_legacy_image_intent(plan, settings, unavailable_providers=retained) == receipt
    assert plan == migrated
    assert (rows, settings) == original


@pytest.mark.asyncio
@pytest.mark.parametrize('disabled_pin', [True, False], ids=['disabled-override-pin', 'ordinary-unpinned'])
async def test_prepare_manager_forwards_filtered_override_image_intent(tmp_path, monkeypatch, disabled_pin):
    """Real ordinary-root wiring, stopped immediately before module mounting."""
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from amplifier_foundation import Bundle
    from amplifier_web import provider_environment
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.host import session as host

    workspace, home, shared = tmp_path / 'workspace', tmp_path / 'app', tmp_path / 'shared'
    workspace.mkdir()
    monkeypatch.chdir(workspace)  # Restore cwd after prepare_manager changes it.
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    root, behavior = workspace / 'ordinary.yaml', workspace / 'image.yaml'
    root.write_text(json.dumps({
        'bundle': {'name': 'fixture-root'},
        'session': {'orchestrator': {'module': 'loop-streaming'},
                    'context': {'module': 'context-simple'}},
        'providers': [provider(api_key='hidden-root-credential'),
                      {**provider('other', api_key='hidden-other-credential'), 'module': 'provider-other'}],
    }))
    behavior.write_text(json.dumps({
        'bundle': {'name': 'fixture-image'},
        'tools': [{'module': 'tool-image', 'config': {'allow_paid': True}}],
    }))
    settings = {
        'bundle': {'active': str(root), 'app': [str(behavior)]},
        'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)},
        'image_generation': {'mode': 'automatic'},
    }
    if disabled_pin:
        # Neither provider row carries a pin; A's selection exists only in its override.
        settings['overrides'] = {'blue': {'enabled': False, 'config': {
            'api_key': 'hidden-override-credential',
            'image_generation': {'enabled': True, 'id': 'saved-blue', 'model': 'pinned',
                                 'api_key': 'hidden-image-key', 'opaque': {'token': 'hidden-image-token'}},
        }}}
    store = SettingsStore(home, shared_home=shared)
    store.update(workspace, 'global', lambda values: values.update(copy.deepcopy(settings)))
    original_bytes = {path: path.read_bytes() for path in (root, behavior, store.path(workspace, 'global'))}

    # Follow mounted_host's inert loop/job/child boundaries, but do not replace
    # Foundation, the registry, load_config, composition, or image migration.
    runtime = SimpleNamespace(session_id='image-host-wiring', observer=None)
    jobs = SimpleNamespace(rows=[], close=Mock())
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.runtime',
                        SimpleNamespace(Runtime=lambda: runtime))
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.job_store',
                        SimpleNamespace(JobStore=lambda _: jobs))
    monkeypatch.setitem(sys.modules, 'amplifier_web.host.children',
                        SimpleNamespace(install_children=AsyncMock(), StandaloneHostAdapter=lambda: object()))

    class BeforeSessionCreation(Exception):
        pass

    prepared = SimpleNamespace(create_session=AsyncMock(side_effect=BeforeSessionCreation))
    preparations, schema_calls = [], []

    async def prepare_without_install(bundle, **kwargs):
        assert kwargs['strict'] is True and kwargs['install_deps'] is False
        preparations.append(bundle)
        prepared.mount_plan = bundle.to_mount_plan()
        return prepared

    async def inert_schema(module, **kwargs):
        schema_calls.append(module)
        return {'fields': [{'id': 'api_key', 'field_type': 'secret', 'required': True}]}

    monkeypatch.setattr(Bundle, 'prepare', prepare_without_install)
    monkeypatch.setattr(provider_environment, 'provider_config_schema', inert_schema)
    with pytest.raises(BeforeSessionCreation):
        await host.prepare_manager(workspace, runtime=runtime, qualification_readonly=True)

    prepared.create_session.assert_awaited_once()
    jobs.close.assert_called_once()
    assert len(preparations) == 1
    assert preparations[0].name == 'fixture-root' and not host.is_snapshot(preparations[0])
    plan = prepared.mount_plan
    assert plan['bundle_name'] == str(root)
    assert plan['session']['orchestrator']['module'] == 'loop-live'
    assert [row['id'] for row in plan['providers']] == (['other'] if disabled_pin else ['blue', 'other'])
    assert schema_calls == (['provider-other'] if disabled_pin else ['provider-fixture', 'provider-other'])
    image = next(row['config'] for row in plan['tools'] if row['module'] == 'tool-image')
    assert image['allow_paid'] is True and 'backend' not in image
    receipt = plan['image_routing_migration']
    if disabled_pin:
        assert image['routing_blocked'] is True
        assert receipt == {
            'version': 1, 'sourceMode': 'automatic', 'result': 'ambiguous_requires_review',
            'source': [{'providerInstance': 'blue',
                        'image': {'enabled': True, 'id': 'saved-blue', 'model': 'pinned'}}],
            'scopes': [{'mode': 'automatic', 'scope': 'global'}],
        }
    else:
        assert 'routing_blocked' not in image
        assert receipt == {
            'version': 1, 'sourceMode': 'automatic', 'source': [], 'result': 'preserved',
            'scopes': [{'mode': 'automatic', 'scope': 'global'}],
        }
    assert 'hidden-' not in json.dumps(receipt) and 'api_key' not in json.dumps(receipt)
    assert {path: path.read_bytes() for path in original_bytes} == original_bytes


@pytest.mark.parametrize('mode,image,tool', [
    ('selected', {'enabled': True, 'id': 'custom-backend', 'model': 'pin'}, {}),
    (None, {'enabled': True, 'model': 'auto'}, {}),
    ('off', {'enabled': True, 'model': 'pin'}, {'backend': 'explicit'}),
    ('selected', {'enabled': True, 'model': 'pin'}, {'allow_paid': False}),
    ('selected', {'enabled': True, 'model': 'pin'}, {'backend': 'explicit'}),
    ('automatic', {'enabled': False}, {}),
    ('future-unsupported-mode', None, {}),
], ids=['selected-pin', 'implicit-pin', 'off', 'paid-denial', 'explicit-backend',
        'ambiguous', 'unsupported'])
def test_migration_is_exactly_idempotent_including_receipt(mode, image, tool):
    plan = plan_for([provider(image=image)], **tool)
    settings = {} if mode is None else {'image_generation': {'mode': mode}}
    source = copy.deepcopy(settings)
    receipt = copy.deepcopy(apply_legacy_image_intent(plan, settings))
    migrated = copy.deepcopy(plan)
    for _ in range(3):
        assert apply_legacy_image_intent(plan, settings) == receipt
        assert plan == migrated
        assert settings == source
