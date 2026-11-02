"""Optional features preserve the exact serving application and its dependencies.

Installers, processes and package metadata are synthetic; no host is changed.
"""
from amplifier_web.state_records import load as load_saved_state
import asyncio
import builtins
import copy
from importlib import metadata
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web import app_features, app_updates
from amplifier_web.app_feature_probe import DEPENDENCY_PROBE
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager, work_paused


REVISION = "a" * 40
HOST_APP = {"name": "amplifier-unified", "version": app_updates.__version__,
            "url": app_updates.SOURCE, "revision": REVISION}
BASELINE = [
    {"name": "amplifier-app-tui", "version": "1.0.0", "url": "https://github.com/microsoft/amplifier-app-tui", "revision": "b" * 40},
    {"name": "amplifier-core", "version": "1.6.1"},
    {"name": "httpx", "version": "0.28.1"},
]
ADDITION = {"name": "amplifier-module-tool-computer-use", "version": "0.1.0",
            "url": "https://github.com/microsoft/amplifier-bundle-computer-use", "revision": "c" * 40,
            "subdirectory": "modules/tool-computer-use"}
CANDIDATE = sorted([*BASELINE, HOST_APP, ADDITION], key=lambda row: row["name"])


@pytest.fixture
def helper_manager(monkeypatch):
    service = SimpleNamespace(lock=asyncio.Lock(), state={"updates": {}}, _publish=Mock())
    service._publish_changes = service._publish
    manager = SimpleNamespace(service=service, lock=asyncio.Lock(), awaiting_restart=Mock(return_value=False),
                              running_identity={"revision": REVISION})
    monkeypatch.setattr(app_updates, "installed_extras", lambda: ["tui"])
    return manager


@pytest.fixture
def packaged_app(monkeypatch):
    direct = {"url": app_updates.SOURCE, "vcs_info": {"vcs": "git", "commit_id": REVISION}}
    distribution = SimpleNamespace(
        version=app_updates.__version__,
        locate_file=Mock(return_value=Path(app_features.__file__).parent),
        read_text=Mock(side_effect=lambda name: json.dumps(direct)),
    )
    monkeypatch.setattr(app_features.metadata, "distribution", lambda name: distribution)
    return distribution, direct


def test_running_application_attests_exact_packaged_microsoft_source(helper_manager, packaged_app):
    assert app_features.running_application(helper_manager) == HOST_APP
    packaged_app[0].locate_file.assert_called_once_with("amplifier_web")
    packaged_app[0].read_text.assert_called_once_with("direct_url.json")


def test_git_suffix_is_canonicalized_across_original_and_successor_app(helper_manager, packaged_app, monkeypatch):
    packaged_app[1]["url"] = app_updates.SOURCE + ".git"
    selected = app_features.selection(helper_manager, "native-desktop", "request-one")
    assert selected["hostApp"] == HOST_APP
    packaged_app[1]["url"] = app_updates.SOURCE
    monkeypatch.setattr(app_updates, "installed_extras", lambda: ["native-desktop", "tui"])
    assert app_features.validate_selection(helper_manager, selected, ["native-desktop", "tui"], installing=False) == ["native-desktop", "tui"]


def test_app_subdirectory_install_cannot_be_replaced_with_repository_root(helper_manager, packaged_app):
    packaged_app[1]["subdirectory"] = "packages/custom-app"
    with pytest.raises(ValueError, match="verified Microsoft source provenance"):
        app_features.running_application(helper_manager)
    assert app_features.status(helper_manager)["supported"] is False


@pytest.mark.parametrize("mismatch", ["checkout", "repository", "revision", "version", "unversioned", "local", "malformed"])
def test_feature_provenance_rejects_unqualified_app(helper_manager, packaged_app, mismatch):
    distribution, direct = packaged_app
    if mismatch == "checkout":
        distribution.locate_file.return_value = Path("/other/development/checkout/amplifier_web")
    elif mismatch == "repository":
        direct["url"] = "https://github.com/other/amplifier-unified"
    elif mismatch == "revision":
        direct["vcs_info"]["commit_id"] = "d" * 40
    elif mismatch == "version":
        distribution.version = "99.0.0"
    elif mismatch == "unversioned":
        direct.clear()
    elif mismatch == "local":
        direct["url"] = "file:///development/amplifier-unified"
    else:
        distribution.read_text.side_effect = None
        distribution.read_text.return_value = "{malformed"
    with pytest.raises(ValueError):
        app_features.running_application(helper_manager)
    assert app_features.status(helper_manager)["supported"] is False


def test_feature_status_without_manager_is_unsupported():
    assert app_features.status(None)["supported"] is False


async def test_status_reports_pending_guard_without_installing(helper_manager, packaged_app):
    status = app_features.status(helper_manager)
    assert status["supported"] and status["pending"] is False
    assert status["appRevision"] == REVISION
    assert status["installedExtras"] == ["tui"]
    assert status["action"] == "updates.featureInstall"
    await helper_manager.lock.acquire()
    assert app_features.status(helper_manager)["pending"] is True
    helper_manager.lock.release()
    for field in ("pendingApp", "pendingRelease"):
        helper_manager.service.state["updates"][field] = {"id": "pending"}
        assert app_features.status(helper_manager)["pending"] is True
        helper_manager.service.state["updates"].pop(field)
    helper_manager.awaiting_restart.return_value = True
    assert app_features.status(helper_manager)["pending"] is True
    helper_manager.service._publish.assert_not_called()


