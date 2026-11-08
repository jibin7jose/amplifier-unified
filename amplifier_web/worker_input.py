"""Bounded async reads from the worker's synchronous Windows stdin pipe."""

import asyncio
import os
import queue
import threading


_CONTROL_INPUT = None
_CONTROL_CLAIMED = False
_FAILED_CONTROL_INPUTS = []


def _bind_standard_input(fd):
    """Keep the Win32 default and CRT fd zero on the SAME independent object."""
    import ctypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    kernel.SetStdHandle.restype = ctypes.c_int32
    kernel.GetStdHandle.argtypes = [ctypes.c_uint32]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    handle = msvcrt.get_osfhandle(fd)
    if not kernel.SetStdHandle(0xfffffff6, handle):  # STD_INPUT_HANDLE
        raise ctypes.WinError(ctypes.get_last_error())
    if kernel.GetStdHandle(0xfffffff6) != handle:
        raise OSError("Windows default stdin binding was not confirmed")


def isolate_control_input(pipe):
    """Before any read, detach the private worker pipe from default child stdin.

    DuplicateHandle alone still names the synchronously read file object. Git for
    Windows can inspect inherited stdin before main; the default must instead be
    an independently opened EOF source. Never swap it around individual tools.
    Only the dedicated reader may own/read the retained non-inheritable pipe.
    """
    global _CONTROL_INPUT
    if os.name != "nt":
        raise NotImplementedError("private control-input isolation is Windows-only")
    if _FAILED_CONTROL_INPUTS:
        raise RuntimeError("Windows control-input rollback is unconfirmed")
    if _CONTROL_INPUT is not None:
        if _CONTROL_INPUT.closed:
            raise RuntimeError("Windows control input is already closed")
        return _CONTROL_INPUT
    import msvcrt
    if pipe.fileno() != 0:
        raise ValueError("Windows worker control input must initially be fd zero")
    control = eof = None
    changed = False
    original_inheritable = os.get_inheritable(0)
    try:
        fd = os.dup(0)
        try:
            os.set_inheritable(fd, False)
            if os.get_inheritable(fd):
                raise OSError("Windows private control input remained inheritable")
            msvcrt.setmode(fd, os.O_BINARY)
            control = os.fdopen(fd, "rb")
        except BaseException:
            try:
                os.close(fd)
            except BaseException:
                _FAILED_CONTROL_INPUTS.append(fd)
            raise
        # Opening NUL, rather than duplicating fd zero, supplies an independent
        # file object that immediately returns EOF to ordinary tool children.
        eof = os.open(os.devnull, os.O_RDONLY | os.O_BINARY)
        changed = True  # A failed dup2 can still have changed its destination.
        os.dup2(eof, 0, inheritable=True)
        _bind_standard_input(0)
        closing, eof = eof, None
        try:
            os.close(closing)
        except BaseException:
            _FAILED_CONTROL_INPUTS.append(closing)
            raise
        _CONTROL_INPUT = control
        return control
    except BaseException as error:
        restored = not changed
        if changed:
            try:
                os.dup2(control.fileno(), 0, inheritable=original_inheritable)
                _bind_standard_input(0)
                restored = True
            except BaseException:
                _FAILED_CONTROL_INPUTS.append(control)
                error.add_note("Windows control-input rollback was not confirmed; no reader was started")
        if control is not None and restored:
            try:
                control.close()
            except BaseException:
                _FAILED_CONTROL_INPUTS.append(control)
        if eof is not None:
            try:
                os.close(eof)
            except BaseException:
                _FAILED_CONTROL_INPUTS.append(eof)
        raise  # A cleanup failure must not replace the original setup error.


class ThreadedPipeReader:
    """One consumer, one requested line, no executor or read-ahead message queue.

    The daemon owns a private binary stream, never sys.stdin's buffered lock.
    Closing cancels delivery, not the OS read: a blocked read retains this one
    descriptor/thread until input/EOF or process exit. Never close that stream
    or join the thread from the loop; either can block shutdown indefinitely.
    This reader is scoped to a worker process, not a reconnectable transport.
    """

    @classmethod
    def for_worker(cls, pipe, limit):
        global _CONTROL_CLAIMED
        if os.name != "nt":
            return cls(pipe, limit)  # Portable forced-threaded probes stay unchanged.
        asyncio.get_running_loop()  # Refuse before changing stdin without a consumer.
        control = isolate_control_input(pipe)
        if _CONTROL_CLAIMED:
            raise RuntimeError("Windows control input already has its reader")
        _CONTROL_CLAIMED = True
        return cls(control, limit, _owned_stream=True)

    def __init__(self, pipe, limit, *, _owned_stream=False):
        self._stream = pipe if _owned_stream else None
        try:
            self._loop = asyncio.get_running_loop()
            self._limit = limit
            self._requests = queue.Queue(maxsize=1)
            self._closed = threading.Event()
            self._pending = None
            self._eof = False
            if self._stream is None:
                # Binary mode also disables the CRT's CRLF/Ctrl-Z translation.
                fd = os.dup(pipe.fileno())
                try:
                    os.set_inheritable(fd, False)
                    self._stream = os.fdopen(fd, "rb")
                except BaseException:
                    try:
                        os.close(fd)
                    except BaseException:
                        pass
                    raise
            self._thread = threading.Thread(
                target=self._read, name="worker-stdin", daemon=True)
            self._thread.start()
        except BaseException:
            if self._stream is not None:
                try:
                    self._stream.close()
                except BaseException:
                    pass  # Preserve the original pre-reader setup failure.
            raise

    async def readline(self):
        if self._closed.is_set():
            raise RuntimeError("Worker input is closed.")
        if self._eof:
            return b""
        if self._pending is not None:
            raise RuntimeError("Worker input already has a pending read.")
        future = self._pending = self._loop.create_future()
        self._requests.put_nowait(future)
        try:
            line = await future
            self._eof = not line
            return line
        except BaseException:
            self.close()
            raise
        finally:
            self._pending = None

    def close(self):
        self._closed.set()
        if self._pending is not None:
            self._pending.cancel()
        try:
            self._requests.put_nowait(None)
        except queue.Full:
            pass  # The queued read will observe _closed before touching stdin.

    def _deliver(self, future, line, error):
        if self._closed.is_set() or future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(line)

    def _read(self):
        with self._stream:
            while True:
                future = self._requests.get()
                if self._closed.is_set() or future is None:
                    return
                line, error = None, None
                try:
                    line = self._stream.readline(self._limit + 1)
                    if len(line) > self._limit:
                        raise ValueError("Runtime message exceeds the transport limit.")
                except Exception as exc:
                    error = exc
                if self._closed.is_set():
                    return
                try:
                    self._loop.call_soon_threadsafe(self._deliver, future, line, error)
                except RuntimeError:
                    return  # The loop closed during cancellation.
                if error is not None or not line:
                    return