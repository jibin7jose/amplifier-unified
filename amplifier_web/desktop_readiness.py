"""Application setup projection; no tool activation, capture or account discovery.

The host's optional foreground library and the conversation's mounted tools
belong to different interpreters. Only already-running workers are inspected.
Desktop diagnostics remain the mounted tool's existing doctor action, invoked
explicitly through runtime.control/tool.invoke and its normal policy hooks.
"""
from importlib import metadata
import platform
import sys
import time

from .host_identity import local_host_identity


def environment():
    try:
        version = metadata.version("amplifier-module-tool-computer-use")
    except metadata.PackageNotFoundError:
        version = None
    return {"host": local_host_identity(), "python": {"path": sys.executable,
        "version": platform.python_version()}, "platform": platform.system(),
        "computerUsePackage": {"status": "installed" if version else "missing",
            "version": version, "importVerified": False}}


def worker_report(controls):
    """Read public configuration/catalog metadata, never execute a tool."""
    config = controls.configuration()
    tools = controls.coordinator.get("tools") or {}
    desktop = tools.get("desktop")
    schema = getattr(desktop, "input_schema", {}) if desktop else {}
    actions = schema.get("properties", {}).get("action", {}).get("enum", [])
    unavailable = tools.get("computer_use_unavailable")
    return {**environment(), "status": "available", "observedAt": time.time(),
        "catalogRevision": controls.catalog_revision,
        "modules": [{key: row.get(key) for key in ("module", "id", "enabled")}
            for row in config["modules"] if row["section"] == "tools"],
        "mountedTools": sorted(tools),
        "computerControl": {"status": "unavailable" if "computer_use_unavailable" in tools
            else "mounted" if desktop else "not_mounted", "doctorSupported": "doctor" in actions,
            "detail": str(getattr(unavailable, "description", ""))[:1600] if unavailable else None},
        "browserContext": {"status": "not_exposed",
            "reason": "A tool name or installed package does not identify a browser, tab or signed-in account. Inspect the selected adapter through its own published actions."}}


def native_next_step(native):
    code = native.get("code") or native.get("status")
    if code == "backend_not_installed":
        return "Install Unified's optional native-desktop extra in the serving app environment shown here, then check again. Installing it only in a conversation worker does not enable native screen observation. Coordinate any managed app replacement or restart with its owner."
    if code == "unsupported":
        return "Native foreground observation supports a local macOS app host. Use Choose screen source in Chat controls → Computer use to share from the browser device instead."
    if code in {"permission_required", "permission_unknown"}:
        return "On the named Mac host, check System Settings → Privacy & Security → Screen Recording for the application running this Python process. If permission is missing, the user must grant it there, then check again. Accessibility permission is not required for screen observation."
    if native.get("available") is True:
        return "In Chat controls → Computer use, check the desktop host and explicitly allow foreground snapshots from that host. Each capture is a separate action."
    return "Check the named host's desktop session and optional native-desktop installation, then retry. A failed check does not grant permission or capture a screen."


async def inspect(service, sid):
    from .app_features import status as feature_status
    try:
        native = await service.voice_visual.native.run("status")
    except (TimeoutError, ValueError, OSError):
        native = {"available": False, "status": "error", "code": "native_status_failed"}
    # A helper status is bounded to availability fields, never observation data.
    reason = native.get("reason")
    native = {key: native[key] for key in ("available", "status", "code", "permission", "backend") if key in native}
    if isinstance(reason, str):
        native["reason"] = reason[:300]
    worker = {"status": "unavailable", "sessionId": sid, "reason": "No ready conversation runtime. Start a conversation normally, then check again."}
    if sid and service.runtime and hasattr(service.runtime, "desktop_readiness"):
        worker = await service.runtime.desktop_readiness(sid)
    voice = service.state.get("voice", {})
    visual = service.voice_visual.status(sid, voice.get("id")) if sid and voice.get("sessionId") == sid else {}
    client_id = service.clients.current.get()
    if client_id is not None and visual.get("clientId") != client_id:
        visual = {}
    computer = service.computer_visual.project(client_id)
    if computer.get("available") and computer.get("sessionId") == sid:
        visual = computer
    source = visual.get("source") if visual.get("available") else None
    native_source = source and source.get("kind") == "native-foreground"
    return {"schemaVersion": 1, "observedAt": time.time(), "sessionId": sid,
        "host": {**environment(), "instanceId": service.instance_id},
        "nativeObservation": {**native, "nextStep": native_next_step(native)},
        "featureSetup": feature_status(getattr(service, 'update_manager', None)),
        "worker": worker,
        "voiceObservation": {"status": "source_selected" if source else "not_shared",
            "callId": voice.get("id") if voice.get("sessionId") == sid else None,
            "source": source, "expiresAt": visual.get("expiresAt") if source else None,
            "browserContext": "The native source is bound to the named host and app instance. Window identity is supplied only by an explicit capture; browser tabs and accounts are not exposed."
                if native_source else "The browser reports only the selected source label and kind. Tab URL, browser profile and signed-in account are not exposed by this transport.",
            "nextStep": "Use Capture screen for one snapshot from the selected source, or Stop screen sharing to revoke the selected source permission. Screen observation grants no desktop control."
                if source else "Open Chat controls → Computer use and choose a screen source in this browser. Text and voice use the same sharing permission. A direct user click opens the browser picker. Screen observation grants no desktop control."},
        "nextActions": [
            {"label": "Updates", "action": "view.update", "args": {"patch": {"panel": "settings", "settingsSection": "maintenance", "settingsExpanded": ["updates"]}}},
            {"label": "Bundles & modules", "action": "view.update", "args": {"patch": {"panel": "settings", "settingsSection": "capabilities", "settingsExpanded": ["app-bundles"]}}},
            {"label": "Smart Tools connections", "action": "view.update", "args": {"patch": {"panel": "settings", "settingsSection": "capabilities", "settingsExpanded": ["smart-tools"]}}},
            {"label": "Session tools", "action": "view.update", "args": {"patch": {"panel": "settings", "settingsSection": "setup", "settingsExpanded": ["runtime"], "runtimeDraft": {"tab": "tools"}}}},
        ],
        "boundaries": "This check runs a prompt-free native status preflight and reads mounted-tool metadata. It never starts a worker, activates computer control, discovers accounts, captures content, changes providers or clears a safety halt."}


async def dispatch(service, args, origin, expected_revision, caller_session_id):
    from .errors import AppError
    async with service.lock:
        if expected_revision is not None and expected_revision != service.state["revision"]:
            raise AppError("The app changed. Refresh its state and retry.", 409)
        sid = args.get("sessionId") or service.state.get("selectedSessionId")
        if origin == "agent":
            if not caller_session_id or (args.get("sessionId") and args["sessionId"] != caller_session_id):
                raise AppError("Desktop readiness must target the calling conversation.", 409)
            sid = caller_session_id
        if sid:
            service._session(sid)
    result = await inspect(service, sid)
    return {"accepted": True, "revision": service.state["revision"], "effects": [], "result": result}
