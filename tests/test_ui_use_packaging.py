"""Optional support affects new declarations, never existing worker receipts."""
import json
import sys
from types import ModuleType, SimpleNamespace
import tomllib
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web import app_features, app_updates, runtime_environment as environments
from test_app_features import helper_manager, managed, packaged_app

QUALIFIED_BROWSER = {'ownedExecutable': '/owned/browsers/chromium/chrome',
    'selectedExecutable': '/owned/browsers/chromium/chrome', 'selection': 'owned',
    'launchArgs': [], 'blankLaunchVerified': True}


def test_connected_browser_default_has_no_browser_or_decision_runtime():
    raw = environments.declared_manifest(extras=[])
    assert raw == environments.manifest_path().read_bytes()
    dependencies = tomllib.loads(raw.decode())['project']['dependencies']
    assert any(value.startswith('amplifier-bundle-ui-use @ ') for value in dependencies)
    assert not any(any(name in value for name in ('playwright', 'lib-decisions', 'provider-jev',
        'provider-local-decisions', 'torch', 'transformers')) for value in dependencies)
    assert environments.declared_manifest(extras=['managed-browser']) == raw


def test_shared_library_does_not_claim_old_chat_provider_has_decision_support(monkeypatch):
    present = {'amplifier-lib-decisions', 'amplifier-module-provider-openai', 'amplifier-module-provider-jev'}
    def distribution(name):
        if name not in present:
            raise app_updates.metadata.PackageNotFoundError(name)
        return SimpleNamespace(entry_points=[SimpleNamespace(group='amplifier.decision_backends')]
            if name == 'amplifier-module-provider-jev' else [])
    monkeypatch.setattr(app_updates.metadata, 'distribution', distribution)
    assert app_updates.installed_extras() == ['decisions-jev']


@pytest.mark.parametrize('feature,package', [
    ('decisions-openai', 'amplifier-module-provider-openai'),
    ('decisions-azure', 'amplifier-module-provider-azure-openai'),
    ('decisions-jev', 'amplifier-module-provider-jev'),
    ('decisions-local', 'amplifier-module-provider-local-decisions'),
])
def test_selected_decision_support_reaches_only_new_worker_declarations(tmp_path, monkeypatch, feature, package):
    old = environments.declared_manifest(extras=[])
    receipt = environments.receipt_directory(tmp_path)
    receipt.mkdir(parents=True)
    (receipt / 'runtime.toml').write_bytes(old)
    (receipt / 'runtime.lock').write_text('version = 1\n')
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: [feature])
    current = environments.declared_manifest()
    manifest = tomllib.loads(current.decode())
    assert sum(value.split('[')[0] == package for value in manifest['project']['dependencies']) == 1
    assert manifest['tool']['uv']['sources'][package] == {
        'git': 'https://github.com/microsoft/' + package, 'rev': 'main'}
    assert '[inference]' not in current.decode() and '[server]' not in current.decode()
    # Installed support is not permission to modify an already-qualified runtime.
    assert environments.manifest_content(tmp_path) == old
    assert (receipt / 'runtime.lock').read_text() == 'version = 1\n'
    inventory = environments.inventory(tmp_path, installed={}, graph=[])
    assert any(row['id'] == 'runtime:environment' and row['status'] == 'update' for row in inventory)


def test_decision_support_keeps_explicit_provider_source_policy(monkeypatch):
    content = environments.declared_manifest(extras=['decisions-openai'])
    updated = environments.augmented_manifest(content, [{
        'kind': 'runtime dependency', 'package': 'amplifier-module-provider-openai',
        'url': 'https://example.invalid/owned-provider', 'ref': 'a' * 40, 'current': 'a' * 40,
        'provenance': 'installed Git distribution',
    }])
    source = tomllib.loads(updated.decode())['tool']['uv']['sources']['amplifier-module-provider-openai']
    assert source == {'git': 'https://example.invalid/owned-provider', 'rev': 'a' * 40}


