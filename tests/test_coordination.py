import asyncio
import copy
import json

import pytest

from amplifier_operations.coordination import ChangeSignal, delivery
from amplifier_web.runtime import normalize_event
from amplifier_web.service import AppError, AppService


class Runtime:
    def __init__(self):
        self.sent, self.messages, self.stops = [], [], []
        self.failure = False

    async def send(self, session, text, input_id, emit):
        self.sent.append((session["id"], text, input_id))
        return {"accepted": True}

    async def collaboration_input(self, session, args, guard, emit):
        assert guard() is None
        admitted = self.app.collaboration.admission(session["id"], args)
        assert admitted["admitted"]
        self.sent.append((session["id"], admitted["message"]["text"], args["inputId"]))
        await emit("runtime.status", {"sessionId": session["id"], "status": "working"})
        return {"accepted": True}

    async def message_worker(self, sid, wid, text, input_id=None):
        self.messages.append((sid, wid, text, input_id))
        if self.failure:
            raise RuntimeError("Connection lost after submission")
        return {"accepted": True, "inputId": input_id, "completed": False}

    async def stop_worker(self, sid, wid):
        self.stops.append((sid, wid))

    async def stop(self, sid, *, reason="stopped"):
        self.stops.append((sid,))

    async def close(self):
        pass


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    service.runtime.app = service
    # No runtime is started by constructing saved authoritative records.
    service.state["sessions"] = [service._new_session({"title": title}) for title in ("Selected", "Other")]
    service.state["selectedSessionId"] = service.state["sessions"][0]["id"]
    service.state["view"]["draft"] = "Unsent original"
    service._publish()
    yield service
    if not service.closed:
        await service.close()


async def worker(app, sid, wid="child-a", **patch):
    await app.on_runtime_event("worker.updated", {"sessionId": sid, "id": wid, "kind": "session", "parentSessionId": "native-parent", "runId": "run-1", "persistent": True, "status": "running", **patch})


async def test_list_exposes_revision_without_unbounded_wait_signal(app):
    caller, peer = app.state['sessions']
    peer.update(interruptionRevision=7,
                approvals=[{'id': f'approval-{i}', 'status': 'pending'} for i in range(100)])
    for i in range(100):
        app.questions.store.put({
            'id': f'question-{i}', 'sessionId': peer['id'], 'createdAt': i,
            'status': 'pending', 'prompt': f'Question {i}', 'revision': 1,
            'required': True,
        })
    app._publish()
    page = app.coordination.list({'sessionId': peer['id']}, caller['id'])
    item = page['items'][0]
    assert item['signal'] == {'interruptionRevision': 7}
    assert item['approvalIds'] == [f'approval-{i}' for i in range(64)]
    assert item['questionIds'] == [f'question-{i}' for i in range(64)]
    assert not app.runtime.sent and not app.runtime.messages


