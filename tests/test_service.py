import asyncio
import json
import pytest
from amplifier_web.service import AppService, AppError, validate_theme


class Runtime:
    def __init__(self):
        self.sent = []
        self.stopped = []
        self.started = []
    async def start(self, session, emit):
        self.started.append(session["id"])
    async def send(self, session, text, input_id, emit):
        self.sent.append((session["id"], text, input_id))
        await emit("assistant.message", {"sessionId": session["id"], "text": "Test transport result", "inputId": input_id})
        await emit("runtime.generation", {"sessionId": session["id"], "event": "generation.finished",
            "generation_id": "test-generation", "input_ids": [input_id], "text": "Test transport result",
            "active_job_ids": [], "disposition": "manager_turn_finished"})
        await emit("runtime.status", {"sessionId": session["id"], "status": "idle"})
    async def stop(self, sid, *, reason="stopped"):
        self.stopped.append(sid)
    async def close(self):
        pass


class ClosableRuntime:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


class BlockingRuntime(ClosableRuntime):
    def __init__(self):
        super().__init__()
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.cancelled = False
        self.finished = False

    async def close(self):
        self.closed += 1
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.finished = True


async def test_shutdown_waits_for_lifecycle_replacement_then_closes_installed_runtime(tmp_path):
    original, candidate = BlockingRuntime(), ClosableRuntime()
    service = AppService(tmp_path, original, workspace=tmp_path)

    async def replace():
        async with service.runtime_lifecycle_lock:
            await service.replace_runtime(candidate)

    lifecycle = asyncio.create_task(replace())
    await asyncio.wait_for(original.entered.wait(), 3)
    shutdown = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    assert service.closed and not shutdown.done()
    original.release.set()
    await lifecycle
    await shutdown
    assert service.runtime is candidate and candidate.closed == 1


async def test_shutdown_waits_for_replacement_to_finish_retiring_previous_runtime(tmp_path):
    original, candidate = BlockingRuntime(), ClosableRuntime()
    service = AppService(tmp_path, original, workspace=tmp_path)

    async def replace():
        async with service.runtime_lifecycle():
            await service.replace_runtime(candidate)

    lifecycle = asyncio.create_task(replace())
    await asyncio.wait_for(original.entered.wait(), 3)
    shutdown = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    assert service.closed and not shutdown.done()
    assert not original.cancelled
    original.release.set()
    with pytest.raises(asyncio.CancelledError):
        await lifecycle
    await shutdown
    assert original.finished and candidate.closed == 1


async def test_shutdown_rejects_and_closes_lifecycle_candidate_queued_before_shutdown(tmp_path):
    original, candidate = ClosableRuntime(), ClosableRuntime()
    service = AppService(tmp_path, original, workspace=tmp_path)
    await service.runtime_lifecycle_lock.acquire()

    async def replace():
        async with service.runtime_lifecycle_lock:
            await service.replace_runtime(candidate)

    lifecycle = asyncio.create_task(replace())
    await asyncio.sleep(0)
    shutdown = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    assert service.closed
    service.runtime_lifecycle_lock.release()
    with pytest.raises(RuntimeError, match="host is closing"):
        await lifecycle
    await shutdown
    assert service.runtime is original and original.closed == 1
    assert candidate.closed == 1


async def test_repair_does_not_construct_a_candidate_after_shutdown_begins_while_waiting_on_state_lock(tmp_path, monkeypatch):
    from amplifier_web import updates
    from amplifier_web.management import Management

    original, candidate = ClosableRuntime(), ClosableRuntime()
    service = AppService(tmp_path, original, workspace=tmp_path)
    manager = Management(service)
    service.management = manager
    created = asyncio.Event()

    def runtime_candidate():
        created.set()
        return candidate

    async def process(*args, **kwargs):
        raise AssertionError("Repair must not start after shutdown begins.")

    monkeypatch.setattr(service, "runtime_candidate", runtime_candidate)
    monkeypatch.setattr(updates, "process", process)
    await service.runtime_lifecycle_lock.acquire()
    await service.lock.acquire()
    repair = asyncio.create_task(manager.perform("maintenance.repair", {}))
    await asyncio.sleep(0)
    service.runtime_lifecycle_lock.release()
    await asyncio.sleep(0)
    shutdown = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    assert service.closed
    service.lock.release()
    with pytest.raises(asyncio.CancelledError):
        await repair
    await shutdown
    assert not created.is_set()
    assert original.closed == 1 and candidate.closed == 0