def test_selection_only_adds_native_desktop_and_keeps_existing_extras(helper_manager, packaged_app):
    selected = app_features.selection(helper_manager, "native-desktop", "request-one")
    assert selected == {"kind": "add-feature", "feature": "native-desktop", "requestId": "request-one",
                        "hostApp": HOST_APP, "baselineExtras": ["tui"], "extras": ["native-desktop", "tui"]}
    assert app_features.validate_selection(helper_manager, selected, selected["extras"]) == selected["extras"]


@pytest.mark.parametrize("feature", ["tui", "desktop-control", "", None])
def test_unrequested_features_cannot_be_installed(helper_manager, packaged_app, feature):
    with pytest.raises(ValueError, match="native-desktop"):
        app_features.selection(helper_manager, feature, "request-one")


@pytest.mark.parametrize("change", ["drop-extra", "extra-different", "request-id", "kind", "feature", "host-app", "baseline"])
def test_selection_receipt_rejects_tampering_and_changed_host(helper_manager, packaged_app, change):
    selected = app_features.selection(helper_manager, "native-desktop", "request-one")
    extras = ["native-desktop", "tui"]
    if change == "drop-extra":
        selected["extras"] = ["native-desktop"]
    elif change == "extra-different":
        extras = ["native-desktop"]
    elif change == "request-id":
        selected["requestId"] = ""
    elif change == "kind":
        selected["kind"] = "replace-features"
    elif change == "feature":
        selected["feature"] = "tui"
    elif change == "host-app":
        selected["hostApp"]["revision"] = "d" * 40
    else:
        selected["baselineExtras"] = []
        selected["extras"] = extras = ["native-desktop"]
    with pytest.raises(ValueError):
        app_features.validate_selection(helper_manager, selected, extras)


def test_already_installed_native_extra_is_not_removed_or_duplicated(helper_manager, packaged_app, monkeypatch):
    monkeypatch.setattr(app_updates, "installed_extras", lambda: ["native-desktop", "tui"])
    selected = app_features.selection(helper_manager, "native-desktop", "request-one")
    assert selected["baselineExtras"] == selected["extras"] == ["native-desktop", "tui"]


@pytest.mark.parametrize("mismatch", ["app-source", "receipt-host-app"])
def test_successor_validation_still_requires_exact_packaged_app_provenance(helper_manager, packaged_app, monkeypatch, mismatch):
    selected = app_features.selection(helper_manager, "native-desktop", "request-one")
    monkeypatch.setattr(app_updates, "installed_extras", lambda: ["native-desktop", "tui"])
    # The successor has the additional extra, so only the old extras baseline
    # comparison is skipped. Its application identity must still be attested.
    assert app_features.validate_selection(helper_manager, selected, ["native-desktop", "tui"], installing=False) == ["native-desktop", "tui"]
    if mismatch == "app-source":
        packaged_app[1]["url"] = "https://github.com/other/amplifier-unified"
    else:
        selected["hostApp"]["revision"] = "d" * 40
    with pytest.raises(ValueError):
        app_features.validate_selection(helper_manager, selected, ["native-desktop", "tui"], installing=False)


def test_component_preservation_allows_additions_with_every_baseline_unchanged():
    before = copy.deepcopy(BASELINE)
    app_features.require_preserved_components(CANDIDATE, BASELINE)
    assert BASELINE == before


@pytest.mark.parametrize("change", ["missing", "version", "source", "revision"])
def test_component_preservation_rejects_even_transitive_or_same_version_source_changes(change):
    candidate = copy.deepcopy(CANDIDATE)
    row = next(row for row in candidate if row["name"] == ("httpx" if change in {"missing", "version"} else "amplifier-app-tui"))
    if change == "missing":
        candidate.remove(row)
    elif change == "version":
        row["version"] = "0.29.0"
    elif change == "source":
        row["url"] = "https://github.com/other/amplifier-app-tui"
    else:
        row["revision"] = "d" * 40
    with pytest.raises(ValueError, match="preserve every installed component"):
        app_features.require_preserved_components(candidate, BASELINE)


async def test_feature_result_updates_are_copied_published_and_bounded(helper_manager):
    details = {"nested": ["evidence"]}
    await app_features.record(helper_manager, "request-one", "queued", receipt=details)
    details["nested"].append("later mutation")
    await app_features.record(helper_manager, "request-one", "staging")
    row = helper_manager.service.state["updates"]["featureResults"]["request-one"]
    assert row["phase"] == "staging" and row["receipt"] == {"nested": ["evidence"]}
    assert row["feature"] == "native-desktop" and row["updatedAt"] > 0
    for index in range(25):
        await app_features.record(helper_manager, f"request-{index}", "error")
    assert len(helper_manager.service.state["updates"]["featureResults"]) == 20
    assert helper_manager.service._publish.call_count == 27


