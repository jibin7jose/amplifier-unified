"""Real chat navigation and persistence with disposable histories; no model calls."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

source_root = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, source_root)
# Query helpers inherit the same source selection even with an older installed app.
os.environ['PYTHONPATH'] = os.pathsep.join(filter(None, (source_root, os.environ.get('PYTHONPATH'))))

from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web.session_files import project_slug


class ObservedRuntime:
    def __init__(self):
        self.started = []
        self.sent = []
        self.jobs = {}

    async def start(self, session, emit):
        self.started.append(session['id'])
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready'})

    async def send(self, session, text, input_id, emit):
        self.sent.append(session['id'])
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'working'})
        if os.environ.get('AMPLIFIER_SHELL_PROOF') == '1' and text == 'Hold work during shell changes':
            self.jobs[session['id']] = asyncio.create_task(asyncio.Event().wait())
            return
        await emit('assistant.message', {'sessionId': session['id'], 'text': 'Fixture reply to a real submitted turn.', 'inputId': input_id})
        await emit('runtime.generation', {'sessionId': session['id'], 'event': 'generation.finished',
                   'generation_id': 'fixture-turn', 'input_ids': [input_id], 'active_job_ids': [], 'disposition': 'manager_turn_finished'})
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})

    async def control(self, *args):
        return {}

    async def stop(self, *args, reason="stopped"):
        pass

    async def close(self):
        for task in self.jobs.values():
            task.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)


def native_chat(home, workspace, identity, title, at, *, exists=True, worker=False):
    if exists:
        workspace.mkdir(parents=True, exist_ok=True)
    directory = home / 'projects' / project_slug(workspace) / 'sessions' / identity
    directory.mkdir(parents=True)
    metadata = {'session_id': identity, 'bundle': 'bundle:anchors', 'name': title,
                'working_dir': str(workspace), 'created': '2020-01-01T00:00:00Z', 'turn_count': 1}
    if worker:
        metadata['parent_id'] = 'alpha-201'
    (directory / 'metadata.json').write_text(json.dumps(metadata))
    transcript = directory / 'transcript.jsonl'
    transcript.write_text(''.join(json.dumps(row) + '\n' for row in [
        {'role': 'user', 'content': 'Saved question'},
        {'role': 'assistant', 'content': ('Saved response for ' + identity
          if os.environ.get('AMPLIFIER_NAVIGATION_GAPS') == '1' else 'Saved response')},
    ]))
    os.utime(transcript, (at, at))


async def main():
    with tempfile.TemporaryDirectory(prefix='amplifier-chat-library-') as directory:
        temp = Path(directory).resolve()
        home, data = temp / 'native', temp / 'app'
        os.environ.update(AMPLIFIER_HOME=str(home), AMPLIFIER_WEB_HOME=str(data),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(temp / 'legacy'),
                          AMPLIFIER_SESSION_STATE_HOME=str(temp / 'session-state'))
        paths = {'one': temp / 'folders/development/playground',
                 'two': temp / 'folders/research/playground',
                 'missing': temp / 'folders/deleted/playground'}
        for index in range(202):
            title = 'Review very long workspace navigation names without changing the height of neighboring rows' if index == 201 and os.environ.get('AMPLIFIER_NAVIGATION_PROOF') == '1' else f'Alpha {index:03}'
            native_chat(home, paths['one'], f'alpha-{index:03}', title, 1_700_000_000 + index)
        for index in range(3):
            native_chat(home, paths['two'], f'beta-{index:03}', f'Beta {index:03}', 1_700_001_000 + index)
        native_chat(home, paths['one'], 'worker-only', 'Hidden worker', 1_700_009_000, worker=True)
        native_chat(home, paths['missing'], 'missing-root', 'Deleted workspace chat', 1_700_010_000, exists=False)
        if os.environ.get('AMPLIFIER_NAVIGATION_GAPS') == '1':
            # Opt-in page boundary and ancestor-only folder, isolated from all
            # existing fixtures and never backed by a user's workspace.
            for index in range(105):
                native_chat(home, temp / f'folders/paged/team-{index:03}',
                            f'paged-{index:03}', f'Paged {index:03}', 1_700_020_000 + index)
        from configured_browser import seed_connection
        seed_connection(home)
        runtime = ObservedRuntime()
        stopped, restart = asyncio.Event(), asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        port, generation, initial, quiet = 0, 0, None, None
        while not stopped.is_set():
            restart.clear()
            app = await create_app(data, workspace=paths['one'], runtime=runtime,
                                   voice=False, background_updates=False, preload_providers=False)
            app['control_token'] = 'fixture-browser-control-token'
            service = app['service']
            await service.history.refresh()
            async with asyncio.timeout(30):
                while service.state.get('sharedHistory', {}).get('reconciliation', {}).get('phase') != 'complete':
                    if service.state.get('sharedHistory', {}).get('error'):
                        raise AssertionError(service.state['sharedHistory']['error'])
                    await service.history.refresh(force=False)
                    await asyncio.sleep(.01)
            if generation == 0:
                await service.dispatch('session.create', {'title': 'Quiet older chat', 'workspace': str(paths['one'])})
                quiet = service.state['selectedSessionId']
                chat = service._session(quiet)
                service._message(chat, 'user', 'An earlier fixture question')
                service._message(chat, 'assistant', 'An earlier fixture response')
                chat.update(createdAt=1_600_000_000, updatedAt=1_600_000_000,
                            recentActivityAt=1_600_000_000, navigationActivityAt=1_600_000_000,
                            deferRuntimeUntilInteraction=True)
                chat.pop('navigationActivityPending', None)
                for row in chat['messages']:
                    row['createdAt'] = 1_600_000_000
                initial = service.directory.resolve('alpha-201')['id']
                await service.dispatch('session.select', {'id': initial})
                await service.dispatch('view.update', {'patch': {'navPinned': True}})
                service._save()
            if generation == 0 and os.environ.get('AMPLIFIER_NAVIGATION_PROOF') == '1':
                by_native = {identity: service._session(identity, hydrate=False)
                             for identity in ('alpha-200', 'alpha-199', 'alpha-198', 'alpha-197')}
                by_native['alpha-200'].update(status='working', recentActivityAt=time.time()-120)
                by_native['alpha-199'].update(approvals=[{'id': 'navigation-approval', 'title': 'Allow workspace access', 'status': 'pending'}], recentActivityAt=time.time()-240)
                by_native['alpha-198'].update(completion={'id': 'navigation-completion', 'at': time.time()-600}, recentActivityAt=time.time()-600)
                by_native['alpha-197'].update(error='Fixture operation failed', status='error', recentActivityAt=time.time()-900)
                service._save()
            generation += 1

            async def info(request):
                return web.json_response({'paths': {key: str(value) for key, value in paths.items()},
                    'initialSession': initial, 'quietSession': quiet, 'generation': generation,
                    'runtimeJobs': [key for key, task in runtime.jobs.items() if not task.done()], 'runtimeStarts': runtime.started, 'runtimeSends': runtime.sent, 'state': service.get_state()})

            async def agent(request):
                payload = await request.json()
                result = await service.app_bridge(payload.get('operation', 'dispatch'), payload['args'],
                                                 service.state.get('selectedSessionId'))
                return web.json_response(result)

            async def restart_server(request):
                asyncio.get_running_loop().call_later(.1, restart.set)
                return web.json_response({'restarting': True, 'generation': generation})

            async def directory_info(request):
                # This proof must not enumerate the dormant library to inspect it.
                row = service.directory.resolve(initial)
                resident = next((item for item in service.state['sessions'] if item['id'] == initial), {})
                return web.json_response({'initialSession': initial, 'generation': generation,
                    'rootCount': service.directory.root_count(service.state),
                    'residentCount': len(service.state['sessions']),
                    'title': row['title'], 'available': row.get('workspaceAvailable'),
                    'residentAvailable': resident.get('workspaceAvailable'),
                    'residentReadOnlyReason': resident.get('historyReadOnlyReason'),
                    'physicalAvailable': paths['one'].is_dir(),
                    'runtimeStarts': runtime.started, 'runtimeSends': runtime.sent})

            async def directory_change(request):
                payload = await request.json()
                if 'title' in payload:
                    metadata = home / 'projects' / project_slug(paths['one']) / 'sessions' / 'alpha-201' / 'metadata.json'
                    value = json.loads(metadata.read_text())
                    value.update(name=str(payload['title']), name_revision=value.get('name_revision', 0) + 1)
                    metadata.write_text(json.dumps(value))
                if 'available' in payload:
                    parked = paths['one'].with_name('temporarily-unavailable')
                    source, destination = (parked, paths['one']) if payload['available'] else (paths['one'], parked)
                    source.rename(destination)
                # Force the observation into a later cooperative slice. A
                # forced first slice must not hide missing delta propagation.
                budget = service.history.index._recovery_budget
                service.history.index._recovery_budget = 1
                try:
                    await service.history.refresh(force=True)
                    async with asyncio.timeout(30):
                        while service.state.get('sharedHistory', {}).get('reconciliation', {}).get('phase') != 'complete':
                            await service.history.refresh(force=False)
                            await asyncio.sleep(.01)
                finally:
                    service.history.index._recovery_budget = budget
                assert not service.state.get('sharedHistory', {}).get('error')
                assert not service.state.get('sharedHistory', {}).get('issues')
                return await directory_info(request)

            async def update_status(request):
                payload = await request.json()
                await service.update_manager.publish(
                    phase='activating' if payload.get('restarting') else 'installed',
                    pendingRestart={'version': '99.0.0'} if payload.get('restarting') else None,
                    error=None)
                return web.json_response({'ok': True})

            app.router.add_post('/api/fixture/update-status', update_status)
            app.router.add_get('/api/fixture/info', info)
            app.router.add_post('/api/fixture/agent', agent)
            app.router.add_post('/api/fixture/restart', restart_server)
            app.router.add_get('/api/fixture/directory', directory_info)
            app.router.add_post('/api/fixture/directory', directory_change)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', port)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
            if generation == 1:
                print(json.dumps({'port': port}), flush=True)
            waits = [asyncio.create_task(stopped.wait()), asyncio.create_task(restart.wait())]
            try:
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in waits:
                    task.cancel()
                await asyncio.gather(*waits, return_exceptions=True)
                await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