async def test_list_pages_dormant_peers_without_admission_or_body_reads(app, monkeypatch):
    caller, peer = app.state['sessions']
    peer_id = peer['id']
    peer.update(status='stopped', interruptionRevision=7)
    peer['workers'] = [{'id': 'saved-child', 'status': 'completed', 'name': 'Retained worker', 'kind': 'session'}]
    app.state.setdefault('runtimeControl', {})[peer_id] = {'task.get': {'task': {
        'id': 'saved-task', 'status': 'blocked', 'revision': 3, 'questionIds': []}}}
    foreign = app._new_session({'title': 'Other workspace'})
    foreign['workspace'] = str(app.data_dir / 'elsewhere')
    internal = app._new_session({'title': 'Private job', 'purpose': 'test'})
    app.state['sessions'].extend([foreign, internal])
    app._save()
    home, workspace = app.data_dir, app.default_workspace
    await app.close()
    restored = AppService(home, Runtime(), workspace=workspace)
    try:
        assert {row['id'] for row in restored.state['sessions']} == {caller['id']}
        selected, draft = restored.state['selectedSessionId'], restored.state['view']['draft']
        # Both receipt formats must remain bounded and passive, including when
        # the calling reader itself is dormant. No generic bridge tail allowed.
        def forbidden(*args, **kwargs):
            pytest.fail('Passive list receipt reached admission/history/snapshot/flush')
        with monkeypatch.context() as patch:
            for name in ('_session', '_admit_session', '_flush_pending_progress', 'state_context'):
                patch.setattr(restored, name, forbidden)
            patch.setattr(restored.history, 'refresh', forbidden)
            patch.setattr(restored.history, 'ensure_loaded', forbidden)
            patch.setattr('amplifier_web.agent_canvas.state', forbidden)
            patch.setattr('amplifier_web.history_query._rows', forbidden)
            for reader in (caller['id'], peer_id):
                for mode in (None, 'receipt'):
                    result = await restored.app_bridge('dispatch', {
                        'action': 'coordination.list', 'args': {'sessionId': peer_id},
                        'id': 'passive-list', **({'responseMode': mode} if mode else {})}, reader)
                    assert result['accepted']
                    assert result['result']['items'][0]['target']['sessionId'] == peer_id
                    assert set(result['state']) == {'revision', 'sessionId', '_stateAccess'}
                    assert result['state']['sessionId'] == reader
                    assert result['state']['revision'] == restored.state['revision']
                    access = result['state']['_stateAccess']
                    if mode:
                        assert set(access) == {'note', 'readback'}
                        assert access['readback'] == [] and result['commandId'] == 'passive-list'
                    else:
                        assert set(access) == {'note'}
                        assert 'full action result' in access['note']
                    assert draft not in json.dumps(result)
        assert {row['id'] for row in restored.state['sessions']} == {caller['id']}
        admitted = restored._session
        def caller_only(identity=None, *args, **kwargs):
            assert identity in {None, caller['id']}, 'Discovery admitted a dormant peer'
            return admitted(identity, *args, **kwargs)
        monkeypatch.setattr(restored, '_session', caller_only)
        exact = await restored.app_bridge('dispatch', {'action': 'coordination.list',
            'args': {'sessionId': peer_id}}, caller['id'])
        assert exact['result']['items'][0]['target']['sessionId'] == peer_id
        import amplifier_web.session_projection as projection
        original_hydrate = projection.hydrate
        monkeypatch.setattr(projection, 'hydrate', lambda *args, **kwargs: pytest.fail('Root discovery loaded saved views'))
        rows = []
        offset = 0
        while True:
            page = restored.coordination.list({'limit': 1, 'offset': offset}, caller['id'])
            rows.extend(page['items'])
            if page['nextOffset'] is None:
                break
            offset = page['nextOffset']
        assert {row['target']['sessionId'] for row in rows} == {caller['id'], peer_id}
        saved = next(row for row in rows if row['target']['sessionId'] == peer_id)
        assert saved['signal']['interruptionRevision'] == 7
        assert saved['taskId'] == 'saved-task' and saved['attention']
        assert saved['canFollowup'] and not saved['canInterrupt']
        assert saved['peerActions'] == [{'modes': ['auto', 'notify', 'queue']}]
        monkeypatch.setattr(projection, 'hydrate', original_hydrate)
        explicit = restored.coordination.list({'sessionId': peer_id, 'includeWorkers': True}, caller['id'])
        assert [row['kind'] for row in explicit['items']] == ['conversation', 'worker']
        assert explicit['items'][1]['target']['workerId'] == 'saved-child'
        assert {row['id'] for row in restored.state['sessions']} == {caller['id']}
        assert (restored.state['selectedSessionId'], restored.state['view']['draft']) == (selected, draft)
        assert not restored.runtime.sent and not restored.runtime.messages
    finally:
        await restored.close()


