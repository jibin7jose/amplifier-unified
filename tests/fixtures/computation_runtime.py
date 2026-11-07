"""Safe fixture adapter using real approved tools; no provider account calls."""

import asyncio
import sys
from contextvars import ContextVar
from types import SimpleNamespace

from amplifier_web.computation import KERNEL_TRANSPORT
from amplifier_web.runtime_controls import RuntimeControls


class Runtime:
    def __init__(self, service, workspace):
        from amplifier_module_tool_bash import BashTool

        self.service = service
        self.workspace = workspace
        self.tools = {
            "bash": BashTool(
                {
                    "managed_processes": True,
                    "managed_stdin": True,
                    "safety_profile": "unrestricted",
                    "working_dir": str(workspace),
                }
            )
        }
        self.calls = []
        self.hook = None
        self.controls = None
        sys.modules.setdefault(
            "amplifier_module_loop_live.scope",
            SimpleNamespace(JOB_CALL=ContextVar("fixture_job", default=None)),
        )

    async def setup(self, session_id):
        if self.controls:
            return

        async def observed(event):
            if event["source"] == "tool-bash" and KERNEL_TRANSPORT.get():
                return {"capturedBy": "computation"}
            return await self.service.operations.observe(session_id, session_id, event)

        async def emit(name, data):
            if name == "tool:pre":
                self.calls.append(
                    {key: value for key, value in data.items() if key != "tool_obj"}
                )
                if self.hook:
                    return await self.hook(data)
            return SimpleNamespace(action="continue")

        async def process_hook(result, *args):
            return result

        async def mount(kind, tool, name):
            self.tools[name] = tool

        coordinator = SimpleNamespace(
            session_id=session_id,
            get=lambda name: self.tools if name == "tools" else None,
            get_capability=lambda name: (
                observed if name == "operations.observe" else None
            ),
            hooks=SimpleNamespace(emit=emit),
            process_hook_result=process_hook,
            mount=mount,
        )
        self.tools["bash"]._processes.observer = lambda: observed
        controls = object.__new__(RuntimeControls)
        controls.coordinator = coordinator
        controls.session = SimpleNamespace(session_id=session_id)
        controls.runtime = SimpleNamespace(generation="fixture-active")
        controls.kernels = controls.kernel_install = None
        controls.lock = asyncio.Lock()
        self.controls = controls

    async def start(self, session, on_event, **kwargs):
        # The public kernel-create path prepares a runtime before control.
        # This fixture mounts approved local tools without a model/provider.
        await self.setup(session["id"])

    async def control(self, session_id, operation, args):
        await self.setup(session_id)
        return await self.controls.perform(operation, args)

    async def close(self):
        if self.controls and self.controls.kernels:
            await self.controls.kernels.shutdown()
        await self.tools["bash"].close()