@pytest.mark.parametrize('feature', ['managed-browser', 'decisions-openai', 'decisions-azure', 'decisions-jev', 'decisions-local'])
def test_support_pack_is_additive_and_has_an_exact_restart_receipt(helper_manager, packaged_app, monkeypatch, feature):
    original = app_features.metadata.distribution
    def distribution(name):
        if name != 'amplifier-unified':raise app_features.metadata.PackageNotFoundError(name)
        return original(name)
    monkeypatch.setattr(app_features.metadata, 'distribution', distribution)
    selected = app_features.selection(helper_manager, feature, 'request-pack')
    assert selected['extras'] == sorted([feature, 'tui'])
    assert app_features.validate_selection(helper_manager, selected, selected['extras']) == selected['extras']
    target = {'version': selected['hostApp']['version'], 'revision': selected['hostApp']['revision'],
              'featureSelection': selected, 'dependencyDigest': 'f' * 64}
    assert app_features.valid_restart_selection(target)
    selected['feature'] = 'decisions-local' if feature != 'decisions-local' else 'managed-browser'
    assert not app_features.valid_restart_selection(target)


def test_adding_decisions_to_old_provider_requires_explicit_component_update(monkeypatch):
    monkeypatch.setattr(app_features.metadata, 'distribution', lambda _: SimpleNamespace(entry_points=[]))
    with pytest.raises(ValueError, match='preserves existing component revisions'):
        app_features.require_provider_support('decisions-openai')
    monkeypatch.setattr(app_features.metadata, 'distribution', lambda _: SimpleNamespace(
        entry_points=[SimpleNamespace(group='amplifier.decision_backends')]))
    app_features.require_provider_support('decisions-openai')


def test_managed_browser_requires_package_and_real_executable(tmp_path, monkeypatch):
    binary = tmp_path / 'chromium'
    monkeypatch.setenv('AMPLIFIER_UI_BROWSER', str(binary))
    monkeypatch.setattr(app_features.metadata, 'version', lambda _: '1')
    assert app_features.managed_browser_executable(tmp_path) is None
    binary.write_text('fixture'); binary.chmod(0o600)
    assert app_features.managed_browser_executable(tmp_path) is None
    binary.chmod(0o700)
    assert app_features.managed_browser_executable(tmp_path) == str(binary)
    def missing(_): raise app_features.metadata.PackageNotFoundError()
    monkeypatch.setattr(app_features.metadata, 'version', missing)
    assert app_features.managed_browser_executable(tmp_path) is None


async def test_browser_preparation_owns_cache_and_never_installs_os_packages(tmp_path):
    diagnostics = SimpleNamespace(run=AsyncMock(side_effect=['', json.dumps(QUALIFIED_BROWSER)]), browser_qualified=Mock())
    manager = SimpleNamespace(home=tmp_path, diagnostics=diagnostics)
    await app_updates.prepare_browser(manager, '/owned/python', [])
    diagnostics.run.assert_not_awaited()
    assert await app_updates.prepare_browser(manager, '/owned/python', ['managed-browser']) == QUALIFIED_BROWSER
    call = diagnostics.run.await_args_list[0]
    assert call.args[2:] == ('/owned/python', '-I', '-m', 'playwright', 'install', 'chromium')
    assert call.kwargs['env']['PLAYWRIGHT_BROWSERS_PATH'] == str(tmp_path / 'browsers')
    assert not any('--with-deps' in str(value) for value in call.args)
    assert (tmp_path / 'browsers').is_dir()
    assert diagnostics.run.await_args_list[1].args[0] == 'managed-browser-probe'
    diagnostics.browser_qualified.assert_called_once_with()


@pytest.mark.parametrize('receipt', [QUALIFIED_BROWSER, {**QUALIFIED_BROWSER, 'blankLaunchVerified':False}, {}])
async def test_browser_recovery_requires_validated_launch_receipt(tmp_path, receipt):
    diagnostics = SimpleNamespace(run=AsyncMock(return_value=json.dumps(receipt)), browser_qualified=Mock())
    manager = SimpleNamespace(home=tmp_path, diagnostics=diagnostics)
    if receipt == QUALIFIED_BROWSER:
        assert await app_updates.qualify_browser(manager, '/owned/python') == receipt
        diagnostics.browser_qualified.assert_called_once_with()
    else:
        with pytest.raises(ValueError, match='could not start'):
            await app_updates.qualify_browser(manager, '/owned/python')
        diagnostics.browser_qualified.assert_not_called()


