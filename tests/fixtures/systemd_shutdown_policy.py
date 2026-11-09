"""Real user-service signals for legacy-unit migration; disposable environments only.

Complements test_runtime_shutdown.py's real app/worker settlement assertions.
This fixture proves that systemd lets the parent initiate shutdown, and retains
whole-cgroup timeout escalation. It uses no network, credentials or model calls.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import threading
import time


def serve(root: Path, *, hang: bool):
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    child = subprocess.Popen([sys.executable, __file__, "child"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline() == "ready\n"
    (root / "ready.json").write_text(json.dumps({"parent": os.getpid(), "child": child.pid}))
    assert stopped.wait(20), "Fixture never received systemd stop"
    if hang:
        time.sleep(20)
        raise AssertionError("Systemd did not escalate the stalled shutdown")
    # Give a group-wide SIGTERM time to kill the child. With mixed, only the
    # parent is signalled, and it sends the ordinary worker stop request.
    try:
        early_exit = child.wait(timeout=.4)
    except subprocess.TimeoutExpired:
        early_exit = None
    if early_exit is None:
        child.stdin.write("stop\n")
        child.stdin.flush()
    child.wait(timeout=3)
    (root / "result.json").write_text(json.dumps({
        "exitBeforeParentStop": early_exit,
        "childExit": child.returncode,
        "childReply": child.stdout.read(),
    }))


def exercise():
    from amplifier_web import deployment_service as service

    root = Path.home() / "shutdown-policy-test"
    root.mkdir(exist_ok=False)
    name = "amplifier-shutdown-policy-test.service"
    unit = Path.home() / ".config/systemd/user" / name
    unit.parent.mkdir(parents=True, exist_ok=True)
    assert not unit.exists(), "Never replace another fixture's service"
    # Use the installer's literal launch shape, with an owned signal-test app.
    launcher = root / 'amplifier-unified'
    launcher.write_text(f'#!{sys.executable}\nimport runpy\nrunpy.run_path({str(Path(__file__).resolve())!r}, run_name="__main__")\n')
    launcher.chmod(0o700)
    launch = [str(launcher), '--no-open', '--data-dir', str(root), '--workspace', str(root), 'serve']
    original = (service.MARKER + "\n[Unit]\nDescription=Owned shutdown signal fixture\n"
                "[Service]\nType=simple\nTimeoutStopSec=2\n"
                f"ExecStart={shlex.join(launch)}\n")
    unit.write_text(original)
    service.UNIT_NAME = name
    service.unit_path = lambda: unit

    def ctl(*args):
        return subprocess.run(["systemctl", "--user", *args], check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()

    def stopped_run():
        for path in (root / "ready.json", root / "result.json"):
            path.unlink(missing_ok=True)
        ctl("start", name)
        deadline = time.monotonic() + 10
        while not (root / "ready.json").exists():
            assert time.monotonic() < deadline, "Fixture did not start"
            time.sleep(.02)
        pids = json.loads((root / "ready.json").read_text())
        ctl("stop", name)
        return pids

    try:
        ctl("daemon-reload")
        assert ctl("show", name, "-p", "KillMode", "--value") == "control-group"
        stopped_run()
        before = json.loads((root / "result.json").read_text())
        assert before == {"exitBeforeParentStop": -signal.SIGTERM,
                          "childExit": -signal.SIGTERM, "childReply": ""}, before

        service.prepare_managed_shutdown(root)
        assert ctl("show", name, "-p", "KillMode", "--value") == "mixed"
        assert unit.read_text() == original.replace("[Service]\n", "[Service]\nKillMode=mixed\n")
        backup, = unit.parent.glob(name + ".backup-*")
        assert backup.read_text() == original and backup.stat().st_mode & 0o777 == 0o600
        stopped_run()
        after = json.loads((root / "result.json").read_text())
        assert after == {"exitBeforeParentStop": None, "childExit": 0,
                         "childReply": "stopped\n"}, after

        # A stuck host must not leave its worker alive forever under mixed.
        (root / 'hang').touch()
        pids = stopped_run()
        assert not (root / "result.json").exists()
        assert ctl("show", name, "-p", "Result", "--value") == "timeout"
        for pid in pids.values():
            status = Path(f"/proc/{pid}/stat")
            assert not status.exists() or status.read_text().rsplit(")", 1)[1].split()[0] == "Z"
        print(json.dumps({"before": before, "after": after, "timeoutEscalation": "passed"}))
    finally:
        ctl("stop", name)
        ctl("reset-failed", name)
        unit.unlink()
        for backup in unit.parent.glob(name + ".backup-*"):
            backup.unlink()
        ctl("daemon-reload")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        print("ready", flush=True)
        assert input() == "stop"
        print("stopped", flush=True)
    elif '--data-dir' in sys.argv:
        root = Path(sys.argv[sys.argv.index('--data-dir') + 1])
        serve(root, hang=(root / 'hang').exists())
    else:
        exercise()
