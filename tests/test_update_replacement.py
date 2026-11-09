"""Unknown application replacement outcomes keep admission closed.

Every installer, package inventory, and restart operation is synthetic.
"""
from amplifier_web.state_records import load as load_saved_state
import asyncio
import copy
import json
import sys
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_features, app_updates
from amplifier_web.auth import data_identity
from amplifier_web.service import AppError, AppService
from amplifier_web.update_readiness import recovery_candidate
from amplifier_web.updates import UpdateManager, work_paused
from test_app_features import BASELINE, CANDIDATE, HOST_APP, REVISION, managed, stage_feature


async def stage_replacement(managed, kind):
    if kind == "feature":
        await stage_feature(managed)
        return copy.deepcopy(CANDIDATE), ["native-desktop", "tui"]
    managed.service.state["updates"]["application"] = {
        "status": "update", "revision": REVISION, "latest": HOST_APP["version"],
    }
    graph = sorted([*copy.deepcopy(BASELINE), copy.deepcopy(HOST_APP)], key=lambda row: row["name"])
    app_updates.components.read_graph.return_value = graph
    await app_updates.stage(managed.manager)
    pending = managed.service.state['updates']['pendingApp']
    folder = managed.manager.directory / 'applications' / pending['revision'] / pending['generation']
    validated = json.loads((folder / 'validated.json').read_text())
    return validated['componentGraph'], validated['extras']


def replacement_install(args, kwargs):
    return "tool" in args and "install" in args and not kwargs.get("env", {}).get("UV_TOOL_DIR")


def saved_updates(service):
    return load_saved_state(service.db)["updates"]


async def save_held_input(service, identity):
    sid = service._session()["id"]
    receipt = await service.dispatch("conversation.send", {
        "sessionId": sid, "text": "Continue once the replacement is verified",
    }, command_id=identity, include_state=False)
    assert receipt["accepted"] and receipt["delivery"] == "queued_for_update"
    saved = json.loads(service.db.execute("SELECT receipt FROM commands WHERE id=?", (identity,)).fetchone()[0])
    assert saved["delivery"] == "queued_for_update"
    assert [sid, identity] in saved_updates(service)["drain"]["messages"]
    assert not service.runtime.sent
    # Saving a new human input does not open runtime admission for retries.
    with pytest.raises(AppError, match="ecosystem update is activating"):
        await service.dispatch("conversation.retry", {"sessionId": sid, "inputId": identity})


def successful_probe():
    return "AMPLIFIER_UPDATE_PROBE=" + json.dumps({"ok": True, "stage": "complete", "version": HOST_APP["version"],
        "isolated": True, "packageInEnvironment": True, "frontendPresent": True, "loginAvailable": True})


@pytest.mark.parametrize("kind", ["app", "feature"])
async def test_replacement_marker_is_durable_before_install_and_atomically_handed_to_restart(managed, monkeypatch, kind):
    await stage_replacement(managed, kind)
    original_process, original_publish = app_updates.process, managed.service._publish
    entered = []
    after_install = []

    async def process(*args, **kwargs):
        if replacement_install(args, kwargs):
            state = managed.service.state["updates"]
            marker = copy.deepcopy(state.get("pendingReplacement"))
            assert isinstance(marker, dict) and marker
            assert saved_updates(managed.service)["pendingReplacement"] == marker
            assert not state.get("pendingRestart")
            assert work_paused(managed.service.state) and managed.manager.awaiting_restart()
            # An override constrains a dependency only if it is requested.
            # Reinstall the complete qualified graph, including dependencies of
            # optional transitive extras, for both app and feature replacements.
            requirements = args[args.index("--with-requirements") + 1]
            assert requirements == args[args.index("--overrides") + 1]
            assert str(requirements).endswith("/components.txt")
            entered.append(marker)
        return await original_process(*args, **kwargs)

    def publish(*args, **kwargs):
        original_publish(*args, **kwargs)
        if entered:
            state = managed.service.state["updates"]
            after_install.append(copy.deepcopy(state))
            assert work_paused(managed.service.state)
            assert bool(state.get("pendingReplacement")) != bool(state.get("pendingRestart"))

    monkeypatch.setattr(app_updates, "process", process)
    monkeypatch.setattr(managed.service, "_publish", publish)
    await app_updates.activate(managed.manager)
    state = managed.service.state["updates"]
    assert len(entered) == 1 and after_install
    assert not state.get("pendingReplacement") and state.get("pendingRestart")
    assert not saved_updates(managed.service).get("pendingReplacement")
    assert saved_updates(managed.service)["pendingRestart"] == state["pendingRestart"]
    managed.restart.assert_awaited_once_with(managed.manager)


