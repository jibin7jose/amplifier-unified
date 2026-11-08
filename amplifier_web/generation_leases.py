"""Private durable references from live worker processes to source generations."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def acquire(home, generation, pid):
    from .host.config import write_private

    path = Path(home) / "updates/worker-leases" / (uuid.uuid4().hex + ".json")
    write_private(path, json.dumps({"schema": 1, "generation": generation, "pid": pid}))
    return path


def _process_exists(pid):
    """Treat uncertain liveness as protection, without signaling on Windows."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        return True

    import ctypes

    # Do not truncate an unrepresentable lease PID to a different DWORD PID.
    if pid > 0xFFFFFFFF:
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # DWORD/BOOL are 32-bit; HANDLE is pointer-sized, including on 64-bit Windows.
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int32

    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only.
    if not handle:
        # ERROR_INVALID_PARAMETER: no process has this valid, nonzero PID.
        # Access denied and all other errors leave the generation protected.
        return ctypes.get_last_error() != 87
    try:
        # Only WAIT_OBJECT_0 proves exit. Timeout, WAIT_FAILED and unknown results
        # retain the lease. A zero timeout never waits for the process to exit.
        return kernel32.WaitForSingleObject(handle, 0) != 0
    finally:
        kernel32.CloseHandle(handle)


def references(home):
    generations = set()
    for path in (Path(home) / "updates/worker-leases").glob("*.json"):
        record = json.loads(path.read_text())
        if (
            record.get("schema") != 1
            or type(record.get("pid")) is not int
            or record["pid"] <= 0
        ):
            raise ValueError("Unrecognized worker lease; storage retained")
        if not _process_exists(record["pid"]):
            # Crash recovery: no live holder remains. PID reuse can only retain
            # too much storage, never remove a live generation.
            path.unlink(missing_ok=True)
            continue
        generations.add(record["generation"])
    return generations
