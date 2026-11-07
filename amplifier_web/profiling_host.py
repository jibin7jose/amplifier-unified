"""Debug-only adapter to the optional, host-independent profiling library.

Profiles are process-local and bounded. They are not app state, event capture,
or execution authority. Requests never enable debugging, select a PID, or
write a caller-supplied path. Only small command receipts are persisted.
"""
import asyncio
import hashlib
import importlib
import json
import yaml

from .deployment import config_path, validate_server

DEFINITIONS = {
    "profiling.status": ("Inspect optional host profiling availability and limits; never enables debugging.",
                        {"type": "object", "properties": {}, "additionalProperties": False}),
    "profiling.targets": ("List debug-enabled targets in the serving host process, not agent workers or arbitrary PIDs.",
                         {"type": "object", "properties": {}, "additionalProperties": False}),
    "profiling.start": ("Start a finite host Python capture when the operator enabled debug.profiling. Supply a stable command id; retries do not start another capture.",
                       {"type": "object", "additionalProperties": False,
                        "properties": {"target": {"type": "string", "minLength": 1, "maxLength": 100},
                                       "seconds": {"type": "number", "minimum": 0.1, "maximum": 60},
                                       "rateHz": {"type": "number", "minimum": 1, "maximum": 50}},
                        "required": ["target"]}),
    "profiling.stop": ("Request a retained capture to stop; does not stop the host or any work. Supply a stable command id.",
                      {"type": "object", "additionalProperties": False,
                       "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 100}},
                       "required": ["id"]}),
    "profiling.read": ("Read a bounded capture summary; settled raw profiles use the authenticated download URL. No full app snapshot or automatic upload.",
                      {"type": "object", "additionalProperties": False,
                       "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 100}},
                       "required": ["id"]}),
    "profiling.release": ("Explicitly release a settled process-local capture after saving any needed output. Never deletes canonical history. Supply a stable command id.",
                         {"type": "object", "additionalProperties": False,
                          "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 100}},
                          "required": ["id"]}),
}

def profiling_enabled(config):
    """Opaque config extensions cannot grant capture by truthiness."""
    debug = config.get("debug")
    return isinstance(debug, dict) and debug.get("profiling") is True


