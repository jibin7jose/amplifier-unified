"""Bounded stdlib-only CI observer for offline Windows bootstrap fixtures.

No app imports, package resolution, policy changes or actual installation.
Only result.json is public; raw child output is temporary and sanitized first.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import sysconfig
import tempfile
import time
import unittest

ROOT = Path(__file__).absolute().parents[2]
INPUTS = (
    ".github/workflows/platform-mechanisms.yml",
    "scripts/install.ps1", "scripts/windows_prototype_launch.py",
    "scripts/windows_bootstrap_capsule.py",
    "tests/fixtures/windows_bootstrap_prototype.ps1",
    "tests/windows/install.Tests.ps1", "tests/test_windows_bootstrap_prototype.py",
    "tests/fixtures/windows_bootstrap_ci.py",
)
FIXTURES = (
    ("tests/fixtures/windows_bootstrap_prototype.ps1", "PROTOTYPE OFFLINE MODEL PASS",
     ["-Installer", "scripts/install.ps1"]),
    ("tests/windows/install.Tests.ps1", "OFFLINE FIXTURE PASS",
     ["-PythonExecutable", sys.executable]),
)
HOSTS = {"desktop": "powershell.exe", "core": "pwsh.exe"}
POLICY_PROBE = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=New-Object Security.Principal.WindowsPrincipal($identity)
[ordered]@{
 version=$PSVersionTable.PSVersion.ToString();edition=$PSVersionTable.PSEdition
 process64=[Environment]::Is64BitProcess;platform=[Environment]::OSVersion.Platform.ToString()
 elevated=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
 osBuild=[Environment]::OSVersion.Version.Build
 effectivePolicy=(Get-ExecutionPolicy).ToString()
 scopes=@(Get-ExecutionPolicy -List | ForEach-Object {
   @{scope=$_.Scope.ToString();policy=$_.ExecutionPolicy.ToString()}
 })
} | ConvertTo-Json -Depth 4 -Compress
"""
ROOTS_PROBE = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$parent=[Environment]::GetFolderPath('LocalApplicationData')
ConvertTo-Json -Compress -InputObject @(
 Get-ChildItem -LiteralPath $parent -Directory -Filter 'amplifier-install-*' |
 Select-Object -ExpandProperty FullName
 Get-ChildItem -LiteralPath ([IO.Path]::GetTempPath()) -Directory -Filter 'prototype-fixture-*' |
 Select-Object -ExpandProperty FullName
)
"""
PROCESSES_PROBE = r"""
$ErrorActionPreference='Stop'
ConvertTo-Json -Compress -InputObject @(
 Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $PID -and $_.CommandLine -and
  $_.CommandLine -match '(windows_bootstrap_prototype\.ps1|install\.Tests\.ps1|amplifier-install-[0-9a-f]{32}[\\/].*crt-probe\.ps1)'
 } | Select-Object -ExpandProperty ProcessId
)
"""


def report_path() -> Path:
    return Path(os.environ["RUNNER_TEMP"]) / "unified-bootstrap-offline" / "result.json"


def save(report: dict) -> None:
    path = report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)  # an outer interruption preserves the prior whole receipt


def hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in INPUTS}


def sanitize(text: str) -> str:
    # Fixtures are synthetic, but source locations and temp users are not public
    # evidence. No environment dump, raw SID, command exception or private path.
    for value, label in sorted(
        ((str(ROOT), "<checkout>"), (os.environ.get("RUNNER_TEMP", ""), "<runner-temp>"),
         (os.environ.get("USERPROFILE", ""), "<profile>"),
         (os.environ.get("LOCALAPPDATA", ""), "<local-app-data>")),
        key=lambda pair: len(pair[0]), reverse=True,
    ):
        if value:
            text = re.sub(re.escape(value), lambda _match: label, text, flags=re.I)
    text = re.sub(r"S-1-(?:\d+-)+\d+", "<sid>", text)
    text = re.sub(r"[A-Za-z]:\\[^\r\n\"<>]*", "<windows-path>", text)
    return text[:32768]


def child_env(host: str) -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if host != "desktop" or key.upper() != "PSMODULEPATH"}


def run(command: list[str], *, host: str, timeout: int = 90, public: bool = True) -> dict:
    """One finite child. Timeout remains failure/unknown descendant cleanup."""
    base = Path(tempfile.mkdtemp(prefix="bootstrap-ci-", dir=os.environ["RUNNER_TEMP"]))
    code, timed_out, exited, truncated, output, errors = None, False, False, False, "", ""
    try:
        with (base / "out").open("w+b") as stdout, (base / "err").open("w+b") as stderr:
            process = subprocess.Popen(command, cwd=ROOT, env=child_env(host),
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()  # only the exact retained child, never a PID/port search
                try:
                    code = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass  # retain unknown settlement; never start another fixture
            exited = process.poll() is not None
            truncated = stdout.seek(0, 2) > 32768 or stderr.seek(0, 2) > 32768
            stdout.seek(0); stderr.seek(0)
            output = stdout.read(32769).decode("utf-8-sig", errors="replace")
            errors = stderr.read(32769).decode("utf-8-sig", errors="replace")
    finally:
        try:
            shutil.rmtree(base)
            removed = True
        except OSError:
            # A descendant can retain a raw-output handle. Do not lose the
            # timeout receipt to a temporary-directory cleanup exception.
            removed = False
    return {
        "exitCode": code, "timedOut": timed_out,
        "outputTruncated": truncated,
        "stdout": sanitize(output) if public else output,
        "stderr": sanitize(errors) if public else errors,
        "directChildExited": exited, "captureDirectoryRemoved": removed,
        "descendantCleanup": "unknown-on-timeout" if timed_out else "fixture-finally-and-observations-required",
    }


def query(executable: str, code: str, host: str, *, timeout: int = 10):
    observed = run([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", code],
                   host=host, timeout=timeout, public=False)
    if (observed["exitCode"] or observed["timedOut"] or observed["outputTruncated"]
            or not observed.get("directChildExited", True) or not observed.get("captureDirectoryRemoved", True)):
        raise RuntimeError("Native observation prerequisite failed")
    return json.loads(observed["stdout"])


def source_identity() -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, timeout=15).strip()
    head, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    if not re.fullmatch(r"[0-9a-f]{40}", head) or head != os.environ["GITHUB_SHA"]:
        raise RuntimeError("Checkout does not match dispatched SHA")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Tracked source changed before checks")
    return {"head": head, "tree": tree, "sha256": hashes()}


def initialize() -> dict:
    report = {
        "schema": 1, "status": "FAIL", "qualification": "offline-bootstrap-fixtures-only",
        "publicAcceptance": False, "cleanMachineAcceptance": False,
        "initialize": "FAIL", "editions": {name: {"status": "NOT_RUN"} for name in HOSTS},
    }
    save(report)  # retained even if a prerequisite or native script policy refuses
    try:
        report["source"] = source_identity()
        report["python"] = {
            "version": list(sys.version_info[:3]), "platform": sys.platform,
            "implementation": sys.implementation.name, "bits": struct.calcsize("P") * 8,
            "freeThreaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        }
        if (sys.platform != "win32" or sys.implementation.name != "cpython"
                or sys.version_info[:2] != (3, 13) or struct.calcsize("P") != 8
                or sysconfig.get_config_var("Py_GIL_DISABLED")):
            raise RuntimeError("Requires ordinary Windows CPython 3.13 x64")
        report["hosts"] = {}
        for name, executable in HOSTS.items():
            path = shutil.which(executable)
            if not path:
                raise RuntimeError("Required native PowerShell host unavailable")
            facts = query(path, POLICY_PROBE, name)
            report["hosts"][name] = facts
            save(report)  # preserve available host/policy facts before next preflight
            expected = "5.1." if name == "desktop" else "7."
            if not facts["version"].startswith(expected) or facts["platform"] != "Win32NT" or not facts["process64"]:
                raise RuntimeError("Required native PowerShell host has wrong identity")
        # No policy mutation: an ordinary custom Actions -File wrapper must run
        # before --run can be reached. A refusal leaves that edition NOT_RUN.
        report["initialize"] = "PASS"
        return report
    finally:
        save(report)


def path_test() -> dict:
    spec = importlib.util.spec_from_file_location("bootstrap_model", ROOT / "tests/test_windows_bootstrap_prototype.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.TestSuite([
        module.LauncherTests("test_environment_survives_missing_shell_path_without_copying_keys"),
    ]))
    return {"testsRun": result.testsRun, "skips": len(result.skipped),
            "passed": result.wasSuccessful() and result.testsRun == 1 and not result.skipped,
            "output": sanitize(stream.getvalue())}


def run_edition(report: dict, host: str) -> None:
    if report["initialize"] != "PASS" or report["source"]["sha256"] != hashes():
        raise RuntimeError("Prerequisite or exact input identity changed")
    if any(row.get("status") == "RUNNING" or row.get("operationState") == "RUNNING"
           or any(item.get("timedOut") or item.get("executionState") == "RUNNING"
                  or not item.get("cleanupObserved", False) for item in row.get("fixtures", []))
           for row in report["editions"].values()):
        raise RuntimeError("A prior fixture has unknown descendant cleanup; no further execution")
    row = report["editions"][host] = {"status": "RUNNING", "fixtures": []}
    save(report)
    executable = shutil.which(HOSTS[host])
    if not executable:
        raise RuntimeError("Native host disappeared")
    # 2 * (90s fixture + up to10s exact-child termination + 3 * 10s
    # passive queries) = 260s. The aggregate deadline leaves 30s for result
    # finalization before the 300s Actions step budget.
    deadline = time.monotonic() + 270

    def budget(seconds: int) -> int:
        remaining = int(deadline - time.monotonic())
        if remaining < seconds:
            raise RuntimeError("Aggregate offline check deadline reached before next operation")
        return seconds

    def observe(code: str, phase: str):
        row["operationState"] = "RUNNING"
        row["operation"] = phase
        save(report)
        result = query(executable, code, host, timeout=budget(10))
        row["operationState"] = "COMPLETED"
        save(report)
        return result

    try:
        for fixture, marker, arguments in FIXTURES:
            before = set(observe(ROOTS_PROBE, "private-roots-before"))
            args = [str(ROOT / value) if value in INPUTS else value for value in arguments]
            command = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(ROOT / fixture), *args]
            observed = {
                "fixture": fixture, "executionState": "RUNNING", "cleanupObserved": False,
                "command": [HOSTS[host], "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                            fixture, *[("<python>" if value == sys.executable else value) for value in arguments]],
            }
            row["fixtures"].append(observed)
            save(report)  # retained BEFORE spawning; outer interruption is not absence
            observed.update(run(command, host=host, timeout=budget(90)))
            observed["executionState"] = "COMPLETED"
            observed["passMarker"] = marker in observed["stdout"]
            save(report)  # child failure/timeout retained before passive cleanup query
            if (observed["timedOut"] or not observed.get("directChildExited", True)
                    or not observed.get("captureDirectoryRemoved", True)):
                observed["cleanupObserved"] = False
                observed["cleanupUnknown"] = True
                save(report)
                raise RuntimeError("Fixture deadline expired; descendant cleanup is unknown")
            after = set(observe(ROOTS_PROBE, "private-roots-after"))
            observed["newPrivateRootsRemaining"] = len(after - before)
            observed["fixtureProcessesRemaining"] = len(observe(PROCESSES_PROBE, "fixture-processes-after"))
            observed["cleanupObserved"] = not (after - before) and not observed["fixtureProcessesRemaining"]
            save(report)
            if (observed["exitCode"] or not observed["passMarker"] or not observed["cleanupObserved"]
                    or observed["outputTruncated"]):
                raise RuntimeError("Native fixture did not qualify")
        row["pathTest"] = path_test()
        if not row["pathTest"]["passed"] or report["source"]["sha256"] != hashes():
            raise RuntimeError("PATH regression or final source identity failed")
        row["status"] = "PASS"
    finally:
        if row["status"] == "RUNNING":
            row["status"] = "FAIL"
        save(report)


def summarize(report: dict) -> bool:
    report["status"] = "PASS" if report["initialize"] == "PASS" and all(
        report["editions"][name]["status"] == "PASS" for name in HOSTS) else "FAIL"
    save(report)
    return report["status"] == "PASS"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--initialize", action="store_true")
    selection.add_argument("--run", choices=HOSTS)
    selection.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    try:
        if args.initialize:
            initialize()
        else:
            report = json.loads(report_path().read_text(encoding="utf-8"))
            if args.run:
                run_edition(report, args.run)
            elif not summarize(report):
                return 1
        return 0
    except Exception:
        # Safe fixed error only. Existing result retains actual bounded child
        # output or NOT_RUN when the Actions script wrapper itself was denied.
        print("Offline bootstrap qualification failed or could not start; inspect result.json.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())