async def fail_replacement(managed, monkeypatch, kind, failure="probe"):
    graph, extras = await stage_replacement(managed, kind)
    original = app_updates.process
    installed = False

    async def process(*args, **kwargs):
        nonlocal installed
        if replacement_install(args, kwargs):
            installed = True
            # Package presence can become visible before verification succeeds.
            monkeypatch.setattr(app_updates, "installed_extras", lambda: extras)
            if failure == "install":
                raise RuntimeError("synthetic installer outcome unknown")
        elif installed and app_updates.PROBE in args and failure == "probe":
            raise RuntimeError("synthetic installed verification failed")
        return await original(*args, **kwargs)

    if failure == "graph":
        async def read_graph(*args, **kwargs):
            return [{**row, "version": "99.0.0"} if row["name"] == "httpx" else row for row in graph] if installed else graph
        monkeypatch.setattr(app_updates.components, "read_graph", read_graph)
    monkeypatch.setattr(app_updates, "process", process)
    await app_updates.activate(managed.manager)
    assert installed
    return graph, extras


@pytest.mark.parametrize("kind", ["app", "feature"])
@pytest.mark.parametrize("failure", ["install", "probe", "graph"])
async def test_uncertain_replacement_failures_hold_work_and_later_feature_fastpath(managed, monkeypatch, kind, failure):
    await fail_replacement(managed, monkeypatch, kind, failure)
    service, manager = managed.service, managed.manager
    state = service.state["updates"]
    marker = copy.deepcopy(state.get("pendingReplacement"))
    assert marker and state["phase"] == "error"
    assert saved_updates(service)["pendingReplacement"] == marker
    assert not state.get("pendingRestart")
    assert work_paused(service.state) and manager.awaiting_restart()
    await save_held_input(service, "held-after-failure")
    await manager.tick()
    assert not service.runtime.sent
    assert saved_updates(service)["pendingReplacement"] == marker
    before = list(managed.calls)
    # Reproduce the dangerous package-presence shortcut after the uv write.
    monkeypatch.setattr(app_updates, "installed_extras", lambda: ["native-desktop", "tui"])
    await manager.command("featureInstall", {"feature": "native-desktop", "hostInstanceId": service.instance_id}, "retry-after-uncertain")
    assert state["featureResults"]["retry-after-uncertain"]["phase"] == "error"
    assert state["pendingReplacement"] == marker
    assert managed.calls == before
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize("kind", ["app", "feature"])
async def test_cancellation_during_replacement_keeps_durable_marker(managed, monkeypatch, kind):
    await stage_replacement(managed, kind)
    original = app_updates.process
    entered = asyncio.Event()

    async def process(*args, **kwargs):
        if replacement_install(args, kwargs):
            assert saved_updates(managed.service).get("pendingReplacement")
            entered.set()
            await asyncio.Event().wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(app_updates, "process", process)
    activation = asyncio.create_task(app_updates.activate(managed.manager))
    await asyncio.wait_for(entered.wait(), 2)
    activation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await activation
    state = managed.service.state["updates"]
    assert state["phase"] == "interrupted"
    assert state.get("pendingReplacement") and not state.get("pendingRestart")
    assert saved_updates(managed.service)["pendingReplacement"] == state["pendingReplacement"]
    assert work_paused(managed.service.state) and managed.manager.awaiting_restart()
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize("kind", ["app", "feature"])
async def test_startup_from_preinstall_snapshot_retains_marker_without_replay(managed, monkeypatch, kind):
    await stage_replacement(managed, kind)
    original = app_updates.process
    snapshots = []

    async def process(*args, **kwargs):
        if replacement_install(args, kwargs):
            snapshots.append(copy.deepcopy(saved_updates(managed.service)))
            raise RuntimeError("model abrupt installer interruption")
        return await original(*args, **kwargs)

    monkeypatch.setattr(app_updates, "process", process)
    await app_updates.activate(managed.manager)
    assert snapshots[0]["phase"] == "activating"
    marker = snapshots[0]["pendingReplacement"]
    managed.service.state["updates"] = snapshots[0]
    calls = list(managed.calls)
    successor = UpdateManager(managed.service)
    managed.service.update_manager = successor
    assert managed.service.state["updates"]["pendingReplacement"] == marker
    assert work_paused(managed.service.state) and successor.awaiting_restart()
    assert managed.calls == calls
    managed.restart.assert_not_awaited()


