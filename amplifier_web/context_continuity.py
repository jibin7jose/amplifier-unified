"""Host persistence for optional derived compaction checkpoints.

The canonical transcript is the existing evidence store. This adapter writes no
second output archive and never runs model work or replays a tool on restore.
"""
import asyncio
import copy
import hashlib
import inspect
import json
import logging
from pathlib import Path

from .host.config import write_private


_REFUSAL_CODES = {
    "recovery_source_changed", "recovery_evidence_required", "native_checkpoint_invalid",
    "recovery_no_eligible_work", "recovery_retention_changed", "native_measurement_unavailable",
    "recovery_request_changed", "native_no_reduction", "native_input_oversized",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class ContextRecoveryError(RuntimeError):
    """Fixed, content-free failure at the host recovery boundary."""
    def __init__(self, code):
        self.code = code
        super().__init__(f"Context recovery stopped [{code}]. Inspect the retained recovery receipt; no automatic retry is permitted.")


def _sync(callback):
    value = callback()
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise ContextRecoveryError("recovery_guard_unavailable")
    return value


async def _guarded(guard, callback, /, *args, **kwargs):
    if guard:
        guard()
    try:
        value = callback(*args, **kwargs)
        value = await value if inspect.isawaitable(value) else value
    except BaseException:
        # A stale observer must not replace the original cancellation/failure.
        if guard:
            try:
                guard()
            except BaseException:
                pass
        raise
    if guard:
        guard()
    return value


class ContextContinuity:
    def __init__(self, coordinator, session_id, directory, checkpoint, *, writer_guard=None):
        self.coordinator = coordinator
        self.session_id = session_id
        self.path = Path(directory) / "context-checkpoint.json"
        self.checkpoint = checkpoint
        self.loaded = False
        self.saved_digest = None
        self._identity = None
        self.writer_guard = writer_guard
        self.recovery_path = Path(directory) / "context-recovery.json"
        self._journal = None
        self._recovery_busy = False
        self._recovery_guard = None
        self._suppress_save = False
        self.status = {"supported": bool(coordinator.get_capability("context.checkpoint.export")), "status": "pending"}
        coordinator.register_capability("context.checkpoint_identity", lambda: copy.deepcopy(self._identity))
        coordinator.register_capability("context.preserve_evidence", self.preserve)
        coordinator.register_capability("context.persist_checkpoint", self.save)
        coordinator.register_capability("web.continuity.status", self.public)
        coordinator.register_capability("web.continuity.recovery", self)
        coordinator.register_capability("context.recovery.run_pending", self.run_pending)

    def public(self):
        getter = self.coordinator.get_capability("context.checkpoint.status")
        module_status = getter() if getter else {}
        if self.loaded and self.status["supported"] and (not self._identity or not all(self._identity.values())):
            return {"supported": True, "status": "unavailable", "reason": "An explicit provider and model identity is required.", "originalsAvailable": True}
        if self.status.get("status") == "rejected":
            return copy.deepcopy(self.status)
        return {**copy.deepcopy(self.status), **module_status}

    async def refresh_identity(self):
        providers = self.coordinator.get("providers") or {}
        loop = self.coordinator.get("orchestrator")
        provider = (getattr(loop, "root_provider", None) or loop._select_provider(providers)) if providers else None
        if provider is None:
            self._identity = None
            return
        info = provider.get_info()
        if inspect.isawaitable(info):
            info = await info
        defaults = info.get("defaults", {}) if isinstance(info, dict) else getattr(info, "defaults", {})
        name = info.get("id") if isinstance(info, dict) else getattr(info, "id", None)
        original = getattr(provider, "original", provider)
        instance = next((key for key, value in providers.items() if value is original), name)
        self._identity = {"provider": instance or name, "model": (defaults or {}).get("model") or (defaults or {}).get("default_model")}

    async def preserve(self, messages):
        self._check_restore_disposition()
        guard = self._recovery_guard
        await _guarded(guard, self.refresh_identity)
        # Read before checkpoint(): that callback can itself call save(). An
        # unloaded accepted checkpoint must never be replaced by active defaults.
        record, restore, present, read_failed = None, None, False, False
        if not self.loaded:
            restore = self.coordinator.get_capability("context.checkpoint.restore")
            present = bool(restore and self.path.exists())
            if present:
                try:
                    record = json.loads(self.path.read_text())
                except (ValueError, OSError, TypeError) as exc:
                    read_failed = True
                    self.status.update(status="rejected", reason=str(exc), originalsAvailable=True)
        # Native storage is already lossless for these messages. Commit it before
        # any request fitter sees and potentially clips a derived copy.
        current = digest(messages)
        if current != self.saved_digest:
            await _guarded(guard, self.checkpoint)
            self.saved_digest = current
        if not self.loaded:
            self._check_restore_disposition()
            self.loaded = True
            if present:
                try:
                    if not read_failed:
                        self.status.update(restore(record, self._identity))
                except (ValueError, OSError, TypeError) as exc:
                    self.status.update(status="rejected", reason=str(exc), originalsAvailable=True)
                if self.coordinator.hooks:
                    try:
                        await _guarded(guard, self.coordinator.hooks.emit, "context:checkpoint_restored", {
                            key: value for key, value in self.status.items()
                            if key in {"status", "reason", "throughMessage", "completedParts", "originalsAvailable"}})
                    except Exception as exc:
                        # Observability cannot invalidate an accepted checkpoint
                        # or turn successful recovery into a conversation fault.
                        logging.getLogger(__name__).warning("Context checkpoint restore observer failed (%s)", type(exc).__name__)
        # References identify exact original content in the canonical transcript.
        # Process tool output can additionally contain operation_output references
        # supplied by its operation journal; do not copy that archive here.
        return [{"kind": "transcript", "sessionId": self.session_id, "messageIndex": index,
                 "sha256": digest(row), "toolCallId": row.get("tool_call_id"), "sourceRevision": current}
                for index, row in enumerate(messages)
                if row.get("role") == "tool" and len(json.dumps(row, ensure_ascii=False)) > 16000]

    def save(self):
        if not self.loaded or self._recovery_busy:
            return
        self._recovery_record()
        if self._suppress_save:
            return
        export = self.coordinator.get_capability("context.checkpoint.export")
        if export is None or not self._identity or not all(self._identity.values()):
            return
        writer = self.writer_guard
        if writer is not None:
            self._check_writer()
        record = export(self._identity)
        if record is not None:
            text = json.dumps(record, ensure_ascii=False)
            if self.writer_guard is not writer:
                raise ContextRecoveryError("recovery_writer_unavailable")
            if writer is not None:
                self._check_writer()
            write_private(self.path, text)
            self.status = {"supported": True, "status": "progress_saved" if record.get("progress") else "saved", "sourceRevision": record["sourceRevision"], "originalsAvailable": True}

    def _check_restore_disposition(self):
        current = self._recovery_record()
        if current and current["state"] != "committed" and current.get("candidateSha256"):
            # Storage acknowledgement is not activation/commit. In particular,
            # a non-measured preparation must not reconcile an unknown install.
            raise ContextRecoveryError("recovery_unreconciled")

    def _recovery_record(self):
        # One read per mount; ordinary no-intent preparation does not scan history
        # or repeatedly parse private native windows. This is the sole journal.
        if self._journal is None:
            try:
                journal = json.loads(self.recovery_path.read_text())
                if (journal.get("version") != 1 or not isinstance(journal.get("attempts"), dict)
                        or journal.get("current") not in journal["attempts"]):
                    raise ValueError()
                for key, row in journal["attempts"].items():
                    source = row.get("sourceRevision") or {}
                    contract = row.get("contract")
                    if (not isinstance(key, str) or not 0 < len(key) <= 200 or row.get("commandId") != key
                            or row.get("state") not in {"pending", "running", "installing", "committed", "failed", "refused", "unknown"}
                            or type(row.get("interruptionRevision")) is not int or row["interruptionRevision"] < 0
                            or type(row.get("targetBytes")) is not int or row["targetBytes"] < 1
                            or not isinstance(row.get("identity"), dict) or not isinstance(contract, dict)
                            or set(contract) != {"version", "sha256"} or type(contract["version"]) is not int
                            or contract["version"] != 1 or not isinstance(contract["sha256"], str)
                            or len(contract["sha256"]) != 64
                            or any(char not in "0123456789abcdef" for char in contract["sha256"])
                            or type(source.get("messages")) is not int or source["messages"] < 1
                            or not isinstance(source.get("sha256"), str) or len(source["sha256"]) != 64
                            or type(row.get("completedParts")) is not int or row["completedParts"] < 0):
                        raise ValueError()
            except FileNotFoundError:
                journal = {"version": 1, "current": None, "attempts": {}}
            except (ValueError, OSError, TypeError, AttributeError, KeyError):
                self._suppress_save = True
                raise ContextRecoveryError("recovery_journal_unavailable") from None
            self._journal = journal
        current = self._journal["attempts"].get(self._journal["current"])
        if current and current["state"] != "committed" and current.get("candidateSha256"):
            self._suppress_save = True
        return current

    def _check_writer(self):
        if not callable(self.writer_guard):
            raise ContextRecoveryError("recovery_writer_unavailable")
        outcome = _sync(self.writer_guard)
        if outcome is not None and outcome is not True:
            raise ContextRecoveryError("recovery_writer_unavailable")

    def _write_recovery(self):
        self._check_writer()
        text = json.dumps(self._journal, ensure_ascii=False, allow_nan=False)
        write_private(self.recovery_path, text)
        if self.recovery_path.read_text() != text:
            raise ContextRecoveryError("recovery_save_unconfirmed")

    def recovery_status(self):
        return self._recovery_public(self._recovery_record())

    def _recovery_public(self, record):
        result = {"supported": callable(self.coordinator.get_capability("context.recovery.recover")),
                  "state": "none", "attempts": len(self._journal["attempts"])}
        if record:
            state, code = record["state"], record.get("code")
            if state in {"running", "installing"} and not self._recovery_busy:
                state, code = "unknown", "recovery_interrupted"
            result.update(commandId=record["commandId"], state=state, code=code,
                          sourceMessages=record["sourceRevision"]["messages"],
                          completedParts=record.get("completedParts", 0))
            result["verification"] = {key: value for key, value in record.get("verification", {}).items()
                                      if key in {"before", "after", "limit", "throughMessage"} and type(value) is int}
        return result

    def _fresh_guard(self):
        cap = self.coordinator.get_capability
        names = ("live.activation", "live.runtime", "web.context_recovery.epoch",
                 "web.context_recovery.contract", "context.recovery.recover", "context.history",
                 "web.app_access.bridge")
        bound = {name: cap(name) for name in names}
        gate, runtime = bound["live.activation"], bound["live.runtime"]
        writer = self.writer_guard
        cancellation = getattr(self.coordinator, "cancellation", None)
        if (not callable(writer) or gate is None or runtime is None or cancellation is None
                or not all(callable(bound[name]) for name in names[2:])):
            raise ContextRecoveryError("recovery_guard_unavailable")
        token = gate.current()
        epoch = _sync(bound["web.context_recovery.epoch"])
        def contract():
            value = _sync(bound["web.context_recovery.contract"])
            if not isinstance(value, dict) or not value:
                raise ContextRecoveryError("recovery_contract_unavailable")
            return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
        configuration = contract()
        if type(epoch) is not int:
            raise ContextRecoveryError("recovery_guard_unavailable")
        def check():
            gate.check(token)
            current_epoch = _sync(bound["web.context_recovery.epoch"])
            if (gate.current() is not token or self.writer_guard is not writer
                    or any(cap(name) is not value for name, value in bound.items())
                    or getattr(self.coordinator, "cancellation", None) is not cancellation
                    or getattr(cancellation, "is_cancelled", True)
                    or getattr(runtime, "stop_requested", True) or getattr(runtime, "closed", True)
                    or type(current_epoch) is not int or current_epoch != epoch):
                raise ContextRecoveryError("recovery_interrupted")
            if contract() != configuration:
                raise ContextRecoveryError("recovery_contract_changed")
            outcome = _sync(writer)
            if outcome is not None and outcome is not True:
                raise ContextRecoveryError("recovery_writer_unavailable")
        check()
        # Keep the exact comparison only in this invocation. The journal must
        # not duplicate materialized credentials, prompts or tool schemas.
        return check, {"version": 1, "sha256": digest(configuration)}

    async def _interruption_revision(self):
        bridge = self.coordinator.get_capability("web.app_access.bridge")
        if not callable(bridge):
            raise ContextRecoveryError("recovery_stop_unavailable")
        response = await bridge("dispatch", {"action": "coordination.list", "args": {
            "sessionId": self.session_id, "limit": 1, "includeWorkers": False}})
        result = response.get("result") if isinstance(response, dict) else None
        items = result.get("items") if isinstance(result, dict) else None
        if (not isinstance(items, list) or len(items) != 1
                or not isinstance(items[0], dict)
                or items[0].get("target") != {"sessionId": self.session_id}
                or not isinstance(items[0].get("signal"), dict)
                or type(items[0]["signal"].get("interruptionRevision")) is not int
                or items[0]["signal"]["interruptionRevision"] < 0):
            raise ContextRecoveryError("recovery_stop_unavailable")
        return items[0]["signal"]["interruptionRevision"]

    async def request_recovery(self, command_id, interruption_revision, target_bytes=1_500_000):
        if (not isinstance(command_id, str) or not 0 < len(command_id) <= 200
                or type(interruption_revision) is not int or interruption_revision < 0
                or type(target_bytes) is not int or target_bytes < 1):
            raise ContextRecoveryError("recovery_arguments_invalid")
        current = self._recovery_record()
        existing = self._journal["attempts"].get(command_id)
        if existing:
            if (existing["interruptionRevision"], existing["targetBytes"]) != (interruption_revision, target_bytes):
                raise ContextRecoveryError("recovery_command_changed")
            return self._recovery_public(existing)
        if self._recovery_busy or current and current["state"] in {"pending", "running", "installing", "unknown"}:
            raise ContextRecoveryError("recovery_unreconciled")
        self._recovery_busy = True
        record = None
        try:
            guard, configuration = self._fresh_guard()
            await _guarded(guard, self.refresh_identity)
            if not self._identity or not all(self._identity.values()):
                raise ContextRecoveryError("recovery_identity_unavailable")
            messages = await _guarded(guard, self.coordinator.get_capability("context.history"))
            if not isinstance(messages, list) or not messages:
                raise ContextRecoveryError("recovery_source_unavailable")
            source = {"messages": len(messages), "sha256": digest(messages)}
            if await _guarded(guard, self._interruption_revision) != interruption_revision:
                raise ContextRecoveryError("recovery_interrupted")
            record = {"commandId": command_id, "state": "pending", "code": None,
                      "interruptionRevision": interruption_revision, "targetBytes": target_bytes,
                      "contract": configuration, "identity": copy.deepcopy(self._identity),
                      "sourceRevision": source, "progress": None, "completedParts": 0}
            self._journal["attempts"][command_id] = record
            self._journal["current"] = command_id
            self._write_recovery()
            guard()
            return self._recovery_public(record)
        except BaseException:
            if record is not None:
                # A failed acknowledgement is not permission to arm again.
                record.update(state="unknown", code="recovery_save_unconfirmed")
                try:
                    self._write_recovery()
                except BaseException:
                    pass
            raise
        finally:
            self._recovery_busy = False

    async def run_pending(self, *, provider, retain_contents=(), count_view):
        record = self._recovery_record()
        if record is None or record["state"] == "committed":
            return None
        if self._recovery_busy or record["state"] != "pending":
            raise ContextRecoveryError("recovery_unreconciled")
        self._recovery_busy = True
        entered = False
        compaction_error = ()
        try:
            record.update(state="running", code=None)
            self._write_recovery()  # Consume durably before any provider callback.
            guard, configuration = self._fresh_guard()
            self._recovery_guard = guard
            if configuration != record["contract"]:
                raise ContextRecoveryError("recovery_contract_changed")
            await _guarded(guard, self.refresh_identity)
            if self._identity != record["identity"]:
                raise ContextRecoveryError("recovery_identity_changed")
            async def check_stop():
                if await _guarded(guard, self._interruption_revision) != record["interruptionRevision"]:
                    raise ContextRecoveryError("recovery_interrupted")
            await check_stop()
            messages = await _guarded(guard, self.coordinator.get_capability("context.history"))
            source = record["sourceRevision"]
            if len(messages) < source["messages"] or digest(messages[:source["messages"]]) != source["sha256"]:
                raise ContextRecoveryError("recovery_source_changed")
            # Restore before the module snapshots active derived state. Its later
            # preserve callback must not perform a lazy restore inside that fence.
            await _guarded(guard, self.preserve, messages)
            if self.status.get("status") == "rejected":
                raise ContextRecoveryError("recovery_checkpoint_rejected")

            def save_progress(progress, _metadata):
                guard()
                record["progress"] = copy.deepcopy(progress)
                record["completedParts"] += 1
                self._write_recovery()
                guard()

            async def install_candidate(candidate, verification):
                await check_stop()
                sha = candidate.get("sha256")
                if (not isinstance(sha, str) or sha != digest({key: value for key, value in candidate.items() if key != "sha256"})
                        or verification.get("sha256") != sha):
                    raise ContextRecoveryError("recovery_candidate_invalid")
                guard()
                self._suppress_save = True
                record.update(state="installing", candidateSha256=sha, verification=copy.deepcopy(verification))
                self._write_recovery()  # A crash from here needs reconciliation.
                guard()
                write_private(self.path, json.dumps(candidate, ensure_ascii=False, allow_nan=False))
                if json.loads(self.path.read_text()) != candidate:
                    raise ContextRecoveryError("recovery_install_unconfirmed")
                guard()
                record["installedCandidate"] = sha
                self._write_recovery()
                guard()
                return {"sha256": sha}

            # This optional dependency is needed only for an explicitly armed
            # operation. Ordinary continuity never imports the recovery module.
            from amplifier_module_context_managed.errors import CompactionError
            compaction_error = CompactionError
            entered = True
            result = await _guarded(guard, self.coordinator.get_capability("context.recovery.recover"),
                provider=provider, count_view=count_view, identity=copy.deepcopy(self._identity),
                save_progress=save_progress, install=install_candidate, guard=guard,
                retain_contents=retain_contents, target_bytes=record["targetBytes"],
                progress=record["progress"], source_revision=copy.deepcopy(source))
            await check_stop()
            verification = result.get("recovery", {})
            if (verification.get("status") != "installed" or not record.get("installedCandidate")
                    or verification.get("sha256") != record["installedCandidate"]):
                raise ContextRecoveryError("recovery_install_unconfirmed")
            record.update(state="committed", code=None)
            self._write_recovery()
            guard()
            self._suppress_save = False
            return result
        except BaseException as exc:
            # Provider/host effects cannot be rolled back. In particular, never
            # overwrite a candidate after storage succeeded but activation failed.
            state = "unknown" if entered or self._suppress_save else "failed"
            code = ("recovery_outcome_unknown" if entered else
                    exc.code if isinstance(exc, ContextRecoveryError) else "recovery_preparation_failed")
            if isinstance(exc, asyncio.CancelledError) and not entered:
                code = "recovery_interrupted"
            if (entered and not self._suppress_save and isinstance(exc, compaction_error)
                    and exc.code in _REFUSAL_CODES):
                state, code = "refused", exc.code
            record.update(state=state, code=code)
            try:
                self._write_recovery()
            except BaseException:
                pass  # The prior running/installing receipt remains non-retryable.
            raise
        finally:
            self._recovery_guard = None
            self._recovery_busy = False


def install(coordinator, session_id, directory, checkpoint, *, writer_guard=None):
    return ContextContinuity(coordinator, session_id, directory, checkpoint, writer_guard=writer_guard)
