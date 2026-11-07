"""Legacy failure groups are history; the current graph owns health.

The previous tier/scope incident-matching contract is deliberately superseded by
Updates current-state semantics (2026-11-07). Keep the original receipts instead
of manufacturing a historical recovery event from a current installation.
"""
import copy
import json

import pytest

from amplifier_web.update_diagnostics import UpdateDiagnostics
from amplifier_web import update_health
from test_update_current_health import mounted_fixture


def test_activation_and_reload_keep_legacy_unknown_and_different_tier_history(tmp_path, monkeypatch):
    manager,stage,project,source=mounted_fixture(tmp_path,monkeypatch)
    original=copy.deepcopy(manager.diagnostics.state)
    assert sorted(g['componentCount'] for g in original['activeFailures'].values())==[0,5]
    assert {g['tier'] for g in original['activeFailures'].values()}=={'included','other'}
    files={p:p.read_bytes() for p in stage.iterdir() if p.is_file()}
    evidence=update_health.active_evidence(manager)
    assert update_health.current(manager.service.state['updates'],evidence)['status']=='current'
    reloaded=UpdateDiagnostics(manager)
    assert reloaded.state==original
    assert files=={p:p.read_bytes() for p in stage.iterdir() if p.is_file()}
    assert not any(e.get('phase')=='ecosystem-reconcile' for e in reloaded.state['events'])


@pytest.mark.parametrize('damage',['validation','graph','lock','activation'])
def test_missing_current_proof_keeps_actionable_limitation_without_touching_history(tmp_path,monkeypatch,damage):
    m,stage,project,source=mounted_fixture(tmp_path,monkeypatch)
    old=copy.deepcopy(m.diagnostics.state['activeFailures'])
    if damage=='validation':(stage/'validated.json').write_text('{}')
    elif damage=='graph':(stage/'runtime-installed.json').write_text('[]')
    elif damage=='lock':(project/'uv.lock').write_text('changed')
    else:m.diagnostics.state['completedBatches']=[]
    evidence=update_health.active_evidence(m)
    status=update_health.current(m.service.state['updates'],evidence)
    if damage=='activation':
        assert evidence['activationUnknown']
        assert status['status']=='unchecked' and not status['actionRequired']
    else:
        assert not evidence
        assert status['actionRequired']
    assert m.diagnostics.state['activeFailures']==old