async def test_reopened_app_restores_uncertain_marker_and_refuses_package_presence_retry(managed, monkeypatch):
    await fail_replacement(managed, monkeypatch, "feature")
    marker = copy.deepcopy(saved_updates(managed.service)["pendingReplacement"])
    before = list(managed.calls)
    await managed.service.close()
    reopened = AppService(managed.service.data_dir, workspace=managed.service.default_workspace)
    try:
        manager = UpdateManager(reopened)
        reopened.update_manager = manager
        assert reopened.state["updates"]["pendingReplacement"] == marker
        assert work_paused(reopened.state) and manager.awaiting_restart()
        await manager.command("featureInstall", {"feature": "native-desktop", "hostInstanceId": reopened.instance_id}, "retry-from-reopened-host")
        assert reopened.state["updates"]["featureResults"]["retry-from-reopened-host"]["phase"] == "error"
        assert saved_updates(reopened)["pendingReplacement"] == marker
        assert managed.calls == before
        managed.restart.assert_not_awaited()
    finally:
        await reopened.close()


@pytest.mark.parametrize("kind", ["app", "feature"])
@pytest.mark.parametrize("mismatch", [None, "source", "revision", "extras", "graph", "same-instance", "wrong-health"])
async def test_uncertain_replacement_opens_only_for_exact_authenticated_new_host(managed, monkeypatch, kind, mismatch):
    graph, extras = await fail_replacement(managed, monkeypatch, kind)
    marker = copy.deepcopy(managed.service.state["updates"]["pendingReplacement"])
    successor = UpdateManager(managed.service)
    managed.service.update_manager = successor
    successor.running_identity = {"version": HOST_APP["version"], "revision": REVISION,
                                  "instanceId": "old-process" if mismatch == "same-instance" else "verified-new-process"}
    managed.capture(successor)
    monkeypatch.setattr(app_updates, "installed_extras", lambda: [] if mismatch == "extras" else extras)
    dependencies = [row for row in graph if row["name"] != "amplifier-unified"]
    monkeypatch.setattr(app_updates.components, "installed_graph", lambda: dependencies + [{"name": "unexpected", "version": "1.0"}] if mismatch == "graph" else dependencies)
    if mismatch == "source":
        monkeypatch.setattr(app_features, "running_application", lambda manager: {**HOST_APP, "url": "https://github.com/other/amplifier-unified"})
    elif mismatch == "revision":
        monkeypatch.setattr(app_features, "running_application", lambda manager: {**HOST_APP, "revision": "d" * 40})
    health = {"ok": True, "app": "amplifier-unified", "dataIdentity": "another-data-root" if mismatch == "wrong-health" else data_identity(successor.home),
              **successor.running_identity}
    before = list(managed.calls)

    async def repaired_probe(*args, **kwargs):
        assert tuple(map(str, args)) == (sys.executable, "-I", "-c", app_updates.PROBE, *extras)
        managed.calls.append((tuple(map(str, args)), kwargs))
        return successful_probe()

    monkeypatch.setattr(app_updates, "process", repaired_probe)
    await save_held_input(managed.service, "held-for-recovery")
    confirmed = await successor.confirm_readiness(health)
    state = managed.service.state["updates"]
    assert confirmed is (mismatch is None)
    if mismatch:
        assert state["pendingReplacement"] == marker
        assert work_paused(managed.service.state) and successor.awaiting_restart()
        await successor.tick()
        assert not managed.service.runtime.sent
        assert saved_updates(managed.service)["pendingReplacement"] == marker
        assert state["drain"]["phase"] == "replacing"
    else:
        assert not state.get("pendingReplacement") and not state.get("pendingRestart")
        assert not work_paused(managed.service.state) and not successor.awaiting_restart()
        assert not saved_updates(managed.service).get("pendingReplacement")
        if kind == "feature":
            assert state["featureResults"]["request-one"]["phase"] == "installed"
        # The fixture has no background loop. Exercise its real tick/finish
        # lifecycle; only subsequent unrelated component discovery is mocked.
        assert successor.wakeup.is_set()
        assert not managed.service.runtime.sent
        successor.check = AsyncMock()
        await successor.tick()
        await successor.tick()
        assert [row[2] for row in managed.service.runtime.sent] == ["held-for-recovery"]
        assert state["drain"]["phase"] == "released"
        saved = json.loads(managed.service.db.execute(
            "SELECT receipt FROM commands WHERE id='held-for-recovery'").fetchone()[0])
        assert saved["delivery"] == "accepted"
        receipt = await managed.service.dispatch("conversation.send", {"text": "Admitted after exact recovery"})
        assert receipt["delivery"] == "accepted"
    if mismatch:
        assert managed.calls == before
    else:
        assert len(managed.calls) == len(before) + 1
        assert managed.calls[-1][0] == (sys.executable, "-I", "-c", app_updates.PROBE, *extras)
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize("kind", ["app", "feature"])
async def test_legacy_activating_app_without_replacement_marker_is_unqualified_and_held(managed, kind):
    await stage_replacement(managed, kind)
    state = managed.service.state["updates"]
    state.update(phase="activating", pendingRestart=None)
    state.pop("pendingReplacement", None)
    before = list(managed.calls)
    successor = UpdateManager(managed.service)
    managed.service.update_manager = successor
    assert state["pendingReplacement"].get("unqualified") is True
    assert work_paused(managed.service.state) and successor.awaiting_restart()
    assert recovery_candidate(successor) is None
    assert saved_updates(managed.service)["pendingReplacement"] == state["pendingReplacement"]
    assert managed.calls == before
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize("marker", [{}, [], "malformed", 0, {"unqualified": True}])
async def test_malformed_or_unqualified_marker_stays_held_regardless_of_error_phase(managed, marker):
    state = managed.service.state["updates"]
    state.update(phase="error", pendingReplacement=copy.deepcopy(marker), pendingRestart=None)
    successor = UpdateManager(managed.service)
    managed.service.update_manager = successor
    assert state["pendingReplacement"] == marker
    assert work_paused(managed.service.state) and successor.awaiting_restart()
    assert recovery_candidate(successor) is None
    successor.running_identity = {"version": HOST_APP["version"], "revision": REVISION, "instanceId": "new-process"}
    health = {"ok": True, "app": "amplifier-unified", "dataIdentity": data_identity(successor.home), **successor.running_identity}
    assert await successor.confirm_readiness(health) is False
    assert state["pendingReplacement"] == marker and work_paused(managed.service.state)
    assert managed.calls == []