@pytest.mark.parametrize("pending", ["pendingApp", "pendingRestart"])
def test_startup_reconciliation_retains_only_exact_pending_request_without_replaying(pending):
    phases = ["queued", "staging", "qualified", "activating", "restart_pending", "installed", "error"]
    state = {"featureResults": {phase: {"phase": phase} for phase in phases},
             pending: {"featureSelection": {"requestId": "qualified"}}}
    app_features.reconcile_requests(state)
    assert state["featureResults"]["qualified"]["phase"] == "qualified"
    assert state["featureResults"]["installed"]["phase"] == "installed"
    assert state["featureResults"]["error"]["phase"] == "error"
    for phase in set(phases) - {"qualified", "installed", "error"}:
        assert state["featureResults"][phase]["phase"] == "interrupted"
        assert "No installation or restart was replayed" in state["featureResults"][phase]["detail"]


def test_startup_without_pending_receipt_never_infers_installation_success():
    state = {"featureResults": {"request-one": {"phase": "restart_pending"}}}
    app_features.reconcile_requests(state)
    assert state["featureResults"]["request-one"]["phase"] == "interrupted"


@pytest.fixture
async def managed(tmp_path, monkeypatch):
    from test_service import Runtime
    service = AppService(tmp_path / "app", Runtime(), workspace=tmp_path)
    await service.dispatch("session.create", {})
    service.port = 8941
    manager = UpdateManager(service)
    service.update_manager = manager
    manager.running_identity = {"version": app_updates.__version__, "revision": REVISION, "instanceId": "old-process"}
    installation = SimpleNamespace(manager=manager, service=service, calls=[], probe_version=app_updates.__version__)
    monkeypatch.setattr(app_features, "running_application", lambda manager: copy.deepcopy(HOST_APP))
    monkeypatch.setattr(app_updates, "installed_extras", lambda: ["tui"])
    monkeypatch.setattr(app_updates.components, "installed_graph", lambda: copy.deepcopy(BASELINE))
    monkeypatch.setattr(app_updates.components, "read_graph", AsyncMock(return_value=copy.deepcopy(CANDIDATE)))
    monkeypatch.setattr(app_updates.components, "updates", AsyncMock(side_effect=AssertionError("feature checked newer components")))
    monkeypatch.setattr(app_updates, "check", AsyncMock(side_effect=AssertionError("feature checked newer app releases")))
    monkeypatch.setattr(app_updates.shutil, "which", lambda name: "/fixture/" + name)
    monkeypatch.setattr(app_updates, "git_environment", lambda: {"GIT_TERMINAL_PROMPT": "0"})
    installation.previous = {"version": app_updates.__version__, "source": "git+" + app_updates.SOURCE + "@" + REVISION,
                             "installation": str(tmp_path / "installed")}
    monkeypatch.setattr(app_updates, "installed_target", AsyncMock(return_value=(
        "/fixture/uv", "/fixture/launcher", tmp_path / "installed/python", installation.previous)))
    installation.restart = AsyncMock()
    monkeypatch.setattr(app_updates, "request_managed_restart", installation.restart)
    monkeypatch.setattr("amplifier_web.deployment_service.current_process_is_unit_managed", lambda home: True)
    monkeypatch.setattr(app_updates.os, "kill", lambda *args: pytest.fail("synthetic updater must never kill the host"))

    async def process(*args, **kwargs):
        installation.calls.append((tuple(map(str, args)), kwargs))
        if "tool" in args and "install" in args:
            tool_root = kwargs.get("env", {}).get("UV_TOOL_DIR")
            if tool_root:
                python = Path(tool_root) / "amplifier-unified/bin/python"
                python.parent.mkdir(parents=True, exist_ok=True)
                python.touch()
            return ""
        if "-c" in args and app_updates.PROBE in args:
            return installation.probe_version
        if "-c" in args and DEPENDENCY_PROBE in args:
            return "Feature dependency metadata is compatible."
        raise AssertionError("unexpected updater process")

    monkeypatch.setattr(app_updates, "process", process)
    yield installation
    await service.close()


async def stage_feature(installation, request_id="request-one"):
    await app_updates.stage(installation.manager, feature="native-desktop", request_id=request_id)
    pending = installation.service.state["updates"]["pendingApp"]
    folder = installation.manager.directory / "applications" / pending["revision"] / pending["generation"]
    return folder, json.loads((folder / "validated.json").read_text())


