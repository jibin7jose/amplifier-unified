"""Offline real loop catch and worker publication; no vendor or child process.

The synthetic provider fails in parse_tool_calls, AFTER complete returned.
The pending child is an asyncio task owned by the real loop's shutdown path,
not a native delegated session. Process transport alone is replaced by a queue.
"""
import asyncio
import copy
import errno
import importlib
import json
from pathlib import Path

import pytest

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ProviderInfo
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_web import runtime_worker
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.service import AppService


PRIVATE = "PRIVATE_MANAGER_MESSAGE_AND_LOCAL"


def retain_failure(worker, terminal):
    """Use the real observer before the loop retires its failed generation."""
    worker.runtime.session_id = 'root'
    worker.observe({'type': 'generation.failed', **terminal})
    return worker.failed_generation


@pytest.mark.parametrize('cleanup_conflict', [False, True])
async def test_real_post_provider_manager_exception_reaches_public_inspection(tmp_path, monkeypatch, cleanup_conflict):
    # The original-code subprocess disables repository conftest discovery.
    # Apply the same file isolation explicitly to both red and green cases.
    for key, folder in (("AMPLIFIER_HOME", "amplifier"), ("AMPLIFIER_WEB_HOME", "unified"),
                        ("AMPLIFIER_SESSION_STATE_HOME", "shared"), ("AMPLIFIER_TERMINAL_HOME", "terminal")):
        monkeypatch.setenv(key, str(tmp_path / folder))
    monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", raising=False)
    # Required imports: an unavailable real boundary is a failure, never a skip.
    from amplifier_module_loop_live.runtime import Input, Runtime
    from amplifier_web.host.session import _NativeTranscriptGuard

    native = tmp_path / 'private-transcript.jsonl'
    native.write_text('original\n')
    guard = _NativeTranscriptGuard(native)
    expected_type = 'OSError' if cleanup_conflict else 'ValueError'

    paths = {name: Path(importlib.import_module("amplifier_module_" + name.replace("-", "_")).__file__).parent
             for name in ("loop-live", "context-simple")}
    plan = {"session": {"orchestrator": {"module": "loop-live", "config": {"use_streaming": False}},
                        "context": {"module": "context-simple"}}}
    app = AppService(tmp_path / "app", workspace=tmp_path)
    session = task = None
    worker = runtime_worker.Worker()
    published, raw = [], []
    monkeypatch.setattr(runtime_worker, "publish", lambda row: published.append(copy.deepcopy(row)))
    try:
        await app.dispatch("session.create", {})
        root = app._session()
        prepared = PreparedBundle(plan, BundleModuleResolver(paths), Bundle(name="exception-boundary", session=plan["session"]))
        session = await prepared.create_session(session_id=root["id"])
        worker.session = session
        if hasattr(worker, 'capture_manager_failures'):
            worker.capture_manager_failures()
        def observe(event):
            raw.append(copy.deepcopy(event))
            worker.observe(event)  # Production terminal publication, not a fake.
            if cleanup_conflict and event['type'] == 'session.closed':
                # A real guard detects the changed file during loop teardown.
                # It must not hide the original exception from this generation.
                guard.check()
        worker.runtime = Runtime(root["id"], observer=observe)
        coordinator = session.coordinator
        coordinator.register_capability("live.runtime", worker.runtime)
        loop = coordinator.get("orchestrator")
        child_started, child_cancelled = asyncio.Event(), asyncio.Event()

        async def controlled_child():
            child_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                child_cancelled.set()

        def manager_admission_sentinel():
            private_local = PRIVATE
            if cleanup_conflict:
                native.write_text('partial-save\n')
                raise OSError(errno.ENOSPC, private_local, str(native))
            raise ValueError(private_local)

        class Provider:
            name = "offline-boundary"
            calls = 0
            def get_info(self):
                return ProviderInfo(id=self.name, display_name="Offline fixture",
                                    capabilities=["tools"], defaults={"model": "fixture"})
            async def list_models(self):
                return []
            async def complete(self, request, **kwargs):
                self.calls += 1
                assert self.calls == 1, "Synthetic effect must not repeat"
                child = asyncio.create_task(controlled_child())
                loop.jobs["controlled-child"] = {"task": child, "call_id": "controlled-call", "status": "pending"}
                await child_started.wait()
                return ChatResponse(content=[], tool_calls=[ToolCall(id="fixture-call", name="unused", arguments={})],
                                    usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
            def parse_tool_calls(self, response):
                # Inject at the production post-complete parser boundary.
                # Do not replace execute(), turn_failure(), or the manager catch.
                assert not loop.jobs["controlled-child"]["task"].done()
                manager_admission_sentinel()

        provider = Provider()
        worker.telemetry = ExecutionEvents(root["id"], runtime_worker.publish)
        await coordinator.mount("providers", worker.telemetry.instrument_provider(root["id"], provider), name=provider.name)
        task = worker.execution = asyncio.create_task(session.execute(""))
        await worker.runtime.wait_for(lambda row: row["type"] == "session.idle", timeout=10)
        await worker.runtime.submit(Input("user", "Synthetic boundary input", id="boundary-input"))
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(asyncio.shield(task), 10)
        await asyncio.wait_for(child_cancelled.wait(), 2)
        assert child_started.is_set()
        worker.executed(task)  # Production callback still has the real cause.
        terminal = next(row for row in raw if row["type"] == "generation.failed")
        exit_event = next(row for row in published if row["type"] == "runtime.error")
        assert terminal["error_type"] == expected_type
        assert terminal["error_category"] == "unknown" and terminal["error_stage"] == "manager_turn"
        assert terminal["input_ids"] == ["boundary-input"]
        completed_calls = [event["event"] for event in published
                           if event["type"] == "execution.event" and event["event"].get("kind") == "llm"
                           and event["event"].get("phase") == "completed"]
        assert len(completed_calls) == 1 and completed_calls[0]["sessionId"] == root["id"]
        assert not any(event["type"] == "execution.event" and event["event"].get("failure") for event in published)
        # Record first loss without reconstructing anything from exception text.
        print("BOUNDARY", json.dumps({"rawLoopHasChain": bool(terminal.get("exception_chain")),
            "workerHasChain": bool(exit_event.get("terminalFailure", {}).get("exceptionChain")),
            "workerFile": runtime_worker.__file__, "captureSupported": hasattr(worker, "capture_manager_failures"),
            "generation": terminal["generation_id"], "inputs": terminal["input_ids"]}))
        # Keep the production JSON-lines reader and its runtime.error branch.
        # Only OS pipes/process effects are replaced; all bytes originate above.
        stdout = asyncio.StreamReader()
        stdout.feed_data(b"".join((json.dumps(row) + "\n").encode() for row in published))
        stdout.feed_eof()
        class Process:
            returncode = 0
            async def wait(self):
                return 0
        process = Process()
        process.stdout = stdout
        ready = asyncio.get_running_loop().create_future()
        ready.set_result({})
        delivered = []
        branches = []
        # Observe the existing production rejection without changing its result.
        from amplifier_web import session_health
        original_supplement = getattr(session_health, "terminal_exception_failure", None)
        if original_supplement:
            def observed_supplement(current, capture):
                result = original_supplement(current, capture)
                branches.append({"branch": "supplement_accept" if result is not None else "supplement_reject",
                                 "status": current.get("status"),
                                 "generation": capture.get("generationId") if isinstance(capture, dict) else None})
                return result
            monkeypatch.setattr(session_health, "terminal_exception_failure", observed_supplement)
        sequence = []
        async def emit(kind, payload):
            delivered.append((kind, copy.deepcopy(payload)))
            before = app._session(root["id"])
            observation = {"index": len(sequence), "kind": kind,
                "event": payload.get("event"), "statusBefore": before.get("status"),
                "interruptionRevision": before.get("interruptionRevision"),
                "generation": payload.get("generation_id") or (payload.get("terminalFailure") or {}).get("generationId"),
                "inputIds": payload.get("input_ids") or (payload.get("terminalFailure") or {}).get("inputIds"),
                "supplement": "terminalFailure" in payload, "chainBefore": bool((before.get("failure") or {}).get("exceptionChain"))}
            await app.on_runtime_event(kind, payload)
            after = app._session(root["id"])
            observation.update(statusAfter=after.get("status"),
                               chainAfter=bool((after.get("failure") or {}).get("exceptionChain")))
            sequence.append(observation)
        row = {"process": process, "ready": ready, "pending": {}, "closing": False,
               "runtimeOwnerId": "synthetic-boundary-process",
               "runtime_id": root["id"], "inputId": "boundary-input", "emit": emit}
        manager = RuntimeManager()
        manager.workers[root["id"]] = row
        await manager._read(root["id"], row)
        print("BOUNDARY_SEQUENCE", json.dumps({"events": sequence, "branches": branches}))
        receipt = await app.dispatch("session.inspect", {"id": root["id"]})
        failure = receipt["result"]["failure"]
        assert failure["errorType"] == expected_type and failure["category"] == "unknown"
        assert failure["stage"] == "manager_turn" and failure["retryable"] is False
        assert failure["generationId"] == terminal["generation_id"] and failure["inputIds"] == ["boundary-input"]
        retained_terminal = next(event for event in app._session(root["id"])["generations"] if event["event"] == "generation.failed")
        assert failure["recordedAt"] == retained_terminal["at"]
        chain = failure.get("exceptionChain", [])
        assert any(item["errorType"] == expected_type and
                   any(frame["function"] == "manager_admission_sentinel" for frame in item["frames"]) for item in chain)
        if cleanup_conflict:
            assert next(item for item in chain if item['errorType'] == 'OSError')['errno'] == errno.ENOSPC
            assert failure['secondaryFailure']['stage'] == 'loop_exit'
            assert failure['secondaryFailure']['exceptionChain'][0]['errorType'] == 'NativeTranscriptConflict'
            # The reporting correction does not accept the changed transcript.
            assert native.read_text() == 'partial-save\n'
            with pytest.raises(RuntimeError):
                guard.check()
        assert failure["effects"] == "not_rolled_back" and failure["replayed"] is False
        supplements = [event for event in published if "terminalFailure" in event]
        assert PRIVATE not in json.dumps([supplements, delivered, receipt["result"]])
        assert str(native) not in json.dumps([supplements, receipt['result']['failure']])
        assert provider.calls == 1 and list(worker.runtime.accepted) == ["boundary-input"]
        assert len(app.state["sessions"]) == 1 and loop.jobs["controlled-child"]["task"].done()
        assert loop.jobs["controlled-child"]["task"].cancelled()
        await app.close()
        app = AppService(tmp_path / 'app', workspace=tmp_path)
        reopened = await app.dispatch('session.inspect', {'id': root['id']})
        assert reopened['result']['failure'] == failure
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if session:
            await session.cleanup()
        await app.close()


def test_capture_rejects_payloads_and_bounds_frames():
    from amplifier_web.support_diagnostics import public_exception_chain, terminal_exception_capture
    frame = {"file": "fixture.py", "function": "sentinel", "line": 42,
             "message": PRIVATE, "locals": {"secret": PRIVATE}}
    value = [{"errorType": "ValueError", "frames": [frame] * 20, "body": PRIVATE}] * 8
    safe = public_exception_chain(value)
    assert len(safe) == 5 and all(len(item["frames"]) == 12 for item in safe)
    assert PRIVATE not in json.dumps(safe)
    for invalid in (True, -1, 0, 10_000_001, "42", None):
        assert public_exception_chain([{"errorType": "ValueError", "frames": [{**frame, "line": invalid}]}])[0]["frames"] == []
    assert public_exception_chain([{"errorType": PRIVATE + "\n", "frames": [frame]}]) == []
    assert public_exception_chain([{"errorType": "ValueError", "frames": [{**frame, "file": "/private/fixture.py"}]}])[0]["frames"] == []
    for bad in (None, [], {}, {"exceptionChain": value, "generationId": "g", "inputIds": [True], "errorType": "ValueError"}):
        assert terminal_exception_capture(bad) is None


def test_os_error_numbers_are_bounded_typed_facts_not_message_parsing():
    from amplifier_web.support_diagnostics import exception_chain, public_exception_chain, terminal_exception_capture
    error = OSError(errno.EIO, PRIVATE, '/private/secret-history')
    error.winerror = 112  # Typed Windows error also survives export on other test platforms.
    safe = public_exception_chain(exception_chain(error))
    assert safe == [{'errorType': 'OSError', 'frames': [], 'errno': errno.EIO, 'winerror': 112}]
    assert PRIVATE not in json.dumps(safe) and '/private/' not in json.dumps(safe)
    custom = ValueError('errno=28 winerror=112')
    custom.errno = 28
    assert 'errno' not in exception_chain(custom)[0]
    for invalid in (True, -1, 2**32, '28', None):
        assert public_exception_chain([{'errorType': 'OSError', 'frames': [], 'errno': invalid,
                                        'winerror': invalid}]) == [{'errorType': 'OSError', 'frames': []}]
    capture = {'generationId': 'g', 'inputIds': ['i'], 'errorType': 'OSError', 'exceptionChain': safe,
               'secondaryFailure': {'stage': 'loop_exit', 'exceptionChain': safe, 'message': PRIVATE}}
    assert terminal_exception_capture(capture)['secondaryFailure'] == {'stage': 'loop_exit', 'exceptionChain': safe}
    capture['secondaryFailure']['stage'] = PRIVATE
    assert 'secondaryFailure' not in terminal_exception_capture(capture)


@pytest.mark.parametrize('boundary', ['current', 'stop', 'stop-requested', 'new-generation', 'other-input',
                                    'runtime-replaced', 'old-turn', 'remount', 'yield', 'cancel', 'cancel-context', 'foreign-wrapper'])
async def test_cleanup_context_capture_cannot_cross_current_execution(monkeypatch, boundary):
    from types import SimpleNamespace
    from amplifier_module_loop_live.failures import ManagerTurnError
    from amplifier_web.host.session import NativeTranscriptConflict
    worker = runtime_worker.Worker()
    worker.runtime = SimpleNamespace(stop_requested=False, generation={'id': 'g', 'input_ids': ['i']})
    terminal = {'generation_id': 'g', 'input_ids': ['i'], 'error_type': 'OSError',
                'error_category': 'unknown', 'error_stage': 'manager_turn'}
    published = []
    monkeypatch.setattr(runtime_worker, 'publish', published.append)
    if boundary == 'old-turn':
        retain_failure(worker, terminal)
    published.clear()
    class Foreign(ManagerTurnError):
        pass
    class Loop:
        async def execute(self):
            if boundary != 'old-turn':
                retain_failure(worker, terminal)
                published.clear()
            try:
                try:
                    raise OSError(errno.EIO, PRIVATE)
                except OSError as cause:
                    wrapper = Foreign if boundary == 'foreign-wrapper' else ManagerTurnError
                    raise wrapper({**terminal, 'error_message': 'Safe'}) from cause
            finally:
                if boundary == 'stop':
                    worker.stop_revision += 1
                elif boundary == 'stop-requested':
                    worker.runtime.stop_requested = True
                elif boundary == 'new-generation':
                    worker.runtime.generation = {'id': 'new', 'input_ids': ['i']}
                elif boundary == 'other-input':
                    worker.runtime.generation = {'id': 'g', 'input_ids': ['other']}
                elif boundary == 'runtime-replaced':
                    worker.runtime = SimpleNamespace(stop_requested=False, generation={'id': 'g', 'input_ids': ['i']})
                elif boundary == 'remount':
                    worker.remounting = True
                elif boundary == 'yield':
                    worker.ownership.yielding = True
                elif boundary == 'cancel':
                    raise asyncio.CancelledError()
                elif boundary == 'cancel-context':
                    try:
                        raise asyncio.CancelledError()
                    except asyncio.CancelledError:
                        raise NativeTranscriptConflict(PRIVATE)
                raise NativeTranscriptConflict(PRIVATE)
    loop = Loop()
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: loop))
    worker.capture_manager_failures()
    expected = asyncio.CancelledError if boundary == 'cancel' else NativeTranscriptConflict
    with pytest.raises(expected):
        await loop.execute()
    assert worker.failed_generation['generation_id'] == terminal['generation_id']
    if boundary == 'current':
        assert len(published) == 1
        capture = published[0]['terminalFailure']
        assert capture['generationId'] == 'g' and capture['inputIds'] == ['i']
        assert capture['exceptionChain'][1]['errno'] == errno.EIO
        assert capture['secondaryFailure']['exceptionChain'][0]['errorType'] == 'NativeTranscriptConflict'
        assert PRIVATE not in json.dumps(published)
    else:
        assert published == []


