"""Passive access observations from supported, actually mounted file tools."""
import asyncio
from pathlib import Path

from .file_source_guard import GuardedFileTool, _KNOWN


def check(tools, paths):
    result = {}
    for value in paths:
        path = Path(value).resolve()
        rows = []
        for name, mounted in tools.items():
            tool = mounted.tool if isinstance(mounted, GuardedFileTool) else mounted
            kind = _KNOWN.get((type(tool).__module__, type(tool).__name__))
            if kind not in {'write', 'edit', 'patch'}:
                continue
            allowed = None
            try:
                if kind in {'write', 'edit'}:
                    allowed = bool(tool._check_write_access(path)[0])
                elif (type(tool._engine).__module__, type(tool._engine).__name__) in {
                        ('amplifier_module_tool_apply_patch.engines.native', 'NativeEngine'),
                        ('amplifier_module_tool_apply_patch.engines.function', 'FunctionEngine')}:
                    validation = tool._engine._validate_path(path)
                    allowed = True if validation is None else bool(validation[0])
            except (OSError, ValueError, PermissionError):
                allowed = False
            except Exception as exc:
                # The supported function patch engine has its own denial type.
                # An unexpected adapter failure is unknown, never permission.
                if (type(exc).__module__, type(exc).__name__) == (
                        'amplifier_module_tool_apply_patch.engines.function', '_PatchError'):
                    allowed = False
            rows.append({'tool': name, 'writeAllowed': allowed})
        values = [row['writeAllowed'] for row in rows]
        status = ('allowed' if values and all(value is True for value in values) else
                  'denied' if values and all(value is False for value in values) else
                  'tool-dependent' if values and None not in values else 'unknown')
        result[value] = {'status': status, 'tools': rows,
                         'coverage': 'mounted-reference-file-and-patch-policy',
                         'settingsChanged': False,
                         'detail': 'Observation only; ordinary tool hooks and current policy still apply. Shell, browser and database connection policy are not certified.'}
    return result


async def observe(session, paths):
    if (not isinstance(paths, list) or len(paths) > 32 or any(
            not isinstance(path, str) or not path or len(path) > 4000 or not Path(path).is_absolute() for path in paths)):
        raise ValueError('Inspect at most 32 absolute resource paths.')
    coordinator = getattr(session, 'coordinator', None)
    tools = dict(coordinator.get('tools') or {}) if coordinator else {}
    capability = getattr(coordinator, 'get_capability', lambda _: None)
    inspect = capability('web.work_inspect_tool')
    def inspect_tools():
        inspected = {}
        for name, mounted in tools.items():
            tool = inspect(name, mounted) if callable(inspect) else mounted
            if isinstance(tool, GuardedFileTool) and (tool.guard is not capability('web.file_source_guard')
                    or tool.guard.coordinator is not coordinator):
                raise ValueError('The mounted file guard cannot be verified.')
            inspected[name] = tool
        return inspected
    try:
        inspected = inspect_tools()
    except ValueError:
        return {'status': 'unknown', 'paths': {}, 'runtimeSessionId': getattr(session, 'session_id', None)}
    policy = capability('web.write_policy')
    try:
        effective = {}
        for name, mounted in inspected.items():
            if policy and isinstance(mounted, GuardedFileTool) and mounted.kind != 'read':
                module = 'tool-apply-patch' if mounted.kind == 'patch' else 'tool-filesystem'
                effective[name] = policy.invocation(mounted.tool, module, coordinator)
            else:
                effective[name] = mounted
        revision = policy.revision if policy else None
        result = await asyncio.to_thread(check, effective, paths)
        if policy and policy.refresh()['settingsRevision'] != revision:
            raise ValueError('The file policy changed during observation.')
    except Exception:
        return {'status': 'unknown', 'paths': {}, 'runtimeSessionId': getattr(session, 'session_id', None)}
    current = (coordinator.get('tools') or {}) if coordinator else {}
    stable = set(current) == set(tools) and all(current.get(name) is tool for name, tool in tools.items())
    if stable:
        try:
            stable = all(tool is inspected[name] for name, tool in inspect_tools().items())
        except ValueError:
            stable = False
    return {'status': 'observed' if stable and coordinator else 'unknown', 'paths': result if stable else {},
            'runtimeSessionId': getattr(session, 'session_id', None)}
