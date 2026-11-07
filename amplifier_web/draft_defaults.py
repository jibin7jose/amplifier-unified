"""Resolve draft choices in an isolated probe, without a conversation or inference."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
from .probe_process import spawn, stop

async def resolve_defaults(home, workspace, bundle=None, app_bundle=None, *, global_only=False, session_id=None, catalog=False, refresh=False, replacement=None, desired_bundle=False, catalog_cache=None, instance=None, selected_only=False):
    if catalog:
        from .setup import SetupManager
        manager = SetupManager(home, catalog=catalog_cache, global_only=global_only, allow_missing_workspace=True)
        return await manager.discover_catalog(workspace, refresh=refresh, instance=instance, selected_only=selected_only)
    from .bundle_selection import defaults
    from .runtime import RuntimeManager
    chosen=bundle or defaults(home,workspace,app_bundle,global_only=global_only)['effective']
    command=RuntimeManager()._command(home=home)[:-1]+[str(Path(__file__).with_name('draft_defaults_probe.py'))]
    cwd=Path(workspace).expanduser().resolve()
    while not cwd.is_dir() and cwd.parent!=cwd:cwd=cwd.parent
    process=await spawn(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,cwd=cwd,env={**os.environ,'AMPLIFIER_WEB_HOME':str(home)})
    try:
        output,_=await asyncio.wait_for(process.communicate(json.dumps({'home':str(home),'workspace':workspace,'bundle':chosen,'globalOnly':global_only,'sessionId':session_id,'catalog':catalog,'refresh':refresh,'replacement':replacement,'desiredBundle':desired_bundle}).encode()),150 if catalog or replacement is not None else 45)
        result=json.loads(output)
        if process.returncode or result.get('error'):raise ValueError('Draft default resolution failed')
        return {**result,'bundle':chosen,'globalOnly':global_only}
    finally:
        await stop(process)