@pytest.mark.parametrize("change", ["stop", "new-generation", "wrong-generation", "wrong-input",
                                   "wrong-type", "child", "old-failure", "already-captured"])
def test_supplement_cannot_replace_or_cross_failure_identity(change):
    from amplifier_web.session_health import generation_failure, terminal_exception_failure
    event = {"sessionId": "root", "rootSessionId": "root", "event": "generation.failed", "generation_id": "g",
             "input_ids": ["i"], "error_type": "ValueError", "error_stage": "manager_turn", "error_category": "unknown"}
    failure = {**generation_failure(event), "generationId": "g", "inputIds": ["i"], "recordedAt": 100}
    session = {"id": "root", "status": "error", "failure": failure, "generations": [event]}
    capture = {"generationId": "g", "inputIds": ["i"], "errorType": "ValueError",
               "exceptionChain": [{"errorType": "ValueError", "frames": []}]}
    enriched = terminal_exception_failure(session, capture)
    assert enriched == {**failure, "exceptionChain": capture["exceptionChain"]}
    assert session["failure"] is failure and "exceptionChain" not in failure
    if change == "stop":
        session["status"] = "stopped"
    elif change == "new-generation":
        session["generations"].append({**event, "event": "generation.started", "generation_id": "new"})
    elif change == "wrong-generation":
        capture["generationId"] = "other"
    elif change == "wrong-input":
        capture["inputIds"] = ["other"]
    elif change == "wrong-type":
        event["error_type"] = "TypeError"
    elif change == "child":
        event["sessionId"] = "child"
    elif change == "old-failure":
        failure["generationId"] = "old"
    else:
        failure["exceptionChain"] = [{"errorType": "ValueError", "frames": [{"file": "original.py"}]}]
    before = copy.deepcopy(session)
    assert terminal_exception_failure(session, capture) is None
    assert session == before