async def test_list_none_workspace_scope_filters_before_one_row_pages(app, monkeypatch):
    caller, peer = app.state['sessions']
    foreign = app._new_session({'title': 'Other workspace first'})
    foreign_later = app._new_session({'title': 'Other workspace between'})
    ordered = [foreign, caller, foreign_later, peer]
    app.state['sessions'] = ordered
    app._save()
    # Interleave foreign rows so filtering a global page after LIMIT would
    # return an empty/wrong page or skip a permitted peer.
    app.db.executemany('UPDATE history_local_rows SET ordinal=? WHERE id=?',
                      [(f'{index:04d}', row['id']) for index, row in enumerate(ordered)])
    app.db.commit()
    original = {row['id']: row.get('workspace') for row in (caller, peer)}
    caller['workspace'] = peer['workspace'] = None
    try:
        import amplifier_web.session_projection as projection
        monkeypatch.setattr(projection, 'hydrate', lambda *args, **kwargs: pytest.fail('Scoped metadata listing loaded a saved body'))
        pages = [app.coordination.list({'limit': 1, 'offset': offset}, caller['id'])
                 for offset in (0, 1)]
        assert [[item['target']['sessionId'] for item in page['items']] for page in pages] == [[caller['id']], [peer['id']]]
        assert pages[0]['nextOffset'] == 1 and pages[0]['truncated']
        assert pages[1]['nextOffset'] is None and not pages[1]['truncated']
        with pytest.raises(ValueError, match='scope'):
            app.coordination.list({'workspace': foreign['workspace']}, caller['id'])
        with pytest.raises(AppError, match='scope'):
            app.coordination.list({'sessionId': foreign['id']}, caller['id'])
        whole = app.coordination.list({'scope': 'all', 'limit': 1}, caller['id'])
        assert whole['items'][0]['target']['sessionId'] == foreign['id']
        exact = app.coordination.list({'scope': 'all', 'sessionId': foreign['id']}, caller['id'])
        assert exact['items'][0]['target']['sessionId'] == foreign['id']
        assert exact['items'][0]['canFollowup'] and exact['items'][0]['canMessage']
        assert not exact['items'][0]['canInterrupt']
        assert not app.runtime.sent and not app.runtime.messages
    finally:
        for row in (caller, peer):
            row['workspace'] = original[row['id']]


async def test_metadata_query_filters_before_paging_and_fences_catalog(app, monkeypatch):
    caller, peer = app.state['sessions']
    peer.update(title='Exact known backend title', runtimeSessionId='native-backend-identity')
    duplicate = app._new_session({'title': peer['title']})
    unrelated = [app._new_session({'title': f'Unrelated {n}'}) for n in range(120)]
    app.state['sessions'].extend([*unrelated, duplicate])
    app.state['conversationOrganization'] = {'archived': {peer['id']: {'at': 1}}}
    app._save()
    residents = {row['id'] for row in app.state['sessions']}
    decoded = []
    from amplifier_web.history_directory import HistoryDirectory
    decode = HistoryDirectory.decode
    def counted(self, row):
        decoded.append(row[0])
        return decode(self, row)
    monkeypatch.setattr(HistoryDirectory, 'decode', counted)
    def forbidden(*args, **kwargs):
        pytest.fail('Metadata discovery must not read transcripts or admit a runtime')
    monkeypatch.setattr('amplifier_web.history_query._rows', forbidden)
    monkeypatch.setattr(app, '_admit_session', forbidden)
    monkeypatch.setattr(app.history, 'refresh', forbidden)
    async def lookup(**args):
        receipt = await app.app_bridge('dispatch', {'action': 'coordination.list', 'args': args}, caller['id'])
        return receipt['result']
    first = await lookup(query='Exact known backend title', limit=1)
    assert first['nextOffset'] == 1
    # The revision owner visits current live overlays once; final query decode
    # is page + lookahead. Dormant bodies/transcripts remain untouched.
    assert len(decoded) <= len(residents) + 2
    second = await lookup(query='Exact known backend title', limit=1, offset=1,
                          directoryRevision=first['directoryRevision'])
    assert {first['items'][0]['target']['sessionId'], second['items'][0]['target']['sessionId']} == {peer['id'], duplicate['id']}
    assert second['nextOffset'] is None
    exact = await lookup(query='native-backend-identity')
    assert [row['target']['sessionId'] for row in exact['items']] == [peer['id']]
    assert exact['items'][0]['sessionRef']['sessionId'] == 'native-backend-identity'
    archived = await lookup(query='Exact known backend title', archive='archived')
    assert [row['target']['sessionId'] for row in archived['items']] == [peer['id']]
    assert archived['items'][0]['archived'] is True
    active = await lookup(query='Exact known backend title', archive='active')
    assert [row['target']['sessionId'] for row in active['items']] == [duplicate['id']]
    app.state['conversationOrganization']['archived'].pop(peer['id'])
    with pytest.raises(AppError, match='directory'):
        await lookup(query='Exact known backend title', archive='active',
                     directoryRevision=active['directoryRevision'])
    restored = await lookup(query='Exact known backend title', archive='active')
    app.state['conversationOrganization']['archived'][peer['id']] = {'at': 2}
    with pytest.raises(AppError, match='directory'):
        await lookup(query='Exact known backend title', archive='active',
                     directoryRevision=restored['directoryRevision'])
    with pytest.raises(AppError, match='filters changed'):
        await lookup(query='different title', directoryRevision=first['directoryRevision'])
    duplicate['title'] = 'Changed catalog'
    app._save_changes(sessions={duplicate['id']}, globals=set())
    with pytest.raises(AppError, match='directory'):
        await lookup(query='Exact known backend title', limit=1, offset=1,
                     directoryRevision=first['directoryRevision'])
    app.state['sharedHistory'] = {'loading': False, 'issueCount': 1, 'reconciliation': {'phase': 'incomplete'}}
    missing = await lookup(query='not registered here')
    assert not missing['items'] and missing['discovery']['complete'] is False
    assert {row['id'] for row in app.state['sessions']} == residents
    assert not app.runtime.sent and not app.runtime.messages and not app.runtime.stops


