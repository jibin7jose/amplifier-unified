"""Runtime batch policy follows package installs, not contributor sync groups."""
import json
import shutil
import subprocess
import sys

import pytest

from amplifier_web import runtime_source_alignment as alignment
from amplifier_web import runtime_source_policy as policy
from amplifier_web import runtime_policy_reconciliation as reconciliation
from amplifier_web.runtime_environment import ProtectedRuntimeSource
from amplifier_web import selected_runtime_dependencies as dependencies
from test_runtime_policy_reconciliation import selected, NAME, URL, git
from test_selected_runtime_dependencies import dist


def group_manifest(group="dev", *, default=False, include=False, legacy=False, marked=False):
    requirement = NAME + " @ git+" + URL + "@" + "d" * 40
    if marked:
        requirement = NAME + "[unused] @ git+" + URL + "@" + "d" * 40 + ' ; python_version > "1"'
    raw = '[project]\nname="fixture-hook"\nversion="1"\ndependencies=[]\n'
    if legacy:
        return (raw + '[tool.uv]\ndev-dependencies=' + json.dumps([requirement]) + '\n').encode()
    raw += '[dependency-groups]\n' + group + '=' + json.dumps([requirement]) + '\n'
    if include:
        raw += 'all-checks=[{include-group="' + group + '"}]\n'
    if default:
        raw += '[tool.uv]\ndefault-groups=' + json.dumps(["all-checks" if include else group]) + '\n'
    return raw.encode()


@pytest.mark.parametrize("case", ["dev", "test", "qa", "default", "included", "marked", "legacy"])
async def test_collected_package_groups_do_not_constrain_independent_runtime_source(selected, case):
    s = selected
    raw = group_manifest(case if case in {"dev", "test", "qa"} else "test",
                         default=case in {"default", "included"}, include=case == "included",
                         marked=case == "marked", legacy=case == "legacy")
    hook = s.root / "hook"
    hook.mkdir()
    (hook / "pyproject.toml").write_bytes(raw)
    git(s.root, "add", "hook")
    git(s.root, "commit", "-m", "contributor checks")
    head = git(s.root, "rev-parse", "HEAD")
    s.marker.write_text(json.dumps({"git_url": URL, "ref": "main", "commit": head}))
    s.batch.names["fixture-hook"] = hook
    assert NAME in policy.constraint_declarations(raw)  # Generic sync stays conservative.
    report = await s.collector.report(s.batch, s.config)
    alignment.verify_checked_selection(
        {NAME: {"url": URL, "ref": "main", "revision": head, "subdirectory": ""}},
        report, exact=True,
    )
    assert report["packages"][NAME]["status"] == "selected"
    assert (hook / "pyproject.toml").read_bytes() == raw


@pytest.mark.parametrize("case", ["dev", "test", "qa", "default", "included", "marked", "legacy"])
def test_transitive_runtime_choice_ignores_group_only_pin_and_marker(case):
    raw = group_manifest(case if case in {"dev", "test", "qa"} else "test",
                         default=case in {"default", "included"}, include=case == "included",
                         marked=case == "marked", legacy=case == "legacy").replace(
        b'dependencies=[]', ('dependencies=["' + NAME + ' @ git+' + URL + '@main"]').encode())
    result = dependencies.selected({"fixture-hook": raw}, distributions=[dist()])
    assert result[NAME]["status"] == "selected"
    assert NAME not in policy.declarations(raw)  # Generic sync's competing pin remains visible.


