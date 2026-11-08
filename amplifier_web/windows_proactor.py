"""Process-local socket cleanup backport of CPython gh-160429.

Upstream: 7b06d6f80df2729a68f78d2a346c043d10f82fd6 (PSF license;
see CPYTHON-LICENSE.txt). Remove this adapter when the supported Windows
interpreter includes that fix. Never modify the interpreter on disk, the pipe
transport class, loop policy, or exception handler. Only the reviewed method
shapes are accepted; an unknown implementation needs review, not suppression.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import socket
import sys
import textwrap
import threading

_ORIGINAL = "3e505aca99bcee5089dd5a2e084726d3f36c384346479a523484c3309fcdfa82"
_FIXED = "c8996436202be950708dc22899f610a23f745901c519fcaa239faef3afbe821e"
_lock = threading.Lock()


def _shape(function):
    # inspect.getsource follows __wrapped__. Its source can therefore describe
    # a reviewed function while the callable actually installed is unknown.
    if hasattr(function, "__wrapped__"):
        return None
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        node = tree.body[0]
        if not isinstance(node, ast.FunctionDef):
            return None
        return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
    except (OSError, TypeError, ValueError, SyntaxError):
        return None


def _call_connection_lost(self, exc):
    if self._called_connection_lost:
        return
    try:
        self._protocol.connection_lost(exc)
    finally:
        # CPython gh-160429: only an already-gone connection is ignored here.
        # Other shutdown/close/protocol exceptions remain observable.
        if hasattr(self._sock, 'shutdown') and self._sock.fileno() != -1:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except ConnectionError:
                pass
        self._sock.close()
        self._sock = None
        server = self._server
        if server is not None:
            server._detach(self)
            self._server = None
        self._called_connection_lost = True


def _install(base, transport):
    """One startup transition; injected classes also permit portable tests."""
    with _lock:
        current = transport._call_connection_lost
        if current is _call_connection_lost:
            return "backport-active"
        if current is not base._call_connection_lost:
            raise RuntimeError("Windows socket cleanup has an unrecognized override; no backport was applied.")
        shape = _shape(current)
        if shape == _FIXED:
            return "upstream-fixed"
        if shape != _ORIGINAL:
            raise RuntimeError("Windows socket cleanup is unrecognized; use a qualified interpreter. No backport was applied.")
        # Both plain TCP and SSLProtocol create this socket transport. Pipe
        # transports retain their original method and subprocess semantics.
        transport._call_connection_lost = _call_connection_lost
        return "backport-active"


def install():
    """Install before this process accepts sockets; report the actual outcome."""
    if sys.platform != "win32":
        return "not-windows"
    if sys.implementation.name != "cpython":
        raise RuntimeError("Windows socket cleanup requires a qualified CPython interpreter.")
    from asyncio.proactor_events import _ProactorBasePipeTransport, _ProactorSocketTransport
    return _install(_ProactorBasePipeTransport, _ProactorSocketTransport)