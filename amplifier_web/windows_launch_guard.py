"""Trusted, stdlib-only bootstrap. Execute this exact file with Python -I -S.

The interpreter must be the explicit host-owned DIRECT/base executable, not
a Windows venv redirector; READY must identify the original creation PID.
Before GO this script may only report its identity and read a bounded frame.
stdin is read with exact os.read lengths: bytes after the frame belong to the
payload, not a buffered reader. Status uses a separate, non-inherited pipe.
The host must assign and verify the job BEFORE sending GO. This is not a
security sandbox and does not undo effects made through external services.
"""

import os
import sys
import threading


MAX_CONFIG_BYTES = 1024 * 1024
MAX_STATUS_BYTES = 1024
MAX_GATE_SECONDS = 30.0


def _read_exact(fd, size):
    data = bytearray()
    while len(data) < size:
        part = os.read(fd, size - len(data))
        if not part:
            raise EOFError("gate closed")
        data.extend(part)
    return bytes(data)


def read_config(fd, token):
    import json
    import struct

    size = struct.unpack("!I", _read_exact(fd, 4))[0]
    if not 0 < size <= MAX_CONFIG_BYTES:
        raise ValueError("gate frame exceeds limit")
    config = json.loads(_read_exact(fd, size).decode("utf-8"))
    if not isinstance(config, dict) or config.get("go") != token:
        raise ValueError("invalid GO")
    validate_payload(config)
    return config


def validate_payload(config):
    """Reject implicit shell/PATH resolution; retain exact argv/env/cwd."""
    argv, env, cwd = config.get("argv"), config.get("env"), config.get("cwd")
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(v, str) or "\0" in v for v in argv)
        or not os.path.isabs(argv[0])
        or (os.name == "nt" and not argv[0].lower().endswith(".exe"))
    ):
        raise ValueError("payload requires absolute executable argv")
    if not isinstance(cwd, str) or "\0" in cwd or not os.path.isabs(cwd):
        raise ValueError("payload requires absolute cwd")
    if not isinstance(env, dict) or any(
        not isinstance(k, str) or not k or "=" in k or "\0" in k
        or not isinstance(v, str) or "\0" in v
        for k, v in env.items()
    ):
        raise ValueError("payload requires explicit string environment")
    if len({k.upper() for k in env}) != len(env):
        raise ValueError("ambiguous environment keys")


def creation_time(kernel, handle):
    """GetProcessTimes FILETIME, in native 100ns ticks; never float seconds."""
    import ctypes as c

    filetime = c.c_uint32 * 2
    kernel.GetProcessTimes.argtypes = [c.c_void_p] + [c.POINTER(filetime)] * 4
    kernel.GetProcessTimes.restype = c.c_int32
    times = [filetime() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(c.byref(v) for v in times)):
        raise c.WinError(c.get_last_error())
    return times[0][0] | (times[0][1] << 32)


def _status(fd, **fields):
    import json

    frame = json.dumps(fields, separators=(",", ":")).encode("ascii") + b"\n"
    if len(frame) > MAX_STATUS_BYTES:
        raise ValueError("status frame exceeds limit")
    while frame:
        count = os.write(fd, frame)
        if not count:
            raise OSError("status pipe closed")
        frame = frame[count:]


def main():
    # Start the finite pre-GO deadline before importing subprocess/ctypes/json.
    # EOF is not sufficient: an unrelated inherited pipe writer may survive.
    seconds = float(sys.argv[3])
    if not 0 < seconds <= MAX_GATE_SECONDS:
        raise ValueError("invalid gate timeout")
    timer = threading.Timer(seconds, os._exit, args=(124,))
    timer.daemon = True
    timer.start()

    import ctypes as c
    import msvcrt
    import subprocess

    control = msvcrt.open_osfhandle(int(sys.argv[1]), os.O_WRONLY | os.O_BINARY)
    os.set_inheritable(control, False)
    msvcrt.setmode(0, os.O_BINARY)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = c.c_void_p
    _status(control, event="ready", pid=os.getpid(),
            created=creation_time(kernel, kernel.GetCurrentProcess()))
    try:
        config = read_config(0, sys.argv[2])
    except (EOFError, ValueError):
        return 125
    # GO is one-use. No payload imports, app code or payload environment have
    # run before the host's check. Timer cancellation is safe only after GO.
    timer.cancel()
    child = subprocess.Popen(
        config["argv"], executable=config["argv"][0], cwd=config["cwd"],
        env=config["env"], stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
        close_fds=True, shell=False,
    )
    # Popen retains its creation handle until wait, preventing PID reuse while
    # we open a query-only identity handle. It is never used to signal anything.
    kernel.OpenProcess.argtypes = [c.c_uint32, c.c_int32, c.c_uint32]
    kernel.OpenProcess.restype = c.c_void_p
    kernel.CloseHandle.argtypes = [c.c_void_p]
    kernel.CloseHandle.restype = c.c_int32
    handle = kernel.OpenProcess(0x1000, False, child.pid)
    if not handle:
        raise c.WinError(c.get_last_error())
    try:
        created = creation_time(kernel, handle)
    finally:
        if not kernel.CloseHandle(handle):
            raise c.WinError(c.get_last_error())
    _status(control, event="started", pid=child.pid, created=created)
    # A grandchild retaining stdout/stderr must NOT delay root-exit reporting.
    _status(control, event="exit", returncode=child.wait())
    os.close(control)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())