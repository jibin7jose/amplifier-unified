"""Offline cleanup oracle; real Proactor sockets are checked on Windows."""
import asyncio
import functools
import importlib.util
import inspect
from pathlib import Path
import socket
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from amplifier_web import windows_proactor as repair


def transport(shutdown=None, protocol_error=None):
    sock, protocol, server = Mock(), Mock(), Mock()
    sock.fileno.return_value = 4
    sock.shutdown.side_effect = shutdown
    protocol.connection_lost.side_effect = protocol_error
    return SimpleNamespace(_called_connection_lost=False, _sock=sock,
                           _protocol=protocol, _server=server), sock, protocol, server


@pytest.mark.parametrize("error", [ConnectionResetError(10054, "reset"),
                                  BrokenPipeError("closed"), None])
def test_upstream_backport_completes_cleanup_once(error):
    target, sock, protocol, server = transport(error)
    repair._call_connection_lost(target, None)
    repair._call_connection_lost(target, None)
    protocol.connection_lost.assert_called_once_with(None)
    sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    sock.close.assert_called_once_with()
    server._detach.assert_called_once_with(target)
    assert target._called_connection_lost and target._sock is None and target._server is None


def test_protocol_error_remains_observable_after_cleanup():
    target, sock, _, server = transport(ConnectionResetError(), ValueError("protocol failure"))
    with pytest.raises(ValueError, match="protocol failure"):
        repair._call_connection_lost(target, None)
    sock.close.assert_called_once()
    server._detach.assert_called_once()
    assert target._called_connection_lost


def test_unexpected_shutdown_error_is_not_suppressed():
    target, sock, _, _ = transport(OSError("unexpected shutdown"))
    with pytest.raises(OSError, match="unexpected shutdown"):
        repair._call_connection_lost(target, None)
    sock.close.assert_not_called()  # Matches upstream, not a new cleanup policy.


def test_backport_matches_the_reviewed_upstream_method_shape():
    assert repair._shape(repair._call_connection_lost) == repair._FIXED


def test_unrecognized_or_already_overridden_transport_is_not_changed(monkeypatch):
    class Base:
        def _call_connection_lost(self, exc): pass
    class Socket(Base): pass
    with pytest.raises(RuntimeError, match="unrecognized"):
        repair._install(Base, Socket)
    assert "_call_connection_lost" not in Socket.__dict__
    Socket._call_connection_lost = lambda self, exc: None
    original = Socket._call_connection_lost
    with pytest.raises(RuntimeError, match="override"):
        repair._install(Base, Socket)
    assert Socket._call_connection_lost is original


def test_recognized_original_changes_only_socket_class_and_is_idempotent(monkeypatch):
    class Base:
        def _call_connection_lost(self, exc): pass
    class Socket(Base): pass
    class Pipe(Base): pass
    original = Base._call_connection_lost
    monkeypatch.setattr(repair, "_shape", lambda function: repair._ORIGINAL)
    assert repair._install(Base, Socket) == "backport-active"
    assert repair._install(Base, Socket) == "backport-active"
    assert Socket._call_connection_lost is repair._call_connection_lost
    assert Base._call_connection_lost is original and Pipe._call_connection_lost is original


def test_fixed_runtime_and_non_windows_need_no_override(monkeypatch):
    class Base:
        _call_connection_lost = repair._call_connection_lost
    class Socket(Base): pass
    monkeypatch.setattr(repair, "_shape", lambda function: repair._FIXED)
    # A runtime's fixed function is not the adapter's own object.
    Base._call_connection_lost = lambda self, exc: None
    assert repair._install(Base, Socket) == "upstream-fixed"
    assert "_call_connection_lost" not in Socket.__dict__
    monkeypatch.setattr(repair.sys, "platform", "linux")
    assert repair.install() == "not-windows"


