"""Actual Core/Live observes commissioning before its first scripted request.

Reuses the existing native/event-pump adapter, without a model service or full
subprocess/restart claim. Ten-worker concurrency is covered by the owner test.
"""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip('amplifier_module_loop_live.orchestrator')
pytest.importorskip('amplifier_module_context_simple')
from amplifier_web import runtime_worker
from amplifier_web.management import Management
from fixtures.collaboration_native_probe import ScriptedProvider, tool_output
from test_completed_peer_native import NativeHost
from test_collaborative_workspaces import app, until
from test_peer_creation_operation import commission, plan, initial_accepted
from test_worktrees import repository


class CommissioningHost(NativeHost):
    async def before_execute(self, worker):
        await worker.controls.restore()
        await super().before_execute(worker)

    async def start(self, row, emit, *, guard=None, **kwargs):
        if guard:
            guard()
        if row['id'] not in self.roots:
            await self.mount(row, self.provider, self.paths)


class Member(ScriptedProvider):
    async def complete(self, request, **kwargs):
        self.calls += 1
        receipt = self.host.app.collaboration.receipt('native-commission')
        worker = self.host.roots[receipt['sessionId']]
        if self.calls == 1:
            coordinator = worker.session.coordinator
            task = worker.controls.tasks.record()
            saved = json.loads((self.host.app.data_dir / 'sessions' / receipt['sessionId'] / 'control-state.json').read_text())
            checkout = self.host.app.worktrees.record(receipt['sessionId'], receipt['checkoutId'])
            assert receipt['phase'] == 'input_committed'
            assert str(coordinator.get_capability('session.working_dir')) == checkout['path']
            assert task['id'] == receipt['objectiveTaskId'] == saved['task']['id']
            assert task['objective'] == 'Verify the isolated fixture'
            assert task['constraints'] == ['Keep the source unchanged']
            assert coordinator.session_state['goal']['task_id'] == task['id']
            assert Path(checkout['path'], 'a.txt').read_text() == 'original\n'
            return self.dispatch('complete-native-fixture', 'task.complete', {
                'sessionId': receipt['sessionId'], 'expectedRevision': task['revision'],
                'condition': task['objective'], 'evidence': ['Isolated committed fixture content verified.']})
        assert self.calls == 2
        assert tool_output(request, 'complete-native-fixture')['accepted']
        return self.final('The isolated fixture was verified; the source is unchanged.')


@pytest.mark.parametrize('binding', ['sourceRevision', 'expectedCommit'])
async def test_native_first_request_sees_isolated_placement_and_saved_objective(app, tmp_path, monkeypatch, binding):
    host = app.runtime = CommissioningHost()
    host.app = app
    host.provider = Member()
    host.provider.host = host
    host.paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
                  for name in ('loop-live', 'context-simple')}
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    app.management = Management(app)
    host.pump = asyncio.create_task(host.route_events())
    source = app.state['sessions'][0]
    plan(app, source)
    root = repository(tmp_path)
    if binding == 'expectedCommit':
        from amplifier_worktrees.git import MAX_BYTES
        with (root / 'retained-evidence.bin').open('wb') as stream:
            stream.truncate(MAX_BYTES + 1)
    inspected = app.worktrees.git.inspect(root, mode='clean' if binding == 'expectedCommit' else None)
    async with asyncio.timeout(40):
        receipt = await commission(app, source, 'native-commission',
            objective='Verify the isolated fixture', constraints=['Keep the source unchanged'],
            execution={'kind': 'worktree', 'repositoryPath': str(root),
                       binding: inspected[binding]},
            returnNotification=False)
        await initial_accepted(app, receipt['initialInputId'])
        await until(lambda: receipt['sessionId'] in host.roots)
        worker = host.roots[receipt['sessionId']]
        await host.outcome(worker, receipt['initialInputId'])
        assert worker.controls.tasks.record()['status'] == 'completed'
        assert host.provider.calls == 2
        assert (root / 'a.txt').read_text() == 'original\n'
        assert not host.approvals and not host.errors
