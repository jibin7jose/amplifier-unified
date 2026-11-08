"""The integration environment inherits, rather than replaces, runtime policy."""
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from scripts.integration_native import integration_dev_dependencies, project

ROOT = Path(__file__).resolve().parents[1]


def test_integration_project_preserves_declared_runtime_and_host_test_inputs():
    runtime = tomllib.loads((ROOT / "amplifier_web/runtime_deps/pyproject.toml").read_text())
    host = tomllib.loads((ROOT / "pyproject.toml").read_text())
    prepared = tomllib.loads(project(ROOT))
    assert set(runtime["project"]["dependencies"]) <= set(prepared["project"]["dependencies"])
    dev = host["dependency-groups"]["dev"]
    fixture = [item for item in dev if canonicalize_name(Requirement(item).name)
               in {"amplifier-module-tool-filesystem", "amplifier-module-context-managed",
                   "amplifier-module-tool-exec"}]
    assert len(fixture) == 3
    assert any("@663f26a878ae71e136f439fa73ded52b2eb24174" in item for item in fixture)
    assert set(dev) - set(fixture) <= set(prepared["project"]["dependencies"])
    filesystem = [item for item in prepared["project"]["dependencies"]
                  if canonicalize_name(Requirement(item).name) == "amplifier-module-tool-filesystem"]
    assert filesystem == [
        "amplifier-module-tool-filesystem @ git+https://github.com/microsoft/amplifier-module-tool-filesystem@main"]
    context = [item for item in prepared["project"]["dependencies"]
               if canonicalize_name(Requirement(item).name) == "amplifier-module-context-managed"]
    assert context == [
        "amplifier-module-context-managed @ git+https://github.com/microsoft/amplifier-bundle-context-managed@main#subdirectory=modules/context-managed"]
    executor = [item for item in prepared["project"]["dependencies"]
                if canonicalize_name(Requirement(item).name) == "amplifier-module-tool-exec"]
    assert executor == [
        "amplifier-module-tool-exec @ git+https://github.com/microsoft/amplifier-module-tool-exec@main"]
    assert not set(fixture) & set(prepared["project"]["dependencies"])
    for name, source in runtime["tool"]["uv"]["sources"].items():
        assert prepared["tool"]["uv"]["sources"][name] == source
    assert prepared["tool"]["uv"]["no-build-package"] == runtime["tool"]["uv"]["no-build-package"]
    assert prepared["tool"]["uv"]["override-dependencies"] == runtime["tool"]["uv"]["override-dependencies"]
    assert prepared["tool"]["uv"]["sources"]["amplifier-unified"] == {"path": str(ROOT), "editable": True}
    assert not any("amplifier-app-cli" in item for item in prepared["project"]["dependencies"])


@pytest.mark.parametrize("revision", ["main", "a" * 40])
@pytest.mark.parametrize("name", ["amplifier-module-tool-filesystem", "Amplifier_Module_Tool_Filesystem"])
def test_native_filesystem_projection_is_narrow_and_does_not_mutate_host_inputs(revision, name):
    dependencies = [
        "pytest>=8",
        name + " @ git+https://github.com/microsoft/amplifier-module-tool-filesystem@" + revision,
        "other-tool @ git+https://example.invalid/other-tool@main",
        "another-tool[extra]>=2; python_version >= '3.13'",
    ]
    original = dependencies.copy()
    assert integration_dev_dependencies(dependencies) == [dependencies[0], *dependencies[2:]]
    assert dependencies == original


