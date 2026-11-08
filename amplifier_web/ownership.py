"""Unified's lifecycle policy on Foundation's session release mechanism."""

from __future__ import annotations

import asyncio
import os

_WINDOWS = os.name == "nt"


class WorkerOwnership:
    def __init__(self, worker, publish):
        self.worker = worker
        self.publish = publish
        self.registration = None
        self.yielding = False

    async def register(self):
        if _WINDOWS:
            # Exclusive acquisition/release belongs to SharedSessionStore and
            # remains held. No POSIX socket registration, checkpoint/fence
            # mutation or claimed handoff support on the Windows first slice.
            self.publish({"type": "runtime.capability", "capability": "session.handoff",
                          "status": "unavailable", "platform": "windows",
                          "detail": "Stop the current owner before opening this conversation elsewhere."})
            return
        from amplifier_foundation.session import register_release_handler

        self.registration = await register_release_handler(
            self.worker.shared_handle, prepare_release=self.prepare
        )

    async def prepare(self, request):
        from amplifier_foundation.session import CannotRelease, ReadyToRelease
        from amplifier_module_loop_live.runtime import Input

        publish = self.publish
        worker = self.worker
        async with worker.command_lock:
            held = worker.shared_handle
            if held is None or held.owner["acquisition_id"] != request.acquisition_id:
                return CannotRelease(
                    "owner_changed", "This runtime no longer owns that acquisition."
                )
            self.yielding = True
            request.report_progress("draining")
            publish(
                {
                    "type": "runtime.ownership",
                    "status": "yielding",
                    "source": request.requester_app,
                }
            )
            token = worker.activation_gate.bind(worker.activation)
            try:
                if worker.runtime and not worker.runtime.closed:
                    await worker.runtime.submit(Input("stop", target="cancel"))
            finally:
                worker.activation_gate.reset(token)
        try:
            if worker.execution:
                await asyncio.shield(worker.execution)
            if (
                worker.naming
                and worker.naming.pending
                and not worker.naming.pending.done()
            ):
                worker.naming.pending.cancel()
                await asyncio.gather(worker.naming.pending, return_exceptions=True)
            async with worker.command_lock:
                token = worker.activation_gate.bind(worker.activation)
                try:
                    request.report_progress("persisting")
                    checkpoint = worker.session.coordinator.get_capability(
                        "live.checkpoint"
                    )
                    if checkpoint is None:
                        raise RuntimeError("Session persistence is unavailable")
                    await checkpoint("cancelled")
                    if worker.controls:
                        await worker.controls.close()
                    await worker.session.cleanup()
                    worker.activation_gate.release(worker.activation)
                    worker.session = worker.runtime = worker.controls = None
                    worker.parked = True
                finally:
                    worker.activation_gate.reset(token)
            # Foundation releases after this callback returns. Publish success
            # only after its task has completed and the handle is inactive.
            asyncio.create_task(self._released(held, request.requester_app))
            return ReadyToRelease()
        except Exception as exc:
            publish(
                {
                    "type": "runtime.ownership",
                    "status": "yield-failed",
                    "source": request.requester_app,
                    "detail": f"Unable to finish safe shutdown ({type(exc).__name__}).",
                }
            )
            return CannotRelease(
                "shutdown_failed",
                "Unified could not finish saving and cleanup; ownership is retained.",
            )

    async def _released(self, held, source):
        publish = self.publish
        await asyncio.shield(self.registration.pending)
        if not held.active:
            if self.worker.shared_handle is held:
                self.worker.shared_handle = None
            publish(
                {"type": "runtime.ownership", "status": "yielded", "source": source}
            )