async def test_feature_staging_uses_current_revision_and_pins_complete_host_graph(managed):
    service, manager = managed.service, managed.manager
    newer = {"status": "update", "revision": "d" * 40, "latest": "99.0.0", "componentUpdates": [{"name": "amplifier-core"}]}
    service.state["updates"]["application"] = copy.deepcopy(newer)
    folder, receipt = await stage_feature(managed)
    install, options = managed.calls[0]
    assert install[-1] == app_updates.install_requirement(REVISION, ["native-desktop", "tui"])
    assert "--upgrade" not in install
    assert install[install.index("--overrides") + 1] == str(folder / "host-components.txt")
    assert install[install.index("--with-requirements") + 1] == str(folder / "host-components.txt")
    assert (folder / "host-components.txt").read_text() == app_updates.components.requirements(BASELINE)
    assert options["env"]["UV_TOOL_DIR"] == str(folder / "tools")
    assert receipt["revision"] == REVISION and receipt["version"] == app_updates.__version__
    assert receipt["hostGraph"] == BASELINE and receipt["componentGraph"] == CANDIDATE
    assert receipt["extras"] == ["native-desktop", "tui"]
    assert receipt["featureSelection"]["hostApp"] == HOST_APP
    dependency_probe = managed.calls[2][0]
    assert dependency_probe == (str(folder / "tools/amplifier-unified/bin/python"), "-I", "-c", DEPENDENCY_PROBE, "native-desktop", "tui")
    assert service.state["updates"]["application"] == newer
    assert service.state["updates"]["featureResults"]["request-one"]["phase"] == "qualified"
    assert not (manager.directory / "previous-app.json").exists()
    managed.restart.assert_not_awaited()
    app_updates.check.assert_not_awaited()
    app_updates.components.updates.assert_not_awaited()


async def test_normal_staging_keeps_published_release_selection_and_existing_extras(managed, monkeypatch):
    release = {"status": "update", "revision": "d" * 40, "latest": "99.0.0"}
    managed.service.state["updates"]["application"] = release
    managed.probe_version = "99.0.0"
    graph = sorted([*BASELINE, {**HOST_APP, "version": "99.0.0", "revision": "d" * 40}], key=lambda row: row["name"])
    app_updates.components.read_graph.return_value = graph
    await app_updates.stage(managed.manager)
    install = managed.calls[0][0]
    assert "--upgrade" in install
    assert install[-1] == app_updates.install_requirement("d" * 40, ["tui"])
    assert "--with-requirements" not in install
    pending = managed.service.state["updates"]["pendingApp"]
    assert pending["revision"] == "d" * 40 and "featureSelection" not in pending
    assert not managed.service.state["updates"].get("featureResults")

@pytest.mark.parametrize("recover_feature", [False, True])
async def test_attention_feature_failure_requires_same_revision_feature_recovery(managed, monkeypatch, recover_feature):
    from amplifier_web.attention import snapshot
    from amplifier_web.auth import data_identity
    manager, service = managed.manager, managed.service
    original_process = app_updates.process
    async def reject_install(*args, **kwargs):
        if "tool" in args and "install" in args:
            raise RuntimeError("synthetic unavailable feature")
        return await original_process(*args, **kwargs)
    monkeypatch.setattr(app_updates, "process", reject_install)
    with pytest.raises(RuntimeError):
        await app_updates.stage(manager, feature="native-desktop", request_id="failed-feature")
    failure = next(item for item in snapshot(service.state)["items"] if item.get("updateKind") == "application")
    await service.dispatch("attention.read", {"ids": [failure["id"]],
        "fingerprints": {failure["id"]: failure["fingerprint"]}})
    monkeypatch.setattr(app_updates, "process", original_process)
    if recover_feature:
        await stage_feature(managed, "matching-feature")
        graph, extras = CANDIDATE, ["native-desktop", "tui"]
    else:
        graph = sorted([*BASELINE, HOST_APP], key=lambda row: row["name"])
        extras = ["tui"]
        app_updates.components.read_graph.return_value = copy.deepcopy(graph)
        service.state["updates"]["application"] = {
            "status": "update", "revision": REVISION, "latest": app_updates.__version__}
        await app_updates.stage(manager)
    await app_updates.activate(manager)
    # A replacement host reconstructs diagnostics from saved values, not the
    # staging manager's in-memory feature selection.
    service.state["updates"] = json.loads(json.dumps(service.state["updates"]))
    service.update_manager = manager = UpdateManager(service)
    manager.running_identity = {"version": app_updates.__version__, "revision": REVISION,
                                "instanceId": "new-process"}
    monkeypatch.setattr(app_updates, "installed_extras", lambda: extras)
    monkeypatch.setattr(app_updates.components, "installed_graph",
                        lambda: [row for row in graph if row["name"] != "amplifier-unified"])
    health = {"ok": True, "app": "amplifier-unified", "dataIdentity": data_identity(manager.home),
              **manager.running_identity}
    assert await manager.confirm_readiness(health)
    remaining = [item for item in snapshot(service.state)["items"] if item.get("updateKind") == "application"]
    assert len(remaining) == (0 if recover_feature else 1)
    if remaining:
        assert remaining[0]["fingerprint"] == failure["fingerprint"] and remaining[0]["read"]