@pytest.mark.parametrize("kind", ["runtime", "optional", "source", "override", "constraint", "build-constraint"])
async def test_actual_source_restrictions_remain_protected_with_inactive_groups(selected, kind):
    s = selected
    requirement = NAME + " @ git+" + URL + "@" + "e" * 40
    raw = group_manifest("qa", marked=True)
    if kind == "runtime":
        raw = raw.replace(b"dependencies=[]", ("dependencies=" + json.dumps([requirement])).encode())
    elif kind == "optional":
        raw += ('[project.optional-dependencies]\nfeature=' + json.dumps([requirement]) + '\n').encode()
    elif kind == "source":
        raw += ('[tool.uv.sources]\n' + NAME + '={git="' + URL + '", rev="' + "e" * 40 + '"}\n').encode()
    else:
        key = {"override": "override-dependencies", "constraint": "constraint-dependencies",
               "build-constraint": "build-constraint-dependencies"}[kind]
        raw += ('[tool.uv]\n' + key + '=' + json.dumps([requirement]) + '\n').encode()
    hook = s.root / "hook"
    hook.mkdir()
    (hook / "pyproject.toml").write_bytes(raw)
    git(s.root, "add", "hook")
    git(s.root, "commit", "-m", "preserve runtime source choice")
    head = git(s.root, "rev-parse", "HEAD")
    s.marker.write_text(json.dumps({"git_url": URL, "ref": "main", "commit": head}))
    s.batch.names["fixture-hook"] = hook
    report = await s.collector.report(s.batch, s.config)
    with pytest.raises(ProtectedRuntimeSource, match="worker dependency source"):
        alignment.verify_checked_selection(
            {NAME: {"url": URL, "ref": "main", "revision": head, "subdirectory": ""}},
            report, exact=True,
        )
    assert report["packages"][NAME]["status"] == "authored-constraint"
    assert (hook / "pyproject.toml").read_bytes() == raw


def test_previous_group_selection_receipt_is_rechecked(selected, monkeypatch):
    from test_runtime_policy_reconciliation import row
    s = selected
    monkeypatch.setattr('amplifier_web.host.config.read_config', lambda *a, **kw: s.config)
    oldrow = row()
    receipt = s.home / "updates/baseline-runtime"
    receipt.mkdir()
    receipt.joinpath(reconciliation.RECEIPT).write_text(json.dumps({
        "version": 1, "selectionVersion": 3, "configuration": reconciliation.configuration(s.config),
        "remaining": reconciliation.legacy_candidates([oldrow]),
        "decisions": {NAME: "authored-constraint"}, "offeredPackages": [],
    }))
    assert reconciliation.pending(s.home, None, [oldrow]) is not None


async def test_real_foundation_batch_does_not_install_even_default_included_groups(selected, tmp_path, monkeypatch):
    from amplifier_foundation.modules.activator import ModuleActivator
    from amplifier_foundation.modules.batch import DependencyBatch
    from test_runtime_checked_cache import BACKEND

    uv = shutil.which("uv")
    assert uv, "This regression requires the owned qualification environment's uv"
    s = selected
    raw = group_manifest("checks", default=True, include=True).replace(
        b'fixture-hook', NAME.encode())
    raw += b'[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n'
    (s.root / "pyproject.toml").write_bytes(raw)
    (s.root / "backend.py").write_text(BACKEND)
    git(s.root, "add", "pyproject.toml", "backend.py")
    git(s.root, "commit", "-m", "default checks are not runtime dependencies")
    head = git(s.root, "rev-parse", "HEAD")
    s.marker.write_text(json.dumps({"git_url": URL, "ref": "main", "commit": head}))
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    target = tmp_path / "target"
    subprocess.run([uv, "venv", "--python", sys.executable, str(target)], check=True, capture_output=True)
    python = str(target / "bin/python")
    batch = DependencyBatch()
    activator = ModuleActivator(cache_dir=s.stage / "foundation/cache", install_python=python,
                                refresh_dependencies=True, dependency_batch=batch)
    batch.add(activator, s.root)
    commands = []
    run = subprocess.run
    def record(command, *args, **kwargs):
        if command[:3] == ["uv", "pip", "install"]:
            commands.append(list(command))
        return run(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", record)
    await batch.install()
    assert len(commands) == 1 and "--no-sources" in commands[0]
    assert "--group" not in commands[0]
    installed = subprocess.check_output([python, "-I", "-c",
        "import importlib.metadata as m,json; print(json.dumps(sorted(d.metadata['Name'] for d in m.distributions())))"],
        text=True)
    assert json.loads(installed) == [NAME]
    report = await s.collector.report(batch, s.config, allow_generated=True)
    alignment.verify_checked_selection(
        {NAME: {"url": URL, "ref": "main", "revision": head, "subdirectory": ""}},
        report, exact=True,
    )
    assert (s.root / "pyproject.toml").read_bytes() == raw