async def test_shutdown_cancels_blocked_repair_and_closes_all_runtime_candidates(tmp_path, monkeypatch):
    from amplifier_web import updates
    from amplifier_web.management import Management

    original, candidate = ClosableRuntime(), ClosableRuntime()
    service = AppService(tmp_path, original, workspace=tmp_path)
    manager = Management(service)
    service.management = manager
    entered = asyncio.Event()

    def runtime_candidate():
        return candidate

    async def process(*args, **kwargs):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(service, "runtime_candidate", runtime_candidate)
    monkeypatch.setattr(updates, "process", process)
    repair = asyncio.create_task(manager.perform("maintenance.repair", {}))
    await asyncio.wait_for(entered.wait(), 3)

    await asyncio.wait_for(service.close(), 3)

    with pytest.raises(asyncio.CancelledError):
        await repair
    assert service.runtime is original
    assert original.closed == 1
    assert candidate.closed == 1


async def test_repair_rechecks_busy_after_a_queued_message_is_admitted(tmp_path, monkeypatch):
    from amplifier_web import updates
    from amplifier_web.management import Management
    from amplifier_web.updates import UpdateManager

    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = Management(service)
    service.management = manager
    service.update_manager = UpdateManager(service)
    await service.dispatch("session.create", {})
    sending, release = asyncio.Event(), asyncio.Event()

    async def send(*args):
        sending.set()
        await release.wait()

    async def process(*args, **kwargs):
        pytest.fail("Repair must not start after a message is admitted.")

    monkeypatch.setattr(service, "_send", send)
    monkeypatch.setattr(updates, "process", process)
    await service.lock.acquire()
    message = asyncio.create_task(service.dispatch("conversation.send", {"text": "Queued first"}))
    await asyncio.sleep(0)
    repair = asyncio.create_task(manager.perform("maintenance.repair", {}))
    while not service.runtime_lifecycle_lock.locked():
        await asyncio.sleep(0)
    service.lock.release()
    try:
        await asyncio.wait_for(sending.wait(), 3)
        with pytest.raises(ValueError, match="Finish active work"):
            await repair
    finally:
        release.set()
        await message
        await service.close()


@pytest.fixture
async def service(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    await app.dispatch("session.create", {})
    yield app
    await app.close()


async def test_progress_messages_arrive_during_generation_and_replays_do_not_erase_next_stream(service):
    from amplifier_web.runtime import normalize_event
    sid = service.get_state()['selectedSessionId']
    await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'working'})
    async def message(text, sequence, at, **extra):
        event = {'type': 'assistant.message', 'text': text, 'generation_id': 'generation',
                 'input_ids': ['input'], 'sequence': sequence, 'time': at, **extra}
        await service.on_runtime_event(*normalize_event(event, sid))

    await message('Direction accepted', 5, 100)
    await message('Workers running', 9, 120)
    await message('Workers running', 12, 140)  # New block with identical text.
    session = service._session(sid)
    assert session['status'] == 'working'
    assert [m['text'] for m in session['messages']] == ['Direction accepted', 'Workers running', 'Workers running']
    assert [m['createdAt'] for m in session['messages']] == [100, 120, 140]
    await service.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'Next update'})
    await message('Direction accepted', 5, 100)  # Replayed earlier block.
    assert len(session['messages']) == 3 and session['streaming'] == 'Next update'
    await message('Build ready', 16, 160)
    assert 'streaming' not in session
    await service.on_runtime_event('runtime.generation', {'sessionId': sid, 'event': 'generation.finished',
        'generation_id': 'generation', 'input_ids': ['input'], 'text': 'Build ready'})
    await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})
    assert len(session['messages']) == 4


