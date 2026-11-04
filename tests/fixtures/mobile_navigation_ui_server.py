"""Isolated responsive navigation fixture, provider-free and dynamic-port safe."""
import asyncio
import os
import signal
from pathlib import Path
import tempfile
from aiohttp import web
import chat_ui_server


async def main():
    with tempfile.TemporaryDirectory(prefix='amplifier-mobile-navigation-') as tmp:
        app = await chat_ui_server.fixture.main(Path(tmp))
        # Exercise real WorkWindows loading/retry publication at a deterministic
        # point. Only the first reader attempt is failed; the retry reads the
        # actual synthetic event source after the browser releases this gate.
        service = app['service']
        windows = service.event_log_view.windows
        gate = asyncio.Event()
        gate.set()
        fail_next = set()
        worker = windows._worker()
        project = worker.project
        async def controlled_project(*args, **kwargs):
            key = kwargs.get('key')
            if key in fail_next:
                fail_next.remove(key)
                raise RuntimeError('Synthetic initial work-reader failure')
            await gate.wait()
            return await project(*args, **kwargs)
        worker.project = controlled_project
        async def work_reader(request):
            body = await request.json()
            identity = body['sessionId']
            if not any(row['id'] == identity for row in service.state['sessions']):
                raise web.HTTPNotFound()
            if body['operation'] == 'retry':
                gate.clear()
                fail_next.add(identity + ':latest')
                windows.invalidate({identity})
                windows.current.pop(identity, None)
            elif body['operation'] == 'release':
                gate.set()
            else:
                raise web.HTTPBadRequest()
            windows.retry_at.pop(identity, None)
            windows.request(identity)
            return web.json_response({'accepted': True})
        app.router.add_post('/api/fixture/work-reader', work_reader)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', int(os.environ.get('AMPLIFIER_TEST_PORT', '0')))
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
        print(f'http://127.0.0.1:{port}', flush=True)
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stopped.set)
        try:
            await stopped.wait()
        finally:
            try:
                await runner.cleanup()
            finally:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.remove_signal_handler(sig)


if __name__ == '__main__':
    asyncio.run(main())