@pytest.mark.parametrize("field", ["sourceInstanceId", "empty-source-instance", "nonstring-source-instance", "qualification"])
async def test_incomplete_qualified_marker_cannot_prove_new_host_or_installation(managed, monkeypatch, field):
    graph, extras = await fail_replacement(managed, monkeypatch, "feature")
    state = managed.service.state["updates"]
    marker = state["pendingReplacement"]
    if field == "empty-source-instance":
        marker["sourceInstanceId"] = ""
    elif field == "nonstring-source-instance":
        marker["sourceInstanceId"] = []
    else:
        marker.pop(field)
    managed.manager.running_identity = {"version": HOST_APP["version"], "revision": REVISION, "instanceId": "new-process"}
    monkeypatch.setattr(app_updates, "installed_extras", lambda: extras)
    monkeypatch.setattr(app_updates.components, "installed_graph", lambda: [row for row in graph if row["name"] != "amplifier-unified"])
    health = {"ok": True, "app": "amplifier-unified", "dataIdentity": data_identity(managed.manager.home), **managed.manager.running_identity}
    assert recovery_candidate(managed.manager) is None
    assert await managed.manager.confirm_readiness(health) is False
    assert state["pendingReplacement"] == marker
    assert work_paused(managed.service.state)


async def recovering_host(managed, monkeypatch, kind="feature"):
    graph, extras = await fail_replacement(managed, monkeypatch, kind)
    successor = UpdateManager(managed.service)
    managed.service.update_manager = successor
    successor.running_identity = {"version": HOST_APP["version"], "revision": REVISION, "instanceId": "new-process"}
    dependencies = [row for row in graph if row["name"] != "amplifier-unified"]
    monkeypatch.setattr(app_updates, "installed_extras", lambda: extras)
    monkeypatch.setattr(app_updates.components, "installed_graph", lambda: dependencies)
    managed.capture(successor)
    health = {"ok": True, "app": "amplifier-unified", "dataIdentity": data_identity(successor.home), **successor.running_identity}
    return successor, health, dependencies