class ProfilingHost:
    def __init__(self, service, config):
        self.service = service
        self.settings = profiling_enabled(config)
        self.controller = None
        self.backend = None
        self.lock = asyncio.Lock()
        self.closed = False
        self.monitor = None
        self.pending_results = {}

    async def _refresh(self):
        # A changed operator config can revoke capture without a host restart.
        # A malformed/unreadable config fails closed, never grants authority.
        path = config_path(self.service.data_dir)
        def enabled():
            if not path.exists():
                return False
            return profiling_enabled(validate_server(yaml.safe_load(path.read_text()) or {}))
        try:
            self.settings = await asyncio.to_thread(enabled)
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            self.settings = False
        if self.closed:
            self.settings = False
        if self.settings and self.controller is None:
            try:
                self.backend = importlib.import_module("amplifier_profiling")
                self.controller = self.backend.Profiler(debug_enabled=True)
            except ImportError:
                self.backend = None
        if self.controller:
            self.controller.set_debug_enabled(self.settings)

    async def _monitor(self):
        try:
            while not self.closed and self.controller and self.controller.status()["activeCapture"]:
                await asyncio.sleep(0.25)
                async with self.lock:
                    await self._refresh()
        except asyncio.CancelledError:
            pass

    def status(self):
        state = self.controller.status() if self.controller else {
            "available": False, "debugEnabled": self.settings,
            "reason": ("The optional amplifier-profiling package is not installed"
                       if self.settings else "Host profiling is disabled"),
        }
        return {**state, "hostInstanceId": self.service.instance_id,
                "scope": "serving-host-process", "persistentResults": False}

    async def dispatch(self, action, args, command_id=None):
        from .errors import AppError
        async with self.lock:
            await self._refresh()
            if action == "profiling.status":
                return {"accepted": True, "effects": [], "result": self.status()}
            if action == "profiling.targets":
                return {"accepted": True, "effects": [], "result": {
                    **self.status(), "targets": self.controller.targets() if self.controller else []}}
            mutation = action in {"profiling.start", "profiling.stop", "profiling.release"}
            if mutation and (not isinstance(command_id, str) or not 1 <= len(command_id) <= 200):
                raise AppError("Profiling mutations require a stable command id.")
            fingerprint = hashlib.sha256(json.dumps([action, args], sort_keys=True).encode()).hexdigest()
            if mutation:
                prior = self.service.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?",
                                                (command_id,)).fetchone()
                if prior:
                    if prior[0] != fingerprint:
                        raise AppError("This command ID was used with different contents.", 409)
                    receipt = json.loads(prior[1])
                    receipt = self.pending_results.get(command_id, receipt)
                    if receipt["result"]["hostInstanceId"] != self.service.instance_id:
                        receipt["result"].update(available=False, state="unavailable_after_restart")
                    elif receipt["result"]["state"] == "admitting":
                        receipt["result"].update(state="unknown", available=False)
                    return {**receipt, "duplicate": True}
            if not self.settings or not self.controller:
                raise AppError(self.status().get("reason", "Host profiling is disabled"),
                               403, code="profiling_disabled")
            if mutation:
                if self.service.closed:
                    raise AppError("The host is shutting down.", 409)
                # Persist admission first. A crash must not cause an exact retry
                # to create another capture. Sampling has no remote effects.
                receipt = {"accepted": True, "effects": [], "result": {
                    "hostInstanceId": self.service.instance_id, "state": "admitting"}}
                self.service.db.execute("INSERT INTO commands VALUES (?,?,?)",
                                        (command_id, fingerprint, json.dumps(receipt)))
                self.service.db.commit()
            try:
                if action == "profiling.start":
                    result = self.controller.start(args["target"], seconds=args.get("seconds", 5),
                                                   rate_hz=args.get("rateHz", 20))
                    if not self.monitor or self.monitor.done():
                        self.monitor = asyncio.create_task(self._monitor())
                elif action == "profiling.stop":
                    result = self.controller.stop(args["id"])
                elif action == "profiling.release":
                    self.controller.release(args["id"])
                    result = {"id": args["id"], "state": "released"}
                else:
                    result = await asyncio.to_thread(self.controller.summary, args["id"])
                    await self._refresh()
                    if not self.settings:
                        raise AppError("Host profiling was revoked.", 403)
                    if result["state"] != "running":
                        result["downloadUrl"] = "/api/debug/profiles/" + args["id"]
                result = {**result, "hostInstanceId": self.service.instance_id,
                          "scope": "serving-host-process"}
            except self.backend.ProfilingError as exc:
                if mutation:
                    receipt = {"accepted": False, "effects": [], "result": {
                        "hostInstanceId": self.service.instance_id, "state": "refused", "error": str(exc)}}
                    self.service.db.execute("UPDATE commands SET receipt=? WHERE id=?",
                                            (json.dumps(receipt), command_id))
                    self.service.db.commit()
                raise AppError(str(exc), 409, code="profiling_refused") from None
            receipt = {"accepted": True, "effects": [], "result": result}
            if mutation:
                self.pending_results[command_id] = receipt
                self.service.db.execute("UPDATE commands SET receipt=? WHERE id=?",
                                        (json.dumps(receipt), command_id))
                self.service.db.commit()
                self.pending_results.pop(command_id, None)
            return receipt

    async def content(self, identity):
        from .errors import AppError
        async with self.lock:
            await self._refresh()
            if not self.settings or not self.controller:
                raise AppError("Host profiling is disabled.", 403)
            if not isinstance(identity, str) or not 1 <= len(identity) <= 100:
                raise AppError("Invalid capture identity.")
            try:
                result = await asyncio.to_thread(self.controller.read, identity)
            except self.backend.ProfilingError as exc:
                raise AppError(str(exc), 404) from None
            if result["state"] == "running":
                raise AppError("Capture has not settled.", 409)
            content = await asyncio.to_thread(json.dumps, result)
            await self._refresh()
            if not self.settings:
                raise AppError("Host profiling was revoked.", 403)
            return content

    async def close(self):
        self.closed = True
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        if self.controller:
            await asyncio.to_thread(self.controller.close)