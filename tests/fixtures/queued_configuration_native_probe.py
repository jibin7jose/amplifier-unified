"""Real Worker/Core/Foundation recovery probe and isolated save-skipping witness.

Only the provider and one pre-write fault are scripted. The production worker
owns mounting, lease/activation, native history, admission and process cleanup.
Run only in the explicitly admitted isolated check environment, never live homes.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import signal
import sys

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))

PROVIDER = r'''
import asyncio, json, os
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
__amplifier_module_type__ = "provider"
class RecoveryProvider:
    name = "recovery"
    def __init__(self, coordinator=None, config=None):
        self.coordinator, self.config = coordinator, config or {}
    def get_info(self):
        return ProviderInfo(id=self.name, display_name="Offline recovery fixture",
            capabilities=["tools"], defaults={"model":"offline-recovery"})
    def get_config_schema(self): return {"fields":[]}
    async def list_models(self): return []
    def parse_tool_calls(self, response): return response.tool_calls or []
    async def complete(self, request, **kwargs):
        scenario = json.loads(Path(self.config["scenario"]).read_text())
        tools = sorted(self.coordinator.get("tools"))
        with Path(scenario["calls"]).open("a") as out:
            out.write(json.dumps({"pid":os.getpid(), "tools":tools})+"\n")
        users = [str(m.content) for m in request.messages
                 if m.role == "user" and not (m.metadata or {}).get("ephemeral")]
        if users and "RECOVERY_NEXT" in users[-1]:
            assert "app_control" in tools and "read_file" not in tools, tools
            return ChatResponse(content=[TextBlock(text="recovery-next-turn-verified")])
        result = next((m for m in request.messages
            if m.role == "tool" and m.tool_call_id == "native-queue-config"), None)
        if result is not None:
            # The host must observe queue acceptance before this real generation
            # can finish. No fabricated generation, counter or event is used.
            async with asyncio.timeout(30):
                while not Path(scenario["release"]).exists():
                    await asyncio.sleep(.02)
            return ChatResponse(content=[TextBlock(text="recovery-original-turn-finished")])
        assert "app_control" in tools and "read_file" in tools, tools
        return ChatResponse(content=[], tool_calls=[ToolCall(
            id="native-queue-config", name="app_control",
            arguments={"operation":"dispatch","args":{
                "action":"configuration.apply","id":"native-original-config",
                "args":{"id":scenario["sid"],"config":scenario["desired"],"whenIdle":True}}})])
async def mount(coordinator, config=None):
    await coordinator.mount("providers", RecoveryProvider(coordinator, config), name="recovery")
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def process_stamp(pid):
    """Linux hosted-check identity, not merely a reusable PID."""
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat.rsplit(")", 1)[1].split()[19]


async def writer_available(workspace, native_id):
    def acquire_release():
        from amplifier_foundation.session import SharedSessionStore, SessionBusyError
        try:
            handle = SharedSessionStore(workspace, native_id).acquire(app="recovery-acceptance-probe")
        except SessionBusyError:
            return False
        try:
            return True
        finally:
            handle.release()
    return await asyncio.wait_for(asyncio.to_thread(acquire_release), 3)


async def confirm_process_exit(process, fallback_signals):
    """Fallback only for this probe's owned handles after ordinary close fails."""
    if process.returncode is not None:
        return
    for signum in (signal.SIGTERM, signal.SIGKILL):
        if process.returncode is not None:
            return
        if os.name == "posix":
            assert os.getpgid(process.pid) == process.pid
        event = {"pid": process.pid, "signal": signum.name, "outcome": "attempted"}
        fallback_signals.append(event)
        try:
            if os.name == "posix":
                os.killpg(process.pid, signum)
            elif signum == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
            event["outcome"] = "sent"
        except ProcessLookupError:
            event["outcome"] = "already_absent"
        try:
            await asyncio.wait_for(process.wait(), 5)
            event["returncode"] = process.returncode
            return
        except TimeoutError:
            event["wait"] = "timed_out"
            continue
    raise AssertionError(f"Owned worker exit unconfirmed: {process.pid}")


