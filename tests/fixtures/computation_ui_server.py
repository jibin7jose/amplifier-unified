"""Isolated real-process computation UI fixture, no external provider calls."""
import asyncio
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

import settings_ui_server as fixture
from aiohttp import web
from computation_runtime import Runtime


async def main(home, *, output_retention=False):
    app = await fixture.main(home)
    service = app["service"]
    service.runtime = Runtime(service, home / "workspace")
    sid = service._session()["id"]
    if output_retention:
        from amplifier_web.storage_cleanup import policy

        await service.dispatch("storage.cleanup.applyPreset", {
            "expectedRevision": policy(service)["revision"],
            "preset": "temporary-operation-output",
        }, command_id="fixture-output-retention")

        def authorized(request):
            if request.headers.get("Authorization") != "Bearer fixture-browser-control-token":
                raise web.HTTPForbidden()

        async def observations(request):
            authorized(request)
            return web.json_response({"sessionId": sid, "runtimeCalls": len(service.runtime.calls),
                "cells": [row for row in service.operations.journal.list(sid) if row["kind"] == "kernel-cell"]})

        async def expire_output(request):
            authorized(request)
            identity = (await request.json())["id"]
            row = service.operations.journal.status(sid, identity)
            if row["kind"] != "kernel-cell" or row["state"] != "completed" or row.get("outputRetentionDays") != 30:
                raise web.HTTPBadRequest(text="Expected the fixture's completed, opted-in cell")
            # Advance only this journal expiry check to its captured deadline.
            # Policy/Work-reference admission is covered by the backend tests.
            result = service.operations.journal.expire_output(sid, identity, row["revision"], row["outputExpiresAt"])
            return web.json_response(result)

        app.router.add_get("/fixture/output-retention", observations)
        app.router.add_post("/fixture/expire-output", expire_output)
    await service.dispatch("view.update", {"patch": {"draft": "Keep computation draft"}})
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    print(json.dumps({"fixture": "computation-ui", "pid": os.getpid(), "base": url,
                      "url": url, "sessionId": sid}), flush=True)
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="amplifier-computation-ui-") as temp:
        asyncio.run(main(Path(temp), output_retention="--output-retention" in sys.argv))
