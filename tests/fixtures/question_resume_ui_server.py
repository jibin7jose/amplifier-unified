"""Rendered answer -> real host guard; scripted runtime, no external providers."""
import asyncio
import copy
import hashlib
import json
import signal
import sys
import tempfile
from pathlib import Path

from aiohttp import web
import settings_ui_server as fixture
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_assignments import child, generation, agent_action, until
from test_collaborative_workspaces import Runtime as PeerRuntime, settled


class Runtime(PeerRuntime, fixture.Runtime):
    def __init__(self):
        PeerRuntime.__init__(self)
        fixture.Runtime.__init__(self)
        self.answer_sends = []
        self.resumes = []
        self.question = None
        self.target = None
        self.problem = None

    async def send(self, session, text, input_id, emit):
        self.answer_sends.append(input_id)
        source = self.app._session(session['id'])
        await generation(self.app, source, 'answer-' + input_id, [input_id])
        record = self.app.questions.for_input(source['id'], input_id)
        assert text == f'Answer to “{record["prompt"]}”:\n{record["answer"]["text"]}\n\nFor: {record["dependency"]}'
        if '\nKeep it stopped\n' in text:
            self.app._message(source, 'assistant', 'Work remains stopped, as requested.')
        else:
            # The scripted manager uses the public surface, not direct calls to
            # the new verifier. The host still owns all admission/dedupe checks.
            found = (await agent_action(self.app, source, 'coordination.context',
                {'sessionId': source['id']}, 'inspect-answer-' + input_id))['result']['humanInput']
            if found is None:
                self.problem = 'The answered question was not offered as current human input.'
                self.app._message(source, 'assistant', self.problem)
            else:
                args = {'sessionId': self.target['id'], 'mode': 'queue',
                    'text': 'Continue the unfinished report; preserve earlier results and unknown effects.',
                    'resume': {'sourceMessageId': found['sourceMessageId'],
                               'expectedInterruptionRevision': self.target['interruptionRevision']}}
                identity = 'resume-' + input_id
                await agent_action(self.app, source, 'coordination.send', args, identity)
                result = await settled(self.app, identity, 'accepted')
                # Same command is retrieval, not another manager/worker effect.
                repeated = await agent_action(self.app, source, 'coordination.send', args, identity)
                assert repeated['duplicate']
                self.resumes.append(result)
                self.app._message(source, 'assistant', 'The unfinished report continued. No second Resume message was needed.')
        await emit('runtime.status', {'sessionId': source['id'], 'status': 'idle'})


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    runtime = Runtime()
    runtime.app = service
    service.runtime = app['runtime'] = runtime
    source = service._session()
    await generation(service, source, 'commission-generation', ['synthetic-commission'])
    target, _ = await child(service, source)
    runtime.target = target
    await service.dispatch('conversation.stop', {'sessionId': target['id']}, command_id='stop-member')
    await until(lambda: target['status'] == 'stopped')
    await service.on_runtime_event('runtime.ended', {'sessionId': target['id'], 'rootSessionId': target['id']})
    target['task'] = {'id': 'saved-report', 'revision': 4, 'status': 'paused',
                      'maxTurns': 5, 'goalState': {'turns_used': 2},
                      'operationIds': ['unknown-external-effect']}
    original_task = copy.deepcopy(target['task'])
    service.collaboration.insert('prior-unknown', 'fixture', {
        'requestId': 'prior-unknown', 'delivery': 'unknown', 'qualified': False,
        'detail': 'Saved unknown effect; never replay or relabel.'})
    unknown = copy.deepcopy(service.collaboration.receipt('prior-unknown'))
    runtime.inputs.clear()
    question = (await agent_action(service, source, 'question.create', {
        'sessionId': source['id'], 'prompt': 'Resume the unfinished report?',
        'required': True, 'dependency': 'Continue the existing report task',
        'options': [{'id': 'resume', 'label': 'Resume the unfinished report'},
                    {'id': 'stop', 'label': 'Keep it stopped'}],
    }, 'ask-resume'))['result']
    service._publish()

    async def check(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        return web.json_response({'answerSends': runtime.answer_sends,
            'peerInputs': [row[1] for row in runtime.inputs], 'resumes': runtime.resumes,
            'problem': runtime.problem, 'taskUnchanged': target['task'] == original_task,
            'unknownUnchanged': service.collaboration.receipt('prior-unknown') == unknown,
            'question': service.questions.store.get(source['id'], question['id']),
            'selectedSessionId': service.state['selectedSessionId'],
            'targetMessages': [{key: m.get(key) for key in ('inputOrigin', 'inputId', 'peerEnvelope')}
                               for m in target['messages'] if m.get('inputId', '').startswith('resume-')]})
    app.router.add_get('/fixture/resume-proof', check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    import amplifier_web.peer_resume as module
    print(json.dumps({'url': url, 'sessionId': source['id'], 'targetId': target['id'],
                      'questionId': question['id'], 'source': str(Path(module.__file__).resolve()),
                      'sourceSha256': hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()}), flush=True)
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='question-resume-ui-') as directory:
        asyncio.run(main(Path(directory)))
