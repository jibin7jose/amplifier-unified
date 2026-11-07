"""Full access recovery through the native process fixture, without a grant.

The real worker bridge and configuration writer run in a synthetic subprocess;
mounting and lease setup are fixture adapters. No provider calls are made.
"""
import asyncio
import copy
import json

import pytest

from amplifier_web.configuration_recovery import evidence
from amplifier_web.runtime_controls import override_path
from amplifier_web.service import AppError
from test_queued_configuration_boundary import (
    deferred_native_evidence, operation, plan, process_apply, process_boundary, recovery,
)


pytestmark = pytest.mark.parametrize("process_boundary", ["full"], indirect=True)


async def refused_operation(b):
    assert b.authorized is None
    assert not b.app.configuration_authority.rows(b.owner["id"])
    await b.runtime._request(b.owner["id"], "fixture.busy", count=1)
    with deferred_native_evidence():
        assert (await process_apply(b))["phase"] == "error"
    saved = operation(b)
    # Existing records call both grant and Full authority "grant". Recovery
    # must interpret the retained authority without migrating this request.
    assert saved["request"]["authorization"] == "grant"
    assert "accessRevision" in saved["request"]["authority"]
    assert "grantId" not in saved["request"]["authority"]
    assert saved["state"]["effect"] == "unknown"
    assert evidence(saved["attempts"][-1])["effect"] == "none"
    assert not override_path(b.owner["runtimeSessionId"]).exists()
    return saved


async def replace_mode(b, mode):
    report = (await b.app.dispatch("access.get", {"sessionId": b.owner["id"]}))["result"]
    await b.app.dispatch("access.set", {"sessionId": b.owner["id"], "scope": "session",
        "mode": mode, "expectedRevision": report["revision"]})


async def invalidate(b, reason):
    if reason == "revoked":
        await replace_mode(b, "ask")
        await replace_mode(b, "full")
    elif reason == "stopped":
        b.owner["interruptionRevision"] = b.owner.get("interruptionRevision", 0) + 1
    else:
        b.app.management.queued_path(b.owner["id"]).write_text(json.dumps(plan(3)))


async def test_full_native_refusal_recovers_without_reapproval_or_rewriting_history(process_boundary):
    b = process_boundary
    saved = await refused_operation(b)
    request, history = copy.deepcopy(saved["request"]), copy.deepcopy(saved["history"])
    view = (await recovery(b, "read", includeRecovery=True))["result"]
    assert view["recovery"]["canReconcile"] and not view["recovery"]["canResume"]
    assert operation(b) == saved  # Observing availability never replays work.
    reconciled = (await recovery(b, "reconcile"))["result"]
    assert reconciled["state"]["effect"] == "none"
    assert (await recovery(b, "read", includeRecovery=True))["result"]["recovery"]["canResume"]
    assert not override_path(b.owner["runtimeSessionId"]).exists()
    await b.runtime._request(b.owner["id"], "fixture.busy", count=0)
    resumed = await recovery(b, "resume", identity="full-resume")
    await asyncio.wait_for(b.app.management.pending_config_tasks[b.owner["id"]], 12)
    final = operation(b)
    assert final["id"] == saved["id"] and final["request"] == request
    assert final["requestHash"] == saved["requestHash"]
    assert final["history"][:len(history)] == history
    assert len(final["attempts"]) == 2 and final["attempts"][0] == saved["attempts"][0]
    assert final["state"]["effect"] == "mounted" and final["state"]["phase"] == "ready"
    assert json.loads(override_path(b.owner["runtimeSessionId"]).read_text()) == plan(2)
    assert b.owner["configuration"]["plan"]["tools"][0]["config"]["limit"] == 2
    assert not b.owner["approvals"] and not b.app.configuration_authority.rows(b.owner["id"])
    duplicate = await recovery(b, "resume", identity="full-resume", revision=reconciled["revision"])
    assert duplicate["duplicate"] and duplicate["result"] == resumed["result"]
    assert operation(b) == final


@pytest.mark.parametrize("reason", ["revoked", "stopped", "payload"])
async def test_full_recovery_keeps_original_authority_and_payload(process_boundary, reason):
    b = process_boundary
    await refused_operation(b)
    await recovery(b, "reconcile")
    saved = operation(b)
    await invalidate(b, reason)
    view = (await recovery(b, "read", includeRecovery=True))["result"]
    assert not view["recovery"]["canReconcile"] and not view["recovery"]["canResume"]
    with pytest.raises(AppError):
        await recovery(b, "resume")
    assert operation(b) == saved
    assert not override_path(b.owner["runtimeSessionId"]).exists()


@pytest.mark.parametrize("reason", ["revoked", "stopped"])
async def test_full_resumed_queue_rechecks_authority_before_native_dispatch(process_boundary, reason):
    b = process_boundary
    await refused_operation(b)
    await recovery(b, "reconcile")
    await b.runtime._request(b.owner["id"], "fixture.busy", count=0)
    b.owner["status"] = "working"
    assert (await recovery(b, "resume"))["result"]["state"]["phase"] == "queued"
    pending = b.app.management.pending_config_tasks[b.owner["id"]]
    saved = operation(b)
    await invalidate(b, reason)
    b.owner["status"] = "idle"
    await asyncio.wait_for(pending, 12)
    final = operation(b)
    assert final["state"]["phase"] == "error" and final["state"]["effect"] == "none"
    assert final["request"] == saved["request"] and final["requestHash"] == saved["requestHash"]
    assert final["attempts"] == saved["attempts"]
    assert not override_path(b.owner["runtimeSessionId"]).exists()


async def test_full_access_does_not_resume_partial_native_write(process_boundary):
    b = process_boundary
    await b.runtime._request(b.owner["id"], "fixture.partial")
    assert (await process_apply(b))["phase"] == "error"
    saved = operation(b)
    assert evidence(saved["attempts"][-1])["effect"] == "unknown"
    settings = override_path(b.owner["runtimeSessionId"]).read_bytes()
    assert json.loads(settings) == plan(2)
    assert (await recovery(b, "reconcile"))["result"]["state"]["effect"] == "unknown"
    with pytest.raises(AppError, match="no-effect"):
        await recovery(b, "resume")
    assert operation(b)["attempts"] == saved["attempts"]
    assert operation(b)["request"] == saved["request"]
    assert override_path(b.owner["runtimeSessionId"]).read_bytes() == settings