async def test_message_ids_and_legacy_fallback_are_scoped_without_dropping_later_progress(service):
    from amplifier_web.runtime import normalize_event
    sid = service.get_state()['selectedSessionId']
    async def message(text, generation='generation', **extra):
        await service.on_runtime_event(*normalize_event({'type': 'assistant.message', 'text': text,
            'generation_id': generation, 'input_ids': ['input'], **extra}, sid))

    await message('Starting')
    await message('Progress')
    await message('Progress')  # Legacy replay, no per-message identity.
    await message('Finished', message_id='final', event_id='event-final')
    await message('Finished', message_id='final', event_id='replayed-envelope')
    await message('Finished')  # Legacy final-result fallback after content block.
    await message('Finished', message_id='another-final', event_id='event-next')
    await message('Finished', generation='next', message_id='final', event_id='event-final')
    assert [m['text'] for m in service._session(sid)['messages']] == ['Starting', 'Progress', 'Finished', 'Finished', 'Finished']


async def test_missing_generation_and_message_ids_do_not_make_a_session_wide_dedup_key(service):
    from amplifier_web.runtime import normalize_event
    sid = service.get_state()['selectedSessionId']
    for input_id, text in [('one', 'Starting'), ('one', 'Progress'), ('one', 'Progress'), ('two', 'Progress')]:
        await service.on_runtime_event(*normalize_event({'type': 'assistant.message', 'text': text, 'sequence': 1}, sid, input_id))
    for _ in range(2):
        await service.on_runtime_event(*normalize_event({'type': 'assistant.message', 'text': 'Uncorrelated'}, sid))
    assert [m['text'] for m in service._session(sid)['messages']] == ['Starting', 'Progress', 'Progress', 'Uncorrelated', 'Uncorrelated']


async def test_message_replay_identity_survives_host_restart_without_reusing_a_worker_sequence(tmp_path):
    from amplifier_web.runtime import normalize_event
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app.get_state()['selectedSessionId']
    event = {'type':'assistant.message', 'text':'Saved progress', 'generation_id':'first',
             'input_ids':['input'], 'sequence':5, 'time':123}
    await app.on_runtime_event(*normalize_event(event, sid))
    await app.close()
    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await restored.on_runtime_event(*normalize_event(event, sid))
        await restored.on_runtime_event(*normalize_event({**event, 'generation_id':'second', 'time':140}, sid))
        messages = restored._session(sid)['messages']
        assert len(messages) == 2
        assert [m['generationId'] for m in messages] == ['first', 'second']
        assert [m['createdAt'] for m in messages] == [123, 140]
    finally:
        await restored.close()


async def test_conversation_survives_restart_and_deduplication(tmp_path):
    runtime = Runtime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch("session.create", {}, command_id="create")
    await app.dispatch("conversation.send", {"text": "Remember this"}, command_id="send")
    await asyncio.gather(*app.tasks)
    original = app.get_state()["sessions"][0]
    await app.close()
    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    duplicate = await restored.dispatch("conversation.send", {"text": "Remember this"}, command_id="send")
    assert duplicate["duplicate"] is True
    assert restored.get_state()["sessions"][0]["messages"] == original["messages"]
    assert not restored.runtime.sent
    with pytest.raises(AppError, match="different contents"):
        await restored.dispatch("conversation.send", {"text": "Changed"}, command_id="send")
    await restored.close()


async def test_busy_admission_preserves_browser_draft_attachments_and_history(tmp_path):
    from amplifier_web.runtime import SessionInUseError
    class BusyRuntime(Runtime):
        async def send(self, session, text, input_id, emit):
            # Progress uses AppService.lock just as the real RuntimeManager
            # does. The admission path must not still be holding that lock.
            await emit("runtime.status", {"sessionId": session["id"], "status": "starting"})
            raise SessionInUseError({"app": "amplifier-cli", "host": "test-host", "pid": 123})

    app = AppService(tmp_path, BusyRuntime(), workspace=tmp_path)
    await app.dispatch("session.create", {})
    session_id = app.get_state()["selectedSessionId"]
    attachment = await app.dispatch("attachment.add", {
        "sessionId": session_id, "name": "draft.txt", "base64": "ZHJhZnQ="})
    attachment_id = attachment["state"]["sessions"][0]["draftAttachments"][0]["id"]
    await app.dispatch("view.update", {"patch": {"draft": "Keep this draft"}})

    with pytest.raises(AppError, match="conversation is in use") as error:
        await asyncio.wait_for(app.dispatch("conversation.send", {
            "sessionId": session_id, "text": "Keep this draft",
            "attachmentIds": [attachment_id]}, command_id="busy-send"), 2)

    assert error.value.status == 409
    session = app.get_state()["sessions"][0]
    assert session["messages"] == []
    assert session["draftAttachments"][0]["id"] == attachment_id
    assert app.get_state()["view"]["draft"] == "Keep this draft"
    assert not session.get("execution", {}).get("turns", [])
    assert not app.runtime.sent
    assert session["lockOwner"]["app"] == "amplifier-cli"
    duplicate = await app.dispatch("conversation.send", {
        "sessionId": session_id, "text": "Keep this draft",
        "attachmentIds": [attachment_id]}, command_id="busy-send")
    assert duplicate["accepted"] is False
    await app.close()


