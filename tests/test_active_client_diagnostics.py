"""Fixture diagnostics preserve admission behavior and isolate concurrent spans."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
import pytest
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('fixture_diagnostics', ROOT / 'tests/fixtures/active_client_diagnostics.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.asyncio
async def test_admission_spans_are_bounded_and_preserve_returns():
    saved = []
    service = SimpleNamespace(lock=asyncio.Lock(), _save_changes=lambda **args: saved.append(args) or 'saved')
    diagnostics = module.AdmissionDiagnostics(service)
    request = SimpleNamespace(path='/fixture/admit')

    async def handler(_):
        diagnostics.call('synthetic-admission-' + str(len(saved)))
        with diagnostics.span('capacity'):
            async with service.lock:
                assert service._save_changes(sessions={'owner'}) == 'saved'
        with diagnostics.span('receiptRead', cpu=True):
            assert saved[-1] == {'sessions': {'owner'}}
        return web.json_response({'allowed': True})

    for _ in range(40):
        response = await diagnostics.middleware(request, handler)
        assert response.status == 200
        assert response.headers['X-Fixture-Request-Id'] == diagnostics.snapshot()[-1]['requestId']
        assert response.headers['Server-Timing'].startswith('fixture;dur=')
    rows = diagnostics.snapshot()
    assert len(rows) == 32 and rows[0]['callId'] == 'synthetic-admission-8'
    assert len(saved) == 40 and not service.lock.locked()
    assert all(row['saveCalls'] == row['lockWaitCalls'] == row['capacityCalls'] == 1 for row in rows)
    assert all(row['capacityMs'] >= row['saveMs'] >= row['saveThreadCpuMs'] - 1 for row in rows)
    assert diagnostics.current.get() is None


@pytest.mark.asyncio
async def test_unrelated_task_is_not_charged_to_waiting_admission():
    release, saved = asyncio.Event(), []
    service = SimpleNamespace(lock=asyncio.Lock(), _save_changes=lambda **args: saved.append(args))
    diagnostics = module.AdmissionDiagnostics(service)

    async def background():
        await release.wait()
        service._save_changes(sessions={'unrelated'})
        service.lock.release()

    await service.lock.acquire()
    background_task = asyncio.create_task(background())

    async def handler(_):
        diagnostics.call('synthetic-admission-waiting')
        release.set()
        async with service.lock:
            service._save_changes(sessions={'admission'})
        return web.json_response({'allowed': True})

    await diagnostics.middleware(SimpleNamespace(path='/fixture/admit'), handler)
    await background_task
    row = diagnostics.snapshot()[0]
    assert saved == [{'sessions': {'unrelated'}}, {'sessions': {'admission'}}]
    assert row['saveCalls'] == row['lockWaitCalls'] == 1


@pytest.mark.asyncio
async def test_failure_and_lock_cancellation_keep_original_authority():
    error = RuntimeError('original save failure')
    def save(**_):
        raise error
    service = SimpleNamespace(lock=asyncio.Lock(), _save_changes=save)
    diagnostics = module.AdmissionDiagnostics(service)
    request = SimpleNamespace(path='/fixture/admit')

    async def failed(_):
        async with service.lock:
            service._save_changes(sessions={'owner'})

    with pytest.raises(RuntimeError) as raised:
        await diagnostics.middleware(request, failed)
    assert raised.value is error and not service.lock.locked()
    assert diagnostics.snapshot()[-1]['errorType'] == 'RuntimeError'
    assert diagnostics.snapshot()[-1]['saveCalls'] == 1
    await service.lock.acquire()
    waiting = asyncio.create_task(diagnostics.middleware(request, failed))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert service.lock.locked()  # Diagnostic cancellation never releases someone else's lock.
    service.lock.release()
    assert diagnostics.snapshot()[-1]['errorType'] == 'CancelledError'
    assert 'saveCalls' not in diagnostics.snapshot()[-1]
    assert diagnostics.current.get() is None


def test_failed_browser_always_retains_active_client_evidence():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    steps = workflow['jobs']['browser']['steps']
    run = next(step for step in steps if 'scripts/browser_check_runner.py' in step.get('run', '')
               and '--initialize' not in step['run'])
    assert '--plan tests/web/browser-checks.json' in run['run']
    # Report ownership and distinct destinations belong to the runner contract
    # in test_browser_check_runner; this check guards failed-run retention.
    artifact = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@')
                    and 'output/active-client-performance/' in step.get('with', {}).get('path', '').splitlines())
    assert artifact['if'] == 'always()'
    assert 'output/browser-checks/' in artifact['with']['path'].splitlines()
