"""Saved connections must enter strict preparation with their own source identity."""
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.host.config import HostConfig
from amplifier_web.host.session import _apply_settings


def configured(tmp_path, rows, **settings):
    return HostConfig(tmp_path / 'app', tmp_path, {
        **settings, 'config': {'providers': rows},
    }, tmp_path / 'registry')


def bundle(rows):
    return SimpleNamespace(providers=rows, tools=[], hooks=[], session={}, agents={})


def test_saved_named_connection_is_source_qualified_without_account_fallback(tmp_path):
    source = 'git+https://github.com/microsoft/amplifier-module-provider-openai@reviewed'
    declared = {'module': 'provider-openai', 'instance_id': 'bundle-account',
                'source': source, 'config': {'api_key': '${BUNDLE_KEY}', 'priority': 1}}
    saved = {'module': 'provider-openai', 'id': 'chosen-account',
             'config': {'api_key': '${CHOSEN_KEY}', 'default_model': 'chosen-model'}}
    original = copy.deepcopy(saved)
    result = _apply_settings(bundle([declared]), configured(tmp_path, [saved]))
    chosen = next(row for row in result.providers if row['instance_id'] == 'chosen-account')
    # Foundation skips source-less declarations before invoking its source
    # resolver. Every configured account must carry the source into that step.
    assert chosen.get('source') == source
    assert chosen['config'] == original['config']
    assert len(result.providers) == 2
    assert result.providers[0]['config']['api_key'] == '${BUNDLE_KEY}'
    assert saved == original


def test_known_connection_without_bundle_provider_uses_catalog_source(tmp_path):
    saved = {'module': 'provider-openai', 'id': 'chosen', 'config': {'api_key': '${CHOSEN_KEY}'}}
    result = _apply_settings(bundle([]), configured(tmp_path, [saved]))
    assert result.providers[0]['source'] == 'git+https://github.com/microsoft/amplifier-module-provider-openai@main'
    assert result.providers[0]['config'] == saved['config']


@pytest.mark.parametrize('module,hook', [
    ('provider-jev', 'hooks-jev-decisions'),
    ('provider-local-decisions', 'hooks-local-decisions'),
])
def test_settings_created_decision_account_has_source_before_hook_projection(tmp_path, module, hook):
    from amplifier_web.decision_accounts import DecisionAccounts, logical_plan
    from amplifier_web.host.session import module_source
    from amplifier_web.provider_isolation import ProviderIsolation

    saved = {'module': module, 'id': 'configured-decision-account', 'config': {
        'api_key': 'synthetic-unused', 'chat_enabled': False,
        'decisions': {'enabled': True, 'backends': {
            'ui-browser-configured': {'model': 'fixture', 'enabled': True},
            'ui-computer-configured': {'model': 'fixture', 'enabled': True},
        }}}}
    original = copy.deepcopy(saved)
    config = configured(tmp_path, [saved])
    result = _apply_settings(bundle([]), config)
    adapter = DecisionAccounts({'providers': result.providers}, ProviderIsolation(),
        lambda name, source: module_source(config, False, name, source),
        definitions={module: hook})
    assert not adapter.failures, adapter.failures
    assert len(adapter.expected) == 1
    projected = adapter.plan['hooks'][0]
    assert projected['module'] == hook
    assert projected['source'] == f'git+https://github.com/microsoft/amplifier-module-{module}@main'
    assert projected['config']['decisions'] == saved['config']['decisions']
    assert logical_plan(adapter.plan, definitions={module: hook})['providers'][0]['config'] == original['config']
    assert saved == original


@pytest.mark.parametrize('setting', ['sources', 'overrides'])
def test_explicit_module_source_qualifies_missing_account(tmp_path, setting):
    selected = 'file:///explicit/provider'
    settings = {'sources': {'modules': {'provider-custom': selected}}} if setting == 'sources' else {
        'overrides': {'provider-custom': {'source': selected}}}
    saved = {'module': 'provider-custom', 'id': 'chosen', 'config': {'api_key': '${CHOSEN_KEY}'}}
    result = _apply_settings(bundle([]), configured(tmp_path, [saved], **settings))
    assert result.providers[0]['source'] == selected


def test_explicit_account_sources_are_not_replaced_or_used_ambiguously(tmp_path):
    rows = [
        {'module': 'provider-openai', 'id': 'first', 'source': 'file:///first', 'config': {'api_key': '${FIRST_KEY}'}},
        {'module': 'provider-openai', 'id': 'second', 'source': 'file:///second', 'config': {'api_key': '${SECOND_KEY}'}},
        {'module': 'provider-openai', 'id': 'unresolved', 'config': {'api_key': '${THIRD_KEY}'}},
        {'module': 'provider-unknown', 'id': 'unknown', 'config': {}},
    ]
    result = _apply_settings(bundle([]), configured(tmp_path, rows))
    by_id = {row['instance_id']: row for row in result.providers}
    assert by_id['first']['source'] == 'file:///first'
    assert by_id['second']['source'] == 'file:///second'
    assert 'source' not in by_id['unresolved']
    assert 'source' not in by_id['unknown']
    assert all(row['config'] == by_id[row['id']]['config'] for row in rows)


async def test_snapshot_sources_do_not_inherit_current_connection_defaults(tmp_path):
    from amplifier_foundation import Bundle
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.host.session import compose_configured_bundle

    rows = [{'module': 'provider-openai', 'instance_id': 'saved', 'config': {}}]
    snapshot = Bundle(name='saved', version=SNAPSHOT_VERSION, providers=copy.deepcopy(rows))
    config = configured(tmp_path, [{'module': 'provider-openai', 'id': 'current', 'source': 'file:///new'}])
    result = await compose_configured_bundle(None, snapshot, config)
    assert result.providers == rows