async def test_shared_agent_control_state_and_stale_revisions(service):
    before = service.get_state()
    await service.app_bridge("dispatch", {"action": "view.update", "args": {"patch": {"scheme": "dark", "panel": "appearance"}}, "expectedRevision": before["revision"]}, before["selectedSessionId"])
    assert service.get_state()["view"]["scheme"] == "dark"
    with pytest.raises(AppError, match="app changed"):
        await service.dispatch("view.update", {"patch": {"scheme": "light"}}, expected_revision=before["revision"])
    snapshot = service.get_state()
    snapshot["view"]["scheme"] = "corrupted"
    assert service.get_state()["view"]["scheme"] == "dark"


async def test_voice_pins_session_and_end_does_not_stop_work(service):
    first = service.get_state()["selectedSessionId"]
    await service.dispatch("session.create", {})
    await service.record_voice_transcript("user", "Voice request", voice_id="call", item_id="one", session_id=first)
    await service.voice_delegate("Voice request", "voice-1", session_id=first)
    await asyncio.gather(*service.tasks)
    result = await service.wait_for_response(first, "voice-1", timeout=1)
    assert result["text"] == "Test transport result"
    assert service.runtime.sent[0][0] == first
    await service.dispatch("call.end", {})
    assert not service.runtime.stopped


async def test_skin_roundtrip_and_external_assets_rejected(service):
    css = '#amp-one {color: #4338f4; background: #e8eeff;}'
    await service.dispatch("theme.apply", {"name": "Custom violet", "css": css}, origin="agent")
    exported = await service.dispatch("theme.export", {})
    assert exported["effects"][0]["content"] == css
    for bad in ['@import "https://example.com/a.css";', '#amp-one {background:url("https://example.com/x")}']:
        with pytest.raises(AppError):
            validate_theme(bad)
    assert service.get_state()["theme"]["css"] == css


async def test_saved_skin_survives_changed_defaults_until_explicit_reset(tmp_path, monkeypatch):
    saved = {"name": "Saved studio skin", "css": "/* Private palette */ #amp-one {--a-accent: #765432;}"}
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    await app.dispatch("theme.apply", saved, origin="agent")
    await app.close()

    replacement = "#amp-one {--a-accent: #123456;}"
    monkeypatch.setattr(AppService, "default_theme", lambda self: replacement)
    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        assert restored.get_state()["theme"] == saved
        exported = await restored.dispatch("theme.export", {}, origin="agent")
        assert exported["effects"][0]["content"] == saved["css"]
        await restored.dispatch("theme.reset", {}, origin="ui")
        assert restored.get_state()["theme"] == {"name": "Amplifier Unified", "css": replacement}
    finally:
        await restored.close()


async def test_device_observation_and_agent_effect_delivery(service):
    revision = service.get_state()["revision"]
    await service.update_device({"clientId": "desktop", "visibleText": "My conversation", "controls": []})
    assert service.get_state()["revision"] == revision
    assert service.get_state()["devices"]["desktop"]["visibleText"] == "My conversation"
    receipt = await service.dispatch("notification.request", {}, origin="agent")
    assert receipt["effects"][0]["id"] == service.get_state()["deviceCommands"][-1]["id"]


async def test_tool_approval_cannot_be_self_granted(service):
    sid = service.get_state()["selectedSessionId"]
    await service.on_runtime_event("approval.requested", {"sessionId": sid, "id": "permission", "prompt": "Allow tool?"})
    with pytest.raises(AppError, match="user approval"):
        await service.dispatch("approval.respond", {"id": "permission", "decision": "allow"}, origin="agent")
    await service.on_runtime_event("approval.resolved", {"sessionId": sid, "id": "permission", "decision": "expired"})
    assert service.get_state()["sessions"][0]["approvals"][0]["status"] == "expired"


