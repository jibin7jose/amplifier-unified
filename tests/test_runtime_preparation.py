"""The native worker's immutable receipt describes its entire actual lifetime."""
import asyncio
import builtins
import subprocess
from types import SimpleNamespace

import pytest

from amplifier_web.runtime_preparation import preparation_scope
from amplifier_web.runtime_worker import Worker


@pytest.mark.parametrize("value", ["", "0"])
def test_default_preparation_does_not_require_policy_support(monkeypatch, value):
    monkeypatch.setenv("AMPLIFIER_RUNTIME_IMMUTABLE", value)
    original = builtins.__import__
    def imported(name, *args, **kwargs):
        if name == "amplifier_foundation.modules":
            pytest.fail("Mutable startup must not acquire a new dependency")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", imported)
    with preparation_scope() as report:
        assert report == {}


@pytest.mark.parametrize("value", ["true", "false", "2"])
async def test_malformed_policy_refuses_before_reading_work(monkeypatch, value):
    monkeypatch.setenv("AMPLIFIER_RUNTIME_IMMUTABLE", value)
    worker = Worker()
    with pytest.raises(ValueError, match="immutable-runtime"):
        await worker.run()
    assert worker.preparation_report == {}
    assert not worker.tasks


async def test_old_foundation_refuses_before_reading_work(monkeypatch):
    monkeypatch.setenv("AMPLIFIER_RUNTIME_IMMUTABLE", "1")
    original = builtins.__import__
    def imported(name, *args, **kwargs):
        if name == "amplifier_foundation.modules":
            raise ImportError("old Foundation")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", imported)
    worker = Worker()
    with pytest.raises(RuntimeError, match="requires Foundation"):
        await worker.run()
    assert worker.preparation_report == {}
    assert not worker.tasks


async def test_real_policy_covers_root_child_lazy_activation_and_cleanup(tmp_path, monkeypatch):
    foundation = pytest.importorskip("amplifier_foundation.modules")
    if not hasattr(foundation, "dependency_installation_policy"):
        pytest.skip("Native runtime qualification requires compatible Foundation")
    monkeypatch.setenv("AMPLIFIER_RUNTIME_IMMUTABLE", "1")
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname="worker-policy-fixture"\nversion="0.0.0"\n'
        'dependencies=["must-never-resolve-worker-policy"]\n')
    activators = []
    observed = []
    def activator():
        return foundation.ModuleActivator(cache_dir=tmp_path / "cache",
                                          strict=True, refresh_dependencies=True)
    async def attempt(label):
        candidate = activator()
        with pytest.raises(foundation.DependencyInstallationDenied):
            await candidate.activate("tool-worker-policy-fixture", str(source))
        observed.append(label)
        return candidate
    def forbidden(*args, **kwargs):
        pytest.fail("Immutable activation reached a dependency installer")
    monkeypatch.setattr(subprocess, "run", forbidden)

    worker = Worker()
    async def read():
        await attempt("root")
        await asyncio.create_task(attempt("child"))
        # An activator created in a propagated thread keeps the policy after
        # the worker has finished, without relying on its environment flag.
        activators.append(await asyncio.to_thread(activator))
        worker.shutdown.set()
    async def cleanup():
        await attempt("cleanup")
    worker.read = read
    worker.session = SimpleNamespace(cleanup=cleanup)
    await asyncio.wait_for(worker.run(), 5)
    assert observed == ["root", "child", "cleanup"]
    assert worker.preparation_report == {"runtimePreparation": {
        "version": 1, "immutable": True, "dependencyInstallation": False,
        "policy": "foundation-dependency-installation"}}
    monkeypatch.delenv("AMPLIFIER_RUNTIME_IMMUTABLE")
    with pytest.raises(foundation.DependencyInstallationDenied):
        await activators[0].activate("tool-worker-policy-fixture", str(source))
