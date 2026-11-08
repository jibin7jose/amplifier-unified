"""Owner-interactive Scheduled Task candidate, not Windows readiness attestation.

Only Task Scheduler owns the host lifetime. The action enters the existing CLI
in the same Python process; there is no detached child or independent supervisor.
Task-engine PID is deliberately NOT treated as the host PID. Native sign-out,
descendant cleanup, private logs and authenticated process correlation still need
qualification before the ordinary launcher/update path can use this backend.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path, PureWindowsPath
import re
import runpy
import subprocess
import sys
import sysconfig
import uuid
import xml.etree.ElementTree as ET

SOURCE = "amplifier-unified owner-logon v1"
NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
LIMIT = 128 * 1024
ET.register_namespace("", NAMESPACE)


class WindowsServiceError(RuntimeError):
    pass


def _sid(value: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"S-1-(?:\d+-)+\d+", value)
            or value in {"S-1-5-18", "S-1-5-19", "S-1-5-20"}):
        raise ValueError("An owner user SID is required.")
    return value


def _owner() -> str:
    if sys.platform != "win32":
        raise WindowsServiceError("This command requires native Windows Task Scheduler.")
    from .windows_files import current_process_sid
    try:
        return _sid(current_process_sid())
    except (OSError, ValueError, RuntimeError):
        raise WindowsServiceError("Cannot verify the Windows owner process-token SID.") from None


def task_name(sid: str) -> str:
    return "AmplifierUnified-" + _sid(sid)


def _text(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 16384
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("Task values must be bounded text without control characters.")
    return value


def _path(value: str) -> str:
    _text(value)
    path = PureWindowsPath(value)
    if (not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive)
            or any(part in {".", ".."} for part in value.replace("/", "\\")[3:].split("\\"))
            or any(char in value[2:] for char in '<>:"|?*')):
        raise ValueError("Task paths must be absolute local Windows drive paths.")
    return value


@dataclass(frozen=True)
class TaskSpec:
    sid: str
    python: str
    data_dir: str
    workspace: str
    state_home: str
    tool_path: str
    registration_id: str
    github_access: str = "auto"

    def __post_init__(self):
        _sid(self.sid)
        for value in (self.python, self.data_dir, self.workspace, self.state_home):
            _path(value)
        # ExecAction expands environment variables in Command/WorkingDirectory.
        # Encoded data/state roots are not scheduler properties and stay literal.
        if "%" in self.python or "%" in self.workspace:
            raise ValueError("Task executable and working-directory paths must not contain percent characters.")
        if PureWindowsPath(self.python).name.lower() != "python.exe":
            raise ValueError("The task requires an absolute Python interpreter, not a shell or Store alias.")
        _text(self.tool_path)
        if not re.fullmatch("[a-f0-9]{32}", self.registration_id):
            raise ValueError("Invalid task registration identity.")
        if self.github_access not in {"auto", "anonymous"}:
            raise ValueError("Invalid component download mode.")

    def encoded(self) -> str:
        # Only public launch configuration, never credentials, goes into XML.
        raw = json.dumps(asdict(self), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return base64.b64encode(raw.encode("ascii")).decode("ascii")

    @classmethod
    def decode(cls, value: str):
        if not isinstance(value, str) or len(value) > LIMIT:
            raise ValueError("Invalid task launch configuration.")
        return cls(**json.loads(base64.b64decode(value, validate=True).decode("utf-8")))

    def arguments(self) -> str:
        command = subprocess.list2cmdline(["-I", "-m", "amplifier_web.windows_service", "--run", self.encoded()])
        if len(command.encode("utf-16-le")) // 2 > 30000:
            raise ValueError("Task action exceeds the Windows command-line budget.")
        return command


def _q(name: str) -> str:
    return "{" + NAMESPACE + "}" + name


def task_xml(spec: TaskSpec) -> str:
    root = ET.Element(_q("Task"), version="1.2")

    def child(parent, name, text=None, **attributes):
        node = ET.SubElement(parent, _q(name), attributes)
        node.text = text
        return node

    info = child(root, "RegistrationInfo")
    child(info, "Author", spec.sid)
    child(info, "Description", SOURCE + ":" + spec.encoded())
    child(info, "URI", "\\" + task_name(spec.sid))
    child(info, "Source", SOURCE)
    trigger = child(child(root, "Triggers"), "LogonTrigger")
    child(trigger, "Enabled", "true")
    child(trigger, "UserId", spec.sid)
    principal = child(child(root, "Principals"), "Principal", id="Owner")
    child(principal, "UserId", spec.sid)
    child(principal, "LogonType", "InteractiveToken")
    child(principal, "RunLevel", "LeastPrivilege")
    settings = child(root, "Settings")
    for key, value in {
        "MultipleInstancesPolicy": "IgnoreNew",
        "DisallowStartIfOnBatteries": "false", "StopIfGoingOnBatteries": "false",
        "AllowHardTerminate": "true", "StartWhenAvailable": "false",
        "RunOnlyIfNetworkAvailable": "false", "AllowStartOnDemand": "true",
        "Enabled": "true", "Hidden": "false", "RunOnlyIfIdle": "false",
        "WakeToRun": "false", "ExecutionTimeLimit": "PT0S", "Priority": "7",
    }.items():
        child(settings, key, value)
    idle = child(settings, "IdleSettings")
    child(idle, "StopOnIdleEnd", "false")
    child(idle, "RestartOnIdle", "false")
    restart = child(settings, "RestartOnFailure")
    child(restart, "Interval", "PT1M")
    child(restart, "Count", "3")
    action = child(child(root, "Actions", Context="Owner"), "Exec")
    child(action, "Command", spec.python)
    child(action, "Arguments", spec.arguments())
    child(action, "WorkingDirectory", spec.workspace)
    xml = ET.tostring(root, encoding="unicode")
    if len(xml.encode("utf-8")) > LIMIT:
        raise ValueError("Task definition is too large.")
    return xml


def _tree(node):
    # Ignore presentation whitespace and sibling order, but not duplicate nodes,
    # attributes or unknown security/execution settings.
    text = node.text or ""
    if len(node) and not text.strip():
        text = ""
    return (node.tag, tuple(sorted(node.attrib.items())), text,
            tuple(sorted((_tree(child) for child in node), key=repr)))


def owned_spec(xml: str, sid: str, data_dir=None) -> TaskSpec:
    """Validate the complete supported definition before adoption or mutation.

    Scheduler-added registration dates carry no authority. Everything else must
    match our generated definition, including ALL actions, triggers and settings.
    A customized/foreign definition is never overwritten, even with --replace.
    """
    try:
        if not isinstance(xml, str) or len(xml.encode("utf-8")) > LIMIT or "<!" in xml:
            raise ValueError("Invalid task XML.")
        root = ET.fromstring(xml)
        description = root.findtext(f"{_q('RegistrationInfo')}/{_q('Description')}", "")
        if not description.startswith(SOURCE + ":"):
            raise ValueError("No ownership metadata.")
        spec = TaskSpec.decode(description[len(SOURCE) + 1:])
        if spec.sid != _sid(sid) or (data_dir is not None
                                    and PureWindowsPath(spec.data_dir) != PureWindowsPath(str(data_dir))):
            raise ValueError("Task belongs to another owner/data root.")
        info = root.find(_q("RegistrationInfo"))
        for date in list(info.findall(_q("Date"))):
            info.remove(date)
        if _tree(root) != _tree(ET.fromstring(task_xml(spec))):
            raise ValueError("Task definition does not match its ownership metadata.")
        return spec
    except (ValueError, TypeError, ET.ParseError, KeyError) as error:
        raise WindowsServiceError("Refusing unmanaged or changed Windows task definition.") from error


# Static source only. JSON is data on stdin, never substituted into PowerShell.
# COM uses TASK_LOGON_INTERACTIVE_TOKEN=3, TASK_CREATE=2 / TASK_UPDATE=4.
# Exact XML is rechecked on the live task immediately before each mutation.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $p = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -cne $p.sid) {
        throw 'Owner SID mismatch'
    }
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $task = $null
    try { $task = $folder.GetTask($p.name) }
    catch {
        $failure = $_.Exception
        while ($null -ne $failure.InnerException) { $failure = $failure.InnerException }
        if ($failure.HResult -ne -2147024894) { throw }
    }
    if ($p.op -ne 'query') {
        if ($null -eq $p.previous) {
            if ($null -ne $task) { throw 'Task appeared; reconcile before retrying' }
        } elseif ($null -eq $task -or $task.Xml -cne $p.previous) {
            throw 'Task changed; reconcile before retrying'
        }
        if ($p.op -in @('stop', 'remove', 'replace')) {
            $task.Stop(0)
            $deadline = [DateTime]::UtcNow.AddSeconds(10)
            while ($task.GetInstances(0).Count -ne 0) {
                if ([DateTime]::UtcNow -ge $deadline) { throw 'Task did not stop' }
                Start-Sleep -Milliseconds 100
            }
            if ($folder.GetTask($p.name).Xml -cne $p.previous) {
                throw 'Task changed while stopping; reconcile before retrying'
            }
        }
        switch ($p.op) {
            'create' { $task = $folder.RegisterTask($p.name, $p.xml, 2, $p.sid, $null, 3, $null) }
            'replace' { $task = $folder.RegisterTask($p.name, $p.xml, 4, $p.sid, $null, 3, $null) }
            'start' { if ($task.GetInstances(0).Count -eq 0) { $null = $task.Run($null) } }
            'stop' {}
            'remove' { $folder.DeleteTask($p.name, 0); $task = $null }
            default { throw 'Unsupported operation' }
        }
    }
    if ($null -eq $task) {
        @{exists=$false} | ConvertTo-Json -Compress
    } else {
        $xml = $task.Xml
        if ($xml.Length -gt 131072) { throw 'Task XML is too large' }
        @{exists=$true; xml=$xml; state=[int]$task.State;
          lastResult=[long]$task.LastTaskResult; instances=[int]($task.GetInstances(0).Count)} |
            ConvertTo-Json -Compress
    }
} catch {
    # No localized/secret-bearing error dump on the protocol channel.
    @{error='Task Scheduler operation failed; policy, identity or definition needs attention';
      hresult=$_.Exception.HResult} | ConvertTo-Json -Compress
    exit 1
}
"""