async def test_metadata_lookup_preserves_managed_and_workspace_scope(app):
    caller, peer = app.state['sessions']
    peer['title'] = 'Scoped unique title'
    receipt = await app.dispatch('session.create', {'title': peer['title'], 'location': {'kind': 'managed'}, 'select': False})
    managed = next(row for row in app.state['sessions'] if row['id'] == receipt['sessionId'])
    app._save()
    own = app.coordination.list({'query': peer['title']}, caller['id'])
    assert [item['target']['sessionId'] for item in own['items']] == [peer['id']]
    all_roots = app.coordination.list({'query': peer['title'], 'scope': 'all'}, caller['id'])
    assert {item['target']['sessionId'] for item in all_roots['items']} == {peer['id'], managed['id']}
    found = next(item for item in all_roots['items'] if item['target']['sessionId'] == managed['id'])
    assert found['location']['kind'] == 'managed'
    assert found['workspace'] == managed['workspace'] != caller['workspace']
    assert found['workspaceLabel'] == 'No workspace'


async def test_exact_native_lookup_rejects_ambiguity_without_admitting(app, monkeypatch):
    caller, peer = app.state['sessions']
    peer.update(nativeProject='project-one', nativeIdentity='duplicate-native')
    other = app._new_session({'title': 'Same native ID in another project'})
    other.update(nativeProject='project-two', nativeIdentity='duplicate-native')
    app.state['sessions'].append(other)
    app._save()
    before = {row['id'] for row in app.state['sessions']}
    monkeypatch.setattr(app, '_admit_session', lambda *a, **kw: pytest.fail('Metadata lookup admitted an owner'))
    async def lookup(**args):
        return (await app.app_bridge('dispatch', {'action': 'coordination.list', 'args': args}, caller['id']))['result']
    with pytest.raises(AppError, match='more than one project'):
        await lookup(sessionId='duplicate-native')
    exact = await lookup(sessionId='duplicate-native', nativeProject='project-two')
    assert exact['items'][0]['target']['sessionId'] == other['id']
    assert exact['items'][0]['sessionRef']['nativeProject'] == 'project-two'
    found = await lookup(query='duplicate-native')
    assert {row['target']['sessionId'] for row in found['items']} == {peer['id'], other['id']}
    assert {row['id'] for row in app.state['sessions']} == before


async def wait(app, targets, **args):
    return (await app.dispatch("coordination.wait", {"targets": targets, **args}))["result"]


def after(result):
    return [{**row["target"], "afterCursor": row["nextCursor"]} for row in result["targets"]]


async def test_wait_two_targets_wakes_first_report_ignores_activity_and_dedups(app):
    sid = app.state["sessions"][1]["id"]
    await worker(app, sid)
    baseline = await wait(app, [{"sessionId": sid}, {"sessionId": sid, "workerId": "child-a"}])
    waiter = asyncio.create_task(wait(app, after(baseline), waitMs=1000))
    await asyncio.sleep(.01)
    await app.on_runtime_event("worker.updated", {"sessionId": sid, "id": "child-a", "phase": "retrying", "status": "running"})
    await asyncio.sleep(.01)
    assert not waiter.done()
    await worker(app, sid, status="idle", report="First report", reportId="report-1", reports=1)
    result = await waiter
    child = result["targets"][1]
    assert child["results"][0]["id"] == "report-1"
    assert child["parentSessionId"] == "native-parent"
    assert child["status"] == "idle"  # A persistent report does not complete its responsibility.
    assert not result["timedOut"]
    assert app.state["view"]["draft"] == "Unsent original"
    assert app.state["selectedSessionId"] != sid
    await worker(app, sid, status="idle", report="First report", reportId="report-1", reports=1)
    replay = await wait(app, after(result), waitMs=1)
    assert replay["timedOut"] and not replay["changed"]
    assert replay["targets"][1]["results"] == []
    assert app.runtime.sent == []