def reviewed_method(kind, tmp_path):
    if kind == "original":
        from asyncio.proactor_events import _ProactorBasePipeTransport
        method = _ProactorBasePipeTransport._call_connection_lost
        assert repair._shape(method) == repair._ORIGINAL
        return method
    # An independent plain fixed callable, not the adapter's identity shortcut.
    path = tmp_path / "reviewed_fixed.py"
    path.write_text(textwrap.dedent(inspect.getsource(repair._call_connection_lost)))
    spec = importlib.util.spec_from_file_location("reviewed_fixed", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    method = module._call_connection_lost
    assert method is not repair._call_connection_lost
    assert repair._shape(method) == repair._FIXED
    return method


@pytest.mark.parametrize("kind", ["original", "fixed"])
def test_wrapped_reviewed_callable_is_not_recognized_or_replaced(kind, tmp_path):
    method = reviewed_method(kind, tmp_path)
    @functools.wraps(method)
    def wrapper(self, exc):
        return method(self, exc)
    class Base:
        _call_connection_lost = wrapper
    class Socket(Base): pass
    class Pipe(Base): pass
    with pytest.raises(RuntimeError, match="unrecognized"):
        repair._install(Base, Socket)
    assert Base._call_connection_lost is wrapper
    assert Socket._call_connection_lost is wrapper
    assert Pipe._call_connection_lost is wrapper
    assert "_call_connection_lost" not in Socket.__dict__


@pytest.mark.parametrize("kind", ["original", "fixed"])
def test_plain_reviewed_callable_retains_positive_and_idempotent_paths(kind, tmp_path):
    method = reviewed_method(kind, tmp_path)
    class Base:
        _call_connection_lost = method
    class Socket(Base): pass
    class Pipe(Base): pass
    expected = "backport-active" if kind == "original" else "upstream-fixed"
    assert repair._install(Base, Socket) == expected
    assert repair._install(Base, Socket) == expected
    assert Base._call_connection_lost is method and Pipe._call_connection_lost is method
    if kind == "original":
        assert Socket._call_connection_lost is repair._call_connection_lost
    else:
        assert Socket._call_connection_lost is method
        assert "_call_connection_lost" not in Socket.__dict__


async def test_app_factory_applies_the_boundary_before_data_or_socket_start(tmp_path, monkeypatch):
    from amplifier_web import server
    def refuse(): raise RuntimeError("unrecognized runtime")
    monkeypatch.setattr(repair, "install", refuse)
    root = tmp_path / "not-created"
    with pytest.raises(RuntimeError, match="unrecognized runtime"):
        await server.create_app(root, voice=False, background_updates=False)
    assert not root.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows Proactor")
def test_native_proactor_tcp_and_tls_use_repaired_socket_not_pipe():
    from asyncio import proactor_events
    base = proactor_events._ProactorBasePipeTransport._call_connection_lost
    socket_class = proactor_events._ProactorSocketTransport
    existing = socket_class.__dict__.get("_call_connection_lost")
    had_override = "_call_connection_lost" in socket_class.__dict__
    loop = asyncio.new_event_loop()
    try:
        assert repair.install() in {"backport-active", "upstream-fixed"}
        assert isinstance(loop, asyncio.ProactorEventLoop)
        assert proactor_events._ProactorBasePipeTransport._call_connection_lost is base
        assert proactor_events._ProactorDuplexPipeTransport._call_connection_lost is base
        # Deterministic exact failure on the actual installed transport method,
        # not a claim reproducing the peer's original FIN/RST timing.
        target, sock, _, server = transport(ConnectionResetError(10054, "reset"))
        socket_class._call_connection_lost(target, None)
        assert target._called_connection_lost
        sock.close.assert_called_once()
        server._detach.assert_called_once()
        # Both maintained TCP/TLS factories instantiate this class directly.
        for name in ("_make_socket_transport", "_make_ssl_transport"):
            source = inspect.getsource(getattr(type(loop), name))
            assert "_ProactorSocketTransport(" in source
    finally:
        loop.close()
        if had_override:
            socket_class._call_connection_lost = existing
        else:
            del socket_class._call_connection_lost


@pytest.mark.skipif(sys.platform != "win32", reason="requires real Windows TCP/TLS Proactor sockets")
def test_native_tcp_tls_reset_during_cleanup_closes_socket_and_server():
    import shutil
    import ssl
    import uuid
    from asyncio import proactor_events
    from amplifier_web import windows_files
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca, ssl_context
    root = Path.home() / (".unified-proactor-test-" + uuid.uuid4().hex)
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    socket_class = proactor_events._ProactorSocketTransport
    original = socket_class.__dict__.get("_call_connection_lost")
    overridden = "_call_connection_lost" in socket_class.__dict__

    class Protocol(asyncio.Protocol):
        def __init__(self):
            self.transport = None
            self.done = asyncio.Event()
            self.lost = 0
        def connection_made(self, value): self.transport = value
        def data_received(self, value): self.transport.write(value)
        def connection_lost(self, exc):
            self.lost += 1
            self.done.set()

    class ResetDuringShutdown:
        def __init__(self, sock): self.sock, self.closed = sock, False
        def __getattr__(self, name): return getattr(self.sock, name)
        def shutdown(self, how): raise ConnectionResetError(10054, "deterministic reset")
        def close(self):
            self.sock.close()
            self.closed = True

    async def exercise(secure):
        loop = asyncio.get_running_loop()
        errors = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: errors.append(context))
        try:
            for tls in (False, True):
                protocols = []
                def factory():
                    protocol = Protocol()
                    protocols.append(protocol)
                    return protocol
                server = await loop.create_server(factory, "127.0.0.1", 0, ssl=secure if tls else None)
                writer = None
                try:
                    client = ssl.create_default_context(cafile=str(root / "config/tls/ca.crt")) if tls else None
                    reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1],
                        ssl=client, server_hostname="localhost" if tls else None)
                    writer.write(b"cleanup-check")
                    await writer.drain()
                    assert await asyncio.wait_for(reader.readexactly(13), 5) == b"cleanup-check"
                    protocol = protocols[0]
                    raw = protocol.transport._ssl_protocol._transport if tls else protocol.transport
                    assert type(raw) is socket_class
                    sock = raw._sock
                    wrapped = ResetDuringShutdown(sock)
                    raw._sock = wrapped
                    raw.close()
                    await asyncio.wait_for(protocol.done.wait(), 5)
                    assert wrapped.closed and sock.fileno() == -1
                    assert raw._called_connection_lost and raw._server is None and protocol.lost == 1
                finally:
                    if writer:
                        writer.close()
                        try: await asyncio.wait_for(writer.wait_closed(), 5)
                        except ConnectionResetError: pass
                    server.close()
                    await asyncio.wait_for(server.wait_closed(), 5)
            assert not errors
        finally: loop.set_exception_handler(previous)

    try:
        assert repair.install() in {"backport-active", "upstream-fixed"}
        configured = setup_local_ca(root, load_server_config(root))
        secure = ssl_context(root, configured)
        assert secure
        asyncio.run(exercise(secure))
    finally:
        if overridden: socket_class._call_connection_lost = original
        elif "_call_connection_lost" in socket_class.__dict__:
            del socket_class._call_connection_lost
        shutil.rmtree(root)