@pytest.mark.parametrize("inherited_cache", [None, "/unrelated-browser-cache"])
@pytest.mark.parametrize("cache_usable", [True, False])
async def test_browser_recovery_uses_prepared_cache_and_retains_failed_probe_fence(
        managed, monkeypatch, inherited_cache, cache_usable):
    monkeypatch.setattr(app_updates, "installed_extras", lambda: ["managed-browser", "tui"])
    browser = {"blankLaunchVerified": True, "selection": "owned",
               "ownedExecutable": "/prepared/chromium", "selectedExecutable": "/prepared/chromium", "launchArgs": []}
    monkeypatch.setattr(app_updates, "prepare_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr(app_updates, "qualify_browser", AsyncMock(return_value=browser))
    successor, health, _ = await recovering_host(managed, monkeypatch, "app")
    marker = copy.deepcopy(managed.service.state["updates"]["pendingReplacement"])
    if inherited_cache is None:
        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    else:
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", inherited_cache)
    probes = []

    async def probe(*args, **kwargs):
        assert tuple(map(str, args)) == (sys.executable, "-I", "-c", app_updates.PROBE, "managed-browser", "tui")
        probes.append(kwargs)
        # A service restart need not inherit the environment of the process
        # that downloaded and qualified the browser. Recovery uses that cache.
        if kwargs.get("env", {}).get("PLAYWRIGHT_BROWSERS_PATH") != str(app_features.managed_browser_cache(successor.home)):
            raise RuntimeError("Browser absent from inherited cache")
        if not cache_usable:
            raise RuntimeError("Prepared browser is no longer usable")
        return successful_probe()

    monkeypatch.setattr(app_updates, "process", probe)
    await save_held_input(managed.service, "held-browser-recovery")
    assert await successor.confirm_readiness(health) is cache_usable
    assert len(probes) == 1
    state = managed.service.state["updates"]
    assert work_paused(managed.service.state) is (not cache_usable)
    if not cache_usable:
        assert saved_updates(managed.service)["pendingReplacement"] == marker
        assert not managed.service.runtime.sent
    else:
        assert not state.get("pendingReplacement") and not state.get("pendingRestart")
        successor.check = AsyncMock()
        await successor.tick()
        await successor.tick()
        assert state["drain"]["phase"] == "released"
        assert [row[2] for row in managed.service.runtime.sent] == ["held-browser-recovery"]


@pytest.mark.parametrize("failure", ["import-error", "failed-structured", "unstructured", "wrong-version", "not-isolated", "incomplete"])
async def test_exact_metadata_and_health_cannot_clear_failed_recovery_probe(managed, monkeypatch, failure):
    successor, health, _ = await recovering_host(managed, monkeypatch)
    marker = copy.deepcopy(managed.service.state["updates"]["pendingReplacement"])
    probes = []

    async def failed_probe(*args, **kwargs):
        assert tuple(map(str, args)) == (sys.executable, "-I", "-c", app_updates.PROBE, "native-desktop", "tui")
        probes.append(args)
        if failure == "import-error":
            raise RuntimeError("synthetic optional foreground import failed")
        if failure == "failed-structured":
            return "AMPLIFIER_UPDATE_PROBE=" + json.dumps({"ok": False, "stage": "capabilities", "errorType": "ModuleNotFoundError", "version": HOST_APP["version"]})
        if failure == "unstructured":
            return HOST_APP["version"]
        return "AMPLIFIER_UPDATE_PROBE=" + json.dumps({"ok": True,
            "stage": "imports" if failure == "incomplete" else "complete",
            "isolated": failure != "not-isolated",
            "version": "99.0.0" if failure == "wrong-version" else HOST_APP["version"]})

    monkeypatch.setattr(app_updates, "process", failed_probe)
    assert await successor.confirm_readiness(health) is False
    assert len(probes) == 1
    assert managed.service.state["updates"]["pendingReplacement"] == marker
    assert saved_updates(managed.service)["pendingReplacement"] == marker
    assert work_paused(managed.service.state) and successor.awaiting_restart()
    managed.restart.assert_not_awaited()


async def test_cancelled_recovery_probe_retains_marker_and_releases_update_lock(managed, monkeypatch):
    successor, health, _ = await recovering_host(managed, monkeypatch)
    marker = copy.deepcopy(managed.service.state["updates"]["pendingReplacement"])
    entered = asyncio.Event()

    async def blocked_probe(*args, **kwargs):
        assert args[1:3] == ("-I", "-c") and args[3] == app_updates.PROBE
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(app_updates, "process", blocked_probe)
    recovery = asyncio.create_task(successor.confirm_readiness(health))
    await asyncio.wait_for(entered.wait(), 2)
    recovery.cancel()
    with pytest.raises(asyncio.CancelledError):
        await recovery
    assert managed.service.state["updates"]["pendingReplacement"] == marker
    assert saved_updates(managed.service)["pendingReplacement"] == marker
    assert not successor.lock.locked()
    assert work_paused(managed.service.state) and successor.awaiting_restart()
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize("change", ["receipt", "nested-request", "metadata"])
async def test_awaited_recovery_probe_cannot_clear_changed_receipt_or_environment(managed, monkeypatch, change):
    successor, health, dependencies = await recovering_host(managed, monkeypatch)
    state = managed.service.state["updates"]
    probes = []

    async def racing_probe(*args, **kwargs):
        probes.append(args)
        await asyncio.sleep(0)
        async with managed.service.lock:
            if change == "receipt":
                state["pendingReplacement"] = {**copy.deepcopy(state["pendingReplacement"]), "attemptId": "d" * 32}
            elif change == "nested-request":
                state["pendingReplacement"]["featureSelection"]["requestId"] = "different-request"
                state["featureResults"]["different-request"] = {"phase": "queued"}
            else:
                monkeypatch.setattr(app_updates.components, "installed_graph", lambda: dependencies + [{"name": "racing-dependency", "version": "1.0"}])
            managed.service._publish()
        return successful_probe()

    monkeypatch.setattr(app_updates, "process", racing_probe)
    assert await successor.confirm_readiness(health) is False
    assert len(probes) == 1
    assert state.get("pendingReplacement")
    assert saved_updates(managed.service)["pendingReplacement"] == state["pendingReplacement"]
    assert work_paused(managed.service.state) and successor.awaiting_restart()
    if change == "receipt":
        assert state["pendingReplacement"]["attemptId"] == "d" * 32
    elif change == "nested-request":
        assert state["pendingReplacement"]["featureSelection"]["requestId"] == "different-request"
        assert state["featureResults"]["different-request"]["phase"] == "queued"
    managed.restart.assert_not_awaited()


async def test_nonstring_qualified_app_url_is_held_without_probe_or_startup_crash(managed, monkeypatch):
    from amplifier_web.app_replacement import qualified
    successor, health, _ = await recovering_host(managed, monkeypatch)
    marker = managed.service.state["updates"]["pendingReplacement"]
    marker["qualification"]["app"]["url"] = 123
    assert qualified(marker) is False
    successor = UpdateManager(managed.service)
    managed.service.update_manager = successor
    assert recovery_candidate(successor) is None
    assert await successor.confirm_readiness(health) is False
    assert managed.service.state["updates"]["pendingReplacement"] == marker
    assert work_paused(managed.service.state) and successor.awaiting_restart()