@pytest.mark.parametrize("change", ["drop-transitive", "upgrade-transitive", "new-app-revision"])
async def test_feature_candidate_rejects_dependency_loss_or_opportunistic_updates(managed, change):
    graph = copy.deepcopy(CANDIDATE)
    row = next(row for row in graph if row["name"] == ("amplifier-unified" if change == "new-app-revision" else "httpx"))
    if change == "drop-transitive":
        graph.remove(row)
    elif change == "upgrade-transitive":
        row["version"] = "0.29.0"
    else:
        row["revision"] = "d" * 40
    app_updates.components.read_graph.return_value = graph
    with pytest.raises(ValueError):
        await stage_feature(managed)
    assert not managed.service.state["updates"].get("pendingApp")
    assert len(managed.calls) == 2
    assert not list(managed.manager.directory.glob("applications/*/*/validated.json"))
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize("change", ["host-graph", "candidate-graph", "extras", "app-revision", "selection", "resolution-file"])
async def test_activation_revalidates_exact_qualified_candidate_and_host_before_install(managed, monkeypatch, change):
    folder, receipt = await stage_feature(managed)
    managed.calls.clear()
    if change == "host-graph":
        monkeypatch.setattr(app_updates.components, "installed_graph", lambda: [*BASELINE, {"name": "new-package", "version": "1"}])
    elif change == "candidate-graph":
        app_updates.components.read_graph.return_value = [row for row in CANDIDATE if row["name"] != "httpx"]
    elif change == "extras":
        monkeypatch.setattr(app_updates, "installed_extras", lambda: [])
    elif change == "app-revision":
        monkeypatch.setattr(app_features, "running_application", lambda manager: {**HOST_APP, "revision": "d" * 40})
    elif change == "selection":
        receipt["featureSelection"]["requestId"] = "another-request"
        (folder / "validated.json").write_text(json.dumps(receipt))
    else:
        (folder / "components.txt").write_text("httpx==0.29.0\n")
    with pytest.raises(ValueError):
        await app_updates.activate(managed.manager)
    assert managed.calls == []
    assert not (managed.manager.directory / "previous-app.json").exists()
    assert managed.service.state["updates"]["featureResults"]["request-one"]["phase"] == "error"
    managed.restart.assert_not_awaited()


async def test_feature_activation_preserves_recovery_record_and_awaits_exact_restart(managed):
    await managed.manager.featureInstall("native-desktop", managed.service.instance_id, "request-one")
    state = managed.service.state["updates"]
    assert state["featureResults"]["request-one"]["phase"] == "restart_pending"
    assert state["pendingRestart"]["featureSelection"]["extras"] == ["native-desktop", "tui"]
    assert state["pendingRestart"]["revision"] == REVISION
    assert state["pendingRestart"]["dependencyDigest"] == app_updates.components.digest([row for row in CANDIDATE if row["name"] != "amplifier-unified"])
    assert work_paused(managed.service.state)
    assert json.loads((managed.manager.directory / "previous-app.json").read_text()) == managed.previous
    replacement = managed.calls[3][0]
    assert replacement[-1] == app_updates.install_requirement(REVISION, ["native-desktop", "tui"])
    assert "--with-requirements" in replacement and "--overrides" in replacement
    assert "--upgrade" not in replacement
    assert managed.calls[4][0][-2:] == ("native-desktop", "tui")
    managed.restart.assert_awaited_once_with(managed.manager)


@pytest.mark.parametrize("guard", ["working", "voice", "worker-control", "smart-tool", "queued-feedback"])
async def test_feature_activation_retains_existing_busy_work_guards(managed, guard):
    service = managed.service
    if guard == "working":
        service._session()["status"] = "working"
    elif guard == "voice":
        service.state["voice"]["status"] = "connected"
    elif guard == "worker-control":
        service.runtime.has_pending_operations = lambda: True
    elif guard == "smart-tool":
        service.state["smartTools"] = {"operations": [{"status": "queued"}]}
    else:
        service.state["feedback"] = {"requests": [{"status": "queued"}]}
    await managed.manager.featureInstall("native-desktop", service.instance_id, "request-one")
    assert service.state["updates"]["featureResults"]["request-one"]["phase"] == "qualified"
    assert service.state["updates"]["pendingApp"]["featureSelection"]["requestId"] == "request-one"
    assert len(managed.calls) == 3
    assert not work_paused(service.state)
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize("guard", ["host", "feature", "lock", "pendingApp", "pendingRelease", "restart", "closed"])
async def test_feature_admission_rejects_wrong_host_or_pending_update_before_staging(managed, guard):
    manager, service = managed.manager, managed.service
    feature, host = "native-desktop", service.instance_id
    if guard == "host":
        host = "old-host-instance"
    elif guard == "feature":
        feature = "tui"
    elif guard == "lock":
        await manager.lock.acquire()
    elif guard == "closed":
        manager.closed = True
    elif guard == "restart":
        service.state["updates"]["pendingRestart"] = {"revision": REVISION}
    else:
        service.state["updates"][guard] = {"revision": REVISION}
    try:
        with pytest.raises(ValueError):
            await manager.featureInstall(feature, host, "request-one")
        assert managed.calls == []
        assert service.state["updates"]["featureResults"]["request-one"]["phase"] == "error"
        managed.restart.assert_not_awaited()
    finally:
        if guard == "lock":
            manager.lock.release()