async def test_reconnect_and_restart_keep_receipt_ids_without_replay(app):
    sid = app.state["sessions"][0]["id"]
    await worker(app, sid, status="idle", report="A saved result", reportId="stable-report")
    first = await wait(app, [{"sessionId": sid, "workerId": "child-a"}])
    # Repeating an unacknowledged cursor is intentionally repeatable; clients
    # deduplicate receipt IDs, then persist the advanced cursor.
    retry = await wait(app, [{"sessionId": sid, "workerId": "child-a"}])
    assert first["targets"][0]["results"] == retry["targets"][0]["results"]
    await app.close()
    app.closed = True
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        resumed = await wait(reopened, after(first), waitMs=1)
        assert resumed["targets"][0]["results"] == []
        assert not reopened.runtime.sent and not reopened.runtime.messages
    finally:
        await reopened.close()


async def test_followups_use_shared_admission_preserve_selection_and_draft(app):
    selected, target = app.state["sessions"]
    await app.dispatch("coordination.followup", {"sessionId": target["id"], "text": "Explicit follow-up"}, command_id="top-followup")
    assert app.state["selectedSessionId"] == selected["id"]
    assert app.state["view"]["draft"] == "Unsent original"
    assert app.runtime.sent == [(target["id"], "Explicit follow-up", "top-followup")]
    await worker(app, target["id"], status="idle")
    args = {"sessionId": target["id"], "workerId": "child-a", "text": "Another step"}
    first = await app.dispatch("coordination.followup", args, command_id="worker-followup")
    second = await app.dispatch("coordination.followup", args, command_id="worker-followup")
    assert first["delivery"] == second["delivery"] == "accepted"
    assert second["duplicate"]
    assert len(app.runtime.messages) == 1
    assert app.runtime.messages[0][-1] == "worker-followup"


async def test_unknown_followup_is_not_replayed(app):
    sid = app.state["sessions"][0]["id"]
    await worker(app, sid, status="idle")
    app.runtime.failure = True
    args = {"sessionId": sid, "workerId": "child-a", "text": "Maybe delivered"}
    with pytest.raises(RuntimeError, match="Connection lost"):
        await app.dispatch("coordination.followup", args, command_id="uncertain")
    result = await app.dispatch("coordination.followup", args, command_id="uncertain")
    assert result["delivery"] == "unknown" and result["duplicate"]
    assert len(app.runtime.messages) == 1


async def test_followup_retry_after_browser_identity_rotation_deduplicates(app):
    sid = app.state["sessions"][0]["id"]
    await worker(app, sid, status="idle")
    args = {"sessionId": sid, "workerId": "child-a", "text": "Once across reconnect"}
    app.clients.attach("before")
    app.clients.attach("after", resume="before")
    with app.clients.bind("before"):
        await app.dispatch("coordination.followup", args, command_id="same-request")
    with app.clients.bind("after"):
        result = await app.dispatch("coordination.followup", args, command_id="same-request")
    assert result["duplicate"] and result["delivery"] == "accepted"
    assert len(app.runtime.messages) == 1


async def test_shutdown_during_submission_preserves_unknown_receipt(app):
    sid = app.state["sessions"][0]["id"]
    await worker(app, sid, status="idle")
    entered = asyncio.Event()
    async def pending(*args):
        entered.set()
        await asyncio.Event().wait()
    app.runtime.message_worker = pending
    args = {"sessionId": sid, "workerId": "child-a", "text": "No replay after crash"}
    submission = asyncio.create_task(app.dispatch("coordination.followup", args, command_id="crash-window"))
    await entered.wait()
    submission.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submission
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        result = await reopened.dispatch("coordination.followup", args, command_id="crash-window")
        assert result["duplicate"] and result["delivery"] == "unknown"
        assert reopened._session(sid)["workers"][0]["status"] == "interrupted"
        assert not reopened.runtime.messages
    finally:
        await reopened.close()


