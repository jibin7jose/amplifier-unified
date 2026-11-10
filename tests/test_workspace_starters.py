"""Outcome tests: definitions persist, exact plans provision, existing work survives."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.workspace_starters import StarterCatalog, definition
from amplifier_web.workspace_starters import DEVELOPMENT_INSTRUCTIONS, SCRATCH_TEMPLATE
from amplifier_web import workspace_provisioning as provisioning
from amplifier_web.bundle_selection import defaults


@pytest.fixture
async def service(tmp_path):
    folder = tmp_path / 'initial'
    folder.mkdir()
    value = AppService(tmp_path / 'data', workspace=folder)
    yield value
    await value.close()


def test_provided_markdown_soft_wraps_prose_only():
    for text in (DEVELOPMENT_INSTRUCTIONS, SCRATCH_TEMPLATE):
        for block in text.strip().split('\n\n'):
            if block.startswith(('#', '@', '|', '    ')):
                continue
            assert '\n' not in block, 'Provided prose paragraphs must let the viewer wrap naturally.'
    assert '@./SCRATCH.md\n' in DEVELOPMENT_INSTRUCTIONS
    assert '    workspace/\n' in DEVELOPMENT_INSTRUCTIONS


async def test_workspace_defaults_inspect_targets_browsed_not_selected_workspace(service):
    original = service.state['selectedWorkspaceId']
    _, row = await create(service, 'development', 'Independent defaults')
    await service.dispatch('bundle.default', {'scope':'workspace','workspace':row['path'],'bundle':'work'})
    await service.dispatch('workspace.select', {'id': original})
    result = await service.dispatch('workspace.bundleDefault.inspect', {'workspaceId':row['id']})
    assert result['result']['workspace'] == 'work'
    assert result['result']['workspacePath'] == row['path']
    with pytest.raises(AppError, match='registered'):
        await service.dispatch('workspace.bundleDefault.inspect', {'workspaceId':'unregistered'})


async def settle(service):
    for _ in range(300):
        row = next(w for w in service.state['workspaces'] if w['id'] == service.state['selectedWorkspaceId'])
        if row.get('setup', {}).get('status') not in {'pending', 'running'}:
            return row
        await asyncio.sleep(.01)
    raise AssertionError('Starter setup did not settle')


async def create(service, starter='development', name='My work'):
    prepared = await service.dispatch('workspace.prepare', {'name': name, 'starterId': starter})
    plan = prepared['result']
    result = await service.dispatch('workspace.create', {'planId': plan['planId'], 'fromDraft': True}, command_id='create-'+name)
    return result, await settle(service)


def test_catalog_duplicate_edit_conflict_delete_restart(tmp_path):
    catalog = StarterCatalog(tmp_path)
    assert [row['id'] for row in catalog.listing()['items']] == ['blank', 'development', 'amplifier-development']
    copy = catalog.command('workspace.starters.duplicate', {'id': 'development'}, 'duplicate')
    assert catalog.command('workspace.starters.duplicate', {'id': 'development'}, 'duplicate') == copy
    fields = {key: value for key, value in copy.items() if key not in {'id', 'revision', 'builtIn'}}
    fields.update(name='My starter', bundle='anchors')
    args = {'id': copy['id'], 'expectedRevision': 1, 'starter': fields}
    updated = catalog.command('workspace.starters.save', args, 'edit')
    assert StarterCatalog(tmp_path).snapshot(copy['id']) == updated
    assert catalog.snapshot('development')['bundle'] == ''
    with pytest.raises(ValueError, match='changed'):
        catalog.command('workspace.starters.save', args, 'stale-edit')
    with pytest.raises(ValueError, match='Built-in'):
        catalog.command('workspace.starters.save', {**args, 'id': 'development'}, 'builtin-edit')
    catalog.command('workspace.starters.remove', {'id': copy['id'], 'expectedRevision': 2}, 'remove')
    assert len(StarterCatalog(tmp_path).listing()['items']) == 3


def test_only_preconfigured_bundles_offered_and_sources_correct(tmp_path):
    from amplifier_web.host.config import PRECONFIGURED_BUNDLES, load_config
    config = load_config(tmp_path, home=tmp_path / 'app')
    expected = {'anchors', 'anchors-amp-dev', 'work', 'work-amp-dev'}
    assert set(PRECONFIGURED_BUNDLES) == expected
    assert config.registrations['anchors'].endswith('amplifier-foundation@main#subdirectory=bundles/anchors/bundle.md')
    assert config.registrations['anchors-amp-dev'].endswith('amplifier-foundation@main#subdirectory=bundles/anchors-amp-dev/bundle.md')
    assert config.registrations['work'].endswith('amplifier-bundle-work@main#subdirectory=bundle.md')
    assert config.registrations['work-amp-dev'] == 'git+https://github.com/microsoft/amplifier-bundle-work@main#subdirectory=bundles/work-amp-dev.md'
    catalog = StarterCatalog(tmp_path)
    assert {row['value'] for row in catalog.listing()['bundles']} == expected
    assert catalog.snapshot('amplifier-development')['bundle'] == 'work-amp-dev'
    assert {row['id']: row['revision'] for row in catalog.listing()['items']} == {
        'blank': 1, 'development': 7, 'amplifier-development': 9}
    assert catalog.snapshot('development')['scratch'] is True
    assert catalog.snapshot('blank')['scratch'] is False
    assert catalog.snapshot('amplifier-development')['rootGit'] is True
    assert catalog.snapshot('development')['rootGit'] is False
    assert catalog.snapshot('blank')['rootGit'] is False
    for name in ('amplifier-dev', 'foundation', 'exp-delegation', 'git+https://example.test/root'):
        with pytest.raises(ValueError, match='configured standalone'):
            catalog.command('workspace.starters.save', {'starter': {'name': 'Invalid', 'bundle': name}}, 'invalid-'+name)


@pytest.mark.parametrize('app_bundle,shared_bundle,expected', [
    ('work-amp-dev', None, 'work-amp-dev'),
    ('anchors', None, 'anchors'),
    ('work', None, 'work'),
    (None, 'work-amp-dev', 'work-amp-dev'),
    (None, 'anchors', 'anchors'),
    (None, 'work', 'work'),
    ('anchors', 'work-amp-dev', 'anchors'),
    (None, None, 'work-amp-dev'),
])
async def test_amplifier_starter_created_root_respects_saved_defaults_or_fallback(
        service, local_amplifier_repositories, app_bundle, shared_bundle, expected):
    from amplifier_web.shared_settings import read_yaml
    initial = service.state['settings']['workspace']
    await service.dispatch('session.create', {'bundle': 'anchors'})
    existing = service._session()['id']
    # A selected workspace's explicit choice must not become new-workspace intent.
    await service.dispatch('bundle.default', {'scope': 'workspace', 'bundle': 'work'})
    local_settings = Path(initial) / '.amplifier/settings.local.yaml'
    before = local_settings.read_bytes()
    await service.dispatch('bundle.default', {'scope': 'shared', 'bundle': shared_bundle})
    await service.dispatch('bundle.default', {'scope': 'app', 'bundle': app_bundle})
    fallback = StarterCatalog(service.data_dir).snapshot('amplifier-development')
    _, row = await create(service, 'amplifier-development', 'Conditional development')
    folder = Path(row['path'])
    assert row['setup']['status'] == 'ready'
    saved = row['setup']['starter']
    inherited = bool(app_bundle or shared_bundle)
    assert saved == {**fallback, 'bundle': '' if inherited else 'work-amp-dev'}
    settings = folder / '.amplifier/settings.yaml'
    if inherited:
        assert not settings.exists()
    else:
        assert read_yaml(settings)['bundle']['active'] == 'work-amp-dev'
    assert not (folder / '.amplifier/settings.local.yaml').exists()
    assert (folder / 'AGENTS.md').read_text() == DEVELOPMENT_INSTRUCTIONS + '\n'
    assert (folder / 'SCRATCH.md').read_text() == SCRATCH_TEMPLATE
    assert (folder / '.amplifier/AGENTS.md').read_text() == '@../AGENTS.md\n@../SCRATCH.md\n'
    assert row['resourceTracking'] is True and row['resources']['resources'] == []
    assert row['setup']['rootGit']['status'] == 'ready'
    assert subprocess.run(['git', '-C', str(folder), 'rev-parse', '--verify', 'HEAD'],
                          capture_output=True).returncode != 0
    assert subprocess.check_output(['git', '-C', str(folder), 'remote']).strip() == b''
    assert local_amplifier_repositories['clones'] == [item['url'] for item in fallback['repositories']]
    for item in row['setup']['repositories']:
        assert item['status'] == 'ready'
        assert item['sha'] == local_amplifier_repositories['commits'][item['url']]
        assert (folder / item['directory'] / '.git').is_dir()
    await service.dispatch('session.create', {})
    assert service._session()['workspace'] == row['path']
    assert service._session()['bundle'] == expected
    assert service._session(existing)['bundle'] == 'anchors'
    assert local_settings.read_bytes() == before


@pytest.mark.parametrize('starter', ['blank', 'development'])
@pytest.mark.parametrize('bundle', ['work-amp-dev', 'anchors'])
async def test_blank_and_development_created_roots_keep_app_inheritance(service, starter, bundle):
    await service.dispatch('bundle.default', {'scope': 'app', 'bundle': bundle})
    _, row = await create(service, starter, 'Inherited workspace')
    folder = Path(row['path'])
    assert not (folder / '.amplifier/settings.yaml').exists()
    await service.dispatch('session.create', {})
    assert service._session()['workspace'] == row['path']
    assert service._session()['bundle'] == bundle


async def test_inherited_starter_plan_stays_live_without_retargeting_existing_roots(
        service, local_amplifier_repositories):
    await service.dispatch('bundle.default', {'scope': 'app', 'bundle': 'work-amp-dev'})
    plan = (await service.dispatch('workspace.prepare', {
        'name': 'Live inheritance', 'starterId': 'amplifier-development'}))['result']
    assert plan['starter']['bundle'] == ''
    # The retained choice is inheritance, not a frozen copy of work-amp-dev.
    await service.dispatch('bundle.default', {'scope': 'app', 'bundle': 'anchors'})
    await service.dispatch('workspace.create', {'planId': plan['planId'], 'fromDraft': True},
                           command_id='live-inheritance')
    row = await settle(service)
    folder = Path(row['path'])
    assert row['setup']['status'] == 'ready' and row['setup']['starter'] == plan['starter']
    await service.dispatch('session.create', {})
    first = service._session()['id']
    assert service._session(first)['bundle'] == 'anchors'
    await service.dispatch('bundle.default', {'scope': 'app', 'bundle': 'work-amp-dev'})
    await service.dispatch('session.create', {})
    assert service._session()['bundle'] == 'work-amp-dev'
    assert service._session(first)['bundle'] == 'anchors'
    await service.dispatch('session.create', {'bundle': 'anchors-amp-dev'})
    assert service._session()['bundle'] == 'anchors-amp-dev'
    assert not (folder / '.amplifier/settings.yaml').exists()
    assert not (folder / '.amplifier/settings.local.yaml').exists()


@pytest.mark.parametrize('old_revision', [5, 8])
async def test_reviewed_older_plan_keeps_its_explicit_bundle(
        service, local_amplifier_repositories, monkeypatch, old_revision):
    original = StarterCatalog.listing
    def older_catalog(catalog):
        value = original(catalog)
        row = next(item for item in value['items'] if item['id'] == 'amplifier-development')
        row.update(revision=old_revision, bundle='anchors-amp-dev')
        return value
    # Prepare through the normal owner while reproducing the prior catalog.
    with monkeypatch.context() as patch:
        patch.setattr(StarterCatalog, 'listing', older_catalog)
        plan = (await service.dispatch('workspace.prepare', {
            'name': 'Reviewed old plan', 'starterId': 'amplifier-development'}))['result']
    await service.dispatch('bundle.default', {'scope': 'app', 'bundle': 'work-amp-dev'})
    await service.dispatch('workspace.create', {'planId': plan['planId'], 'fromDraft': True},
                           command_id='reviewed-old-plan')
    row = await settle(service)
    assert row['setup']['status'] == 'ready' and row['setup']['starter'] == plan['starter']
    assert row['setup']['starter']['revision'] == old_revision
    assert defaults(service.data_dir, row['path'], 'work-amp-dev')['workspace'] == 'anchors-amp-dev'
    await service.dispatch('session.create', {})
    assert service._session()['bundle'] == 'anchors-amp-dev'


@pytest.mark.parametrize('inherit', [False, True])
def test_amplifier_duplicate_keeps_current_choice_when_duplicated(tmp_path, inherit):
    catalog = StarterCatalog(tmp_path, inherit_amplifier_default=inherit)
    saved = catalog.command('workspace.starters.duplicate', {'id': 'amplifier-development'}, 'duplicate')
    assert saved['bundle'] == ('' if inherit else 'work-amp-dev')
    before = catalog.path.read_bytes()
    changed = StarterCatalog(tmp_path, inherit_amplifier_default=not inherit)
    assert changed.snapshot(saved['id']) == saved
    assert changed.path.read_bytes() == before


def test_saved_anchors_amplifier_starter_is_not_migrated(tmp_path):
    catalog = StarterCatalog(tmp_path)
    saved = catalog.command('workspace.starters.save', {
        'starter': {'name': 'My Amplifier development', 'bundle': 'anchors-amp-dev'},
    }, 'save-anchors')
    before = catalog.path.read_bytes()
    restored = StarterCatalog(tmp_path)
    assert restored.snapshot('amplifier-development')['bundle'] == 'work-amp-dev'
    assert restored.snapshot(saved['id']) == saved
    assert catalog.path.read_bytes() == before


@pytest.mark.parametrize('prepared_default_scope', [None, 'app', 'shared'])
async def test_prepared_starter_keeps_pin_or_inheritance_when_defaults_appear_or_clear(
        service, local_amplifier_repositories, prepared_default_scope):
    from amplifier_web.shared_settings import read_yaml
    await service.dispatch('bundle.default', {'scope': 'app', 'bundle': None})
    await service.dispatch('bundle.default', {'scope': 'shared', 'bundle': None})
    await service.dispatch('session.create', {'bundle': 'anchors'})
    existing = service._session()['id']
    if prepared_default_scope:
        await service.dispatch('bundle.default', {
            'scope': prepared_default_scope, 'bundle': 'work-amp-dev'})
    plan = (await service.dispatch('workspace.prepare', {
        'name': 'Default transition', 'starterId': 'amplifier-development'}))['result']
    assert plan['starter']['revision'] == 9
    assert plan['starter']['bundle'] == ('' if prepared_default_scope else 'work-amp-dev')
    if prepared_default_scope:
        await service.dispatch('bundle.default', {'scope': 'app', 'bundle': None})
        await service.dispatch('bundle.default', {'scope': 'shared', 'bundle': None})
    else:
        await service.dispatch('bundle.default', {'scope': 'app', 'bundle': 'anchors'})
    await service.dispatch('workspace.create', {'planId': plan['planId'], 'fromDraft': True},
                           command_id='prepared-default-transition')
    row = await settle(service)
    assert row['setup']['status'] == 'ready'
    assert row['setup']['starter'] == plan['starter']
    folder = Path(row['path'])
    settings = folder / '.amplifier/settings.yaml'
    if prepared_default_scope:
        assert not settings.exists()
    else:
        assert read_yaml(settings)['bundle']['active'] == 'work-amp-dev'
    assert not (folder / '.amplifier/settings.local.yaml').exists()
    await service.dispatch('session.create', {})
    assert service._session()['workspace'] == row['path']
    assert service._session()['bundle'] == ('work' if prepared_default_scope else 'work-amp-dev')
    assert service._session(existing)['bundle'] == 'anchors'


async def test_starters_and_chat_share_enabled_standalone_catalog(service):
    from amplifier_web.bundles import BundleManager
    manager = BundleManager(service.data_dir)
    path = service.state['settings']['workspace']
    await manager.perform('bundles.add', {'workspace': path, 'name': 'team-root',
        'uri': 'git+https://example.test/team', 'role': 'standalone'})
    behavior = await manager.perform('bundles.add', {'workspace': path, 'name': 'addon',
        'uri': 'git+https://example.test/addon', 'role': 'behavior'})
    chat = (await manager.perform('bundles.list', {'workspace': path}))['registeredBundles']
    starters = (await service.dispatch('workspace.starters.list', {}))['result']
    assert starters['bundles'] == chat
    assert {row['value'] for row in chat} == {'anchors', 'anchors-amp-dev', 'work', 'work-amp-dev', 'team-root'}
    saved = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Team', 'bundle': 'team-root'}}))['result']
    prepared = (await service.dispatch('workspace.prepare', {'name': 'Team workspace', 'starterId': saved['id']}))['result']
    root_row = next(row for row in behavior['bundles'] if row['name'] == 'team-root')
    await manager.perform('bundles.toggle', {'workspace': path, 'id': root_row['id'], 'enabled': False})
    new_catalog = (await service.dispatch('workspace.starters.list', {}))['result']
    assert 'team-root' not in {row['value'] for row in new_catalog['bundles']}
    with pytest.raises(AppError, match='no longer an enabled'):
        await service.dispatch('workspace.create', {'planId': prepared['planId']}, command_id='disabled-plan')
    assert not Path(prepared['path']).exists()
    with pytest.raises(AppError, match='configured standalone'):
        await service.dispatch('workspace.prepare', {'name': 'Disabled root', 'starterId': saved['id']})


async def test_project_standalone_binding_works_in_new_workspace(service):
    from amplifier_web.bundles import BundleManager
    from amplifier_web.host.config import read_config
    manager = BundleManager(service.data_dir)
    old_path = service.state['settings']['workspace']
    uri = 'git+https://example.test/team-root'
    await manager.perform('bundles.add', {'workspace': old_path, 'scope': 'project',
        'name': 'project-team', 'uri': uri, 'role': 'standalone'})
    starter = (await service.dispatch('workspace.starters.save',
        {'starter': {'name': 'Project team', 'bundle': 'project-team'}}))['result']
    _, row = await create(service, starter['id'], 'Independent project')
    assert row['setup']['status'] == 'ready'
    config = read_config(row['path'], home=service.data_dir)
    assert config.registrations['project-team'] == uri
    assert config.settings.get('config', {}).get('providers', []) == []
    await service.dispatch('session.create', {})
    assert service._session()['bundle'] == 'project-team'


def test_snapshot_revalidates_legacy_bundle_without_rewriting(tmp_path):
    from amplifier_worktrees.git import atomic
    catalog = StarterCatalog(tmp_path)
    saved = catalog.command('workspace.starters.save', {'starter': {'name': 'Legacy'}}, 'legacy-save')
    stored = catalog._read()
    stored['items'][0]['bundle'] = 'amplifier-dev'
    atomic(catalog.path, stored)
    before = catalog.path.read_bytes()
    with pytest.raises(ValueError, match='configured standalone'):
        catalog.snapshot(saved['id'])
    assert catalog.path.read_bytes() == before


async def test_legacy_prepared_bundle_refuses_without_rewriting(service):
    from amplifier_worktrees.git import atomic
    prepared = (await service.dispatch('workspace.prepare', {'name': 'Legacy plan', 'starterId': 'amplifier-development'}))['result']
    path = service.data_dir / 'workspace-placement' / (prepared['planId'] + '.json')
    value = json.loads(path.read_text())
    value['starter']['bundle'] = 'amplifier-dev'
    atomic(path, value)
    before = path.read_bytes()
    with pytest.raises(AppError, match='no longer an enabled'):
        await service.dispatch('workspace.create', {'planId': prepared['planId']}, command_id='legacy-plan')
    assert path.read_bytes() == before
    assert not Path(prepared['path']).exists()


def test_git_stream_limit_terminates_noisy_child(tmp_path, monkeypatch):
    original = subprocess.Popen
    children = []
    def fake_git(command, **kwargs):
        child = original([sys.executable, '-c',
            'import os,time;os.write(1,b\"x\"*131072);time.sleep(10)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(provisioning.subprocess, 'Popen', fake_git)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    started = time.monotonic()
    try:
        with pytest.raises(ValueError, match='output exceeds'):
            provisioning._raw_git(fd, 'status', '--porcelain')
    finally:
        os.close(fd)
    assert time.monotonic() - started < 3
    assert children[0].poll() is not None


async def test_timeout_retains_partial_import_and_requires_inspection(service, monkeypatch):
    original_popen = subprocess.Popen
    children = []
    def fake_git(command, **kwargs):
        if 'clone' in command:
            child = original_popen([sys.executable, '-c',
                'from pathlib import Path;import time;Path(\".git\").mkdir();Path(\".git/partial\").write_text(\"retain\");time.sleep(10)'], **kwargs)
            children.append(child)
            return child
        return original_popen(command, **kwargs)
    monkeypatch.setattr(provisioning.subprocess, 'Popen', fake_git)
    monkeypatch.setattr(provisioning, 'GIT_TIMEOUT_SECONDS', .3)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Slow',
        'repositories': [{'url': 'https://example.test/slow', 'directory': 'slow'}]}}))['result']
    _, row = await create(service, starter['id'], 'Slow workspace')
    assert row['setup']['status'] == 'partial'
    assert row['setup']['repositories'][0]['status'] == 'unknown'
    assert '0.3 seconds' in row['setup']['repositories'][0]['error']
    retained = Path(row['path']) / 'slow/.git/partial'
    assert retained.read_text() == 'retain'
    assert children[0].poll() is not None
    with pytest.raises(AppError, match='Inspect'):
        await service.dispatch('workspace.setup.retry', {'workspaceId': row['id'], 'expectedRevision': row['setup']['revision']})
    result = provisioning.reconcile(service.data_dir, row['setupId'])
    assert result['repositories'][0]['status'] == 'unknown'
    assert retained.read_text() == 'retain'


@pytest.mark.parametrize('repo', [
    {'url': 'https://user:secret@example.com/repo'}, {'url': 'file:///tmp/repo'},
    {'url': 'https://example.com/repo', 'directory': '../outside'},
    {'url': 'https://example.com/repo', 'ref': '--upload-pack=bad'},
    {'url': 'https://example.com/repo?token=secret'},
])
def test_repository_inputs_fail_closed(repo):
    with pytest.raises(ValueError):
        definition({'name': 'Bad', 'repositories': [repo]})


async def test_blank_stays_empty_and_development_ready_without_git(service):
    _, blank = await create(service, 'blank', 'Blank work')
    assert list(Path(blank['path']).iterdir()) == []
    assert 'setupId' not in blank
    _, row = await create(service)
    folder = Path(row['path'])
    assert row['setup']['status'] == 'ready'
    assert (folder / '.amplifier/AGENTS.md').is_file()
    assert not (folder / '.git').exists()
    assert (folder / 'SCRATCH.md').is_file()
    assert 'workspace root is never the source root' in (folder / 'AGENTS.md').read_text()
    assert (folder / '.amplifier/AGENTS.md').read_text() == '@../AGENTS.md\n@../SCRATCH.md\n'
    assert service.state['sessions'] == []
    assert row['resourceTracking'] is True
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_web.host.mentions import include_instruction_files
    bundle = include_instruction_files(Bundle(name='fixture'), execution_workspace=folder)
    prepared = PreparedBundle(bundle.to_mount_plan(), BundleModuleResolver({}), bundle)
    session = SimpleNamespace(coordinator=SimpleNamespace(hooks=SimpleNamespace(emit=AsyncMock())))
    render = prepared.create_system_prompt_factory(session, session_cwd=folder)
    assert 'The workspace is a container' in await render()


async def test_custom_scratch_without_instructions_is_loaded(service):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_web.host.mentions import include_instruction_files
    starter = (await service.dispatch('workspace.starters.save',
        {'starter': {'name': 'Scratch only', 'scratch': True}}))['result']
    _, row = await create(service, starter['id'], 'Scratch only')
    folder = Path(row['path'])
    assert not (folder / 'AGENTS.md').exists()
    assert (folder / '.amplifier/AGENTS.md').read_text() == '@../SCRATCH.md\n'
    (folder / 'SCRATCH.md').write_text('CUSTOM-ROOT-MEMORY')
    bundle = include_instruction_files(Bundle(name='work'), execution_workspace=folder)
    prepared = PreparedBundle(bundle.to_mount_plan(), BundleModuleResolver({}), bundle)
    session = SimpleNamespace(coordinator=SimpleNamespace(hooks=SimpleNamespace(emit=AsyncMock())))
    render = prepared.create_system_prompt_factory(session, session_cwd=folder)
    assert 'CUSTOM-ROOT-MEMORY' in await render()


def test_existing_custom_definitions_keep_scratch_disabled_without_migration(tmp_path):
    from amplifier_worktrees.git import atomic
    catalog = StarterCatalog(tmp_path)
    catalog.command('workspace.starters.save', {'starter': {'name': 'Existing'}}, 'old-save')
    value = catalog._read()
    del value['items'][0]['scratch']
    del value['items'][0]['rootGit']
    atomic(catalog.path, value)
    before = catalog.path.read_bytes()
    assert catalog.listing()['items'][-1]['scratch'] is False
    assert catalog.listing()['items'][-1]['rootGit'] is False
    assert catalog.path.read_bytes() == before


async def test_workspace_git_init_excludes_child_repos_and_credentials_no_commits(service):
    starter = (await service.dispatch('workspace.starters.duplicate', {'id': 'amplifier-development'}))['result']
    fields = {key: starter[key] for key in ('name', 'description', 'instructions', 'bundle', 'repositories', 'trackResources', 'scratch', 'rootGit')}
    fields['repositories'] = []
    starter = (await service.dispatch('workspace.starters.save', {'id': starter['id'],
        'expectedRevision': starter['revision'], 'starter': fields}))['result']
    _, row = await create(service, starter['id'], 'Git notes')
    assert row['setup']['status'] == 'ready'
    assert row['setup']['rootGit']['status'] == 'ready'
    folder = Path(row['path'])
    assert (folder / '.git').is_dir()
    assert subprocess.run(['git', '-C', str(folder), 'rev-parse', '--verify', 'HEAD'], capture_output=True).returncode != 0
    assert subprocess.check_output(['git', '-C', str(folder), 'remote']).strip() == b''
    local_git(folder / 'project')
    (folder / '.env').write_text('TOKEN=private')
    (folder / 'keys.env').write_text('secret')
    (folder / 'notes.md').write_text('checkpoint me')
    ignored = subprocess.check_output(['git', '-C', str(folder), 'check-ignore',
        'project/source.txt', '.amplifier/settings.yaml', '.env', 'keys.env']).decode()
    assert all(name in ignored for name in ('project/source.txt', '.amplifier/settings.yaml', '.env', 'keys.env'))
    visible = subprocess.check_output(['git', '-C', str(folder), 'status', '--porcelain']).decode()
    assert 'notes.md' in visible and 'AGENTS.md' in visible and 'SCRATCH.md' in visible
    assert 'project/' not in visible and '.env' not in visible
    before = (folder / '.git/config').read_bytes()
    await service.dispatch('workspace.setup.retry', {'workspaceId': row['id'], 'expectedRevision': row['setup']['revision']})
    assert (folder / '.git/config').read_bytes() == before


async def test_existing_root_git_metadata_is_not_reinitialized(service, tmp_path, monkeypatch):
    original = provisioning.run
    def existing(home, identity, progress=None):
        setup = provisioning.inspect(home, identity)
        folder = Path(setup['path'])
        subprocess.run(['git', 'init', '-b', 'existing', str(folder)], check=True, capture_output=True)
        (folder / '.git/info').mkdir(exist_ok=True)
        (folder / '.git/info/exclude').write_text('original exclusions\n')
        return original(home, identity, progress)
    monkeypatch.setattr(provisioning, 'run', existing)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Existing Git', 'rootGit': True}}))['result']
    _, row = await create(service, starter['id'], 'Existing Git metadata')
    assert row['setup']['rootGit']['status'] == 'preserved'
    folder = Path(row['path'])
    assert (folder / '.git/HEAD').read_text() == 'ref: refs/heads/existing\n'
    assert (folder / '.git/info/exclude').read_text() == 'original exclusions\n'
    assert not (folder / '.gitignore').exists()


async def test_interrupted_workspace_git_is_not_replayed(service, monkeypatch):
    original = provisioning._raw_git
    def fail_init(fd, *args, **kwargs):
        if 'init' in args:
            raise ValueError('Interrupted initialization fixture')
        return original(fd, *args, **kwargs)
    monkeypatch.setattr(provisioning, '_raw_git', fail_init)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Interrupted Git', 'rootGit': True}}))['result']
    _, row = await create(service, starter['id'], 'Interrupted Git')
    assert row['setup']['status'] == 'failed'
    assert row['setup']['rootGit']['status'] == 'unknown'
    monkeypatch.setattr(provisioning, '_raw_git', original)
    with pytest.raises(AppError, match='not replayed'):
        await service.dispatch('workspace.setup.retry', {'workspaceId': row['id'], 'expectedRevision': row['setup']['revision']})


async def test_workspace_git_pinned_metadata_cannot_redirect_to_other_repo(service, tmp_path, monkeypatch):
    outside = tmp_path / 'borrowed'
    local_git(outside)
    before = (outside / '.git/config').read_bytes()
    original = provisioning._raw_git
    def swap(fd, *args, **kwargs):
        if 'init' in args:
            pinned = Path(f'/proc/self/fd/{fd}')
            (pinned / '.git').rename(pinned / 'retained-git')
            (pinned / '.git').symlink_to(outside / '.git', target_is_directory=True)
        return original(fd, *args, **kwargs)
    monkeypatch.setattr(provisioning, '_raw_git', swap)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Pinned', 'rootGit': True}}))['result']
    _, row = await create(service, starter['id'], 'Pinned Git')
    assert row['setup']['status'] == 'failed'
    assert row['setup']['rootGit']['status'] == 'unknown'
    assert (outside / '.git/config').read_bytes() == before


async def test_pre_pin_borrowed_git_is_refused_without_changes(service, tmp_path, monkeypatch):
    borrowed = tmp_path / 'borrowed'
    local_git(borrowed)
    before = (borrowed / '.git/config').read_bytes()
    original = provisioning.os.mkdir
    def swap(name, *args, **kwargs):
        result = original(name, *args, **kwargs)
        if name == '.git' and 'dir_fd' in kwargs:
            root = Path(f"/proc/self/fd/{kwargs['dir_fd']}")
            (root / '.git').rmdir()
            (borrowed / '.git').rename(root / '.git')
        return result
    monkeypatch.setattr(provisioning.os, 'mkdir', swap)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Borrowed', 'rootGit': True}}))['result']
    _, row = await create(service, starter['id'], 'Borrowed refused')
    assert row['setup']['status'] == 'failed'
    assert 'borrowed Git was not modified' in row['setup']['error']
    assert (Path(row['path']) / '.git/config').read_bytes() == before


async def test_conflicting_ignore_rules_preserved_but_git_not_claimed_ready(service, monkeypatch):
    original = provisioning.run
    def existing_ignore(home, identity, progress=None):
        setup = provisioning.inspect(home, identity)
        (Path(setup['path']) / '.gitignore').write_text('!.env\n!project-check/\n')
        return original(home, identity, progress)
    monkeypatch.setattr(provisioning, 'run', existing_ignore)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Ignore conflict', 'rootGit': True}}))['result']
    _, row = await create(service, starter['id'], 'Conflicting exclusions')
    assert row['setup']['status'] == 'failed'
    assert '.gitignore was preserved' in row['setup']['error']
    assert (Path(row['path']) / '.gitignore').read_text() == '!.env\n!project-check/\n'


@pytest.mark.parametrize('rules', ['!secret.pem\n', '!future-project/\n'])
async def test_arbitrary_preserved_ignore_negations_cannot_claim_safe_readiness(service, monkeypatch, rules):
    original = provisioning.run
    def existing_ignore(home, identity, progress=None):
        setup = provisioning.inspect(home, identity)
        (Path(setup['path']) / '.gitignore').write_text(rules)
        return original(home, identity, progress)
    monkeypatch.setattr(provisioning, 'run', existing_ignore)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Negations', 'rootGit': True}}))['result']
    _, row = await create(service, starter['id'], 'Unqualified existing rules')
    assert row['setup']['status'] == 'failed'
    assert row['setup']['rootGit']['status'] == 'unknown'
    assert (Path(row['path']) / '.gitignore').read_text() == rules


async def test_restart_marks_root_git_unknown_and_refuses_retry(service):
    _, row = await create(service)
    target = provisioning.receipt_path(service.data_dir, row['setupId'])
    value = json.loads(target.read_text())
    value.update(status='running', rootGit={'status': 'running'})
    from amplifier_worktrees.git import atomic
    atomic(target, value)
    recovered = provisioning.recover(service.data_dir, row['setupId'])
    assert recovered['rootGit']['status'] == 'unknown'
    with pytest.raises(ValueError, match='not replayed'):
        provisioning.retry(service.data_dir, row['setupId'], recovered['revision'])


async def test_prepared_snapshot_survives_starter_edit_and_delete(service):
    saved = await service.dispatch('workspace.starters.save', {'starter': {'name': 'Custom', 'instructions': 'Original', 'bundle': 'anchors'}})
    starter = saved['result']
    # An explicit saved custom choice still wins over a later app default.
    await service.dispatch('bundle.default', {'scope': 'app', 'bundle': 'work-amp-dev'})
    plan = (await service.dispatch('workspace.prepare', {'name': 'Snapshot', 'starterId': starter['id']}))['result']
    await service.dispatch('workspace.starters.remove', {'id': starter['id'], 'expectedRevision': starter['revision']})
    await service.dispatch('workspace.create', {'planId': plan['planId']}, command_id='snapshot-create')
    row = await settle(service)
    assert (Path(row['path']) / 'AGENTS.md').read_text() == 'Original\n'
    assert defaults(service.data_dir, row['path'])['effective'] == 'anchors'
    await service.dispatch('session.create', {})
    assert service._session()['bundle'] == 'anchors'


async def test_attach_and_collision_never_scaffold(service, tmp_path):
    foreign = tmp_path / 'borrowed'
    foreign.mkdir()
    (foreign / 'keep.txt').write_text('mine')
    await service.dispatch('workspace.add', {'path': str(foreign)})
    assert sorted(p.name for p in foreign.iterdir()) == ['keep.txt']
    plan = (await service.dispatch('workspace.prepare', {'name': 'borrowed', 'root': str(tmp_path), 'starterId': 'development'}))['result']
    assert plan['disposition'] == 'open'
    with pytest.raises(AppError, match='already exists'):
        await service.dispatch('workspace.create', {'planId': plan['planId']}, command_id='collision')
    assert sorted(p.name for p in foreign.iterdir()) == ['keep.txt']


async def test_creation_retry_does_not_rewrite_instructions_or_clone(service):
    result, row = await create(service)
    folder = Path(row['path'])
    instructions = folder / '.amplifier/AGENTS.md'
    instructions.write_text('User edit')
    plan_id = result['result']['setupId']
    again = await service.dispatch('workspace.create', {'planId': plan_id, 'fromDraft': True}, command_id='create-My work')
    assert again['duplicate']
    await service.dispatch('workspace.create', {'planId': plan_id}, command_id='new-transport')
    assert instructions.read_text() == 'User edit'
    assert len([w for w in service.state['workspaces'] if w['path'] == str(folder)]) == 1


async def test_background_setup_does_not_hold_app_lock_or_change_newer_draft(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = provisioning.run
    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(provisioning, 'run', delayed)
    plan = (await service.dispatch('workspace.prepare', {'name': 'Delayed', 'starterId': 'development'}))['result']
    await service.dispatch('workspace.create', {'planId': plan['planId'], 'fromDraft': True}, command_id='delay-create')
    await asyncio.to_thread(entered.wait, 2)
    await asyncio.wait_for(service.dispatch('view.update', {'patch': {'draft': 'newer edit'}}), 1)
    with pytest.raises(AppError, match='still preparing'):
        await service.dispatch('session.create', {})
    with pytest.raises(AppError, match='still preparing'):
        await service.dispatch('session.create', {'workspace': plan['path'] + '/.'})
    ready_path = next(w['path'] for w in service.state['workspaces'] if w['path'] != plan['path'])
    await service.dispatch('session.draft', {'workspace': ready_path})
    with pytest.raises(AppError, match='still preparing'):
        await service.dispatch('session.create', {})
    release.set()
    await settle(service)
    assert service.state['view']['draft'] == 'newer edit'


async def test_existing_instruction_and_symlink_preserved(service, monkeypatch, tmp_path):
    original = provisioning.run
    def race(home, identity, progress=None):
        row = provisioning.inspect(home, identity)
        folder = Path(row['path']) / '.amplifier'
        folder.mkdir()
        (folder / 'AGENTS.md').write_text('User directions')
        (folder.parent / 'AGENTS.md').write_text('Root user directions')
        (folder.parent / 'SCRATCH.md').write_text('Existing memory')
        return original(home, identity, progress)
    monkeypatch.setattr(provisioning, 'run', race)
    _, row = await create(service)
    assert (Path(row['path']) / '.amplifier/AGENTS.md').read_text() == 'User directions'
    assert (Path(row['path']) / 'AGENTS.md').read_text() == 'Root user directions'
    assert (Path(row['path']) / 'SCRATCH.md').read_text() == 'Existing memory'
    assert all(item['status'] == 'preserved' for item in row['setup']['files'])


async def test_resource_records_persist_and_observation_is_not_teardown(service):
    _, row = await create(service)
    args = {'workspaceId': row['id'], 'kind': 'container', 'resourceId': 'example', 'owner': 'other-system'}
    resource = (await service.dispatch('workspace.resources.add', args, command_id='resource-add'))['result']
    await service.dispatch('workspace.resources.update', {'workspaceId': row['id'], 'id': resource['id'], 'expectedRevision': 1, 'status': 'observed_absent', 'evidence': 'Owner confirmed missing'})
    await service.dispatch('workspace.remove', {'id': row['id']})
    assert Path(row['path']).is_dir()
    from amplifier_web.workspace_resources import command
    inventory = command(service.data_dir, row['id'], 'workspace.resources.list', {})
    assert inventory['resources'][0]['status'] == 'observed_absent'
    assert inventory['resources'][0]['owner'] == 'other-system'


def local_git(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-b', 'trunk', str(path)], check=True, capture_output=True)
    (path / 'source.txt').write_text('version one')
    subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-m', 'Initial'], check=True, capture_output=True)
    return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD']).decode().strip()


@pytest.fixture
def local_amplifier_repositories(tmp_path, monkeypatch):
    """Keep the real built-in scaffold/import flow; redirect only Git transport."""
    sources, commits, clones = {}, {}, []
    for name in ('amplifier', 'amplifier-core', 'amplifier-foundation'):
        url = f'https://github.com/microsoft/{name}.git'
        sources[url] = tmp_path / ('origin-' + name)
        commits[url] = local_git(sources[url])
    original = provisioning._git
    def local_clone(fd, *args):
        if args[0] != 'clone':
            return original(fd, *args)
        url = args[-2]
        assert url in sources
        clones.append(url)
        # No network and no mocked clone success; all other Git guards stay real.
        return subprocess.check_output([
            'git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.file.allow=always',
            '-c', f'url.{sources[url]}.insteadOf={url}', *args],
            cwd=f'/proc/self/fd/{fd}', pass_fds=(fd,), stderr=subprocess.DEVNULL).decode().strip()
    monkeypatch.setattr(provisioning, '_git', local_clone)
    return {'commits': commits, 'clones': clones}


async def test_real_git_import_default_branch_partial_retry_keeps_success(service, tmp_path, monkeypatch):
    source = tmp_path / 'origin'
    expected_sha = local_git(source)
    source2 = tmp_path / 'later'
    # Rewrite fixture URLs through Git's configuration, not production validation.
    config = tmp_path / 'gitconfig'
    config.write_text(f'[url "{tmp_path}/"]\n insteadOf = https://fixture.invalid/\n[protocol "file"]\n allow = always\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    original_git = provisioning._git
    def fixture_git(fd, *args):
        # Production forbids file transport; this test uses real local Git only.
        if args[0] == 'clone':
            args = tuple(str(tmp_path / arg.removeprefix('https://fixture.invalid/')) if arg.startswith('https://fixture.invalid/') else arg for arg in args)
            cwd = f'/proc/self/fd/{fd}'
            return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=cwd, pass_fds=(fd,), stderr=subprocess.DEVNULL).decode().strip()
        return original_git(fd, *args)
    monkeypatch.setattr(provisioning, '_git', fixture_git)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Two repos', 'repositories': [
        {'url': 'https://fixture.invalid/origin', 'directory': 'one'}, {'url': 'https://fixture.invalid/later', 'directory': 'two'}]}}))['result']
    # The failed process raises a domain failure like production; no mocked success.
    real = provisioning._git
    def checked(fd, *args):
        try: return real(fd, *args)
        except subprocess.CalledProcessError: raise ValueError('Fixture repository is not accessible yet')
    monkeypatch.setattr(provisioning, '_git', checked)
    _, row = await create(service, starter['id'])
    assert row['setup']['status'] == 'partial'
    assert row['setup']['repositories'][0]['sha'] == expected_sha
    assert row['setup']['repositories'][0]['branch'] == 'trunk'
    (Path(row['path']) / 'one/source.txt').write_text('User work')
    local_git(source2)
    await service.dispatch('workspace.setup.retry', {'workspaceId': row['id'], 'expectedRevision': row['setup']['revision']}, command_id='retry-two')
    row = await settle(service)
    assert row['setup']['status'] == 'ready'
    assert (Path(row['path']) / 'one/source.txt').read_text() == 'User work'
    assert (Path(row['path']) / 'two/source.txt').read_text() == 'version one'
    assert not (Path(row['path']) / '.git').exists()


async def test_restart_reads_completed_receipt_without_replaying(service):
    _, row = await create(service)
    identity = row['setupId']
    folder = Path(row['path'])
    (folder / '.amplifier/AGENTS.md').write_text('User edit')
    # Emulate a crash after the durable setup result but before state publication.
    row['setup'] = {**row['setup'], 'status': 'pending'}
    service._save()
    await service.close()
    restored = AppService(service.data_dir)
    try:
        saved = next(w for w in restored.state['workspaces'] if w.get('setupId') == identity)
        assert saved['setup']['status'] == 'ready'
        assert (folder / '.amplifier/AGENTS.md').read_text() == 'User edit'
        assert not restored.tasks
    finally:
        await restored.close()


async def test_restart_marks_unowned_running_setup_interrupted(service):
    _, row = await create(service)
    target = provisioning.receipt_path(service.data_dir, row['setupId'])
    durable = json.loads(target.read_text())
    durable['status'] = 'running'
    from amplifier_worktrees.git import atomic
    atomic(target, durable)
    await service.close()
    restored = AppService(service.data_dir)
    try:
        saved = next(w for w in restored.state['workspaces'] if w.get('setupId') == row['setupId'])
        assert saved['setup']['status'] == 'interrupted'
        assert not restored.tasks
    finally:
        await restored.close()


async def test_scaffold_symlink_cannot_write_outside_workspace(service, tmp_path, monkeypatch):
    outside = tmp_path / 'outside'
    outside.mkdir()
    original = provisioning.run
    def swap(home, identity, progress=None):
        setup = provisioning.inspect(home, identity)
        (Path(setup['path']) / '.amplifier').symlink_to(outside, target_is_directory=True)
        return original(home, identity, progress)
    monkeypatch.setattr(provisioning, 'run', swap)
    _, row = await create(service)
    assert row['setup']['status'] == 'failed'
    assert list(outside.iterdir()) == []
    with pytest.raises(AppError, match='still preparing'):
        await service.dispatch('session.create', {})


def test_git_observation_disables_monitor_and_filters(tmp_path):
    repo = tmp_path / 'repo'
    local_git(repo)
    sentinel = tmp_path / 'executed'
    helper = tmp_path / 'monitor'
    helper.write_text(f'#!/bin/sh\ntouch \"{sentinel}\"\n')
    helper.chmod(0o755)
    subprocess.run(['git', '-C', str(repo), 'config', 'core.fsmonitor', str(helper)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'filter.bad.clean', f'touch \"{sentinel}\"; cat'], check=True)
    (repo / '.gitattributes').write_text('source.txt filter=bad\n')
    (repo / 'source.txt').write_text('Changed')
    fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert 'source.txt' in provisioning._git(fd, 'status', '--porcelain')
    finally:
        os.close(fd)
    assert not sentinel.exists()


async def test_scaffold_progress_refreshes_defaults_before_import_finishes(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = provisioning.run
    def gated(home, identity, progress=None):
        def publish(value):
            if progress:
                progress(value)
            entered.set()
            assert release.wait(5)
        return original(home, identity, publish)
    monkeypatch.setattr(provisioning, 'run', gated)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Default', 'bundle': 'anchors'}}))['result']
    plan = (await service.dispatch('workspace.prepare', {'name': 'Defaults', 'starterId': starter['id']}))['result']
    key = json.dumps([plan['path'], ''], separators=(',', ':'))
    service.state['draftDefaults'] = {key: {'bundle': 'work', 'phase': 'ready'}}
    revision = service.state.get('configurationRevision', 0)
    await service.dispatch('workspace.create', {'planId': plan['planId']}, command_id='defaults-create')
    await asyncio.to_thread(entered.wait, 2)
    try:
        assert key not in service.state.get('draftDefaults', {})
        assert service.state['configurationRevision'] > revision
        await service.dispatch('session.create', {})
        assert service._session()['bundle'] == 'anchors'
    finally:
        release.set()
    await settle(service)
