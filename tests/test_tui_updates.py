"""Application updates retain and validate every installed optional feature."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates
from test_app_updates import prepared_activation


@pytest.mark.parametrize('extras', [[], ['tui'], ['native-desktop'], ['native-desktop', 'tui'],
    ['decisions-jev'], ['decisions-openai', 'tui']])
async def test_update_stages_and_replaces_the_same_optional_install(tmp_path, monkeypatch, extras):
    service, manager, _ = await prepared_activation(tmp_path, monkeypatch)
    service.state['updates']['application'] = service.state['updates']['pendingApp']
    calls = []
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: extras)
    monkeypatch.setattr(app_updates.components, 'read_graph', AsyncMock(return_value=[
        {'name':'amplifier-unified','version':'99.0.0','url':app_updates.SOURCE,'revision':'a'*40}]))
    monkeypatch.setattr(app_updates.shutil, 'which', lambda _: '/fixture/uv')
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda _: True)

    async def process(*args, **kwargs):
        calls.append(args)
        return '99.0.0' if '-c' in args else ''

    monkeypatch.setattr(app_updates, 'process', process)
    try:
        await app_updates.stage(manager)
        marker = manager.directory / 'applications' / ('a' * 40) / service.state['updates']['pendingApp']['generation'] / 'validated.json'
        candidate = marker.parent / 'tools/amplifier-unified/bin/python'
        candidate.parent.mkdir(parents=True);candidate.touch()
        assert json.loads(marker.read_text())['extras'] == extras
        await app_updates.activate(manager)
        installs = [args for args in calls if 'install' in args]
        assert len(installs) == 2 and installs[0][-1] == installs[1][-1]
        requirement = 'git+' + app_updates.SOURCE + '@' + 'a' * 40
        if extras:
            requirement = 'amplifier-unified[' + ','.join(extras) + '] @ ' + requirement
        assert installs[0][-1] == requirement
        probes = [args for args in calls if '-c' in args]
        assert len(probes) == 2
        assert all(list(args[args.index(app_updates.PROBE) + 1:]) == extras for args in probes)
        assert service.state['updates']['pendingRestart']['version'] == '99.0.0'
    finally:
        await service.close()


@pytest.mark.parametrize('previous,current', [([], ['tui']), (['tui'], []),
    ([], ['native-desktop']), (['native-desktop'], []),
    (['tui'], ['native-desktop', 'tui']), (['native-desktop', 'tui'], ['tui']),
    (['native-desktop'], ['native-desktop', 'tui']), (['native-desktop', 'tui'], ['native-desktop'])])
async def test_changed_extras_can_restage_through_normal_app_command(tmp_path, monkeypatch, previous, current):
    service, manager, _ = await prepared_activation(tmp_path, monkeypatch)
    service.state['updates']['application'] = service.state['updates']['pendingApp']
    marker = manager.directory / 'applications' / ('a' * 40) / 'validated.json'
    marker.write_text(json.dumps({**json.loads(marker.read_text()), 'extras': previous}))
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: current)
    # Idle activation first refreshes public release metadata. Keep that
    # read-only boundary separate from the installer/probe effect witness.
    release_lookup = AsyncMock(return_value=dict(service.state['updates']['pendingApp']))
    monkeypatch.setattr(app_updates, 'check', release_lookup)
    process = AsyncMock()
    close = AsyncMock(return_value=None)
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr(service.runtime, 'close', close)
    try:
        with pytest.raises(ValueError, match='Optional features changed'):
            await manager.app()
        release_lookup.assert_awaited_once_with()
        process.assert_not_awaited()
        close.assert_not_awaited()
        assert service.state['updates']['phase'] == 'error'
        assert service.state['updates']['pendingApp'] is None
        assert service.state['updates']['appAvailable']
        assert 'Install the update again' in service.state['updates']['detail']
        calls = []

        async def install(*args, **kwargs):
            calls.append(args)
            return '99.0.0' if '-c' in args else ''

        monkeypatch.setattr(app_updates, 'process', install)
        monkeypatch.setattr(app_updates.shutil, 'which', lambda _: '/fixture/uv')
        monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda _: True)
        monkeypatch.setattr(manager, 'busy', lambda: True)
        await manager.app()
        assert json.loads(marker.read_text())['extras'] == previous  # Retained legacy receipt.
        marker = marker.parent / service.state['updates']['pendingApp']['generation'] / 'validated.json'
        assert json.loads(marker.read_text())['extras'] == current
        candidate = marker.parent / 'tools/amplifier-unified/bin/python'
        candidate.parent.mkdir(parents=True);candidate.touch()
        assert service.state['updates']['pendingApp']
        assert len(calls) == 2  # Candidate install and probe only; work stays open.
        close.assert_not_awaited()
        monkeypatch.setattr(manager, 'busy', lambda: False)
        await manager.app()
        # Keep the retained host usable until actual shutdown; an installation
        # or restart failure must not strand it with a terminally closed runtime.
        close.assert_not_awaited()
        assert service.state['updates']['pendingRestart']['version'] == '99.0.0'
    finally:
        await service.close()
    close.assert_awaited_once()


@pytest.mark.parametrize('extras', [[], ['tui'], ['native-desktop'], ['native-desktop', 'tui'],
    ['decisions-jev'], ['decisions-local'], ['decisions-openai'], ['managed-browser']])
def test_detect_optional_features_without_loading_them(monkeypatch, extras):
    distributions = {
        'native-desktop': ['amplifier-module-tool-computer-use'], 'tui': ['amplifier-app-tui'],
        'decisions-jev': ['amplifier-lib-decisions', 'amplifier-module-provider-jev'],
        'decisions-local': ['amplifier-lib-decisions', 'amplifier-module-provider-local-decisions'],
        'decisions-openai': ['amplifier-lib-decisions', 'amplifier-module-provider-openai'],
        'managed-browser': ['playwright'],
    }
    requested = []
    def distribution(name):
        requested.append(name)
        if name not in {name for extra in extras for name in distributions[extra]}:
            raise app_updates.metadata.PackageNotFoundError(name)
        return SimpleNamespace(entry_points=[SimpleNamespace(group='amplifier.decision_backends')])
    monkeypatch.setattr(app_updates.metadata, 'distribution', distribution)
    assert app_updates.installed_extras() == extras
    assert len(requested) == len(set(requested))
    assert not any(name in requested for name in ('torch', 'transformers', 'accelerate'))


@pytest.mark.parametrize('extras', [None, 'tui', ['unknown'], ['tui', 'tui'], [['tui']], [True]])
async def test_invalid_extra_receipt_rejected_before_any_activation_effect(tmp_path, monkeypatch, extras):
    service, manager, _ = await prepared_activation(tmp_path, monkeypatch)
    marker = manager.directory / 'applications' / ('a' * 40) / 'validated.json'
    marker.write_text(json.dumps({**json.loads(marker.read_text()), 'extras': extras}))
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: [])
    target, process, close = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(app_updates, 'installed_target', target)
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr(service.runtime, 'close', close)
    try:
        with pytest.raises(ValueError, match='Unsupported optional'):
            app_updates.install_requirement('a' * 40, extras)
        with pytest.raises(ValueError, match='Optional features changed'):
            await app_updates.activate(manager)
        target.assert_not_awaited()
        process.assert_not_awaited()
        close.assert_not_awaited()
        assert service.state['updates']['pendingApp'] is None
        assert json.loads(marker.read_text())['extras'] == extras  # Keep failed evidence.
    finally:
        await service.close()


@pytest.mark.parametrize('receipt', [{}, {'extras': []}, {'extras': ['tui', 'native-desktop']}])
async def test_old_empty_receipts_and_equivalent_extra_order_activate(tmp_path, monkeypatch, receipt):
    service, manager, _ = await prepared_activation(tmp_path, monkeypatch)
    marker = manager.directory / 'applications' / ('a' * 40) / 'validated.json'
    marker.write_text(json.dumps({**json.loads(marker.read_text()), **receipt}))
    extras = sorted(receipt.get('extras', []))
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: extras)
    monkeypatch.setattr(app_updates.components, 'read_graph', AsyncMock(return_value=[
        {'name':'amplifier-unified','version':'99.0.0','url':app_updates.SOURCE,'revision':'a'*40}]))
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda _: True)
    calls = []

    async def process(*args, **kwargs):
        calls.append(args)
        return '99.0.0' if '-c' in args else ''

    monkeypatch.setattr(app_updates, 'process', process)
    try:
        await app_updates.activate(manager)
        assert calls[0][-1] == app_updates.install_requirement('a' * 40, extras)
        assert list(calls[1][calls[1].index(app_updates.PROBE) + 1:]) == extras
        assert service.state['updates']['pendingRestart']['version'] == '99.0.0'
    finally:
        await service.close()


@pytest.mark.parametrize('extras,broken', [
    ([], None), (['tui'], None), (['native-desktop'], None), (['native-desktop', 'tui'], None),
    (['native-desktop', 'tui'], 'native-desktop'), (['native-desktop', 'tui'], 'tui'),
    (['unknown'], None), (['tui', 'tui'], None),
])
def test_real_isolated_probe_validates_selected_features_without_observing_desktop(tmp_path, extras, broken):
    from pathlib import Path
    import shutil
    import subprocess
    import sys
    from amplifier_web.update_diagnostics import probe_record

    # Synthetic candidate packages let the actual isolated probe run without
    # installing anything or starting a terminal/desktop client.
    package = tmp_path / 'amplifier_web'
    package.mkdir()
    (package / '__init__.py').write_text('__version__="99.0.0"\n')
    (package / 'server.py').write_text('def create_app(): pass\n')
    (package / 'static').mkdir()
    (package / 'static/index.html').touch()
    source = Path(app_updates.__file__).parent
    shutil.copy2(source / 'runtime_bootstrap.py', package)
    shutil.copy2(source / 'collaboration_input.py', package)
    shutil.copy2(source / 'task_binding.py', package)
    shutil.copytree(source.parent / 'amplifier_operations', tmp_path / 'amplifier_operations')
    shutil.copytree(source.parent / 'amplifier_kernels', tmp_path / 'amplifier_kernels')
    (tmp_path / 'pam.py').write_text('def authenticate(*args): pass\n')
    desktop = tmp_path / 'amplifier_module_tool_computer_use'
    desktop.mkdir()
    (desktop / '__init__.py').touch()
    if broken != 'native-desktop':
        (desktop / 'foreground.py').write_text(
            'def local_observer(): raise AssertionError("must not observe desktop")\n'
            'class ForegroundUnavailable(RuntimeError): pass\n')
    tui = tmp_path / 'amplifier_tui'
    tui.mkdir()
    (tui / '__init__.py').touch()
    (tui / 'connected.py').write_text('def main(): raise AssertionError("must not launch TUI")\n')
    binary = tmp_path / 'bin/fixture-tui'
    binary.parent.mkdir()
    binary.touch()
    binary.chmod(0o600 if broken == 'tui' else 0o700)
    (tui / 'launcher.py').write_text(
        'from pathlib import Path\n'
        'def executable(_): return Path(__file__).parent.parent/"bin/fixture-tui"\n')
    wrapper = 'import sys;sys.prefix=sys.argv.pop(1);sys.path.insert(0,sys.prefix);exec(sys.argv.pop(1))'
    result = subprocess.run([sys.executable, '-I', '-c', wrapper, str(tmp_path), app_updates.PROBE, *extras],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    report = probe_record(result.stdout)
    expected = broken is None and extras not in (['unknown'], ['tui', 'tui'])
    assert result.returncode == (0 if expected else 1)
    assert report['ok'] is expected and report['isolated'] is True
    if broken:
        assert report['stage'] == ('capabilities' if broken == 'native-desktop' else 'terminal')
    if expected:
        assert report['stage'] == 'complete' and report['version'] == '99.0.0'
        assert report['workerImportsAvailable'] is True