async def test_already_installed_feature_does_not_stage_replace_or_restart(managed, monkeypatch):
    monkeypatch.setattr(app_updates, "installed_extras", lambda: ["native-desktop", "tui"])
    await managed.manager.featureInstall("native-desktop", managed.service.instance_id, "request-one")
    assert managed.service.state["updates"]["featureResults"]["request-one"]["phase"] == "already_installed"
    assert managed.calls == []
    managed.restart.assert_not_awaited()


async def test_failed_replacement_probe_retains_host_and_records_feature_error(managed, monkeypatch):
    await stage_feature(managed)
    managed.probe_version = "99.0.0"
    await app_updates.activate(managed.manager)
    assert managed.service.state["updates"]["featureResults"]["request-one"]["phase"] == "error"
    assert not managed.service.state["updates"].get("pendingRestart")
    assert managed.service.state["updates"].get("pendingReplacement")
    assert work_paused(managed.service.state)
    assert managed.manager.awaiting_restart()
    managed.restart.assert_not_awaited()


async def test_shutdown_lifecycle_guard_blocks_feature_replacement(managed):
    await stage_feature(managed)
    managed.calls.clear()
    await managed.service.runtime_lifecycle_lock.acquire()
    activation = asyncio.create_task(app_updates.activate(managed.manager))
    await asyncio.sleep(0)
    managed.service.closed = True
    managed.service.runtime_lifecycle_lock.release()
    try:
        with pytest.raises(RuntimeError, match="closing"):
            await activation
        assert managed.calls == []
        managed.restart.assert_not_awaited()
    finally:
        managed.service.closed = False


@pytest.mark.parametrize("mismatch", [None, "missing-extra", "changed-graph", "same-process", "changed-app-source", "changed-host-app"])
async def test_restart_acknowledges_feature_only_after_exact_health_extras_and_graph(managed, monkeypatch, mismatch):
    from amplifier_web.auth import data_identity
    await managed.manager.featureInstall("native-desktop", managed.service.instance_id, "request-one")
    manager = managed.manager
    manager.running_identity = {**manager.running_identity, "instanceId": "old-process" if mismatch == "same-process" else "new-process"}
    monkeypatch.setattr(app_updates, "installed_extras", lambda: ["tui"] if mismatch == "missing-extra" else ["native-desktop", "tui"])
    graph = [row for row in CANDIDATE if row["name"] != "amplifier-unified"]
    monkeypatch.setattr(app_updates.components, "installed_graph", lambda: BASELINE if mismatch == "changed-graph" else graph)
    if mismatch == "changed-app-source":
        monkeypatch.setattr(app_features, "running_application", lambda manager: {**HOST_APP, "url": "https://github.com/other/amplifier-unified"})
    elif mismatch == "changed-host-app":
        managed.service.state["updates"]["pendingRestart"]["featureSelection"]["hostApp"]["revision"] = "d" * 40
    health = {"ok": True, "app": "amplifier-unified", "dataIdentity": data_identity(manager.home), **manager.running_identity}
    result = await manager.confirm_readiness(health)
    state = managed.service.state["updates"]
    assert result is (mismatch is None)
    assert state["featureResults"]["request-one"]["phase"] == ("restart_pending" if mismatch else "installed")
    assert bool(state.get("pendingRestart")) is bool(mismatch)


@pytest.mark.parametrize("active", ["staging", "pendingApp", "pendingRelease", "pendingRestart"])
async def test_rejected_concurrent_feature_keeps_active_update_phase_and_receipt(managed, active):
    manager, service = managed.manager, managed.service
    state = service.state["updates"]
    state["phase"] = "staging" if active == "staging" else "activating" if active == "pendingRestart" else "app-staged"
    if active == "staging":
        await manager.lock.acquire()
    else:
        state[active] = {"revision": "d" * 40, "attemptId": "e" * 32, "version": "99.0.0"}
    original = copy.deepcopy(state)
    try:
        await manager.command("featureInstall", {"feature": "native-desktop", "hostInstanceId": service.instance_id}, "rejected-request")
        assert state["phase"] == original["phase"]
        if active != "staging":
            assert state[active] == original[active]
        assert state["featureResults"]["rejected-request"]["phase"] == "error"
        assert managed.calls == []
    finally:
        if active == "staging":
            manager.lock.release()


@pytest.mark.parametrize("caller", ["ui", "agent"])
@pytest.mark.parametrize("feature", ["native-desktop", "managed-browser", "decisions-openai", "decisions-azure", "decisions-jev", "decisions-local"])
async def test_shared_action_persists_queued_admission_and_exact_retry_cannot_replay(managed, monkeypatch, caller, feature):
    service = managed.service
    scheduled = []

    def hold(coroutine):
        # Simulate shutdown after command admission but before background work.
        coroutine.close()
        saved = load_saved_state(service.db)
        scheduled.append(saved["updates"]["featureResults"]["queued-request"])
        return None

    monkeypatch.setattr(service, "_task", hold)
    args = {"feature": feature, "hostInstanceId": service.instance_id}

    async def request():
        if caller == "agent":
            return await service.app_bridge("dispatch", {"action": "updates.featureInstall", "args": args, "id": "queued-request"}, service._session()["id"])
        return await service.dispatch("updates.featureInstall", args, command_id="queued-request", include_state=False)

    first = await request()
    second = await request()
    assert first["requestId"] == second["requestId"] == "queued-request"
    assert first["accepted"] and second["accepted"]
    assert len(scheduled) == 1 and scheduled[0]["phase"] == "queued"
    assert managed.calls == []
    await service.close()
    reopened = AppService(service.data_dir, workspace=service.default_workspace)
    try:
        reopened.update_manager = UpdateManager(reopened)
        result = reopened.state["updates"]["featureResults"]["queued-request"]
        assert result["phase"] == "interrupted"
        assert "No installation or restart was replayed" in result["detail"]
        saved = load_saved_state(reopened.db)
        assert saved["updates"]["featureResults"]["queued-request"]["phase"] == "interrupted"
        assert managed.calls == []
    finally:
        await reopened.close()


