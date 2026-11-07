"""One preparation-local policy composition; fresh proof after any changed input."""
import json
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_policy_reconciliation as policy
from amplifier_web import runtime_environment
from amplifier_web.host import config as host_config
from test_runtime_policy_reconciliation import selected, NAME, URL, git


@pytest.mark.parametrize('change', ['none','manifest','head','binding','config','checked','override','generation'])
async def test_noop_reconciliation_reuses_one_composition_only_for_unchanged_inputs(selected,monkeypatch,change):
    s=selected;project=s.home/'runtime';project.mkdir()
    checked=s.stage/'runtime-sources.json';checked.write_text('{}')
    overrides=s.stage/'runtime-install-overrides.txt';overrides.write_text('original')
    monkeypatch.setattr(host_config,'read_config',lambda *a,**kw:s.config)
    async def inventory(*a):return []
    monkeypatch.setattr(runtime_environment,'update_inventory',inventory)
    key=policy.configuration(s.config)
    monkeypatch.setattr(policy,'pending',lambda *a,**kw:{'configuration':key,'candidates':{'unselected':{'url':URL,'ref':s.old,'current':s.old,'subdirectory':''}}})
    count=0;inputs=None
    async def collect():
        nonlocal count,inputs
        count+=1
        value=await s.collector.report(s.batch,s.config)
        if hasattr(policy,'policy_inputs'):inputs=policy.policy_inputs(s.stage,project)
        return value
    first=await policy.reconcile(s.manager,s.stage,project,collect)
    if change=='manifest':(s.root/'pyproject.toml').write_text('[project]\nname="'+NAME+'"\nversion="2"\n')
    elif change=='head':git(s.root,'checkout','--detach',s.new);s.marker.write_text(json.dumps({'git_url':URL,'ref':'main','commit':s.new}))
    elif change=='binding':s.marker.write_text(json.dumps({'git_url':URL,'ref':'different','commit':s.old}))
    elif change=='config':monkeypatch.setattr(policy,'configuration',lambda *a:'changed')
    elif change=='checked':checked.write_text('{"changed":true}')
    elif change=='override':overrides.write_text('changed')
    elif change=='generation':
        # A different parent after reconcile must not reuse that result.
        (s.home/'updates/active.json').write_text(json.dumps({'current':'d'*32}))
    reuse=getattr(policy,'reuse_collection',None)
    result=await reuse(s.manager,s.stage,project,first,inputs) if reuse else None
    if change=='none':
        if result is None:result=await collect()
        assert count==1, 'Baseline invokes two policy composition processes'
        assert result['packages'][NAME]['revision']==s.old
    else:
        assert result is None
    assert not any('install' in command for command in s.commands)
