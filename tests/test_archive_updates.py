"""Archive activation keeps the existing replacement and generation owners."""
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates, application_generations as generations
from test_app_component_graph import staged
from test_archive_readiness import installation, manager_for


@pytest.mark.parametrize('compatible', [False, True])
async def test_archive_activation_selects_verified_reinstall_or_qualified_promotion(tmp_path, monkeypatch, compatible):
    service, manager, folder, calls = await staged(tmp_path, monkeypatch)
    bootstrap = tmp_path / 'bootstrap'
    info = bootstrap / 'lib/python3.13/site-packages/amplifier_unified-0.1.0.dist-info'
    info.mkdir(parents=True)
    (info / 'METADATA').write_text('Name: amplifier-unified\nVersion: 0.1.0\n')
    original = app_updates.installed_target
    async def installed_target():
        uv, executable, python, previous = await original()
        return uv, executable, python, {**previous, 'installation': str(bootstrap)}
    monkeypatch.setattr(app_updates, 'installed_target', installed_target)
    monkeypatch.setattr(generations, 'supports', lambda _: True)
    reader = AsyncMock(return_value=compatible)
    monkeypatch.setattr(generations, 'can_read_candidate', reader)
    receipt = json.loads((folder / 'validated.json').read_text())
    # Promotion needs the installed environment, not retained installation input.
    if compatible:
        Path(receipt['artifact']['path']).unlink()
        # This fixture models installed packages. Its default graph helper reads
        # the tiny wheel, unlike the production installed-metadata probe.
        monkeypatch.setattr(app_updates.components, 'read_graph', AsyncMock(return_value=receipt['componentGraph']))
    try:
        await app_updates.activate(manager)
        reader.assert_awaited_once()
        installs = [call for call in calls if 'install' in call]
        assert len(installs) == (1 if compatible else 2)
        if not compatible:
            assert installs[0][-1] == installs[1][-1] == app_updates.artifacts.requirement(receipt['artifact'])
            probes = [call for call in calls if app_updates.PROBE in call]
            assert Path(probes[-1][0]) == bootstrap / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        assert bool(generations.active(manager.home)) == compatible
        pending = service.state['updates']['pendingRestart']
        assert pending['qualification']['artifact'] == receipt['artifact']
        app = next(row for row in receipt['componentGraph'] if row['name'] == 'amplifier-unified')
        assert pending['qualification']['app'] == app
        assert 'installation' not in pending['qualification']  # Successor observes its own binding.
        assert service.state['updates'].get('applicationResult') is None
    finally:
        await service.close()


async def test_archive_successor_acknowledges_only_after_its_authenticated_listener_starts(installation, unused_tcp_port):
    import asyncio
    from aiohttp import web
    from amplifier_web.deployment import validate_server
    from amplifier_web.server import create_app
    from amplifier_web import app_features, update_readiness
    from test_service import Runtime

    service, _ = manager_for(installation, {'phase': 'activating', 'pendingRestart': installation.target})
    await service.close()
    app = await create_app(installation.home, workspace=installation.home, runtime=Runtime(), voice=False,
        background_updates=False, preload_providers=False, background_work=False,
        server_config=validate_server({'port': unused_tcp_port, 'bind': ['127.0.0.1']}))
    manager = app['service'].update_manager
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        await asyncio.sleep(.05)
        assert app['service'].state['updates']['pendingRestart'] == installation.target
        assert not app['service'].state['updates'].get('applicationResult')
        await web.TCPSite(runner, '127.0.0.1', unused_tcp_port).start()
        await asyncio.wait_for(manager.readiness_task, 3)
        state = app['service'].state['updates']
        assert state['pendingRestart'] is None
        assert state['applicationResult']['phase'] == 'restart-ack'
        assert state['applicationResult']['qualification'] == installation.proof
        assert update_readiness.artifact_context(manager) == installation.artifact
        assert app_features.running_application(manager) == installation.app
    finally:
        await runner.cleanup()


@pytest.mark.parametrize('changed', ['wheel', 'installed-metadata', 'reader-error'])
async def test_archive_recheck_failure_preserves_serving_host_before_replacement_fence(tmp_path, monkeypatch, changed):
    service, manager, folder, calls = await staged(tmp_path, monkeypatch)
    receipt = json.loads((folder / 'validated.json').read_text())
    close = AsyncMock()
    monkeypatch.setattr(service.runtime, 'close', close)
    if changed == 'wheel':
        with Path(receipt['artifact']['path']).open('ab') as stream:
            stream.write(b'changed')
    elif changed == 'installed-metadata':
        path = next((folder / 'tools/amplifier-unified').rglob('METADATA'))
        path.write_text(path.read_text() + 'Summary: replaced\n')
    else:
        monkeypatch.setattr(generations, 'supports', lambda _: True)
        monkeypatch.setattr(generations, 'can_read_candidate', AsyncMock(side_effect=ValueError('Invalid candidate')))
    try:
        with pytest.raises(ValueError):
            await app_updates.activate(manager)
        close.assert_not_awaited()
        assert len([call for call in calls if 'install' in call]) == 1
        assert not service.state['updates'].get('pendingReplacement')
        assert not service.state['updates'].get('pendingRestart')
        assert not (manager.directory / 'previous-app.json').exists()
        assert generations.active(manager.home) is None
    finally:
        await service.close()
