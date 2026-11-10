"""Real portable checks for the FileLock/AsyncFileLock APIs used by Unified."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from filelock import AsyncFileLock, FileLock, Timeout


CONTENDER = """
import sys
from filelock import FileLock, Timeout
try:
    with FileLock(sys.argv[1], timeout=0):
        print("acquired")
except Timeout:
    print("busy")
"""


class FileLockCompatibilityTests(unittest.TestCase):
    def test_nested_hold_timeout_exception_release_and_reacquisition(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "receipt.lock"
            lock = FileLock(path, timeout=0)

            def contender():
                result = subprocess.run(
                    [sys.executable, "-I", "-c", CONTENDER, str(path)],
                    check=True, capture_output=True, text=True, timeout=10,
                )
                return result.stdout.strip()

            with self.assertRaisesRegex(ValueError, "body failed"):
                with lock:
                    with lock:
                        self.assertEqual(lock.lock_counter, 2)
                        self.assertEqual(contender(), "busy")
                    self.assertTrue(lock.is_locked)
                    self.assertEqual(contender(), "busy")
                    raise ValueError("body failed")
            self.assertFalse(lock.is_locked)
            # Unix retains the inode; Windows may remove the path on release.
            # Contention and reacquisition, not path existence, prove ownership.
            if os.name != "nt":
                self.assertTrue(path.exists())
            self.assertEqual(contender(), "acquired")


class AsyncFileLockCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_cancel_and_exception_do_not_strand_the_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.lock"
            holder = AsyncFileLock(path)
            waiter = AsyncFileLock(path, timeout=-1)
            async with holder:
                with self.assertRaises(Timeout):
                    await AsyncFileLock(path, timeout=0).acquire()
                pending = asyncio.create_task(waiter.acquire())
                await asyncio.sleep(0.05)
                self.assertFalse(pending.done())
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, 5)
                self.assertFalse(waiter.is_locked)
            with self.assertRaisesRegex(ValueError, "body failed"):
                async with waiter:
                    self.assertTrue(waiter.is_locked)
                    raise ValueError("body failed")
            self.assertFalse(waiter.is_locked)
            if os.name != "nt":
                self.assertTrue(path.exists())
            async with AsyncFileLock(path, timeout=1):
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()