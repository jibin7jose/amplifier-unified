"""Host ownership/actions for portable durable operation evidence."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json

from amplifier_operations import OperationJournal
from amplifier_operations.journal import ACTIVE


def definitions():
    from .action_schema import schema, string

    common = {"sessionId": string(200), "id": string(200)}
    read = {
        **common,
        "cursor": {"type": "integer", "minimum": 0},
        "maxBytes": {"type": "integer", "minimum": 4096, "maximum": 100000},
    }
    return {
        "operations.submit": (
            "Start an owned command through the existing shell approval path. Keep requestId stable when retrying; unknown requests never replay. questionIds binds only work depending on those exact answers.",
            schema({"sessionId": string(200), "requestId": string(200), "command": string(65536),
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 3600},
                    "pty": {"type": "boolean"},
                    "questionIds": {"type": "array", "maxItems": 32, "uniqueItems": True,
                                    "items": {"type": "string", "minLength": 1, "maxLength": 200}}},
                   ["sessionId", "requestId", "command"]),
        ),
        "operations.write": (
            "Write input to the exact owned process through existing shell approvals. Raw input also requires the trusted mount policy. Keep requestId stable; uncertain input is never automatically retried.",
            schema({**common, "requestId": string(200), "stdin": string(65536), "closeStdin": {"type": "boolean"}},
                   ["sessionId", "id", "requestId"]),
        ),
        "operations.request": (
            "Read the saved admission or input receipt without replaying it.",
            schema({"sessionId": string(200), "requestId": string(200)}, ["sessionId", "requestId"]),
        ),
        "operations.list": (
            "Read observed operations for a conversation without selecting or running it. Existing Smart Tool receipts and workers share this view.",
            schema(
                {
                    "sessionId": string(200),
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                ["sessionId"],
            ),
        ),
        "operations.status": (
            "Read durable operation status and actual completion evidence; unknown outcomes are never inferred as success.",
            schema(common, ["sessionId", "id"]),
        ),
        "operations.read": (
            "Read bounded durable output by cursor. A gap explicitly identifies missing or expired evidence.",
            schema(read, ["sessionId", "id"]),
        ),
        "operations.wait": (
            "Wait for an actual change to the observed operation. Does not start, resume, cancel or replay work.",
            schema(
                {
                    **read,
                    "afterRevision": string(200),
                    "waitMs": {"type": "integer", "minimum": 0, "maximum": 60000},
                },
                ["sessionId", "id"],
            ),
        ),
        "operations.cancel": (
            "Request cancellation of an owned live process through existing shell hooks/approvals. A request is not proof of cancellation; read the observed final state.",
            schema(common, ["sessionId", "id"]),
        ),
    }


class Operations:
    def __init__(self, service):
        self.service = service
        self.journal = OperationJournal(service.data_dir / "operations.sqlite3")
        self.journal.recover()
        from .operation_requests import OperationRequests
        self.requests = OperationRequests(self.journal)
        self.changed = asyncio.Event()
        self.closed = False
        self.pending = set()
        self.adapters = {}

    def notify(self):
        previous = self.changed
        self.changed = asyncio.Event()
        previous.set()

    async def observe(self, session_id, runtime_session_id, event):
        # session_id comes from the authenticated worker bridge, never the tool.
        self.service._session(session_id)
        if (event.get("source"), event.get("kind")) not in {
            ("tool-bash", "process"),
            ("computation", "kernel"),
            ("computation", "kernel-cell"),
        }:
            raise ValueError(
                "No host adapter is registered for this operation producer"
            )
        if self.closed:
            raise ValueError("Operation journal is closing")
        if (
            event.get("source") == "computation"
            and event.get("kind") == "kernel-cell"
            and event.get("phase") == "started"
        ):
            for question_id in event.get("metadata", {}).get("questionIds", []):
                if not hasattr(self.service, "questions"):
                    raise ValueError("Required question answers are unavailable")
                self.service.questions.answer_for_dependency(session_id, question_id)
        task = asyncio.create_task(
            asyncio.to_thread(
                self.journal.ingest, session_id, runtime_session_id, event
            )
        )
        self.pending.add(task)

        def settled(done):
            self.pending.discard(done)
            # A timed-out producer may have stopped awaiting this shielded write.
            # Retrieve its exception without logging command output or secrets.
            if not done.cancelled():
                done.exception()
            self.notify()

        task.add_done_callback(settled)
        value, changed = await asyncio.shield(task)
        return {
            "id": value["id"],
            "revision": value["revision"],
            "duplicate": not changed,
        }

    def register_source(self, name, list_records, read_record):
        """Register host-owned read adapters; never an agent execution callback.

        Callbacks return the common operation shape with fully prefixed IDs,
        bound sessionId, state, revision and evidence. The original source stays
        authoritative and calls notify() only when that source changes.
        """
        if not name or ":" in name or name in {"process", "smart-tool", "worker"}:
            raise ValueError("Invalid operation adapter name")
        if name in self.adapters:
            raise ValueError("Operation adapter already registered")
        self.adapters[name] = (list_records, read_record)

    @staticmethod
    def adapted(name, session_id, value):
        if value is None:
            raise ValueError("Operation not found in this conversation")
        if value.get("sessionId") != session_id or not value.get("id", "").startswith(
            name + ":"
        ):
            raise ValueError("Operation adapter returned a different owner or identity")
        return copy.deepcopy(value)

    @staticmethod
    def projection(source, session_id, value):
        state = value.get("status", "outcome_unknown")
        state = {
            "interrupted": "outcome_unknown",
            "stopped": "cancelled",
            "error": "failed",
            "starting": "queued",
            "working": "running",
            "idle": "waiting_input",
            "done": "completed",
        }.get(state, state)
        if state not in ACTIVE | {
            "completed",
            "failed",
            "cancelled",
            "outcome_unknown",
            "waiting_input",
        }:
            state = "outcome_unknown"
        result = {
            "id": source + ":" + value["id"],
            "sessionId": session_id,
            "source": source,
            "sourceId": value["id"],
            "kind": source,
            "state": state,
            "createdAt": value.get("createdAt", value.get("startedAt", 0)),
            "updatedAt": value.get("updatedAt", value.get("endedAt", 0)),
            "outputComplete": None,
            "captureComplete": None,
            "controlAvailable": False,
            "returncode": None,
            "evidence": {
                key: copy.deepcopy(value[key])
                for key in ("result", "error", "report", "action", "name")
                if key in value
            },
        }
        result["revision"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, default=str).encode()
        ).hexdigest()
        return result

    def sources(self, session_id):
        service = self.service
        session = service._session(session_id)
        values = []
        if service.smart_tools:
            for (data,) in service.db.execute(
                "SELECT value FROM smart_tool_operations WHERE json_extract(value,'$.target.sessionId')=? ORDER BY rowid DESC LIMIT 100",
                (session_id,),
            ):
                row = json.loads(data)
                values.append(self.projection("smart-tool", session_id, row))
        values.extend(
            self.projection("worker", session_id, row)
            for row in session.get("workers", [])
        )
        for name, (listing, _) in self.adapters.items():
            values.extend(
                self.adapted(name, session_id, row) for row in listing(session_id)
            )
        return values

    def status(self, session_id, identity):
        self.service._session(session_id, hydrate=False)
        prefix = identity.partition(":")[0]
        if prefix in self.adapters:
            _, reading = self.adapters[prefix]
            value = self.adapted(prefix, session_id, reading(session_id, identity))
            if value["id"] != identity:
                raise ValueError("Operation adapter returned a different identity")
            return value
        if identity.startswith("smart-tool:"):
            if not self.service.smart_tools:
                raise ValueError("Operation not found in this conversation")
            row = self.service.db.execute(
                "SELECT value FROM smart_tool_operations WHERE id=?",
                (identity.removeprefix("smart-tool:"),),
            ).fetchone()
            value = json.loads(row[0]) if row else None
            if value is None or value.get("target", {}).get("sessionId") != session_id:
                raise ValueError("Operation not found in this conversation")
            return self.projection("smart-tool", session_id, value)
        if identity.startswith("worker:"):
            value = next(
                (row for row in self.sources(session_id) if row["id"] == identity), None
            )
            if value is None:
                raise ValueError("Operation not found in this conversation")
            return value
        value = self.journal.status(session_id, identity)
        value["revision"] = str(value["revision"])
        if value["source"] == "kernel":
            value["controlAvailable"] = False
        return value

    def read(self, args):
        value = self.status(args["sessionId"], args["id"])
        if value["source"] not in {"process", "kernel", "kernel-cell"}:
            # Existing receipts stay authoritative; inspect their evidence rather
            # than synthesizing a second streaming-output log.
            return {
                **value,
                "chunks": [],
                "nextCursor": 0,
                "hasMore": False,
                "cursorGap": False,
            }
        result = self.journal.read(
            args["sessionId"],
            args["id"],
            args.get("cursor", 0),
            args.get("maxBytes", 16384),
        )
        result["revision"] = str(result["revision"])
        if result["source"] == "kernel":
            result["controlAvailable"] = False
        return result

    async def dispatch(self, action, args, origin, command_id=None):
        from .errors import AppError

        sid = args["sessionId"]
        self.service._session(sid)
        try:
            if action == "operations.list":
                values = self.journal.list(sid, args.get("limit", 50)) + self.sources(
                    sid
                )
                values.sort(key=lambda row: row.get("createdAt") or 0, reverse=True)
                result = {
                    "operations": values[: args.get("limit", 50)],
                    "requests": self.requests.list(sid),
                    "capabilities": {
                        "durableProcessOutput": True,
                        "pty": None,
                        "ptyRequiresHostOptIn": True,
                        "nativeWindows": False,
                    },
                }
            elif action in {"operations.submit", "operations.write"}:
                result = await self.mutate(action, args, origin)
            elif action == "operations.request":
                result = self.requests.read(sid, args["requestId"])
            elif action == "operations.cancel":
                result = await self.cancel(sid, args["id"], origin)
            elif action == "operations.wait":
                # Capture the event before reading the revision to avoid a
                # change slipping between snapshot and subscription.
                deadline = (
                    asyncio.get_running_loop().time() + args.get("waitMs", 1000) / 1000
                )
                while True:
                    changed = self.changed
                    result = self.read(args)
                    if (
                        str(result["revision"]) != args.get("afterRevision")
                        or self.closed
                    ):
                        break
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        await asyncio.wait_for(changed.wait(), remaining)
                    except TimeoutError:
                        break
            elif action == "operations.status":
                result = self.status(sid, args["id"])
            else:
                result = self.read(args)
            return {"accepted": True, "result": result, "effects": []}
        except (TypeError, ValueError, RuntimeError) as exc:
            raise AppError(str(exc), 409) from exc

    async def mutate(self, action, args, origin, *, integration_id=None):
        sid = args["sessionId"]
        session = self.service._session(sid)
        existing = self.requests.existing(sid, action, args, origin)
        if existing is not None:
            return existing
        if session.get("ownership", {}).get("status") in {"blocked", "yielding", "yielded", "taking-over"}:
            raise ValueError("This conversation is read-only on this host")
        if not args["requestId"].strip():
            raise ValueError("A stable requestId is required")
        runtime = self.service.runtime
        if runtime is None:
            raise ValueError("A conversation runtime is required")
        if action == "operations.submit":
            for question_id in args.get("questionIds", []):
                self.service.questions.answer_for_dependency(sid, question_id)
            payload = {"command": args["command"], "questionIds": args.get("questionIds", []),
                       **{key: args[key] for key in ("timeout", "pty") if key in args}}
            if integration_id is not None:
                payload['integrationId'] = integration_id
        else:
            value = self.status(sid, args["id"])
            if value["source"] != "process" or not value.get("controlAvailable") or value["state"] not in ACTIVE:
                raise ValueError("The original process is unavailable; input was not sent")
            payload = {"processId": value["sourceId"], "runtimeSessionId": value["runtimeSessionId"],
                       "ownerId": value["ownerId"], "operationId": args["id"],
                       "stdin": args.get("stdin", ""), "closeStdin": args.get("closeStdin", False)}
        receipt, fresh = self.requests.begin(sid, action, args, origin)
        if not fresh:
            return receipt
        self.notify()
        try:
            if action == "operations.submit" and self.service.management is not None:
                await self.service.management.ensure_runtime(session)
            if integration_id is not None:
                supports = getattr(runtime, 'integration_validation_supported', None)
                if not callable(supports) or await supports(sid) is not True:
                    return self.requests.finish(receipt, 'rejected', error={
                        'message': 'This runtime cannot enforce integration admission. No validation command was submitted.'})
            response = await runtime.control(sid, action, {**payload, "actor": origin})
            result = response.get("result", response)
            if result.get("success") is False:
                return self.requests.finish(receipt, "rejected", error=result.get("error", {}))
            output = result.get("output", {})
            if result.get("success") is not True or not isinstance(output, dict) or not output.get("process_id"):
                raise ValueError("The runtime did not return confirmed process admission evidence")
            identity = output.get("process_id") or args.get("id")
            return self.requests.finish(receipt, "accepted", operationId=identity,
                callId=response.get("callId"),
                admission={key: output[key] for key in ("process_id", "owner_id", "state", "returncode", "pty", "stdin_closed", "eof_semantics") if key in output})
        except BaseException:
            self.requests.finish(receipt, "outcome_unknown", message="The host did not confirm this request. It was not replayed; inspect saved operation evidence before deciding what to do next.")
            raise
        finally:
            self.notify()

    async def cancel(self, session_id, identity, origin):
        value = self.status(session_id, identity)
        if value["state"] not in ACTIVE:
            return value
        if value["source"] == "kernel-cell":
            from .computation import dispatch

            metadata = value["metadata"]
            return await dispatch(
                self.service,
                "kernels.interrupt",
                {
                    "sessionId": session_id,
                    "kernelId": metadata["kernelId"],
                    "generation": metadata["generation"],
                    "cellId": identity,
                },
                origin,
            )
        if value["source"] != "process":
            raise ValueError(
                "Cancellation is unavailable for this receipt adapter; use the existing worker or Smart Tool controls"
            )
        if value["state"] not in ACTIVE:
            return value
        session = self.service._session(session_id)
        if session.get("ownership", {}).get("status") in {
            "blocked",
            "yielding",
            "yielded",
            "taking-over",
        }:
            raise ValueError("This conversation is read-only on this host")
        runtime = self.service.runtime
        if runtime is None:
            raise ValueError(
                "The owning runtime is unavailable; cancellation was not attempted"
            )
        # Never mount/resume a missing runtime just to cancel historical work.
        response = await runtime.control(
            session_id,
            "operations.cancel",
            {
                "processId": value["sourceId"],
                "runtimeSessionId": value["runtimeSessionId"],
                "ownerId": value["ownerId"],
                "operationId": identity,
                "actor": origin,
            },
        )
        result = response.get("result", response) if isinstance(response, dict) else {}
        if result.get("success") is False:
            raise ValueError(
                result.get("error", {}).get("message", "Cancellation denied")
            )
        return {**self.status(session_id, identity), "request": response}

    def interrupted(self, session_id):
        self.journal.recover(
            session_id,
            "The owning runtime ended; outcome was not observed and work was not replayed.",
        )
        self.notify()

    async def close(self):
        self.closed = True
        self.notify()
        await asyncio.gather(*self.pending, return_exceptions=True)
        self.journal.close()