def _powershell() -> str:
    # Do not discover an executable in an untrusted workspace/PATH.
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.GetSystemDirectoryW.restype = ctypes.c_uint32
    length = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise WindowsServiceError("Cannot locate Windows PowerShell.")
    return str(PureWindowsPath(buffer.value) / "WindowsPowerShell/v1.0/powershell.exe")


def _scheduler(op: str, sid: str, *, previous=None, xml=None, name=None) -> dict:
    if _owner() != sid:
        raise WindowsServiceError("The task owner does not match the process token.")
    if op not in {"query", "create", "replace", "start", "stop", "remove"}:
        raise ValueError("Unknown task operation.")
    payload = json.dumps({"op": op, "sid": sid, "name": name or task_name(sid),
                          "previous": previous, "xml": xml}, ensure_ascii=True).encode("ascii")
    if len(payload) > 4 * LIMIT:
        raise ValueError("Task request is too large.")
    try:
        result = subprocess.run(
            [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")],
            input=payload, capture_output=True, timeout=20, check=False, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise WindowsServiceError(
            "Task Scheduler timed out; the outcome may be unknown. Read status before any retry."
        ) from error
    if len(result.stdout) > 4 * LIMIT:
        raise WindowsServiceError("Task Scheduler returned an oversized response; reconcile status.")
    try:
        value = json.loads(result.stdout.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as error:
        raise WindowsServiceError("Task Scheduler returned no valid receipt; reconcile status.") from error
    if result.returncode or not isinstance(value, dict) or "error" in value:
        raise WindowsServiceError("Task Scheduler refused the operation. Check desktop sign-in and task policy; "
                                  "no elevation/password fallback was attempted. Reconcile status before retrying.")
    if type(value.get("exists")) is not bool:
        raise WindowsServiceError("Task Scheduler returned an invalid receipt; reconcile status.")
    if (op in {"create", "replace", "start", "stop"} and not value["exists"]
            or op == "remove" and value["exists"]):
        raise WindowsServiceError("Task Scheduler did not confirm the requested task effect; reconcile status.")
    if value["exists"] and (
            not isinstance(value.get("xml"), str) or len(value["xml"].encode("utf-8")) > LIMIT
            or type(value.get("state")) is not int or value["state"] not in range(5)
            or type(value.get("instances")) is not int or value["instances"] < 0):
        raise WindowsServiceError("Task Scheduler returned an invalid task observation; reconcile status.")
    return value


def _read(data_dir=None):
    sid = _owner()
    value = _scheduler("query", sid)
    if value["exists"]:
        return value, owned_spec(value.get("xml"), sid, data_dir)
    return value, None


def managed(data_dir=None) -> bool:
    try:
        return _read(data_dir)[1] is not None
    except (OSError, ValueError, RuntimeError):
        return False


def task_state(data_dir=None) -> dict:
    value, spec = _read(data_dir)
    state = "not configured" if spec is None else {
        0: "unknown", 1: "disabled", 2: "starting", 3: "stopped", 4: "running",
    }.get(value.get("state"), "unknown")
    return {"state": state, "processVerified": False, "healthy": False,
            "taskPath": "\\" + task_name(spec.sid) if spec else None,
            "lastResult": value.get("lastResult"), "instances": value.get("instances"),
            "logsAvailable": False}


def _new_spec(data_dir, workspace, sid, previous):
    from .host_dependencies import tool_path
    from .shared_state import shared_state_home
    from .windows_files import address
    _require_python()
    access = os.environ.get("AMPLIFIER_GITHUB_ACCESS")
    return TaskSpec(sid=sid, python=_path(sys.executable),
                    data_dir=str(address(data_dir)), workspace=str(address(workspace or Path.cwd())),
                    state_home=str(address(shared_state_home())), tool_path=tool_path(),
                    registration_id=previous.registration_id if previous else uuid.uuid4().hex,
                    github_access=("anonymous" if access == "anonymous" else
                                   previous.github_access if access is None and previous else "auto"))


def _backup(spec: TaskSpec, xml: str) -> str:
    from .windows_files import address, directory
    root = address(spec.data_dir)
    path = root / "config" / "services"
    name = "task-backup-" + uuid.uuid4().hex + ".xml"
    with directory(path, owned_root=root, create=True) as pinned:
        pinned.publish_private(name, xml.encode("utf-8"))
    return str(path / name)


def install(data_dir, workspace=None, *, replace=False) -> dict:
    value, previous = _read()
    sid = previous.sid if previous else _owner()
    spec = _new_spec(data_dir, workspace, sid, previous)
    xml = task_xml(spec)
    backup = None
    if previous is not None and previous != spec:
        if not replace:
            raise WindowsServiceError("The generated Windows task has changed. Use --replace to save a "
                                      "private backup and replace the owned definition.")
        backup = _backup(previous, value["xml"])
    if previous != spec:
        result = _scheduler("replace" if previous else "create", sid,
                            previous=value.get("xml"), xml=xml)
        if not result["exists"] or owned_spec(result.get("xml"), sid) != spec:
            raise WindowsServiceError("Task registration was not verified; reconcile status.")
        value = result
    # Do not infer health or adopt a listener here. Existing host/reset exclusion
    # remains in CLI serve. A repeated install starts only a stopped owned task.
    _scheduler("start", sid, previous=value["xml"])
    return {"status": "current" if previous == spec else "replaced" if previous else "installed",
            "path": "\\" + task_name(sid), "healthy": False, "processVerified": False,
            **({"backup": backup} if backup else {})}


def command(name: str, data_dir=None):
    if name not in {"start", "stop", "restart", "status", "logs"}:
        raise ValueError(f"Unknown service command: {name}")
    if data_dir is None:
        raise WindowsServiceError("Windows service commands require an explicit data directory.")
    if name == "status":
        value = task_state(data_dir)
        print(json.dumps(value))
        return value
    if name == "logs":
        raise WindowsServiceError("Windows task stdout/stderr capture is not wired. "
                                  "Task Scheduler state is not an application log.")
    value, spec = _read(data_dir)
    if spec is None:
        raise WindowsServiceError("Amplifier Unified's owner-logon task is not installed.")
    if name in {"stop", "restart"}:
        _scheduler("stop", spec.sid, previous=value["xml"])
    if name in {"start", "restart"}:
        _scheduler("start", spec.sid, previous=value["xml"])


def uninstall(data_dir=None) -> None:
    if data_dir is None:
        raise WindowsServiceError("Windows service removal requires an explicit data directory.")
    value, spec = _read(data_dir)
    if spec is not None:
        result = _scheduler("remove", spec.sid, previous=value["xml"])
        if result["exists"]:
            raise WindowsServiceError("Task removal was not verified; reconcile status.")
    # App data, credentials, backups and personal startup entries are untouched.


def current_process_is_managed(data_dir) -> bool:
    # An environment marker or IRunningTask.EnginePID is not host-process proof.
    # Until native correlation is wired, disable automatic in-app restart.
    return False


def managed_restart_command() -> tuple[str, ...]:
    raise WindowsServiceError("Automatic Windows host restart is unavailable until exact task/host "
                              "process correlation is qualified. Use service restart from the owner launcher.")


def _require_python():
    if (sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13)
            or sysconfig.get_config_var("Py_GIL_DISABLED")):
        raise WindowsServiceError("The Windows task candidate requires ordinary CPython 3.13.")


def _run(encoded: str) -> None:
    _require_python()
    spec = TaskSpec.decode(encoded)
    if spec.sid != _owner():
        raise WindowsServiceError("The task owner does not match the process token.")
    value, installed = _read(spec.data_dir)
    if installed != spec or value.get("state") != 4:
        raise WindowsServiceError("The running task definition does not match this host launch.")
    os.environ["PATH"] = spec.tool_path
    os.environ["AMPLIFIER_SESSION_STATE_HOME"] = spec.state_home
    os.environ["AMPLIFIER_GITHUB_ACCESS"] = spec.github_access
    os.chdir(spec.workspace)
    sys.argv = [spec.python, "--no-open", "--data-dir", spec.data_dir,
                "--workspace", spec.workspace, "serve"]
    runpy.run_module("amplifier_web", run_name="__main__")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--run":
        raise SystemExit("This module is the owner-logon task action, not a service installer.")
    _run(sys.argv[2])