async def test_preparation_progress_is_visible_and_clears_when_work_begins(service):
    sid = service.get_state()["selectedSessionId"]
    await service.on_runtime_event("runtime.status", {"sessionId": sid, "status": "starting", "phase": "bundle-preparation", "detail": "Loading your configured bundle and tools.", "elapsedSeconds": 25})
    session = service.get_state()["sessions"][0]
    assert session["progress"]["elapsedSeconds"] == 25
    assert session["progress"]["detail"] == "Loading your configured bundle and tools."
    await service.on_runtime_event("runtime.status", {"sessionId": sid, "status": "working"})
    assert "progress" not in service.get_state()["sessions"][0]


async def test_parallel_tool_activity_tracks_completion_without_claiming_turn_finished(service):
    sid = service.get_state()["selectedSessionId"]
    await service.on_runtime_event("runtime.status", {"sessionId": sid, "status": "working"})
    for identity in ("one", "two"):
        await service.on_runtime_event("runtime.tool", {"sessionId": sid, "tool": "delegate", "callId": identity, "phase": "pre"})
    activity = service.get_state()["sessions"][0]["activity"]
    assert len(activity["activeTools"]) == 2
    assert activity["label"] == "Delegating work"
    await service.on_runtime_event("runtime.tool", {"sessionId": sid, "tool": "delegate", "callId": "one", "phase": "post"})
    session = service.get_state()["sessions"][0]
    assert [t["callId"] for t in session["activity"]["activeTools"]] == ["two"]
    assert session["status"] == "working"
    await service.on_runtime_event("runtime.tool", {"sessionId": sid, "tool": "delegate", "callId": "two", "phase": "error"})
    activity = service.get_state()["sessions"][0]["activity"]
    assert activity["phase"] == "model"
    assert not activity["activeTools"]
    assert activity["lastEvent"]["phase"] == "error"
    assert activity["updatedAt"] >= activity["startedAt"]
    await service.on_runtime_event("runtime.status", {"sessionId": sid, "status": "idle"})
    assert service.get_state()["sessions"][0]["activity"]["phase"] == "idle"

async def test_voice_waits_for_identified_completion_not_early_text(service):
    sid = service.get_state()['selectedSessionId']
    await service.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'Starting the tools', 'inputId': 'voice:pending'})
    waiter = asyncio.create_task(service.wait_for_response(sid, 'voice:pending', timeout=2))
    await asyncio.sleep(0)
    assert not waiter.done()
    await service.on_runtime_event('runtime.generation', {'sessionId': sid, 'event': 'generation.finished',
        'generation_id': 'other', 'input_ids': ['different'], 'text': 'Unrelated completed result', 'active_job_ids': []})
    await asyncio.sleep(0)
    assert not waiter.done()
    await service.on_runtime_event('runtime.generation', {'sessionId': sid, 'event': 'generation.finished',
        'generation_id': 'correct', 'input_ids': ['voice:pending'], 'text': 'Worker launched; still running',
        'active_job_ids': ['worker-1'], 'disposition': 'manager_turn_finished'})
    result = await waiter
    assert result['generation_id'] == 'correct'
    assert result['active_job_ids'] == ['worker-1']


async def test_voice_acceptance_is_durable_and_never_replays(tmp_path):
    runtime = Runtime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app.get_state()['selectedSessionId']
    await app.voice_delegate('One operation', 'voice:once', sid)
    await asyncio.gather(*app.tasks)
    await app.close()
    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    assert (await restored.voice_delegate('One operation', 'voice:once', sid))['duplicate']
    assert not restored.runtime.sent
    with pytest.raises(AppError, match='different contents'):
        await restored.voice_delegate('Different operation', 'voice:once', sid)
    await restored.close()


async def test_settings_navigation_and_filters_are_agent_visible(service):
    patch = {"settingsSection": "capabilities", "settingsExpanded": ["loaded-modules"], "settingsFilters": {"loaded-modules": "tool-*"}}
    await service.app_bridge("dispatch", {"action": "view.update", "args": {"patch": patch}}, service.get_state()["selectedSessionId"])
    view = service.get_state()["view"]
    assert all(view[key] == value for key, value in patch.items())
    await service.dispatch("view.update", {"patch": {"settingsExpanded": []}})
    assert service.get_state()["view"]["settingsFilters"]["loaded-modules"] == "tool-*"


