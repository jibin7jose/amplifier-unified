"""Experimental trusted attachment to the existing composite execution owner.

AppService and RuntimeManager retain lifecycle, authority, history and accounting.
This adapter owns only connection observations and unsettled response waiters.
It is not a remotely authenticated service or an independent native runtime.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from .runtime_event_batches import EventSink, MAX_EVENTS
from .runtime_protocol import MAX_MESSAGE_BYTES
from .errors import AppError


class Projection:
    """One bounded drain per active input; backpressure cannot block the owner."""
    def __init__(self, observe):
        self.observe = observe
        self.queue = asyncio.Queue(maxsize=MAX_EVENTS)
        self.bytes = 0
        self.failed = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self.drain())

    def fail(self, message):
        if not self.failed.done():
            self.failed.set_result(message)

    def offer(self, event):
        if self.failed.done():
            return
        try:
            size = len(json.dumps(event, ensure_ascii=False).encode())
        except (TypeError, ValueError):
            self.fail("Observation could not be encoded; inspect retained owner state.")
            return
        if self.queue.full() or self.bytes + size > MAX_MESSAGE_BYTES:
            self.fail("Observer backlog exceeded the native frame/batch bound; native work may continue. Inspect retained owner state.")
            return
        self.bytes += size
        self.queue.put_nowait((event, size))

    async def drain(self):
        while True:
            event, size = await self.queue.get()
            try:
                await self.observe(event)
            except Exception:
                self.fail("Observation delivery failed; native work may continue. Inspect retained owner state.")
            finally:
                self.bytes -= size
                self.queue.task_done()

    async def finish(self):
        joined = asyncio.create_task(self.queue.join())
        try:
            await asyncio.wait({joined, self.failed}, return_when=asyncio.FIRST_COMPLETED)
            if self.failed.done():
                raise ValueError(self.failed.result())
        finally:
            joined.cancel()
            await asyncio.gather(joined, return_exceptions=True)

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.failed.cancel()


class ExecutionOwner:
    def __init__(self, service, *, workspace, bundle, origin):
        self.service = service
        self.workspace = Path(workspace).resolve(strict=True)
        self.bundle = bundle
        # Trusted construction supplies the principal; peer input cannot change it.
        self.origin = origin
        self.attached = set()
        self.pending = {}
        self.original_events = service.on_runtime_event
        service.on_runtime_event = EventSink(self.event, self.events)

    async def create(self, cwd):
        if Path(cwd).resolve(strict=True) != self.workspace:
            raise ValueError("This execution owner is bound to a different workspace")
        receipt = await self.service.dispatch("session.create", {
            "workspace": str(self.workspace), "bundle": self.bundle, "select": False,
        }, origin=self.origin, include_state=False)
        identity = receipt["result"]["sessionId"]
        self.attached.add(identity)
        return identity

    def require_attachment(self, identity):
        if identity not in self.attached:
            raise ValueError("Execution is not attached to this connection")

    async def prompt(self, identity, input_id, text, on_event):
        self.require_attachment(identity)
        if identity in self.pending:
            raise ValueError("This execution already has an unsettled input")
        delivery = Projection(on_event)
        row = {"input": input_id, "delivery": delivery, "stop": None,
               "stopped": asyncio.get_running_loop().create_future(),
               "approval_ids": set(), "approval_task": None}
        self.pending[identity] = row
        waiter = None
        try:
            # The owner may already be executing while its admission ACK waits.
            # Detaching this observer must not cancel that dispatch or its work.
            admission = self.service._task(self.service.dispatch("conversation.send", {
                "sessionId": identity, "text": text,
            }, origin=self.origin, command_id=input_id, include_state=False))
            admission.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            done, _ = await asyncio.wait({admission, delivery.failed}, return_when=asyncio.FIRST_COMPLETED)
            if delivery.failed in done:
                raise ValueError(delivery.failed.result())
            receipt = admission.result()
            if receipt.get("duplicate"):
                raise ValueError("Input was already admitted; inspect retained owner state before retrying")
            waiter = asyncio.create_task(self.service.wait_for_response(identity, input_id, timeout=None))
            done, _ = await asyncio.wait({waiter, delivery.failed}, return_when=asyncio.FIRST_COMPLETED)
            if delivery.failed in done:
                raise ValueError(delivery.failed.result())
            try:
                result = waiter.result()
                outcome = {"outcome": "completed", "text": result.get("text", "")}
            except AppError:
                # Detached/failed is not cancellation. Require this exact Stop
                # command and the owner's post-runtime-stop observation.
                if row["stop"] is None:
                    raise
                await asyncio.wait({row["stopped"], delivery.failed}, return_when=asyncio.FIRST_COMPLETED)
                if delivery.failed.done():
                    raise ValueError(delivery.failed.result())
                stop_error = row["stopped"].result()
                if stop_error:
                    raise ValueError(stop_error)
                outcome = {"outcome": "cancelled"}
            await delivery.finish()
            return outcome
        finally:
            if waiter is not None:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            if self.pending.get(identity) is row:
                del self.pending[identity]
            row["stopped"].cancel()
            if row["approval_task"] is not None:
                row["approval_task"].cancel()
                await asyncio.gather(row["approval_task"], return_exceptions=True)
            await delivery.close()

    async def cancel(self, identity):
        self.require_attachment(identity)
        row = self.pending.get(identity)
        if row is None or row["stop"] is not None:
            return
        command = str(uuid.uuid4())
        row["stop"] = command
        try:
            await self.service.dispatch("conversation.stop", {"sessionId": identity},
                                        origin=self.origin, command_id=command, include_state=False)
        except BaseException:
            if row["stop"] == command:
                row["stop"] = None
                row["delivery"].fail("Stop did not settle; inspect retained owner state before retrying.")
            raise

    async def release(self, identity):
        """Detach observation only; neither Stop nor history deletion is implied."""
        self.attached.discard(identity)
        if identity in self.pending:
            self.pending[identity]["delivery"].fail("Observation detached; native work may continue. Inspect retained owner state.")

    async def event(self, kind, payload):
        await self.original_events(kind, payload)
        await self.observe(kind, payload)

    async def events(self, rows):
        await self.original_events.batch(rows)
        for kind, payload in rows:
            await self.observe(kind, payload)

    async def approvals(self, identity, row):
        try:
            while row["approval_ids"]:
                identities = row["approval_ids"]
                row["approval_ids"] = set()
                # Full remains the existing owner's decision. Joining its actual
                # completion avoids mistaking a pending worker ACK for Ask.
                task = self.service._task(self.service.access_mode.settle(identity, wait=True))
                await asyncio.shield(task)
                session = self.service._session(identity, hydrate=False)
                if any(item.get("id") in identities and item.get("status") == "pending"
                       for item in session.get("approvals", [])):
                    row["delivery"].fail("Interactive approval is unavailable on this attachment. The owner retains the pending request; inspect it before retrying. No decision or work was replayed.")
                    return
        except Exception:
            row["delivery"].fail("Approval settlement is unresolved; inspect retained owner state before retrying.")
        finally:
            row["approval_task"] = None

    async def observe(self, kind, payload):
        identity = payload.get("sessionId")
        row = self.pending.get(identity)
        if row is None or identity not in self.attached:
            return
        input_id = row["input"]
        if kind == "approval.requested":
            row["approval_ids"].add(payload.get("id"))
            if row["approval_task"] is None:
                row["approval_task"] = asyncio.create_task(self.approvals(identity, row))
            return
        if kind in {"assistant.delta", "assistant.message"}:
            if payload.get("inputId") != input_id:
                return
            event = {"type": kind, "text": payload.get("text", "")}
        elif kind == "execution.event":
            if payload.get("turnId") != input_id:
                return
            # Model accounting was already persisted by the canonical owner.
            # Only public tool observations are useful to the attached client.
            if payload.get("kind") not in {"tool", "llm"}:
                return
            event = {"type": kind, "event": {key: payload[key] for key in
                ("id", "sessionId", "toolCallId", "kind", "phase", "label", "input", "output", "lifecycle")
                if key in payload and (key not in {"input", "output"} or payload.get("kind") == "tool")}}
        elif kind == "runtime.error":
            current = self.service._session(identity, hydrate=False)
            if (row["stop"] is not None and not row["stopped"].done()
                    and current.get("status") == "error"
                    and (current.get("lastInterruption") or {}).get("commandId") == row["stop"]):
                # A rejected/stale event must not settle this wait. Completion
                # still wins in the canonical waiter before this result is used.
                row["stopped"].set_result("Stop did not settle; inspect retained owner state before retrying.")
            return
        elif kind == "runtime.status" and payload.get("status") == "stopped":
            current = self.service._session(identity, hydrate=False)
            if (row["stop"] is not None and not row["stopped"].done()
                    and current.get("status") == "stopped"):
                if (current.get("lastInterruption") or {}).get("commandId") == row["stop"]:
                    row["stopped"].set_result(None)
                else:
                    row["delivery"].fail("A different interruption ended this turn; inspect retained owner state.")
            return
        else:
            return
        row["delivery"].offer(event)

    async def close(self):
        """Owned service shutdown, distinct from releasing one attachment."""
        await self.service.close()
        for row in self.pending.values():
            row["delivery"].fail("Execution owner closed; inspect retained state before retrying")
        self.service.on_runtime_event = self.original_events
