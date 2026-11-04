"""Real HTTP/SSE Updates UI with synthetic transitions, no installation/model work."""
import asyncio
import copy
import json
import time
from aiohttp import web
from settings_ui_server import main as settings_app
from pathlib import Path
import tempfile

async def main(home):
    app=await settings_app(home)
    service=app['service']
    manager=service.update_manager
    calls = {'check': 0, 'install': 0, 'rollback': 0}
    service.state['settings']['updates'].update(autoCheck=True, autoInstall=False, intervalHours=7)
    async def check(*args, **kwargs):
        calls['check'] += 1
        await manager.publish(phase='checking', error=None)
        await asyncio.sleep(.05)
        await manager.publish(phase='error' if calls['check']==1 else 'installed',
            error='Synthetic update lookup failed. Check again.' if calls['check']==1 else None)
    async def forbidden_install(*args, **kwargs):
        calls['install'] += 1
        raise RuntimeError('Fixture forbids installation effects')
    async def forbidden_rollback(*args, **kwargs):
        calls['rollback'] += 1
        raise RuntimeError('Fixture forbids rollback effects')
    manager.check = check
    manager.install = forbidden_install
    manager.rollback = forbidden_rollback
    async def observations(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        async with service.lock:
            return web.json_response({'calls': calls, 'preferences': service.state['settings']['updates']})
    app.router.add_get('/fixture/update-observations', observations)
    diagnostics=manager.diagnostics
    diagnostics.begin('ecosystem','a'*32,tier='included',components=['foundation'])
    diagnostics.record('ecosystem-activation','succeeded')
    diagnostics.begin('ecosystem','b'*32,tier='other',components=['amplifier-module-hooks-approval'])
    attempt=diagnostics.state['attemptId']
    unrelated_control = None
    original_application = dict(service.state['updates']['application'])
    original_notice_start = service.state['releaseNoticeStartVersion']
    summary_notes = [{'version': version, 'title': title, 'changes': [change], 'notices': []}
        for version, title, change in [('0.20.207', 'Later release', 'A later improvement.'),
            ('0.20.206', 'Clearer update summaries', 'See what an update includes before installing.'),
            ('0.20.205', 'Earlier installed release', 'Earlier improvements.')]]
    summary_notes[1]['notices'] = [{'id': 'summary-guidance', 'title': 'Example important guidance',
        'detail': 'Synthetic guidance retained separately.', 'action': 'Review your settings.'}]
    service.state['updates'].update(initialPreparation=False,phase='validating',error=None,pendingRelease=None,
        sequence={'stage':'other','install':True},
        probeProgress={'attemptId':attempt,'phase':'prepare','completed':24,'total':37,'startedAt':time.time()})
    async def transition(request):
        nonlocal unrelated_control
        stage=(await request.json())['stage']
        if stage == 'unrelated-control':
            unrelated_control = {'updates': {key: copy.deepcopy(service.state['updates'].get(key))
                for key in ('phase', 'pendingApp', 'pendingRelease', 'error')},
                'management': copy.deepcopy(service.state.get('management', {})), 'sessions': []}
            for index in range(3):
                session = service._new_session({'title': f'Running fixture chat {index + 1}'})
                session['status'] = 'working'
                service.state['sessions'].append(session)
                unrelated_control['sessions'].append(session['id'])
            await service.management.command_transition('runtime.control', {'operation': 'goal.get'},
                'fixture-unrelated-control', 'queued')
            await service.management.command_transition('runtime.control', {'operation': 'goal.get'},
                'fixture-unrelated-control', 'error', 'ValueError: Unsupported runtime control: goal.get')
            await manager.publish(phase='app-staged', pendingApp=dict(original_application),
                pendingRelease=None, error=None)
        elif stage == 'unrelated-control-update-failed':
            await manager.publish(phase='error', error='Synthetic restart failed; the update remains fenced.')
        elif stage == 'unrelated-control-reset':
            service.state['sessions'] = [row for row in service.state['sessions'] if row['id'] not in unrelated_control['sessions']]
            service.state['management'] = unrelated_control['management']
            # Restore only this scenario's fields through the updater owner;
            # replacing its diagnostics object would detach the receipt writer.
            await manager.publish(**unrelated_control['updates'])
            service._publish_changes(globals={'management'})
            unrelated_control = None
        elif stage == 'summary-before':
            # Simulate the install's notice history as well as its displayed version.
            service.state['releaseNoticeStartVersion'] = '0.20.205'
            await manager.publish(phase='checked', error=None, pendingApp=None, pendingRestart=None,
                pendingReplacement=None, pendingSmartTools=[], pendingRelease=None, applicationResult=None,
                sequence={'stage':'application','install':False},
                application={**original_application,'current':'0.20.205','latest':'v0.20.206',
                    'status':'update','revision':'b'*40,'runningRevision':'a'*40,'releaseNotes':summary_notes})
        elif stage == 'summary-queued':
            application = service.state['updates']['application']
            await manager.publish(phase='app-staged', pendingApp=dict(application),
                application={**application,'latest':'v0.20.207','revision':'e'*40},
                sequence={'stage':'application','install':True})
        elif stage == 'summary-restarting':
            await manager.publish(phase='activating', pendingApp=None,
                pendingRestart={'version':'0.20.206','requestStatus':'uncertain'})
        elif stage == 'summary-after':
            # This transition models only the runner remaining after installation.
            application = {**service.state['updates']['application'],'current':'0.20.206',
                'latest':'v0.20.206','status':'current','runningRevision':'b'*40}
            await manager.publish(phase='staged', application=application, pendingRestart=None,
                pendingSmartTools=[{'id':'runner','runnerSource':True}],
                sequence={'stage':'included','install':True},
                applicationResult={'attemptId':'c'*32,'phase':'restart-ack','version':'0.20.206',
                    'revision':'b'*40,'verifiedAt':time.time()})
        elif stage == 'summary-missing':
            await manager.publish(phase='activating', pendingSmartTools=[],
                pendingRestart={'version':'0.20.204','requestStatus':'uncertain'})
        elif stage == 'summary-reset':
            service.state['releaseNoticeStartVersion'] = original_notice_start
            await manager.publish(phase='installed', pendingApp=None, pendingRestart=None,
                pendingSmartTools=[], applicationResult=None, application=original_application,
                sequence={'stage':'complete','install':False})
        elif stage in {'initial', 'regular'}:
            await manager.publish(initialPreparation=stage=='initial')
        elif stage=='waiting':
            service.state['voice']={'status':'connected'}
            await manager.publish(phase='staged',pendingRelease='b'*32,
                                  plan={},blockers=[{'kind':'voice','label':'A voice call is active'},{'kind':'worker','label':'A background worker is finishing'}],detail='Waiting for active work and calls to finish.')
        elif stage=='activating':
            service.state['voice']={'status':'disconnected'}
            await manager.publish(phase='staged',pendingRelease='b'*32,
                                  plan={'activation':'per-worker'},
                                  detail='Update prepared; making it available to new conversation work.')
        elif stage=='adopting':
            await manager.publish(phase='installed',pendingRelease=None,error=None,
                                  sequence={'stage':'complete','install':False},
                                  adoption={'pendingWorkers':2,'activeWorkers':2},
                                  items=[{**row,'status':'current'} for row in service.state['updates']['items']])
        elif stage=='installed':
            diagnostics.record('ecosystem-activation','succeeded')
            await manager.publish(phase='installed',pendingRelease=None,error=None,
                                  sequence={'stage':'complete','install':False},adoption={'pendingWorkers':0},
                                  items=[{**row,'status':'current'} for row in service.state['updates']['items']])
        elif stage=='failed':
            diagnostics.record('ecosystem-prepare','failed',errorType='BundleNotFoundError')
            await manager.publish(phase='error',error='Synthetic activation failure; active generation preserved.')
        elif stage=='source-mismatch':
            from amplifier_web.update_diagnostics import failure_guidance
            failure=diagnostics.record('ecosystem-validation','failed',errorType='ValueError',
                reason='runtime-installed-source-mismatch',package='amplifier-foundation',
                resolutionMismatches=['revision'])
            await manager.publish(phase='error',error=failure_guidance(failure))
        elif stage=='application-confirmed':
            # Browser presentation fixture only. The real readiness proof and
            # persisted receipt are exercised by test_update_readiness.py.
            application={**service.state['updates']['application'], 'status':'current', 'runningRevision':'c'*40}
            result={'attemptId':'d'*32,'phase':'restart-ack','version':application['current'],
                    'revision':application['runningRevision'],'verifiedAt':time.time()}
            await manager.publish(application=application,applicationResult=result,phase='installed',
                pendingApp=None,pendingRestart=None,pendingReplacement=None,pendingRelease=None,error=None,
                sequence={'stage':'included','install':False},lastAttempt=None)
        elif stage=='component-source-changed':
            diagnostics.begin('ecosystem','e'*32,tier='other',components=['amplifier-foundation'])
            diagnostics.record('ecosystem-validation','failed',errorType='ValueError',reason='runtime-source-changed')
            await manager.publish(phase='error',sequence={'stage':'other','install':False},
                error='Worker sources changed while checking. Check for updates again.')
        elif stage=='component-source-constraint':
            # Presentation fixture, not source-policy or installer execution.
            # Keep the prior verified app identity while its component batch fails.
            from amplifier_web.runtime_environment import ProtectedRuntimeSource
            from amplifier_web.update_diagnostics import exception_facts, failure_guidance
            diagnostics.begin('ecosystem','f'*32,tier='other',components=['amplifier-foundation'])
            await manager.publish(phase='validating',error=None,
                sequence={'stage':'other','install':False},
                application={**service.state['updates']['application'],
                    'detail':'The latest published application release is installed.'},
                detail='Preparing dependencies in a separate runtime…')
            error=ProtectedRuntimeSource('amplifier-foundation','runtime-source-constraint')
            failure=diagnostics.record('ecosystem-validation','failed',
                **{**error.diagnostic_facts,**exception_facts(error)})
            diagnostics.record('ecosystem-stage','failed',preserve_last_failure=True)
            await manager.publish(phase='error',pendingRelease=None,
                detail='Component update preparation failed. Current components remain active.',
                error=failure_guidance(failure))
        elif stage=='app-result-stale':
            await manager.publish(application={**service.state['updates']['application'],'runningRevision':'f'*40})
        elif stage=='unconfirmed-restart':
            await manager.publish(pendingRestart={'requestStatus':'uncertain'},phase='activating',error=None)
        elif stage=='clear-app-result':
            await manager.publish(applicationResult=None,pendingRestart=None,phase='installed',error=None,
                sequence={'stage':'complete','install':False})
        elif stage=='covered-retry':
            # Same diagnostic owner and occurrence fences as a real retry. Only
            # the installation effect is synthetic in this presentation fixture.
            diagnostics.begin('ecosystem', '8'*32, tier='other', components=[
                'amplifier-foundation', 'amplifier-module-hooks-approval',
                *[f'fixture-component-{index}' for index in range(6)]])
            await manager.publish(phase='validating', error=None, pendingApp=None,
                pendingRestart=None, pendingReplacement=None, pendingRelease=None,
                pendingSmartTools=[], sequence={'stage':'other','install':True})
        elif stage=='covered-activation':
            diagnostics.record('ecosystem-activation', 'succeeded')
            diagnostics.clear_failure()
            await manager.publish(phase='installed', error=None,
                detail='Qualified component batch activated.',
                sequence={'stage':'complete','install':False})
        elif stage=='compatibility':
            await manager.publish(probeProgress={'attemptId':attempt,'phase':'compatibility','completed':12,'total':37,'startedAt':time.time()})
        else:raise web.HTTPBadRequest()
        return web.json_response({'ok':True,'stage':stage})
    app.router.add_post('/fixture/update-progress',transition)
    service._publish()
    return app

async def serve():
    import amplifier_web
    with tempfile.TemporaryDirectory(prefix='unified-update-progress-') as tmp:
        app = await main(Path(tmp))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        print(json.dumps({'url': url, 'package': amplifier_web.__file__}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(serve())