async def test_attention_acknowledgement_is_shared_and_changed_items_reappear(service):
    cached = service.projections.attention(service.state)
    assert cached['items'] == []
    row={'id':'repo','label':'Community bundle','status':'update','current':'old','latest':'new'}
    service.state['settings'].setdefault('updates',{})['autoInstall']=False
    service.state['updates']={'items':[row]}
    initial=service.get_state()['attention']
    assert initial['unread']==1
    assert cached['items'] == []  # Acknowledgement must use current global facts.
    assert initial['sections']['maintenance']==initial['pages']['updates']==1
    await service.app_bridge('dispatch',{'action':'attention.read','args':{'ids':['source:repo']}},service.get_state()['selectedSessionId'])
    assert service.get_state()['attention']['unread']==0
    assert service.get_state()['updates']['items'][0]['status']=='update'
    service.state['updates']['items'][0]['latest']='newer'
    assert service.get_state()['attention']['unread']==1
    service.state['updates']['items'][0]['status']='current'
    assert service.get_state()['attention']['items']==[]
    with pytest.raises(AppError):await service.dispatch('attention.read',{'ids':['not-real']})


async def test_attention_read_persists_and_errors_route_to_their_page(tmp_path):
    app=AppService(tmp_path,Runtime(),workspace=tmp_path)
    app.state['actionStatus']={'providers.test':{'phase':'error','error':'Connection timed out','commandId':'one'}}
    await app.dispatch('attention.read',{'ids':['action:providers.test']})
    await app.close()
    restored=AppService(tmp_path,Runtime(),workspace=tmp_path)
    assert restored.get_state()['attention']['unread']==0
    restored.state['actionStatus']['providers.test']['commandId']='two'
    attention=restored.get_state()['attention']
    assert attention['sections']['setup']==attention['pages']['providers']==1
    await restored.close()


async def test_successful_session_start_clears_persisted_error_and_attention(service):
    sid=service.get_state()['selectedSessionId']
    await service.on_runtime_event('runtime.error',{'sessionId':sid,'error':'ValueError: contextTokens must be a positive integer'})
    assert service.get_state()['attention']['unread']==1
    await service.on_runtime_event('runtime.status',{'sessionId':sid,'status':'ready','phase':'ready'})
    assert 'error' not in service.get_state()['sessions'][0]
    assert service.get_state()['attention']['unread']==0
    await service.on_runtime_event('runtime.status',{'sessionId':sid,'status':'idle'})
    assert 'error' not in service.get_state()['sessions'][0]


@pytest.mark.parametrize('status',['starting','idle','stopping','stopped'])
async def test_non_ready_status_does_not_hide_unresolved_runtime_failure(service,status):
    sid=service.get_state()['selectedSessionId']
    await service.on_runtime_event('runtime.error',{'sessionId':sid,'error':'Provider authentication failed'})
    await service.on_runtime_event('runtime.status',{'sessionId':sid,'status':status})
    assert service.get_state()['sessions'][0]['error']=='Provider authentication failed'
    assert service.get_state()['attention']['unread']==1


async def test_authentication_error_publishes_and_refreshes_its_own_connections(service, monkeypatch):
    from amplifier_web.management import Management
    from amplifier_web.setup import SetupManager

    failed_id = service.get_state()['selectedSessionId']
    await service.dispatch('session.create', {})
    selected_id = service.get_state()['selectedSessionId']
    assert selected_id != failed_id
    service.management = Management(service)
    refreshed = asyncio.Event()
    reads = []

    async def perform(manager, action, args):
        assert not service.lock.locked()
        reads.append((action, args))
        return {'providers': [], 'providersWorkspace': args['workspace']}

    async def warm(manager, workspace, *, refresh=False):
        assert refresh is True
        refreshed.set()

    monkeypatch.setattr(SetupManager, 'perform', perform)
    monkeypatch.setattr(service.management, 'warm_providers', warm)
    revision = service.state['revision']
    await service.on_runtime_event('runtime.error', {
        'sessionId': failed_id, 'error': 'Provider authentication failed',
    })
    assert service.state['revision'] > revision
    assert service._session(failed_id)['failure']['category'] == 'authentication'
    assert service._session(failed_id)['status'] == 'error'
    assert service.state['selectedSessionId'] == selected_id
    assert not service._session(selected_id).get('error')
    await asyncio.wait_for(refreshed.wait(), 3)
    assert len(reads) == 1
    assert reads[0][0] == 'providers.list'
    assert reads[0][1]['sessionId'] == failed_id
    assert reads[0][1]['refresh'] is True
    assert service.state['actionStatus']['providers.list']['phase'] == 'ready'


