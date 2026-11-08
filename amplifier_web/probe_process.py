"""Own finite subprocess probes across creation, cancellation and loop delays."""
import asyncio
import os
import signal
import sys
import threading


class _WindowsProbe:
    """Subprocess-shaped view of the existing Job owner, not a second launcher."""

    def __init__(self, owner, *, discard_stderr):
        self._owner = owner
        self.stdin, self.stdout = owner.stdin, owner.stdout
        self.stderr = None if discard_stderr else owner.stderr
        self.returncode = None
        self._discard_task = None
        if discard_stderr:
            self._discard_task = asyncio.create_task(self._discard_stderr())
            self._discard_task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception())

    async def _discard_stderr(self):
        while await self._owner.stderr.read(8192):
            pass

    async def wait(self):
        result = await self._owner.wait()
        # The guard's exit code is NOT the provider/validator exit code.
        # Owner.wait proves empty Job + finalized I/O, not just root exit.
        if result.output_discarded or type(result.payload_returncode) is not int:
            raise RuntimeError('Native probe ended without a complete payload result.')
        self.returncode = result.payload_returncode
        if self._discard_task is not None:
            await self._discard_task
        return self.returncode

    async def communicate(self, input=None):
        from .probe_diagnostics import _exchange

        return await _exchange(self, input, capture_stderr=True)

    async def close(self):
        try:
            result = await self._owner.close()
            self.returncode = result.payload_returncode
        finally:
            if self._discard_task is not None:
                self._discard_task.cancel()
                await asyncio.gather(self._discard_task, return_exceptions=True)


async def _spawn_windows(command, kwargs):
    # These are the binary-pipe shapes used by Settings, defaults and validation.
    # Do not silently ignore subprocess flags or inherit the host's stdin.
    options = dict(kwargs)
    stdin = options.pop('stdin', None)
    stdout = options.pop('stdout', None)
    stderr = options.pop('stderr', None)
    cwd = options.pop('cwd', None)
    env = options.pop('env', None)
    if (options or stdin != asyncio.subprocess.PIPE or stdout != asyncio.subprocess.PIPE
            or stderr not in (asyncio.subprocess.PIPE, asyncio.subprocess.DEVNULL)):
        raise ValueError('Native probes require binary stdin/stdout pipes and piped or discarded stderr.')
    from .windows_jobs import WindowsOwnedProcess

    # start owns creation-cancellation, including failed-cleanup custody. Never
    # wrap it in a detached spawn or fall back to a root-only subprocess.
    owner = await WindowsOwnedProcess.start(
        [os.fspath(arg) for arg in command],
        cwd=os.getcwd() if cwd is None else os.fspath(cwd),
        env=dict(os.environ if env is None else env),
        trusted_python=getattr(sys, '_base_executable', None),
    )
    return _WindowsProbe(owner, discard_stderr=stderr == asyncio.subprocess.DEVNULL)


async def _settle(task):
    """Finish the owned finalizer before propagating another cancellation."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def isolated(operation):
    """Supervise a finite probe independently of the caller's event loop.

    Keep its pipe readers and deadline runnable during host publication. The
    executor task is shielded, never abandoned: cancellation reaches the owned
    operation once and waits for its process cleanup and worker-loop shutdown.
    """
    lock = threading.Lock()
    owner = None
    cancelled = False

    async def invoke():
        nonlocal owner
        with lock:
            if cancelled:
                raise asyncio.CancelledError
            owner = asyncio.get_running_loop(), asyncio.current_task()
        try:
            return await operation()
        finally:
            with lock:
                owner = None  # Clear before asyncio.run closes the worker loop.

    worker = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(invoke())))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        with lock:
            cancelled = True
            if owner is not None:
                loop, task = owner
                loop.call_soon_threadsafe(task.cancel)
        try:
            await _settle(worker)
        except asyncio.CancelledError:
            pass
        raise  # A cleanup failure above deliberately takes precedence.


async def _stop(process, readers=(), grace=0):
    readers = tuple(asyncio.ensure_future(reader) for reader in readers)
    if isinstance(process, _WindowsProbe):
        try:
            # The public owner offers finite force-close, not graceful signals.
            # Close even after payload exit: only the owner proves tree/I/O exit.
            await process.close()
        except BaseException:
            # Failed cleanup may not produce EOF. Settle our readers without
            # hiding CleanupError or dropping the owner's retained lifelines.
            for reader in readers:
                reader.cancel()
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)
            raise
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
        return
    if os.name == 'nt':
        raise RuntimeError('Native probe cleanup requires its Windows Job owner.')
    # Root exit does not imply tree exit: a descendant can still hold a pipe.
    if process.returncode is None or any(not reader.done() for reader in readers):
        def send(force):
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
                elif force:
                    process.kill()
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
        if grace and process.returncode is None:
            send(False)
            try:
                await asyncio.wait_for(process.wait(), grace)
            except TimeoutError:
                send(True)
        else:
            send(True)
        await process.wait()
    if readers:
        await asyncio.gather(*readers, return_exceptions=True)


async def stop(process, *readers, grace=0):
    await _settle(asyncio.create_task(_stop(process, readers, grace)))


async def spawn(*command, **kwargs):
    if os.name == 'nt':
        return await _spawn_windows(command, kwargs)
    # asyncio otherwise kills only the initial child if creation is cancelled
    # while pipe transports connect. Its already-started descendants survive.
    start = asyncio.create_task(asyncio.create_subprocess_exec(
        *command, **kwargs, start_new_session=True))
    try:
        return await asyncio.shield(start)
    except asyncio.CancelledError:
        async def finish_start():
            try:
                process = await start
            except OSError:
                return
            # Creation may finish with a full stdout pipe. Drain concurrently
            # before waiting for tree exit, just as after ownership transfer.
            drain = asyncio.create_task(process.communicate())
            await _stop(process, (drain,))
        await _settle(asyncio.create_task(finish_start()))
        raise