@pytest.mark.parametrize("requirement", [
    "amplifier-module-tool-filesystem @ git+https://example.invalid/other-owner@main",
    "amplifier-module-tool-filesystem>=1",
    "amplifier-module-tool-filesystem @ file:///unreviewed",
    "amplifier-module-tool-filesystem[extra] @ git+https://github.com/microsoft/amplifier-module-tool-filesystem@main",
    "amplifier-module-tool-filesystem @ git+https://github.com/microsoft/amplifier-module-tool-filesystem@main ; python_version >= '3.13'",
    "amplifier-module-tool-filesystem @ git+https://github.com/microsoft/amplifier-module-tool-filesystem@unreviewed-branch",
])
def test_native_filesystem_projection_refuses_unexpected_fixture_policy(requirement):
    with pytest.raises(ValueError, match="Review the host filesystem fixture source"):
        integration_dev_dependencies([requirement])


@pytest.mark.parametrize("revision", ["main", "a" * 40])
@pytest.mark.parametrize("name", ["amplifier-module-context-managed", "Amplifier_Module_Context_Managed"])
def test_native_context_projection_retains_current_lane_without_mutating_fixture(revision, name):
    dependencies = ["pytest>=8", name + " @ git+https://github.com/microsoft/"
                    "amplifier-bundle-context-managed@" + revision + "#subdirectory=modules/context-managed",
                    "other-tool @ git+https://example.invalid/other-tool@main"]
    original = dependencies.copy()
    assert integration_dev_dependencies(dependencies) == [dependencies[0], dependencies[2]]
    assert dependencies == original


@pytest.mark.parametrize("requirement", [
    "amplifier-module-context-managed @ git+https://example.invalid/other-owner@main#subdirectory=modules/context-managed",
    "amplifier-module-context-managed>=1",
    "amplifier-module-context-managed @ file:///unreviewed",
    "amplifier-module-context-managed[extra] @ git+https://github.com/microsoft/amplifier-bundle-context-managed@main#subdirectory=modules/context-managed",
    "amplifier-module-context-managed @ git+https://github.com/microsoft/amplifier-bundle-context-managed@main#subdirectory=modules/context-managed ; python_version >= '3.13'",
    "amplifier-module-context-managed @ git+https://github.com/microsoft/amplifier-bundle-context-managed@unreviewed-branch#subdirectory=modules/context-managed",
    "amplifier-module-context-managed @ git+https://github.com/microsoft/amplifier-bundle-context-managed@main#subdirectory=modules/other",
])
def test_native_context_projection_refuses_unexpected_fixture_policy(requirement):
    with pytest.raises(ValueError, match="Review the host context-managed fixture source"):
        integration_dev_dependencies([requirement])


@pytest.mark.parametrize("revision", ["main", "f267548ccd1b38a236a46afb96252bb6dd474dcd"])
@pytest.mark.parametrize("name", ["amplifier-module-tool-exec", "Amplifier_Module_Tool_Exec"])
def test_native_exec_projection_keeps_current_lane_and_preserves_host_pin(revision, name):
    dependencies = ["pytest>=8", name + " @ git+https://github.com/microsoft/"
                    "amplifier-module-tool-exec@" + revision, "unrelated>=1"]
    original = dependencies.copy()
    assert integration_dev_dependencies(dependencies) == [dependencies[0], dependencies[2]]
    assert dependencies == original


@pytest.mark.parametrize("requirement", [
    "amplifier-module-tool-exec @ git+https://example.invalid/other-owner@main",
    "amplifier-module-tool-exec>=1",
    "amplifier-module-tool-exec @ file:///unreviewed",
    "amplifier-module-tool-exec[extra] @ git+https://github.com/microsoft/amplifier-module-tool-exec@main",
    "amplifier-module-tool-exec @ git+https://github.com/microsoft/amplifier-module-tool-exec@main ; python_version >= '3.13'",
    "amplifier-module-tool-exec @ git+https://github.com/microsoft/amplifier-module-tool-exec@unreviewed-branch",
])
def test_native_exec_projection_refuses_unexpected_fixture_policy(requirement):
    with pytest.raises(ValueError, match="Review the host exec fixture source"):
        integration_dev_dependencies([requirement])