@pytest.mark.parametrize('status', ['idle', 'stopped'])
async def test_late_settled_status_preserves_terminal_error_and_execution(service, status):
    from amplifier_web.execution import ensure_turn
    sid = service.get_state()['selectedSessionId']
    session = service._session(sid)
    ensure_turn(session, 'failed-turn')
    await service.on_runtime_event('runtime.error', {
        'sessionId': sid, 'errorType': 'ContextLengthError', 'error': 'context limit',
    })
    await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': status})
    turn = session['execution']['turns'][0]
    assert session['status'] == 'error'
    assert turn['phase'] == 'error'
    assert session['failure']['category'] == 'context_limit'


async def test_child_generation_cannot_clear_or_replace_root_failure_state(service):
    sid = service.get_state()['selectedSessionId']
    session = service._session(sid)
    await service.on_runtime_event('runtime.error', {
        'sessionId': sid, 'errorType': 'ContextLengthError', 'error': 'context limit',
    })
    await service.on_runtime_event('runtime.generation', {
        'sessionId': 'child', 'rootSessionId': sid, 'event': 'generation.started',
    })
    assert session['status'] == 'error'
    assert session['failure']['category'] == 'context_limit'
    await service.on_runtime_event('runtime.generation', {
        'sessionId': sid, 'rootSessionId': sid, 'event': 'generation.finished',
    })
    await service.on_runtime_event('runtime.generation', {
        'sessionId': 'child', 'rootSessionId': sid, 'event': 'generation.failed',
        'error_type': 'ChildFailure',
    })
    assert 'turnErrorType' not in session


async def test_send_does_not_clear_newer_or_other_session_draft(service):
    original = service.state['selectedSessionId']
    await service.dispatch('view.update', {'patch': {'draft': 'Already typing another message'}})
    await service.dispatch('conversation.send', {'sessionId': original, 'text': 'Previously submitted text'})
    assert service.state['view']['draft'] == 'Already typing another message'
    await service.dispatch('session.create', {})
    await service.dispatch('view.update', {'patch': {'draft': 'Other conversation text'}})
    await service.dispatch('conversation.send', {'sessionId': original, 'text': 'Other conversation text'})
    assert service.state['view']['draft'] == 'Other conversation text'


async def test_background_activity_cannot_restart_finished_conversation(service):
    from amplifier_web.runtime import normalize_event
    session=service._session();sid=session['id']
    for status in ['idle','stopped','interrupted','error','ready']:
        session['status']=status
        session['activity']={'phase':status,'label':'Existing state','activeTools':[]}
        await service.on_runtime_event(*normalize_event({'type':'runtime.activity','phase':'retrying','detail':'Retry 3 of 3'},sid))
        assert session['status']==status and session['activity']['label']=='Existing state'
    await service.on_runtime_event(*normalize_event({'type':'input.delivered','input_id':'new'},sid))
    assert session['status']=='working'
    await service.on_runtime_event(*normalize_event({'type':'runtime.activity','phase':'retrying','detail':'Retry 2 of 3'},sid))
    assert session['activity']['phase']=='retrying'
    await service.on_runtime_event(*normalize_event({'type':'session.idle'},sid))
    await service.on_runtime_event(*normalize_event({'type':'runtime.activity','phase':'model'},sid))
    assert session['status']=='idle'


