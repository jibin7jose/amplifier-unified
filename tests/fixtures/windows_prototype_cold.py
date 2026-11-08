"""Manual real-bootstrap CI driver. Preparation/tests are NOT native acceptance.

The controller may use setup-python/Git to inspect source. The actual installer
gets only Windows/System32 on PATH and the existing user's standard profile.
No app server, browser, TLS, provider or worker is started. Unknown effects never
replay. Only the fixed-field result.json is suitable for public CI retention.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import sysconfig
import time
import types
from urllib.parse import urlsplit
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
INSTALLER_SHA = "73c31490c302372e620eaf7ffedd8ad0180b1e1950c2f1a89554d26b66f93cb1"
APP_SOURCE = "1c4ac3b4f9ed5d2d3f588f6fa6c74a119e439153"
MANIFEST_SHA = "a54ea968ba1c9f0bb1fb5f182edee3a3c53c707de8cddb3533bee2fae5935b57"
CAPSULE_SHA = "c35e1a66b4de88aebc871bd8a6b025eb8475c0f6f525adc44455fd0688fa58f8"
CAPSULE_BYTES = 53046193
INSTALL_SECONDS, AUDIT_SECONDS, OBSERVE_SECONDS = 900, 120, 30
# 1,140 seconds of phase budgets plus bounded native launch/I/O settlement and
# final identity/cleanup work. Leave 60 seconds before the Actions step timeout.
OVERALL_SECONDS = 1260
OUTPUT_LIMIT = 2 * 1024 * 1024
INPUTS = ("scripts/install.ps1", "scripts/windows_bootstrap_capsule.py",
          "scripts/windows_prototype_launch.py", "amplifier_web/windows_jobs.py",
          "amplifier_web/windows_launch_guard.py", "amplifier_web/windows_files.py",
          "tests/fixtures/windows_prototype_cold.py", "tests/fixtures/windows_prototype_cold.ps1",
          ".github/workflows/windows-prototype-cold.yml")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_file(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def owners():
    # Load exactly the maintained stdlib owners, not application __init__/site.
    package = types.ModuleType("cold_native")
    package.__path__ = [str(ROOT / "amplifier_web")]
    sys.modules[package.__name__] = package
    files = load_file("cold_native.windows_files", "amplifier_web/windows_files.py")
    load_file("cold_native.windows_launch_guard", "amplifier_web/windows_launch_guard.py")
    jobs = load_file("cold_native.windows_jobs", "amplifier_web/windows_jobs.py")
    return files, jobs


def child_environment(environment: dict[str, str]) -> dict[str, str]:
    # Allowlist excludes runner tokens, provider keys, Git/Python/uv overrides.
    wanted = {"SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "USERPROFILE",
              "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP",
              "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "ALLUSERSPROFILE"}
    result = {k.upper(): v for k, v in environment.items() if k.upper() in wanted}
    system = Path(next(v for k, v in result.items() if k.upper() == "SYSTEMROOT"))
    result["PATH"] = os.pathsep.join([str(system / "System32"), str(system)])
    result.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    for key in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        if not any(k.upper() == key and v for k, v in result.items()):
            raise ValueError("Standard Windows profile is required, never synthesized")
    return result


def validate_url(url: str, suffix: str) -> str:
    part = urlsplit(url)
    if (part.scheme != "https" or part.netloc != "github.com" or part.query or part.fragment
            or not re.fullmatch(r"/microsoft/amplifier-unified/releases/download/[A-Za-z0-9._-]+/" + re.escape(suffix), part.path)
            or part.path.split("/")[-2] in {"main", "latest"}):
        raise ValueError("Exact official release URL required")
    return url


def published_manifest(url: str, expected: str) -> dict:
    validate_url(url, "windows-prototype.json")
    if expected != MANIFEST_SHA:
        raise ValueError("This driver qualifies only the reviewed manifest digest")
    # One bounded passive GET, no installer retry and no token/header supplied.
    request = urllib.request.Request(url, headers={"User-Agent": "Unified-cold-qualification"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if urlsplit(response.url).scheme != "https":
            raise ValueError("Manifest redirect lost HTTPS")
        raw = response.read(1048577)
    if len(raw) != 443 or digest(raw) != expected:
        raise ValueError("Published manifest bytes differ from reviewed input")
    value = json.loads(raw)
    if (value.get("schemaVersion") != 1 or value.get("status") != "prototype"
            or value.get("sourceMode") != "pinned-vcs" or value.get("source") != APP_SOURCE
            or value.get("version") != "0.54.5" or value.get("python") != "3.13.15"
            or value.get("architecture") != "x86_64" or value.get("capsule", {}).get("sha256") != CAPSULE_SHA):
        raise ValueError("Published manifest identity mismatch")
    validate_url(value["capsule"]["url"], "windows-prototype.zip")
    if urlsplit(value["capsule"]["url"]).path.rsplit("/", 1)[0] != urlsplit(url).path.rsplit("/", 1)[0]:
        raise ValueError("Manifest and capsule release differ")
    return value


def output_path() -> Path:
    return Path(os.environ["RUNNER_TEMP"]) / "unified-prototype-cold" / "result.json"


def save(report: dict) -> None:
    path = output_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".partial")
    temp.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def source_identity() -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, timeout=15).strip()
    head, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    if (head != os.environ["GITHUB_SHA"] or head != os.environ.get("COLD_EXPECTED_HEAD")
            or not re.fullmatch("[0-9a-f]{40}", head)
            or git("status", "--porcelain", "--untracked-files=no")):
        raise ValueError("Dispatched checkout is not exact/clean")
    canonical = subprocess.check_output(["git", "show", "HEAD:scripts/install.ps1"], cwd=ROOT, timeout=15)
    if digest(canonical) != INSTALLER_SHA:
        raise ValueError("Installer changed from the reviewed d975 input")
    return {"head": head, "tree": tree,
            "sha256": {p: digest((ROOT / p).read_bytes()) for p in INPUTS},
            "installerCanonicalSha256": INSTALLER_SHA}


def natural_success(result, forced: bool, truncated: bool, timed_out: bool) -> bool:
    return (not forced and not truncated and not timed_out and result.payload_returncode == 0
            and result.active_processes == 0 and not result.output_discarded)


async def owned_run(jobs, command, environment, seconds, log, report, label):
    record = {"state": "RUNNING", "cleanup": "UNKNOWN", "deadlineSeconds": seconds}
    report["operations"].append({"phase": label, **record})
    record = report["operations"][-1]
    save(report)  # before admission, never infer no effect from missing completion
    owner = None
    readers = []
    heartbeat = None
    timed_out = truncated = forced = False
    data = bytearray()
    captured = 0
    last_progress = time.monotonic()
    class ObservedProcess(jobs.WindowsOwnedProcess):
        async def _cleanup(self):
            nonlocal forced
            # Arm at class construction, BEFORE start() can schedule its monitor.
            try:
                if self._assigned:
                    active = self._native.active(self._job)
                    guard_alive = bool(self._guard_handle and not self._native.exited(self._guard_handle))
                    forced |= active > int(guard_alive)
            except BaseException:
                # Observation cannot bypass canonical cleanup or turn uncertain
                # natural-exit evidence into PASS. The owner retains any failure
                # of its own cleanup through its existing failed-owner contract.
                forced = True
                record["naturalExitObservation"] = "FAILED"
            return await super()._cleanup()
    try:
        owner = await ObservedProcess.start(command, cwd=ROOT, env=environment,
                    trusted_python=sys._base_executable, gate_timeout=10, cleanup_timeout=10)
        owner.stdin.close()
        async def consume(stream, collect):
            nonlocal truncated, last_progress, captured
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                if captured + len(chunk) > OUTPUT_LIMIT:
                    truncated = True
                remaining = max(0, OUTPUT_LIMIT - captured)
                if collect:
                    data.extend(chunk[:remaining])
                log.write(chunk[:remaining]); log.flush()
                captured += len(chunk)
                if time.monotonic() - last_progress >= 30:
                    print("Cold bootstrap phase active: " + label, flush=True)
                    last_progress = time.monotonic()
        readers = [asyncio.create_task(consume(owner.stdout, True)),
                   asyncio.create_task(consume(owner.stderr, False))]
        async def progress():
            while True:
                await asyncio.sleep(30)
                print("Cold bootstrap still active: " + label, flush=True)
        heartbeat = asyncio.create_task(progress())
        waiting = asyncio.create_task(owner.wait())
        try:
            result = await asyncio.wait_for(asyncio.shield(waiting), seconds)
        except asyncio.TimeoutError:
            timed_out = forced = True
            result = await owner.close()  # exact retained job only; NEVER a pass
            await waiting
        await asyncio.wait_for(asyncio.gather(*readers), 10)
        record.update(state="COMPLETED", cleanup="CONFIRMED_ZERO_JOB_PROCESSES", timedOut=timed_out,
                      forcedPayloadCleanup=forced, outputTruncated=truncated,
                      payloadExit=result.payload_returncode, outputDiscarded=result.output_discarded,
                      activeProcesses=result.active_processes, ioFinalized=owner.io_finalized)
        save(report)
        if not natural_success(result, forced, truncated, timed_out):
            raise RuntimeError("Owned phase failed or required forceful cleanup")
        return bytes(data)
    except BaseException:
        if owner is not None and owner.state != "closed":
            try:
                await owner.close()
                record.update(cleanup="FORCED_JOB_ZERO_NOT_SUCCESS", forcedPayloadCleanup=True)
            except BaseException:
                record["cleanup"] = "UNKNOWN_OWNER_RETAINED"
        for task in readers:
            if not task.done():
                task.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
        record["state"] = "FAIL"
        save(report)
        raise
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)


def fresh_root(before: set[str], after: set[str], parent: Path) -> Path:
    added = after - before
    if len(added) != 1:
        raise ValueError("Exactly one new installer-owned root required")
    root = Path(added.pop())
    if root.parent != parent or not re.fullmatch("amplifier-install-[0-9a-f]{32}", root.name):
        raise ValueError("Root is not a fresh direct LocalAppData child")
    return root


def public_native(facts: dict) -> dict:
    """Explicit projection; never publish private root addresses or process IDs."""
    return {key: facts[key] for key in (
        "version", "edition", "process64", "platform", "elevated", "osBuild",
        "effectivePolicy", "scopes", "commands", "profile", "pathIsExactSystem32Windows",
        "osProductType", "processorArchitectures", "ready", "refusals")}


def require_ready(facts: dict, report: dict) -> None:
    """Mirror the installed gate independently; no stub can approve CI elevation."""
    eligible = (
        facts.get("ready") is True
        and facts.get("platform") == "Win32NT"
        and facts.get("process64") is True
        and facts.get("edition") == "Desktop"
        and str(facts.get("version", "")).startswith("5.1.")
        and facts.get("elevated") is False
        and facts.get("osProductType") == 1
        and facts.get("osBuild", 0) >= 22000
        and facts.get("processorArchitectures") == [9]
    )
    tools = facts.get("commands", {})
    if not eligible or any(tools.get(name, {}).get("resolvable", True)
            for name in ("uv", "git", "python", "python3", "py", "pwsh")):
        report["readiness"] = "REFUSED_BEFORE_INSTALL"
        save(report)
        raise ValueError("Unmodified installer prerequisites are not met")


def root_identity(root: Path) -> tuple[int, int]:
    item = root.lstat()
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError("Root was substituted")
    return item.st_dev, item.st_ino


def delete_verified_root(root: Path, expected: tuple[int, int], processes: list, files) -> None:
    if processes or root_identity(root) != expected:
        raise ValueError("Root identity/process absence not verified")
    with files.directory(root, owned_root=root, create=False):
        pass  # native ACL/TokenUser/ancestor validation, no private payload read
    # Fail closed on unexpected links anywhere, not merely the root. Same-token
    # and administrator mutation remain inside the native owner's trust boundary.
    for parent, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(parent) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError("Unexpected link prevents disposable-root deletion")
    if root_identity(root) != expected:
        raise ValueError("Root changed before deletion")
    shutil.rmtree(root)
    if root.exists():
        raise RuntimeError("Disposable root removal not observed")


async def diagnostic_command(argv, environment, label, expected=None):
    """Bounded pipe readers, visible only to the controller's private capture."""
    print("Installed check: " + label, file=sys.stderr, flush=True)
    child = await asyncio.create_subprocess_exec(*argv, env=environment,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
    output = bytearray()
    oversized = False
    async def consume(stream, collect):
        nonlocal oversized
        seen = 0
        while chunk := await stream.read(4096):
            available = max(0, 65536 - seen)
            if available:
                kept = chunk[:available]
                sys.stderr.buffer.write(kept); sys.stderr.buffer.flush()
                if collect:
                    output.extend(kept)
            seen += len(chunk)
            oversized |= seen > 65536
    readers = [asyncio.create_task(consume(child.stdout, True)),
               asyncio.create_task(consume(child.stderr, False))]
    try:
        await asyncio.wait_for(asyncio.gather(child.wait(), *readers), 30)
    except BaseException:
        if child.returncode is None:
            child.kill()  # retained exact child, inside the controller-owned job
            await asyncio.wait_for(child.wait(), 10)
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        raise
    if child.returncode or oversized or (expected and expected not in output.decode("utf-8", errors="replace")):
        raise RuntimeError("Installed command check failed: " + label)


def installed_probe(root: Path) -> dict:
    """Executed ONLY by the installed venv Python, read-only except CLI --version."""
    if Path(sys.executable).absolute() != (root / "host/Scripts/python.exe").absolute():
        raise ValueError("Probe must use exact installed venv executable")
    from importlib import metadata
    checker = load_file("cold_source_checker", str(root / "capsule/source_install.py"))
    checker.audit_source(root / "capsule")
    receipt = json.loads((root / "prototype.json").read_text(encoding="utf-8"))
    expected_uv = root / "uv-0.12.18/uv.exe"
    expected_git = root / "git-2.56.0.windows.4/cmd/git.exe"
    if (receipt["uv"] != str(expected_uv) or receipt["git"] != str(expected_git)
            or receipt["source"] != APP_SOURCE or receipt["bootstrapUv"] != "0.12.18"
            or receipt["sourceMode"] != "pinned-vcs"):
        raise ValueError("Retained helpers/source differ")
    base = Path(sys._base_executable)
    if (sys.version_info[:3] != (3, 13, 15) or sysconfig.get_config_var("Py_GIL_DISABLED")
            or not base.is_relative_to(root / "python")):
        raise ValueError("Installed venv does not reference the managed ordinary interpreter")
    if digest((root / "capsule.zip").read_bytes()) != CAPSULE_SHA or (root / "capsule.zip").stat().st_size != CAPSULE_BYTES:
        raise ValueError("Actual capsule bytes differ")
    notices = (root / "uv-0.12.18/LICENSE-MIT", root / "uv-0.12.18/LICENSE-APACHE",
               root / "git-2.56.0.windows.4/LICENSE.txt")
    if not all(p.is_file() and p.stat().st_size for p in notices):
        raise ValueError("Retained helper notices missing")
    launcher = load_file("cold_launcher", str(root / "capsule/launch.py"))
    env = launcher.child_environment(root, receipt)
    if (Path(shutil.which("uv", path=env["PATH"]) or "") != expected_uv
            or Path(shutil.which("git", path=env["PATH"]) or "") != expected_git):
        raise ValueError("Fresh wrapper PATH does not select retained private helpers")
    # Minimal PATH discriminator: explicitly invoke root-local helpers, then use
    # the fresh wrapper environment for the actual --check invocation.
    checks = [([str(expected_uv), "--version"], "uv 0.12.18"),
              ([str(expected_git), "--version"], "git version 2.56.0.windows.4"),
              ([str(root / "host/Scripts/amplifier-unified.exe"), "--data-dir", str(root / "app"), "--version"], "0.54.5")]
    for (argv, expected), label in zip(checks, ("private-uv-version", "private-git-version", "absolute-cli-version")):
        asyncio.run(diagnostic_command(argv, env, label, expected))
    asyncio.run(diagnostic_command(
        [sys.executable, "-I", "-B", str(root / "capsule/launch.py"), "--check"],
        env, "fresh-launcher-check"))
    # Only counts/digests are public. No direct URLs, user paths, SID or output.
    rows = sorted((d.metadata["Name"], d.version) for d in metadata.distributions())
    return {"status": "PASS", "distributionCount": len(rows), "gitOriginsVerified": 5,
            "backendWheelsVerified": 6, "runtimeWheelsVerified": 60,
            "distributionIdentitySha256": digest(json.dumps(rows).encode()),
            "managedPython": "3.13.15", "baseInsidePrivateRoot": True,
            "bootstrapUv": "0.12.18", "productionUv": metadata.version("uv"),
            "standardMinGit": "2.56.0.windows.4", "licensesPresent": True,
            "absoluteCliVersion": "0.54.5", "freshLauncherCheck": True,
            "capsuleSha256": CAPSULE_SHA}


async def qualify(url: str, expected: str, report: dict) -> None:
    files, jobs = owners()
    environment = child_environment(dict(os.environ))
    powershell = str(Path(environment["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    command = [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
               str(ROOT / "tests/fixtures/windows_prototype_cold.ps1")]
    # Logs are private at creation; never upload the raw private directory.
    private = Path(os.environ["RUNNER_TEMP"]) / ("cold-private-" + uuid.uuid4().hex)
    with files.directory(private, owned_root=private, create=True) as pinned:
        pinned.publish_private("operation.log", b"")
    log_path = private / "operation.log"
    # Opening the native private file owner avoids an ordinary create-before-ACL.
    with files.directory(private, owned_root=private, create=False) as pinned:
        log = log_path.open("ab")
        try:
            async def run(argv, seconds, label):
                report["stage"] = label; save(report)
                return await owned_run(jobs, argv, environment, seconds, log, report, label)
            before = json.loads((await run(command + ["-Action", "preflight"], OBSERVE_SECONDS, "native-preflight")).decode("utf-8-sig"))
            report["native"] = public_native(before)
            save(report)
            require_ready(before, report)
            root = None
            try:
                await run(command + ["-Action", "install", "-Installer", str(ROOT / "scripts/install.ps1"),
                          "-ManifestUrl", url, "-ManifestSha256", expected], INSTALL_SECONDS, "real-installer-no-open")
            finally:
                # Passive root discovery after settled install failure is safe;
                # no cleanup/retry when root identity or job custody is unknown.
                if report["operations"][-1].get("cleanup") in {"CONFIRMED_ZERO_JOB_PROCESSES", "FORCED_JOB_ZERO_NOT_SUCCESS"}:
                    after = json.loads((await run(command + ["-Action", "preflight"], OBSERVE_SECONDS, "roots-after")).decode("utf-8-sig"))
                    root = fresh_root(set(before["roots"]), set(after["roots"]), Path(before["localAppData"]))
                    report["freshRootCount"] = 1
                    with files.directory(private, owned_root=private, create=False) as state:
                        state.publish_private("retained-root.json", json.dumps({"root": str(root)}).encode())
            if root is None:
                raise RuntimeError("Fresh root not observed")
            identity = root_identity(root)
            info = json.loads((await run(command + ["-Action", "inspect", "-Root", str(root)], OBSERVE_SECONDS, "shortcut-and-private-root")).decode("utf-8-sig"))
            if (info["rootReferencedProcessIds"]
                    or info["shortcut"]["target"] != str(root / "host/Scripts/python.exe")
                    or info["shortcut"]["workingDirectory"] != str(root)):
                raise ValueError("COM shortcut or process absence failed")
            expected_arguments = '-I -B "' + str(root / "capsule/launch.py") + '"'
            if info["shortcut"]["arguments"] != expected_arguments:
                raise ValueError("COM shortcut arguments differ")
            report["rootInspection"] = {
                "acl": info["acl"], "reparse": info["rootReparsePoint"],
                "hashes": info["hashes"],
                "helpers": {name: {key: item[key] for key in
                    ("version", "archiveSha256", "bootstrapReceiptExists", "licensesExist")}
                    for name, item in info["helpers"].items()},
                "firstLevelEntries": info["firstLevelEntries"],
            }
            audit = await run([str(root / "host/Scripts/python.exe"), "-I", "-B", str(Path(__file__)),
                               "--probe-installed", str(root)], AUDIT_SECONDS, "installed-graph-and-fresh-launcher-check")
            report["installed"] = json.loads(audit.decode("utf-8-sig"))
            final = json.loads((await run(command + ["-Action", "inspect", "-Root", str(root)], OBSERVE_SECONDS, "process-absence-before-delete")).decode("utf-8-sig"))
            delete_verified_root(root, identity, final["rootReferencedProcessIds"], files)
            report.update(disposableRootRemoved=True, shortcutCreatedAndRead=True, cleanup="PASS")
        finally:
            log.close()
    # Success only: private controller logs are also intentionally removed.
    shutil.rmtree(private)
    report["privateCaptureRemoved"] = not private.exists()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-url", default=os.environ.get("COLD_MANIFEST_URL"))
    parser.add_argument("--manifest-sha256", default=os.environ.get("COLD_MANIFEST_SHA256"))
    parser.add_argument("--probe-installed", type=Path)
    args = parser.parse_args()
    if args.probe_installed is not None:
        try:
            print(json.dumps(installed_probe(args.probe_installed)))
            return 0
        except Exception:
            import traceback
            # This stream is the existing bounded PRIVATE controller capture,
            # never copied into public result.json.
            traceback.print_exc(limit=5, file=sys.stderr)
            print("Installed cold-bootstrap probe failed; see private capture.", file=sys.stderr)
            return 1
    report = {"schema": 1, "status": "FAIL", "qualification": "real-prerequisite-bootstrap-no-open",
              "publicAcceptance": False, "ordinaryUserAcceptance": False, "cleanMachineAcceptance": False,
              "operations": [], "cleanup": "NOT_RUN", "stage": "input-and-source",
              "limits": ["CI token may be elevated and policy permissive", "Tools outside child PATH remain installed",
                         "No server/browser/TLS trust/provider/worker qualification"]}
    save(report)
    try:
        report["source"] = source_identity()
        validate_url(args.manifest_url, "windows-prototype.json")
        if args.manifest_sha256 != MANIFEST_SHA:
            raise ValueError("Only the reviewed manifest digest is admitted")
        report["manifest"] = {"url": args.manifest_url, "sha256": args.manifest_sha256}
        save(report)
        if (sys.platform != "win32" or sys.version_info[:2] != (3, 13) or struct.calcsize("P") != 8
                or sysconfig.get_config_var("Py_GIL_DISABLED") or not sys._base_executable):
            raise RuntimeError("Controller needs ordinary native Windows CPython 3.13 x64")
        published_manifest(args.manifest_url, args.manifest_sha256)
        report["publishedManifestGetVerified"] = True
        asyncio.run(asyncio.wait_for(qualify(args.manifest_url, args.manifest_sha256, report), OVERALL_SECONDS))
        if report.get("cleanup") != "PASS" or not report.get("privateCaptureRemoved"):
            raise RuntimeError("Cleanup incomplete")
        report["status"] = "PASS"
        return 0
    except BaseException:
        # Never publish raw exceptions/child output or delete a failed partial root.
        report["failure"] = "Read failed stage and owned operation settlement; no automatic install retry."
        print("Cold bootstrap did not qualify. Preserve the private root/logs and sanitized receipt.", file=sys.stderr)
        return 1
    finally:
        save(report)


if __name__ == "__main__":
    raise SystemExit(main())