async def test_cancelled_feature_qualification_records_interruption_without_restart(managed, monkeypatch):
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(app_updates, "process", blocked)
    task = asyncio.create_task(managed.manager.featureInstall("native-desktop", managed.service.instance_id, "request-one"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert managed.service.state["updates"]["featureResults"]["request-one"]["phase"] == "interrupted"
    assert not managed.service.state["updates"].get("pendingRestart")
    assert not list(managed.manager.directory.glob("applications/*/*/validated.json"))
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize("scenario", ["compatible", "version-conflict", "missing-dependency", "inactive-condition", "inactive-extra", "transitive-extra-conflict"])
def test_real_dependency_metadata_probe_checks_constraints_and_propagated_extras_without_package_imports(monkeypatch, capsys, scenario):
    rows = {
        "amplifier-unified": SimpleNamespace(metadata={"Name": "amplifier-unified"}, version="1.0.0", requires=["native_fixture[vision]>=1; extra == 'native-desktop'"]),
        "native_fixture": SimpleNamespace(metadata={"Name": "native_fixture"}, version="1.2.0", requires=["transitive_fixture[codec]>=1; extra == 'vision'"]),
        "transitive_fixture": SimpleNamespace(metadata={"Name": "transitive_fixture"}, version="1.1.0", requires=["codec_fixture>=1,<2; extra == 'codec'"]),
        "codec_fixture": SimpleNamespace(metadata={"Name": "codec_fixture"}, version="1.5.0", requires=None),
    }
    extras = ["native-desktop"]
    if scenario == "version-conflict":
        rows["native_fixture"].version = "0.9.0"
    elif scenario == "missing-dependency":
        rows.pop("codec_fixture")
    elif scenario == "inactive-condition":
        rows["amplifier-unified"].requires.append("absent_fixture>=1; python_version < '1.0'")
    elif scenario == "inactive-extra":
        extras = []
        rows["codec_fixture"].version = "99.0.0"
    elif scenario == "transitive-extra-conflict":
        rows["codec_fixture"].version = "2.0.0"
    monkeypatch.setattr(metadata, "distributions", lambda: list(rows.values()))
    monkeypatch.setattr(sys, "argv", ["-c", *extras])
    imports = []
    original_import = builtins.__import__

    def no_package_import(name, *args, **kwargs):
        imports.append(name)
        assert name not in {"amplifier_web", "native_fixture", "transitive_fixture", "codec_fixture"}
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_package_import)
    if scenario in {"version-conflict", "missing-dependency", "transitive-extra-conflict"}:
        with pytest.raises(ValueError, match="conflicts with the preserved dependency environment"):
            exec(compile(DEPENDENCY_PROBE, "<feature-dependency-probe>", "exec"), {})
        assert "compatible" not in capsys.readouterr().out
    else:
        exec(compile(DEPENDENCY_PROBE, "<feature-dependency-probe>", "exec"), {})
        assert capsys.readouterr().out == "Feature dependency metadata is compatible.\n"
    assert "importlib.metadata" in imports


async def test_feature_qualification_rejects_dependency_probe_conflict_before_validated_marker(managed, monkeypatch):
    original = app_updates.process
    dependency_calls = []

    async def conflict(*args, **kwargs):
        if DEPENDENCY_PROBE in args:
            dependency_calls.append(args)
            assert not list(managed.manager.directory.glob("applications/*/*/validated.json"))
            raise RuntimeError("synthetic incompatible dependency metadata")
        return await original(*args, **kwargs)

    monkeypatch.setattr(app_updates, "process", conflict)
    await managed.manager.command("featureInstall", {"feature": "native-desktop", "hostInstanceId": managed.service.instance_id}, "request-one")
    assert len(dependency_calls) == 1
    assert app_updates.components.installed_graph() == BASELINE
    assert app_updates.components.read_graph.return_value == CANDIDATE
    assert not list(managed.manager.directory.glob("applications/*/*/validated.json"))
    assert not managed.service.state["updates"].get("pendingApp")
    assert managed.service.state["updates"]["featureResults"]["request-one"]["phase"] == "error"
    assert managed.manager.diagnostics.state["lastFailure"]["phase"] == "candidate-dependencies"
    assert not (managed.manager.directory / "previous-app.json").exists()
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize("change", ["components", "extras", "app"])
async def test_feature_activation_rechecks_host_after_awaited_candidate_graph(managed, monkeypatch, change):
    await stage_feature(managed)
    managed.calls.clear()

    async def candidate_graph(*args):
        await asyncio.sleep(0)
        if change == "components":
            monkeypatch.setattr(app_updates.components, "installed_graph", lambda: [*BASELINE, {"name": "racing-dependency", "version": "1.0"}])
        elif change == "extras":
            monkeypatch.setattr(app_updates, "installed_extras", lambda: [])
        else:
            monkeypatch.setattr(app_features, "running_application", lambda manager: {**HOST_APP, "revision": "d" * 40})
        return copy.deepcopy(CANDIDATE)

    monkeypatch.setattr(app_updates.components, "read_graph", candidate_graph)
    with pytest.raises(ValueError, match="changed"):
        await app_updates.activate(managed.manager)
    assert managed.calls == []
    assert not (managed.manager.directory / "previous-app.json").exists()
    assert managed.service.state["updates"]["featureResults"]["request-one"]["phase"] == "error"
    managed.restart.assert_not_awaited()


def restart_target():
    return {"version": HOST_APP["version"], "revision": REVISION, "attemptId": "e" * 32,
            "sourceInstanceId": "old-process", "dependencyDigest": "f" * 64,
            "featureSelection": {"kind": "add-feature", "feature": "native-desktop", "requestId": "request-one",
                                 "hostApp": copy.deepcopy(HOST_APP), "baselineExtras": ["tui"], "extras": ["native-desktop", "tui"]}}


def test_restart_marker_accepts_valid_feature_and_ordinary_update_shapes():
    from amplifier_web.update_readiness import valid_target
    feature = restart_target()
    assert valid_target(feature)
    ordinary = {key: feature[key] for key in ("version", "revision", "attemptId", "sourceInstanceId")}
    assert valid_target(ordinary)
    assert not valid_target({**ordinary, "dependencyDigest": "f" * 64})


@pytest.mark.parametrize("source", ["git-suffix", "subdirectory"])
def test_feature_restart_requires_canonical_root_app_receipt(source):
    from amplifier_web.update_readiness import valid_target
    target = restart_target()
    app = target["featureSelection"]["hostApp"]
    if source == "git-suffix":
        app["url"] += ".git"
    else:
        app["subdirectory"] = "packages/custom-app"
    assert not valid_target(target)


@pytest.mark.parametrize("malformed", ["selection-null", "selection-list", "selection-string", "host-app-null", "host-app-list", "host-app-source", "host-app-revision", "extras", "digest-missing", "digest-invalid"])
async def test_malformed_feature_restart_marker_is_retired_on_startup_without_crash_or_replay(managed, malformed):
    from amplifier_web.update_readiness import valid_target
    target = restart_target()
    selected = target["featureSelection"]
    if malformed.startswith("selection-"):
        target["featureSelection"] = {"selection-null": None, "selection-list": [], "selection-string": "not-a-selection"}[malformed]
    elif malformed == "host-app-null":
        selected["hostApp"] = None
    elif malformed == "host-app-list":
        selected["hostApp"] = []
    elif malformed == "host-app-source":
        selected["hostApp"]["url"] = "https://github.com/other/amplifier-unified"
    elif malformed == "host-app-revision":
        selected["hostApp"]["revision"] = "d" * 40
    elif malformed == "extras":
        selected["extras"] = ["native-desktop"]
    elif malformed == "digest-missing":
        target.pop("dependencyDigest")
    else:
        target["dependencyDigest"] = "invalid"
    assert not valid_target(target)
    managed.service.state["updates"].update(phase="activating", pendingRestart=target, pendingApp={"featureSelection": []},
                                          featureResults={"request-one": {"phase": "restart_pending"}})
    manager = UpdateManager(managed.service)
    managed.service.update_manager = manager
    state = managed.service.state["updates"]
    assert state["phase"] == "activating"
    assert state["pendingRestart"] is None and state["pendingApp"] is None
    assert state["pendingReplacement"]["unqualified"] is True
    assert state["featureResults"]["request-one"]["phase"] == "interrupted"
    assert manager.awaiting_restart() and work_paused(managed.service.state)
    assert managed.calls == []
    saved = load_saved_state(managed.service.db)
    assert saved["updates"]["pendingRestart"] is None
    managed.restart.assert_not_awaited()


@pytest.mark.parametrize("malformed", [None, [], "invalid"])
def test_startup_reconciliation_tolerates_nonobject_result_collections(malformed):
    state = {"pendingRestart": {"featureSelection": []}, "featureResults": malformed}
    app_features.reconcile_requests(state)
    assert state["featureResults"] == {}


@pytest.mark.parametrize("malformed", [None, [], "invalid"])
def test_startup_reconciliation_tolerates_nonobject_rows_and_pending_selections(malformed):
    state = {"pendingApp": {"featureSelection": malformed}, "featureResults": {"request-one": malformed}}
    app_features.reconcile_requests(state)
    assert state["featureResults"]["request-one"]["phase"] == "interrupted"
    assert state["featureResults"]["request-one"]["requestId"] == "request-one"