@pytest.mark.parametrize('fails', [False, True])
async def test_installed_browser_repair_verifies_before_receipt_without_replacing_app(managed, monkeypatch, fails):
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: ['managed-browser'])
    prepare = AsyncMock(return_value=QUALIFIED_BROWSER, side_effect=RuntimeError('download failed') if fails else None)
    monkeypatch.setattr(app_updates, 'prepare_browser', prepare)
    request = managed.manager.featureInstall('managed-browser', managed.service.instance_id, 'repair-browser')
    if fails:
        with pytest.raises(RuntimeError, match='download failed'):
            await request
    else:
        await request
    prepare.assert_awaited_once()
    row = managed.service.state['updates']['featureResults']['repair-browser']
    assert row['feature'] == 'managed-browser'
    assert row['phase'] == ('error' if fails else 'already_installed')
    assert not managed.calls
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize('mode', ['owned', 'configured', 'dtu'])
async def test_browser_probe_launches_and_closes_only_empty_context(tmp_path, monkeypatch, mode):
    owned = tmp_path / 'managed-chromium'; owned.write_text('fixture'); owned.chmod(0o700)
    configured = tmp_path / 'configured-chromium'; configured.write_text('fixture'); configured.chmod(0o700)
    for key in ('AMPLIFIER_UI_BROWSER', 'AMPLIFIER_UI_BROWSER_NO_SANDBOX', 'DTU_CHROMIUM_SINGLE_PROCESS'):
        monkeypatch.delenv(key, raising=False)
    if mode == 'configured':
        monkeypatch.setenv('AMPLIFIER_UI_BROWSER', str(configured))
        monkeypatch.setenv('AMPLIFIER_UI_BROWSER_NO_SANDBOX', '1')
    if mode == 'dtu':monkeypatch.setenv('DTU_CHROMIUM_SINGLE_PROCESS', '1')
    page = SimpleNamespace(url='about:blank')
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    launch = AsyncMock(return_value=browser)
    driver = SimpleNamespace(chromium=SimpleNamespace(executable_path=str(owned), launch=launch))
    class Playwright:
        async def __aenter__(self):return driver
        async def __aexit__(self, *_):pass
    package, module = ModuleType('playwright'), ModuleType('playwright.async_api')
    module.async_playwright = Playwright
    monkeypatch.setitem(sys.modules, 'playwright', package)
    monkeypatch.setitem(sys.modules, 'playwright.async_api', module)
    receipt = await app_features.qualify_managed_browser()
    selected = configured if mode == 'configured' else owned
    launch.assert_awaited_once_with(executable_path=str(selected), headless=True,
        args=app_features.managed_browser_launch_args(), timeout=10000)
    context.new_page.assert_awaited_once_with()
    browser.close.assert_awaited_once_with()
    assert receipt['ownedExecutable'] == str(owned)
    assert receipt['selectedExecutable'] == str(selected)
    assert receipt['selection'] == ('configured' if mode == 'configured' else 'owned')
    assert receipt['blankLaunchVerified']


async def test_failed_browser_launch_is_actionable_and_never_ready(managed, monkeypatch):
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: ['managed-browser'])
    async def process(*args, **kwargs):
        if '-m' in args:return ''  # Download itself succeeded.
        raise RuntimeError('Chromium failed to start: missing library')
    monkeypatch.setattr(app_updates, 'process', process)
    with pytest.raises(ValueError, match='operating-system libraries'):
        await managed.manager.featureInstall('managed-browser', managed.service.instance_id, 'failed-launch')
    row = managed.service.state['updates']['featureResults']['failed-launch']
    assert row['phase'] == 'error' and row['feature'] == 'managed-browser'
    assert 'managedBrowser' not in row
    assert 'connect an existing browser' in row['detail']
    managed.restart.assert_not_awaited()


async def test_browser_selection_change_invalidates_staged_application_before_replacement(tmp_path, monkeypatch):
    from test_app_updates import prepared_activation
    service, manager, _ = await prepared_activation(tmp_path, monkeypatch)
    marker = manager.directory / 'applications' / ('a' * 40) / 'validated.json'
    marker.write_text(json.dumps({**json.loads(marker.read_text()), 'extras': ['managed-browser'],
        'managedBrowser': QUALIFIED_BROWSER}))
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: ['managed-browser'])
    monkeypatch.setattr(app_updates, 'qualify_browser', AsyncMock(return_value={**QUALIFIED_BROWSER,
        'selection': 'configured', 'selectedExecutable': '/another/chromium'}))
    process = AsyncMock()
    monkeypatch.setattr(app_updates, 'process', process)
    try:
        with pytest.raises(ValueError, match='changed after qualification'):
            await app_updates.activate(manager)
        process.assert_not_awaited()
        assert not (manager.directory / 'previous-app.json').exists()
        assert not service.state['updates'].get('pendingReplacement')
    finally:
        await service.close()
