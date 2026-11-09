"""Process-isolated, configured Amplifier sessions and their public event bridge.

The web server never changes cwd or imports provider SDKs. Each worker owns the
app-owned Foundation lifecycle and standalone Amplifier configuration.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager, nullcontext
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import uuid
from typing import Any, Awaitable, Callable

from .runtime_protocol import MAX_MESSAGE_BYTES, encode_message
from .performance_diagnostics import measure, measured

_CURRENT_GENERATION = object()
_LOG = logging.getLogger(__name__)
_WINDOWS = os.name == 'nt'
_WINDOWS_STOP_SECONDS = 15.0


@dataclass
class _WorkerProcessObservation:
    """Read-only-to-consumers lifecycle facts, not an asyncio Process facade.

    Existing host readers use only pid/returncode. On Windows pid remains unset
    until the worker handshake verifies it, never the guard/uv/redirector PID.
    A non-None returncode means the entire owned job
    and I/O have settled; 'unknown' explicitly preserves an unreported exit code.
    Signalling, waiting and streams belong to the separately retained job owner.
    """
    pid: int | None = None
    returncode: int | str | None = None


class _WindowsWorkerOwner:
    def __init__(self, job):
        self.job = job
        self.process = _WorkerProcessObservation()
        self.stdout = _BoundedWorkerLines(job.stdout)
        self.result = None
        self.completion = asyncio.create_task(self._observe())
        self.completion.add_done_callback(lambda task: None if task.cancelled() else task.exception())

    async def _observe(self):
        self.result = await self.job.wait()
        self._record(self.result)
        return self.result

    def _record(self, result):
        if result.active_processes != 0 or not self.job.tree_empty or not self.job.io_finalized:
            raise RuntimeError('Windows worker cleanup is unconfirmed; ownership retained')
        self.result = result
        self.process.returncode = (result.payload_returncode
                                   if result.payload_returncode is not None else 'unknown')

    async def wait(self):
        await asyncio.shield(self.completion)
        return self.process.returncode

    async def identify(self, timeout):
        line = await asyncio.wait_for(self.stdout.readline(), timeout)
        identity = json.loads(line)
        if (not isinstance(identity, dict)
                or set(identity) != {'type', 'pid', 'created'}
                or identity['type'] != 'runtime.identity'):
            raise RuntimeError('Windows worker identity handshake is missing')
        if identity['pid'] == self.job.identity.guard_pid:
            raise RuntimeError('Windows guard cannot identify as the conversation worker')
        self.job.verify_member(identity['pid'], identity['created'])
        self.process.pid = identity['pid']
        self.created = identity['created']

    async def close(self):
        self._record(await self.job.close())
        # A forced close can settle before the monitor's next poll.
        await asyncio.shield(self.completion)


class _BoundedWorkerLines:
    """Protocol lines independent of the job primitive's 64 KiB pipe watermark."""
    def __init__(self, stream, limit=MAX_MESSAGE_BYTES):
        self.stream, self.limit, self.buffer = stream, limit, bytearray()

    async def readline(self):
        start = 0
        while True:
            end = self.buffer.find(b'\n', start)
            if end >= 0:
                line = bytes(self.buffer[:end + 1])
                del self.buffer[:end + 1]
                return line
            if len(self.buffer) >= self.limit:
                raise ValueError('Runtime message exceeds the transport limit')
            start = len(self.buffer)
            chunk = await self.stream.read(min(64 * 1024, self.limit - len(self.buffer)))
            if not chunk:
                line = bytes(self.buffer)
                self.buffer.clear()
                return line
            self.buffer.extend(chunk)


def _worker_pipes(row):
    owner = row.get('windows_owner')
    return owner.job if owner is not None else row['process']


async def _wait_worker(row):
    owner = row.get('windows_owner')
    return await (owner.wait() if owner is not None else row['process'].wait())

Emitter = Callable[[str, dict], Awaitable[None]]

class RuntimeOperationPending(RuntimeError):
    """A written operation has no reply yet; its outcome remains uncertain."""

    def __init__(self, operation):
        self.operation = operation
        super().__init__('The runtime operation has not returned yet. It may still be running; do not automatically repeat it.')


class RuntimeStartupError(RuntimeError):
    """This attempt failed preparation before the worker submitted its input."""


class RuntimeHostClosingError(RuntimeError):
    """The host deliberately closed admission; outstanding effects stay unknown."""

    def __init__(self):
        super().__init__('The runtime host is closing. Unconfirmed work was not replayed.')


class RuntimePreparationCancelled(asyncio.CancelledError):
    """The outer task was cancelled before native input admission."""


async def _terminate_unregistered(proc, windows_owner=None):
    """Reap a spawned preparation process before releasing its cleanup owner."""
    async def stop():
        if windows_owner is not None:
            await windows_owner.close()
            return
        if proc.returncode is not None:
            return
        try:
            if os.name != 'nt':
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except TimeoutError:
            try:
                if os.name != 'nt':
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
    task = asyncio.create_task(stop())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


class SessionInUseError(RuntimeError):
    """A definite rejected admission, not an uncertain execution failure."""

    def __init__(self, owner=None):
        self.owner = owner if isinstance(owner, dict) else {}
        from .session_ownership import owner_label
        super().__init__(
            f"This conversation is in use by {owner_label(self.owner)}. "
            "Use the conversation's Continue here action. Your draft has been kept."
        )


class HandoffNotStartedError(SessionInUseError):
    """The initial writer check refused handoff before any release effect."""


def _worker_error(data):
    if data.get("code") == "session_busy":
        return SessionInUseError(data.get("owner"))
    if data.get("code") == "module_load_failed":
        from .module_failures import ConfiguredModuleError
        return ConfiguredModuleError(data.get("moduleFailures", []))
    if data.get("code") == "worker_preparation_failed":
        from .module_failures import ConfiguredModuleError
        from .worker_diagnostics import receipt_path
        error = RuntimeStartupError('The conversation worker could not start. This attempt did not send your message.')
        from .session_health import failure_details
        detail = failure_details(data.get('error', ''))
        if detail['category'] == 'provider_selection':
            error.args = (f"ProviderSelectionError: {detail['summary']} {detail['guidance']}",)
        elif detail['category'] == 'bundle_configuration':
            error.args = (f"{detail['errorType']}: {detail['summary']} {detail['guidance']}",)
        error.failures = ConfiguredModuleError(data.get('moduleFailures', [])).failures
        if error.failures:
            error.args = (str(error) + '\n\n' + str(ConfiguredModuleError(error.failures)),)
        diagnostic = receipt_path(data.get('diagnosticReceipt'))
        if diagnostic:
            error.diagnostic_path = diagnostic
        return error
    return RuntimeError(data.get("error", "Amplifier runtime failed"))


def normalize_event(event: dict, session_id: str, input_id: str | None = None):
    """Only publish useful runtime events; keep analysis/provider payloads private."""
    kind = event.get("type", "")
    base = {"sessionId": session_id}
    if kind == 'runtime.error' and 'terminalFailure' in event:
        from .support_diagnostics import terminal_exception_capture
        capture = terminal_exception_capture(event['terminalFailure'])
        return ('runtime.error', {**base, 'terminalFailure': capture,
                'error': 'The manager turn failed. Work was not replayed.'}) if capture else None
    if kind == 'runtime.bridge':
        from .support_diagnostics import event_detail
        return kind, {**base, **event_detail(event)}
    if kind == 'runtime.timing':
        from .performance_diagnostics import timing_metadata
        return kind, {**base, 'timing': timing_metadata(event.get('timing'))}
    if kind == 'image.generated':
        return kind, {**base, 'nodeId': event.get('nodeId'), 'receipt': event.get('receipt')}
    if kind == 'runtime.ownership':
        return kind, {**base, **{key: event[key] for key in ('status', 'source', 'detail') if key in event}}
    if kind in {'session.naming','session.naming.progress'}:
        return kind, {**base,**{key:event[key] for key in ('name','description','completedInputs','nameRevision') if key in event}}
    if kind == "execution.event":
        event = event.get("event", {})
        allowed = ("id", "revision", "producerId", "budgetRevision", "admittedAt", "parentId", "turnId", "sessionId", "rootSessionId", "kind", "phase", "label",
            "toolCallId", "provider", "model", "parentProvider", "routing", "runId", "startedAt", "endedAt", "usage", "summary", "input", "output", "error", "lifecycle", "failure", "liveObservation")
        public = {key:event[key] for key in allowed if key in event and (key not in {"input", "output", "error"} or event.get("kind") == "tool")}
        from .tool_observations import metadata as tool_metadata
        public.update(tool_metadata(event))
        if event.get('kind') == 'tool' and event.get('label') == 'todo' and 'statusPlan' in event:
            from .status_protocol.hook import accepted_plan
            public['statusPlan'] = accepted_plan({'todos': event['statusPlan']})
        if event.get('kind') == 'tool' and event.get('label') in {'image_generate', 'nano-banana'}:
            from .image_generation import metadata
            image = metadata(event.get('imageGeneration'))
            if image:
                public['imageGeneration'] = image
        if "routing" in public:
            from .host.model_selection import public_routing
            public["routing"] = public_routing(public["routing"])
        return "execution.event", public
    if kind == "runtime.activity":
        allowed = {"model", "processing", "waiting-workers", "tools", "retrying", "compacting"}
        phase = event.get("phase")
        if phase not in allowed:
            return None
        return "runtime.status", {**base, "status": "working", "activityOnly": True, "phase": phase,
            "detail": event.get("detail", "Working"), "activeWorkers": event.get("activeWorkers", 0),
            **{key:event[key] for key in ("retryAttempt", "retryMax") if key in event}}
    if kind == "assistant.delta":
        return "assistant.delta", {**base, "text": event.get("text", ""),
            "inputId": (event.get("input_ids") or [input_id])[-1],
            "generationId": event.get("generation_id"), "inputIds": event.get("input_ids", []),
            "requestId": event.get("request_id"), "blockIndex": event.get("block_index")}
    if kind == "assistant.message":
        from .assistant_messages import metadata
        return "assistant.message", {**base, "text": event.get("text", ""),
            "inputId": (event.get("input_ids") or [input_id])[-1],
            "generationId": event.get("generation_id"), "inputIds": event.get("input_ids", []),
            **metadata(event),
            **{key: event[key] for key in ("scheduled_monitor_input_id", "scheduled_monitor_only", "observation_input_id", "observation_id") if key in event}}
    if kind in {"generation.started", "generation.finished", "generation.failed", "generation.detached"}:
        identity = event.get("sessionId") or event.get("session_id") or session_id
        root = event.get("rootSessionId") or event.get("root_session_id") or session_id
        return "runtime.generation", {**base, "sessionId": identity, "rootSessionId": root, "event": kind,
            **{key: event[key] for key in ("generation_id", "input_ids", "initial_input_id", "interruptionRevision",
                "text", "nativeTerminal", "finalMessageRefs", "active_job_ids", "disposition", "error_type", "error_category", "error_code", "error_stage", "count_failure", "retryable", "accepted_input_ids", "scheduled_monitor_input_id", "scheduled_monitor_only", "observation_input_id", "observation_id") if key in event}}
    if kind == "collaboration.checkpoint":
        return "runtime.collaboration_checkpoint", {**base,
            "generation_id": event.get("generation_id"), "messageAnchors": event.get("messageAnchors", []),
            "sessionId": event.get("sessionId") or event.get("session_id") or session_id,
            "rootSessionId": event.get("rootSessionId") or event.get("root_session_id") or session_id}
    if kind in {"steering.sent", "steering.accepted", "steering.applied", "steering.pending", "steering.failed", "steering.held", "steering.unknown", "native.outcome_unknown"}:
        return "runtime.steering", {**base, "event": kind, **{key: event[key] for key in
            ("input_id", "response_id", "steer_id", "accepted", "reason", "execution_replayed",
             "target_generation_id", "disposition", "generation_id", "delivery") if key in event}}
    if kind.startswith("job."):
        statuses = {"queued": "queued", "returned": "completed", "failed": "error",
                    "cancelled": "cancelled", "cancel_requested": "stopping", "recovered": "interrupted"}
        return "worker.updated", {**base, "id": event.get("job_id"),
            "status": (statuses.get(event.get("status"), event.get("status")) or "interrupted") if kind == "job.recovered" else statuses.get(kind[4:]) or event.get("status") or "running",
            "callId": event.get("call_id"), "name": event.get("agent") or "Delegated work",
            "kind": "job", "event": kind, "updatedAt": event.get("time")}
    if kind == "worker.activity":
        return "worker.updated", {**base, "id": event.get("workerId"), "activityOnly": True,
            "phase": event.get("phase"), "detail": event.get("detail"),
            "callId": event.get("callId"), "name": event.get("name", "Worker"),
            "kind": "session", "updatedAt": event.get("time"),
            **{key:event[key] for key in ("runId", "retryAttempt", "retryMax") if key in event}}
    if kind == "child.updated":
        from .host.model_selection import public_routing
        return "worker.updated", {**base, "id": event.get("sessionId"),
            "status": event.get("status", "running"), "name": event.get("agent", "Worker"),
            "report": event.get("report", ""), "persistent": event.get("persistent", False),
            "callId": event.get("callId"), "kind": "session", "updatedAt": event.get("time"),
            **({"routing": public_routing(event["routing"])} if "routing" in event else {}),
            **{key: event[key] for key in ("runId", "parentSessionId", "reportId", "reports", "reportTruncated", "reportSourceChars", "reportWindow", "event") if key in event}}
    statuses = {"session.ready": "ready", "session.idle": "idle", "session.closed": "stopped",
                "input.delivered": "working"}
    if kind in statuses:
        return "runtime.status", {**base, "status": statuses[kind], "event": kind,
            **({"inputId": event["input_id"]} if "input_id" in event else {})}
    if kind in {"provider.error", "persistence.failed", "command.rejected", "native.error"}:
        return "runtime.error", {**base, "error": event.get("reason") or event.get("error_type") or kind,
                                  "event": kind, "errorType": event.get("error_type")}
    if kind in {"tool.pre", "tool.post", "tool.error"}:
        return "runtime.tool", {**base, "tool": event.get("tool"), "callId": event.get("call_id"), "phase": kind[5:]}
    return None