async def test_failed_direct_send_settles_activity_without_replay_or_lost_input(tmp_path):
    import sys
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.updates import UpdateManager
    # An unexpected startup failure differs from deliberately closing the host.
    runtime = RuntimeManager(command=[sys.executable, '-c', 'raise SystemExit(3)'],
                             retention={'prewarm_on_select': False})
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        request = {'text': 'Keep this unsent request'}
        with pytest.raises(AppError, match='did not send your message') as failure:
            await app.dispatch('conversation.send', request, command_id='failed-start')
        assert failure.value.status == 503 and failure.value.code == 'worker_startup_failed'
        session = app.get_state()['sessions'][0]
        assert session['status'] == 'error'
        assert 'message has been saved' in session['error']
        message = next(row for row in session['messages'] if row.get('inputId') == 'failed-start')
        assert message['text'] == request['text']
        assert message['delivery']['status'] == 'failed'
        assert session['execution']['turns'][-1]['phase'] == 'error'
        assert not UpdateManager(app).busy()
        duplicate = await app.dispatch('conversation.send', request, command_id='failed-start')
        assert duplicate['duplicate'] is True
        assert duplicate['accepted'] is False and duplicate['delivery'] == 'failed'
        assert not runtime.workers
    finally:
        await app.close()


async def test_first_message_fallback_title_is_clean_and_stays_automatic(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch("session.create", {}, command_id="create")
        await app.dispatch("conversation.send", {"text": "## Use the bash tool to run git status here, then tell me in one sentence"},
                           command_id="send")
        await asyncio.gather(*app.tasks)
        session = app.get_state()["sessions"][0]
        assert session["title"] == "Use the bash tool to run git status here, then tell me in one…"
        assert session["titleSource"] != "manual"
    finally:
        await app.close()


async def test_restart_and_rewarming_cannot_turn_unconfirmed_work_into_success(tmp_path):
    from amplifier_web.execution import ensure_turn
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    await app.dispatch('session.create', {})
    session = app._session()
    sid = session['id']
    ensure_turn(session, 'unfinished')
    session['status'] = 'working'
    app._publish_changes(sessions={sid})
    await app.close()
    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        current = restored._session(sid)
        assert current['execution']['turns'][0]['phase'] == 'interrupted'
        assert current['failure']['category'] == 'execution_interrupted'
        for status in ('starting', 'ready', 'idle'):
            await restored.on_runtime_event('runtime.status', {'sessionId': sid, 'status': status})
        assert current['execution']['turns'][0]['phase'] == 'interrupted'
        assert current['error'] and current['failure']['category'] == 'execution_interrupted'
        assert not restored.runtime.sent
        await restored.on_runtime_event('runtime.generation', {'sessionId': sid, 'event': 'generation.started'})
        assert 'error' not in current
    finally:
        await restored.close()


async def test_worker_exit_only_marks_unfinished_root_work(service):
    from amplifier_web.execution import ensure_turn
    session = service._session()
    sid = session['id']
    await service.on_runtime_event('runtime.ended', {'sessionId': sid})
    assert 'error' not in session
    ensure_turn(session, 'unfinished')
    await service.on_runtime_event('runtime.ended', {'sessionId': sid, 'backgroundOnly': True})
    assert session['execution']['turns'][0]['phase'] == 'running'
    await service.on_runtime_event('runtime.ended', {'sessionId': sid})
    assert session['execution']['turns'][0]['phase'] == 'interrupted'
    assert session['failure']['category'] == 'execution_interrupted'


@pytest.mark.parametrize('later_turn', [False, True])
async def test_manager_failure_corrects_earlier_idle_receipt_for_only_its_input(service, later_turn):
    from amplifier_web.execution import ensure_turn
    session = service._session(); sid = session['id']
    ensure_turn(session, 'previous')
    await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})
    ensure_turn(session, 'failed-input')
    await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'working'})
    await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})
    assert session['execution']['turns'][-1]['phase'] == 'completed'
    if later_turn:
        ensure_turn(session, 'new-input')
    await service.on_runtime_event('runtime.generation', {
        'sessionId': sid, 'rootSessionId': sid, 'event': 'generation.failed',
        'generation_id': 'failed-generation', 'input_ids': ['failed-input'],
        'error_type': 'LLMError', 'error_category': 'context_measurement',
        'count_failure': {'category': 'service', 'httpStatus': 500, 'attempts': 3, 'retryable': True},
    })
    turns = {turn['id']: turn['phase'] for turn in session['execution']['turns']}
    assert turns['previous'] == 'completed'
    assert turns['failed-input'] == 'error'
    if later_turn:
        assert turns['new-input'] == 'running'
    else:
        await service.on_runtime_event('runtime.error', {'sessionId': sid, 'error': 'Manager turn failed'})
        assert session['status'] == 'error'
        assert session['failure']['countFailure']['httpStatus'] == 500