def test_native_project_bootstraps_without_site_packages(tmp_path):
    probe = (
        "import runpy,sys,tomllib; from pathlib import Path; "
        "module=runpy.run_path(sys.argv[1]); "
        "data=tomllib.loads(module['project'](Path(sys.argv[2]))); "
        "assert data['project']['name']=='amplifier-native-integration'; "
        "assert 'packaging' not in sys.modules"
    )
    subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", probe,
         str(ROOT / "scripts/integration_native.py"), str(ROOT)],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )


def test_profiling_requires_explicit_source_instead_of_unrelated_same_named_package():
    base = tomllib.loads(project(ROOT))["project"]["dependencies"]
    assert not any("profiling" in item for item in base)
    url = "git+https://example.invalid/canonical-profiling@deadbeef"
    configured = tomllib.loads(project(ROOT, url))["project"]["dependencies"]
    assert configured.count("amplifier-profiling @ " + url) == 1
    assert [item for item in configured if item != "amplifier-profiling @ " + url] == base


def test_core_source_build_policy_cannot_silently_disappear(tmp_path):
    (tmp_path / "amplifier_web/runtime_deps").mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    source = (ROOT / "amplifier_web/runtime_deps/pyproject.toml").read_text()
    (tmp_path / "amplifier_web/runtime_deps/pyproject.toml").write_text(
        source.replace('no-build-package = ["amplifier-core"]', 'no-build-package = []'))
    with pytest.raises(ValueError, match="prohibit Core source builds"):
        project(tmp_path)


def test_authenticated_loop_checkout_must_be_clean_canonical_and_named(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q")
    git("remote", "add", "origin", "https://github.com/microsoft/amplifier-module-loop-live")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="amplifier-module-loop-live"\nversion="0.0.0"\n')
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "Fixture")
    prepared = tomllib.loads(project(ROOT, loop_source=tmp_path))
    assert prepared["tool"]["uv"]["sources"]["amplifier-module-loop-live"] == {"path": str(tmp_path)}
    (tmp_path / "untracked").write_text("not frozen")
    with pytest.raises(ValueError, match="clean"):
        project(ROOT, loop_source=tmp_path)
    (tmp_path / "untracked").unlink()
    git("remote", "set-url", "origin", "https://example.invalid/other-owner")
    with pytest.raises(ValueError, match="canonical"):
        project(ROOT, loop_source=tmp_path)


def test_native_search_dependencies_are_present_before_pytest_can_skip():
    from scripts.integration_native import REQUIRED_MODULES
    dependencies = tomllib.loads(project(ROOT))['project']['dependencies']
    for package in ('filesystem', 'search'):
        assert any(item.startswith(f'amplifier-module-tool-{package} @ git+https://github.com/microsoft/')
                   for item in dependencies)
    assert {'amplifier_module_tool_filesystem', 'amplifier_module_tool_search.grep',
            'amplifier_module_tool_search.glob'} <= set(REQUIRED_MODULES)


def test_missing_actual_ripgrep_fails_preflight_without_installing(monkeypatch):
    from scripts import integration_native
    monkeypatch.setattr(integration_native.shutil, 'which', lambda _: None)
    monkeypatch.setattr(integration_native.subprocess, 'check_output',
                        lambda *a, **k: pytest.fail('must reject before launching a process'))
    with pytest.raises(ValueError, match='requires ripgrep'):
        integration_native.ripgrep_identity()


def test_native_ripgrep_receipt_uses_the_resolved_executable(monkeypatch):
    from scripts import integration_native
    monkeypatch.setattr(integration_native.shutil, 'which', lambda _: '/owned/bin/rg')
    def version(command, **kwargs):
        assert command == ['/owned/bin/rg', '--version']
        return 'ripgrep 14.1.1\n'
    monkeypatch.setattr(integration_native.subprocess, 'check_output', version)
    assert integration_native.ripgrep_identity() == {'path': '/owned/bin/rg', 'version': 'ripgrep 14.1.1'}