async def test_unsupported_or_stopped_capture_does_not_change_execution(monkeypatch):
    from types import SimpleNamespace
    from amplifier_web.runtime_worker import Worker
    worker = Worker()
    published = []
    monkeypatch.setattr(runtime_worker, "publish", published.append)
    class Immutable:
        __slots__ = ()
        async def execute(self):
            return "original"
    immutable = Immutable()
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: immutable))
    worker.capture_manager_failures()
    assert await immutable.execute() == "original" and not published
    from amplifier_module_loop_live.failures import ManagerTurnError
    class Loop:
        async def execute(self):
            worker.stop_revision += 1
            try:
                raise ValueError(PRIVATE)
            except ValueError as error:
                raise ManagerTurnError({"error_type": "ValueError", "error_category": "unknown",
                                        "error_stage": "manager_turn", "error_message": "Safe"}) from error
    loop = Loop()
    worker.runtime = SimpleNamespace(stop_requested=False, generation={'id': 'g', 'input_ids': ['i']})
    retain_failure(worker, {"generation_id": "g", "input_ids": ["i"], "error_type": "ValueError",
                           "error_category": "unknown", "error_stage": "manager_turn"})
    published.clear()
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: loop))
    worker.capture_manager_failures()
    original = loop.execute
    worker.capture_manager_failures()
    assert loop.execute is original
    with pytest.raises(ManagerTurnError):
        await loop.execute()
    assert not published


