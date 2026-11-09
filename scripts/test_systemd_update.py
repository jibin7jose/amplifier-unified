#!/usr/bin/env python3
"""Exercise update handoff against real systemd in a disposable Docker container.

Requires Docker with Linux containers and privileged-container support. No host
ports, service units, credentials, or writable repository mounts are passed in.
Package installation is local/synthetic; systemd, cgroups, signals, HTTP health,
update activation, persisted receipts, and readiness reconciliation are real.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import os
import subprocess
import sys
import time
import uuid

IMAGE = "amplifier-update-systemd-test"
# Both registries publish the same Docker Official Python 3.13-slim OCI index.
# Pin its bytes so a registry quota fallback cannot change the test environment;
# Docker still selects the daemon's native platform from that same index.
BASE_DIGEST = "sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f"
BASE_IMAGES = tuple(f"{registry}@{BASE_DIGEST}" for registry in (
    "public.ecr.aws/docker/library/python", "docker.io/library/python",
))
DOCKERFILE = """FROM {base_image}
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv dbus-user-session git ca-certificates libpam0g && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
ENV container=docker
CMD [\"/sbin/init\"]
"""


def acquire_base_image(docker):
    """Try each immutable mirror once, before any build or application work."""
    for image in BASE_IMAGES:
        try:
            docker("pull", image, timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"Base image acquisition failed for {image}: {exc}", file=sys.stderr)
            continue
        # Inspect only public image identity, never a container's environment.
        identity = json.loads(docker(
            "image", "inspect", "--format",
            '{"id":{{json .Id}},"digests":{{json .RepoDigests}},'
            '"os":{{json .Os}},"architecture":{{json .Architecture}}}',
            image, capture_output=True, text=True, timeout=15,
        ).stdout)
        digests = identity.get("digests") or []
        if not any(isinstance(value, str) and value.rpartition("@")[2] == BASE_DIGEST
                   for value in digests) or identity.get("os") != "linux":
            raise RuntimeError("Acquired base image does not match the pinned Linux image")
        print("Selected systemd base image: " + json.dumps({"reference": image, **identity}), flush=True)
        return image
    raise RuntimeError("Could not acquire the pinned systemd base image from either official registry")


def wait_for_systemd(docker, name, *, timeout=60):
    """The container can be running before systemd exposes its D-Bus socket."""
    deadline = time.monotonic() + timeout
    last_probe = 'No response from systemd yet'
    while time.monotonic() < deadline:
        try:
            probe = docker("exec", name, "systemctl", "show", "--property=SystemState", "--value",
                           check=False, capture_output=True, text=True, timeout=5)
            status = probe.stdout.strip()
            if probe.returncode == 0 and status in {"running", "degraded"}:
                return
            last_probe = f"exit {probe.returncode}: {(probe.stderr or probe.stdout).strip()[-1000:]}"
        except subprocess.TimeoutExpired:
            last_probe = 'systemctl probe timed out'
        # An unavailable bus is transient; an exited PID 1 will never recover.
        state = docker("inspect", "--format", "{{json .State}}", name,
                       check=False, capture_output=True, text=True, timeout=5)
        if state.returncode != 0:
            raise RuntimeError("Cannot inspect disposable systemd container: " + state.stderr.strip())
        container = json.loads(state.stdout)
        if container.get('Status') in {'exited', 'dead'} or container.get('OOMKilled'):
            raise RuntimeError("Disposable systemd container stopped during boot: " + json.dumps(container))
        time.sleep(.5)
    raise RuntimeError("Disposable systemd manager did not become ready: " + last_probe)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default=shutil.which("docker"), help="Docker executable")
    args = parser.parse_args()
    if not args.docker:
        parser.error("Docker is required; this test does not silently substitute mocks")
    root = Path(__file__).resolve().parents[1]
    name = "amplifier-update-test-" + uuid.uuid4().hex[:12]

    def docker(*command, check=True, **kwargs):
        return subprocess.run([args.docker, *command], check=check, **kwargs)

    base_image = acquire_base_image(docker)
    # BuildKit may resolve this immutable reference again. Never switch back to
    # a failed mirror or retry a build/container/application operation.
    docker("build", "-t", IMAGE, "-", input=DOCKERFILE.format(base_image=base_image), text=True)
    docker("run", "-d", "--name", name, "--privileged", "--cgroupns=private",
           "--tmpfs", "/run", "--tmpfs", "/run/lock", "--tmpfs", "/tmp",
           "--mount", f"type=bind,source={root},target=/src,readonly", IMAGE)
    try:
        wait_for_systemd(docker, name)
        docker("exec", name, "useradd", "-m", "-u", "1234", "updater")
        docker("exec", name, "loginctl", "enable-linger", "updater")
        docker("exec", name, "systemctl", "start", "user@1234.service")
        docker("exec", name, "uv", "venv", "/opt/test")
        docker("exec", name, "uv", "pip", "install", "--python", "/opt/test/bin/python", "/src")
        historical = subprocess.run(['git', '-C', str(root), 'show',
            'fd12fb041dbf71050257e8f97b2605b33cec0492:amplifier_web/app_updates.py'],
            check=True, capture_output=True, env={**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}).stdout
        docker("exec", "-i", name, "/opt/test/bin/python", "-c",
               "import sys; from pathlib import Path; Path('/opt/legacy-app-updates.py').write_bytes(sys.stdin.buffer.read())",
               input=historical)
        docker("exec", name, "/opt/test/bin/python", "/src/tests/fixtures/systemd_update_service.py", "prepare")
        docker("exec", "--user", "1234", "--env", "HOME=/home/updater",
               "--env", "XDG_RUNTIME_DIR=/run/user/1234",
               "--env", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1234/bus", name,
               "/opt/test/bin/python", "/src/tests/fixtures/systemd_update_service.py", "exercise")
        docker("exec", "--user", "1234", "--env", "HOME=/home/updater",
               "--env", "XDG_RUNTIME_DIR=/run/user/1234",
               "--env", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1234/bus", name,
               "/opt/test/bin/python", "/src/tests/fixtures/systemd_shutdown_policy.py")
    except BaseException:
        # These work even if PID 1 exited before journald or D-Bus came up. State
        # only, not full inspect (which can contain environment variables).
        subprocess.run([args.docker, "inspect", "--format", "{{json .State}}", name], check=False)
        subprocess.run([args.docker, "logs", "--tail", "120", name], check=False)
        subprocess.run([args.docker, "exec", name, "/usr/local/bin/python", "-c",
                        "from pathlib import Path; print(Path('/proc/1/cgroup').read_text()); "
                        "print(''.join(line for line in Path('/proc/1/mountinfo').read_text().splitlines(True) if ' - cgroup' in line))"], check=False)
        subprocess.run([args.docker, "exec", name, "journalctl", "--no-pager", "-n", "120",
                        "_SYSTEMD_USER_UNIT=amplifier-unified.service", "+", "_SYSTEMD_UNIT=user@1234.service"], check=False)
        raise
    finally:
        subprocess.run([args.docker, "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