async def test_agent_can_control_own_workers_but_peer_followup_requires_root_binding(app):
    caller, other = app.state["sessions"]
    await worker(app, caller["id"], status="idle")
    args = {"sessionId": caller["id"], "workerId": "child-a", "text": "Scoped step"}
    result = await app.app_bridge("dispatch", {"action": "coordination.followup", "args": args, "id": "agent-step"}, caller["id"])
    assert result["delivery"] == "accepted"
    with pytest.raises(AppError, match="transport-bound root generation"):
        await app.app_bridge("dispatch", {"action": "coordination.followup", "args": {"sessionId": other["id"], "text": "Unapproved cross-task work"}}, caller["id"])
    with pytest.raises(AppError, match="user must explicitly"):
        await app.dispatch("coordination.followup", args, origin="agent")
    assert len(app.runtime.messages) == 1


async def test_bound_root_peer_followup_returns_receipt_without_grant(app):
    caller, other = app.state["sessions"]
    await app.on_runtime_event("runtime.generation", {"sessionId": caller["id"], "rootSessionId": caller["id"],
        "event": "generation.started", "generation_id": "peer-woken"})
    await app.on_runtime_event("runtime.status", {"sessionId": caller["id"], "status": "working",
        "event": "input.delivered", "inputId": "incoming-peer"})
    args = {"sessionId": other["id"], "text": "Scoped peer task"}
    transport = {"action": "coordination.followup", "args": args, "id": "peer-followup",
        "_runtimeSessionId": caller["id"], "_generationId": "peer-woken",
        "_inputBindings": [{"inputId": "incoming-peer"}]}
    receipt = await app.app_bridge("dispatch", transport, caller["id"])
    assert receipt["accepted"] and receipt["delivery"] == "queued"
    async with asyncio.timeout(5):
        while app.collaboration.receipt("peer-followup")["delivery"] in {"queued", "submitting"}:
            await asyncio.sleep(.01)
    assert app.collaboration.receipt("peer-followup")["delivery"] == "accepted"
    duplicate = await app.app_bridge("dispatch", transport, caller["id"])
    assert duplicate["duplicate"] and duplicate["delivery"] == "accepted"
    assert app.runtime.sent == [(other["id"], args["text"], "peer-followup")]
    assert not app.collaboration.current(caller["id"])["grants"]
    assert app.state["selectedSessionId"] == caller["id"]
    assert app.state["view"]["draft"] == "Unsent original"
    with pytest.raises(AppError, match="explicitly"):
        await app.app_bridge("dispatch", {**transport, "action": "coordination.interrupt",
            "args": {"sessionId": other["id"]}, "id": "peer-stop"}, caller["id"])


async def test_interrupt_shared_stop_epoch_is_idempotent_and_not_completion(app):
    sid = app.state["sessions"][0]["id"]
    app._session(sid)["status"] = "working"
    first = await app.dispatch("coordination.interrupt", {"sessionId": sid}, command_id="stop-1")
    second = await app.dispatch("coordination.interrupt", {"sessionId": sid}, command_id="stop-1")
    assert first["accepted"] and second["duplicate"]
    assert app._session(sid)["interruptionRevision"] == 1
    assert app._session(sid)["lastInterruption"]["commandId"] == "stop-1"
    await asyncio.sleep(.01)
    assert app.runtime.stops == [(sid,)]
    assert app._session(sid)["status"] != "completed"


async def test_attention_wakes_without_selecting_and_keeps_task_identity_separate(app):
    sid = app.state["sessions"][1]["id"]
    app._session(sid)["status"] = "working"
    baseline = await wait(app, [{"sessionId": sid}])
    pending = asyncio.create_task(wait(app, after(baseline), waitMs=1000))
    await asyncio.sleep(.01)
    app.state.setdefault("runtimeControl", {})[sid] = {"task.get": {"task": {"id": "saved-task", "status": "active", "revision": 3}}}
    response = await app.dispatch("question.create", {"sessionId": sid, "prompt": "Choose the report color", "required": True, "dependency": "report", "allowFreeText": True})
    question_id = response["result"]["id"]
    result = (await pending)["targets"][0]
    assert result["attention"] and result["questionIds"] == [question_id]
    assert result["taskId"] == "saved-task" and result["target"]["sessionId"] == sid
    assert app.state["selectedSessionId"] != sid


