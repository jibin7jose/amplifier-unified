"""Client-bound state observations over SSE or an HTTP-pool-independent socket."""
import asyncio
import json
from aiohttp import WSMsgType, web
from .auth import with_history_scope


async def _reject_commands(socket):
    # Only transport ping/pong/close frames are accepted. All app commands keep
    # their authenticated HTTP admission, receipt and client ownership rules.
    async for message in socket:
        if message.type in {WSMsgType.TEXT, WSMsgType.BINARY}:
            await socket.close(code=1008, message=b'State observations are read-only.')
            break


async def _next_snapshot(queue, reader):
    if reader is None:
        return await asyncio.wait_for(queue.get(), 20)
    pending = asyncio.create_task(queue.get())
    try:
        done, _ = await asyncio.wait({pending, reader}, timeout=20,
                                     return_when=asyncio.FIRST_COMPLETED)
        if reader in done:
            raise ConnectionResetError('State socket closed')
        if pending in done:
            return pending.result()
        raise TimeoutError
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def serve(request, service, streams):
    socket = request.path.endswith('/ws')
    if socket and service.clients.current.get() is None:
        from .errors import AppError
        raise AppError('Attach a client before opening its state socket.', 400)
    response = (web.WebSocketResponse(heartbeat=20, max_msg_size=1024) if socket else
                web.StreamResponse(headers={'Content-Type': 'text/event-stream',
                    'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}))
    await response.prepare(request)
    queue = reader = None
    task = asyncio.current_task()
    streams.add(task)
    try:
        if socket:
            reader = asyncio.create_task(_reject_commands(response))
        await service._flush_pending_progress()
        queue = service.subscribe()
        snapshot = service.browser_state()
        selected = next((row for row in snapshot['sessions']
                         if row['id'] == snapshot.get('selectedSessionId')), None)
        if selected and (service.history.windows.bound_web() or selected.get('nativeProject')):
            service._task(service.history.refresh_session(selected['id']))
        previous = None
        compact = request.query.get('transport') == 'delta-v1'
        while True:
            snapshot = with_history_scope(request, snapshot)
            if 'shellClientId' in snapshot:
                event, payload = 'shell', snapshot
            else:
                from .state_transport import delta
                event = 'state-delta' if compact and previous is not None else 'state'
                payload = delta(previous, snapshot) if event == 'state-delta' else snapshot
                previous = snapshot
            if socket:
                await response.send_json({'event': event, 'data': payload})
            else:
                identity = '' if event == 'shell' else 'id: ' + str(snapshot['revision']) + '\n'
                await response.write(('event: ' + event + '\n' + identity +
                                      'data: ' + json.dumps(payload) + '\n\n').encode())
            try:
                snapshot = await _next_snapshot(queue, reader)
            except TimeoutError:
                if not socket:
                    await response.write(b': heartbeat\n\n')
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        streams.discard(task)
        if queue is not None:
            service.unsubscribe(queue)
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            await response.close()
    return response