async def test_capture_transport_failure_cannot_replace_original_error(monkeypatch):
    from types import SimpleNamespace
    from amplifier_module_loop_live.failures import ManagerTurnError
    original = ValueError(PRIVATE)
    wrapper = ManagerTurnError({"error_type": "ValueError", "error_category": "unknown",
                               "error_stage": "manager_turn", "error_message": "Safe"})
    class Loop:
        async def execute(self):
            retain_failure(worker, {"generation_id": "g", "input_ids": ["i"], "error_type": "ValueError",
                                   "error_category": "unknown", "error_stage": "manager_turn"})
            raise wrapper from original
    worker = runtime_worker.Worker()
    worker.runtime = SimpleNamespace(stop_requested=False, generation={'id': 'g', 'input_ids': ['i']})
    monkeypatch.setattr(runtime_worker, 'publish', lambda row: None)
    retain_failure(worker, {"generation_id": "g", "input_ids": ["i"], "error_type": "ValueError",
                           "error_category": "unknown", "error_stage": "manager_turn"})
    loop = Loop()
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: loop))
    def broken_transport(row):
        if "terminalFailure" in row:
            raise BrokenPipeError("closed")
    monkeypatch.setattr(runtime_worker, "publish", broken_transport)
    worker.capture_manager_failures()
    with pytest.raises(ManagerTurnError) as caught:
        await loop.execute()
    assert caught.value is wrapper and caught.value.__cause__ is original


