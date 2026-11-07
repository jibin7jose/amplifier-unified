"""Hooks must configure the tool that actually executes behind the Work guard."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_core import ToolResult
from amplifier_web.app_guidance import install_app_access


class GatedTool:
    name = 'computer'
    native_tool_spec = {'computer_use': {'version': 1}}

    def __init__(self):
        self._write_approval = None
        self._unattended_writes_ok = False
        self.effects = []

    async def execute(self, arguments):
        if self._write_approval is None:
            raise RuntimeError('approval callback not installed on executing tool')
        if not await self._write_approval(arguments, SimpleNamespace(backend=SimpleNamespace(name='test'))):
            return ToolResult(success=False, error={'code': 'approval_denied'})
        self.effects.append(arguments)
        return ToolResult(success=True, output='recorded')


async def wrapped(late=False):
    tools, capabilities = {}, {}
    async def mount(kind, module, name=None):
        tools[name or module.name] = module
    def get(kind, name=None):
        values = tools if kind == 'tools' else {}
        return values.get(name) if name else values
    coordinator = SimpleNamespace(mount=mount, get=get, get_capability=capabilities.get,
        register_capability=capabilities.__setitem__, hooks=SimpleNamespace(register=lambda *a, **kw: None))
    check = AsyncMock(return_value={'allowed': True})
    capabilities['web.work_effect_check'] = check
    original = GatedTool()
    if not late:
        await coordinator.mount('tools', original)
    await install_app_access(coordinator, AsyncMock(return_value={}))
    if late:
        await coordinator.mount('tools', original)
    else:
        capabilities['web.work_guard_tools']()
    return coordinator, original, tools, check


@pytest.mark.parametrize('late', [False, True])
@pytest.mark.parametrize('allowed,approved', [(True, True), (True, False), (False, True)])
async def test_hook_policy_reaches_executing_tool_without_bypassing_work(late, allowed, approved):
    coordinator, original, tools, check = await wrapped(late)
    mounted = tools['computer']
    approval = AsyncMock(return_value=approved)
    # Same policy-binding operation used by the shared computer-use hook.
    mounted._write_approval = approval
    mounted._unattended_writes_ok = False
    check.return_value = {'allowed': allowed}
    assert original._write_approval is approval
    assert mounted is not original
    assert coordinator.get_capability('web.work_inspect_tool')('computer', mounted) is original
    arguments = {'action': 'key', 'text': 'Escape'}
    result = await mounted.execute(arguments)
    check.assert_awaited_once_with('computer', arguments)
    assert approval.await_count == int(allowed)
    assert original.effects == ([arguments] if allowed and approved else [])
    assert result.success is (allowed and approved)


async def test_policy_rebind_and_tool_replacement_keep_separate_owners():
    coordinator, original, tools, check = await wrapped()
    old = tools['computer']
    old._write_approval = AsyncMock(return_value=True)
    replacement = GatedTool()
    await coordinator.mount('tools', replacement)
    current = tools['computer']
    denied = AsyncMock(return_value=False)
    current._write_approval = denied
    fresh_check = AsyncMock(return_value={'allowed': True})
    coordinator.register_capability('web.work_effect_check', fresh_check)
    coordinator.get_capability('web.work_guard_tools')()
    assert tools['computer'] is current
    assert old._write_approval is not denied and replacement._write_approval is denied
    assert current._work_check is fresh_check
    assert not hasattr(replacement, '_work_check')
    assert not (await current.execute({'action': 'key', 'text': 'Escape'})).success
    assert not original.effects and not replacement.effects
    check.assert_not_awaited()
    fresh_check.assert_awaited_once()


async def test_shared_computer_hook_binds_through_work_wrapper():
    hook = pytest.importorskip('amplifier_module_hook_computer_use')
    coordinator, original, tools, _ = await wrapped()
    coordinator.register_capability('approval.interactive', True)
    coordinator.approval_system = SimpleNamespace(request_approval=AsyncMock(return_value='Allow'))
    handler = hook._make_gate_handler(coordinator)
    await handler('tool:pre', {'tool_name': 'computer'})
    assert callable(original._write_approval)
    arguments = {'action': 'key', 'text': 'Escape'}
    assert (await tools['computer'].execute(arguments)).success
    coordinator.approval_system.request_approval.assert_awaited_once()
    coordinator.approval_system.request_approval.return_value = 'Deny'
    assert not (await tools['computer'].execute(arguments)).success
    assert original.effects == [arguments]
