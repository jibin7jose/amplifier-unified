"""Isolated browser fixture: real task actions/receipts, controlled DTU transport."""
import argparse
import asyncio
from collections import Counter
import json
import os
import signal
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web import task_environments as environments

TOKEN = 'fixture-browser-control-token'


class Runtime:
    def __init__(self):
        self.calls = []

    async def start(self, session, emit, **kwargs):
        self.calls.append('start')
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready'})

    async def stop(self, *args, **kwargs):
        self.calls.append('stop')

    async def close(self):
        pass

    async def control(self, *args, **kwargs):
        self.calls.append('control')
        return {}


async def main(home, port, backend='docker'):
    home = home.resolve()
    os.environ['AMPLIFIER_HOME'] = str(home / 'native')
    os.environ['AMPLIFIER_WEB_HOME'] = str(home)
    os.environ['AMPLIFIER_UNIFIED_IMPORT_HOME'] = str(home / 'legacy')
    workspace = home / 'workspace'
    workspace.mkdir()
    (workspace / 'check.py').write_text("print('workspace check passed')\n")
    calls, states, mode = [], {}, {'executeTimeout': False, 'capacityAvailable': True,
                                 'backendAvailable': True, 'longOutput': False, 'executeFailure': False,
                                 'setupRequired': False, 'artifactTimeout': False,
                                 'removeUnknown': False, 'removeFailure': False, 'finishCleanupFailure': False}
    setup_calls = []
    probes = Counter()
    lock = threading.Lock()
    config = {'python': '/fixture/managed/python', 'dockerHost': 'unix:///fixture/docker.sock', 'image': environments.APPROVED_IMAGE}
    if backend == 'incus':
        config = {'python': '/fixture/managed/python', 'backend': 'incus',
                  'incus': {'project': 'fixture-project', 'daemonFingerprint': 'a' * 64}}
    # The controlled transport models a qualified Linux backend on every test host.
    environments.SUPPORTED_HOST = True
    def configuration(app):
        probes['configuration'] += 1
        return (None, 'disabled', 'Set up the controlled test runner.') if mode['setupRequired'] else (config, 'ready', 'Browser fixture: controlled Linux test backend; no Docker or provider calls.') if mode['backendAvailable'] else (None, 'unavailable', 'New execution is unavailable in this controlled fixture.')
    environments.configuration = configuration
    async def setup_read(app):
        probes['setupRead'] += 1
        return {'status': 'disabled' if mode['setupRequired'] else 'ready',
                'reason': 'Controlled fixture; no backend installation.', 'operation': None,
                'backends': [{'id': name, 'label': label, 'available': True, 'reason': 'Controlled fixture'}
                             for name, label in [('docker', 'Docker'), ('incus', 'Incus')]]}
    async def setup_request(app, args, actor, guard=None, *, authority=None, origin=None):
        if guard:
            await guard()
        setup_calls.append(dict(args))
        mode['setupRequired'] = False
        return {'operation': {'state': 'completed'}}
    environments.setup.read = setup_read
    environments.setup.request = setup_request
    environments.dependencies.prepare = lambda config, root, snapshot, profile_id, request_id: {
        'status': 'ready', 'profileId': profile_id, 'sourceSha256': snapshot['archive_sha256']}
    environments.dependencies.cleanup = lambda *args: {'complete': True, 'status': 'removed'}
    def validate_configuration(app, value):
        probes['retainedConfiguration'] += 1
        return {**value, 'python': config['python']}, 'ready', 'Retained controlled fixture backend.'
    environments.validate_configuration_value = validate_configuration
    backend_identity = ({'backend': 'incus', 'daemon': 'a' * 64, 'project': 'fixture-project'}
                        if backend == 'incus' else {'backend': 'docker', 'daemon': 'fixture-daemon'})
    def invoke(config, home, row, action, args):
        with lock:
            calls.append({'action': action, 'requestId': args.get('requestId'), 'resourceId': row['id'],
                          'sessionId': row['sessionId'], 'workspaceId': row['workspaceId'],
                          'backendKey': environments.config_key(config), 'backend': config.get('backend', 'docker'),
                          **({'retentionMode': args.get('retentionMode', config.get('retentionMode', 'files'))}
                             if action == 'remove' else {})})
            identity = 'fixture-' + row['id']
            if action == 'prepare':
                (environments.storage.directory(home, row) / 'source.tar').write_bytes(b'fixture-retained-source')
                return {'ok': True, 'universeId': identity, 'backendIdentity': backend_identity, 'snapshot': {'snapshot_commit': 'fixture-snapshot', 'snapshot_tree': 'fixture-tree',
                    'source_head': 'fixture-head', 'source_branch': 'fixture-branch', 'files': 1, 'archive_bytes': 23, 'archive_sha256': 'fixture-archive'}}
            if action == 'start':
                states[row['id']] = 'running'
            elif action == 'stop':
                states[row['id']] = 'stopped'
            elif action == 'remove' and mode['removeUnknown']:
                return {'ok': False, 'status': 'unknown', 'universeId': identity,
                        'message': 'Synthetic removal outcome unknown; no retry is implied.'}
            elif action == 'remove' and mode['removeFailure'] or action == 'finishCleanup' and mode['finishCleanupFailure']:
                return {'ok': False, 'status': 'stopped', 'effectAdmitted': False, 'phase': 'verifying', 'code': 'archive_verification_failed'}
            elif action in {'remove', 'finishCleanup'}:
                states[row['id']] = 'removed'
            elif action == 'execute' and mode['executeTimeout']:
                return {'ok': False, 'status': 'unknown', 'universeId': identity,
                    'message': 'The fixture command timed out. It may still be running; inspect or stop it without replaying this request.'}
            value = {'ok': True, 'universeId': identity, 'status': states.get(row['id'], 'unknown')}
            if action == 'execute':
                output = ('queue-page-start\n' + 'retained check line\n' * 400 + 'queue-page-end\n'
                          if mode['longOutput'] else 'workspace check passed\n')
                value['result'] = {'exit_code': 1 if mode['executeFailure'] else 0,
                    'stdout': output, 'stderr': 'fixture check failed\n' if mode['executeFailure'] else '',
                    'stdout_truncated': False, 'stderr_truncated': False}
                if args.get('durableJob'):
                    value['job'] = {'id': args['requestId'], 'version': 1,
                                    'state': 'failed' if mode['executeFailure'] else 'passed', 'terminal': True}
            elif action == 'job_read':
                value.update(ok=False, job={'id': args['requestId'], 'version': 1, 'state': 'unknown', 'terminal': False})
            if action == 'reconcile':
                value['observation'] = {'backendIdentity': backend_identity, 'rootOwner': row['sessionId'],
                    'operationId': row['launchOperationId'], 'universeId': identity, 'ownership': 'matched', 'absenceConfirmed': False}
            if action == 'exportArtifacts':
                if mode['artifactTimeout']:
                    return {'ok': False, 'status': 'unknown', 'message': 'Artifact export acknowledgement lost.'}
                value['artifactExport'] = {'verified': True, 'selection': ['/artifacts'],
                    'archiveBytes': 1024, 'logicalBytes': 24, 'format': 'tar'}
                value['archives'] = []  # Controlled transport: no real archive claim.
            if action in {'remove', 'finishCleanup'}:
                value['retention'] = {'verified': True, 'sourceBytes': 23, 'archiveBytes': 2025,
                    'totalBytes': 2048, 'format': 'tar', 'archiveOnly': action == 'finishCleanup',
                    'policy': args.get('retentionMode', config.get('retentionMode', 'files'))}
            return value
    environments.invoke = invoke
    def capacity(app, config):
        probes['capacity'] += 1
        return {'available': mode['capacityAvailable'],
            'counts': {'active': 0 if mode['capacityAvailable'] else 1}, 'limits': {'max_running': 1},
            'blockers': [] if mode['capacityAvailable'] else [{'code': 'execution_capacity'}]}
    environments.capacity = capacity
    runtime = Runtime()
    app = await create_app(home, workspace=workspace, runtime=runtime, voice=False, background_updates=False)
    app['control_token'] = TOKEN
    app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
    service = app['service']
    await service.dispatch('session.create', {'title': 'Test environment acceptance', 'workspace': str(workspace), 'bundle': 'anchors'})
    await service.dispatch('view.update', {'patch': {'setupJourney': 'done', 'panel': None}})
    def authorize(request):
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise web.HTTPForbidden()
    async def observations(request):
        authorize(request)
        sid = service.state['selectedSessionId']
        with lock:
            observed = {'calls': list(calls), 'setupCalls': list(setup_calls), 'callCounts': dict(Counter(call['action'] for call in calls)), 'states': dict(states), 'mode': dict(mode),
                        'probeCounts': dict(probes)}
        observed['resources'] = [{key: row.get(key) for key in ('id', 'sessionId', 'workspaceId',
            'revision', 'backendKey', 'backendType', 'retentionMode', 'environmentState',
            'lastOperation', 'lastArtifactExport', 'artifactExport')}
            for identity in dict.fromkeys(call['resourceId'] for call in observed['calls'])
            for row in [environments.get(home, sid, identity)]]
        observed['requests'] = service.operations.requests.list(sid)
        observed['sessionId'] = sid
        observed['workspaceId'] = next(row['id'] for row in service.state['workspaces'] if row['path'] == str(workspace))
        observed['messages'] = service.state['sessions'][0].get('messages', [])
        observed['runtimeCalls'] = list(runtime.calls)
        observed['workspaceFile'] = (workspace / 'check.py').read_text()
        return web.json_response(observed)
    async def change_mode(request):
        authorize(request)
        value = await request.json()
        with lock:
            mode['executeTimeout'] = bool(value.get('executeTimeout', value.get('mode') == 'execute-timeout'))
            if 'capacityAvailable' in value:
                mode['capacityAvailable'] = bool(value['capacityAvailable'])
            if 'backendAvailable' in value:
                mode['backendAvailable'] = bool(value['backendAvailable'])
            for key in ('longOutput', 'executeFailure', 'setupRequired', 'artifactTimeout', 'removeUnknown', 'removeFailure', 'finishCleanupFailure'):
                if key in value:
                    if type(value[key]) is not bool:
                        raise web.HTTPBadRequest(text=key + ' must be boolean')
                    mode[key] = value[key]
        service.test_queue.backoff.clear()
        service.test_queue.wakeup.set()
        return web.json_response(mode)
    app.router.add_get('/fixture/environments', observations)
    app.router.add_post('/fixture/environment-mode', change_mode)
    return app


async def serve(home, port, backend):
    app = await main(home, port, backend)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    stopped = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    print(json.dumps({'url': url, 'sessionId': app['service'].state['selectedSessionId']}), flush=True)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--backend', choices=('docker', 'incus'), default='docker',
                        help='Controlled transport capability only; never contacts Docker or Incus.')
    parser.add_argument('--interactive', action='store_true', help='Enable fixture-only login for manual browser acceptance on loopback.')
    options = parser.parse_args()
    if options.interactive:
        # This disposable fixture has no real account, provider or backend data.
        # Preserve the real login/cookie/CSRF flow for computer-use acceptance.
        from amplifier_web import auth
        auth.authenticate_pam = lambda username, password: username == 'fixture' and password == TOKEN
    with tempfile.TemporaryDirectory(prefix='amplifier-environments-ui-') as temporary:
        asyncio.run(serve(Path(temporary), options.port, options.backend))