@pytest.mark.parametrize('boundary', [None, 'before-failure', 'before-close', 'before-supplement',
                                     'new-generation', 'wrong-generation', 'wrong-input', 'wrong-type',
                                     'child', 'foreign-root'])
async def test_terminal_closure_preserves_only_current_uninterrupted_cause(tmp_path, boundary):
    from test_service import Runtime
    runtime = Runtime()
    app = AppService(tmp_path / 'app', workspace=tmp_path, runtime=runtime)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        sid = session['id']
        base = {'sessionId': sid, 'rootSessionId': sid, 'generation_id': 'current'}
        await app.on_runtime_event('runtime.generation', {**base, 'event': 'generation.started'})
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'event': 'input.delivered',
                                                    'status': 'working', 'inputId': 'input'})
        failed = {**base, 'event': 'generation.failed', 'input_ids': ['input'],
                  'error_type': 'ValueError', 'error_category': 'unknown', 'error_stage': 'manager_turn'}
        capture = {'generationId': 'current', 'inputIds': ['input'], 'errorType': 'ValueError',
                   'exceptionChain': [{'errorType': 'ValueError', 'frames': [
                       {'file': 'fixture.py', 'function': 'safe_sentinel', 'line': 1}]}]}
        if boundary == 'new-generation':
            await app.on_runtime_event('runtime.generation', {**base, 'event': 'generation.started',
                                                             'generation_id': 'new'})
        elif boundary == 'wrong-generation':
            failed['generation_id'] = 'other'
        elif boundary == 'wrong-input':
            failed['input_ids'] = ['other']
        elif boundary == 'wrong-type':
            capture['errorType'] = 'TypeError'
        elif boundary == 'child':
            failed['sessionId'] = 'child'
        elif boundary == 'foreign-root':
            failed['rootSessionId'] = 'other-root'
        async def stop():
            await app.dispatch('conversation.stop', {'sessionId': sid})
            assert session['interruptionRevision'] == 1
            assert session['status'] in {'stopped', 'stopping'}
        if boundary == 'before-failure':
            await stop()
        await app.on_runtime_event('runtime.generation', failed)
        before_close = copy.deepcopy(session.get('failure'))
        if boundary is None:
            assert session['status'] == 'error'
            assert session['errorAt'] == before_close['recordedAt']
        if boundary == 'before-close':
            await stop()
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'event': 'session.closed',
                                                     'status': 'stopped'})
        if boundary is None:
            assert session['status'] == 'error' and session['failure'] == before_close
        if boundary == 'before-supplement':
            await stop()
        await app.on_runtime_event('runtime.error', {'sessionId': sid, 'terminalFailure': capture})
        receipt = await app.dispatch('session.inspect', {'id': sid})
        result = receipt['result'].get('failure') or {}
        if boundary is None:
            assert result == {**before_close, 'exceptionChain': capture['exceptionChain']}
            assert session['errorAt'] == before_close['recordedAt']
            assert result['retryable'] is False and result['replayed'] is False
        else:
            assert not result.get('exceptionChain')
            if boundary.startswith('before-'):
                assert session['status'] in {'stopped', 'stopping'}
                assert session['interruptionRevision'] == 1 and runtime.stopped == [sid]
        assert runtime.sent == []
    finally:
        await app.close()