async def test_retained_worker_reports_are_bounded_and_not_in_hot_browser_projection(app):
    sid = app.state["sessions"][0]["id"]
    for i in range(35):
        await worker(app, sid, status="idle", report="Text" + str(i), reportId=f"report-{i}")
    snapshot = (await wait(app, [{"sessionId": sid, "workerId": "child-a"}]))["targets"][0]
    assert snapshot["cursorGap"] and snapshot["earliestSequence"] == 4
    assert len(app._session(sid)["workers"][0]["reportReceipts"]) == 32
    browser = app.browser_state()
    assert "reportReceipts" not in browser["sessions"][0]["workers"][0]
    assert len((await app.dispatch("coordination.list", {}))["result"]["items"]) == 3


async def test_repeated_generation_final_has_one_authoritative_message(app):
    sid = app.state["sessions"][0]["id"]
    payload = {"sessionId": sid, "inputId": "input", "generationId": "generation", "text": "A final response"}
    await app.on_runtime_event("assistant.message", payload)
    await app.on_runtime_event("assistant.message", payload)
    assert len(app._session(sid)["messages"]) == 1
    result = await wait(app, [{"sessionId": sid}])
    assert len(result["targets"][0]["results"]) == 1


def test_cursor_rejects_other_identity_marks_rewrites_and_can_acknowledge_expired_history():
    source = {"identity": "one", "results": [{"id": "a", "sequence": 1, "text": "old"}]}
    cursor = delivery(source)["nextCursor"]
    with pytest.raises(ValueError, match="another target"):
        delivery({**source, "identity": "two"}, cursor)
    rewritten = delivery({**source, "results": [{"id": "b", "sequence": 1, "text": "new"}]}, cursor)
    assert rewritten["cursorGap"]
    expired = {"identity": "one", "results": [], "latestSequence": 50}
    gap = delivery(expired, cursor)
    assert gap["cursorGap"] and not gap["hasMore"]
    assert not delivery(expired, gap["nextCursor"])["changed"]


async def test_target_validation_partial_errors_and_cancel_wait(app):
    sid = app.state["sessions"][0]["id"]
    baseline = await wait(app, [{"sessionId": sid}])
    wrong = copy.deepcopy(after(baseline))
    wrong[0]["sessionId"] = app.state["sessions"][1]["id"]
    result = await wait(app, wrong + [{"sessionId": sid, "workerId": "missing"}])
    assert len(result["errors"]) == 2
    with pytest.raises(AppError, match="distinct"):
        await wait(app, [{"sessionId": sid}] * 2)
    pending = asyncio.create_task(wait(app, after(baseline), waitMs=60000))
    await asyncio.sleep(.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not app.runtime.stops


def test_pages_bound_utf8_and_acknowledge_only_delivered_reports():
    rows = [{"id": f"r{i}", "sequence": i, "text": "🌱" * 300} for i in range(5, 9)]
    source = {"identity": "child", "results": rows, "signal": {"status": "completed"}}
    first = delivery(source, max_bytes=512)
    assert first["cursorGap"] and first["hasMore"]
    assert len(first["results"][0]["text"].encode()) == 512
    assert first["results"][0]["textTruncated"]
    second = delivery(source, first["nextCursor"], max_bytes=4096)
    assert [r["id"] for r in second["results"]] == ["r6", "r7", "r8"]
    assert not second["hasMore"]
    assert not delivery(source, second["nextCursor"])["changed"]


async def test_notifier_has_no_read_wait_lost_wakeup():
    signal = ChangeSignal()
    reads = 0
    def read():
        nonlocal reads
        reads += 1
        if reads == 1:
            signal.notify()
        return {"changed": reads == 2}
    result = await signal.wait(read, wait_ms=100)
    assert result["changed"] and not result["timedOut"] and reads == 2


def test_runtime_event_keeps_real_parent_run_and_report_identity():
    kind, value = normalize_event({"type": "child.updated", "sessionId": "child", "parentSessionId": "parent", "runId": "run", "reportId": "report", "reports": 2, "report": "Result"}, "conversation")
    assert kind == "worker.updated"
    assert (value["sessionId"], value["id"], value["parentSessionId"], value["runId"], value["reportId"]) == ("conversation", "child", "parent", "run", "report")
