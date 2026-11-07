"""A proven pre-write rejection must not keep ordinary input locked."""
import copy
import json

import pytest

from amplifier_web.configuration_recovery import evidence, receipt_path, seal
from amplifier_web.runtime_controls import REDACTED, override_path
from test_configuration_authority import finished, grant
from test_coordination_generation import agent
from test_queued_configuration_boundary import boundary, operation


async def reject_stale_plan(b):
    b.runtime.block_start = False
    tool = b.runtime.coordinator.config["tools"][0]
    tool.update(source="/synthetic/current/tool")
    tool["config"]["api_key"] = "synthetic-secret"
    request = copy.deepcopy(b.runtime.coordinator.config)
    request["tools"][0]["source"] = "/synthetic/retired/tool"
    request["tools"][0]["config"]["api_key"] = REDACTED
    await grant(b.app, ["runtime_configuration"], ["session"])
    await agent(b.app, b.owner, "configuration.apply",
        {"id": b.owner["id"], "config": request}, "rejected-stale-plan")
    assert (await finished(b.app, "rejected-stale-plan"))["phase"] == "error"
    saved = operation(b)
    assert "A redacted value cannot be used for a new setting" in saved["state"]["error"]
    assert saved["request"]["config"] == request
    assert len(saved["attempts"]) == 1
    assert not override_path(b.owner["id"]).exists()
    assert b.runtime.controls.state_path().read_bytes() == b.runtime.original_controls
    assert tool["config"]["api_key"] == "synthetic-secret"
    assert not b.runtime.inputs and "stop:configuration" not in b.runtime.calls
    return saved


async def test_rejected_stale_redacted_plan_releases_input_without_retry(boundary):
    saved = await reject_stale_plan(boundary)
    observed = evidence(saved["attempts"][-1])
    assert observed["effect"] == "none" and observed["terminal"] is True
    assert saved["state"]["phase"] == "error"
    assert saved["state"]["effect"] == "none"
    assert saved["state"]["stage"] == "reconciled"
    assert not saved["state"]["recoveryRequired"]
    assert not boundary.owner["configurationBusy"]
    assert "configurationOperationFence" not in boundary.owner


@pytest.mark.parametrize("receipt", ["missing", "corrupt", "record-list", "body-list",
    "forged", "foreign", "nonterminal", "unknown", "saved"])
async def test_unproven_rejection_keeps_input_held(boundary, receipt):
    original = boundary.runtime.control

    async def altered(sid, name, args, **kwargs):
        try:
            return await original(sid, name, args, **kwargs)
        except ValueError:
            if name != "configuration.apply":
                raise
            attempt = args["_configurationAttempt"]
            path = receipt_path(attempt)
            if receipt == "missing":
                path.unlink()
            elif receipt == "corrupt":
                path.write_text("{")
            elif receipt == "record-list":
                path.write_text("[]")
            elif receipt == "body-list":
                path.write_text('{"body": []}')
            else:
                effect = {"unknown": "unknown", "saved": "saved_not_mounted"}.get(receipt, "none")
                record = seal(attempt, effect, terminal=receipt != "nonterminal")
                if receipt == "forged":
                    record["signature"] = "0" * 64
                elif receipt == "foreign":
                    record = seal({**attempt, "requestHash": "another-request"}, "none", terminal=True)
                path.write_text(json.dumps(record))
            raise

    boundary.runtime.control = altered
    saved = await reject_stale_plan(boundary)
    assert saved["state"]["effect"] == "unknown"
    assert boundary.owner["configurationBusy"]
    assert boundary.owner["configurationOperationFence"] == saved["id"]


async def test_proven_rejection_does_not_release_another_operation_fence(boundary):
    original = boundary.runtime.control

    async def replaced(sid, name, args, **kwargs):
        try:
            return await original(sid, name, args, **kwargs)
        except ValueError:
            boundary.owner["configurationOperationFence"] = "different-operation"
            raise

    boundary.runtime.control = replaced
    saved = await reject_stale_plan(boundary)
    assert saved["state"]["effect"] == "none"
    assert boundary.owner["configurationBusy"]
    assert boundary.owner["configurationOperationFence"] == "different-operation"