def finish_cleanup_evidence(root, report, primary_failure):
    """Retain partial evidence even when a worker's identity record is corrupt."""
    errors, sources = report["cleanupErrors"], []
    for path in sorted(root.glob("worker-source-*.json")):
        try:
            sources.append(json.loads(path.read_text()))
        except (OSError, ValueError, TypeError) as exc:
            errors.append("worker-source " + path.name + ": " + type(exc).__name__ + ": " + str(exc))
    report["workerSources"] = sources
    try:
        (root / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
    except OSError as exc:
        errors.append("evidence-write: " + type(exc).__name__ + ": " + str(exc))
    if errors or report["cleanupFallbackSignals"]:
        detail = "Native cleanup did not complete normally: " + json.dumps({
            "errors": errors, "fallbackSignals": report["cleanupFallbackSignals"]})
        if primary_failure is not None:
            primary_failure.add_note(detail)
        else:
            raise AssertionError(detail)


async def worker(root, mutant):
    from amplifier_web import configuration_recovery
    from amplifier_web.runtime_controls import RuntimeControls, override_path
    from amplifier_web.runtime_worker import Worker, configure_protocol
    original = RuntimeControls.prepare_configuration

    def refuse_once(self, config):
        plan = original(self, config)
        marker = root / "prewrite-fault.json"
        if not marker.exists():
            marker.write_text(json.dumps({
                "pid": os.getpid(), "nativeId": self.session.session_id,
                "configurationBefore": digest(override_path(self.session.session_id)),
                "controlsBefore": digest(self.state_path()),
                "boundary": "original_validation_completed_before_save"}))
            raise ValueError("Synthetic one-shot pre-write refusal")
        return plan

    RuntimeControls.prepare_configuration = refuse_once
    source = inspect.getsource(configuration_recovery.native_apply)
    needle = "    controls.save_configuration(plan)\n"
    assert source.count(needle) == 1
    changed = source.replace(needle, "    pass  # Deliberate save-skipping product regression.\n")
    if mutant:
        # Change only this process's copy of the actual product function. Never
        # alter a checkout, installed file, receipt, oracle or permission guard.
        namespace = dict(vars(configuration_recovery))
        exec(compile(changed, configuration_recovery.__file__, "exec"), namespace)
        configuration_recovery.native_apply = namespace["native_apply"]
    (root / f"worker-source-{os.getpid()}.json").write_text(json.dumps({
        "pid": os.getpid(), "mutant": mutant, "product": configuration_recovery.__file__,
        "probePid": os.getppid(), "processGroup": os.getpgrp(),
        "startTicks": process_stamp(os.getpid()),
        "originalFunction": hashlib.sha256(source.encode()).hexdigest(),
        "executedFunction": hashlib.sha256((changed if mutant else source).encode()).hexdigest()}))
    configure_protocol()
    await Worker().run()


async def until(predicate, *, seconds=30):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(.02)


async def probe(root, mutant):
    root.mkdir(parents=True, exist_ok=False)
    workspace, shared, home = (root / name for name in ("workspace", "shared", "app"))
    workspace.mkdir()
    shared.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(shared), AMPLIFIER_WEB_HOME=str(home),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "capture"),
        AMPLIFIER_RUNTIME_IMMUTABLE="1", PYTHONPATH=str(SOURCE))
    for key in list(os.environ):
        if key.endswith(("_API_KEY", "_ACCESS_TOKEN")) or key in {"OPENAI_KEY", "ANTHROPIC_AUTH_TOKEN"}:
            os.environ.pop(key, None)
    import amplifier_module_context_simple
    import amplifier_module_tool_filesystem
    import amplifier_module_loop_live
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_web.auth import new_session, SESSION_COOKIE
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.builtin_behaviors import APPENDED_DEFAULTS
    from amplifier_web.configuration_recovery import evidence
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_controls import override_path
    from amplifier_web.server import create_app
    from amplifier_web.session_files import sessions_dir
    from aiohttp.test_utils import TestClient, TestServer
    import yaml

    # Fail loudly on missing imports; there is deliberately no skip or install.
    versions = {name: importlib.metadata.version(name) for name in
        ("amplifier-core", "amplifier-foundation", "amplifier-module-loop-live",
         "amplifier-module-context-simple", "amplifier-module-tool-filesystem")}
    paths = {name: str(Path(module.__file__).parent) for name, module in
        (("context-simple", amplifier_module_context_simple),
         ("tool-filesystem", amplifier_module_tool_filesystem),
         ("loop-live", amplifier_module_loop_live))}
    provider = root / "provider" / "amplifier_module_provider_recovery"
    provider.mkdir(parents=True)
    (provider / "__init__.py").write_text(PROVIDER)
    scenario = root / "scenario.json"
    declaration = {"module": "provider-recovery", "source": str(provider.parent),
                   "config": {"scenario": str(scenario), "default_model": "offline-recovery"}}
    bundle = root / "bundle.yaml"
    bundle.write_text(yaml.safe_dump({
        "bundle": {"name": "native-recovery-fixture", "version": SNAPSHOT_VERSION},
        "session": {"orchestrator": {"module": "loop-live", "config": {"use_streaming": False}},
                    "context": {"module": "context-simple", "source": paths["context-simple"]}},
        "providers": [declaration],
        "tools": [{"module": "tool-filesystem", "source": paths["tool-filesystem"]}]}))
    (shared / "settings.yaml").write_text(yaml.safe_dump({
        "bundle": {"active": str(bundle), "app": []},
        "web_bundles": {"excluded": list(APPENDED_DEFAULTS)},
        "sources": {"modules": {**paths, "provider-recovery": str(provider.parent)}}}))
    command = [sys.executable, "-u", str(Path(__file__).resolve()), "--worker", str(root)]
    if mutant:
        command.append("--skip-save")
    runtime = RuntimeManager(command=command, startup_timeout=45,
        retention={"prewarm_on_select": False})
    web_app = await create_app(home, workspace=workspace, runtime=runtime,
        voice=False, background_updates=False, preload_providers=False)
    app = web_app["service"]
    runtime.app_bridge, runtime.home = app.app_bridge, home
    client = TestClient(TestServer(web_app))
    processes, sid, report = [], None, {"mutant": mutant, "versions": versions, "paths": paths}
    calls = root / "provider-calls.jsonl"
    primary_failure = None
    try:
        await client.start_server()
        headers = {"Cookie": SESSION_COOKIE + "=" + new_session(web_app["session_secret"]),
                   "X-Amplifier-State-Transport": "delta-v1"}

        async def action(name, args, identity):
            response = await client.post("/api/actions", headers=headers,
                json={"action": name, "args": args, "id": identity})
            result = await response.json()
            assert response.status == 200 and result.get("accepted"), result
            return result

        await action("session.create", {"title": "Native recovery", "bundle": str(bundle)}, "native-create")
        owner = app._session()
        sid = owner["id"]
        await action("session.naming", {"id": sid, "automatic": False}, "native-disable-naming")
        await runtime.start(owner, app.on_runtime_event)
        processes.append(runtime.workers[sid]["process"])
        assert not calls.exists(), "Mounting must not invoke the provider."
        initial = await runtime.control(sid, "configuration.inspect", {})
        desired = copy.deepcopy(initial["plan"])
        declared = next(row for row in desired["tools"] if row["module"] == "tool-filesystem")
        assert declared.get("enabled", True)
        declared["enabled"] = False
        release = root / "release-final"
        scenario.write_text(json.dumps({"sid": sid, "desired": desired,
            "calls": str(calls), "release": str(release)}))
        grant = (await action("capability.grant", {"sessionId": sid,
            "effects": ["runtime_configuration"], "scopes": ["session"],
            "reason": "Synthetic queued configuration recovery"}, "native-grant"))["result"]
        grant_before = copy.deepcopy(app.configuration_authority.record(grant["id"]))
        await action("conversation.send", {"sessionId": sid, "text": "RECOVERY_ORIGINAL"},
            "native-original-input")
        await until(lambda: owner.get("pendingConfiguration", {}).get("phase") == "queued")
        assert not owner["collaborationGeneration"]["terminal"]
        assert owner["pendingConfiguration"]["authority"]["grantId"] == grant["id"]
        native_id = runtime.workers[sid]["runtime_id"]
        assert not await writer_available(workspace, native_id), "Held native writer was not detected."
        report["heldWriterControl"] = True
        release.touch()
        await until(lambda: owner.get("pendingConfiguration", {}).get("phase") == "error"
            and sid not in app.management.pending_config_tasks)
        record = app.management.configuration_record(sid, owner["pendingConfiguration"]["operationId"])
        assert record["request"]["authorization"] == "grant"
        assert record["state"]["effect"] == "none"
        assert record["state"]["stage"] == "reconciled"
        assert owner["collaborationGeneration"]["terminal"]
        attempt = record["attempts"][0]
        assert evidence(attempt, home=home)["effect"] == "none"
        assert attempt["workerPid"] == processes[0].pid
        fault = json.loads((root / "prewrite-fault.json").read_text())
        assert fault["configurationBefore"] == digest(override_path(attempt["nativeSessionId"]))
        assert not owner["configurationBusy"] and len(record["attempts"]) == 1
        assert "configurationOperationFence" not in owner
        before_calls = calls.read_bytes()
        proof = (await action("configuration.operation.read", {"sessionId": sid,
            "operationId": record["id"]}, "native-read"))["result"]
        reconciled = (await action("configuration.operation.reconcile", {"sessionId": sid,
            "operationId": record["id"], "expectedRevision": proof["revision"]}, "native-reconcile"))["result"]
        assert reconciled["state"]["effect"] == "none"
        assert calls.read_bytes() == before_calls and runtime.workers[sid]["process"] is processes[0]
        resume_args = {"sessionId": sid, "operationId": record["id"],
                       "expectedRevision": reconciled["revision"]}
        resume = await action("configuration.operation.resume", resume_args, "native-resume")
        await until(lambda: sid not in app.management.pending_config_tasks
            and owner["pendingConfiguration"]["phase"] in {"ready", "error"}, seconds=60)
        current = app.management.configuration_record(sid, record["id"])
        if sid in runtime.workers and runtime.workers[sid]["process"] not in processes:
            processes.append(runtime.workers[sid]["process"])
        assert current["request"] == record["request"] and len(current["attempts"]) == 2
        assert current["history"][:len(record["history"])] == record["history"]
        assert app.configuration_authority.record(grant["id"]) == grant_before
        assert calls.read_bytes() == before_calls, "Recovery must not repeat the completed generation."
        assert evidence(current["attempts"][1], home=home)["effect"] == "saved_not_mounted"
        assert processes[0].returncode is not None and len(processes) == 2
        report.update(nativeId=attempt["nativeSessionId"], originalOperation=record["id"],
            attempts=2, originalAuthorityPreserved=True, noRecoveryProviderCalls=True,
            prewriteRefusal=True, resumedSaveAcknowledged=True, actualWorkerRemounted=True,
            state=current["state"])
        # Identical oracle for clean and regressed product. A failed setup,
        # provider or first refusal never reaches this witness boundary.
        if current["state"]["phase"] != "ready" or current["state"]["effect"] != "mounted":
            assert "does not match loaded-plan readback" in current["state"].get("error", ""), current["state"]
            report["negativeWitness"] = "resumed_save_skipped_loaded_plan_mismatch"
            raise AssertionError("Native recovery did not mount the original requested plan")
        stored = json.loads(override_path(attempt["nativeSessionId"]).read_text())
        assert next(row for row in stored["tools"] if row["module"] == "tool-filesystem")["enabled"] is False
        loaded = await runtime.control(sid, "configuration.inspect", {})
        assert app.management.configuration_readback_matches(desired, loaded["plan"])
        duplicate = await action("configuration.operation.resume", resume_args, "native-resume")
        assert duplicate.get("duplicate") and duplicate["result"] == resume["result"]
        assert app.management.configuration_record(sid, record["id"]) == current
        assert runtime.workers[sid]["process"] is processes[1] and calls.read_bytes() == before_calls
        await action("conversation.send", {"sessionId": sid, "text": "RECOVERY_NEXT"},
            "native-next-input")
        await until(lambda: any(m.get("text") == "recovery-next-turn-verified"
            for m in owner["messages"]) and owner["collaborationGeneration"].get("terminal"))
        await until(lambda: runtime.workers[sid].get("parked"))
        messages = SessionHistoryStore(sessions_dir(workspace) / attempt["nativeSessionId"]).load_messages()
        ids = [(m.get("metadata") or {}).get("amplifier_input", {}).get("id") for m in messages]
        assert ids.count("native-original-input") == ids.count("native-next-input") == 1
        assert len([m for m in messages if m.get("role") == "tool"
                    and m.get("tool_call_id") == "native-queue-config"]) == 1
        assert await writer_available(workspace, attempt["nativeSessionId"])
        report.update(mountedPlanVerified=True, nextInputUsable=True, originalInputCount=1,
            originalToolResultCount=1, duplicateResumePassive=True, writerReleasedWhileParked=True)
    except BaseException as exc:
        primary_failure = exc
        report["primaryFailure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        # Always unblock the provider if a prior assertion failed.
        (root / "release-final").touch()
        if sid in runtime.workers and runtime.workers[sid]["process"] not in processes:
            processes.append(runtime.workers[sid]["process"])
        cleanup_errors, fallback_signals = [], []
        close = asyncio.create_task(client.close())
        try:
            await asyncio.wait_for(asyncio.shield(close), 45)
        except BaseException as exc:
            cleanup_errors.append(type(exc).__name__ + ": " + str(exc))
        finally:
            try:
                # Give production cleanup a normal 45s budget first. If it did
                # not complete, identify and reap only this probe's processes.
                for row in tuple(runtime.workers.values()):
                    if row["process"] not in processes:
                        processes.append(row["process"])
                for process in processes:
                    try:
                        await confirm_process_exit(process, fallback_signals)
                    except BaseException as exc:
                        cleanup_errors.append(type(exc).__name__ + ": " + str(exc))
                if not close.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(close), 5)
                    except BaseException as exc:
                        cleanup_errors.append(type(exc).__name__ + ": " + str(exc))
                        close.cancel()
                if close.done() and not close.cancelled() and close.exception() is not None:
                    cleanup_errors.append("close: " + str(close.exception()))
                if sid is not None:
                    native_id = report.get("nativeId", owner.get("runtimeSessionId") or sid)
                    try:
                        report["writerAvailableAfterClose"] = await writer_available(workspace, native_id)
                    except BaseException as exc:
                        cleanup_errors.append("writer: " + type(exc).__name__ + ": " + str(exc))
            finally:
                if any(p.returncode is None for p in processes) or runtime.workers:
                    cleanup_errors.append("Owned worker exit or runtime removal unconfirmed.")
                if sid is not None and report.get("writerAvailableAfterClose") is not True:
                    cleanup_errors.append("Writer release unconfirmed.")
                report.update(cleanupErrors=cleanup_errors,
                    cleanupFallbackSignals=fallback_signals,
                    processes=[{"pid": p.pid, "returncode": p.returncode} for p in processes],
                    runtimeRowsAfterClose=len(runtime.workers))
                finish_cleanup_evidence(root, report, primary_failure)


async def bounded_probe(root, mutant):
    # Parent timeout asks for ordinary cancellation so the probe's finally can
    # close real workers and release their writer before the process exits.
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    signals = (signal.SIGTERM, signal.SIGINT) if os.name == "posix" else ()
    def cancel_once():
        if not task.cancelling():
            task.cancel()
    for signum in signals:
        loop.add_signal_handler(signum, cancel_once)
    try:
        async with asyncio.timeout(150):
            await probe(root, mutant)
    finally:
        for signum in signals:
            loop.remove_signal_handler(signum)


if __name__ == "__main__":
    if sys.argv[1] == "--worker":
        asyncio.run(worker(Path(sys.argv[2]), "--skip-save" in sys.argv))
    else:
        asyncio.run(bounded_probe(Path(sys.argv[1]), "--skip-save" in sys.argv))
