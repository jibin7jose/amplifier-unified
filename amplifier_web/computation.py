"""Host adapter: public computation actions and an approved mounted tool."""

from __future__ import annotations

import contextvars
import copy
import os
import shutil
import sys
from pathlib import Path

from amplifier_kernels import Kernels

# Trusted transport-start scope is inherited by the process observer task. It
# keeps encoded wire packets out of a second archive; decoded cell evidence is
# durably recorded once. This flag is never accepted from tool/action arguments.
KERNEL_TRANSPORT = contextvars.ContextVar("kernel_transport", default=False)
ACTIONS = ("create", "execute", "interrupt", "reset", "close")


def tool_schema():
    return {
        "type": "object",
        "properties": {
            "action": {"enum": list(ACTIONS)},
            "language": {"enum": ["python", "node"]},
            "kernelId": {"type": "string", "maxLength": 200},
            "cellId": {"type": "string", "maxLength": 200},
            "generation": {"type": "integer", "minimum": 1},
            "code": {"type": "string", "minLength": 1, "maxLength": 16000},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
            "questionIds": {
                "type": "array",
                "items": {"type": "string", "maxLength": 200},
                "maxItems": 20,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }


def definitions():
    from .action_schema import schema, string

    common = {
        "sessionId": string(200),
        "kernelId": string(200),
        "generation": {"type": "integer", "minimum": 1},
        "cellId": string(200),
    }
    definitions = {
        "kernels.list": (
            "Read saved computation kernels without starting or resuming a runtime.",
            schema({"sessionId": string(200)}, ["sessionId"]),
        ),
        "kernels.status": (
            "Read a saved kernel and generation; a restart never restores variables or replays cells.",
            schema(common, ["sessionId", "kernelId"]),
        ),
        "kernels.create": (
            "Create a Python or Node child process under existing execution permissions. Variables live only in its current owned generation.",
            schema(
                {"sessionId": string(200), "language": {"enum": ["python", "node"]}},
                ["sessionId", "language"],
            ),
        ),
    }
    for action in ("execute", "interrupt", "reset", "close"):
        properties = dict(common)
        required = ["sessionId", "kernelId", "generation"]
        if action == "execute":
            properties.update(
                code=tool_schema()["properties"]["code"],
                timeout=tool_schema()["properties"]["timeout"],
                questionIds={
                    "type": "array",
                    "items": string(200),
                    "maxItems": 20,
                    "uniqueItems": True,
                },
            )
            required.append("code")
        definitions["kernels." + action] = (
            "Submit exact code through execution approval; returns a durable cell operation ID."
            if action == "execute"
            else "Control the exact owned generation through execution approval. Reset requires confirmed termination before creating fresh state.",
            schema(properties, required),
        )
    return definitions


def runtimes():
    # No relative PATH/current-directory lookup and no untrusted request override.
    paths = os.pathsep.join(
        part
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if Path(part).is_absolute()
    )
    node = shutil.which("node", path=paths)
    result = {"python": str(Path(sys.executable).absolute())}
    if node:
        result["node"] = str(Path(node).absolute())
    return result


async def install(controls):
    from amplifier_core.models import ToolResult

    coordinator = controls.coordinator

    async def transport(action, arguments):
        token = KERNEL_TRANSPORT.set(True)
        try:
            response = await controls.invoke(
                {"name": "bash", "arguments": {"action": action, **arguments}},
                bound_arguments={"action": action, **arguments},
                checkpoint=False,
                provenance={"source": "kernels.transport"},
            )
        finally:
            KERNEL_TRANSPORT.reset(token)
        result = response.get("result", response)
        if not result.get("success"):
            raise ValueError(
                result.get("error", {}).get("message", "Process transport denied")
            )
        return result["output"]

    async def observe(event):
        callback = coordinator.get_capability("operations.observe")
        if not callable(callback):
            raise TypeError("Durable computation observation is unavailable")
        return await callback(event)

    manager = Kernels(transport, observe, runtimes())

    class Computation:
        name = "compute"
        description = (
            "Persistent Python/Node computation in an owned child process. Code can read/write files "
            "and use the network with the same host authority as approved shell execution; this is not a sandbox. "
            "Create, execute exact code in a known generation, interrupt, reset or close. Interrupt destroys "
            "variable memory; reset creates a fresh generation only after confirmed termination. Cells never replay."
        )
        input_schema = tool_schema()

        async def execute(self, data):
            try:
                action = data["action"]
                if action == "create":
                    result = await manager.create(data["language"])
                elif action == "execute":
                    result = await manager.execute(
                        data["kernelId"],
                        data["generation"],
                        data["code"],
                        data.get("timeout", 30),
                        data.get("questionIds", ()),
                    )
                elif action == "interrupt":
                    result = await manager.interrupt(
                        data["kernelId"], data["generation"], cell_id=data.get("cellId")
                    )
                else:
                    result = await getattr(manager, action)(
                        data["kernelId"], data["generation"]
                    )
                return ToolResult(success=True, output=result)
            except Exception as exc:  # noqa: BLE001 - Tool protocol returns failures instead of leaking exceptions.
                return ToolResult(success=False, error={"message": str(exc)})

    await coordinator.mount("tools", Computation(), name="compute")
    return manager


def saved(service, session_id, identity):
    value = service.operations.journal.status(session_id, identity)
    if value["kind"] != "kernel":
        raise ValueError("This operation is not a computation kernel")
    metadata = copy.deepcopy(value.get("metadata", {}))
    if value["state"] == "outcome_unknown":
        metadata.update(
            state="unavailable",
            reason="Host restarted or owner ended; variables were not restored and cells were not replayed",
        )
    return {
        **metadata,
        "operationState": value["state"],
        "revision": str(value["revision"]),
    }


async def dispatch(service, action, args, origin):
    from .errors import AppError

    sid = args["sessionId"]
    session = service._session(sid)
    try:
        verb = action.removeprefix("kernels.")
        if verb == "list":
            result = {
                "kernels": [
                    saved(service, sid, row["id"])
                    for row in service.operations.journal.list(sid, 100)
                    if row["kind"] == "kernel"
                    and row.get("metadata", {}).get("generation", 0) > 0
                ]
            }
        elif verb == "status":
            result = saved(service, sid, args["kernelId"])
        else:
            if session.get("ownership", {}).get("status") in {
                "blocked",
                "yielding",
                "yielded",
                "taking-over",
            }:
                raise ValueError("This conversation is read-only on this host")
            if verb != "create":
                record = saved(service, sid, args["kernelId"])
                if record["operationState"] == "outcome_unknown":
                    raise ValueError(
                        "The original kernel is unavailable; create a new kernel explicitly"
                    )
                if record["generation"] != args["generation"]:
                    raise ValueError("Kernel generation changed; no code was submitted")
            if verb == "execute":
                for question_id in args.get("questionIds", []):
                    if not hasattr(service, "questions"):
                        raise ValueError("Required question answers are unavailable")
                    service.questions.answer_for_dependency(sid, question_id)
            if service.runtime is None:
                raise ValueError("A mounted conversation runtime is required")
            if verb == "create" and service.management is not None:
                # Explicit creation may start a fresh owner after Stop. Existing
                # generation actions must never restart/replay a lost kernel.
                await service.management.ensure_runtime(session)
            payload = {key: value for key, value in args.items() if key != "sessionId"}
            response = await service.runtime.control(
                sid, action, {**payload, "actor": origin}
            )
            result = response.get("result", response)
            if not result.get("success"):
                raise ValueError(
                    result.get("error", {}).get("message", "Computation control denied")
                )
            result = result["output"]
        return {"accepted": True, "result": result, "effects": []}
    except (TypeError, ValueError, KeyError, RuntimeError) as exc:
        raise AppError(str(exc), 409) from exc