class RuntimeManager:
    def __init__(self, app_bridge=None, *, command=None, startup_timeout=600,
                 preparation_timeout=3600, progress_interval=5, retention=None, performance_diagnostics=None):
        self.app_bridge = app_bridge
        self.performance_diagnostics = performance_diagnostics
        self.command = command
        self.startup_timeout = startup_timeout
        # Qualification has several independently bounded 900s install/probe
        # phases. Worker-ready time starts only after that preparation finishes.
        self.preparation_timeout = preparation_timeout
        self.progress_interval = progress_interval
        self.workers: dict[str, dict] = {}
        self._preparations: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._closed = False
        self._retired = {}
        self._execution_state = None
        self._configuration_revision = lambda: 0
        self._input_configuration = None
        self.home = None
        from .runtime_retention import WorkerRetention
        self.retention = WorkerRetention(self, retention)

    def bind_execution_state(self, read):
        """Host-owned synchronous authority; never supplied by a model command."""
        self._execution_state = read

    def bind_configuration_revision(self, read):
        """Capture host configuration provenance before asynchronous preparation."""
        self._configuration_revision = read

    def bind_input_configuration(self, prepare):
        """Host-only next-message validation; never invoked by reads or warmup."""
        self._input_configuration = prepare

    def _check_execution(self, sid, session=None, *, allow_fenced=False):
        if self._execution_state is None:
            return
        current = self._execution_state(sid)
        if current.get('hostMatches') is False and not allow_fenced:
            raise ValueError('The saved execution host differs from this local host. Cross-host execution is unavailable.')
        if current['fenced'] and not allow_fenced:
            raise ValueError('The task execution handoff is pending or unknown. Inspect and reconcile it before starting more work.')
        if session is not None and (
            session.get('executionRevision', 0) != current['revision']
            or (session.get('workingDirectory') or session.get('workspace')) != current['directory']
        ):
            raise ValueError('The task execution folder changed before runtime admission. Retry from the current task state; no input was replayed.')
        return current

    def pending_session_ids(self):
        return sorted({sid for sid, lock in self._locks.items() if lock.locked()} | {
            sid for sid, row in self.workers.items()
            if row.get("inflight") and row["process"].returncode is None})

    def has_pending_operations(self):
        """Preparing a worker or waiting for its reply must defer host updates."""
        return any(lock.locked() for lock in self._locks.values()) or any(
            row.get("inflight") and row["process"].returncode is None
            for row in self.workers.values()
        )

    def promote_generation(self, generation, *, protected=None):
        """A host promotion changes future starts, never mounted code in place.

        Old workers remain leased until their own park/retire handshake proves
        there are no active jobs, approvals, queued inputs or uncertain effects.
        This boundary is usable by future host/agent protocol adapters as well.
        """
        self.retention.protected = protected or (lambda sid: False)
        for sid, row in self.workers.items():
            row['update_pending'] = row.get('generation') != generation
        self.retention.wake()

    def generation_usage(self):
        return [{'sessionId': sid, 'generation': row.get('generation'),
                 'updatePending': bool(row.get('update_pending'))}
                for sid, row in self.workers.items() if row['process'].returncode is None]

    def configure_retention(self, settings):
        from .runtime_retention import validate_retention
        updated = validate_retention(settings)
        if updated['max_background_starts'] != self.retention.settings['max_background_starts']:
            raise ValueError('Changing background startup concurrency requires a host restart.')
        self.retention.settings = updated
        self.retention.wake()

    def _command(self, release=_CURRENT_GENERATION, *, home=None):
        if self.command:
            return list(self.command)
        worker = Path(__file__).with_name("runtime_worker.py")
        from .host_dependencies import require_uv
        uv = require_uv()
        # The packaged manifest is copied to a writable cache: installed package
        # directories may be read-only and uv creates its lock and .venv there.
        from .updates import active_release
        from .runtime_environment import prepare_project, receipt_directory
        home = Path(home or self.home or os.environ.get("AMPLIFIER_WEB_HOME", Path.home() / ".amplifier-unified"))
        generation = active_release(home).get("current") if release is _CURRENT_GENERATION else release
        cache = prepare_project(home, generation)
        recorded = (receipt_directory(home, generation) / 'runtime.lock').exists()
        return [uv, "run", *(["--locked"] if recorded else []), *(["--no-sync"] if (receipt_directory(home, generation) / "runtime-project.json").exists() else []), "--project", str(cache), "--python", "3.13", "python", str(worker)]

    @staticmethod
    def project_path(command):
        """Return the runtime project's path from a uv command without offsets."""
        try:
            project = command.index("--project") + 1
            return command[project]
        except (ValueError, IndexError) as exc:
            raise RuntimeError("The runtime command does not identify a uv project.") from exc

    def _check_preparation_cleanup(self, sid):
        preparation = self._preparations.get(sid)
        if preparation and preparation.get('cleanup_error') is not None:
            raise RuntimeStartupError(
                'Windows worker cleanup is unconfirmed; generation protection and '
                'preparation ownership are retained. No new command was sent.'
            ) from preparation['cleanup_error']

    async def start(self, session: dict, emit: Emitter, *, preserve_emit=False, guard=None, preserve_configuration=False):
        sid = session["id"]
        async with self._admission(sid):
            await self._start_locked(session, emit, preserve_emit=preserve_emit, guard=guard,
                **({'preserve_configuration': True} if preserve_configuration else {}))

    @asynccontextmanager
    async def _admission(self, sid):
        while True:
            async with self._locks.setdefault(sid, asyncio.Lock()):
                if self._closed:
                    raise RuntimeHostClosingError()
                self._check_preparation_cleanup(sid)
                row = self.workers.get(sid)
                retirement = row.get('retirement_task') if row else None
                stopping = row.get('stop_task') if row else None
                if stopping is not None:
                    if stopping.done():
                        # A failed shutdown cannot reopen the still-owned row.
                        # Successful shutdown removes it before this future ends.
                        raise RuntimeError('Worker shutdown was not confirmed. No new command was sent; inspect shutdown before retrying.')
                    pending = stopping
                elif retirement is not None and not retirement.done():
                    pending = retirement
                elif row and row.get('update_pending') and self.retention.eligible(row) and not self.retention.protected(sid):
                    # Admission wins races with the periodic retirement sweep.
                    # Wait for the same acknowledged handshake before new work.
                    pending = self.retention.schedule(sid, row)
                else:
                    yield
                    return
            # session.closed can be published before the OS process exits.
            # Never admit work to that closing owner, including after a user
            # Stop. Waiting outside the lock neither cancels nor replays it.
            try:
                await asyncio.wait_for(asyncio.shield(pending), self.retention.reply_timeout)
            except asyncio.CancelledError:
                if self._closed and not asyncio.current_task().cancelling():
                    raise RuntimeHostClosingError() from None
                raise
            except TimeoutError as exc:
                raise RuntimeError('Worker shutdown is still being confirmed. No new command was sent; retry after it settles.') from exc

    async def _start_locked(self, session, emit, *, preserve_emit=False, guard=None, preserve_configuration=False):
        # Host callbacks only: no command argument can authorize a restart.
        if preserve_configuration and guard is None:
            raise ValueError('Placement-only startup requires the retained host operation guard.')
        if session.get('bundleReplacement', {}).get('deferred') and guard is None:
            raise RuntimeStartupError('Deferred bundle replacement requires its host admission guard.')
        if guard is not None: guard()
        if self._closed:
            raise RuntimeHostClosingError()
        sid = session["id"]
        self._check_preparation_cleanup(sid)
        self._check_execution(sid, session)
        configuration_revision = self._configuration_revision()
        self._retired.pop(sid, None)
        current = self.workers.get(sid)
        if current and current["process"].returncode is None:
            if session.get('bundleReplacement') is not None:
                raise RuntimeError('The conversation already has a worker. Retry the bundle change after it settles.')
            if current.get('preparation_error'):
                raise current['preparation_error']
            if not preserve_emit:
                current["emit"] = emit
            current['start_session'] = {key: session[key] for key in (
                'id', 'workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision', 'bundle', 'selection',
                'runtimeSessionId', 'nativeIdentity', 'forkContext') if key in session}
            await asyncio.wait_for(asyncio.shield(current["ready"]), self.startup_timeout)
            return
        await emit("runtime.status", {"sessionId": sid, "status": "starting", "phase": "runtime-setup",
            "detail": "Preparing the pinned Amplifier runtime. First use may install dependencies.", "elapsedSeconds": 0})
        from .host.config import worker_environment
        from .updates import active_release
        from .host.config import app_home
        home = self.home or app_home()
        from .generation_leases import acquire
        reservation = None
        source_reservation = None
        proc = lease = windows_owner = startup_stderr_task = None
        startup_stderr = []
        if guard is not None: guard()
        preparation = {"stopped": self._closed, "shutdown": self._closed, "task": None, "started_at": time.monotonic()}
        self._preparations[sid] = preparation
        heartbeat = asyncio.create_task(self._preparation_progress(sid, preparation, emit))
        try:
            try:
                generation = active_release(home).get('current')
                reservation = acquire(home, generation, os.getpid())
                source_generation = generation
                if not self.command:
                    from .runtime_profiles import ensure
                    setup_started = time.monotonic()
                    async def preparation_progress(detail):
                        preparation["detail"] = detail
                        await emit("runtime.status", {"sessionId": sid, "status": "starting",
                            "phase": "runtime-setup", "detail": detail,
                            "elapsedSeconds": int(time.monotonic() - setup_started),
                            "preparationProgress": True})
                    preparation["task"] = asyncio.create_task(ensure(home, generation, session, progress=preparation_progress))
                    try:
                        source_generation = await asyncio.wait_for(preparation["task"], self.preparation_timeout)
                    except TimeoutError as exc:
                        raise RuntimeStartupError(f"Runtime qualification exceeded {self.preparation_timeout:g} seconds; no input was sent.") from exc
                    except asyncio.CancelledError:
                        if not preparation["stopped"]:
                            raise
                        raise RuntimeStartupError("Runtime preparation was stopped before input admission.") from None
                if preparation["stopped"]:
                    raise RuntimeStartupError("Runtime preparation was stopped before worker spawn.")
                source_reservation = acquire(home, source_generation, os.getpid())
                environment = {**worker_environment(), 'AMPLIFIER_WEB_HOME': str(home),
                               'AMPLIFIER_UNIFIED_RELEASE': source_generation or ''}
                if source_generation and (Path(home)/'updates/releases'/source_generation/'profiles-qualified.json').exists():
                    environment['AMPLIFIER_RUNTIME_IMMUTABLE'] = '1'
                # Finish fallible host reads before a process can exist without
                # readers or a registered cleanup owner.
                update_pending = generation != active_release(home).get('current')
                if guard is not None: guard()
                command = self._command(source_generation, home=home)
                if _WINDOWS:
                    from .windows_jobs import CleanupError, WindowsOwnedProcess
                    try:
                        job = await WindowsOwnedProcess.start(command, cwd=os.getcwd(), env=environment,
                            trusted_python=getattr(sys, '_base_executable', None))
                    except CleanupError as cleanup:
                        # The primitive can retain a job before returning an
                        # adapter. Its module list is not durable generation
                        # protection: keep the host reservations and owner here.
                        preparation.update(cleanup_error=cleanup, windows_job=cleanup.owner)
                        raise
                    windows_owner = _WindowsWorkerOwner(job)
                    proc = windows_owner.process
                    # Drain uv/import logs before the real worker handshake.
                    startup_stderr_task = asyncio.create_task(self._drain_stderr(
                        {'windows_owner': windows_owner, 'stderr': startup_stderr}))
                    preparation["task"] = asyncio.create_task(windows_owner.identify(self.startup_timeout))
                    try:
                        await preparation["task"]
                    except asyncio.CancelledError:
                        if not preparation["stopped"]:
                            raise
                        raise RuntimeStartupError("Runtime preparation was stopped before worker identity verification.") from None
                    finally:
                        preparation["task"] = None
                else:
                    proc = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=MAX_MESSAGE_BYTES,
                        start_new_session=True, env=environment)
                if preparation["stopped"]:
                    raise RuntimeStartupError("Runtime preparation was stopped before input admission.")
                lease = acquire(home, source_generation, proc.pid)
                if windows_owner is None:
                    for candidate in (reservation, source_reservation):
                        if candidate:
                            candidate.unlink(missing_ok=True)
            finally:
                # This await still precedes registration. Cancellation here
                # must enter the same cleanup path as qualification/spawn errors.
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if preparation.get('shutdown'):
                raise RuntimeHostClosingError()
            if preparation['stopped']:
                raise RuntimeStartupError('Runtime preparation was stopped before input admission.')
        except BaseException as exc:
            try:
                if proc is not None and proc.returncode is None:
                    if windows_owner is not None:
                        await _terminate_unregistered(proc, windows_owner)
                    else:
                        await _terminate_unregistered(proc)
            except BaseException as cleanup:
                if windows_owner is None or proc.returncode is not None:
                    raise
                preparation.update(cleanup_error=cleanup, windows_job=windows_owner.job,
                                   windows_owner=windows_owner)
            finally:
                # Reaping can propagate a second cancellation after exit. Do
                # not strand its lease, or release it before exit is confirmed.
                if lease and proc is not None and proc.returncode is not None:
                    try:
                        lease.unlink(missing_ok=True)
                    except OSError:
                        pass  # An orphan lease is retained evidence, not an owner.
                if startup_stderr_task is not None:
                    startup_stderr_task.cancel()
                    await asyncio.gather(startup_stderr_task, return_exceptions=True)
            if preparation.get('cleanup_error') is not None:
                await emit('runtime.error', {
                    'sessionId': sid, 'errorType': 'WorkerCleanupError', 'phase': 'worker_startup',
                    'error': 'Windows worker cleanup is unconfirmed; generation protection and preparation ownership are retained. No input was sent.',
                })
                raise preparation['cleanup_error']
            if preparation.get('shutdown'):
                await emit('runtime.ended', {'sessionId': sid, 'status': 'interrupted', 'reason': 'shutdown'})
                if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
                    raise
                raise RuntimeHostClosingError() from None
            if isinstance(exc, asyncio.CancelledError):
                await emit("runtime.status", {"sessionId": sid, "status": "stopped",
                    "phase": "runtime-setup", "detail": "Preparation cancelled before input admission."})
                raise
            # Qualification and spawn failures happen before a worker/readers
            # exist. Settle every caller (input, controls and background warmup)
            # here, not just send(), and retain the sanitized probe facts.
            from .worker_diagnostics import save_startup_failure, diagnostic_reference, setup_failure_detail
            from .update_diagnostics import exception_type, probe_record, PROBE_PREFIX
            from .session_health import failure_details
            facts = getattr(exc, 'diagnostic_facts', {})
            try:
                probe = probe_record(PROBE_PREFIX + json.dumps(facts.get('probe', {}))) if isinstance(facts, dict) else None
            except Exception:
                probe = None
            kind = (probe or {}).get('errorType') or exception_type(exc)
            detail = failure_details('', kind)
            public = str(exc) if isinstance(exc, RuntimeStartupError) else ('The conversation worker could not start. This attempt did not send your message.'
                      if detail['category'] == 'unknown' else f"{kind}: {detail['summary']} {detail['guidance']}")
            diagnostic = await asyncio.to_thread(save_startup_failure,
                {'runtime_id': session.get('runtimeSessionId') or session.get('nativeIdentity') or sid,
                 'phase': 'runtime-setup'}, None,
                failure=setup_failure_detail(exc),
                home=home)
            failure = RuntimeStartupError(public)
            if diagnostic:
                failure.diagnostic_path = diagnostic
                failure.args = (public + f' Startup details were saved locally to {diagnostic}.',)
            await emit('runtime.error', {'sessionId': sid, 'error': str(failure),
                'errorType': 'RuntimeStartupError', 'phase': 'worker_startup',
                **diagnostic_reference(failure)})
            raise failure from exc
        finally:
            if preparation.get('cleanup_error') is not None:
                # These host-PID leases protect both source generations even
                # when the unverified worker PID cannot safely own a lease.
                # No automatic retry/release: retained native cleanup failures
                # require separate confirmed zero-process/I/O reconciliation.
                preparation['generation_reservations'] = tuple(
                    candidate for candidate in (reservation, source_reservation) if candidate)
            else:
                self._preparations.pop(sid, None)
                for candidate in (reservation, source_reservation):
                    if candidate:
                        try:
                            candidate.unlink(missing_ok=True)
                        except OSError:
                            pass  # Preserve the original failed setup diagnosis.
        row = {"process": proc, "emit": emit, "runtimeOwnerId": str(uuid.uuid4()), "configurationRevision": configuration_revision, "generation": generation, "source_generation": source_generation, "update_pending": update_pending, "ready": asyncio.get_running_loop().create_future(),
               "pending": {}, "inflight": set(), "inputId": None, "closing": False, "stderr": startup_stderr, "bridge_tasks": set(),
               "started_at": time.monotonic(), "phase": "runtime-setup",
               "detail": "Preparing the pinned Amplifier runtime. First use may install dependencies."}
        self.workers[sid] = row
        if windows_owner is not None:
            row['windows_owner'] = windows_owner
        if session.get('bundleReplacement', {}).get('deferred'):
            row['configuration_guard'] = guard
        row['generation_lease'] = lease
        row["reader"] = asyncio.create_task(self._read(sid, row))
        row["stderr_task"] = startup_stderr_task or asyncio.create_task(self._drain_stderr(row))
        row["heartbeat"] = asyncio.create_task(self._progress(sid, row))
        # The worker restores normal history from its checkpoint. Sending the
        # browser's execution logs, catalogs and attachment history is redundant.
        config = {key:session[key] for key in ('id','workspace','workingDirectory','executionRevision','interruptionRevision','bundle','selection','forkContext','replaceSavedSelection','bundleReplacement') if key in session}
        config['id'] = session.get('runtimeSessionId') or session.get('nativeIdentity') or sid
        config['configurationPendingRevision'] = session.get('configurationPendingRevision', 0)
        config['ownerSessionId'] = sid  # Identity only; workers read current standing policy themselves.
        if preserve_configuration:
            config['_preserveConfiguration'] = True
        row['runtime_id'] = config['id']
        row['start_session'] = {**config, 'id': sid, 'runtimeSessionId': config['id']}
        row['start_session'].pop('replaceSavedSelection', None)
        row['start_session'].pop('bundleReplacement', None)
        row['start_session'].pop('_preserveConfiguration', None)
        row['parked'] = False
        self.retention.wake()
        if session.get('forkContext'):
            config['messages'] = session.get('messages', [])
        try:
            if preparation["stopped"]:
                raise RuntimeStartupError("Runtime preparation was stopped before input admission.")
            try:
                if guard is not None: guard()
                await self._write(row, {"op": "start", "session": config})
            except (BrokenPipeError, ConnectionResetError) as exc:
                # A worker can exit between spawn and stdin.drain(). Let the
                # existing reader retain its exit/diagnostic outcome before
                # cleanup, under the same bounded startup wait. Never retry
                # the write or admit input on an unusable command channel.
                await asyncio.wait_for(asyncio.shield(row["ready"]), self.startup_timeout)
                raise RuntimeStartupError(
                    "The conversation worker connection closed during startup. "
                    "This attempt did not send your message."
                ) from exc
            await asyncio.wait_for(asyncio.shield(row["ready"]), self.startup_timeout)
        except TimeoutError as exc:
            await self.stop(sid)
            raise RuntimeError(f"Amplifier preparation exceeded {self.startup_timeout:g} seconds and was stopped before accepting your message. Check the configured bundle, then retry.") from exc
        except BaseException as exc:
            await self.stop(sid, reason='shutdown' if self._closed else 'stopped')
            if (isinstance(exc, asyncio.CancelledError) and self._closed
                    and row.get('stop_reason') == 'shutdown' and not asyncio.current_task().cancelling()):
                # close() cancelled this owner's readiness future, not the
                # caller task. Preserve external cancellation as cancellation.
                raise RuntimeHostClosingError() from None
            raise

    async def _progress(self, sid, row):
        while not row["ready"].done() and not row["closing"]:
            await asyncio.sleep(self.progress_interval)
            if not row["ready"].done() and not row["closing"]:
                await self._emit_progress(sid, row)

    async def _preparation_progress(self, sid, preparation, emit):
        while True:
            await asyncio.sleep(self.progress_interval)
            await emit("runtime.status", {"sessionId": sid, "status": "starting",
                "phase": "runtime-qualification", "preparationProgress": True,
                "detail": preparation.get("detail", "Qualifying the inherited runtime; no task input has been sent."),
                "elapsedSeconds": round(time.monotonic() - preparation["started_at"], 2)})

    async def _emit_progress(self, sid, row):
        await row["emit"]("runtime.status", {"sessionId": sid, "status": "starting",
            "phase": row["phase"], "detail": row["detail"],
            "elapsedSeconds": int(time.monotonic() - row["started_at"]),
            "preparationProgress": True})

    async def _drain_stderr(self, row):
        stream = _worker_pipes(row).stderr
        # Logs need no protocol-line semantics. Chunked reads also drain an
        # unbroken/saturated stderr line without the StreamReader 64 KiB limit.
        async def read():
            return await (stream.read(4096) if row.get('windows_owner') else stream.readline())
        while line := await read():
            # Keep diagnostics local and bounded; never stream arbitrary SDK logs
            # (which can contain prompts/credentials) into browser state.
            from .worker_diagnostics import redact_diagnostic
            row["stderr"].append(redact_diagnostic(line.decode(errors="replace"))[-2000:])
            del row["stderr"][:-30]

    async def _write(self, row, data):
        stream = _worker_pipes(row).stdin
        stream.write(encode_message(data))
        await stream.drain()

    async def _bridge(self, sid, row, data, *, dequeued_ns=None):
        from .performance_diagnostics import PhaseTiming, BRIDGE_TIMING, timing_metadata, TIMING_ENABLED
        observed = timing_metadata(data.get('_timing'))
        timing = None
        if TIMING_ENABLED and observed.get('kind') == 'bridge' and observed.get('bridgeId') == data.get('id'):
            timing = PhaseTiming(kind='host.bridge', bridgeId=data['id'], operation=data['operation'],
                **{key: observed[key] for key in ('invocationId', 'toolCallId', 'runtimeSessionId', 'generationId')
                   if key in observed})
            if dequeued_ns is not None:
                timing.marks['dequeued'] = dequeued_ns
            timing.mark('start')
        token = BRIDGE_TIMING.set(timing)
        outcome = 'completed'
        try:
            try:
                if self.workers.get(sid) is not row or row.get("closing") or row.get("yielded") or row.get("yielding") or row["process"].returncode is not None:
                    raise RuntimeError("The calling runtime no longer owns this bridge.")
                if timing:
                    timing.mark('handler.start')
                try:
                    if data['operation'] == 'configuration.nextMessage.admit':
                        guard = row.get('configuration_guard')
                        if guard is None:
                            raise RuntimeError('No deferred bundle application owns this worker.')
                        guard()
                        result = {'allowed': True}
                    else:
                        if not self.app_bridge:
                            raise RuntimeError("App controls are not connected")
                        result = await self.app_bridge(data["operation"], {**data.get("args", {}),
                            "_runtimeOwnerId": row["runtimeOwnerId"]}, sid)
                finally:
                    if timing:
                        timing.mark('handler.end')
                reply = {"op": "bridge.result", "id": data["id"], "result": result}
            except Exception as exc:
                outcome = 'failed'
                reply = {"op": "bridge.result", "id": data["id"], "error": str(exc)}
                if isinstance(getattr(exc, '_handoff_disposition', None), dict):
                    reply['_handoffDisposition'] = exc._handoff_disposition
            if row["process"].returncode is None:
                if timing:
                    timing.mark('reply.start')
                try:
                    await self._write(row, reply)
                except ValueError as exc:
                    outcome = 'failed'
                    await self._write(row, {"op":"bridge.result","id":data["id"],"error":str(exc)})
                if timing:
                    timing.mark('reply.end')
        except BaseException as exc:
            outcome = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
            raise
        finally:
            BRIDGE_TIMING.reset(token)
            if timing:
                timing.active = False
                timing.mark('end')
                try:
                    if self.performance_diagnostics is not None:
                        self.performance_diagnostics.record_runtime_timing(sid, timing.snapshot(outcome))
                except Exception:
                    pass

    async def _cancel_capacity_bridge(self, sid, row, bridge):
        bridge['task'].cancel()
        await asyncio.gather(bridge['task'], return_exceptions=True)
        # Cancellation is not evidence that an already admitted call was unused.
        # The private accounting operation only marks an exact existing receipt
        # uncertain; it cannot invent a receipt or free an allowance.
        if self.workers.get(sid) is row and self.app_bridge:
            await self.app_bridge('capacity.abandoned', bridge['args'], sid)

    async def _read(self, sid, row):
        from .runtime_event_batches import (BufferedLines, EventSink, MAX_BYTES, ObservationTurns,
                                           finish_owned, observation, starting_observation, terminal)
        from . import performance_diagnostics
        stdout = _worker_pipes(row).stdout
        lines = BufferedLines(row['windows_owner'].stdout if row.get('windows_owner') else stdout)
        turns = ObservationTurns()

        def terminal_event(data):
            if not isinstance(data, dict) or data.get('op') or data.get('type') != 'execution.event':
                return None
            payload = data.get('event')
            if not terminal('execution.event', payload):
                return None
            # Only one current worker's root receipt; child identity is retained.
            if payload.get('rootSessionId') not in (sid, row.get('runtime_id')):
                return None
            payload = dict(payload)
            for key in ('sessionId', 'rootSessionId'):
                if payload.get(key) == row.get('runtime_id'):
                    payload[key] = sid
            return normalize_event({**data, 'event': payload}, sid, row['inputId'])

        def receipt_event(data):
            event = terminal_event(data)
            if event is not None:
                return event
            if not isinstance(data, dict) or data.get('op'):
                return None
            # These exact worker frames contain observations only. Unknown
            # fields, child roots, authority and lifecycle remain flush barriers.
            identity_fields = {'sessionId', 'session_id', 'rootSessionId', 'root_session_id'}
            if any(data.get(key) not in (None, sid, row.get('runtime_id')) for key in identity_fields):
                return None
            kind = data.get('type')
            allowed = {
                'runtime.activity': {'type', 'phase', 'detail', 'activeWorkers', 'retryAttempt', 'retryMax'},
                'runtime.bridge': {'type', 'bridgeId', 'operation', 'generation_id', 'timeoutSeconds',
                                   'runtimeSessionId', 'action', 'phase', 'elapsedSeconds', 'errorType'},
                'runtime.timing': {'type', 'timing'},
            }.get(kind)
            if allowed is None or set(data) - allowed - identity_fields:
                return None
            if kind == 'runtime.bridge' and data.get('runtimeSessionId') not in (None, sid, row.get('runtime_id')):
                return None
            event = normalize_event(data, sid, row['inputId'])
            return event if event is not None and observation(*event) else None

        decoded = None

        async def deliver_batch(line, event, sink):
            def accept(candidate):
                nonlocal decoded
                if row['emit'] is not sink:
                    return None
                try:
                    data = json.loads(candidate)
                except (ValueError, UnicodeDecodeError):
                    return None
                result = receipt_event(data)
                if result is None:
                    decoded = (candidate, data)
                return result
            events = await lines.collect(line, event, accept)
            # start/takeover may replace the callback during the fairness yield,
            # just as in the single-event path. Never call a retired callback.
            sink = row['emit']
            if isinstance(sink, EventSink):
                await sink.batch(events)
            else:
                for kind, payload in events:
                    await row['emit'](kind, payload)
            # Delivery succeeded durably before lifecycle bookkeeping advances.
            for _, payload in events:
                if payload.get('lifecycle') == 'background':
                    row.setdefault('backgroundCalls', set()).add(payload['id'])

        reported_error = None
        try:
            while line := await lines.next():
                dequeued_ns = time.monotonic_ns() if performance_diagnostics.TIMING_ENABLED else None
                try:
                    data = decoded[1] if decoded is not None and line is decoded[0] else json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    turns.reset()
                    await asyncio.sleep(0)
                    continue
                finally:
                    decoded = None
                if not isinstance(data, dict):
                    turns.reset()
                    await asyncio.sleep(0)
                    continue
                sink = row['emit']
                event = terminal_event(data) if isinstance(sink, EventSink) and len(line) <= MAX_BYTES else None
                if event is not None:
                    turns.reset()
                    # Cancellation cannot abandon already consumed receipts.
                    # The bounded collector never waits for another full line.
                    await finish_owned(deliver_batch(line, event, sink))
                    continue
                # Buffered progress uses the unchanged single-event reducer.
                # A bounded prefix gets one fairness turn, not a competing save
                # per frame. Commands, lifecycle, unknown and child frames keep
                # their ordered yield boundary; no bytes are read ahead here.
                if turns.yield_before(len(line), isinstance(sink, EventSink) and
                        starting_observation(data, (sid, row.get('runtime_id')))):
                    await asyncio.sleep(0)
                if data.get("op") == "bridge":
                    task = asyncio.create_task(self._bridge(sid, row, data, dequeued_ns=dequeued_ns))
                    row["bridge_tasks"].add(task)
                    task.add_done_callback(row["bridge_tasks"].discard)
                    if data.get('operation') == 'capacity.admit' and data.get('args', {}).get('waitForCapacity'):
                        row.setdefault('capacity_bridges', {})[data['id']] = {'task': task, 'args': data['args']}
                elif data.get('op') in {'bridge.cancel', 'bridge.received'}:
                    bridge = row.get('capacity_bridges', {}).pop(data.get('id'), None)
                    if bridge and data['op'] == 'bridge.cancel' and self.workers.get(sid) is row:
                        task = asyncio.create_task(self._cancel_capacity_bridge(sid, row, bridge))
                        row['bridge_tasks'].add(task)
                        task.add_done_callback(row['bridge_tasks'].discard)
                elif data.get("op") == "reply":
                    row["inflight"].discard(data.get("id"))
                    tracked_input = row.get('sendInputs', {}).pop(data.get('id'), None)
                    peer_input = tracked_input if isinstance(tracked_input, dict) else None
                    input_id = None if peer_input else tracked_input
                    future = row["pending"].get(data.get("id"))
                    error = _worker_error(data) if data.get('error') else None
                    if isinstance(error, RuntimeStartupError):
                        row['preparation_error'] = error
                        reported_error = str(error)
                        from .worker_diagnostics import diagnostic_reference
                        await row['emit']('runtime.error', {'sessionId': sid,
                            'error': str(error), 'errorType': 'RuntimeStartupError',
                            'phase': 'worker_startup', 'moduleFailures': error.failures,
                            **diagnostic_reference(error)})
                        if input_id:
                            # Only first sends are tracked here. A rejected
                            # retry cannot settle its earlier uncertain attempt.
                            await row['emit']('runtime.delivery', {'sessionId': sid,
                                'inputId': input_id, 'delivery': 'failed'})
                    if future and not future.done():
                        if error:
                            future.set_exception(error)
                        else:
                            future.set_result(data.get("result"))
                    result = data.get('result')
                    if (peer_input and not error and isinstance(result, dict)
                            and self.workers.get(sid) is row
                            and result.get('inputId', peer_input['inputId']) == peer_input['inputId']):
                        # Control replies can outlive their caller just like sends.
                        # Retain exact admission evidence, never another submission.
                        await row['emit']('runtime.delivery', {'sessionId': sid,
                            **peer_input, 'peerAdmission': result})
                    if (input_id and not data.get('error') and isinstance(result, dict)
                            and result.get('accepted') is True and result.get('inputId', input_id) == input_id):
                        # The HTTP caller may already have timed out. Reconcile
                        # only this send's receipt, without changing turn state.
                        await row['emit']('runtime.delivery', {'sessionId': sid, 'inputId': input_id})
                elif data.get("type") == "runtime.parked":
                    row["parked"] = True
                    row["parked_at"] = self.retention.clock()
                    self.retention.wake()
                    await row['emit']('runtime.warmth', {'sessionId': sid, 'status': 'warm'})
                elif data.get("type") == "runtime.reactivated":
                    row["parked"] = False
                    await row['emit']('runtime.warmth', {'sessionId': sid, 'status': 'active'})
                elif data.get("type") == "runtime.progress":
                    if not row["ready"].done() and not row["closing"]:
                        row["phase"] = data.get("phase", row["phase"])
                        row["detail"] = data.get("detail", row["detail"])
                        await self._emit_progress(sid, row)
                elif data.get("type") == "runtime.ready":
                    if not row["ready"].done():
                        # uv is the managed launcher, not necessarily the Python
                        # worker. Bind configuration effects to the identity on
                        # this worker's own pipe, scoped to the started session.
                        identity = data.get("workerIdentity")
                        owner = row.get('windows_owner')
                        if owner is not None:
                            if (not isinstance(identity, dict)
                                    or identity.get('sessionId') != row.get('runtime_id')
                                    or type(identity.get('pid')) is not int
                                    or identity['pid'] != owner.process.pid):
                                raise RuntimeError('Windows ready identity differs from its verified worker')
                            owner.job.verify_member(owner.process.pid, owner.created)
                        if (isinstance(identity, dict)
                                and identity.get("sessionId") == row.get("runtime_id")
                                and type(identity.get("pid")) is int and identity["pid"] > 0):
                            row["native_worker_pid"] = identity["pid"]
                        row["ready"].set_result(data.get("report", {}))
                    await row["emit"]("runtime.status", {"sessionId": sid, "status": "ready", "phase": "ready",
                        "detail": "Amplifier is ready.", "elapsedSeconds": int(time.monotonic() - row["started_at"]),
                        "runtimeOwnerId": row['runtimeOwnerId'], "configurationRevision": row['configurationRevision'],
                        "report": data.get("report", {})})
                elif data.get("type") == "runtime.ownership":
                    row['yielded'] = data.get('status') == 'yielded'
                    row['yielding'] = data.get('status') in {'yielding', 'yield-failed'}
                    await row['emit']('runtime.ownership', {'sessionId': sid,
                        **{key: data[key] for key in ('status', 'source', 'detail') if key in data}})
                elif data.get("type") == "runtime.error":
                    if 'terminalFailure' in data:
                        # This is supplemental metadata, not a new failure or
                        # startup exception. Invalid/unsupported captures cannot
                        # be downgraded into an uncorrelated generic exit.
                        normalized = normalize_event(data, sid)
                        if (self.workers.get(sid) is row and not row['closing']
                                and row['ready'].done() and normalized):
                            await row['emit'](*normalized)
                        continue
                    from .session_health import failure_details
                    failure = _worker_error(data)
                    diagnostic = None
                    startup = not row['ready'].done()
                    if startup and not isinstance(failure, SessionInUseError):
                        from .worker_diagnostics import receipt_path, save_startup_failure
                        diagnostic = receipt_path(data.get("diagnosticReceipt"))
                        if diagnostic is None:
                            # A handled worker error can race the final stderr.
                            # Wait only briefly; failure must not hang on a worker
                            # that reports an error without closing its streams.
                            try:
                                await asyncio.wait_for(asyncio.shield(row["stderr_task"]), 0.2)
                            except Exception:
                                pass
                            diagnostic = await asyncio.to_thread(save_startup_failure, row, None,
                                failure=data.get("error") or str(failure))
                        from .module_failures import ConfiguredModuleError
                        if not isinstance(failure, ConfiguredModuleError):
                            from .session_health import failure_details
                            detail = failure_details(data.get("error"), data.get("errorType"))
                            labels = {'authentication': 'AuthenticationError', 'rate_limit': 'RateLimitError',
                                      'context_limit': 'ContextLengthError', 'invalid_image': 'InvalidImageError',
                                      'tool_configuration': 'ToolConfigurationError', 'provider_selection': 'ProviderSelectionError',
                                      'bundle_configuration': detail['errorType']}
                            label = labels.get(detail['category'])
                            # Publish only our bounded classification and guidance,
                            # never the original provider payload or exception text.
                            public = (f"{label}: {detail['summary']} {detail['guidance']}" if label
                                      else "The conversation worker could not start.")
                            failure = RuntimeStartupError(public)
                        if diagnostic:
                            failure.diagnostic_path = diagnostic
                    error = str(failure)
                    reported_error = error
                    if not row["ready"].done():
                        row["ready"].set_exception(failure)
                    if isinstance(failure, SessionInUseError):
                        await row["emit"]("runtime.ownership", {"sessionId": sid, "status": "blocked", "owner": failure.owner})
                    else:
                        from .module_failures import ConfiguredModuleError
                        await row["emit"]("runtime.error", {"sessionId": sid, "error": error,
                            **({"errorType": "RuntimeStartupError"} if startup else
                               {"errorType": failure_details(error, data["errorType"])["errorType"]} if data.get("errorType") else {}),
                            **({'phase': 'worker_startup'} if startup else {}),
                            **({"diagnosticReceipt": diagnostic.name} if diagnostic else {}),
                            **({"moduleFailures": failure.failures} if isinstance(failure, ConfiguredModuleError) else {})})
                elif data.get("type") == "history.revised":
                    await row["emit"]("history.revised", {**data, "sessionId": sid})
                elif data.get("type") in {"approval.requested", "approval.resolved"} and data.get("id"):
                    await row["emit"](data["type"], {**data, "sessionId": sid})
                else:
                    if data.get('type') == 'execution.event' and isinstance(data.get('event'), dict):
                        data = {**data, 'event': dict(data['event'])}
                        if data['event'].get('lifecycle') == 'background' and data['event'].get('id'):
                            row.setdefault('backgroundCalls', set()).add(data['event']['id'])
                        for key in ('sessionId', 'rootSessionId'):
                            if data['event'].get(key) == row.get('runtime_id'):
                                data['event'][key] = sid
                    emitter = data.get("sessionId") or data.get("session_id") or row["runtime_id"]
                    root_emitter = data.get("rootSessionId") or data.get("root_session_id") or row["runtime_id"]
                    if data.get("type") == "input.delivered" and emitter == row["runtime_id"] and root_emitter == row["runtime_id"]:
                        row["inputId"] = data.get("input_id")
                    if (data.get("type") == "input.delivered" or data.get("type", "").startswith("steering.")) and (emitter != row["runtime_id"] or root_emitter != row["runtime_id"]):
                        continue  # Child input cannot mutate root admission provenance.
                    if data.get("type", "").startswith("generation.") or data.get("type") == "collaboration.checkpoint":
                        data = dict(data)
                        identity = data.get("sessionId") or data.get("session_id") or row["runtime_id"]
                        root = data.get("rootSessionId") or data.get("root_session_id") or row["runtime_id"]
                        data["sessionId"] = sid if identity == row["runtime_id"] else identity
                        data["rootSessionId"] = sid if root == row["runtime_id"] else root
                    normalized = normalize_event(data, sid, row["inputId"])
                    if (normalized and normalized[0] == 'runtime.status'
                            and normalized[1].get('status') == 'stopped'
                            and row.get('stop_reason') in {'configuration', 'shutdown', 'retention'}):
                        # Closing a process for maintenance is not a user Stop.
                        normalized = ('runtime.warmth', {'sessionId': sid, 'status': 'cold'})
                    if normalized:
                        if normalized[0] == 'worker.updated' and not normalized[1].get('activityOnly'):
                            # Only lifecycle receipts on this process's pipe
                            # bind its native jobs/children. Progress and peer
                            # chats cannot acquire that execution ownership.
                            normalized[1]['runtimeOwnerId'] = row['runtimeOwnerId']
                        await row["emit"](*normalized)
            code = await _wait_worker(row)
            if not row["closing"] and not reported_error:
                diagnostic = None
                error = f"Amplifier worker exited (code {code}). Work was not replayed."
                if not row["ready"].done():
                    # stdout can reach EOF before the separate stderr reader.
                    try:
                        await asyncio.wait_for(asyncio.shield(row["stderr_task"]), 1)
                    except (TimeoutError, OSError, ValueError):
                        pass
                    from .worker_diagnostics import save_startup_failure
                    diagnostic = await asyncio.to_thread(save_startup_failure, row, code)
                    if diagnostic:
                        error += f" Startup details were saved locally to {diagnostic}."
                await row["emit"]("runtime.error", {"sessionId": sid, "error": error, "errorType": "WorkerExitedError" if row["ready"].done() else "RuntimeStartupError", "exitCode": code,
                    **({'phase': 'worker_startup'} if not row['ready'].done() else {}),
                    **({"diagnosticReceipt": diagnostic.name} if diagnostic else {})})
                if not row["ready"].done():
                    failure = RuntimeError(error)
                    if diagnostic:
                        failure.diagnostic_path = diagnostic
                    row["ready"].set_exception(failure)
            await self._execution_ended(sid, row, "stopped" if row["closing"] else "interrupted")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            cleanup_error = None
            if row.get('windows_owner'):
                # A malformed/oversized protocol cannot strand a live job after
                # its sole stdout reader has stopped. Failure retains the owner.
                try:
                    await row['windows_owner'].close()
                except Exception as cleanup:
                    cleanup_error = cleanup
            error = f'Amplifier worker communication failed ({type(exc).__name__}). Work was not replayed.'
            if cleanup_error is not None:
                error += ' Windows worker cleanup is unconfirmed; ownership is retained.'
            if not row['closing']:
                await row['emit']('runtime.error', {'sessionId':sid,'error':error,'errorType':'WorkerCommunicationError' if row['ready'].done() else 'RuntimeStartupError',
                    **({'phase': 'worker_startup'} if not row['ready'].done() else {})})
            if not row["ready"].done():
                row["ready"].set_exception(RuntimeError(error))
            if cleanup_error is not None:
                raise cleanup_error
            # A reader/publication failure can skip the normal EOF path even
            # after the exact process has exited. Settle its routing authority
            # through the same owner-bound event; an exception alone is not
            # process-death evidence and must never terminate surviving work.
            # The Windows observation publishes a returncode only after its
            # owner proves zero processes AND finalized I/O; failed cleanup
            # above must never reach this boundary.
            if row['process'].returncode is not None:
                await self._execution_ended(sid, row, 'stopped' if row['closing'] else 'interrupted')
        finally:
            try:
                await finish_owned(lines.close())
            finally:
                row.get('sendInputs', {}).clear()
                for future in row["pending"].values():
                    if not future.done():
                        future.set_exception(RuntimeHostClosingError() if row.get('stop_reason') == 'shutdown'
                                             else RuntimeError("Amplifier worker disconnected"))

    async def _execution_ended(self, sid, row, status):
        """Only confirmed process exit settles its native children/background calls."""
        if row.get("executionEnded"): return
        shutdown = row.get('stop_reason') == 'shutdown'
        await row["emit"]("runtime.ended", {"sessionId": sid, "status": 'interrupted' if shutdown else status,
            **({'runtimeOwnerId': row['runtimeOwnerId']} if row.get('runtimeOwnerId') else {}),
            **({'reason': 'shutdown'} if shutdown else {}), "backgroundCallIds": sorted(row.get("backgroundCalls", ()))})
        # The reader can be cancelled while delivery waits for the service
        # lock. Only successful delivery suppresses the stop-path retry.
        row["executionEnded"] = True

    async def _request(self, sid, op, *, guard=None, **args):
        row = self.workers.get(sid)
        if op == 'approval' and row and not row['ready'].done():
            # A module can ask for approval during initialization. start() owns
            # the admission lock until ready, so its answer must bypass that
            # lock. A starting worker is never eligible for idle retirement.
            return await self._request_unlocked(sid, op, **args)
        # The admission portion holds the same lock as retirement. Waiting for
        # replies does not: a tool control may itself await an approval/bridge.
        with measure(self, 'input.runtime.admit', session_id=sid) if op in {'send', 'retry'} else nullcontext():
            async with self._admission(sid):
                if guard is not None: guard()
                if sid not in self.workers and sid in self._retired and op in {"send", "retry", "control", "resume"} and not (op == "control" and (args.get("operation", "").startswith(("operations.", "kernels.")))):
                    session, emit = self._retired[sid]
                    # Retirement can occur after preservation-aware preparation but
                    # before this control. Keep its original host guard and startup
                    # preservation; the native attempt still checks the old PID.
                    placement = op == 'control' and args.get('operation') == 'configuration.patchPlacement.bind'
                    await self._start_locked(session, emit, **({'guard': guard} if guard is not None else {}),
                        **({'preserve_configuration': True} if placement else {}))
                # No suspension between this authority check and _admit's transport
                # write. The callback is host-only and is never serialized.
                if guard is not None: guard()
                pending = await self._admit(sid, op, args)
        with measure(self, 'input.runtime.ack', session_id=sid) if op in {'send', 'retry'} else nullcontext():
            return await self._reply(*pending, op=op, args=args)

    async def _request_unlocked(self, sid, op, **args):
        return await self._reply(*await self._admit(sid, op, args), op=op, args=args)

    async def _admit(self, sid, op, args):
        if self._closed:
            raise RuntimeHostClosingError()
        # Handoff holds this same admission lock while releasing its writer.
        # The host fence persists until the durable execution-state commit, so
        # queued controls cannot resurrect the old checkout in the gap.
        safe = op in {'approval', 'coordination.approval', 'worker.stop', 'park', 'retire', 'dependencies', 'desktop.readiness', 'handoff.boundary'} or (
            op == 'control' and args.get('operation') in {
                'operations.cancel', 'kernels.interrupt', 'kernels.close',
                'task.pause', 'task.block', 'task.complete',
            }
        )
        execution = self._check_execution(sid, allow_fenced=safe)
        row = self.workers.get(sid)
        if not row or row["process"].returncode is not None:
            raise RuntimeError("Session is not running")
        peer_admission = None
        if op == 'control' and args.get('operation') in {
                'coordination.submit', 'coordination.deliver', 'coordination.steer'}:
            peer_admission = args.get('arguments')
            if (not isinstance(peer_admission, dict)
                    or not isinstance(peer_admission.get('inputId'), str) or not peer_admission['inputId']):
                raise ValueError('Peer admission requires an exact input identity.')
        if (op == 'control' and args.get('arguments', {}).get('integrationId') is not None
                and row.get('integrationValidation') != 'admission-v1'):
            raise RuntimeError('The current worker has not confirmed integration admission support.')
        if row.get('preparation_error') and op in {'send', 'retry', 'resume', 'control'}:
            raise row['preparation_error']
        identity = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        row["pending"][identity] = future
        if op == 'send':
            row.setdefault('sendInputs', {})[identity] = args['input_id']
        # A caller timing out or disconnecting does not cancel work already
        # handed to the worker. Keep it busy until the reply or process exit.
        row["inflight"].add(identity)
        if op not in {"park", "retire", "dependencies", "desktop.readiness", "delivery", "approval", "coordination.approval", "handoff.boundary"}:
            row["parked"] = False
        try:
            # Capture host authority at admission, before execution can emit a
            # generation. Event arrival must never recapture a later Stop epoch.
            interruption = (execution or row.get('start_session', {})).get('interruptionRevision', 0)
            if peer_admission is not None:
                row.setdefault('sendInputs', {})[identity] = {
                    'inputId': peer_admission['inputId'], 'interruptionRevision': interruption,
                    'targetGenerationId': peer_admission.get('targetGenerationId')}
            await self._write(row, {"op": op, "id": identity, **args,
                                   "interruptionRevision": interruption})
        except (ValueError, TypeError):
            row["inflight"].discard(identity)
            row["pending"].pop(identity, None)
            row.get('sendInputs', {}).pop(identity, None)
            raise
        except BaseException as exc:
            # A partial transport write has an unknown outcome. Keep the
            # in-flight fence until its reply or process exit, just like timeout.
            if op == 'retire' and isinstance(exc, Exception):
                # A partial retirement write can still get a late reply. Its
                # reconciler keeps the original future until reply or exit.
                return row, identity, future
            row["pending"].pop(identity, None)
            raise
        return row, identity, future

    async def _reply(self, row, identity, future, *, op, args):
        try:
            timeout = 75 if op == "control" and args.get("operation") == "memory.consolidate" else None if op == "retire" or op == "control" and args.get("operation") == "history.edit" else 180 if op == "control" and args.get("operation") in {"bundle.preview", "bundle.prepare"} else 600 if op == "control" and args.get("operation") == "tool.invoke" else 150 if op == "control" and args.get("operation") in {"configuration.providerModels","configuration.providerTest"} else 30
            if op == "coordination.approval":
                # The worker expires the human decision after 50 seconds.
                timeout = 55
            try:
                return await asyncio.wait_for(future, timeout)
            except TimeoutError as exc:
                raise RuntimeOperationPending(op) from exc
        finally:
            row["pending"].pop(identity, None)

    async def prewarm(self, session, emit):
        if self._closed or not self.retention.settings["max_warm_workers"] or not self.retention.settings["idle_timeout_hours"]:
            return
        # Repeated explicit preparation neither reacquires a loaded chat’s writer
        # lock nor extends its idle lifetime. Actual input still validates state on wake.
        if self.workers.get(session["id"], {}).get("process") is not None:
            row = self.workers[session["id"]]
            if row["process"].returncode is None:
                return
        async with self.retention.background:
            # Policy or another admission may have changed while this startup
            # waited behind other background preparations.
            if self._closed or not self.retention.settings["max_warm_workers"] or not self.retention.settings["idle_timeout_hours"]:
                return
            row = self.workers.get(session["id"])
            if row and row["process"].returncode is None:
                return
            await self.start(session, emit)
            await self._request(session["id"], "park")

    async def send(self, session, text, input_id, emit, *, admission_check=None):
        if admission_check:
            admission_check()
        update_gate = getattr(self, 'update_input_gate', lambda *args: None)
        update_gate(session['id'], input_id)
        configuration_guard = await self._start_for_input(session, emit,
            **({'admission_check': admission_check} if admission_check else {}))
        def guard():
            update_gate(session['id'], input_id)
            message = next((m for m in session.get('messages', []) if m.get('inputId') == input_id), {})
            getattr(self, 'update_message_guard', lambda sid, message: None)(session['id'], message)
            if configuration_guard:
                configuration_guard()
        return await self._request(session["id"], "send", guard=guard, text=text, input_id=input_id,
            reply_context=next((m.get("replyTo") for m in session.get("messages",[]) if m.get("inputId")==input_id),None),
            context_binding=session.get('surfaceInputs', {}).get(input_id, {'clientId': None, 'targets': []}),
            attachments=next((m.get("attachments",[]) for m in session.get("messages",[]) if m.get("inputId")==input_id),[]))

    async def steer(self, session, text, input_id, emit):
        """Target the existing worker only; steering can never start a worker."""
        from .message_delivery import find_message
        message = find_message(session, input_id)
        sid = session['id']
        args = {'operation': 'conversation.steer', 'arguments': {
            'text': text, 'inputId': input_id,
            'targetGenerationId': message['steering']['generationId'],
            'allowNextTurn': message['steering'].get('allowNextTurn') is True,
            'reply_context': message.get('replyTo'), 'attachments': message.get('attachments', []),
            'context_binding': session.get('surfaceInputs', {}).get(input_id, {'clientId': None, 'targets': []})}}
        async with self._admission(sid):
            if message['steering'].get('allowNextTurn') is True:
                getattr(self, 'update_input_gate', lambda *args: None)(sid, input_id)
                getattr(self, 'update_message_guard', lambda *args: None)(sid, message)
            row = self.workers.get(sid)
            if self._closed or not row or row['process'].returncode is not None or row.get('closing'):
                return {'accepted': False, 'reason': 'The run is no longer available for steering.'}
            self._check_execution(sid, session)
            pending = await self._admit(sid, 'control', args)
        return await self._reply(*pending, op='control', args=args)

    async def collaboration_input(self, session, arguments, guard, emit):
        """Explicit idle start plus guarded ordinary native admission."""
        update_gate = getattr(self, 'update_input_gate', lambda: None)
        from .update_drain import UpdateDeferred
        try:
            update_gate()
        except UpdateDeferred:
            return {"accepted": False, "effect": "none", "waitingForUpdate": True}
        reason = guard()
        if reason:
            return {"accepted": False, "reason": reason}
        try:
            configuration_guard = await self._start_for_input(session, emit)
        except (RuntimeStartupError, SessionInUseError, asyncio.CancelledError) as exc:
            # Positive evidence from this attempt's pre-admission boundary.
            # This is never inferred from worker-row absence after restart.
            if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
                raise RuntimePreparationCancelled("Preparation cancelled before input admission.") from exc
            return {"accepted": False, "delivery": "not_sent",
                    "reason": str(exc) or "Preparation cancelled before input admission."}
        async with self._admission(session["id"]):
            try:
                update_gate()
            except UpdateDeferred:
                return {"accepted": False, "effect": "none", "waitingForUpdate": True}
            reason = guard()
            if reason:
                return {"accepted": False, "reason": reason}
            if configuration_guard:
                configuration_guard()
            operation = "coordination.deliver" if arguments.get("automaticDelivery") is True else "coordination.submit"
            args = {"operation": operation, "arguments": arguments}
            pending = await self._admit(session["id"], "control", args)
        return await self._reply(*pending, op="control", args=args)

    async def collaboration_steer(self, session, arguments, guard, emit):
        """Guard an existing owner's exact generation; never start an idle turn."""
        reason = guard()
        if reason:
            return {"accepted": False, "effect": "none", "reason": reason}
        sid = session["id"]
        async with self._admission(sid):
            reason = guard()
            if reason:
                return {"accepted": False, "effect": "none", "reason": reason}
            row = self.workers.get(sid)
            if self._closed or not row or row["process"].returncode is not None or row.get("closing"):
                return {"accepted": False, "supported": False, "effect": "none",
                        "reason": "No live recipient runtime is available for anchored steering."}
            self._check_execution(sid, session)
            args = {"operation": "coordination.steer", "arguments": arguments}
            pending = await self._admit(sid, "control", args)
        return await self._reply(*pending, op="control", args=args)

    async def collaboration_approval(self, session_id, prompt, approval_id=None):
        """Ask once through the existing approval UI without a mutation lock."""
        # The host validates retained human source/generation before and after
        # this decision. This transport grants no local scope or authority.
        return await self._request_unlocked(session_id, "coordination.approval", prompt=prompt,
            **({"approval_id": approval_id} if approval_id else {}))

    @measured('input.runtime.prepare', session=True)
    async def _start_for_input(self, session, emit, *, admission_check=None):
        try:
            guard = None
            captured_revision = session.get('nextMessageRevision', 0)
            if self._input_configuration and session.get('nextMessageRevision'):
                session, guard = await self._input_configuration(session)
            configuration_guard = guard
            if admission_check:
                def guard():
                    admission_check()
                    if configuration_guard:
                        configuration_guard()
                guard()
            requested=session.get('pendingModelSelection')
            guarded = {'guard': guard} if guard else {}
            await self.start({**session,**({'selection':requested,'replaceSavedSelection':True} if requested else {})}, emit, **guarded)
            if session.get('modelConnectionKey') and (requested or session.get('selection')):
                try:
                    await self.control(session['id'],'provider.verifyConnection',{
                        'instance':(requested or session['selection'])['instance'],
                        'expectedConnectionKey':session['modelConnectionKey']
                    }, **guarded)
                except (SessionInUseError, RuntimeStartupError, RuntimeHostClosingError):
                    raise
                except Exception as exc:
                    raise RuntimeStartupError(
                        'Could not verify the saved connection. No input was sent. '
                        'Review its account and endpoint, reconnect if needed, and save the model again.') from exc
            if requested:
                await self.control(session['id'],'provider.select',{
                    **requested, **({'expectedConnectionKey':session['modelConnectionKey']} if session.get('modelConnectionKey') else {})
                }, **guarded)
            if configuration_guard:
                guard()
                await emit('runtime.nextMessageApplied', {'sessionId': session['id'],
                    'revision': captured_revision, 'bundle': session.get('bundle'), 'selection': requested})
            elif requested:
                await emit('runtime.modelSelectionApplied',{'sessionId':session['id'],'selection':requested})
            return guard
        except (SessionInUseError, RuntimeStartupError, RuntimeHostClosingError):
            raise
        except Exception as exc:
            failure = RuntimeStartupError('The conversation worker could not start. This attempt did not send your message.')
            if getattr(exc, 'diagnostic_path', None) is not None:
                failure.diagnostic_path = exc.diagnostic_path
            raise failure from exc

    async def delivery(self, session, input_id):
        """Inspect existing evidence; never start a worker or submit an input."""
        row = self.workers.get(session['id'])
        if row and row['process'].returncode is None and row['ready'].done():
            try:
                result = await self._request(session['id'], 'delivery', input_id=input_id)
                if result.get('delivery') == 'accepted':
                    return 'accepted'
            except (RuntimeError, TimeoutError, OSError):
                pass  # A worker may have retired; still inspect its saved evidence.
        from .message_delivery import saved_delivery
        return await asyncio.to_thread(saved_delivery, session, input_id)

    async def retry(self, session, text, input_id, emit, *, admission_check=None):
        guard = await self._start_for_input(session, emit,
            **({'admission_check': admission_check} if admission_check else {}))
        return await self._request(session['id'], 'retry', guard=guard, text=text, input_id=input_id,
            reply_context=next((m.get('replyTo') for m in session.get('messages',[]) if m.get('inputId')==input_id),None),
            context_binding=session.get('surfaceInputs', {}).get(input_id, {'clientId': None, 'targets': []}),
            attachments=next((m.get('attachments', []) for m in session.get('messages', []) if m.get('inputId') == input_id), []))

    async def takeover(self, session, emit, expected_owner=None, timeout=30):
        """One deliberate request; a competing successor is never asked to yield."""
        if _WINDOWS:
            raise RuntimeError('Session handoff is unavailable on Windows. Stop the current owner before opening here.')
        from amplifier_foundation.session import SharedSessionStore, request_release
        row = self.workers.get(session['id'])
        if row and row.get('yielding'):
            raise RuntimeError('The owner has not finished safe shutdown. Ownership is retained.')
        if row and row.get('yielded'):
            await self.stop(session['id'])
        try:
            await self.start(session, emit)
            await self._request(session['id'], 'resume')
            return
        except SessionInUseError as busy:
            if expected_owner and expected_owner.get('acquisition_id') != busy.owner.get('acquisition_id'):
                raise
            store = SharedSessionStore(session['workspace'],
                session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'])
            result = await request_release(store, expected_owner=busy.owner, request_id=uuid.uuid4().hex,
                requester_app='Amplifier Unified', timeout=timeout)
            # Attempt the real acquisition even if the final reply was lost.
            try:
                await self.start(session, emit)
                await self._request(session['id'], 'resume')
            except SessionInUseError as current:
                current.args = (f'Takeover did not complete ({result.status}). {result.message} {current}',)
                raise current from None

    async def handoff_boundary(self, session_id, request_id, *, cancel=False, receipt=None, update=False, configuration=False):
        """Inspect only this existing worker; never warm or acquire a writer."""
        row = self.workers.get(session_id)
        expected = (receipt or {}).get('configurationWait') if configuration else (receipt or {}).get('updateWait')
        def guard():
            current = self.workers.get(session_id)
            if expected and (not current or current.get('runtimeOwnerId') != expected['owner']):
                raise ValueError('The requesting runtime owner changed before its update boundary.')
        guard()
        if row is None:
            return {'ready': True, 'waitingFor': [], 'continuationAllowed': False}
        if row['process'].returncode is not None:
            raise RuntimeError('The old worker exited before confirming its saved boundary.')
        return await self._request(session_id, 'handoff.boundary', request_id=request_id, cancel=cancel,
            receipt=receipt, **({'guard': guard} if expected else {}), **({'update': True} if update else {}),
            **({'configuration': True} if configuration else {}))

    @contextmanager
    def stopped_boundary(self, session):
        """Hold existing native ownership while proving a stopped root is absent.

        Called under the service mutation lock. This non-awaiting inspection
        neither starts nor stops workers, and never requests another owner yield.
        The lease remains held through the caller's durable recovery commit.
        """
        from amplifier_foundation.session import SharedSessionStore
        sid = session['id']
        self._check_execution(sid, session)
        row = self.workers.get(sid)
        lock = self._locks.get(sid)
        if (self._closed or sid in self._preparations or lock and lock.locked()
                or row and (row['process'].returncode is None or not row.get('executionEnded')
                            or any(not task.done() for task in row.get('bridge_tasks', ()))
                            or row.get('stop_task') and not row['stop_task'].done())):
            raise RuntimeError('Wait for this chat and its workers to settle before continuing.')
        store = SharedSessionStore(session['workspace'],
            session.get('runtimeSessionId') or session.get('nativeIdentity') or sid)
        held = store.acquire(app='amplifier-unified-stop-recovery')
        try:
            from .host_identity import local_host_identity
            yield {'sessionId': sid, 'nativeSessionId': store.session_id,
                   'executionHost': local_host_identity(),
                   'executionRevision': session.get('executionRevision', 0),
                   'interruptionRevision': session.get('interruptionRevision', 0),
                   'observedAt': time.time(), 'workerAbsent': True,
                   'ownershipAcquisitionId': held.owner['acquisition_id'],
                   'effectsRolledBack': False, 'inputsReplayed': False}
        finally:
            held.release()

    async def quiesce_for_handoff(self, session, request_id, *, transfer_destination=None):
        """Save and release a known local writer before moving execution cwd."""
        if _WINDOWS:
            raise RuntimeError('Session handoff and transfer fencing are unavailable on Windows; no ownership was changed.')
        from amplifier_foundation.session import SharedSessionStore, SessionBusyError, request_release
        sid = session['id']
        store = SharedSessionStore(session['workspace'], session.get('runtimeSessionId') or session.get('nativeIdentity') or sid)
        async with self._admission(sid):
            row = self.workers.get(sid)
            released_owner = None
            try:
                held = await asyncio.to_thread(store.acquire, app='amplifier-unified-handoff')
            except SessionBusyError as busy:
                if not row or busy.owner.get('app') != 'amplifier-unified' or busy.owner.get('pid') != row['process'].pid:
                    # No release request, Stop or placement mutation occurred.
                    # Later failures must remain uncertain, even if they also
                    # report a busy writer after the release boundary.
                    raise HandoffNotStartedError(busy.owner) from None
                released_owner = {key: busy.owner[key] for key in ('hostname', 'app', 'pid', 'acquisition_id') if key in busy.owner}
                result = await request_release(store, expected_owner=busy.owner, request_id=request_id, requester_app='Unified checkout handoff', timeout=30)
                if result.status != 'released':
                    raise RuntimeError(f'Checkout handoff could not confirm saved writer release ({result.status}). {result.message}')
                held = await asyncio.to_thread(store.acquire, app='amplifier-unified-handoff')
            try:
                # Parked workers already saved before relinquishing their lock.
                # A released worker cannot write while this proof handle is held.
                await self.stop(sid)
                from .host_identity import local_host_identity, require_local_host
                owner = held.owner
                require_local_host(owner.get('hostname'))
                if transfer_destination is not None:
                    if not hasattr(held, 'fence_transfer'):
                        raise ValueError('This runtime needs Foundation durable transfer fencing before task export')
                    held.fence_transfer(request_id, transfer_destination, role='source')
                    return {'quiesced': True, 'transferFenced': True, 'nativeSessionId': store.session_id,
                            'effectsRolledBack': False, 'inputsReplayed': False}
                return {'quiesced': True, 'executionHost': local_host_identity(), 'releasedOwner': released_owner, 'releaseOwner': {key: owner[key] for key in ('hostname', 'app', 'pid', 'acquisition_id') if key in owner}, 'nativeSessionId': store.session_id, 'historyHome': session['workspace'], 'effectsRolledBack': False, 'inputsReplayed': False}
            finally:
                await asyncio.to_thread(held.release)

    async def approval(self, session_id, approval_id, decision, comment=None):
        return await self._request(session_id, "approval", approval_id=approval_id, decision=decision,
                                   **({"comment": comment} if comment else {}))

    async def scheduled_input(self, session_id, arguments, guard):
        """Internal authorized schedule handoff using ordinary worker admission."""
        async with self._admission(session_id):
            if session_id not in self.workers and session_id in self._retired:
                session, emit = self._retired[session_id]
                await self._start_locked(session, emit)
            reason = guard()
            if reason:
                return {"accepted": False, "reason": reason}
            args = {"operation": "schedule.submit", "arguments": arguments}
            pending = await self._admit(session_id, "control", args)
        return await self._reply(*pending, op="control", args=args)

    def observation_available(self, session_id):
        return session_id in self.workers or session_id in self._retired

    async def observation_input(self, session_id, arguments, guard):
        """Typed one-shot outbox handoff. No foreground preparation until terminal."""
        async with self._admission(session_id):
            reason = guard()
            if reason: return {"accepted": False, "reason": reason}
            if session_id not in self.workers and session_id in self._retired:
                session, emit = self._retired[session_id]
                await self._start_locked(session, emit)
            reason = guard()
            if reason: return {"accepted": False, "reason": reason}
            args = {"operation": "observation.submit", "arguments": arguments}
            pending = await self._admit(session_id, "control", args)
        return await self._reply(*pending, op="control", args=args)

    async def control(self, session_id, operation, arguments=None, *, guard=None):
        return await self._request(session_id, "control", operation=operation, arguments=arguments or {},
            **({'guard': guard} if guard is not None else {}))

    async def integration_validation_supported(self, session_id):
        """Require a positive current-worker capability; older adapters refuse."""
        row = self.workers.get(session_id)
        if not row or row['process'].returncode is not None or not row['ready'].done() or row['ready'].cancelled() or row['ready'].exception() is not None:
            return False
        try:
            async with asyncio.timeout(10):
                value = await self._request(session_id, 'dependencies', integrationValidation=True)
            supported = (self.workers.get(session_id) is row and row['process'].returncode is None
                         and value.get('integrationValidation') == 'admission-v1')
            if supported:
                row['integrationValidation'] = 'admission-v1'
            return supported
        except (RuntimeError, TimeoutError, AttributeError):
            return False

    async def dependencies(self, session_id, *, verify=False):
        """Inspect an existing worker without warming or acquiring its session."""
        row = self.workers.get(session_id)
        if not row or row['process'].returncode is not None or not row['ready'].done() or row['ready'].cancelled():
            return {'status': 'unavailable', 'sessionId': session_id, 'reason': 'No ready session runtime.'}
        if row['ready'].exception() is not None:
            return {'status': 'unavailable', 'sessionId': session_id, 'reason': 'Session runtime did not finish loading.'}
        try:
            return {**await self._request(session_id, 'dependencies', **({'verifyImports': True} if verify else {})), 'sessionId': session_id}
        except RuntimeError:
            return {'status': 'unknown', 'sessionId': session_id, 'reason': 'Runtime inspection did not return. No work was started.'}

    async def provenance(self, session_id, modules=()):
        """Inspect existing loaded origins; never start or unpark a worker."""
        row = self.workers.get(session_id)
        if not row or row['process'].returncode is not None or not row['ready'].done() or row['ready'].cancelled():
            return {'status': 'unknown', 'sessionId': session_id, 'reason': 'No ready session runtime.'}
        if row['ready'].exception() is not None:
            return {'status': 'unknown', 'sessionId': session_id, 'reason': 'Session runtime did not finish loading.'}
        try:
            async with asyncio.timeout(10):
                result = await self._request(session_id, 'dependencies', provenance=True, modules=list(modules))
            if self.workers.get(session_id) is not row or row['process'].returncode is not None:
                return {'status': 'unknown', 'sessionId': session_id, 'reason': 'Worker changed during loaded-source inspection.'}
            return {**result, 'sessionId': session_id, 'workerInstance': f"{row['process'].pid}:{row['started_at']}"}
        except (RuntimeError, TimeoutError):
            return {'status': 'unknown', 'sessionId': session_id, 'reason': 'Loaded-source inspection did not return. No work was started.'}

    async def resource_access(self, session_id, paths):
        """Read a ready worker's current file policy without mounting anything."""
        row = self.workers.get(session_id)
        unknown = {'status': 'unknown', 'paths': {}}
        if not row or row['process'].returncode is not None or not row['ready'].done() or row['ready'].cancelled():
            return unknown
        if row['ready'].exception() is not None:
            return unknown
        try:
            async with asyncio.timeout(10):
                result = await self._request(session_id, 'dependencies', resourceAccess=True, paths=paths)
            if self.workers.get(session_id) is not row or row['process'].returncode is not None:
                return unknown
            return result
        except (RuntimeError, TimeoutError):
            return unknown

    async def refresh_file_access(self, session_id, revision):
        """Refresh only an existing worker; never revive a retired runtime."""
        row = self.workers.get(session_id)
        if (not row or row['process'].returncode is not None or not row['ready'].done()
                or row['ready'].cancelled() or row['ready'].exception() is not None):
            return False
        try:
            async with asyncio.timeout(10):
                result = await self._request(session_id, 'file_access.refresh', settingsRevision=revision)
            return (self.workers.get(session_id) is row and row['process'].returncode is None
                    and result == {'settingsRevision': revision, 'boundary': 'next-tool-call'})
        except (RuntimeError, TimeoutError):
            return False

    async def desktop_readiness(self, session_id):
        """Inspect only the mounted worker; do not revive retired conversations."""
        row = self.workers.get(session_id)
        if not row or row['process'].returncode is not None or not row['ready'].done() or row['ready'].cancelled():
            return {'status': 'unavailable', 'sessionId': session_id, 'reason': 'No ready conversation runtime. Start the conversation normally, then check again.'}
        if row['ready'].exception() is not None:
            return {'status': 'unavailable', 'sessionId': session_id, 'reason': 'Conversation runtime did not finish loading.'}
        try:
            return {**await self._request(session_id, 'desktop.readiness'), 'sessionId': session_id}
        except RuntimeError:
            return {'status': 'unknown', 'sessionId': session_id, 'reason': 'Runtime inspection did not return. No work was started.'}

    async def shared_state_probe(self, request):
        """Run one read-only shared-state request inside the isolated runtime."""
        if not isinstance(request, dict):
            raise ValueError("Shared-state request must be an object.")
        command = self._command()
        command[-1] = str(Path(__file__).with_name("shared_state_probe.py"))
        if _WINDOWS:
            return await self._windows_shared_state_probe(command, request)
        proc = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=MAX_MESSAGE_BYTES,
        )
        try:
            proc.stdin.write(encode_message(request))
            await proc.stdin.drain()
            proc.stdin.close()
            line = await asyncio.wait_for(proc.stdout.readline(), 30)
            await asyncio.wait_for(proc.wait(), 30)
            response = json.loads(line) if line else None
            if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
                raise RuntimeError("Shared-state probe returned an invalid response.")
            if not response["ok"]:
                raise RuntimeError(response.get("error", {}).get("message") or "Shared-state probe failed.")
            return response["result"]
        except TimeoutError as exc:
            raise RuntimeError("The shared-state probe timed out without changing session ownership.") from exc
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def _windows_shared_state_probe(self, command, request):
        """Finite read-only probe uses the same containment, never a PID kill."""
        from .windows_jobs import WindowsOwnedProcess
        from .host.config import worker_environment
        job = await WindowsOwnedProcess.start(command, cwd=os.getcwd(),
            env=worker_environment(), trusted_python=getattr(sys, '_base_executable', None))

        async def discard_logs():
            # Drain without accumulating SDK logs or requiring newline framing.
            while await job.stderr.read(4096):
                pass

        logs = asyncio.create_task(discard_logs())
        try:
            async with asyncio.timeout(30):
                job.stdin.write(encode_message(request))
                await job.stdin.drain()
                job.stdin.close()
                line = await _BoundedWorkerLines(job.stdout).readline()
                result = await job.wait()
                await logs
            if result.payload_returncode != 0:
                raise RuntimeError('Shared-state probe did not exit successfully.')
            response = json.loads(line) if line else None
            if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
                raise RuntimeError("Shared-state probe returned an invalid response.")
            if not response["ok"]:
                raise RuntimeError(response.get("error", {}).get("message") or "Shared-state probe failed.")
            return response["result"]
        except TimeoutError as exc:
            raise RuntimeError("The shared-state probe timed out without changing session ownership.") from exc
        finally:
            try:
                await job.close()
            finally:
                logs.cancel()
                await asyncio.gather(logs, return_exceptions=True)

    async def message_worker(self, session_id, worker_id, text, input_id=None):
        return await self._request(session_id, "worker.message", worker_id=worker_id, text=text, input_id=input_id)

    async def steer_worker(self, session_id, worker_id, text):
        return await self._request(session_id, "worker.steer", worker_id=worker_id, text=text)

    async def stop_worker(self, session_id, worker_id):
        return await self._request(session_id, "worker.stop", worker_id=worker_id)

    async def stop(self, session_id, *, reason="stopped"):
        self._check_preparation_cleanup(session_id)
        self._retired.pop(session_id, None)
        preparation = self._preparations.get(session_id)
        if preparation:
            if reason == 'stopped' or 'stop_reason' not in preparation:
                preparation['stop_reason'] = reason
            if reason == 'stopped':
                preparation['shutdown'] = False
            preparation["stopped"] = True
            if preparation["task"] is not None:
                preparation["task"].cancel()
                await asyncio.gather(preparation["task"], return_exceptions=True)
        row = self.workers.get(session_id)
        if not row:
            return
        # A real Stop wins even if maintenance already started shutdown. A later
        # refresh cannot downgrade an explicit Stop that owns this process.
        if reason == 'stopped' or 'stop_reason' not in row:
            row['stop_reason'] = reason
        retirement = row.get('retirement_task')
        if retirement and retirement is not asyncio.current_task() and not retirement.done():
            retirement.cancel()
            await asyncio.gather(retirement, return_exceptions=True)
        previous = row.get('stop_task')
        if previous and previous.done() and row['process'].returncode is not None:
            failure = None if previous.cancelled() else previous.exception()
            if previous.cancelled() or failure is not None:
                # Retry only settlement for this confirmed-dead process, never
                # another stop/input against a process whose outcome is unknown.
                _LOG.warning('Retrying stopped worker settlement for %s after %s', session_id,
                             'cancellation' if previous.cancelled() else 'failure',
                             exc_info=(type(failure), failure, failure.__traceback__) if failure else None)
                row.pop('stop_task')
        if "stop_task" not in row:
            row["stop_task"] = asyncio.create_task(self._stop_row(session_id, row))
        await asyncio.shield(row["stop_task"])

    async def _stop_row(self, session_id, row):
        # An exit already observed before the shutdown boundary is still a
        # crash. Let its reader record that evidence before cancelling readers.
        if row['process'].returncode is not None and not row['closing']:
            try:
                await asyncio.shield(row['reader'])
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                _LOG.warning('Settling exited worker %s after its reader was cancelled', session_id)
            except Exception:
                # A failed reader (including failed error publication) cannot
                # keep a reaped process mounted forever. Preserve its exception
                # and still require successful owner-bound settlement below.
                _LOG.warning('Settling exited worker %s after reader failure', session_id, exc_info=True)
        row["closing"] = True
        if not row["ready"].done():
            row["ready"].cancel()
        proc = row["process"]
        windows_owner = row.get('windows_owner')
        if windows_owner is not None:
            if proc.returncode is None:
                try:
                    # Bound the write as well as the wait: a full stdin pipe
                    # must not prevent job cleanup after a real user Stop.
                    async with asyncio.timeout(_WINDOWS_STOP_SECONDS):
                        await self._write(row, {"op": "stop"})
                        await windows_owner.wait()
                except (TimeoutError, BrokenPipeError, ConnectionResetError):
                    await windows_owner.close()
            # Successful root exit alone is insufficient: require the retained
            # owner's zero-process/I/O proof before freeing leases and rows.
            await windows_owner.wait()
        elif proc.returncode is None:
            try:
                await self._write(row, {"op": "stop"})
                await asyncio.wait_for(proc.wait(), 15)
            except (TimeoutError, BrokenPipeError, ConnectionResetError):
                if proc.returncode is None:
                    if os.name != "nt":
                        os.killpg(proc.pid, signal.SIGTERM)
                    else:
                        proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), 5)
                    except TimeoutError:
                        if os.name != "nt":
                            os.killpg(proc.pid, signal.SIGKILL)
                        else:
                            proc.kill()
                        await proc.wait()
        for task in [row["reader"], row["stderr_task"], row["heartbeat"], *row["bridge_tasks"]]:
            if not task.done():
                task.cancel()
        await asyncio.gather(row["reader"], row["stderr_task"], row["heartbeat"], *row["bridge_tasks"], return_exceptions=True)
        await self._execution_ended(session_id, row, "stopped")
        cold = row.get('stop_reason') in {'configuration', 'shutdown', 'retention'}
        await row["emit"]("runtime.warmth" if cold else "runtime.status",
                          {"sessionId": session_id, "status": "cold" if cold else "stopped"})
        if self.workers.get(session_id) is row:
            self.workers.pop(session_id, None)
            if row.get('generation_lease'):
                row['generation_lease'].unlink(missing_ok=True)

    def begin_shutdown(self):
        """Synchronously fence admission and classify only our live owners."""
        self._closed = True
        for preparation in self._preparations.values():
            preparation['stopped'] = True
            if preparation.get('stop_reason') != 'stopped':
                preparation.update(shutdown=True, stop_reason='shutdown')
            if preparation['task'] is not None:
                preparation['task'].cancel()
        for row in self.workers.values():
            if row['process'].returncode is None:
                row['closing'] = True
                if row.get('stop_reason') != 'stopped':
                    row['stop_reason'] = 'shutdown'

    async def close(self):
        self.begin_shutdown()
        await self.retention.close()
        await asyncio.gather(*(self.stop(sid, reason='shutdown') for sid in set(self.workers) | set(self._preparations)), return_exceptions=True)
        self._retired.clear()

    async def spawn_worker(self, session, instruction, input_id, emit):
        """Ask the configured root to delegate through its normal tool hooks."""
        return await self.send(session,
            "Please delegate this request to a suitable configured agent as a persistent worker lane. "
            "Use the normal delegate tool and preserve all approval checks. Report the actual worker identity "
            "once created; if delegation is unavailable, explain that honestly. Request:\n\n" + instruction,
            input_id, emit)
