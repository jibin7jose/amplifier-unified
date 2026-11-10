"""Destructive cases use only fixture roots; never the installed host's data."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading

import pytest

from amplifier_web import reset_retention as policy, reset
from test_reset import repair_tree, fake_install


def members(owner, operation):
    return [Path(member["path"]) for member in owner.state["operations"][operation]["members"]]


def populate(owner, operation):
    for path in members(owner, operation):
        (path / "sentinel").write_text(operation)
        if path.name == operation and path.parent.name == "backups":
            (path / "private-state.tar.gz").write_text("fixture archive")


def completed(owner, origin="in-app", tool=None):
    operation = owner.begin(origin, tool_root=tool)
    populate(owner, operation)
    owner.succeed(operation)
    return operation


@pytest.mark.parametrize("origins", [("cli", "cli", "cli"), ("in-app",) * 3,
                                    ("cli", "in-app", "cli"), ("in-app", "cli", "in-app")])
def test_shared_keep_one_physically_removes_all_old_members(tmp_path, origins):
    home, tool = tmp_path / "data", tmp_path / "tools"
    tool.mkdir()
    manual = home / "backups" / ("e" * 32)
    manual.mkdir(parents=True)
    (manual / "private-state.tar.gz").write_text("manual backup")
    with policy.lease(home) as owner:
        old_paths = []
        for origin in origins:
            operation = completed(owner, origin, tool)
            current = members(owner, operation)
            # Changing mtimes cannot change the committed completion order.
            for path in current:
                os.utime(path, ns=(1, 1))
            result = owner.cleanup()
            assert result["cleanupComplete"] and result["retainedOperationId"] == operation
            assert all(not path.exists() for path in old_paths)
            assert all((path / "sentinel").read_text() == operation for path in current)
            old_paths = current
        assert owner.state["sequence"] == 3
    assert (manual / "private-state.tar.gz").read_text() == "manual backup"


def test_pending_failed_and_legacy_never_become_disposable(tmp_path):
    with policy.lease(tmp_path) as owner:
        first = completed(owner)
        previous = members(owner, first)
        failed = owner.begin("in-app")
        populate(owner, failed)
        owner.fail(failed)
        pending = owner.begin("in-app")
        populate(owner, pending)
        legacy = tmp_path / "backups" / ("reset-" + "a" * 32)
        legacy.mkdir()
        owner.cleanup()
        assert all(path.exists() for path in previous)
        completed(owner)
        report = owner.cleanup()
        assert not any(path.exists() for path in previous)
        assert set(report["preservedUnresolved"]) == {failed, pending}
        assert report["legacyReview"][0]["path"] == str(legacy)
        assert all(path.exists() for operation in (failed, pending) for path in members(owner, operation))


def test_cleanup_error_and_crash_between_members_are_retryable(tmp_path, monkeypatch):
    class Crash(BaseException):
        pass
    with policy.lease(tmp_path) as owner:
        first = completed(owner)
        paths = members(owner, first)
        latest = completed(owner)
        original = policy.remove_member
        calls = []
        def crash(path, member):
            original(path, member)
            calls.append(path)
            if len(calls) == 1:
                raise Crash()
        with monkeypatch.context() as patch:
            patch.setattr(policy, "remove_member", crash)
            with pytest.raises(Crash):
                owner.cleanup()
        assert not paths[0].exists() and paths[1].exists()
        assert all(path.exists() for path in members(owner, latest))
        assert owner.cleanup()["cleanupComplete"]
        assert not any(path.exists() for path in paths)
        assert owner.state["sequence"] == 2


def test_partial_member_deletion_can_resume_after_owner_marker_removed(tmp_path, monkeypatch):
    with policy.lease(tmp_path) as owner:
        first = completed(owner)
        latest = completed(owner)
        def partial(path, member):
            (path / "sentinel").unlink()
            (path / "private-state.tar.gz").unlink()
            (path / policy.MARKER).unlink()
            raise PermissionError("fixture access denied")
        with monkeypatch.context() as patch:
            patch.setattr(policy, "remove_member", partial)
            result = owner.cleanup()
        assert not result["cleanupComplete"] and "access denied" in result["errors"][0]
        assert owner.cleanup()["cleanupComplete"]
        assert not any(path.exists() for path in members(owner, first))
        assert all(path.exists() for path in members(owner, latest))


@pytest.mark.parametrize("attack", ["path", "owner", "inode", "ancestor", "order", "missing_latest", "foreign_home"])
def test_malicious_or_ambiguous_metadata_preserves_unrelated_files(tmp_path, attack):
    home = tmp_path / "data"
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("untouched")
    with policy.lease(home) as owner:
        old = completed(owner)
        latest = completed(owner)
        row = owner.state["operations"][old]
        member = row["members"][0]
        saved = Path(member["path"])
        if attack == "path":
            member["path"] = str(victim)
        elif attack == "owner":
            (saved / policy.MARKER).write_text("{}")
        elif attack == "inode":
            moved = saved.with_name(saved.name + "-manual")
            saved.rename(moved)
            saved.mkdir()
            shutil.copy2(moved / policy.MARKER, saved / policy.MARKER)
        elif attack == "ancestor":
            backup = home / "backups"
            backup.rename(home / "manual-backups")
            backup.symlink_to(victim, target_is_directory=True)
        elif attack == "order":
            row["successOrder"] = owner.state["operations"][latest]["successOrder"]
        elif attack == "missing_latest":
            shutil.rmtree(members(owner, latest)[0])
        elif attack == "foreign_home":
            owner.state["dataDir"] = str(victim)
        owner.save()
        try:
            result = owner.cleanup()
            assert not result["cleanupComplete"]
        except (ValueError, OSError):
            pass
        assert (victim / "keep").read_text() == "untouched"


def test_final_and_nested_symlinks_unlinked_without_following_targets(tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("safe")
    with policy.lease(tmp_path / "data") as owner:
        old = completed(owner)
        paths = members(owner, old)
        shutil.rmtree(paths[0])
        paths[0].symlink_to(victim, target_is_directory=True)
        (paths[1] / "linked").symlink_to(victim, target_is_directory=True)
        latest = completed(owner)
        assert owner.cleanup()["cleanupComplete"]
        assert not paths[0].is_symlink() and not paths[1].exists()
        assert all(path.exists() for path in members(owner, latest))
    assert (victim / "keep").read_text() == "safe"


def test_other_profile_and_shared_tool_process_leases(tmp_path):
    tool = tmp_path / "tools"
    tool.mkdir()
    first_home, second_home = tmp_path / "one", tmp_path / "two"
    with policy.lease(first_home) as first, policy.lease(second_home) as second:
        old = completed(first, "cli", tool)
        foreign = completed(second, "cli", tool)
        completed(first)
        with reset.repair_lock(reset.tool_recovery_root(tool)):
            assert not first.cleanup()["cleanupComplete"]  # Fail fast, never wait on host shutdown.
        assert first.cleanup()["cleanupComplete"]
        assert not any(path.exists() for path in members(first, old))
        assert all(path.exists() for path in members(second, foreign))
        code = ("import sys; sys.path.insert(0,sys.argv[1]); "
                "from amplifier_web.reset_retention import lease; "
                "owner=lease(sys.argv[2]); owner.__enter__()")
        result = subprocess.run([sys.executable, "-I", "-S", "-c", code,
                                 str(Path(__file__).parents[1]), str(first_home)],
                                capture_output=True, text=True)
        assert result.returncode != 0 and "BlockingIOError" in result.stderr
    # The host's shared repair lease does not prevent in-app retention.
    with reset.repair_lock(first_home, shared=True), policy.lease(first_home):
        pass


def test_ledger_cannot_enroll_a_substituted_external_namespace(tmp_path):
    tool, forged_tool = tmp_path / "tools", tmp_path / "other-tools"
    tool.mkdir()
    forged_tool.mkdir()
    with policy.lease(tmp_path / "data") as owner:
        old = completed(owner, "cli", tool)
        latest = completed(owner)
        row = owner.state["operations"][old]
        original = Path(row["members"][1]["path"])
        forged = reset.tool_recovery_root(forged_tool) / old
        forged.parent.mkdir()
        original.rename(forged)
        row["toolRoot"] = str(forged_tool)
        row["toolMembers"] = {"previous-installation": str(forged / "previous"),
                              "failed-replacement": str(forged / "failed")}
        row["members"][1]["path"] = str(forged)
        owner.save()
        result = owner.cleanup()
        assert not result["cleanupComplete"]
        assert (forged / "sentinel").read_text() == old
        assert all(path.exists() for path in members(owner, latest))


def test_forged_deleting_record_cannot_delete_another_profiles_pair(tmp_path):
    import copy
    tool = tmp_path / "tools"
    tool.mkdir()
    with policy.lease(tmp_path / "one") as local, policy.lease(tmp_path / "two") as foreign:
        completed(local, "cli", tool)  # Even an enrolled shared root isn't ownership.
        operation = completed(foreign, "cli", tool)
        row = copy.deepcopy(foreign.state["operations"][operation])
        row["successOrder"] = 2
        row["members"][0]["path"] = str(local.home / "reset-backups" / operation)
        for member in row["members"]:
            member["deleting"] = True
        local.state["operations"][operation] = row
        local.state["sequence"] = 2
        local.save()
        completed(local)
        result = local.cleanup()
        assert not result["cleanupComplete"]
        assert all((path / "sentinel").read_text() == operation for path in members(foreign, operation))


def test_payload_sync_precedes_success_ledger_and_pruning(tmp_path, monkeypatch):
    events = []
    with policy.lease(tmp_path) as owner:
        old = completed(owner)
        operation = owner.begin("in-app")
        populate(owner, operation)
        sync = policy._sync_directory
        write = policy._write
        def recorded_sync(path):
            events.append(("sync", str(path)))
            sync(path)
        def recorded_write(path, value):
            if path.name == "state.json" and value["operations"][operation]["state"] == "successful":
                events.append(("success", operation))
            write(path, value)
        monkeypatch.setattr(policy, "_sync_directory", recorded_sync)
        monkeypatch.setattr(policy, "_write", recorded_write)
        owner.succeed(operation)
        commit = next(index for index, event in enumerate(events) if event[0] == "success")
        assert ("sync", str(members(owner, operation)[0])) in events[:commit]
        assert ("sync", str(members(owner, operation)[1])) in events[:commit]
        assert all(path.exists() for path in members(owner, old))


def test_removed_member_reappearing_is_not_deleted_or_reported_complete(tmp_path):
    with policy.lease(tmp_path) as owner:
        old = completed(owner)
        paths = members(owner, old)
        completed(owner)
        assert owner.cleanup()["cleanupComplete"]
        paths[0].mkdir()
        (paths[0] / "manual").write_text("new unrelated content")
        assert not owner.cleanup()["cleanupComplete"]
        assert (paths[0] / "manual").read_text() == "new unrelated content"


@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink"])
def test_retention_and_repair_locks_reject_substitutions(tmp_path, kind):
    target = tmp_path / "target"
    target.write_text("private target")
    for directory, name, acquire in ((tmp_path / "one", "lock", lambda home: policy.Retention(home.parent).acquire()),
                                     (tmp_path / "two", ".reset.lock", reset.repair_lock)):
        # Retention uses its own exact control directory.
        if name == "lock":
            directory = tmp_path / "retention-home" / "reset-recovery"
        directory.mkdir(parents=True)
        path = directory / name
        if kind == "symlink":
            path.symlink_to(target)
        elif kind == "hardlink":
            os.link(target, path)
        else:
            os.mkfifo(path)
        with pytest.raises((OSError, ValueError)):
            if name == "lock":
                owner = policy.Retention(directory.parent).acquire()
                owner.close()
            else:
                with acquire(directory):
                    pass
    assert target.read_text() == "private target"


def test_emergency_script_isolated_actual_external_cleanup(tmp_path):
    home, tool = tmp_path / "data", tmp_path / "tools"
    tool.mkdir()
    with policy.lease(home) as owner:
        old = completed(owner, "cli", tool)
        previous = members(owner, old)
        latest = completed(owner)
        current = members(owner, latest)
    # No normal app package in this directory; -I -S also ignores site/PYTHONPATH.
    emergency = tmp_path / "emergency"
    emergency.mkdir()
    for name in ("reset.py", "reset_retention.py"):
        shutil.copy2(Path(reset.__file__).with_name(name), emergency / name)
    result = subprocess.run([sys.executable, "-I", "-S", str(emergency / "reset.py"),
                             "--data-dir", str(home), "--cleanup-only"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"cleanupComplete": true' in result.stdout
    assert not any(path.exists() for path in previous)
    assert all((path / "sentinel").read_text() == latest for path in current)


@pytest.mark.parametrize("state", [[], {"operations": []}, {"operations": {"a": []}}])
def test_malformed_catalog_state_protects_manual_archives(tmp_path, state):
    root = tmp_path / "reset-recovery"
    root.mkdir()
    if isinstance(state, dict):
        state["dataIdentity"] = policy.identity(tmp_path)
    (root / "state.json").write_text(json.dumps(state))
    assert policy.archive_ids(tmp_path) is None


def test_emergency_direct_script_and_policy_import_without_app_dependencies(tmp_path):
    script = Path(reset.__file__)
    result = subprocess.run([sys.executable, "-I", "-S", str(script),
                             "--data-dir", str(tmp_path / "data"), "--cleanup-only"],
                            capture_output=True, text=True,
                            env={**os.environ, "HOME": str(tmp_path / "user")})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cleanupComplete"]


def test_three_cli_resets_and_postcheck_failure_preserve_previous(repair_tree, monkeypatch):
    home, tool, bins = repair_tree
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    fake_install(monkeypatch)
    first = reset.repair(home, "uv", tool, bins, "fixture.whl", {}, None, True)
    second = reset.repair(home, "uv", tool, bins, "fixture.whl", {}, None, True)
    assert not first.exists()
    assert not (reset.tool_recovery_root(tool) / first.name).exists()
    monkeypatch.setattr(reset, "post_checks", lambda *a, **kw: {"doctor": {"ok": False}, "service status": {"ok": False}})
    with pytest.raises(RuntimeError):
        reset.repair(home, "uv", tool, bins, "fixture.whl", {}, None, True)
    assert second.exists() and (reset.tool_recovery_root(tool) / second.name / "previous").exists()
    monkeypatch.setattr(reset, "post_checks", lambda *a, **kw: {"doctor": {"ok": True}, "service status": {"ok": False}})
    third = reset.repair(home, "uv", tool, bins, "fixture.whl", {}, None, True)
    assert third.exists() and not second.exists()
    assert not (reset.tool_recovery_root(tool) / second.name).exists()
    with policy.lease(home) as owner:
        assert owner.state["sequence"] == 3
        assert len(owner.cleanup()["preservedUnresolved"]) == 1


async def app_fixture(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    from amplifier_web.updates import UpdateManager
    from test_service import Runtime
    app = AppService(tmp_path / "app", Runtime(), workspace=tmp_path)
    app.management = Management(app)
    app.update_manager = UpdateManager(app)
    return app


async def dispatch_reset(app, command, parts=("runtime",)):
    receipt = await app.dispatch("maintenance.reset", {
        "parts": list(parts), "apply": True, "confirmation": "RESET"}, command_id=command)
    for _ in range(1000):
        if command in app.state.get("managementResults", {}) and not app.management.lock.locked():
            break
        await asyncio.sleep(.01)
    else:
        pytest.fail("Reset action did not settle")
    assert app.state["managementResults"][command]["phase"] == "ready"
    return receipt


async def test_three_public_inapp_resets_pair_archive_originals_and_manual_policy(tmp_path):
    from amplifier_web.storage_backup_cleanup import candidates
    from amplifier_web.storage_catalog import catalog
    app = await app_fixture(tmp_path)
    try:
        await app.management.perform("maintenance.backup", {})
        manual = Path(app.state["maintenance"]["backup"]).parent
        previous = []
        for index, parts in enumerate((["settings"], ["cache"], ["runtime", "conversations"])):
            await dispatch_reset(app, f"reset-{index}", parts)
            row = app.state["maintenance"]
            current = [Path(row["backup"]).parent, Path(row["retained"])]
            assert current[1].name == "reset-" + current[0].name
            assert all(path.exists() for path in current)
            assert all(not path.exists() for path in previous)
            previous = current
        assert candidates(app.data_dir) == [manual]
        roots = catalog(app.data_dir, [])
        assert all(roots[path]["protected"] for path in current)
        assert manual.exists()
        # Conversations reset preserved the public effect's command binding.
        old_operation = row["resetOperationId"]
        await app.dispatch("maintenance.reset", {"parts": ["runtime", "conversations"], "apply": True,
                           "confirmation": "RESET"}, command_id="reset-2")
        await asyncio.sleep(.02)
        assert app.state["maintenance"]["resetOperationId"] == old_operation
    finally:
        await app.close()


async def test_public_final_outcome_save_failure_never_prunes(tmp_path, monkeypatch):
    app = await app_fixture(tmp_path)
    try:
        await dispatch_reset(app, "first")
        old = [Path(app.state["maintenance"]["backup"]).parent, Path(app.state["maintenance"]["retained"])]
        original = app._save
        def fail_terminal():
            if app.state.get("managementResults", {}).get("second", {}).get("phase") == "ready":
                raise OSError("terminal receipt fixture failure")
            return original()
        with monkeypatch.context() as patch:
            patch.setattr(app, "_save", fail_terminal)
            await app.dispatch("maintenance.reset", {"parts": ["runtime"], "apply": True,
                               "confirmation": "RESET"}, command_id="second")
            for _ in range(1000):
                if not app.management.lock.locked() and "second" in app.state.get("managementResults", {}):
                    break
                await asyncio.sleep(.01)
        assert all(path.exists() for path in old)
        with policy.lease(app.data_dir) as owner:
            assert owner.state["sequence"] == 1
            assert any(row["state"] == "unresolved" for row in owner.state["operations"].values())
    finally:
        await app.close()


async def test_applied_store_save_failure_preserves_prior_recovery(tmp_path, monkeypatch):
    app = await app_fixture(tmp_path)
    try:
        await dispatch_reset(app, "first")
        previous = Path(app.state["maintenance"]["retained"])
        def fail_full(*args, **kwargs):
            raise OSError("fixture reset-store save failed")
        monkeypatch.setattr(app, "_publish_full", fail_full)
        await app.dispatch("maintenance.reset", {"parts": ["runtime"], "apply": True,
                           "confirmation": "RESET"}, command_id="second")
        for _ in range(1000):
            if app.state.get("managementResults", {}).get("second", {}).get("phase") == "error":
                break
            await asyncio.sleep(.01)
        assert app.state["managementResults"]["second"]["phase"] == "error"
        assert previous.exists()
        with policy.lease(app.data_dir) as owner:
            assert owner.state["sequence"] == 1
    finally:
        await app.close()


async def test_cleanup_failure_is_successful_and_explicit_retry_offloop(tmp_path, monkeypatch):
    app = await app_fixture(tmp_path)
    try:
        await dispatch_reset(app, "first")
        old = Path(app.state["maintenance"]["retained"])
        original_runtime = app.runtime
        original_remove = policy.remove_member
        main_thread = threading.get_ident()
        def denied(path, member):
            assert threading.get_ident() != main_thread
            assert not app.lock.locked() and not app.publishing.lock.locked()
            raise PermissionError("fixture denied")
        with monkeypatch.context() as patch:
            patch.setattr(policy, "remove_member", denied)
            await dispatch_reset(app, "second")
        assert app.runtime is not original_runtime
        assert app.state["maintenance"]["resetRetention"]["cleanupComplete"] is False
        assert "Reset succeeded; older-backup cleanup incomplete" in app.state["maintenance"]["detail"]
        assert old.exists()
        await app.management.perform("maintenance.resetCleanup", {})
        assert not old.exists()
        assert app.state["maintenance"]["resetRetention"]["cleanupComplete"]
    finally:
        await app.close()


async def test_cli_inapp_cli_share_one_recovery_point(repair_tree, monkeypatch):
    home, tool, bins = repair_tree
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    fake_install(monkeypatch)
    # repair_tree's intentionally opaque state_records are for byte-preserving
    # CLI rollback, not an app schema. Start the app from its real schema first.
    for path in home.glob("app.sqlite3*"):
        path.unlink()
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    from amplifier_web.updates import UpdateManager
    from test_service import Runtime
    initial = AppService(home, Runtime(), workspace=home.parent)
    await initial.close()
    first = reset.repair(home, "uv", tool, bins, "fixture.whl", {}, None, True)
    # Reuse precisely the same data-directory identity through the public app.
    app = AppService(home, Runtime(), workspace=home.parent)
    app.management = Management(app)
    app.update_manager = UpdateManager(app)
    try:
        await dispatch_reset(app, "in-app")
        current = [Path(app.state["maintenance"]["backup"]).parent, Path(app.state["maintenance"]["retained"])]
        assert not first.exists() and not (reset.tool_recovery_root(tool) / first.name).exists()
    finally:
        await app.close()
    latest = reset.repair(home, "uv", tool, bins, "fixture.whl", {}, None, True)
    assert latest.exists() and not any(path.exists() for path in current)


@pytest.mark.parametrize("defect", ["missing", "symlink", "marker"])
def test_corrupt_newest_never_prunes_previous(tmp_path, defect):
    with policy.lease(tmp_path) as owner:
        old = completed(owner)
        latest = completed(owner)
        path = members(owner, latest)[0]
        if defect == "marker":
            (path / policy.MARKER).write_text("{}")
        else:
            shutil.rmtree(path)
            if defect == "symlink":
                path.symlink_to(members(owner, old)[0])
        with pytest.raises((ValueError, OSError)):
            owner.cleanup()
        assert all(path.exists() for path in members(owner, old))


def test_missing_newest_archive_payload_preserves_previous(tmp_path):
    with policy.lease(tmp_path) as owner:
        old = completed(owner)
        latest = completed(owner)
        (members(owner, latest)[0] / "private-state.tar.gz").unlink()
        with pytest.raises((ValueError, OSError)):
            owner.cleanup()
        assert all(path.exists() for path in members(owner, old))


def test_active_recovery_path_and_plain_structural_substitution_preserved(tmp_path, monkeypatch):
    with policy.lease(tmp_path / "data") as owner:
        old = completed(owner)
        completed(owner)
        path = members(owner, old)[0]
        original = policy.subprocess.run
        monkeypatch.setattr(policy.subprocess, "run", lambda *a, **kw: type("Result", (), {"stdout": f"123 {path}/bin/python worker.py"})())
        assert not owner.cleanup()["cleanupComplete"]
        assert path.exists()
        monkeypatch.setattr(policy.subprocess, "run", original)
        parent = path.parent
        parent.rename(parent.with_name("saved-backups"))
        parent.mkdir()
        assert not owner.root.is_relative_to(parent)
        with pytest.raises((ValueError, OSError)):
            owner.cleanup()
        assert (parent.with_name("saved-backups") / old).exists()


@pytest.mark.parametrize("broken", [None, [], {"state": "successful", "successOrder": "yesterday"}])
def test_malformed_ledger_fails_closed(tmp_path, broken):
    with policy.lease(tmp_path) as owner:
        previous = completed(owner)
        paths = members(owner, previous)
        owner.state["operations"]["bad"] = broken
        owner.save()
        with pytest.raises(ValueError):
            owner.cleanup()
        assert all(path.exists() for path in paths)


def test_pending_creation_and_missing_new_member_cannot_commit_success(tmp_path):
    with policy.lease(tmp_path) as owner:
        old = completed(owner)
        pending = owner.begin("in-app")
        shutil.rmtree(members(owner, pending)[1])
        with pytest.raises(ValueError):
            owner.succeed(pending)
        owner.cleanup()
        assert all(path.exists() for path in members(owner, old))
        assert owner.state["sequence"] == 1


def test_public_cli_run_three_resets_and_preview_no_mutation(repair_tree, monkeypatch):
    import argparse
    home, tool, bins = repair_tree
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    fake_install(monkeypatch)
    monkeypatch.setattr(reset, "find_uv", lambda: "uv")
    original = reset.command
    def command(*args, **kwargs):
        if args[:3] == ("uv", "tool", "dir"):
            return type("Result", (), {"stdout": str(bins if "--bin" in args else tool)})()
        return original(*args, **kwargs)
    monkeypatch.setattr(reset, "command", command)
    args = argparse.Namespace(data_dir=str(home), source="fixture.whl", yes=True,
                              dry_run=True, no_start=True, verbose=False)
    reset.run(args)
    assert not (home / "reset-recovery").exists()
    args.dry_run = False
    old = []
    for _ in range(3):
        reset.run(args)
        assert all(not path.exists() for path in old)
        old = list((home / "reset-backups").iterdir())
        assert len(old) == 1
    assert len(list(reset.tool_recovery_root(tool).glob("*/previous"))) == 1


@pytest.mark.parametrize("failure", ["install", "probe", "database", "startup", "doctor", "status"])
def test_failure_at_each_cli_stage_cannot_prune(repair_tree, monkeypatch, failure):
    home, tool, bins = repair_tree
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    fake_install(monkeypatch)
    prior = reset.repair(home, "uv", tool, bins, "fixture.whl", {}, None, True)
    fake_install(monkeypatch, fail=failure == "install", probe_fail=failure == "probe")
    if failure == "database":
        def bad_db(*args):
            raise sqlite3.DatabaseError("fixture database failure")
        monkeypatch.setattr(reset, "reset_update_state", bad_db)
    if failure == "startup":
        def bad_start(*args):
            raise OSError("fixture startup failure")
        monkeypatch.setattr(reset, "start_service", bad_start)
    if failure in {"doctor", "status"}:
        monkeypatch.setattr(reset, "post_checks", lambda *a, **kw: {
            "doctor": {"ok": failure != "doctor"}, "service status": {"ok": failure != "status"}})
    with pytest.raises((RuntimeError, ValueError, subprocess.SubprocessError, sqlite3.Error, OSError)):
        reset.repair(home, "uv", tool, bins, "fixture.whl", {}, (["stop"], ["start"], None), False)
    assert prior.exists() and (reset.tool_recovery_root(tool) / prior.name / "previous").exists()
    with policy.lease(home) as owner:
        assert owner.state["sequence"] == 1


async def test_cleanup_waits_for_actual_completion_with_committed_outcome_and_available_locks(tmp_path, monkeypatch):
    from amplifier_web.state_records import load
    app = await app_fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    task = None
    try:
        await dispatch_reset(app, "first")
        old = Path(app.state["maintenance"]["retained"])
        original = policy.remove_member
        def paused(path, member):
            with sqlite3.connect(app.data_dir / "app.sqlite3") as db:
                state = load(db)
                assert state["managementResults"]["second"]["phase"] == "ready"
                assert state["maintenance"]["resetOperationId"]
            entered.set()
            assert release.wait(10), "fixture cleanup was not released"
            original(path, member)
        monkeypatch.setattr(policy, "remove_member", paused)
        task = asyncio.create_task(app.management.command("maintenance.reset", {
            "parts": ["runtime"], "apply": True, "confirmation": "RESET"}, "second"))
        for _ in range(1000):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        assert entered.is_set() and not task.done() and old.exists()
        async with app.lock:
            pass
        async with app.publishing.lock:
            pass
        async with app.runtime_lifecycle_lock:
            pass
        release.set()
        await task
        assert not old.exists()
        assert app.state["maintenance"]["resetRetention"]["cleanupComplete"]
    finally:
        release.set()
        if task:
            await task
        await app.close()


@pytest.mark.parametrize("failure", ["metadata", "cleanup_publication"])
async def test_after_success_failure_never_overwrites_ready_or_discards_runtime(tmp_path, monkeypatch, failure):
    app = await app_fixture(tmp_path)
    try:
        await dispatch_reset(app, "first")
        old = Path(app.state["maintenance"]["retained"])
        if failure == "metadata":
            def denied(*args):
                raise OSError("fixture ledger write denied")
            monkeypatch.setattr(policy.Retention, "succeed", denied)
        else:
            publish = app.management.publish
            async def failed_publish(**values):
                if "resetRetention" in values.get("maintenance", {}):
                    raise OSError("fixture final cleanup publication failed")
                return await publish(**values)
            monkeypatch.setattr(app.management, "publish", failed_publish)
        await dispatch_reset(app, "second")
        assert app.state["managementResults"]["second"]["phase"] == "ready"
        assert app.state["updates"]["phase"] == "idle"
        if failure == "metadata":
            assert old.exists() and not app.state["maintenance"]["resetRetention"]["cleanupComplete"]
    finally:
        await app.close()


async def test_cancellation_during_cleanup_settles_thread_before_releasing_lease(tmp_path, monkeypatch):
    app = await app_fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    task = None
    try:
        await dispatch_reset(app, "first")
        original = policy.remove_member
        def paused(path, member):
            entered.set()
            assert release.wait(10)
            original(path, member)
        monkeypatch.setattr(policy, "remove_member", paused)
        task = asyncio.create_task(app.management.command("maintenance.reset", {
            "parts": ["runtime"], "apply": True, "confirmation": "RESET"}, "second"))
        for _ in range(1000):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        assert entered.is_set()
        task.cancel()
        await asyncio.sleep(.02)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        with pytest.raises(BlockingIOError):
            with policy.lease(app.data_dir):
                pass
        release.set()
        await task
        with policy.lease(app.data_dir) as owner:
            assert owner.cleanup()["cleanupComplete"]
        assert app.state["managementResults"]["second"]["phase"] == "ready"
    finally:
        release.set()
        if task:
            await task
        await app.close()