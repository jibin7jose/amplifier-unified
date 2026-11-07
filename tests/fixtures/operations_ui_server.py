"""Isolated operation evidence fixture; no provider or external command calls."""

import asyncio
import json
import os
import signal
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

import settings_ui_server as fixture
from aiohttp import web


def event(sequence, phase, **value):
    return {
        "schemaVersion": 1,
        "source": "tool-bash",
        "kind": "process",
        "operationId": "browser-operation",
        "ownerId": "fixture-mount",
        "eventId": f"browser-operation:{sequence}",
        "sequence": sequence,
        "phase": phase,
        "at": 1,
        **value,
    }


async def main(home):
    app = await fixture.main(home)
    service = app["service"]
    sid = service._session()["id"]
    from amplifier_module_tool_bash import BashTool
    from amplifier_web.runtime_controls import RuntimeControls
    tool = BashTool({"managed_processes": True, "managed_stdin": True,
                     "managed_pty": True, "safety_profile": "unrestricted",
                     "working_dir": service._session()["workspace"]})
    tool._processes.observer = lambda: (lambda value: service.operations.observe(sid, sid, value))
    tool._processes.admission = lambda: (lambda ids: service.app_bridge("questions.admit", {"questionIds": ids}, sid))
    hooks = SimpleNamespace(emit=AsyncMock(return_value=SimpleNamespace(action="continue")))
    coordinator = SimpleNamespace(get=lambda name: {"bash": tool} if name == "tools" else None,
                                  get_capability=lambda name: None, hooks=hooks,
                                  process_hook_result=AsyncMock(side_effect=lambda value, *args: value))
    controls = object.__new__(RuntimeControls)
    controls.coordinator = coordinator
    controls.session = SimpleNamespace(session_id=sid)
    controls.lock = asyncio.Lock()
    original_control = service.runtime.control
    async def control(owner, operation, args):
        if operation.startswith("operations."):
            return await controls.perform(operation, args)
        return await original_control(owner, operation, args)
    service.runtime.control = control
    async def cleanup_tool(app):
        await tool.close()
    app.on_cleanup.append(cleanup_tool)

    async def effects(request):
        path = Path(service._session(sid)["workspace"]) / "effect.txt"
        return web.json_response({"count": len(path.read_text().splitlines()) if path.exists() else 0})
    app.router.add_get("/fixture/effects", effects)
    await service.operations.observe(
        sid, sid, event(1, "started", status={"state": "running"})
    )
    await service.operations.observe(
        sid,
        sid,
        event(
            2,
            "output",
            chunk={
                "cursor": 0,
                "next_cursor": 1,
                "stream": "stdout",
                "text": "Build started\n",
                "source_bytes": 14,
                "binary_output_withheld": False,
            },
        ),
    )
    await service.dispatch(
        "view.update", {"patch": {"draft": "Keep this unsent draft"}}
    )

    async def finish(request):
        await service.operations.observe(
            sid,
            sid,
            event(
                3,
                "output",
                chunk={
                    "cursor": 1,
                    "next_cursor": 2,
                    "stream": "stdout",
                    "text": "Build finished\n",
                    "source_bytes": 15,
                    "binary_output_withheld": False,
                },
            ),
        )
        await service.operations.observe(
            sid,
            sid,
            event(
                4,
                "finished",
                status={
                    "state": "completed",
                    "returncode": 0,
                    "output_complete": True,
                    "total_output_bytes": 29,
                    "ended_at": 2,
                },
            ),
        )
        return web.json_response({"ok": True})

    app.router.add_post("/fixture/finish", finish)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopped.set)
    print(json.dumps({"fixture": "operations-ui", "pid": os.getpid(), "base": url,
                      "url": url, "sessionId": sid}), flush=True)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        loop.remove_signal_handler(signal.SIGTERM)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="amplifier-operations-ui-") as temp:
        asyncio.run(main(Path(temp)))
