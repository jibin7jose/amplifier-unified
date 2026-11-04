"""A new stock runtime cache must not rewrite recorded generations or user pins."""
import json
from pathlib import Path
import tomllib

import pytest

from amplifier_web import runtime_environment as environments
from amplifier_web.runtime import RuntimeManager


OLD_MANIFEST = Path(__file__).with_name('fixtures') / 'runtime_readiness_v1.toml'
PACKAGE = 'amplifier-module-provider-openai'


def command(home, monkeypatch):
    manager = RuntimeManager.__new__(RuntimeManager)
    manager.command = None
    monkeypatch.setattr('amplifier_web.runtime.shutil.which', lambda name: '/synthetic/uv')
    return manager._command(home=home)


def test_stock_compatibility_revision_uses_fresh_cache_and_keeps_provider_branch(tmp_path, monkeypatch):
    # Qualify the genuine ordinary default. Decision-feature projection and
    # frozen-generation retention are covered independently in UI-use packaging.
    monkeypatch.setattr('amplifier_web.app_updates.installed_extras', lambda: [])
    old = OLD_MANIFEST.read_bytes()
    old_project = environments.prepare_project(tmp_path, content=old)
    old_lock = old_project / 'uv.lock'
    old_lock.write_bytes(b'version=1\n# retained old resolution\n')
    before = {p.name: p.read_bytes() for p in old_project.iterdir()}
    selected = command(tmp_path, monkeypatch)
    fresh = Path(RuntimeManager.project_path(selected))
    assert fresh != old_project and '--locked' not in selected
    assert not (fresh / 'uv.lock').exists()  # no fabricated old or new lock
    assert (fresh / 'pyproject.toml').read_bytes() == environments.manifest_path().read_bytes()
    assert {p.name: p.read_bytes() for p in old_project.iterdir()} == before
    new_source = tomllib.loads((fresh / 'pyproject.toml').read_text())['tool']['uv']['sources'][PACKAGE]
    assert new_source == tomllib.loads(old.decode())['tool']['uv']['sources'][PACKAGE]
    assert new_source['rev'] == 'main'


@pytest.mark.parametrize('generation', [None, 'a' * 32])
def test_recorded_baseline_and_generation_keep_exact_manifest_lock_and_cache(tmp_path, monkeypatch, generation):
    receipt = environments.receipt_directory(tmp_path, generation)
    receipt.mkdir(parents=True)
    (receipt / 'runtime.toml').write_bytes(OLD_MANIFEST.read_bytes())
    (receipt / 'runtime.lock').write_bytes(b'version=1\n# immutable qualified resolution\n')
    if generation:
        (tmp_path / 'updates/active.json').write_text(json.dumps({'current': generation}))
    project = environments.project_path(tmp_path, generation)
    before = {p.name:p.read_bytes() for p in receipt.iterdir()}
    selected = command(tmp_path, monkeypatch)
    assert Path(RuntimeManager.project_path(selected)) == project
    assert '--locked' in selected
    assert (project / 'pyproject.toml').read_bytes() == before['runtime.toml']
    assert (project / 'uv.lock').read_bytes() == before['runtime.lock']
    assert {p.name:p.read_bytes() for p in receipt.iterdir()} == before


def test_explicit_provider_pin_is_not_replaced_by_new_stock_main_policy():
    pin = 'b' * 40
    row = {'kind':'runtime dependency','package':PACKAGE,'ref':pin,'current':pin,
           'url':'https://example.invalid/owned-provider','provenance':'installed Git distribution'}
    updated = environments.augmented_manifest(environments.manifest_path().read_bytes(), [row])
    source = tomllib.loads(updated.decode())['tool']['uv']['sources'][PACKAGE]
    assert source == {'git':row['url'],'rev':pin}
