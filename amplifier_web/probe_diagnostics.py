"""Bounded provider subprocess evidence, summarized without copying SDK logs."""
import asyncio
import re


# Probe JSON is a finite metadata/result document, not an open-ended SDK log.
# Reject overflow instead of accepting a truncated JSON prefix. StreamReader
# flow control plus chunked drain keeps transport buffering bounded as well.
MAX_PROBE_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 8192


async def _capture(stream):
    if stream is None:
        return None
    output = bytearray()
    while chunk := await stream.read(_CHUNK_BYTES):
        if len(output) + len(chunk) > MAX_PROBE_BYTES:
            raise ValueError('Probe output exceeded the bounded capture limit.')
        output.extend(chunk)
    return bytes(output)


async def _exchange(process, payload, *, capture_stderr=False):
    async def write_input():
        try:
            if payload is not None:
                if len(payload) > MAX_PROBE_BYTES:
                    raise ValueError('Probe input exceeded the bounded capture limit.')
                for offset in range(0, len(payload), _CHUNK_BYTES):
                    process.stdin.write(payload[offset:offset + _CHUNK_BYTES])
                    await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    tasks = [asyncio.create_task(_capture(process.stdout)),
             asyncio.create_task(write_input()), asyncio.create_task(process.wait())]
    if capture_stderr:
        tasks.append(asyncio.create_task(_capture(process.stderr)))
    try:
        results = await asyncio.gather(*tasks)
        return results[0], results[3] if capture_stderr else None
    finally:
        # gather does not cancel siblings on a reader/write failure. Do not leave
        # an orphaned wait or competing stream reader for the caller's stop().
        # Also close if cancellation prevented write_input from ever starting.
        process.stdin.close()
        for task in tasks:
            if not task.done():
                task.cancel()

        async def finish():
            results = await asyncio.gather(*tasks, return_exceptions=True)
            from .windows_jobs import CleanupError
            for result in results:
                if isinstance(result, CleanupError):
                    raise result  # Cleanup failure takes precedence over cancellation.

        from .probe_process import _settle
        await _settle(asyncio.create_task(finish()))


class HostProbeFailure(ValueError):
    """Closed host-owned remedies; no interpolated provider text."""
    messages = {
        'timeout': 'Provider check timed out after 90 seconds. Check connectivity and credentials, then retry.',
        'disabled': 'This configured connection is disabled. Enable it before checking it.',
        'unsaved': 'Save this provider before discovering models or testing it.',
        'not-verifiable': 'This provider does not expose API model listing for connection testing. Local recommendations do not verify connectivity.',
    }

    def __init__(self, code):
        self.code = code
        self.public_message = self.messages[code]
        self.diagnostic = {'stage': 'probe', 'category': 'HostProbeFailure', 'code': code}
        super().__init__(self.public_message)


class ProbeProcessFailure(ValueError):
    """Host-authored subprocess facts, never an arbitrary provider message."""
    def __init__(self, returncode, stderr):
        self.public_message = (
            f'The provider check ended without a valid result (exit code {int(returncode)}). '
            + failure_detail(stderr))
        self.diagnostic = {'stage':'preparation','category':'ProbeProcessFailure'}
        super().__init__(self.public_message)


async def communicate(process, payload):
    """Read stdout while the caller's separate bounded reader drains stderr."""
    from .probe_process import _WindowsProbe

    if isinstance(process, _WindowsProbe):
        output, _ = await _exchange(process, payload)
        return output

    # Retain the ordinary asyncio subprocess seam and POSIX lifetime behavior.
    # The Windows owner has no communicate method; its adapter uses _exchange.
    async def write_input():
        try:
            process.stdin.write(payload)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    output, _, _ = await asyncio.gather(process.stdout.read(), write_input(), process.wait())
    return output


async def stderr_tail(stream):
    tail = b''
    while chunk := await stream.read(8192):
        tail = (tail + chunk)[-32768:]
    return tail.decode(errors='replace')


def failure_detail(stderr):
    # Never echo arbitrary stderr: it may contain credentials, URLs, paths or
    # request bodies. Emit only fixed descriptions and numeric version evidence.
    text = stderr.lower()
    rust = re.search(r'rustc (\d{1,4}\.\d{1,4}(?:\.\d{1,4})?) is not supported', text)
    required = re.search(r'requires rustc (\d{1,4}\.\d{1,4}(?:\.\d{1,4})?)', text)
    if rust:
        return ('Runtime build failed: Rust ' + rust[1] + ' is unsupported' +
                ('; a dependency requires Rust ' + required[1] if required else '') +
                '. Update the app to use the published runtime dependency.')
    for markers, detail in (
        (('no space left on device',), 'Runtime preparation failed: the server disk is full. Free space and retry.'),
        (('certificate_verify_failed', 'certificate verify failed'), 'The runtime download failed TLS certificate verification. Check the server trust configuration.'),
        (('could not resolve host', 'name or service not known', 'nodename nor servname'), 'The runtime download could not resolve its host. Check server DNS and connectivity.'),
        (('429 too many requests', 'rate limit exceeded'), 'The runtime download was rate limited. Wait for the remote limit to reset, then retry.'),
        (('no solution found', 'resolutionimpossible'), 'The runtime dependencies could not be resolved. Update the app and check its configured dependency sources.'),
        (('maturin.build_wheel', 'failed to build', 'failed building wheel'), 'A runtime dependency failed to build. Update the app to use published binaries, or check the configured source dependency toolchain.'),
        (('modulenotfounderror', 'importerror:'), 'The provider subprocess could not import a required module. Repair or update the runtime environment.'),
        (('permission denied', 'permissionerror:'), 'Runtime preparation was denied access to a required file. Check server file permissions.'),
    ):
        if any(marker in text for marker in markers):
            return detail
    return 'Check the runtime installation or update the app, then retry.'
