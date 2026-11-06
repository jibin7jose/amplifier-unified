"""Check a built wheel with the real upstream behavior and skills module.

This uses provider-free coordinator fixtures, not a live conversation. Run with
amplifier-module-tool-skills installed, or pass its source module directory as
an optional second argument. The behavior include resolves through Foundation.
"""
import asyncio, dataclasses, json, re, sys, tempfile, zipfile
from pathlib import Path
from types import SimpleNamespace

if len(sys.argv) not in (2, 3):
    raise SystemExit("Usage: validate_shell_skill.py WHEEL [SKILLS_MODULE_DIRECTORY]")
wheel = Path(sys.argv[1]).resolve()
skills_module = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else None

async def run(root):
    sys.path.insert(0, str(root))
    if skills_module is not None:
        sys.path.insert(0, str(skills_module))
    from amplifier_web.builtin_behaviors import resource_root, resolve_builtin_behavior, SHELL_BEHAVIOR_URI
    from amplifier_foundation.registry import BundleRegistry
    from amplifier_foundation.mentions import BaseMentionResolver
    from amplifier_module_tool_skills import mount
    assert resource_root().is_relative_to(root)
    registry = BundleRegistry(home=root / 'registry', strict=True)
    behavior = await registry.load(resolve_builtin_behavior(SHELL_BEHAVIOR_URI))
    behavior.resolve_pending_context()
    assert len(behavior.tools) == 1
    assert all(Path(p).is_file() for p in behavior.context.values())
    refs = []
    for md in (resource_root() / 'skills').rglob('*.md'):
        for link in re.findall(r'\]\(([^)]+)\)', md.read_text()):
            assert (md.parent / link).is_file(), (md, link)
            refs.append(link)
    resolver = BaseMentionResolver(bundles={ns:dataclasses.replace(behavior, base_path=p) for ns,p in behavior.source_base_paths.items()})
    assert resolver.resolve('@unified:skills').resolve() == (resource_root() / 'skills').resolve()
    class Hooks:
        def __init__(self): self.rows=[]
        def register(self, event, handler, priority=50, name=None):
            row=(priority,event,handler); self.rows.append(row)
            return lambda: self.rows.remove(row) if row in self.rows else None
        async def emit(self,event,data):
            result=[]
            for _,e,h in sorted(self.rows[:],key=lambda r:r[0]):
                if e==event: result.append(await h(event,data))
            return result
    class Coordinator:
        def __init__(self):
            self.hooks=Hooks(); self.config={}; self.capabilities={}; self.tools={}
            self.session=SimpleNamespace(metadata={})
        def get_capability(self,k): return self.capabilities.get(k)
        def register_capability(self,k,v): self.capabilities[k]=v
        async def mount(self,kind,tool,name): self.tools[name]=tool
    config = dict(behavior.tools[0]['config'])
    # Isolate local/user inputs while leaving the exact namespace source intact.
    config['skills']=[str(root / 'workspace-skills'), str(root / 'user-skills'), '@unified:skills']
    results=[]
    for timing in ('at-mount', 'first-request'):
        coordinator=Coordinator()
        if timing=='at-mount': coordinator.register_capability('mention_resolver',resolver)
        cleanup=await mount(coordinator,config)
        coordinator.register_capability('mention_resolver',resolver)
        output=await coordinator.hooks.emit('provider:request',{})
        assert any('amplifier-shell' in (getattr(item,'context_injection',None) or '') for item in output)
        tool=coordinator.tools['load_skill']
        result=await tool.execute({'skill_name':'amplifier-shell'})
        assert result.success, result
        assert 'Discover the target' in str(result.output)
        assert tool.skills['amplifier-shell'].path.resolve().is_relative_to(resource_root().resolve())
        app = await tool.execute({'skill_name': 'unified-app'})
        assert app.success and 'Read only the companion' in str(app.output)
        app_path = tool.skills['unified-app'].path.resolve()
        assert app_path.is_relative_to(resource_root().resolve())
        # The installed reference stays reachable without joining ambient context.
        assert 'resetPreview' in (app_path.parent / 'canvas.md').read_text()
        assert 'resetPreview' not in str(app.output)
        results.append({'resolverTiming':timing,'catalogVisible':True,'skillLoads':True})
        await cleanup()
    print(json.dumps({'wheel':wheel.name,'packagedRelativeReferences':len(refs),'actualUpstreamBehavior':True,'checks':results},indent=2))

with tempfile.TemporaryDirectory(prefix='unified-shell-wheel-') as td:
    root=Path(td)
    with zipfile.ZipFile(wheel) as archive: archive.extractall(root)
    asyncio.run(run(root))