@pytest.mark.parametrize('ownership', ['current', 'missing', 'replaced', 'closing'])
async def test_supplement_reader_requires_current_nonclosing_owner(ownership):
    capture = {'generationId': 'g', 'inputIds': ['i'], 'errorType': 'ValueError',
               'exceptionChain': [{'errorType': 'ValueError', 'frames': []}]}
    stdout = asyncio.StreamReader()
    stdout.feed_data((json.dumps({'type': 'runtime.error', 'terminalFailure': capture}) + '\n' +
                      json.dumps({'type': 'runtime.error', 'error': 'Original execution ended'}) + '\n').encode())
    stdout.feed_eof()
    class Process:
        returncode = 0
        async def wait(self):
            return 0
    process = Process()
    process.stdout = stdout
    ready = asyncio.get_running_loop().create_future()
    ready.set_result({})
    delivered = []
    async def emit(kind, payload):
        delivered.append((kind, payload))
    row = {'process': process, 'ready': ready, 'pending': {}, 'closing': ownership == 'closing',
           'runtimeOwnerId': 'original-owner', 'runtime_id': 'root', 'inputId': 'i', 'emit': emit}
    manager = RuntimeManager()
    if ownership != 'missing':
        manager.workers['root'] = row if ownership != 'replaced' else {**row, 'runtimeOwnerId': 'new-owner'}
    await manager._read('root', row)
    supplements = [payload for _, payload in delivered if 'terminalFailure' in payload]
    assert len(supplements) == (1 if ownership == 'current' else 0)
    if supplements:
        assert supplements[0]['terminalFailure'] == capture
