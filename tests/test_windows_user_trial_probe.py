"""Portable models of the CI fixture only: never launch the Windows journey."""
import ast
import base64
import copy
import csv
import hashlib
import importlib.util
import json
import io
from pathlib import Path
import socket
import subprocess
import sys
import types
import zipfile

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("windows_trial_fixture",
    ROOT / "tests/fixtures/windows_user_trial_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_fixture_and_embedded_provider_compile_and_require_maintained_modules():
    compile(ast.parse(Path(SPEC.origin).read_text(encoding="utf-8")), SPEC.origin, "exec")
    compile(probe.PROVIDER, "<scripted-provider>", "exec")
    requirements = probe.requirements(ROOT / "pyproject.toml")
    assert len(requirements) == 4
    assert all("git+https://github.com/microsoft/" in value for value in requirements)
    assert any(value.startswith("amplifier-module-context-simple @") for value in requirements)


def test_current_default_ui_use_graph_remains_declared_and_audited():
    import tomllib
    host = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    worker = tomllib.loads((ROOT / "amplifier_web/runtime_deps/pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert any(value.startswith("amplifier-bundle-ui-use @ ") for value in host["dependencies"])
    assert any(value.startswith("amplifier-bundle-ui-use @ ") for value in worker["dependencies"])
    assert any(value.startswith("qrcode") for value in host["dependencies"])
    for name in ("amplifier_ui_use", "amplifier_module_tool_ui_use"):
        assert probe.IMPORTS[name] == "amplifier-bundle-ui-use"
    assert probe.IMPORTS["qrcode"] == "qrcode"
    # This is installed-package provenance, not a claim that the UI-use tool or
    # optional browser/decision backend was mounted or exercised on Windows.


def test_scripted_provider_passes_real_metadata_constructor_before_mount(monkeypatch):
    from amplifier_web.provider_environment import provider_class, provider_config_schema
    module = types.ModuleType("amplifier_module_provider_fixture")
    exec(compile(probe.PROVIDER, "<scripted-provider>", "exec"), module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert provider_class("provider-fixture") is module.Provider
    # The real metadata path constructs with config={} and NO coordinator.
    # Await-free: no loop, socket, mount, model call, filesystem write or secret.
    assert immediate(provider_config_schema("provider-fixture", config={}, environment={})) == {"fields": []}


def core_contract(coroutine):
    """Run actual Core awaitables with a finite deadline, not a runtime probe."""
    import asyncio
    async def bounded():
        return await asyncio.wait_for(coroutine, 5)
    return asyncio.run(bounded())


def test_scripted_provider_actual_core_validation_mount_and_metadata_are_effect_free(
        tmp_path, monkeypatch):
    from amplifier_core import ProviderInfo
    from amplifier_core.testing import MockCoordinator
    from amplifier_core.validation.provider import ProviderValidator
    from amplifier_web.provider_environment import construct_schema_provider, construct_provider, config_schema
    module = types.ModuleType("amplifier_module_provider_fixture")
    exec(compile(probe.PROVIDER, "<scripted-provider>", "exec"), module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    config = {name: str(tmp_path / name) for name in ("output", "blocked", "calls")}
    # Actual Core 2.0.2 validator: structural methods, real Rust-backed mount
    # coordinator and ProviderInfo. No replacement validator/metadata seam.
    result = core_contract(ProviderValidator().validate(module.__name__, config=config))
    assert result.passed and result.checks and all(check.passed for check in result.checks)
    assert {check.name for check in result.checks} >= {
        "protocol_compliance", "provider_get_info", "provider_list_models", "provider_complete",
        "provider_parse_tool_calls"}
    schema_provider = construct_schema_provider(module.Provider, config, environment={})
    setup_provider = construct_provider(module.Provider, config)
    assert schema_provider.coordinator is None and schema_provider.config == {}
    assert setup_provider.coordinator is None and setup_provider.config == config
    assert isinstance(schema_provider.get_info(), ProviderInfo)
    assert immediate(config_schema(schema_provider)) == {"fields": []}
    coordinator = MockCoordinator()
    core_contract(module.mount(coordinator, config))
    provider = coordinator.get("providers")["fixture"]
    assert provider.coordinator is coordinator and provider.config == config
    from amplifier_web.provider_isolation import require_selected, UnavailableProvider
    assert not isinstance(provider, UnavailableProvider)
    require_selected(coordinator)
    assert provider.get_info().defaults["model"] == "fixture"
    assert immediate(provider.list_models()) == []
    assert provider.parse_tool_calls(types.SimpleNamespace(tool_calls=[])) == []
    assert not any((tmp_path / name).exists() for name in config)


def test_legacy_provider_constructor_reproduces_real_schema_isolation_refusal(monkeypatch):
    from amplifier_web.provider_environment import provider_config_schema
    from amplifier_web.provider_isolation import ProviderIsolation, ProviderUnavailable, UnavailableProvider
    from amplifier_core.testing import MockCoordinator
    module = types.ModuleType("amplifier_module_provider_fixture")
    exec(compile(probe.PROVIDER, "<scripted-provider>", "exec"), module.__dict__)
    def legacy(self, coordinator, config):
        self.coordinator, self.config = coordinator, config
    module.Provider.__init__ = legacy
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(TypeError, match="coordinator") as caught:
        immediate(provider_config_schema("provider-fixture", config={}, environment={}))
    # Same real isolation classification as materialize_bundle_providers on this
    # exception. The fallback stays unavailable; no require_selected bypass.
    isolation = ProviderIsolation()
    spec = {"module": "provider-fixture", "config": {}}
    failure = isolation.record(spec, "provider_schema_failed", error=caught.value)
    assert failure["reason_code"] == "provider_schema_failed"
    assert failure["diagnostic"] == {"stage": "schema", "category": "schema", "errorType": "TypeError"}
    unavailable = UnavailableProvider(spec, failure)
    coordinator = MockCoordinator()
    core_contract(coordinator.mount("providers", unavailable, name="fixture"))
    from amplifier_web.provider_isolation import require_selected
    with pytest.raises(ProviderUnavailable) as refused:
        require_selected(coordinator)
    assert refused.value.failures[0]["reason_code"] == "provider_schema_failed"
    assert refused.value.failures[0]["diagnostic"] == failure["diagnostic"]


def test_real_provider_contract_checks_remain_network_guarded_in_fresh_interpreter(tmp_path):
    script = r'''
import importlib.util, os, pathlib, sys
from contextlib import nullcontext
root, log = map(pathlib.Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("guarded_provider_probe",
    root / "tests/fixtures/windows_user_trial_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
guard = probe.NetworkGuard(log)
sys.addaudithook(guard.audit)
sys.path.insert(0, str(root))
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
import pytest
with probe.control_socketpairs(guard) if sys.platform == "win32" else nullcontext():
    status = pytest.main(["-q", "-p", "no:cacheprovider",
        str(root / "tests/test_windows_user_trial_probe.py"),
        "-k", "scripted_provider or legacy_provider_constructor"])
assert status == 0
assert guard.verify()["unknownAttempts"] == 0
assert not log.exists()
'''
    child = subprocess.run([sys.executable, "-I", "-B", "-c", script,
        str(ROOT), str(tmp_path / "network.jsonl")], capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stdout + child.stderr
    assert "19 passed" in child.stdout
    assert not (tmp_path / "network.jsonl").exists()


@pytest.mark.parametrize("tail", [False, True])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("branch", ["stop", "resume", "first", "tool_result"])
def test_scripted_provider_controls_select_latest_delivered_input(tmp_path, monkeypatch, tail, structured, branch):
    import asyncio
    import sqlite3
    from importlib.metadata import distribution
    from amplifier_core.message_models import ChatRequest, Message, ToolSpec, TextBlock
    from amplifier_web.access_mode import keys, read_mode
    module = types.ModuleType("trial_stop_provider")
    exec(compile(probe.PROVIDER, "<scripted-provider>", "exec"), module.__dict__)
    workspace = tmp_path / "workspace café 日本語"
    workspace.mkdir()
    sid = "synthetic-stop"
    # Real worker-side standing-policy read, no Full-access function double.
    with sqlite3.connect(tmp_path / "app.sqlite3") as db:
        db.execute("CREATE TABLE commands (id TEXT PRIMARY KEY, receipt TEXT)")
        db.execute("INSERT INTO commands VALUES (?,?)", (
            keys(workspace, sid)["workspace"], json.dumps({"result": {"mode": "full", "revision": 1}})))
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    assert read_mode(tmp_path, workspace, sid) == "full"
    provider = module.Provider({name: str(path) for name, path in {
        "output": workspace / "trial.txt", "calls": tmp_path / "calls.jsonl",
        "blocked": tmp_path / "blocked.json"}.items()}, coordinator=types.SimpleNamespace(session_id=sid))
    # Load this dependency's inspected pure provenance module directly, avoiding
    # package __init__ side effects. Do not replace InputContext or Core Message.
    source = distribution("amplifier-module-loop-live").locate_file("amplifier_module_loop_live/provenance.py")
    spec = importlib.util.spec_from_file_location("trial_input_provenance", source)
    provenance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provenance)
    def user(text, identity):
        saved = []
        async def add(message):
            saved.append(message)
        command = types.SimpleNamespace(kind="user", id=identity, source="user",
                                        call_id=None, target_generation_id=None)
        context = provenance.InputContext(types.SimpleNamespace(add_message=add), text, command)
        immediate(context.add_message({"role": "user", "content": text}))
        assert saved[0]["metadata"]["amplifier_input"]["id"] == identity
        if structured:
            saved[0]["content"] = [TextBlock(text=text)]
        return Message(**saved[0])
    messages = [
        user("FIRST_TRIAL café 日本語", "first"),
        Message(role="tool", tool_call_id="trial-write", content="Saved"),
        Message(role="assistant", content=probe.MARKER),
        user("WAIT_FOR_STOP", "stop"),
    ]
    if branch == "resume":
        messages.append(user("RESUME_TRIAL", "resume"))  # old WAIT must not replay
    elif branch == "first":
        messages = [messages[0]]
    elif branch == "tool_result":
        messages.pop()  # first turn's real tool result must still finish normally
    if tail:
        # The maintained loop appends provider:request injections as user-role,
        # ephemeral request-view messages AFTER the actual admitted input.
        messages.append(Message(role="user", content="Synthetic app guidance" if branch == "stop"
                                else "Synthetic reminder WAIT_FOR_STOP RESUME_TRIAL",
                                metadata={"ephemeral": True, "reminder_placement": "tail"}))
    request = ChatRequest(messages=messages, tools=[ToolSpec(name="write_file", parameters={})])
    spawned, waiting = [], []
    marker = object()
    def spawn(argv, **kwargs):
        assert argv == [sys.executable, "-I", "-c", "import time; time.sleep(180)"]
        assert kwargs == {"stdin": -3, "stdout": -3, "stderr": -3}
        spawned.append(True)
        return types.SimpleNamespace(pid=1234)  # Never create or signal a process.
    class Pending:
        def __await__(self):
            waiting.append(True)
            yield marker
    module.subprocess = types.SimpleNamespace(Popen=spawn, DEVNULL=-3)
    module.asyncio = types.SimpleNamespace(Event=lambda: types.SimpleNamespace(wait=lambda: Pending()))
    operation = provider.complete(request)
    try:
        if branch == "stop":
            assert operation.send(None) is marker
            assert spawned == waiting == [True]
            assert json.loads((tmp_path / "blocked.json").read_text(encoding="utf-8")) == {"pid": 1234}
            cancelled = asyncio.CancelledError("synthetic stop")
            with pytest.raises(asyncio.CancelledError) as caught:
                operation.throw(cancelled)
            assert caught.value is cancelled
        else:
            response = immediate(operation)
            if branch == "first":
                assert len(response.tool_calls) == 1
                assert response.tool_calls[0].model_dump() == {
                    "id": "trial-write", "name": "write_file",
                    "arguments": {"file_path": str(workspace / "trial.txt"), "content": probe.MARKER + "\n"}}
            else:
                assert response.tool_calls is None
                assert response.content[0].text == (probe.RESUMED if branch == "resume" else probe.MARKER)
            assert not spawned and not waiting and not (tmp_path / "blocked.json").exists()
    finally:
        operation.close()
    assert len((tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def replay_history(output):
    """Synthetic canonical rows, including the actual tool call/result shape."""
    def user(text, identity):
        return {"role": "user", "content": text, "metadata": {
            "amplifier_input": {"version": 1, "kind": "user", "id": identity, "source": "user"}}}
    first = [
        user("FIRST_TRIAL café 日本語", "synthetic-first"),
        {"role": "assistant", "content": [], "tool_calls": [{
            "id": "trial-write", "name": "write_file",
            "arguments": {"file_path": str(output), "content": probe.MARKER + "\n"}}]},
        {"role": "tool", "tool_call_id": "trial-write",
         "content": json.dumps({"file_path": str(output), "bytes": len((probe.MARKER + "\n").encode())})},
        {"role": "assistant", "content": [{"type": "text", "text": probe.MARKER}]},
        user("WAIT_FOR_STOP", "synthetic-stop"),
    ]
    resume = [user("RESUME_TRIAL", "synthetic-resume"),
              {"role": "assistant", "content": [{"type": "text", "text": probe.RESUMED}]}]
    return first, resume


def real_trial_writer(workspace):
    from amplifier_module_tool_filesystem.write import WriteTool
    async def emit(*args, **kwargs):
        pass
    # Real installed tool/path policy/writer; this hook sink has no I/O/effects.
    return WriteTool({"working_dir": str(workspace), "allowed_write_paths": [str(workspace)]},
                     types.SimpleNamespace(hooks=types.SimpleNamespace(emit=emit)))


@pytest.mark.parametrize("extra_effect", [False, True, "shutdown"])
def test_non_replay_oracle_detects_extra_actual_identical_byte_tool_write_without_new_user(
        tmp_path, extra_effect):
    workspace = tmp_path / "workspace café 日本語"
    output = workspace / "trial.txt"
    writer = real_trial_writer(workspace)
    arguments = {"file_path": str(output), "content": probe.MARKER + "\n"}
    first, resume = replay_history(output)
    # First-host observation encloses the REAL WriteTool.execute and its actual
    # byte writer. The observer never generates a substitute tool result/write.
    with probe.observe_tool_writes(tmp_path, output) as first_observer:
        first_result = immediate(writer.execute(arguments))
    assert first_result.success and first_observer == {"failed": False}
    before_bytes = output.read_bytes()
    baseline = probe.non_replay_baseline(tmp_path, first)
    assert len(baseline["effects"]) == 1
    probe.write_json(tmp_path / "baseline.json", baseline)
    # New observer instance, same persisted ledger/baseline: model fresh host
    # lifetime rather than resetting counters or using provider-call counts.
    with probe.observe_tool_writes(tmp_path, output) as second_observer:
        if extra_effect is True:
            second_result = immediate(writer.execute(arguments))
            assert second_result.success and second_result.model_dump() == first_result.model_dump()
    assert second_observer == {"failed": False}
    after = first + resume
    # The old oracle STILL passes even after an extra actual same-byte write.
    assert output.read_bytes() == before_bytes == (probe.MARKER + "\n").encode()
    assert sum(row["role"] == "user" and "FIRST_TRIAL" in str(row["content"]) for row in after) == 1
    assert any(row["role"] == "assistant" and probe.RESUMED in str(row["content"]) for row in after)
    persisted = json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))
    if extra_effect == "shutdown":
        probe.assert_resume_without_replay(tmp_path, persisted, after)  # Parked observation passes.
        with probe.observe_tool_writes(tmp_path, output) as shutdown_observer:
            assert immediate(writer.execute(arguments)).success  # Late real write, no new input.
        assert shutdown_observer == {"failed": False} and output.read_bytes() == before_bytes
    if extra_effect:
        assert len(probe.tool_effects(tmp_path)) == 2
        with pytest.raises(AssertionError):
            probe.assert_resume_without_replay(tmp_path, persisted, after)
    else:
        probe.assert_resume_without_replay(tmp_path, persisted, after)
    # Only bounded size/hash facts survive in the private ledger/checkpoint.
    assert str(output) not in json.dumps(persisted) and probe.MARKER not in json.dumps(persisted)


@pytest.mark.parametrize("duplicate", ["tool", "assistant_call", "assistant_result",
                                     "resume_assistant", "resume_user", "first_user"])
def test_non_replay_oracle_detects_duplicate_history_effects_with_unchanged_file_and_ledger(
        tmp_path, duplicate):
    output = tmp_path / "workspace" / "trial.txt"
    with probe.observe_tool_writes(tmp_path, output):
        assert immediate(real_trial_writer(output.parent).execute({
            "file_path": str(output), "content": probe.MARKER + "\n"})).success
    first, resume = replay_history(output)
    baseline = probe.non_replay_baseline(tmp_path, first)
    after = first + resume
    index = {"tool": 2, "assistant_call": 1, "assistant_result": 3,
             "resume_assistant": 6, "resume_user": 5, "first_user": 0}[duplicate]
    after.append(copy.deepcopy(after[index]))
    assert len(probe.tool_effects(tmp_path)) == 1 and output.read_bytes() == (probe.MARKER + "\n").encode()
    with pytest.raises(AssertionError):
        probe.assert_resume_without_replay(tmp_path, baseline, after)


@pytest.mark.parametrize("fault", ["missing-ledger", "empty-ledger", "malformed-ledger",
                                  "wrong-hash", "oversized-ledger", "changed-tool-content",
                                  "missing-tool", "extra-assistant"])
def test_non_replay_oracle_never_accepts_incomplete_or_changed_evidence(tmp_path, fault):
    output = tmp_path / "workspace" / "trial.txt"
    with probe.observe_tool_writes(tmp_path, output):
        assert immediate(real_trial_writer(output.parent).execute({
            "file_path": str(output), "content": probe.MARKER + "\n"})).success
    first, resume = replay_history(output)
    baseline, after = probe.non_replay_baseline(tmp_path, first), copy.deepcopy(first + resume)
    path = tmp_path / "tool-effects.jsonl"
    if fault == "missing-ledger":
        path.unlink()
    elif fault == "empty-ledger":
        path.write_bytes(b"")
    elif fault == "malformed-ledger":
        path.write_bytes(b"{")
    elif fault == "wrong-hash":
        path.write_text(json.dumps({"bytes": 1, "sha256": "a"*64}) + "\n", encoding="utf-8")
    elif fault == "oversized-ledger":
        path.write_bytes(b"x" * 4097)
    elif fault == "changed-tool-content":
        after[2]["content"] = "Synthetic changed result"
    elif fault == "missing-tool":
        del after[2]
    elif fault == "extra-assistant":
        after.append({"role": "assistant", "content": "Synthetic extra effect"})
    with pytest.raises((AssertionError, OSError, ValueError)):
        probe.assert_resume_without_replay(tmp_path, baseline, after)


def test_write_effect_observer_does_not_count_denied_tool_intents(tmp_path):
    output = tmp_path / "workspace" / "trial.txt"
    writer = real_trial_writer(output.parent)
    writer.denied_write_paths = [str(output.parent)]
    with probe.observe_tool_writes(tmp_path, output) as observation:
        result = immediate(writer.execute({"file_path": str(output), "content": probe.MARKER + "\n"}))
    assert result.success is False and observation == {"failed": False}
    assert not output.exists() and not (tmp_path / "tool-effects.jsonl").exists()
    with pytest.raises(OSError):
        probe.non_replay_baseline(tmp_path, replay_history(output)[0])


@pytest.mark.parametrize("fault", ["writer-permission", "writer-cancel", "ledger", "readback"])
def test_write_effect_observer_preserves_real_tool_result_exception_and_cancel(
        tmp_path, monkeypatch, fault):
    import asyncio
    import amplifier_module_tool_filesystem.write as implementation
    output = tmp_path / "workspace" / "trial.txt"
    writer = real_trial_writer(output.parent)
    arguments = {"file_path": str(output), "content": probe.MARKER + "\n"}
    original_writer, original_open = implementation.write_text_preserving, Path.open
    original_error = asyncio.CancelledError("synthetic cancel") if fault == "writer-cancel" else PermissionError(13, "synthetic refusal")
    if fault.startswith("writer-"):
        def denied(path, data):
            assert path == output
            raise original_error
        monkeypatch.setattr(Path, "write_bytes", denied)
        if fault == "writer-permission":
            unobserved = immediate(writer.execute(arguments))
        with probe.observe_tool_writes(tmp_path, output) as observation:
            if fault == "writer-cancel":
                with pytest.raises(asyncio.CancelledError) as caught:
                    immediate(writer.execute(arguments))
                assert caught.value is original_error
            else:
                observed = immediate(writer.execute(arguments))
                assert observed.model_dump() == unobserved.model_dump() and observed.success is False
        assert observation == {"failed": False}
    else:
        def failed_open(path, mode="r", *args, **kwargs):
            if (fault == "ledger" and path.name == "tool-effects.jsonl" or
                    fault == "readback" and path == output and mode == "rb"):
                raise OSError(28, "synthetic diagnostic I/O")
            return original_open(path, mode, *args, **kwargs)
        monkeypatch.setattr(Path, "open", failed_open)
        with probe.observe_tool_writes(tmp_path, output) as observation:
            observed = immediate(writer.execute(arguments))
        assert observed.success is True and observation == {"failed": True}
        with original_open(output, "rb") as stream:
            assert stream.read() == (probe.MARKER + "\n").encode()
    assert implementation.write_text_preserving is original_writer
    assert not (tmp_path / "tool-effects.jsonl").exists()


def test_write_effect_diagnostic_failure_invalidates_worker_only_after_cleanup(tmp_path, monkeypatch):
    import amplifier_web
    import amplifier_module_tool_filesystem.write as writer_module
    implementation = types.ModuleType("amplifier_web.runtime_worker")
    original_writer, original_publish, original_open = writer_module.write_text_preserving, lambda event: None, Path.open
    implementation.publish, implementation.configure_protocol = original_publish, lambda: None
    output = tmp_path / "workspace café 日本語" / "trial.txt"
    results, cleaned = [], []
    class Worker:
        async def start(self):
            pass
        async def run(self):
            try:
                result = await real_trial_writer(output.parent).execute({
                    "file_path": str(output), "content": probe.MARKER + "\n"})
                assert result.success is True
                results.append(result)
            finally:
                cleaned.append(True)
    implementation.Worker = Worker
    monkeypatch.setitem(sys.modules, implementation.__name__, implementation)
    monkeypatch.setattr(amplifier_web, "runtime_worker", implementation, raising=False)
    monkeypatch.setattr(probe, "require_ci", lambda: None)
    monkeypatch.setattr(probe.sys, "addaudithook", lambda hook: None)
    monkeypatch.setattr(probe, "NetworkGuard", lambda log: types.SimpleNamespace(
        verify=lambda: {}, audit=lambda event, args: None))
    monkeypatch.setenv("WINDOWS_TRIAL_ROOT", str(tmp_path))
    def failed_ledger(path, *args, **kwargs):
        if path.name == "tool-effects.jsonl":
            raise OSError(28, "synthetic ledger failure")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", failed_ledger)
    with pytest.raises(AssertionError):
        immediate(probe.worker(tmp_path / "candidate.whl"))
    assert len(results) == 1 and cleaned == [True]
    assert writer_module.write_text_preserving is original_writer and implementation.publish is original_publish
    facts = json.loads((tmp_path / "worker-facts.json").read_text(encoding="utf-8"))
    assert facts["diagnosticStatus"] == "FAIL"
    with original_open(output, "rb") as stream:
        assert stream.read() == (probe.MARKER + "\n").encode()


def test_non_replay_wiring_captures_first_host_baseline_after_stop_and_checks_before_pass():
    tree = ast.parse(Path(SPEC.origin).read_text(encoding="utf-8"))
    journey = ast.unparse(next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                              and node.name == "journey"))
    worker = ast.unparse(next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                             and node.name == "worker"))
    assert journey.index("baseline = non_replay_baseline(") > journey.index(
        "assert owners[-1].result.active_processes == 0")
    assert journey.index("assert_resume_without_replay(root, saved['nonReplay'], history)") < journey.index(
        "report['checks']['hostRestartResumeWithoutReplay'] = 'PASS'")
    assert journey.index("report['checks']['hostRestartResumeWithoutReplay'] = 'PASS'") > journey.index(
        "assert report['innerJobsSettled']")
    assert journey.count("assert_resume_without_replay(") == 2
    assert "'nonReplay': baseline" in journey
    assert "with observe_tool_writes(root," in worker
    assert "diagnostic_failed = diagnostic_failed or effects['failed']" in worker
    assert "assert not diagnostic_failed" in worker
    assert "await owner.run()" in worker and "guard.verify()" in worker
    # A private checkpoint/ledger is NOT a new public report field.
    value = report()
    value["hosts"] = [{"status": "FAIL", "stage": "host_restart_resume", "checks": {},
                      "nonReplay": {"private": "not-for-upload"}}]
    with pytest.raises(AssertionError):
        probe.validate_public(value)


def test_missing_module_cannot_silently_qualify(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[dependency-groups]\ndev=[]\n')
    with pytest.raises(AssertionError):
        probe.requirements(path)


def test_ci_gate_refuses_before_private_io_or_listener(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(AssertionError):
        probe.require_ci()


def test_sanitized_environment_has_no_inherited_credentials_or_pythonpath(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_SECRET", "do-not-inherit")
    monkeypatch.setenv("PYTHONPATH", "/unrelated")
    monkeypatch.setenv("HTTP_PROXY", "http://unrelated")
    before = dict(probe.os.environ)
    try:
        probe.sanitize(tmp_path)
        assert "SYNTHETIC_SECRET" not in probe.os.environ
        assert "PYTHONPATH" not in probe.os.environ and "HTTP_PROXY" not in probe.os.environ
        assert probe.os.environ["UV_OFFLINE"] == "1"
    finally:
        probe.os.environ.clear()
        probe.os.environ.update(before)


@pytest.mark.parametrize("event,args", [
    ("socket.bind", (None, ("127.0.0.1", 12345))),
    ("socket.connect", (None, ("127.0.0.1", 12345))),
    ("socket.connect", (None, ("192.0.2.1", 443))),
    ("socket.getaddrinfo", (b"private.invalid", 443)),
    ("socket.gethostbyname", ("private.invalid",)),
    ("socket.gethostbyaddr", ("192.0.2.1",)),
    ("socket.getnameinfo", (("192.0.2.1", 443), 0)),
    ("socket.sendto", (None, b"private", ("192.0.2.1", 443))),
])
def test_unknown_network_attempts_block_and_retain_no_arguments(tmp_path, event, args):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    with pytest.raises(PermissionError):
        guard.audit(event, args)
    assert guard.blocked == 1
    assert json.loads(guard.log.read_text()) == {"event": event, "blocked": True}
    with pytest.raises(AssertionError):
        guard.verify()


def test_unknown_network_receipt_attributes_fixed_case_phase_and_known_code(tmp_path):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    guard.case, guard.phase = "read_workspace_utf8", "call"
    def invoke():
        with pytest.raises(PermissionError):
            guard.audit("socket.gethostbyname", ("PRIVATE-HOST",))
    guard.codes[invoke.__code__] = "selected_test"
    invoke()
    invoke()
    result = guard.network_diagnostics()
    assert result["overflow"] == 0 and len(result["attempts"]) == 1
    entry = result["attempts"][0]
    assert {key: entry[key] for key in ("case", "phase", "event", "count", "source")} == {
        "case": "read_workspace_utf8", "phase": "call", "event": "socket.gethostbyname",
        "count": 2, "source": "selected_test"}
    assert len(entry["codeSha256"]) == 64 and type(entry["lineOffset"]) is int
    assert "PRIVATE-HOST" not in json.dumps(result)
    with pytest.raises(AssertionError):
        guard.verify()


def test_unknown_network_source_is_other_without_raw_name_or_fingerprint(tmp_path):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    def private_symbol():
        with pytest.raises(PermissionError):
            guard.audit("socket.getnameinfo", (("192.0.2.1", 12345), 0))
    private_symbol()
    assert guard.network_diagnostics() == {"attempts": [{
        "case": "outside", "phase": "outside", "event": "socket.getnameinfo",
        "source": "other", "count": 1}], "overflow": 0}


def test_network_diagnostic_limit_retains_overflow_count_and_still_fails(tmp_path):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    attempts = 0
    for case in probe.NATIVE_CONTROLS:
        for phase in ("setup", "call", "teardown"):
            guard.case, guard.phase = case, phase
            with pytest.raises(PermissionError):
                guard.audit("socket.gethostbyname", ("PRIVATE-HOST",))
            attempts += 1
    facts = guard.network_diagnostics()
    assert len(facts["attempts"]) == 32 and facts["overflow"] == attempts - 32
    assert sum(item["count"] for item in facts["attempts"]) + facts["overflow"] == guard.blocked
    with pytest.raises(AssertionError):
        guard.verify()


def test_exception_facts_do_not_stringify_private_classes_names_or_paths():
    class PrivateError(Exception):
        def __str__(self):
            raise AssertionError("diagnostic must not stringify")
    def private_helper():
        try:
            raise PermissionError(13, "PRIVATE-CREDENTIAL", "/private/path")
        except PermissionError as cause:
            cause.winerror = 32
            raise PrivateError("PRIVATE-CREDENTIAL") from cause
    try:
        private_helper()
    except Exception as exc:
        facts = probe.safe_exception(exc, {})
    assert facts == [{"class": "other", "frames": []},
                     {"class": "PermissionError", "frames": [], "errno": 13, "winerror": 32}]
    for forbidden in ("PrivateError", "private_helper", "/private/path", "PRIVATE-CREDENTIAL"):
        assert forbidden not in json.dumps(facts)


def test_exception_source_uses_exact_known_code_and_bounded_cause_chain():
    def boundary():
        try:
            raise ValueError("PRIVATE")
        except ValueError as exc:
            raise RuntimeError("PRIVATE") from exc
    try:
        boundary()
    except Exception as exc:
        facts = probe.safe_exception(exc, {boundary.__code__: "resource_read"})
    assert [row["class"] for row in facts] == ["RuntimeError", "ValueError"]
    assert all(row["frames"][0]["source"] == "resource_read" for row in facts)
    assert "PRIVATE" not in json.dumps(facts) and "boundary" not in json.dumps(facts)


@pytest.mark.parametrize("label,qualname", [
    ("private_directory_init", "PinnedDirectory.__init__"),
    ("private_directory_check", "PinnedDirectory._check"),
    ("private_api_init", "_API.__init__"),
    ("private_api_open", "_API.open"),
    ("private_acl_validate", "_API.validate_acl"),
    ("private_ace_check", "_check_ace"),
    ("private_path_local", "_local"),
    ("private_path_address", "address"),
])
def test_private_refusal_labels_bind_only_exact_loaded_code(label, qualname):
    from amplifier_web import windows_files as native
    function = native
    for part in qualname.split("."):
        function = getattr(function, part)
    codes = probe.diagnostic_codes()
    assert any(code is function.__code__ and name == label for code, name in codes.items())
    fact = probe.safe_frame(types.SimpleNamespace(f_code=function.__code__, f_lineno=10**8), codes)
    assert fact == {"source": label, "codeSha256": probe.digest(function.__code__.co_code),
                    "lineOffset": 10**6}
    # Even an equal code object with an unknown filename must not be disclosed.
    clone = function.__code__.replace(co_filename="/PRIVATE/unknown.py")
    assert probe.safe_frame(types.SimpleNamespace(f_code=clone), codes) == {"source": "other"}


@pytest.mark.parametrize("fault", ["drive", "filesystem", "volume", "foreign_ace"])
def test_actual_private_constructor_refusal_offsets_are_safe_with_inert_api(monkeypatch, fault):
    from amplifier_web import windows_files as native
    codes = probe.diagnostic_codes()  # Attest real loaded code before the API double.
    closed = []
    class API:
        def GetDriveTypeW(self, path):
            return 2 if fault == "drive" else 3
        def open(self, path, *, directory):
            return path
        def info(self, handle, path, *, directory):
            return types.SimpleNamespace(volume=2 if fault == "volume" and
                path != Path(path.anchor) else 1, index_high=0, index_low=1)
        def GetVolumeInformationByHandleW(self, handle, a, b, c, d, e, filesystem, size):
            filesystem.value = "FAT32" if fault == "filesystem" else "NTFS"
            return True
        def ok(self, value):
            assert value
        def validate_acl(self, handle, *, private):
            if fault == "foreign_ace":
                native._check_ace(0, 0, 0x100, "PRIVATE-SID", private=private)
        def close(self, handle):
            closed.append(handle)
    monkeypatch.setattr(native, "_API", API)
    with pytest.raises(PermissionError) as caught:
        native.PinnedDirectory(Path("/PRIVATE/state/updates"), owned_root=Path("/PRIVATE/state"), create=True)
    facts = probe.safe_exception(caught.value, codes)
    sources = [row["source"] for row in facts[0]["frames"]]
    assert sources == (["private_directory_init", "private_ace_check"] if fault == "foreign_ace"
                       else ["private_directory_init"])
    assert all(0 < row["lineOffset"] < 100 for row in facts[0]["frames"])
    assert bool(closed) == (fault != "drive")
    assert "PRIVATE" not in json.dumps(facts)
    value = diagnostic_public_model()
    value["nativeControls"]["selection"]["failureFacts"][0]["errors"] = facts
    probe.validate_public(value)


@pytest.mark.parametrize("fault", ["owner", "null_dacl"])
def test_actual_acl_refusal_offset_without_native_io_or_private_owner_output(fault):
    from amplifier_web import windows_files as native
    C = native.C
    api = native._API.__new__(native._API)
    api.sid = "PRIVATE-TOKEN-SID"
    released = []
    def security(handle, kind, fields, owner, group, dacl, sacl, descriptor):
        C.cast(owner, C.POINTER(native.PVOID))[0] = 1
        C.cast(dacl, C.POINTER(native.PVOID))[0] = 2 if fault == "owner" else None
        return 0
    def control(descriptor, output, revision):
        C.cast(output, C.POINTER(native.WORD))[0] = 4
        return True
    api.GetSecurityInfo, api.GetSecurityDescriptorControl = security, control
    api.sid_text = lambda pointer: "PRIVATE-FOREIGN-SID" if fault == "owner" else api.sid
    api.LocalFree = released.append
    with pytest.raises(PermissionError) as caught:
        api.validate_acl(1, private=True)
    facts = probe.safe_exception(caught.value, probe.diagnostic_codes())
    assert len(released) == 1
    assert facts == [{"class": "PermissionError", "frames": [{
        "source": "private_acl_validate",
        "codeSha256": probe.digest(native._API.validate_acl.__code__.co_code), "lineOffset": 11}]}]
    assert "PRIVATE" not in json.dumps(facts)


@pytest.mark.parametrize("fatal", [False, True])
def test_builtin_exception_groups_expand_bounded_children_without_private_content(fatal):
    child = KeyboardInterrupt("PRIVATE") if fatal else ValueError("PRIVATE")
    group = BaseExceptionGroup("PRIVATE-GROUP", [
        child, ExceptionGroup("PRIVATE-NESTED", [OSError(22, "PRIVATE", "/private/path")])])
    facts = probe.safe_exception(group, {})
    assert [row["class"] for row in facts] == [
        "BaseExceptionGroup" if fatal else "ExceptionGroup",
        "other" if fatal else "ValueError", "ExceptionGroup", "OSError"]
    assert facts[-1]["errno"] == 22
    assert all(not row["frames"] for row in facts)
    serialized = json.dumps(facts)
    assert "PRIVATE" not in serialized and "/private/path" not in serialized
    value = diagnostic_public_model()
    value["nativeControls"]["selection"]["failureFacts"][0]["errors"] = facts
    probe.validate_public(value)


def test_exception_group_budget_and_private_subclass_children_never_escape():
    class PrivateGroup(ExceptionGroup):
        @property
        def exceptions(self):
            raise AssertionError("Private group's attributes must not be inspected")
        def __str__(self):
            raise AssertionError("Diagnostic must not stringify")
    private = PrivateGroup("PRIVATE", [TypeError("PRIVATE")])
    assert probe.safe_exception(private, {}) == [{"class": "other", "frames": []}]
    group = ExceptionGroup("PRIVATE", [private, *[ValueError("PRIVATE") for _ in range(100)]])
    facts = probe.safe_exception(group, {})
    assert len(facts) == probe.EXCEPTION_RECORD_LIMIT == 6
    assert [row["class"] for row in facts] == ["ExceptionGroup", "other", *(["ValueError"] * 4)]
    value = diagnostic_public_model()
    errors = value["nativeControls"]["selection"]["failureFacts"][0]["errors"] = facts
    probe.validate_public(value)
    errors.append({"class": "ValueError", "frames": []})
    with pytest.raises(AssertionError):
        probe.validate_public(value)


@pytest.mark.parametrize("status,result,expected", [
    (503, {"code": "worker_startup_failed", "error": "PRIVATE"}, ("worker_startup_failed", True)),
    (504, {"code": "runtime_pending", "error": "PRIVATE"}, ("runtime_pending", True)),
    (400, {"code": "PRIVATE-CODE", "error": "PRIVATE", "state": "/private"}, ("other", True)),
    (200, {"result": {"text": "PRIVATE", "id": "PRIVATE"}}, ("none", False)),
    (200, {"error": "PRIVATE"}, ("none", True)),
])
def test_action_facts_retain_fixed_status_without_private_response(status, result, expected):
    facts = probe.action_fact("conversation.send", status, result)
    assert facts == {"action": "conversation.send", "httpStatus": status,
                     "hasError": expected[1], "code": expected[0]}
    assert "PRIVATE" not in json.dumps(facts) and "/private" not in json.dumps(facts)
    # The original error/status assertion remains decisive, including HTTP 200 errors.
    assert (status == 200 and not result.get("error")) == (status == 200 and not facts["hasError"])


@pytest.mark.parametrize("failed", [False, True])
def test_worker_event_observer_preserves_protocol_and_error_outcome(tmp_path, monkeypatch, failed):
    import amplifier_web
    implementation = types.ModuleType("amplifier_web.runtime_worker")
    forwarded, events = [], []
    original_publish = forwarded.append
    implementation.publish = original_publish
    implementation.configure_protocol = lambda: events.append("configured")
    body = {"type": "runtime.ready", "report": {"secret": "PRIVATE", "path": "/private"}}
    error = {"type": "runtime.error", "error": "PRIVATE", "errorType": "PrivateError"}
    class Worker:
        async def start(self):
            pass
        async def run(self):
            implementation.publish(body)
            if failed:
                try:
                    raise PermissionError(13, "PRIVATE", "/private")
                except PermissionError:
                    implementation.publish(error)
                    raise
    implementation.Worker = Worker
    monkeypatch.setitem(sys.modules, implementation.__name__, implementation)
    monkeypatch.setattr(amplifier_web, "runtime_worker", implementation, raising=False)
    monkeypatch.setattr(probe, "require_ci", lambda: None)
    monkeypatch.setattr(probe.sys, "addaudithook", lambda hook: None)
    monkeypatch.setattr(probe, "NetworkGuard", lambda log: types.SimpleNamespace(
        verify=lambda: {}, audit=lambda event, args: None))
    monkeypatch.setenv("WINDOWS_TRIAL_ROOT", str(tmp_path))
    if failed:
        with pytest.raises(PermissionError, match="PRIVATE"):
            immediate(probe.worker(tmp_path / "candidate.whl"))
    else:
        immediate(probe.worker(tmp_path / "candidate.whl"))
    assert events == ["configured"] and forwarded[0] is body
    assert implementation.publish is original_publish
    if failed:
        assert forwarded[1] is error
    facts = json.loads((tmp_path / "worker-facts.json").read_text(encoding="utf-8"))
    assert facts["events"] == ({"runtime.ready": 1, "runtime.error": 1} if failed else {"runtime.ready": 1})
    assert facts["errors"] == ([{"class": "PermissionError", "errno": 13, "frames": []}] if failed else [])
    assert facts["diagnosticStatus"] == "PASS"
    assert "PRIVATE" not in json.dumps(facts) and "/private" not in json.dumps(facts)
    value = report()
    value["hosts"] = [{"status": "FAIL" if failed else "PASS", "stage": "first_worker_turn",
                      "checks": {}, "workerFacts": facts}]
    probe.validate_public(value)


def test_unknown_worker_events_and_payload_symbols_do_not_enter_facts():
    facts = {"events": {}, "errors": []}
    probe.worker_event(facts, {"type": "PRIVATE", "error": "PRIVATE"})
    probe.worker_event(facts, {"type": "runtime.error", "error": "PRIVATE", "errorType": "PrivateError"})
    assert facts == {"events": {"runtime.error": 1}, "errors": []}


@pytest.mark.parametrize("outcome", ["return", "wrapped", "cancel"])
@pytest.mark.parametrize("diagnostic_fault", [None, "codes", "facts"])
def test_runtime_startup_observer_preserves_arguments_return_exception_and_cleanup(
        monkeypatch, outcome, diagnostic_fault):
    import asyncio
    from amplifier_web.runtime import RuntimeStartupError
    marker, session, emit, guard = object(), {"id": "PRIVATE"}, object(), object()
    seen, cleaned, observed_after_cleanup = [], [], []
    original = asyncio.CancelledError("PRIVATE") if outcome == "cancel" else RuntimeStartupError("PRIVATE")
    cause = PermissionError(13, "PRIVATE", "/private")
    class Base:
        def __init__(self, *args, **kwargs):
            seen.append(("init", args, kwargs))
        async def _start_locked(self, *args, **kwargs):
            seen.append(("start", args, kwargs))
            try:
                if outcome == "wrapped":
                    raise original from cause
                if outcome == "cancel":
                    raise original
                return marker
            finally:
                cleaned.append(True)
    runtime = probe.observed_runtime_type(Base)("PRIVATE", startup_timeout=90)
    real_codes, real_facts = probe.diagnostic_codes, probe.safe_exception
    def codes():
        observed_after_cleanup.append(bool(cleaned))
        if diagnostic_fault == "codes":
            raise OSError(28, "PRIVATE")
        return real_codes()
    def facts(exc, known):
        observed_after_cleanup.append(bool(cleaned))
        if diagnostic_fault == "facts":
            raise OSError(28, "PRIVATE")
        return real_facts(exc, known)
    monkeypatch.setattr(probe, "diagnostic_codes", codes)
    monkeypatch.setattr(probe, "safe_exception", facts)
    if outcome == "return":
        assert immediate(runtime._start_locked(session, emit, guard=guard, preserve_emit=True)) is marker
        assert runtime.trial_startup == {"status": "PASS", "errors": []}
    else:
        with pytest.raises(type(original)) as caught:
            immediate(runtime._start_locked(session, emit, guard=guard, preserve_emit=True))
        assert caught.value is original
        if diagnostic_fault:
            assert runtime.trial_startup == {"status": "FAIL", "errors": []}
        else:
            assert runtime.trial_startup["status"] == "RECORDED"
            assert [row["class"] for row in runtime.trial_startup["errors"]] == (
                ["RuntimeStartupError", "PermissionError"] if outcome == "wrapped" else ["other"])
        assert observed_after_cleanup and all(observed_after_cleanup)
    assert cleaned == [True]
    assert seen == [("init", ("PRIVATE",), {"startup_timeout": 90}),
                    ("start", (session, emit), {"guard": guard, "preserve_emit": True})]
    assert "PRIVATE" not in json.dumps(runtime.trial_startup)
    value = report()
    value["hosts"] = [{"status": "PASS" if outcome == "return" else "FAIL",
                      "stage": "first_worker_turn", "checks": {}, "runtimeStartup": runtime.trial_startup}]
    probe.validate_public(value)


def test_runtime_observer_uses_real_start_boundary_and_fixed_code_labels_without_io():
    from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
    runtime = probe.observed_runtime_type(RuntimeManager)(command=["PRIVATE"])
    # This actual source branch refuses BEFORE any task, disk, process or loop.
    with pytest.raises(RuntimeStartupError) as caught:
        immediate(runtime._start_locked({"bundleReplacement": {"deferred": True}}, None))
    assert runtime.workers == {} and runtime._preparations == {}
    assert runtime.trial_startup["status"] == "RECORDED"
    assert [row["class"] for row in runtime.trial_startup["errors"]] == ["RuntimeStartupError"]
    assert [frame["source"] for frame in runtime.trial_startup["errors"][0]["frames"]] == ["runtime_start_locked"]
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("outcome", ["return", "permission", "cancel"])
@pytest.mark.parametrize("diagnostic_fault", [None, "observe", "write"])
def test_worker_bootstrap_boundary_retains_failure_without_replacing_original(
        tmp_path, monkeypatch, outcome, diagnostic_fault):
    import asyncio
    marker = object()
    original = asyncio.CancelledError("PRIVATE") if outcome == "cancel" else PermissionError(13, "PRIVATE", "/private")
    cleaned = []
    async def bootstrap(wheel):
        assert wheel is marker
        try:
            if outcome != "return":
                raise original
            return marker
        finally:
            cleaned.append(True)
    monkeypatch.setattr(probe, "worker", bootstrap)
    monkeypatch.setenv("WINDOWS_TRIAL_ROOT", str(tmp_path))
    if diagnostic_fault:
        def fail(*args):
            assert cleaned == [True]
            raise OSError(28, "PRIVATE-DIAGNOSTIC", "/private")
        monkeypatch.setattr(probe, "safe_exception" if diagnostic_fault == "observe" else "write_json", fail)
    if outcome == "return":
        assert immediate(probe.worker_entry(marker)) is marker
    else:
        with pytest.raises(type(original)) as caught:
            immediate(probe.worker_entry(marker))
        assert caught.value is original
    assert cleaned == [True]
    path = tmp_path / "worker-facts.json"
    if outcome != "return" and diagnostic_fault is None:
        facts = json.loads(path.read_text(encoding="utf-8"))
        assert facts["diagnosticStatus"] == "FAIL" and facts["events"] == {}
        assert facts["errors"][0]["class"] == ("other" if outcome == "cancel" else "PermissionError")
        assert facts["errors"][0]["frames"][0]["source"] == "trial_worker"
        assert "PRIVATE" not in json.dumps(facts)
    else:
        assert not path.exists()


@pytest.mark.parametrize("fault", ["status", "message", "class", "frame", "extra", "empty",
                                  "pass-recorded", "pass-unobserved", "pass-failed"])
def test_runtime_startup_facts_reject_private_or_false_pass(fault):
    value = report()
    startup = {"status": "RECORDED", "errors": [{"class": "RuntimeStartupError", "frames": []}]}
    host = {"status": "FAIL", "stage": "first_worker_turn", "checks": {}, "runtimeStartup": startup}
    value["hosts"] = [host]
    probe.validate_public(value)
    if fault == "status":
        startup["status"] = "PRIVATE"
    elif fault == "message":
        startup["errors"][0]["message"] = "PRIVATE"
    elif fault == "class":
        startup["errors"][0]["class"] = "PrivateError"
    elif fault == "frame":
        startup["errors"][0]["frames"] = [{"source": "private_symbol"}]
    elif fault == "extra":
        startup["path"] = "/private"
    elif fault == "empty":
        startup["errors"] = []
    else:
        host["status"] = "PASS"
        if fault == "pass-unobserved":
            startup.update(status="NOT_OBSERVED", errors=[])
        elif fault == "pass-failed":
            startup.update(status="FAIL", errors=[])
    with pytest.raises((AssertionError, KeyError, TypeError)):
        probe.validate_public(value)
    source = ast.unparse(next(node for node in ast.parse(Path(SPEC.origin).read_text()).body
                             if isinstance(node, ast.AsyncFunctionDef) and node.name == "journey"))
    assert "assert report['runtimeStartup']['status'] == 'PASS'" in source


@pytest.mark.parametrize("fault", ["step", "action", "code", "status", "message", "event",
                                  "worker-message", "worker-error", "host-error"])
def test_first_turn_diagnostics_reject_unknown_or_private_fields(fault):
    value = report()
    host = {"status": "FAIL", "stage": "first_worker_turn", "checks": {},
            "turnStep": "conversation_send",
            "lastAction": probe.action_fact("conversation.send", 503, {"code": "worker_startup_failed"}),
            "errors": [{"class": "AssertionError", "frames": []}],
            "workerFacts": {"events": {"runtime.error": 1}, "errors": [{"class": "other", "frames": []}],
                            "diagnosticStatus": "PASS"}}
    value["hosts"] = [host]
    probe.validate_public(value)
    if fault == "step":
        host["turnStep"] = "PRIVATE"
    elif fault in {"action", "code", "status", "message"}:
        host["lastAction"][{"status": "httpStatus"}.get(fault, fault)] = "PRIVATE"
    elif fault == "event":
        host["workerFacts"]["events"]["PRIVATE"] = 1
    elif fault == "worker-message":
        host["workerFacts"]["message"] = "PRIVATE"
    elif fault == "worker-error":
        host["workerFacts"]["errors"][0]["frames"] = [{"source": "private_symbol"}]
    elif fault == "host-error":
        host["errors"][0]["message"] = "PRIVATE"
    with pytest.raises((AssertionError, KeyError, TypeError)):
        probe.validate_public(value)


@pytest.mark.parametrize("kind", ["runtime.ready", "runtime.error"])
@pytest.mark.parametrize("diagnostic_fault", ["observe", "write", "final_write"])
@pytest.mark.parametrize("publisher_outcome", ["return", "permission", "cancel",
                                              "worker_permission", "worker_cancel"])
def test_actual_worker_observer_fault_preserves_publisher_and_defers_failure_until_cleanup(
        tmp_path, monkeypatch, kind, diagnostic_fault, publisher_outcome):
    import asyncio
    import amplifier_web
    implementation = types.ModuleType("amplifier_web.runtime_worker")
    marker, forwarded, returns, cleaned, writes = object(), [], [], [], []
    original_error = (asyncio.CancelledError("PRIVATE-CANCEL") if publisher_outcome == "cancel"
                      else PermissionError(13, "PRIVATE-PUBLISHER", "/private"))
    original_worker_error = (asyncio.CancelledError("PRIVATE-WORKER-CANCEL")
                             if publisher_outcome == "worker_cancel" else
                             PermissionError(13, "PRIVATE-WORKER", "/private"))
    event = {"type": kind, "error": "PRIVATE", "report": {"secret": "PRIVATE"}}
    def original_publish(received):
        forwarded.append(received)
        if publisher_outcome in {"permission", "cancel"}:
            raise original_error
        return marker
    implementation.publish = original_publish
    implementation.configure_protocol = lambda: None
    class Worker:
        async def start(self):
            pass
        async def run(self):
            try:
                if kind == "runtime.error":
                    try:
                        raise original_worker_error
                    except BaseException:
                        returns.append(implementation.publish(event))
                else:
                    returns.append(implementation.publish(event))
                if publisher_outcome.startswith("worker_"):
                    raise original_worker_error
            finally:
                cleaned.append(True)
    implementation.Worker = Worker
    monkeypatch.setitem(sys.modules, implementation.__name__, implementation)
    monkeypatch.setattr(amplifier_web, "runtime_worker", implementation, raising=False)
    monkeypatch.setattr(probe, "require_ci", lambda: None)
    monkeypatch.setattr(probe.sys, "addaudithook", lambda hook: None)
    monkeypatch.setattr(probe, "NetworkGuard", lambda log: types.SimpleNamespace(
        verify=lambda: {}, audit=lambda event, args: None))
    monkeypatch.setenv("WINDOWS_TRIAL_ROOT", str(tmp_path))
    real_write = probe.write_json
    def write(path, facts):
        writes.append(facts["diagnosticStatus"])
        if diagnostic_fault == "write" or diagnostic_fault == "final_write" and cleaned:
            raise OSError(28, "PRIVATE-DIAGNOSTIC", "/private")
        real_write(path, facts)
    monkeypatch.setattr(probe, "write_json", write)
    if diagnostic_fault == "observe":
        def observe(*args):
            raise OSError(28, "PRIVATE-DIAGNOSTIC", "/private")
        monkeypatch.setattr(probe, "worker_event", observe)
    expected_error = original_worker_error if publisher_outcome.startswith("worker_") else original_error
    expected = AssertionError if publisher_outcome == "return" else type(expected_error)
    with pytest.raises(expected) as caught:
        immediate(probe.worker(tmp_path / "candidate.whl"))
    assert cleaned == [True] and len(forwarded) == 1 and forwarded[0] is event
    assert implementation.publish is original_publish
    if publisher_outcome == "return" or publisher_outcome.startswith("worker_"):
        assert returns == [marker]  # observer did not introduce a publish error
    if publisher_outcome != "return":
        assert caught.value is expected_error  # not the diagnostic OSError(28)
    if diagnostic_fault == "observe":
        assert writes == ["FAIL"]
    else:
        assert writes == ["PENDING", "FAIL" if diagnostic_fault == "write" else "PASS"]
    path = tmp_path / "worker-facts.json"
    if path.exists():
        facts = json.loads(path.read_text(encoding="utf-8"))
        assert facts["diagnosticStatus"] == ("FAIL" if diagnostic_fault == "observe" else "PENDING")
        assert "PRIVATE" not in json.dumps(facts)
        value = report()
        value["hosts"] = [{"status": "FAIL", "stage": "first_worker_turn", "checks": {},
                          "workerFacts": facts}]
        probe.validate_public(value)
        value["hosts"][0]["status"] = "PASS"
        with pytest.raises(AssertionError):
            probe.validate_public(value)
    else:
        assert diagnostic_fault == "write"
    completed = probe.completed_worker_facts(path)
    assert completed["diagnosticStatus"] == "FAIL"
    assert "PRIVATE" not in json.dumps(completed)


@pytest.mark.parametrize("status", ["PENDING", "FAIL", None, "PRIVATE"])
def test_missing_or_failed_worker_diagnostics_cannot_qualify(status):
    facts = {"events": {}, "errors": []}
    if status is not None:
        facts["diagnosticStatus"] = status
    value = report()
    value["hosts"] = [{"status": "PASS", "stage": "complete", "checks": {}, "workerFacts": facts}]
    with pytest.raises((AssertionError, KeyError)):
        probe.validate_public(value)
    journey = next(node for node in ast.parse(Path(SPEC.origin).read_text(encoding="utf-8")).body
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == "journey")
    source = ast.unparse(journey)
    assert "assert report['workerFacts']['diagnosticStatus'] == 'PASS'" in source


@pytest.mark.parametrize("raw", [None, b"{", b"\xff", b"[]",
                                  b'{"diagnosticStatus":[]}', b'{"diagnosticStatus":"PRIVATE"}'])
def test_completed_missing_or_malformed_worker_facts_are_fixed_failure(tmp_path, raw):
    path = tmp_path / "worker-facts.json"
    if raw is not None:
        path.write_bytes(raw)
    assert probe.completed_worker_facts(path) == {
        "events": {}, "errors": [], "diagnosticStatus": "FAIL"}


def test_first_turn_discriminators_keep_real_send_and_outcome_assertions():
    tree = ast.parse(Path(SPEC.origin).read_text(encoding="utf-8"))
    journey = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                   and node.name == "journey")
    steps = [ast.literal_eval(node.value) for node in ast.walk(journey)
             if isinstance(node, ast.Assign) and any(isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name) and target.value.id == "report"
                and isinstance(target.slice, ast.Constant) and target.slice.value == "turnStep"
                for target in node.targets)]
    assert set(steps) == probe.TURN_STEPS and len(steps) == len(probe.TURN_STEPS)
    source = ast.unparse(journey)
    for required in (
        "await action('conversation.send'",
        "await until(completed)",
        "assert calls[-1] == {'pid': row['process'].pid, 'access': 'full'}",
        "assert row['process'].pid != row['windows_owner'].job.identity.guard_pid",
        "assert output.read_text(encoding='utf-8') == MARKER + '\\n'",
        "assert any((m['role'] == 'tool' for m in history))",
        "await until(blocked.exists)",
        "kernel, handle = child_handle(json.loads(blocked.read_text())['pid'])",
        "await runtime.stop(sid)",
        "assert kernel.WaitForSingleObject(handle, 5000) == 0",
        "assert owners[-1].job.tree_empty and owners[-1].job.io_finalized",
        "assert owners[-1].result.active_processes == 0",
        "assert report['innerJobsSettled']",
    ):
        assert required in source
    assert "assert response.status == 200 and (not result.get('error'))" in source


def test_real_aiohttp_close_failure_is_retained_with_known_code_without_loop_or_listener():
    from aiohttp.test_utils import BaseTestServer
    from asyncio.base_events import BaseEventLoop
    async def cleanup():
        # Execute the real closed-loop check against inert state, not an event
        # loop or socket. This is a diagnostic model, not the measured CI cause.
        BaseEventLoop._check_closed(types.SimpleNamespace(_closed=True))
    server = types.SimpleNamespace(started=True, closed=False,
                                  runner=types.SimpleNamespace(cleanup=cleanup))
    try:
        immediate(BaseTestServer.close(server))
    except RuntimeError as exc:
        facts = probe.safe_exception(ExceptionGroup("PRIVATE", [exc]), probe.diagnostic_codes())
    else:
        pytest.fail("Cleanup failure must propagate")
    assert [row["class"] for row in facts] == ["ExceptionGroup", "RuntimeError"]
    assert {frame["source"] for frame in facts[1]["frames"]} == {
        "aiohttp_server_close", "asyncio_loop_check_closed"}
    assert server.closed is False
    assert "PRIVATE" not in json.dumps(facts) and "cleanup" not in json.dumps(facts)


def test_only_one_owned_reservation_and_exact_port_connect_are_permitted(tmp_path):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    guard.reserving = True
    guard.audit("socket.bind", (None, ("127.0.0.1", 0)))
    with pytest.raises(PermissionError):
        guard.audit("socket.bind", (None, ("127.0.0.1", 0)))
    guard.port = 12345
    guard.audit("socket.connect", (None, ("127.0.0.1", 12345)))


@pytest.mark.parametrize("fault", [None, "wrong-code", "wrong-module", "repeat", "unclosed", "enabled"])
def test_attested_ipv6_probe_model_is_blocked_not_allowed(tmp_path, monkeypatch, fault):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    sock = types.SimpleNamespace(family=socket.AF_INET6, fileno=lambda: -1)
    frame = types.SimpleNamespace(f_code=guard.probe_code,
        f_globals={"__name__": "urllib3.util.connection", "__file__": str(guard.probe_path)},
        f_locals={"host": "::1"})
    if fault == "wrong-code":
        frame.f_code = (lambda: None).__code__
    if fault == "wrong-module":
        frame.f_globals["__name__"] = "unrelated"
    if fault == "repeat":
        guard.probes = 1
    monkeypatch.setattr(probe.sys, "_getframe", lambda _: frame)
    with pytest.raises(PermissionError):
        guard.audit("socket.bind", (sock, ("::1", 0)))
    if fault in {"wrong-code", "wrong-module", "repeat"}:
        assert guard.blocked == 1
        return
    monkeypatch.setitem(probe.sys.modules, "urllib3.util.connection",
                        types.SimpleNamespace(HAS_IPV6=fault == "enabled"))
    if fault == "unclosed":
        sock.fileno = lambda: 42
    if fault:
        with pytest.raises(AssertionError):
            guard.verify()
    else:
        assert guard.verify() == {"blockedOptionalIpv6Probes": 1, "unknownAttempts": 0}
    assert not guard.log.exists()  # accepted BLOCKED probe, no real bind in this model


def test_exact_probe_body_closes_socket_after_blocked_capability_attempt():
    class Socket:
        closed = False
        def bind(self, address):
            assert address == ("::1", 0)
            raise PermissionError("blocked before any OS bind")
        def close(self):
            self.closed = True
    instance = Socket()
    namespace = {"socket": types.SimpleNamespace(has_ipv6=True, AF_INET6=10,
                                                socket=lambda family: instance)}
    exec(compile(probe.IPV6_PROBE, "<pure-probe-body-model>", "exec"), namespace)
    assert namespace["_has_ipv6"]("::1") is False
    assert instance.closed


def test_changed_capability_probe_source_fails_before_import(tmp_path, monkeypatch):
    path = tmp_path / "changed.py"
    path.write_text(probe.IPV6_PROBE.replace("sock.close()", "pass"))
    monkeypatch.setattr(probe.metadata, "distribution",
                        lambda _: types.SimpleNamespace(locate_file=lambda _: path))
    with pytest.raises(AssertionError):
        probe.NetworkGuard(tmp_path / "network.jsonl")


@pytest.mark.parametrize("fault", [None, "no-scope", "wrong-code", "wrong-module",
                                  "wrong-file", "wrong-socket", "wrong-address"])
@pytest.mark.parametrize("event", ["socket.bind", "socket.connect"])
def test_native_test_loop_socketpair_allowance_is_exact_and_scoped(tmp_path, monkeypatch, fault, event):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    guard.pair_depth = 0 if fault == "no-scope" else 1
    server = types.SimpleNamespace(getsockname=lambda: ("127.0.0.1", 12345))
    client = object()
    frame = types.SimpleNamespace(f_code=socket._fallback_socketpair.__code__,
        f_globals={"__name__": "socket", "__file__": socket.__file__},
        f_locals={"lsock": server, "csock": client}, f_back=None)
    if fault == "wrong-code":
        frame.f_code = (lambda: None).__code__
    elif fault == "wrong-module":
        frame.f_globals["__name__"] = "unrelated"
    elif fault == "wrong-file":
        frame.f_globals["__file__"] = str(tmp_path / "not-socket.py")
    stream = server if event == "socket.bind" else client
    address = ("127.0.0.1", 0 if event == "socket.bind" else 12345)
    if fault == "wrong-socket":
        stream = object()
    elif fault == "wrong-address":
        address = ("192.0.2.1", address[1])
    monkeypatch.setattr(probe.sys, "_getframe", lambda _: frame)
    if fault:
        with pytest.raises(PermissionError):
            guard.audit(event, (stream, address))
        assert guard.blocked == 1
    else:
        guard.audit(event, (stream, address))
        assert guard.verify()["unknownAttempts"] == 0
        assert guard.pair_binds == (1 if event == "socket.bind" else 0)
        assert not guard.log.exists()


def test_socketpair_scope_never_exempts_resolvers(tmp_path):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    guard.pair_depth = 1
    for event in ("socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"):
        with pytest.raises(PermissionError):
            guard.audit(event, ("private.invalid",))
    assert guard.blocked == 3
    with pytest.raises(AssertionError):
        guard.verify()


def test_socketpair_wrapper_closes_both_on_peer_mismatch(tmp_path, monkeypatch):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    closed = []
    left = types.SimpleNamespace(getsockname=lambda: ("127.0.0.1", 1),
        getpeername=lambda: ("127.0.0.1", 2), close=lambda: closed.append("left"))
    right = types.SimpleNamespace(getsockname=lambda: ("127.0.0.1", 2),
        getpeername=lambda: ("127.0.0.1", 3), close=lambda: closed.append("right"))
    original = lambda: (left, right)
    monkeypatch.setattr(socket, "socketpair", original)
    with probe.control_socketpairs(guard):
        with pytest.raises(AssertionError):
            socket.socketpair()
    assert closed == ["left", "right"] and guard.pair_depth == 0
    assert socket.socketpair is original


def immediate(coroutine):
    """Execute an await-free fake transport only; never create an event loop."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()
    pytest.fail("Fake transport unexpectedly waited")


@pytest.fixture
def tls_transport_model(tmp_path, monkeypatch):
    import aiohttp.test_utils as utilities
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    source = ROOT / "tests/test_browser_entry.py"
    code = next(part for part in compile(source.read_bytes(), str(source), "exec",
                                        dont_inherit=True).co_consts
                if isinstance(part, types.CodeType) and
                part.co_name == "test_native_unrelated_listener_and_proxy_never_receive_bearer")
    module = types.ModuleType("trial_browser_transport_model")
    module.__file__ = str(source)
    function = types.FunctionType(code, module.__dict__)
    module.__dict__[code.co_name] = function
    monkeypatch.setitem(probe.sys.modules, module.__name__, module)
    item = types.SimpleNamespace(nodeid="tests/" + probe.NATIVE_CONTROLS["auth_wrong_leaf_no_disclosure"],
        obj=function, callspec=types.SimpleNamespace(params={"mode": "wrong-leaf"}))
    handler_code = next(part for part in code.co_consts
                        if isinstance(part, types.CodeType) and part.co_name == "catcher")
    handler = types.FunctionType(handler_code, module.__dict__,
                                 closure=tuple(types.CellType([]) for _ in handler_code.co_freevars))
    sockets = []
    class Socket:
        def __init__(self, family, kind):
            self.family, self.address, self.closed = family, None, False
            sockets.append(self)
        def bind(self, address):
            guard.audit("socket.bind", (self, address))
            self.address = (address[0], address[1] or 25000 + len(sockets))
        def getsockname(self):
            return self.address
        def fileno(self):
            return -1 if self.closed else 77
        def close(self):
            self.closed = True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
    class Base:
        async def start_server(self, *args, **kwargs):
            if self.started:
                return
            self.stream = self.socket_factory(self.host, self.port or 0, socket.AF_INET)
            self.port = self.stream.getsockname()[1]
            self.started = True
        async def close(self):
            self.closed = True
            if hasattr(self, "stream"):
                self.stream.close()
    class Server(Base):
        def __init__(self, port=None, route_handler=handler):
            self.port, self.host, self.started, self.closed = port, "127.0.0.1", False, False
            def default_factory(host, port, family):
                stream = Socket(family, socket.SOCK_STREAM)
                try:
                    stream.bind((host, port))
                    return stream
                except BaseException:
                    stream.close()
                    raise
            self.socket_factory = default_factory
            self.app = types.SimpleNamespace(router=types.SimpleNamespace(
                routes=lambda: [types.SimpleNamespace(handler=route_handler)]))
    monkeypatch.setattr(utilities, "BaseTestServer", Base)
    monkeypatch.setattr(utilities, "TestServer", Server)
    monkeypatch.setattr(socket, "socket", Socket)
    return types.SimpleNamespace(guard=guard, target=ROOT, source=source, item=item,
                                 sockets=sockets, Server=Server, Base=Base)


@pytest.mark.parametrize("mode", ["wrong-leaf", "redirect"])
def test_exact_tls_negative_case_owns_only_its_live_sink_and_listener(tls_transport_model, mode):
    model = tls_transport_model
    guard = model.guard
    model.item.nodeid = "tests/" + probe.NATIVE_CONTROLS[
        "auth_wrong_leaf_no_disclosure" if mode == "wrong-leaf" else "auth_redirect_proxy_no_disclosure"]
    model.item.callspec.params["mode"] = mode
    original = model.Base.start_server
    with probe.native_tls_cases(guard, model.target, probe.digest(model.source.read_bytes())) as plugin:
        port = plugin.reserve(model.item)
        assert model.sockets[0].closed and guard.tls_binding is None
        hook = plugin.pytest_runtest_call(model.item)
        next(hook)
        sink, listener = model.Server(), model.Server(port)
        if mode == "redirect":
            redirect_code = next(part for part in model.item.obj.__code__.co_consts
                if isinstance(part, types.CodeType) and part.co_name == "redirect")
            redirect = types.FunctionType(redirect_code, model.item.obj.__globals__,
                closure=tuple(types.CellType(None) for _ in redirect_code.co_freevars))
            listener = model.Server(port, route_handler=redirect)
        immediate(sink.start_server())
        immediate(listener.start_server(ssl=object()))
        immediate(listener.start_server())  # cleanup registration is idempotent
        for endpoint in guard.tls_endpoints:
            guard.audit("socket.connect", (None, endpoint.stream.getsockname()))
        # These targets are reachable in CI: original received/trusted_received
        # test assertions, not a blocked transport, decide bearer disclosure.
        assert guard.tls_connects == 2 and len(guard.tls_endpoints) == 2
        with pytest.raises(PermissionError):
            guard.audit("socket.connect", (None, ("127.0.0.1", port + 100)))
        with pytest.raises(PermissionError):
            guard.audit("socket.gethostbyname", ("private.invalid",))
        immediate(listener.close())
        with pytest.raises(PermissionError):
            guard.audit("socket.connect", (None, ("127.0.0.1", port)))
        with pytest.raises(StopIteration):
            next(hook)
        assert guard.tls_scope.get() is None
        with pytest.raises(PermissionError):
            guard.audit("socket.connect", (None, sink.stream.getsockname()))
        immediate(sink.close())
    assert model.Base.start_server is original
    assert all(stream.closed for stream in model.sockets)


@pytest.mark.parametrize("fault", ["node", "parameter", "source-hash", "function-origin"])
def test_tls_negative_port_reservation_requires_exact_case(tls_transport_model, fault):
    model = tls_transport_model
    if fault == "node":
        model.item.nodeid = "tests/test_browser_entry.py::unselected"
    elif fault == "parameter":
        model.item.callspec.params["mode"] = "unselected"
    elif fault == "function-origin":
        model.item.obj = lambda: None
    fingerprint = "f"*64 if fault == "source-hash" else probe.digest(model.source.read_bytes())
    with pytest.raises(AssertionError):
        with probe.native_tls_cases(model.guard, model.target, fingerprint) as plugin:
            plugin.reserve(model.item)
    assert model.sockets == []


@pytest.mark.parametrize("fault", ["no-case", "wrong-port", "wrong-handler", "wrong-tls"])
def test_tls_negative_server_creation_rejects_unowned_transport(tls_transport_model, fault):
    model = tls_transport_model
    with probe.native_tls_cases(model.guard, model.target,
                               probe.digest(model.source.read_bytes())) as plugin:
        port = plugin.reserve(model.item)
        hook = plugin.pytest_runtest_call(model.item)
        if fault != "no-case":
            next(hook)
        server = model.Server(port if fault == "wrong-port" else None,
                              (lambda: None) if fault == "wrong-handler" else
                              model.Server().app.router.routes()[0].handler)
        with pytest.raises((AssertionError, PermissionError)):
            immediate(server.start_server(**({"ssl": object()} if fault == "wrong-tls" else {})))
        if fault != "no-case":
            with pytest.raises(StopIteration):
                next(hook)
    assert not model.guard.tls_endpoints and model.guard.tls_binding is None


@pytest.mark.parametrize("field", ["result", "tree", "io", "active"])
def test_unknown_or_failed_cleanup_never_permits_scratch_deletion(field):
    owner = types.SimpleNamespace(result=types.SimpleNamespace(active_processes=0),
        job=types.SimpleNamespace(tree_empty=True, io_finalized=True))
    assert probe.settled([owner])
    if field == "result":
        owner.result = None
    elif field == "active":
        owner.result.active_processes = 1
    else:
        setattr(owner.job, "tree_empty" if field == "tree" else "io_finalized", False)
    assert not probe.settled([owner])


def report():
    return {"candidate": {"head": "a"*40, "tree": "b"*40}, "status": "FAIL",
        "stage": "first", "checks": {}, "qualification": "public-entry-scripted-provider-not-device",
        "publicAcceptance": False, "runtimePreparation": "separate-private-project-api-plus-preinstalled-worker",
        "outerJobsSettled": False, "scratchRemoved": False, "python": [3, 13, 11], "windowsBuild": 26100}


@pytest.mark.parametrize("field,value", [
    ("path", "/private"), ("failureKind", "PrivateExceptionName"),
    ("stage", "/private"), ("publicAcceptance", True), ("errno", "private"),
    ("candidate", {"head": "private", "tree": "b"*40}),
])
def test_public_record_rejects_private_or_unqualified_fields(field, value):
    value_report = report()
    assert probe.validate_public(value_report) is value_report
    value_report[field] = value
    with pytest.raises((AssertionError, TypeError)):
        probe.validate_public(value_report)


def test_nested_child_record_does_not_bypass_public_allowlist():
    value = report()
    value["hosts"] = [{"status": "FAIL", "stage": "first", "checks": {}, "message": "private"}]
    with pytest.raises(AssertionError):
        probe.validate_public(value)
    value = report()
    value["checks"]["private marker"] = "PASS"
    with pytest.raises(AssertionError):
        probe.validate_public(value)


def test_failure_record_omits_prose_tracebacks_and_symbols():
    value = report()
    probe.failure(value, PermissionError("private path or credential"))
    assert value["failureKind"] == "permission"
    assert "private path" not in json.dumps(value)
    probe.validate_public(value)


def native_result(fault=None, *, results=None, properties=None):
    """Model pytest reports, never invoke native tests or their OS effects."""
    results = probe.NativeResults() if results is None else results
    nodes = list(results.node_labels)
    selected = nodes[:-1] if fault == "missing" else list(nodes)
    if fault == "unexpected":
        selected.append("private/location.py::private_function")
    if fault == "duplicate":
        selected.append(nodes[0])
    results.pytest_collection_finish(types.SimpleNamespace(
        items=[types.SimpleNamespace(nodeid=node) for node in selected]))
    for node in nodes:
        for phase in ("setup", "call", "teardown"):
            if fault == "no-call" and node == nodes[0] and phase == "call":
                continue
            outcome = "passed"
            if node == nodes[0] and phase == ("teardown" if fault == "teardown" else "call"):
                outcome = "skipped" if fault == "skipped" else (
                    "failed" if fault in {"failed", "teardown"} else "passed")
            row = types.SimpleNamespace(nodeid=node, when=phase, outcome=outcome)
            if isinstance(results, probe.StorageResults) and phase == "call":
                row.user_properties = (storage_properties if properties is None else properties)(results.node_labels[node])
            if fault == "xpass" and node == nodes[0] and phase == "call":
                row.wasxfail = "private reason"
            results.pytest_runtest_logreport(row)
    if fault == "collection-error":
        results.pytest_collectreport(types.SimpleNamespace(failed=True, skipped=False))
    return results.finish(2 if fault == "exit-code" else 0)


@pytest.mark.parametrize("fault", [None, "missing", "unexpected", "duplicate", "no-call",
                                  "skipped", "failed", "teardown", "xpass",
                                  "collection-error", "exit-code"])
def test_native_control_result_requires_every_exact_call_pass_with_no_skips(fault):
    result = native_result(fault)
    assert result["status"] == ("PASS" if fault is None else "FAIL")
    assert "private/location.py" not in json.dumps(result)
    assert "private_function" not in json.dumps(result) and "private reason" not in json.dumps(result)
    if fault is None:
        assert len(result["cases"]) == 18
        assert all(case == {"status": "PASS", "collected": 1, "callsPassed": 1,
                            "skips": 0, "failures": 0} for case in result["cases"].values())


def test_native_selection_exists_in_maintained_sources_and_includes_overlap():
    assert len(probe.NATIVE_CONTROLS) == len(set(probe.NATIVE_CONTROLS.values())) == 18
    assert probe.NATIVE_CONTROLS["resource_publish_overlap"].endswith(
        "::test_resource_publication_of_distinct_blob_during_held_projection")
    assert set(probe.NATIVE_FILES) == {"conftest.py", "test_service.py",
        "test_runtime_updates.py", "test_windows_auth.py", "test_runtime.py",
        "test_optional_controller_availability.py", "test_windows_read_files.py", "test_browser_entry.py"}
    for node in probe.NATIVE_CONTROLS.values():
        filename, function = node.split("::")
        name = function.split("[")[0]
        tree = ast.parse((ROOT / "tests" / filename).read_text(encoding="utf-8"))
        matches = [item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and item.name == name]
        assert len(matches) == 1
        if "[" in function:
            parameter, values = (("forced", [False, True]) if function.endswith(("[False]", "[True]"))
                                 else ("mode", ["wrong-leaf", "redirect"]))
            assert function.endswith(("[False]", "[True]", "[pyloop-wrong-leaf]", "[pyloop-redirect]"))
            assert any(isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "parametrize" and isinstance(dec.args[0], ast.Constant)
                and dec.args[0].value == parameter and ast.literal_eval(dec.args[1]) == values
                for dec in matches[0].decorator_list)


def test_native_control_staging_copies_tests_only_not_checkout_production(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "private_directory",
                        lambda path, root: path.mkdir(parents=True, exist_ok=False))
    target, hashes = probe.stage_controls(tmp_path, ROOT / "tests")
    assert set(hashes) == set(probe.NATIVE_FILES)
    assert set(path.name for path in target.iterdir()) == {"tests"}
    assert not (target / "amplifier_web").exists()
    for filename, expected in hashes.items():
        raw = (target / "tests" / filename).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected
        assert raw == (ROOT / "tests" / filename).read_bytes()


@pytest.mark.parametrize("regression", [None, "legacy-ids", "legacy-mode", "legacy-plugin-order"])
def test_actual_native_collection_with_declared_plugins_and_tls_mode_binding(tmp_path, monkeypatch, regression):
    """Collect real rewritten tests, but execute no test/fixture OS effects.

    The local subprocess uses this checkout explicitly: this regression proves
    collection/parameter binding, NOT installed-wheel or native acceptance.
    Only the port reservation used to validate actual collected TLS items is a
    pure fake. The NetworkGuard blocks every real socket/DNS attempt.
    """
    monkeypatch.setattr(probe, "private_directory",
                        lambda path, root: path.mkdir(parents=True, exist_ok=False))
    target, hashes = probe.stage_controls(tmp_path, ROOT / "tests")
    receipt = tmp_path / "collection.json"
    script = r'''
import importlib, importlib.util, json, os, pathlib, socket, sys
root, target, receipt = map(pathlib.Path, sys.argv[1:4])
regression = sys.argv[4]
spec = importlib.util.spec_from_file_location("collection_probe",
    root / "tests/fixtures/windows_user_trial_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
if regression == "legacy-ids":
    # Reproduce the original selection defect only in this child, no file edit.
    probe.NATIVE_CONTROLS = {label: node.replace("[pyloop-", "[")
                            for label, node in probe.NATIVE_CONTROLS.items()}
elif regression == "legacy-mode":
    # Reproduce the old combined-suffix comparison on actual collected items.
    probe.TLS_NEGATIVE_MODES = {node: node.rsplit("[", 1)[1][:-1]
                                for node in probe.TLS_NEGATIVE_MODES}
guard = probe.NetworkGuard(receipt.with_suffix(".network.jsonl"))
sys.addaudithook(guard.audit)
# Explicit local clean-source selection, never inherited PYTHONPATH.
sys.path.insert(0, str(root))
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import pytest
observed, validated = [], []
class Collection:
    def pytest_collection_finish(self, session):
        observed.extend(item.nodeid for item in session.items)
        expected = ["tests/" + node for node in probe.NATIVE_CONTROLS.values()]
        assert sorted(observed) == sorted(expected) and len(set(observed)) == 18
        class Reservation:
            def __init__(self, family, kind):
                assert family == socket.AF_INET and kind == socket.SOCK_STREAM
                self.closed = False
            def bind(self, address):
                guard.audit("socket.bind", (self, address))
                assert address == ("127.0.0.1", 0)
            def getsockname(self): return ("127.0.0.1", 25000)
            def close(self): self.closed = True
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        original = socket.socket
        socket.socket = Reservation
        try:
            for item in session.items:
                if item.nodeid in probe.TLS_NEGATIVE_MODES:
                    chosen = item._fixtureinfo.name2fixturedefs["unused_tcp_port"][-1]
                    assert getattr(chosen.func, "__self__", None) is controls, chosen.func.__module__
                    assert controls.reserve(item) == 25000
                    validated.append({"node": item.nodeid, "mode": item.callspec.params["mode"]})
        finally:
            socket.socket = original
    def pytest_runtest_protocol(self, item, nextitem):
        raise AssertionError("Collection regression must never execute a test")
with probe.native_tls_cases(guard, target,
    probe.digest((target / "tests/test_browser_entry.py").read_bytes())) as controls:
    declared = [] if regression == "legacy-plugin-order" else [
        importlib.import_module(name) for name in ("pytest_asyncio.plugin", "aiohttp.pytest_plugin")]
    result = pytest.main(["-q", "--collect-only", "-p", "no:cacheprovider",
        "-p", "pytest_asyncio.plugin", "-p", "aiohttp.pytest_plugin",
        "-o", "asyncio_mode=auto", "--rootdir", str(target),
        "--confcutdir", str(target),
        *[str(target / "tests" / node) for node in probe.NATIVE_CONTROLS.values()]],
        plugins=[*declared, Collection(), controls])
network = guard.verify()
assert result == 0 and len(observed) == 18 and len(validated) == 2
assert {row["node"]: row["mode"] for row in validated} == probe.TLS_NEGATIVE_MODES
receipt.write_text(json.dumps({"collected": len(observed), "tls": validated,
    "unknownAttempts": network["unknownAttempts"], "testBodiesRun": 0}), encoding="utf-8")
'''
    child = subprocess.run([sys.executable, "-I", "-B", "-c", script,
        str(ROOT), str(target), str(receipt), regression or ""],
        capture_output=True, text=True, timeout=30)
    if regression:
        assert child.returncode != 0 and not receipt.exists()
        assert ("not found" if regression == "legacy-ids" else "INTERNALERROR") in child.stdout + child.stderr
        return
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(receipt.read_text(encoding="utf-8"))
    assert result == {"collected": 18, "tls": [
        {"node": "tests/" + probe.NATIVE_CONTROLS["auth_wrong_leaf_no_disclosure"], "mode": "wrong-leaf"},
        {"node": "tests/" + probe.NATIVE_CONTROLS["auth_redirect_proxy_no_disclosure"], "mode": "redirect"},
    ], "unknownAttempts": 0, "testBodiesRun": 0}
    assert set(hashes) == set(probe.NATIVE_FILES)


def test_native_control_public_selection_and_source_hashes_are_allowlisted():
    result = report()
    result["nativeControls"] = {"status": "PASS", "stage": "complete", "checks": {
        "maintainedNativePrerequisites": "PASS"}, "selection": native_result(),
        "testSources": {name: "e"*64 for name in probe.NATIVE_FILES},
        "network": {"blockedOptionalIpv6Probes": 1, "unknownAttempts": 0,
                    "infrastructureSocketpairBinds": 3, "tlsNegativeReservations": 2,
                    "tlsNegativeListeners": 4, "tlsNegativeConnects": 2}}
    probe.validate_public(result)
    changed = copy.deepcopy(result)
    changed["nativeControls"]["selection"]["cases"]["private_function"] = {}
    with pytest.raises(AssertionError):
        probe.validate_public(changed)
    changed = copy.deepcopy(result)
    changed["nativeControls"]["testSources"]["private/path.py"] = "e"*64
    with pytest.raises(AssertionError):
        probe.validate_public(changed)
    changed = copy.deepcopy(result)
    changed["nativeControls"]["selection"]["cases"]["runtime_forced_cleanup"]["skips"] = 1
    with pytest.raises(AssertionError):
        probe.validate_public(changed)


def test_actual_pytest_reports_capture_safe_failure_per_phase_without_replacing_outcome(tmp_path):
    fixture = tmp_path / "test_synthetic.py"
    fixture.write_text('''
import pytest, sys
def attempt():
    # Synthetic audit event only: never invoke a DNS resolver or socket.
    with pytest.raises(PermissionError):
        sys.audit("socket.gethostbyname", "PRIVATE-HOST")
@pytest.fixture
def setup_denied(request):
    request.addfinalizer(attempt)
    attempt()
    raise PermissionError(13, "PRIVATE-MESSAGE", "/private/path")
@pytest.fixture
def teardown_denied():
    attempt()
    yield
    attempt()
    raise OSError(22, "PRIVATE-MESSAGE", "/private/path")
def test_setup(setup_denied):
    pass
def test_call():
    attempt()
    raise ValueError("PRIVATE-MESSAGE")
def test_teardown(teardown_denied):
    attempt()
@pytest.fixture
def grouped_cleanup(request):
    def first():
        raise OSError(22, "PRIVATE-MESSAGE", "/private/path")
    def second():
        raise ExceptionGroup("PRIVATE-GROUP", [
            ValueError("PRIVATE-MESSAGE"), RuntimeError("PRIVATE-MESSAGE")])
    request.addfinalizer(first)
    request.addfinalizer(second)
def test_group(grouped_cleanup):
    pass
''', encoding="utf-8")
    result_path = tmp_path / "safe.json"
    script = r'''
import importlib.util, json, os, pathlib, sys
source, root, destination = map(pathlib.Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("failure_probe", source)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
guard = probe.NetworkGuard(root / "network.jsonl")
sys.addaudithook(guard.audit)
import pytest
pytest.hookimpl(tryfirst=True)(probe.NativeResults.pytest_runtest_makereport)
for phase in ("setup", "call", "teardown"):
    pytest.hookimpl(hookwrapper=True, tryfirst=True)(
        getattr(probe.NativeResults, "pytest_runtest_" + phase))
results = probe.NativeResults(guard)
results.node_labels = {f"test_synthetic.py::test_{phase}": case for phase, case in (
    ("setup", "auth_converge"), ("call", "read_workspace_utf8"),
    ("teardown", "resource_publish_overlap"), ("group", "auth_wrong_leaf_no_disclosure"))}
code = pytest.main(["-q", "-p", "no:cacheprovider", "--rootdir", str(root),
    "--confcutdir", str(root), str(root / "test_synthetic.py")], plugins=[results])
assert code == 1
selection = results.finish(code)
assert selection["status"] == "FAIL"
assert [(v["case"], v["phase"], v["errors"][0]["class"]) for v in selection["failureFacts"]] == [
    ("auth_converge", "setup", "PermissionError"),
    ("read_workspace_utf8", "call", "ValueError"),
    ("resource_publish_overlap", "teardown", "OSError"),
    ("auth_wrong_leaf_no_disclosure", "teardown", "ExceptionGroup")]
assert selection["cases"]["read_workspace_utf8"]["callsPassed"] == 0
assert selection["cases"]["resource_publish_overlap"]["callsPassed"] == 1
assert selection["cases"]["auth_wrong_leaf_no_disclosure"]["callsPassed"] == 1
assert selection["cases"]["auth_wrong_leaf_no_disclosure"]["status"] == "FAIL"
assert [row["class"] for row in selection["failureFacts"][-1]["errors"]] == [
    "ExceptionGroup", "OSError", "ExceptionGroup", "ValueError", "RuntimeError"]
serialized = json.dumps(selection)
assert "PRIVATE-MESSAGE" not in serialized and "/private/path" not in serialized
assert "test_synthetic" not in serialized and "setup_denied" not in serialized
network = guard.network_diagnostics()
assert [(row["case"], row["phase"], row["count"]) for row in network["attempts"]] == [
    ("auth_converge", "setup", 1), ("auth_converge", "teardown", 1),
    ("read_workspace_utf8", "call", 1), ("resource_publish_overlap", "setup", 1),
    ("resource_publish_overlap", "call", 1), ("resource_publish_overlap", "teardown", 1)]
assert all(row["event"] == "socket.gethostbyname" for row in network["attempts"])
assert guard.blocked == 6 and network["overflow"] == 0
assert (guard.case, guard.phase) == ("outside", "outside")
try:
    guard.verify()
except AssertionError:
    pass
else:
    raise AssertionError("Caught synthetic attempts must still fail policy")
assert "PRIVATE-HOST" not in json.dumps(network)
destination.write_text(json.dumps({"selection": selection, "networkDiagnostics": network}),
                       encoding="utf-8")
'''
    child = subprocess.run([sys.executable, "-I", "-B", "-c", script,
        str(SPEC.origin), str(tmp_path), str(result_path)], capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    facts = json.loads(result_path.read_text(encoding="utf-8"))
    value = report()
    value["nativeControls"] = {"status": "FAIL", "stage": "native_prerequisites", "checks": {},
        **facts, "network": {"blockedOptionalIpv6Probes": 0, "unknownAttempts": 6,
                            "infrastructureSocketpairBinds": 0, "tlsNegativeReservations": 0,
                            "tlsNegativeListeners": 0, "tlsNegativeConnects": 0}}
    probe.validate_public(value)


def diagnostic_public_model():
    value = report()
    selection = native_result("failed")
    selection["failureFacts"] = [{"case": "read_workspace_utf8", "phase": "call", "errors": [{
        "class": "PermissionError", "errno": 13, "winerror": 32, "frames": [{
            "source": "native_read_open", "codeSha256": "e"*64, "lineOffset": 20}]}]}]
    value["nativeControls"] = {"status": "FAIL", "stage": "native_prerequisites", "checks": {},
        "selection": selection,
        "network": {"blockedOptionalIpv6Probes": 0, "unknownAttempts": 1,
                    "infrastructureSocketpairBinds": 6, "tlsNegativeReservations": 0,
                    "tlsNegativeListeners": 0, "tlsNegativeConnects": 0},
        "networkDiagnostics": {"attempts": [{
            "case": "auth_wrong_leaf_no_disclosure", "phase": "setup",
            "event": "socket.bind", "count": 1, "source": "pytest_unused_port",
            "codeSha256": "f"*64, "lineOffset": 2}], "overflow": 0}}
    return value


@pytest.mark.parametrize("fault", ["message", "class", "case", "phase", "source", "path",
                                  "fingerprint", "numeric", "network-host", "network-source",
                                  "network-phase", "network-case", "network-count", "network-other-hash"])
def test_failure_and_network_diagnostics_refuse_private_unbounded_or_inconsistent_fields(fault):
    value = diagnostic_public_model()
    probe.validate_public(value)
    control = value["nativeControls"]
    fact = control["selection"]["failureFacts"][0]
    error = fact["errors"][0]
    network = control["networkDiagnostics"]["attempts"][0]
    if fault == "message":
        error["message"] = "PRIVATE"
    elif fault == "class":
        error["class"] = "PrivateError"
    elif fault == "case":
        fact["case"] = "private_test"
    elif fault == "phase":
        fact["phase"] = "private_phase"
    elif fault in {"source", "path", "fingerprint"}:
        error["frames"][0][{"source": "source", "path": "path", "fingerprint": "codeSha256"}[fault]] = "PRIVATE"
    elif fault == "numeric":
        error["winerror"] = "PRIVATE"
    elif fault == "network-host":
        network["host"] = "PRIVATE"
    elif fault == "network-source":
        network["source"] = "private_symbol"
    elif fault == "network-phase":
        network["phase"] = "private_phase"
    elif fault == "network-case":
        network["case"] = "private_test"
    elif fault == "network-count":
        network["count"] = 2
    elif fault == "network-other-hash":
        network["source"] = "other"
    with pytest.raises((AssertionError, KeyError, TypeError)):
        probe.validate_public(value)


@pytest.mark.parametrize("unknown", [False, True])
def test_run_model_preserves_unknown_job_scratch(tmp_path, monkeypatch, unknown):
    monkeypatch.setattr(probe, "require_ci", lambda: None)
    monkeypatch.setattr(probe, "canonical_temp", lambda _: tmp_path)
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(probe.sys, "getwindowsversion",
                        lambda: types.SimpleNamespace(build=26100), raising=False)
    monkeypatch.setattr(probe.uuid, "uuid4", lambda: types.SimpleNamespace(hex="c"*32))
    monkeypatch.setattr(probe, "sanitize", lambda root: None)
    monkeypatch.setattr(probe, "private_directory", lambda path, root: path.mkdir(parents=True, exist_ok=True))
    async def hosts(root, wheel, value):
        value["outerJobsSettled"] = not unknown
        raise TimeoutError("private diagnostic never published")
    monkeypatch.setattr(probe, "hosts", hosts)
    receipt = tmp_path / "result.json"
    probe.write_json(receipt, {"candidate": report()["candidate"]})
    root = tmp_path / ("ut-" + "c"*12)
    assert probe.run(tmp_path / "unused.whl", receipt) == 1
    result = json.loads(receipt.read_text())
    assert result["status"] == "FAIL" and result["scratchRemoved"] is not unknown
    assert root.exists() is unknown
    assert "private diagnostic" not in receipt.read_text()


@pytest.mark.parametrize("field,value", [
    ("name", "private/path"), ("version", "private prose"), ("commit", "private"),
])
def test_installed_graph_public_fields_are_validated(field, value):
    installation = {"wheelSha256": "d"*64, "verifiedMembers": 123,
        "imports": {name: {"origin": "installed", "sha256": "e"*64} for name in probe.IMPORTS},
        "graph": [{"name": "amplifier-core", "version": "2.0.2"}]}
    result = report()
    result["workers"] = [installation]
    probe.validate_public(result)
    installation["graph"][0][field] = value
    with pytest.raises(AssertionError):
        probe.validate_public(result)


@pytest.fixture
def candidate_record_model(tmp_path, monkeypatch):
    """Wheel/install bytes only: no package build, installer or imports."""
    prefix = tmp_path / "env"
    site = prefix / "Lib/site-packages"
    scripts = prefix / "Scripts"
    site.mkdir(parents=True)
    scripts.mkdir()
    info = "amplifier_unified-0.46.2.dist-info"
    record = info + "/RECORD"
    payloads = {f"amplifier_web/unit{n}.py": f"value={n}\n".encode() for n in range(101)}
    payloads.update({
        info + "/METADATA": b"Name: amplifier-unified\nVersion: 0.46.2\n",
        info + "/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
        info + "/entry_points.txt": b"[console_scripts]\namplifier-unified = amplifier_web.cli:main\n"
                                   b"amplifier-unified-status = amplifier_web.status_protocol.cli:main\n",
    })
    def row(raw):
        return ("sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("="),
                str(len(raw)))
    def encoded(rows):
        stream = io.StringIO(newline="")
        csv.writer(stream, lineterminator="\n").writerows(
            (name, *data) for name, data in rows.items())
        return stream.getvalue().encode()
    wheel_rows = {name: row(raw) for name, raw in payloads.items()}
    wheel_rows[record] = ("", "")
    payloads[record] = encoded(wheel_rows)
    wheel = tmp_path / "amplifier_unified-0.46.2-py3-none-any.whl"
    def save_wheel():
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, raw in payloads.items():
                archive.writestr(name, raw)
    save_wheel()
    for name, raw in payloads.items():
        path = site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    installed_rows = dict(wheel_rows)
    generated = {info + "/INSTALLER": b"uv\n", info + "/REQUESTED": b"",
        info + "/direct_url.json": json.dumps({"url": wheel.as_uri(), "archive_info": {}}).encode(),
        info + "/uv_build.json": b"{}", info + "/uv_cache.json": b'{"timestamp":null}'}
    for script in ("amplifier-unified", "amplifier-unified-status"):
        suffix = ".exe" if sys.platform == "win32" else ""
        generated["../../Scripts/" + script + suffix] = b"synthetic installer launcher"
    for name, raw in generated.items():
        (site / name).write_bytes(raw)
        installed_rows[name] = row(raw)
    def save_record():
        (site / record).write_bytes(encoded(installed_rows))
    save_record()
    dist = types.SimpleNamespace(
        version="0.46.2", metadata={"Name": "amplifier-unified"},
        locate_file=lambda name: site / name,
        read_text=lambda name: (site / info / name).read_text(encoding="utf-8"),
        files=[Path(name) for name in installed_rows])
    monkeypatch.setattr(probe.sys, "prefix", str(prefix))
    monkeypatch.setattr(probe.sysconfig, "get_path", lambda name: str(scripts) if name == "scripts" else "")
    return types.SimpleNamespace(prefix=prefix, site=site, wheel=wheel, dist=dist,
        info=info, record=record, payloads=payloads, installed_rows=installed_rows,
        row=row, encoded=encoded, save_record=save_record, save_wheel=save_wheel)


def test_installer_rewritten_record_passes_only_after_independent_hash_size_audit(candidate_record_model):
    model = candidate_record_model
    assert (model.site / model.record).read_bytes() != model.payloads[model.record]
    with zipfile.ZipFile(model.wheel) as archive:
        assert probe.candidate_members(archive, model.dist) == len(model.payloads) - 1


def test_original_byte_loop_rejects_legitimate_record_rewrite(candidate_record_model):
    model = candidate_record_model
    with zipfile.ZipFile(model.wheel) as archive:
        mismatch = [name for name in archive.namelist() if name.startswith("amplifier_")
                    and (model.site / name).read_bytes() != archive.read(name)]
    assert mismatch == [model.record]  # Retain the exact old before-failure discriminator.


@pytest.mark.parametrize("fault", [
    "payload", "metadata", "wheel-hash", "wheel-size", "installed-hash", "installed-size",
    "missing-wheel-row", "extra-wheel-row", "duplicate-wheel-row", "missing-installed-row",
    "missing-file", "extra-registered-metadata", "extra-unregistered-metadata",
    "extra-registered-payload", "missing-direct-url", "missing-installer", "missing-launcher",
    "changed-installer", "changed-launcher", "nonempty-requested", "record-self-hash",
])
def test_record_audit_rejects_payload_or_inventory_tampering(candidate_record_model, fault):
    model = candidate_record_model
    payload = "amplifier_web/unit0.py"
    if fault in {"payload", "metadata"}:
        name = payload if fault == "payload" else model.info + "/METADATA"
        (model.site / name).write_bytes(b"changed")
        # Even a self-consistent installed RECORD cannot conceal wheel mismatch.
        model.installed_rows[name] = model.row(b"changed")
    elif fault.startswith("wheel-") or fault in {"missing-wheel-row", "extra-wheel-row", "duplicate-wheel-row"}:
        rows = probe.record_rows(model.payloads[model.record], model.record)
        if fault == "wheel-hash":
            rows[payload] = ("sha256=" + "A"*43, rows[payload][1])
        elif fault == "wheel-size":
            rows[payload] = (rows[payload][0], "999")
        elif fault == "missing-wheel-row":
            rows.pop(payload)
        elif fault == "extra-wheel-row":
            rows["amplifier_web/unregistered.py"] = model.row(b"unexpected")
        raw = model.encoded(rows)
        if fault == "duplicate-wheel-row":
            raw += model.encoded({payload: rows[payload]})
        model.payloads[model.record] = raw
        model.save_wheel()
    elif fault in {"installed-hash", "installed-size"}:
        prior = model.installed_rows[payload]
        model.installed_rows[payload] = ("sha256=" + "A"*43, prior[1]) if fault == "installed-hash" else (prior[0], "999")
    elif fault == "missing-installed-row":
        model.installed_rows.pop(payload)
    elif fault == "missing-file":
        (model.site / payload).unlink()
    elif fault.startswith("extra-"):
        name = model.info + "/unexpected.json" if "metadata" in fault else "amplifier_web/extra.py"
        (model.site / name).write_bytes(b"unexpected")
        if "unregistered" not in fault:
            model.installed_rows[name] = model.row(b"unexpected")
    elif fault in {"missing-direct-url", "missing-installer", "missing-launcher"}:
        name = (model.info + ("/direct_url.json" if fault == "missing-direct-url" else "/INSTALLER")
                if fault != "missing-launcher" else next(name for name in model.installed_rows if name.startswith("../")))
        model.installed_rows.pop(name)
        (model.site / name).unlink()
    elif fault in {"changed-installer", "changed-launcher", "nonempty-requested"}:
        name = (model.info + ("/INSTALLER" if fault == "changed-installer" else "/REQUESTED")
                if fault != "changed-launcher" else next(name for name in model.installed_rows if name.startswith("../")))
        (model.site / name).write_bytes(b"changed")
        if fault != "changed-launcher":
            model.installed_rows[name] = model.row(b"changed")
    elif fault == "record-self-hash":
        model.installed_rows[model.record] = model.row(b"not a legitimate self hash")
    model.save_record()
    with zipfile.ZipFile(model.wheel) as archive, pytest.raises((AssertionError, OSError)):
        probe.candidate_members(archive, model.dist)


@pytest.mark.parametrize("fault,stage", [
    (None, "complete"), ("direct", "direct_url"), ("payload", "wheel_members"),
    ("origin", "imports"), ("graph", "graph"),
])
def test_installed_audit_exposes_only_fixed_substage(candidate_record_model, monkeypatch, fault, stage):
    model = candidate_record_model
    original_read = model.dist.read_text
    if fault == "direct":
        model.dist.read_text = lambda name: ('{"url":"file:///wrong.whl"}' if name == "direct_url.json" else original_read(name))
    elif fault == "payload":
        (model.site / "amplifier_web/unit0.py").write_bytes(b"changed")
    monkeypatch.setattr(probe.metadata, "distribution", lambda name: model.dist)
    monkeypatch.setattr(probe.metadata, "version", lambda name: "2.0.2")
    monkeypatch.setattr(probe.metadata, "distributions", lambda: [model.dist])
    if fault == "graph":
        model.dist.metadata["Name"] = "private/path"
    origin = model.site / ("not-recorded.py" if fault == "origin" else "amplifier_web/unit0.py")
    origin.write_bytes(origin.read_bytes() if origin.exists() else b"wrong origin")
    monkeypatch.setattr(probe.importlib, "import_module", lambda name: types.SimpleNamespace(__file__=str(origin)))
    session = types.ModuleType("amplifier_foundation.session")
    session.prepare_session_directory = lambda: None
    monkeypatch.setitem(sys.modules, "amplifier_foundation.session", session)
    report = {}
    if fault:
        with pytest.raises(AssertionError):
            probe.installed(model.wheel, report)
    else:
        assert probe.installed(model.wheel, report)["verifiedMembers"] > 100
    expected = {"installationStage": stage}
    if fault == "origin":
        expected.update(installationImport="amplifier_web", installationCheck="import_origin")
    assert report == expected


@pytest.mark.parametrize("package", list(probe.IMPORTS))
@pytest.mark.parametrize("fault", ["missing-posix", "missing-private", "symbol", "distribution"])
def test_installed_import_failure_identifies_exact_check_without_exception_prose(
        candidate_record_model, monkeypatch, package, fault):
    model = candidate_record_model
    prior = list(probe.IMPORTS)[:list(probe.IMPORTS).index(package)]
    seen = []
    def distribution(name):
        if fault == "distribution" and seen and seen[-1] == package:
            raise probe.metadata.PackageNotFoundError("private-distribution")
        return model.dist
    monkeypatch.setattr(probe.metadata, "distribution", distribution)
    def module(name):
        seen.append(name)
        if name == package:
            if fault in {"missing-posix", "missing-private"}:
                raise ModuleNotFoundError("private path, token, traceback",
                    name="fcntl" if fault == "missing-posix" else "private_module_name")
            if fault == "symbol":
                raise ImportError("private missing symbol", name="amplifier_core", path="/private/file")
        return types.SimpleNamespace(__file__=str(model.site / "amplifier_web/unit0.py"))
    monkeypatch.setattr(probe.importlib, "import_module", module)
    diagnostics = {}
    with pytest.raises(ImportError):
        probe.installed(model.wheel, diagnostics)
    assert seen == [*prior, package]
    assert diagnostics == {
        "installationStage": "imports", "installationImport": package,
        "installationCheck": "distribution_metadata" if fault == "distribution" else "module_import",
        "installationImportFailure": {"distribution": "distribution_not_found",
            "symbol": "symbol_or_loader"}.get(fault, "module_not_found"),
        "installationMissingModule": {"missing-posix": "fcntl", "symbol": "amplifier_core"}.get(fault, "other")}
    result = report()
    result.update(diagnostics)
    probe.validate_public(result)
    serialized = json.dumps(result)
    for forbidden in ("private-distribution", "private path", "private_module_name", "/private/file", "private missing"):
        assert forbidden not in serialized


@pytest.mark.parametrize("field,value", [
    ("installationImport", "private.import"), ("installationCheck", "/private/path"),
    ("installationImportFailure", "private error"), ("installationMissingModule", "private_module"),
])
def test_installed_import_diagnostic_fields_are_strictly_allowlisted(field, value):
    result = report()
    result.update(installationStage="imports", installationImport="amplifier_web.host.session",
        installationCheck="module_import", installationImportFailure="module_not_found",
        installationMissingModule="fcntl")
    probe.validate_public(result)
    result[field] = value
    with pytest.raises(AssertionError):
        probe.validate_public(result)


def test_installed_foundation_api_failure_has_its_own_exact_label(candidate_record_model, monkeypatch):
    model = candidate_record_model
    monkeypatch.setattr(probe.metadata, "distribution", lambda name: model.dist)
    monkeypatch.setattr(probe.metadata, "version", lambda name: "2.0.2")
    monkeypatch.setattr(probe.importlib, "import_module", lambda name: types.SimpleNamespace(
        __file__=str(model.site / "amplifier_web/unit0.py")))
    session = types.ModuleType("amplifier_foundation.session")
    def missing(name):
        if name == "prepare_session_directory":
            raise ImportError("private symbol diagnostics", name="amplifier_foundation.session")
        raise AttributeError(name)
    session.__getattr__ = missing
    monkeypatch.setitem(sys.modules, "amplifier_foundation.session", session)
    diagnostics = {}
    with pytest.raises(ImportError):
        probe.installed(model.wheel, diagnostics)
    assert diagnostics == {
        "installationStage": "imports", "installationImport": "amplifier_foundation",
        "installationCheck": "foundation_api", "installationImportFailure": "symbol_or_loader",
        "installationMissingModule": "amplifier_foundation.session"}
    result = report()
    result.update(diagnostics)
    probe.validate_public(result)


def test_installed_success_clears_prior_failure_diagnostics(candidate_record_model, monkeypatch):
    model = candidate_record_model
    monkeypatch.setattr(probe.metadata, "distribution", lambda name: model.dist)
    monkeypatch.setattr(probe.metadata, "version", lambda name: "2.0.2")
    monkeypatch.setattr(probe.metadata, "distributions", lambda: [model.dist])
    monkeypatch.setattr(probe.importlib, "import_module", lambda name: types.SimpleNamespace(
        __file__=str(model.site / "amplifier_web/unit0.py")))
    session = types.ModuleType("amplifier_foundation.session")
    session.prepare_session_directory = lambda: None
    monkeypatch.setitem(sys.modules, "amplifier_foundation.session", session)
    diagnostics = {"installationStage": "imports", "installationImport": "amplifier_module_loop_live",
        "installationCheck": "module_import", "installationImportFailure": "module_not_found",
        "installationMissingModule": "fcntl"}
    probe.installed(model.wheel, diagnostics)
    assert diagnostics == {"installationStage": "complete"}


def test_public_audit_substage_has_no_freeform_escape():
    for stage in probe.INSTALLATION_STAGES:
        result = report()
        result["installationStage"] = stage
        probe.validate_public(result)
    result["installationStage"] = "/private/member.py"
    with pytest.raises(AssertionError):
        probe.validate_public(result)


@pytest.mark.parametrize("rows", [
    "file,sha256=bad,3\npkg.dist-info/RECORD,,\n",
    "file,sha256=" + "A"*43 + ",-1\npkg.dist-info/RECORD,,\n",
    "file,sha256=" + "A"*43 + ",00\npkg.dist-info/RECORD,,\n",
    "file,sha256=" + "A"*43 + ",3\nfile,sha256=" + "A"*43 + ",3\npkg.dist-info/RECORD,,\n",
    "file,,\npkg.dist-info/RECORD,,\n",
    "file,sha256=" + "A"*43 + ",3\n",
    "file,sha256=" + "A"*43 + ",3,extra\npkg.dist-info/RECORD,,\n",
])
def test_record_parser_requires_unique_complete_rows(rows):
    with pytest.raises(AssertionError):
        probe.record_rows(rows.encode(), "pkg.dist-info/RECORD")


def test_workflow_keeps_old_lane_commands_and_bounded_installed_candidate_job():
    workflow = yaml.safe_load((ROOT / ".github/workflows/platform-mechanisms.yml").read_text())
    # Strip only EOF scalar newline; appending a new job adds one to the prior
    # final block in YAML. No command, matrix, dependency or budget may change.
    # Frozen 177b9c98 commands, including the already accepted bootstrap lane.
    golden = {"passive-process-check": "fa89a68443d5cbbda12ea3513cd5cfa12bb5cb42adbf0f7744f240fdeb3b3d8b",
              "windows-private-config-tls": "557dd004afccf2bff8d53e3a40ad62ca232ee6a218ba1a56e9bbda71ad302987"}
    for name, expected in golden.items():
        job = copy.deepcopy(workflow["jobs"][name])
        assert job.pop("if") == "${{ !inputs.bootstrap_only && !inputs.foreground_only && !inputs.storage_only }}"
        if name == "windows-private-config-tls":
            job["steps"][-1]["run"] = job["steps"][-1]["run"].rstrip("\n")
        assert hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest() == expected
    job = workflow["jobs"]["windows-user-trial"]
    assert job["timeout-minutes"] == 15 and job["runs-on"] == "windows-latest"
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "uv build --wheel" in commands and "--only-binary amplifier-core" in commands
    assert '"pytest-asyncio>=1.4,<2"' in commands and '"pytest-aiohttp>=1.1,<2"' in commands
    assert job["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert "-I -B $probe --run" in commands
    upload = next(step["with"] for step in job["steps"]
                  if step.get("name") == "Upload only allowlisted public facts, never scratch or raw diagnostics")
    assert upload["path"] == "${{ runner.temp }}/ut/evidence/result.json"
    assert "*" not in upload["path"]


def cold_ci_runner():
    workflow = yaml.safe_load((ROOT / ".github/workflows/platform-mechanisms.yml").read_text())
    job = workflow["jobs"]["windows-default-runtime"]
    text = next(step["run"] for step in job["steps"]
                if step.get("name", "").startswith("Require one native cold"))
    code = text.split("$check = @'\n", 1)[1].split("\n'@", 1)[0]
    return workflow, job, ast.parse(code)


def test_default_command_job_is_separate_finite_and_requires_real_native_node():
    workflow, job, runner = cold_ci_runner()
    assert job["needs"] == "windows-user-trial" and job["runs-on"] == "windows-latest"
    assert job["timeout-minutes"] == 34
    assert sum(step.get("timeout-minutes", 0) for step in job["steps"]) < job["timeout-minutes"]
    offline = workflow["jobs"]["windows-user-trial"]
    preserved_offline = copy.deepcopy(offline)
    assert preserved_offline.pop("if") == "${{ inputs.storage_only || (!inputs.bootstrap_only && !inputs.foreground_only) }}"
    # Remove only the new opt-in diagnostic steps/conditions before comparing
    # every default-path byte to the frozen accepted candidate.
    added = {
        "Reject conflicting storage diagnostic selections": "${{ inputs.storage_only && (inputs.bootstrap_only || inputs.foreground_only) }}",
        "Require deterministic native event-publication sharing controls": "${{ inputs.storage_only }}",
        "Upload bounded event-publication sharing result": "${{ always() && inputs.storage_only }}",
    }
    for name, condition in added.items():
        selected = [step for step in preserved_offline["steps"] if step.get("name") == name]
        assert len(selected) == 1
        assert selected[0]["if"] == condition
    preserved_offline["steps"] = [step for step in preserved_offline["steps"] if step.get("name") not in added]
    for step in preserved_offline["steps"]:
        if step.get("name") in {
                "Require native prerequisite cells then probe private auth, tool, Stop and host resume",
                "Check update recovery caller preserves declared private paths"}:
            assert step.pop("if") == "${{ !inputs.storage_only }}"
    assert hashlib.sha256(json.dumps(preserved_offline, sort_keys=True).encode()).hexdigest() == (
        "0e3ec4076be0fc54ca80116f6e612ddfb8c926bf0f0cc0abd28b2a6603f3ebe4")
    assert offline["timeout-minutes"] == 15
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    for forbidden in ("UV_OFFLINE", "prepare_project(", "NetworkGuard(", "runtime.command =", "--editable"):
        assert forbidden not in commands and forbidden not in job["env"]
    assert "uv build --wheel" in commands and "--only-binary amplifier-core" in commands
    assert "& \"$base/env/Scripts/python.exe\" -I -B -c $check" in commands
    assert '$env:AMPLIFIER_WINDOWS_DEFAULT_RUNTIME_PYTHON = "$base/env/Scripts/python.exe"' in commands
    assert '$env:AMPLIFIER_WINDOWS_DEFAULT_RUNTIME_WHEEL = $wheel[0].FullName' in commands
    assert '$env:AMPLIFIER_WINDOWS_DEFAULT_RUNTIME_REPORT = "$base/probe-result.json"' in commands
    assert job["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    node = next(ast.literal_eval(part.value) for part in runner.body
                if isinstance(part, ast.Assign) and any(isinstance(target, ast.Name)
                    and target.id == "node" for target in part.targets))
    assert node == "tests/test_runtime.py::test_native_windows_default_runtime_command_cold_prepare_turn_and_stop"
    native_test = ast.parse((ROOT / "tests/test_runtime.py").read_text(encoding="utf-8"))
    function = next(part for part in native_test.body if isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and part.name == node.split("::")[1])
    timeouts = [ast.literal_eval(keyword.value) for call in ast.walk(function)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr in {"run", "run_owned_probe"}
                for keyword in call.keywords if keyword.arg == "timeout"]
    assert timeouts == [1200]
    cold_step = next(step for step in job["steps"] if step.get("name", "").startswith("Require one native cold"))
    assert cold_step["timeout-minutes"] * 60 >= 1200 + 60
    compile(runner, "<cold-ci-check>", "exec")
    assert sum(isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute)
               and part.func.attr == "installed" for part in ast.walk(runner)) == 2
    assert all(isinstance(part.args[1], ast.Name) and part.args[1].id == "result"
               for part in ast.walk(runner) if isinstance(part, ast.Call)
               and isinstance(part.func, ast.Attribute) and part.func.attr == "installed")
    assert any(isinstance(part, ast.Assert) and "selected.passed(code)" in ast.unparse(part)
               for part in ast.walk(runner))
    assert any(isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute)
               and part.func.attr == "validate_report" for part in ast.walk(runner))
    upload = next(step for step in job["steps"] if step.get("name") == "Upload only the bounded default-runtime result")
    assert upload["if"] == "always()" and upload["with"]["name"] == "windows-default-runtime"
    assert upload["with"]["path"] == "${{ runner.temp }}/dr-ci/evidence/publicresult.json"
    assert "*" not in upload["with"]["path"] and "private" not in upload["with"]["path"]


@pytest.mark.parametrize("fault", [None, "missing", "wrong-node", "duplicate", "skip", "failed",
                                  "setup-failed", "teardown-failed", "no-call", "xfail", "exit-code"])
def test_default_command_ci_result_requires_exact_real_call_pass(fault):
    _, _, tree = cold_ci_runner()
    selected_class = next(part for part in tree.body if isinstance(part, ast.ClassDef) and part.name == "Results")
    namespace = {"node": "tests/test_runtime.py::test_native_windows_default_runtime_command_cold_prepare_turn_and_stop"}
    exec(compile(ast.Module(body=[selected_class], type_ignores=[]), "<ci-results-model>", "exec"), namespace)
    result = namespace["Results"]()
    nodes = [namespace["node"]]
    if fault == "missing":
        nodes = []
    elif fault == "wrong-node":
        nodes = ["tests/test_runtime.py::unrelated"]
    elif fault == "duplicate":
        nodes *= 2
    result.pytest_collection_finish(types.SimpleNamespace(items=[
        types.SimpleNamespace(nodeid=node) for node in nodes]))
    for phase in ("setup", "call", "teardown"):
        if fault == "no-call" and phase == "call":
            continue
        outcome = "passed"
        if phase == "call" and fault in {"failed", "skip"}:
            outcome = "failed" if fault == "failed" else "skipped"
        if fault == phase + "-failed":
            outcome = "failed"
        row = types.SimpleNamespace(nodeid=namespace["node"], when=phase, outcome=outcome)
        if fault == "xfail" and phase == "call":
            row.wasxfail = "not acceptance"
        result.pytest_runtest_logreport(row)
    assert result.passed(1 if fault == "exit-code" else 0) is (fault is None)


def test_public_journey_has_no_private_adapter_or_manual_reinitialization():
    tree = ast.parse(Path(SPEC.origin).read_text(encoding="utf-8"))
    journey = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "journey")
    text = ast.unparse(journey)
    assert "_read_private_owner_control_token" not in text and "publicGateClosed" not in text
    assert "entry.initialize" not in text and "read_owner_control_token =" not in text
    assert 'assert app["browser_entry"] is not None' in text or "assert app['browser_entry'] is not None" in text
    assert "publicOwnerEntryInitialized" in text
    assert probe.validate_public(report())
    obsolete = report()
    obsolete["qualification"] = "private-adapter-candidate-only"
    with pytest.raises(AssertionError):
        probe.validate_public(obsolete)


@pytest.mark.parametrize("mode", probe.CLI_MODES)
def test_foreground_cli_exact_parser_arguments(mode):
    root = Path("/synthetic/café 日本語")
    arguments = probe.cli_arguments(mode, root, 8942)
    assert arguments[:4] == ["--data-dir", str(root / "app"),
                             "--workspace", str(root / "workspace café 日本語")]
    if mode in {"serve", "open"}:
        assert arguments[4:8] == ["--bind", "127.0.0.1", "--port", "8942"]
        assert arguments[8:] == (["--no-open", "serve"] if mode == "serve" else ["open"])
    else:
        assert arguments[4:] == ["setup-tls", *([] if mode == "setup" else [mode])]


def test_foreground_cli_output_is_private_bounded_and_preserves_export_bytes():
    output = probe.CliOutput()
    content = b"-----BEGIN CERTIFICATE-----\r\nsynthetic\r\n-----END CERTIFICATE-----\r\n"
    assert output.write(content) == len(content) and output.getvalue() == content
    output.write(b"x" * (65536 - len(content)))
    with pytest.raises(AssertionError):
        output.write(b"private-overflow")
    assert len(output.getvalue()) == 65536


@pytest.mark.parametrize("fault", [None, "wrong-code", "wrong-object", "wrong-port", "repeat", "not-serve"])
def test_foreground_cli_bind_guard_is_exact_real_asyncio_socket_only(tmp_path, monkeypatch, fault):
    guard = probe.CliNetworkGuard(tmp_path / "network.jsonl", 8942, serve=fault != "not-serve")
    stream = types.SimpleNamespace(family=socket.AF_INET, type=socket.SOCK_STREAM)
    code = probe.asyncio.BaseEventLoop.create_server.__code__
    frame = types.SimpleNamespace(f_code=code, f_locals={"sock": stream})
    if fault == "wrong-code":
        frame.f_code = (lambda: None).__code__
    if fault == "wrong-object":
        frame.f_locals["sock"] = object()
    if fault == "repeat":
        guard.listener = object()
    # No socket, loop, process or real network event is emitted by this model.
    with monkeypatch.context() as patch:
        patch.setattr(probe.sys, "_getframe", lambda *a: frame)
        if fault is None:
            guard.audit("socket.bind", (stream, ("127.0.0.1", 8942)))
            assert guard.listener is stream and guard.blocked == 0
        else:
            with pytest.raises(PermissionError):
                guard.audit("socket.bind", (stream, ("127.0.0.1", 8943 if fault == "wrong-port" else 8942)))
    assert guard.port is None  # Serving never admits outbound app networking.
    if fault:
        assert guard.blocked == 1 and tmp_path.joinpath("network.jsonl").exists()


@pytest.mark.parametrize("event", ["socket.getaddrinfo", "socket.gethostbyname",
                                  "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto"])
def test_foreground_cli_guard_never_adds_dns_or_udp_exemption(tmp_path, event):
    guard = probe.CliNetworkGuard(tmp_path / "network.jsonl", 8942, connect=True)
    with pytest.raises(PermissionError):
        guard.audit(event, ("private-invalid-host",))
    assert guard.blocked == 1
    assert "private-invalid-host" not in (tmp_path / "network.jsonl").read_text()


def cli_public_report():
    installed = {"wheelSha256": "a"*64, "verifiedMembers": 868,
        "imports": {name: {"origin": "installed", "sha256": "b"*64} for name in probe.IMPORTS},
        "graph": [{"name": "amplifier-core", "version": "2.0.2"}]}
    value = {"schema": 1, "qualification": "installed-foreground-cli-no-preload-not-device",
        "publicAcceptance": False, "candidate": {"head": "a"*40, "tree": "b"*40},
        "status": "PASS", "stage": "complete", "checks": {
            "cookieCsrfReplay": "PASS", "exportFingerprint": "PASS"},
        "children": [], "outerJobsSettled": True, "scratchRemoved": True, "unknownNetworkAttempts": 0}
    required = {
        "setup": ["mainReturned"],
        "status": ["mainReturned", "tlsMaterialStable"],
        "export": ["mainReturned", "tlsMaterialStable", "exportMatchesCa"],
        "serve": ["mainReturned", "tlsMaterialStable", "defaultRuntime", "publicEntryInitialized",
                  "updatesDisabled", "appCleanup", "repairLockReleased", "listenerClosed"],
        "open": ["mainReturned", "tlsMaterialStable", "browserDelivered"]}
    for mode in probe.CLI_MODES:
        value["children"].append({"mode": mode, "status": "PASS", "installed": copy.deepcopy(installed),
            "checks": dict.fromkeys(required[mode], "PASS"),
            "network": {"blockedOptionalIpv6Probes": 0, "unknownAttempts": 0}})
    return value


@pytest.mark.parametrize("fault", [None, "missing-child", "missing-public-reader", "forced-only",
    "no-lock-release", "no-main-return", "open-without-delivery", "unknown-network",
    "graph-drift", "cleanup-unknown", "scratch-retained", "export-unchecked",
    "ticket", "raw-error", "public-acceptance", "bad-graph-name", "extra-installed-field", "parent-network"])
def test_foreground_cli_receipt_rejects_missing_proof_and_private_fields(fault):
    value = cli_public_report()
    serve, opened = value["children"][3:]
    if fault == "missing-child":
        value["children"].pop()
    elif fault == "missing-public-reader":
        del serve["checks"]["publicEntryInitialized"]
    elif fault == "forced-only":
        del serve["checks"]["appCleanup"]
    elif fault == "no-lock-release":
        del serve["checks"]["repairLockReleased"]
    elif fault == "no-main-return":
        del serve["checks"]["mainReturned"]
    elif fault == "open-without-delivery":
        del opened["checks"]["browserDelivered"]
    elif fault == "unknown-network":
        opened["network"]["unknownAttempts"] = 1
    elif fault == "parent-network":
        value["unknownNetworkAttempts"] = 1
    elif fault == "graph-drift":
        opened["installed"]["graph"][0]["version"] = "2.0.3"
    elif fault == "cleanup-unknown":
        value["outerJobsSettled"] = False
    elif fault == "scratch-retained":
        value["scratchRemoved"] = False
    elif fault == "export-unchecked":
        del value["checks"]["exportFingerprint"]
    elif fault in {"ticket", "raw-error"}:
        opened[fault] = "/private/credential"
    elif fault == "public-acceptance":
        value["publicAcceptance"] = True
    elif fault == "bad-graph-name":
        opened["installed"]["graph"][0]["name"] = "/private/credential"
    elif fault == "extra-installed-field":
        opened["installed"]["imports"]["amplifier_web"]["path"] = "/private"
    if fault is None:
        assert probe.validate_cli_public(value) is value
    else:
        with pytest.raises(AssertionError):
            probe.validate_cli_public(value)


@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize("dispatch_failure", [False, True])
@pytest.mark.parametrize("fixture_scope", ["fresh", "wrong-home", "existing-session"])
def test_foreground_cli_actual_startup_wrapper_uses_real_settings_action_and_no_fake_runtime(
        monkeypatch, tmp_path, closed, dispatch_failure, fixture_scope):
    """Inert app/loop model only; execute the actual fixture wrapper, never a host."""
    from amplifier_web.configuration_authority import ConfigurationAuthority
    authority = ConfigurationAuthority.__new__(ConfigurationAuthority)
    collaboration_tree = ast.parse((ROOT / "amplifier_web/collaboration.py").read_text(encoding="utf-8"))
    declaration = next(node for node in collaboration_tree.body
                       if isinstance(node, ast.ClassDef) and node.name == "Collaboration")
    source = next(node for node in declaration.body if isinstance(node, ast.FunctionDef) and node.name == "source")
    namespace = {}
    exec(compile(ast.Module(body=[source], type_ignores=[]), "<actual-collaboration-source>", "exec"), namespace)
    class MissingCaller(RuntimeError):
        pass
    def rejected(*args):
        raise MissingCaller("No fixture-owned agent conversation exists")
    collaboration = types.SimpleNamespace(error=rejected)
    collaboration.source = types.MethodType(namespace["source"], collaboration)
    authority.service = types.SimpleNamespace(collaboration=collaboration)
    patch = {"patch": {"updates": {"autoCheck": False, "autoInstall": False}}}
    # Actual capture -> _capture -> source -> Collaboration.source refuses the
    # original unowned system call before any configuration mutation/grant.
    with pytest.raises(MissingCaller):
        authority.capture("settings.update", patch, "system", None)
    assert authority.capture("settings.update", patch, "user", None) is None
    parent = types.ModuleType("amplifier_web")
    parent.__path__ = []
    server = types.ModuleType("amplifier_web.server")
    runtime = types.ModuleType("amplifier_web.runtime")
    class Runtime:
        workers = {}
    runtime.RuntimeManager = Runtime
    service = types.SimpleNamespace(runtime=Runtime(), data_dir=tmp_path / "app",
                                    state={"settings": {"updates": {}}, "sessions": []})
    if fixture_scope == "wrong-home":
        service.data_dir = tmp_path / "unowned"
    elif fixture_scope == "existing-session":
        service.state["sessions"].append({"id": "synthetic-existing"})
    calls = []
    sentinel = PermissionError("Synthetic private failure must not be disclosed")
    async def dispatch(name, args, **kwargs):
        calls.append((name, args, kwargs))
        authority.check(authority.capture(name, args, kwargs["origin"], None))
        if dispatch_failure:
            raise sentinel
        service.state["settings"]["updates"].update(args["patch"]["updates"])
    service.dispatch = dispatch
    app = {"service": service, "browser_entry": None if closed else object()}
    class App(dict):
        on_startup, on_cleanup = [], []
    app = App(app)
    async def create(*args, **kwargs):
        calls.append(("create", args, kwargs))
        return app
    server.create_app = create
    parent.server = server
    monkeypatch.setitem(sys.modules, "amplifier_web", parent)
    monkeypatch.setitem(sys.modules, "amplifier_web.server", server)
    monkeypatch.setitem(sys.modules, "amplifier_web.runtime", runtime)
    checks = {}
    observation = {}
    guard = types.SimpleNamespace(codes={})
    with probe.cli_application(tmp_path, checks, observation, guard):
        coroutine = server.create_app(tmp_path / "app", workspace="synthetic", server_config={})
        if closed:
            with pytest.raises(AssertionError):
                immediate(coroutine)
            assert "publicEntryInitialized" not in checks and len(calls) == 1
            assert observation["step"] == "app_create"
        elif fixture_scope != "fresh":
            with pytest.raises(AssertionError):
                immediate(coroutine)
            assert observation["step"] == "app_create" and len(calls) == 1
            assert "updatesDisabled" not in checks
        elif dispatch_failure:
            with pytest.raises(PermissionError) as error:
                immediate(coroutine)
            assert error.value is sentinel
            assert observation["step"] == "settings_update"
            assert set(checks) == {"defaultRuntime", "publicEntryInitialized"}
        else:
            assert immediate(coroutine) is app
            assert calls[0] == ("create", (tmp_path / "app",),
                {"workspace": "synthetic", "server_config": {}, "preload_providers": False})
            assert calls[1] == ("settings.update",
                {"patch": {"updates": {"autoCheck": False, "autoInstall": False}}},
                {"origin": "user", "include_state": False})
            assert checks == {"defaultRuntime": "PASS", "publicEntryInitialized": "PASS", "updatesDisabled": "PASS"}
            assert observation["step"] == "updates_check"
            assert len(app.on_startup) == len(app.on_cleanup) == 1
            # Drive the actual stop observer with an inert loop/task model.
            # It schedules aiohttp's real GracefulExit, not a replacement server.
            callbacks, cancelled = [], []
            async def until(predicate):
                assert predicate()
            (tmp_path / "cli-stop").touch()
            monkeypatch.setattr(probe, "until", until)
            monkeypatch.setattr(probe.asyncio, "get_running_loop",
                                lambda: types.SimpleNamespace(call_soon=callbacks.append))
            task = types.SimpleNamespace(done=lambda: True, cancel=lambda: cancelled.append(True))
            def create_task(coroutine):
                immediate(coroutine)
                return task
            async def gather(*args, **kwargs):
                assert args == (task,) and kwargs == {"return_exceptions": True}
            monkeypatch.setattr(probe.asyncio, "create_task", create_task)
            monkeypatch.setattr(probe.asyncio, "gather", gather)
            immediate(app.on_startup[0](app))
            from aiohttp import web
            assert len(callbacks) == 1 and not cancelled
            with pytest.raises(web.GracefulExit):
                callbacks[0]()
            immediate(app.on_cleanup[0](app))
            assert checks["appCleanup"] == "PASS"
    assert server.create_app is create
    assert set(guard.codes.values()) == {"cli_app_create"}


def test_foreground_cli_source_and_workflow_preserve_entry_cleanup_budget_and_cold_boundary():
    workflow, job, _ = cold_ci_runner()
    steps = job["steps"]
    cold = next(i for i, s in enumerate(steps) if s.get("name", "").startswith("Require one native cold"))
    foreground = next(i for i, s in enumerate(steps) if s.get("name", "").startswith("Require installed foreground"))
    step = steps[foreground]
    assert foreground == cold + 1 and step.get("if", "success()") == "success()"
    assert step["timeout-minutes"] == 4 and "--cli-run" in step["run"]
    assert '"$base/env/Scripts/python.exe" -I -B $probe' in step["run"]
    assert "--candidate-receipt" in step["run"] and "uv " not in step["run"]
    upload = steps[-1]
    assert upload["if"] == "always()" and upload["with"]["name"] == "windows-foreground-cli"
    assert upload["with"]["path"] == "${{ runner.temp }}/dr-ci/evidence/foreground.json"
    source = Path(SPEC.origin).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {n.name: ast.unparse(n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    child, hosts, application, run = (functions[name] for name in ("cli_child", "cli_hosts", "cli_application", "cli_run"))
    assert child.count("installed(wheel)") == 2 and child.count("runpy.run_module(") == 2
    assert "cli_arguments(mode, root, port)" in child and "sys.argv" in child
    for forbidden in ("web.run_app =", "RuntimeManager(", "read_owner_control_token =", "entry.initialize",
                      "_read_private_owner_control_token", "runtime.command", "prepare_project"):
        assert forbidden not in child + hosts + application + run
    assert "original(*args, **kwargs, preload_providers=False)" in application
    assert "web.GracefulExit()" in application and "call_soon(exit_loop)" in application
    assert "app.on_cleanup.append(cleaned)" in application
    assert 'shared=False' in child and "guard.listener.fileno() == -1" in child
    assert "job.wait()" in hosts and "job.close()" in hosts and "job.tree_empty" in hosts and "job.io_finalized" in hosts
    assert "asyncio.timeout(150)" in run
    assert "sanitize(root)" in run and "canonical_temp(" in run and "report['outerJobsSettled']" in run
    assert "shutil.rmtree(root)" in run and run.index("sanitize(root)") < run.index("asyncio.run(")
    assert "validate_cli_public(report)" in run
    assert "origin='user'" in application and "origin='system'" not in application
    assert "service.data_dir == root / 'app' and (not service.state['sessions'])" in application
    compile(tree, SPEC.origin, "exec")


def test_foreground_cli_only_dispatch_preserves_accepted_cells_and_normal_admission():
    workflow, job, _ = cold_ci_runner()
    event = workflow.get("on", workflow.get(True))  # YAML 1.1 treats 'on' as True.
    assert event["workflow_dispatch"]["inputs"]["foreground_only"] == {
        "description": "Run only installed foreground CLI diagnosis; reuses accepted public/cold evidence, does not requalify it",
        "type": "boolean", "default": False}
    for name in ("passive-process-check", "windows-private-config-tls"):
        assert workflow["jobs"][name]["if"] == "${{ !inputs.bootstrap_only && !inputs.foreground_only && !inputs.storage_only }}"
    assert workflow["jobs"]["windows-user-trial"]["if"] == "${{ inputs.storage_only || (!inputs.bootstrap_only && !inputs.foreground_only) }}"
    assert job["needs"] == "windows-user-trial"
    assert job["if"] == "${{ !inputs.bootstrap_only && !inputs.storage_only && !cancelled() && (inputs.foreground_only || needs.windows-user-trial.result == 'success') }}"
    cold = next(s for s in job["steps"] if s.get("name", "").startswith("Require one native cold"))
    assert cold["if"] == "${{ !inputs.foreground_only }}" and cold["timeout-minutes"] == 21
    cli = next(s for s in job["steps"] if s.get("name", "").startswith("Require installed foreground"))
    assert "if" not in cli and cli["timeout-minutes"] == 4
    # Ordinary PR/manual-default behavior is unchanged; isolated diagnosis never
    # treats skipped accepted cells as newly passing checks.
    for requested, public_result, cancelled, expected in (
            (False, "success", False, True), (False, "failure", False, False),
            (True, "skipped", False, True), (True, "failure", True, False)):
        assert (not cancelled and (requested or public_result == "success")) is expected


@pytest.mark.parametrize("private_source", [False, True])
def test_foreground_cli_child_retains_denied_method_and_exact_known_code_without_network(
        tmp_path, monkeypatch, private_source):
    root = tmp_path
    (root / "cli-port").write_text("8942", encoding="ascii")
    monkeypatch.setenv("WINDOWS_TRIAL_ROOT", str(root))
    monkeypatch.setattr(probe, "require_ci", lambda: None)
    monkeypatch.setattr(sys, "addaudithook", lambda callback: None)
    evidence = cli_public_report()["children"][0]["installed"]
    monkeypatch.setattr(probe, "installed", lambda wheel: evidence)
    monkeypatch.setattr(probe, "cli_material", lambda root: {"synthetic": "a"*64})
    parent = types.ModuleType("amplifier_web")
    parent.__path__ = []
    cli = types.ModuleType("amplifier_web.cli")
    cli.webbrowser = types.SimpleNamespace(open=lambda url: True)
    deployment = types.ModuleType("amplifier_web.deployment")
    deployment.write_private = lambda *args, **kwargs: pytest.fail("No private grant was requested")
    tls = types.ModuleType("amplifier_web.tls")
    tls.ca_fingerprint = lambda home: "00"
    parent.cli, parent.deployment = cli, deployment
    monkeypatch.setitem(sys.modules, "amplifier_web", parent)
    monkeypatch.setitem(sys.modules, "amplifier_web.cli", cli)
    monkeypatch.setitem(sys.modules, "amplifier_web.deployment", deployment)
    monkeypatch.setitem(sys.modules, "amplifier_web.tls", tls)
    observed = []
    original_guard = probe.CliNetworkGuard
    def guard(*args, **kwargs):
        value = original_guard(*args, **kwargs)
        observed.append(value)
        return value
    monkeypatch.setattr(probe, "CliNetworkGuard", guard)
    # Actual stdlib getfqdn source body. Replace ONLY the native resolver call;
    # this test emits no DNS/socket/thread/process, and the real guard denies it.
    def resolver(*args):
        observed[0].audit("socket.gethostbyaddr", args)
    monkeypatch.setattr(socket, "gethostbyaddr", resolver)
    def main(*args, **kwargs):
        assert args == ("amplifier_web",) and kwargs == {"run_name": "__main__"}
        if private_source:
            # The private caller's name, filename and code hash must not escape.
            namespace = {"audit": observed[0].audit}
            exec(compile("def private_customer_function():\n audit('socket.gethostbyname', ('private-host',))",
                         "/private/customer/secret.py", "exec"), namespace)
            namespace["private_customer_function"]()
        else:
            socket.getfqdn("synthetic.invalid")
            print("Local CA: configured\nTLS listener: ready\n00")
    monkeypatch.setattr(probe.runpy, "run_module", main)
    assert probe.cli_child(root / "synthetic.whl", "status") == 1
    child = json.loads((root / "cli-status.json").read_text())
    assert child["status"] == "FAIL" and child["step"] == ("main" if private_source else "status")
    assert child["network"]["unknownAttempts"] == 1
    attempts = child["networkDiagnostics"]["attempts"]
    assert len(attempts) == 1 and attempts[0]["count"] == 1
    assert attempts[0]["event"] == ("socket.gethostbyname" if private_source else "socket.gethostbyaddr")
    assert attempts[0]["source"] == ("other" if private_source else "socket_getfqdn")
    if private_source:
        assert set(attempts[0]) == {"event", "case", "phase", "source", "count"}
    else:
        assert attempts[0]["codeSha256"] == probe.digest(socket.getfqdn.__code__.co_code)
    probe.validate_cli_diagnostics(child)
    public = json.dumps(child)
    assert all(secret not in public for secret in (
        "private_customer_function", "synthetic.invalid", "private-host", "/private", "secret.py"))
    # The old receipt retained only the count; the new one has a discriminating
    # method/known-code fact while preserving the same FAIL.
    # getfqdn catches resolver OSError; the denied event still survives and
    # qualification fails, even when the original library swallows that error.
    assert child["errors"][0]["class"] == ("PermissionError" if private_source else "AssertionError")


@pytest.mark.parametrize("fault", [None, "raw-source", "unknown-hash", "private-class", "raw-message",
    "raw-address", "raw-port", "mismatch-count", "too-many", "private-step", "success-with-error"])
def test_foreground_cli_diagnostics_are_bounded_and_fail_closed(fault):
    child = {"status": "FAIL", "step": "settings_update",
        "network": {"unknownAttempts": 1},
        "errors": [{"class": "PermissionError", "frames": []}],
        "networkDiagnostics": {"attempts": [{"event": "socket.gethostbyname", "source": "other",
            "case": "outside", "phase": "outside", "count": 1}], "overflow": 0}}
    attempt = child["networkDiagnostics"]["attempts"][0]
    if fault == "raw-source": attempt["source"] = "private_customer_function"
    elif fault == "unknown-hash": attempt["codeSha256"] = "a"*64
    elif fault == "private-class": child["errors"][0]["class"] = "CustomerPrivateException"
    elif fault == "raw-message": child["errors"][0]["message"] = "private"
    elif fault == "raw-address": attempt["address"] = "private"
    elif fault == "raw-port": attempt["port"] = 8942
    elif fault == "mismatch-count": child["network"]["unknownAttempts"] = 2
    elif fault == "too-many": child["networkDiagnostics"]["attempts"] *= 33
    elif fault == "private-step": child["step"] = "/private"
    elif fault == "success-with-error": child["status"] = "PASS"
    if fault is None:
        probe.validate_cli_diagnostics(child)
    else:
        with pytest.raises(AssertionError):
            probe.validate_cli_diagnostics(child)


@pytest.mark.parametrize("cli_guard", [False, True])
@pytest.mark.parametrize("fault", [None, "wrong-code", "wrong-module", "wrong-file",
                                  "wrong-host", "wrong-family", "repeat"])
def test_foreground_cli_attested_optional_probe_keeps_real_caller_through_adapter(
        tmp_path, monkeypatch, cli_guard, fault):
    """Execute exact installed probe code with an inert socket, never a bind."""
    guard = (probe.CliNetworkGuard(tmp_path / "network.jsonl", 8942) if cli_guard else
             probe.NetworkGuard(tmp_path / "network.jsonl"))
    streams = []
    class Stream:
        family = socket.AF_INET if fault == "wrong-family" else socket.AF_INET6
        closed = False
        def bind(self, address):
            guard.audit("socket.bind", (self, address))
        def close(self):
            self.closed = True
        def fileno(self):
            return -1 if self.closed else 77
    def create(*args):
        stream = Stream()
        streams.append(stream)
        return stream
    original_frame = sys._getframe
    def frame(depth=0):
        value = original_frame(depth + 1)
        # Native socket.bind is C and contributes no Python caller frame.
        # Remove only this inert transport shim, not the CLI audit adapter.
        if value.f_code is Stream.bind.__code__:
            return value.f_back
        if (value.f_code is probe.CliNetworkGuard.audit.__code__
                and value.f_back.f_code is Stream.bind.__code__):
            return types.SimpleNamespace(f_code=value.f_code, f_locals=value.f_locals,
                f_globals=value.f_globals, f_lineno=value.f_lineno, f_back=value.f_back.f_back)
        return value
    monkeypatch.setattr(sys, "_getframe", frame)
    namespace = {"__name__": "urllib3.util.connection", "__file__": str(guard.probe_path),
        "socket": types.SimpleNamespace(has_ipv6=True, socket=create, AF_INET6=socket.AF_INET6)}
    if fault == "wrong-module":
        namespace["__name__"] = "private_unknown_module"
    elif fault == "wrong-file":
        namespace["__file__"] = str(tmp_path / "private_unknown_module.py")
    code = guard.probe_code.replace(co_name="private_unknown_function") if fault == "wrong-code" else guard.probe_code
    function = types.FunctionType(code, namespace)
    assert function("::2" if fault == "wrong-host" else "::1") is False
    if fault == "repeat":
        assert function("::1") is False
    assert all(stream.closed for stream in streams) and len(streams) == (2 if fault == "repeat" else 1)
    assert guard.probes == (1 if fault in {None, "repeat"} else 0)
    assert guard.blocked == (0 if fault is None else 1)
    log = tmp_path / "network.jsonl"
    assert log.exists() is (fault is not None)
    if fault:
        assert "private_unknown" not in log.read_text()


@pytest.mark.parametrize("unknown", [False, True])
def test_foreground_cli_actual_outer_cleanup_retains_unknown_owned_jobs(tmp_path, monkeypatch, unknown):
    candidate = tmp_path / "candidate.json"
    probe.write_json(candidate, {"candidate": {"head": "a"*40, "tree": "b"*40}})
    receipt = tmp_path / "receipt.json"
    seen = []
    monkeypatch.setattr(probe, "require_ci", lambda: None)
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(probe, "canonical_temp", lambda path: Path(path))
    monkeypatch.setattr(probe, "private_directory", lambda path, root: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(probe, "sanitize", lambda root: seen.append(root))
    class Timeout:
        async def __aenter__(self): pass
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(probe.asyncio, "timeout", lambda seconds: Timeout())
    monkeypatch.setattr(probe.asyncio, "run", immediate)
    async def hosts(root, wheel, report):
        report["outerJobsSettled"] = not unknown
        (root / "private-marker").write_text("synthetic")
        raise AssertionError("Synthetic failed qualification, never product execution")
    monkeypatch.setattr(probe, "cli_hosts", hosts)
    assert probe.cli_run(tmp_path / "wheel.whl", receipt, candidate) == 1
    result = json.loads(receipt.read_text())
    assert result["status"] == "FAIL" and result["outerJobsSettled"] is not unknown
    assert result["scratchRemoved"] is not unknown
    assert seen[0].exists() is unknown
    if unknown:
        assert (seen[0] / "private-marker").read_text() == "synthetic"
    probe.validate_cli_public(result)


def storage_observation(label):
    reader, scope = probe.STORAGE_READERS[label]
    ordinary = label in {"ordinary_view", "ordinary_naming"}
    rollback = label in probe.STORAGE_ROLLBACK_SCOPES
    return {"schema": 1, "reader": reader, "scope": scope, "platform": "windows",
            "result": "raised" if ordinary or rollback else "returned",
            "facts": {"errors": [{"class": "PermissionError", "errno": 13, "winerror": 5, "frames": []}]
                      if ordinary else [{"class": "other", "frames": []}] if rollback else [],
                      "truncated": False}}


def storage_properties(label):
    return [("publication_sharing", json.dumps(storage_observation(label))),
            ("publication_sharing_verified", True)]


def storage_public_report():
    return {"schema": 1, "qualification": "installed-storage-sharing-not-runtime-or-device",
            "publicAcceptance": False, "candidate": {"head": "a"*40, "tree": "b"*40},
            "status": "PASS", "stage": "complete", "sourceBound": True, "probeSha256": "c"*64,
            "testSources": {name: "d"*64 for name in probe.STORAGE_FILES},
            "installation": {"wheelSha256": "e"*64, "verifiedMembers": 101},
            "installedStable": True, "productionModules": 7,
            "selection": native_result(results=probe.StorageResults()),
            "network": {"blockedOptionalIpv6Probes": 0, "unknownAttempts": 0,
                        "infrastructureSocketpairBinds": 1},
            "processAttempts": 0, "scratchRemoved": True}


@pytest.mark.parametrize("fault", [None, "missing", "unexpected", "duplicate", "no-call",
                                  "skipped", "failed", "teardown", "xpass",
                                  "collection-error", "exit-code"])
def test_storage_controls_require_eleven_exact_calls_no_skips_or_collection_errors(fault):
    result = native_result(fault, results=probe.StorageResults())
    assert result["status"] == ("PASS" if fault is None else "FAIL")
    assert set(result["cases"]) == {"held_view", "held_naming", "released_view", "released_naming",
                                   "held_resource", "ordinary_view", "ordinary_naming",
                                   "rollback_restore", "rollback_remove", "restore_refused", "remove_refused"}
    report = storage_public_report()
    report["selection"] = result
    if fault:
        with pytest.raises(AssertionError):
            probe.validate_storage_public(report)
        report["status"] = "FAIL"
    assert probe.validate_storage_public(report) is report


def test_storage_staging_and_selection_leave_default_native_api_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "private_directory",
                        lambda path, root: path.mkdir(parents=True, exist_ok=False))
    target, hashes = probe._stage_control_files(tmp_path, ROOT / "tests", probe.STORAGE_FILES)
    assert set(hashes) == {"conftest.py", "test_windows_read_files.py"}
    assert set(path.name for path in target.iterdir()) == {"tests"}
    assert set(path.name for path in (target / "tests").iterdir()) == set(probe.STORAGE_FILES)
    for name, fingerprint in hashes.items():
        assert fingerprint == probe.digest((ROOT / "tests" / name).read_bytes())
        assert (target / "tests" / name).read_bytes() == (ROOT / "tests" / name).read_bytes()
    assert probe.NativeResults.controls is probe.NATIVE_CONTROLS
    assert len(probe.NativeResults().node_labels) == len(probe.NATIVE_CONTROLS) == 18
    assert not set(probe.STORAGE_CONTROLS.values()) & set(probe.NATIVE_CONTROLS.values())
    assert probe.validate_public(report())["publicAcceptance"] is False
    assert list(probe.STORAGE_CONTROLS.values()) == [
        "test_windows_read_files.py::test_service_publication_with_real_mutable_reader[held-view]",
        "test_windows_read_files.py::test_service_publication_with_real_mutable_reader[held-naming]",
        "test_windows_read_files.py::test_service_publication_with_real_mutable_reader[released-view]",
        "test_windows_read_files.py::test_service_publication_with_real_mutable_reader[released-naming]",
        "test_windows_read_files.py::test_service_publication_during_held_immutable_resource",
        "test_windows_read_files.py::test_service_publication_with_real_mutable_reader[ordinary-view]",
        "test_windows_read_files.py::test_service_publication_with_real_mutable_reader[ordinary-naming]",
        "test_windows_read_files.py::test_service_view_rollback_with_real_presentation_reader[restore-held]",
        "test_windows_read_files.py::test_service_view_rollback_with_real_presentation_reader[remove-held]",
        "test_windows_read_files.py::test_service_view_rollback_with_real_presentation_reader[restore-refused]",
        "test_windows_read_files.py::test_service_view_rollback_with_real_presentation_reader[remove-refused]",
    ]
    tree = ast.parse((ROOT / "tests/test_windows_read_files.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)}
    assert "test_service_publication_during_held_immutable_resource" in functions
    mutable = functions["test_service_publication_with_real_mutable_reader"]
    parameters = [dec for dec in mutable.decorator_list if isinstance(dec, ast.Call)
                  and isinstance(dec.func, ast.Attribute) and dec.func.attr == "parametrize"]
    assert len(parameters) == 1 and ast.literal_eval(parameters[0].args[0]) == "scope,reader_kind"
    cells = parameters[0].args[1].elts
    assert all(isinstance(cell, ast.Call) and isinstance(cell.func, ast.Attribute)
               and cell.func.attr == "param" for cell in cells)
    assert [(tuple(ast.literal_eval(arg) for arg in cell.args),
             {keyword.arg: ast.literal_eval(keyword.value) for keyword in cell.keywords}) for cell in cells] == [
        ((scope, reader), {"id": scope + "-" + reader})
        for scope in ("held", "released", "ordinary") for reader in ("view", "naming")]
    rollback = functions["test_service_view_rollback_with_real_presentation_reader"]
    rollback_params = [dec for dec in rollback.decorator_list if isinstance(dec, ast.Call)
                       and isinstance(dec.func, ast.Attribute) and dec.func.attr == "parametrize"]
    assert len(rollback_params) == 1
    assert ast.literal_eval(rollback_params[0].args[0]) == "scenario"
    assert ast.literal_eval(rollback_params[0].args[1]) == [
        "restore-held", "remove-held", "restore-refused", "remove-refused"]


def test_storage_route_uses_only_selected_nodes_without_worker_or_cli_journey(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(probe, "storage_run",
                        lambda *args: seen.append(args) or 1)
    def forbidden(*args):
        raise AssertionError("unrelated journey")
    for name in ("run", "cli_run", "native_controls", "host", "worker_entry"):
        monkeypatch.setattr(probe, name, forbidden)
    wheel, receipt, candidate = (tmp_path / name for name in ("candidate.whl", "storage.json", "candidate.json"))
    monkeypatch.setattr(sys, "argv", [SPEC.origin, "--storage-run", str(wheel),
                                    "--receipt", str(receipt), "--candidate-receipt", str(candidate)])
    assert probe.main() == 1
    assert seen == [(wheel.resolve(), receipt.resolve(), candidate.resolve())]


def test_existing_run_route_still_dispatches_default_journey(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(probe, "run", lambda *args: seen.append(args) or 0)
    monkeypatch.setattr(probe, "storage_run", lambda *args: pytest.fail("storage route not selected"))
    wheel, receipt = tmp_path / "candidate.whl", tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", [SPEC.origin, "--run", str(wheel), "--receipt", str(receipt)])
    assert probe.main() == 0
    assert seen == [(wheel.resolve(), receipt.resolve())]


@pytest.mark.parametrize("extra", [[], ["--receipt", "result.json"], ["--candidate-receipt", "candidate.json"],
                                  ["--receipt", "result.json", "--candidate-receipt", "candidate.json", "--run", "other.whl"]])
def test_storage_route_refuses_missing_receipts_and_mixed_routes(monkeypatch, extra):
    monkeypatch.setattr(sys, "argv", [SPEC.origin, "--storage-run", "candidate.whl", *extra])
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2


def test_storage_result_observer_uses_canonical_facts_without_private_pytest_prose():
    results = probe.StorageResults()
    node = next(iter(results.node_labels))
    error = PermissionError(13, "PRIVATE-TOKEN", "/private/path")
    error.winerror = 32
    assert results.pytest_runtest_makereport(
        types.SimpleNamespace(nodeid=node),
        types.SimpleNamespace(when="call", excinfo=types.SimpleNamespace(value=error))) is None
    assert results.failure_facts == [{"case": "held_view", "phase": "call", "facts": {
        "errors": [{"class": "PermissionError", "errno": 13, "winerror": 32, "frames": []}],
        "truncated": False}}]
    report = storage_public_report()
    report.update(status="FAIL", selection=results.finish(1))
    probe.validate_storage_public(report)
    assert "PRIVATE" not in json.dumps(report) and "/private" not in json.dumps(report)


def test_storage_collection_skip_and_failure_fact_overflow_never_create_pass():
    results = probe.StorageResults()
    results.pytest_collectreport(types.SimpleNamespace(failed=False, skipped=True))
    selection = native_result(results=results)
    assert selection["status"] == "FAIL" and selection["collectionExact"] is False
    node = next(iter(results.node_labels))
    for _ in range(36):
        results.pytest_runtest_makereport(types.SimpleNamespace(nodeid=node),
            types.SimpleNamespace(when="call", excinfo=types.SimpleNamespace(value=RuntimeError("PRIVATE"))))
    assert len(results.failure_facts) == 33
    report = storage_public_report()
    report.update(status="FAIL", selection=results.finish(1))
    probe.validate_storage_public(report)
    report["selection"]["failureFacts"].append(copy.deepcopy(results.failure_facts[0]))
    with pytest.raises(AssertionError):
        probe.validate_storage_public(report)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(message="PRIVATE"),
    lambda r: r.update(qualification="cold-runtime-pass"),
    lambda r: r.update(schema=True),
    lambda r: r.update(scratchRemoved=False),
    lambda r: r.update(sourceBound=False),
    lambda r: r.update(installedStable=False),
    lambda r: r.update(productionModules=True),
    lambda r: r.update(processAttempts=1),
    lambda r: r["network"].update(unknownAttempts=1),
    lambda r: r["candidate"].update(head="PRIVATE"),
    lambda r: r["testSources"].update(extra="a"*64),
    lambda r: r["testSources"].pop("conftest.py"),
    lambda r: r["selection"]["cases"]["held_view"].update(skips=1),
    lambda r: r["selection"].update(collectionExact=False),
    lambda r: r["installation"].update(path="/private"),
])
def test_storage_public_validator_rejects_false_pass_and_private_fields(mutation):
    report = storage_public_report()
    probe.validate_storage_public(report)
    mutation(report)
    with pytest.raises((AssertionError, TypeError, KeyError, ValueError)):
        probe.validate_storage_public(report)


@pytest.mark.parametrize("mutation", [
    lambda f: f.update(message="PRIVATE"),
    lambda f: f.update(case="private-test-name"),
    lambda f: f.update(phase="private-phase"),
    lambda f: f["facts"].update(secret="PRIVATE"),
    lambda f: f["facts"]["errors"][0].update(message="/private/path"),
    lambda f: f["facts"]["errors"][0].update(winerror=True),
    lambda f: f["facts"]["errors"][0].update(frames=[{"source": "/private", "offset": 0}]),
])
def test_storage_public_validator_refuses_malformed_canonical_failure_facts(mutation):
    report = storage_public_report()
    report["status"] = report["selection"]["status"] = "FAIL"
    fact = {"case": "held_view", "phase": "call", "facts": {
        "errors": [{"class": "PermissionError", "errno": 13, "winerror": 32, "frames": []}],
        "truncated": False}}
    report["selection"]["failureFacts"] = [fact]
    probe.validate_storage_public(report)
    mutation(fact)
    with pytest.raises((AssertionError, TypeError, KeyError, ValueError)):
        probe.validate_storage_public(report)


def test_storage_selection_invokes_only_staged_cells_and_asyncio_plugin(tmp_path, monkeypatch):
    from contextlib import nullcontext
    observed = []
    monkeypatch.setattr(probe, "control_socketpairs", lambda guard: nullcontext())
    def main(arguments, plugins):
        observed.append(arguments)
        results, = plugins
        assert isinstance(results, probe.StorageResults)
        model = probe.StorageResults()
        nodes = list(model.node_labels)
        results.pytest_collection_finish(types.SimpleNamespace(
            items=[types.SimpleNamespace(nodeid=node) for node in nodes]))
        for node in nodes:
            for phase in ("setup", "call", "teardown"):
                results.pytest_runtest_logreport(types.SimpleNamespace(
                    nodeid=node, when=phase, outcome="passed",
                    user_properties=storage_properties(results.node_labels[node]) if phase == "call" else []))
        return 0
    monkeypatch.setattr(pytest, "main", main)
    target = tmp_path / "native-controls"
    result = probe.storage_selection(tmp_path, target, None)
    assert result["status"] == "PASS"
    args, = observed
    assert args[-11:] == [str(target / "tests" / node) for node in probe.STORAGE_CONTROLS.values()]
    assert "pytest_asyncio.plugin" in args and "aiohttp.pytest_plugin" not in args
    assert args[args.index("--rootdir") + 1] == args[args.index("--confcutdir") + 1] == str(target)


@pytest.mark.parametrize("event", ["subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn", "os.exec"])
def test_storage_guard_refuses_process_attempt_without_arguments_or_effects(event):
    guard = object.__new__(probe.StorageGuard)
    guard.process_attempts = 0
    with pytest.raises(PermissionError, match="Storage control process boundary"):
        guard.process_audit(event, ("PRIVATE executable", "PRIVATE arguments"))
    assert guard.process_attempts == 1
    assert probe.StorageGuard.audit is probe.NetworkGuard.audit


@pytest.mark.parametrize("fault", [None, "candidate", "skipped", "malformed", "cleanup",
                                  "source-changed", "source-missing", "installed-changed", "network", "process"])
def test_storage_run_models_source_binding_fail_closed_and_private_cleanup(tmp_path, monkeypatch, fault):
    """Run harness control flow with inert audit/pytest/install boundaries, not Windows cells."""
    candidate_path, receipt = tmp_path / "candidate.json", tmp_path / "storage.json"
    candidate = {"head": "a"*40, "tree": "b"*40}
    probe.write_json(candidate_path, {"candidate": candidate})
    hooks, roots, audited, selected = [], [], [], []
    monkeypatch.setattr(probe, "sys", types.SimpleNamespace(
        flags=types.SimpleNamespace(isolated=True, dont_write_bytecode=True, optimize=0),
        path=[], addaudithook=hooks.append))
    monkeypatch.setattr(probe, "require_ci", lambda: None)
    monkeypatch.setattr(probe, "candidate", lambda source: (
        {**candidate, "head": "f"*40} if fault == "candidate" else candidate))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(probe, "canonical_temp", Path)
    monkeypatch.setattr(probe, "private_directory", lambda path, root: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(probe, "sanitize", roots.append)
    guard = types.SimpleNamespace(probes=0, blocked=0, pair_binds=1, process_attempts=0,
                                  audit=lambda *args: None, process_audit=lambda *args: None,
                                  verify=lambda: None)
    monkeypatch.setattr(probe, "StorageGuard", lambda log: guard)
    def installed(wheel):
        audited.append(wheel)
        return {"wheelSha256": ("f" if fault == "installed-changed" and len(audited) == 2 else "e")*64,
                "verifiedMembers": 101}
    monkeypatch.setattr(probe, "installed", installed)
    monkeypatch.setattr(probe, "storage_imports", lambda: 7)
    def selection(root, target, supplied_guard):
        selected.append(target)
        assert supplied_guard is guard and Path.cwd() == root
        assert len(hooks) == 2 and len(audited) == 1
        print("PRIVATE pytest detail /private/path")
        if fault == "source-changed":
            (target / "tests/conftest.py").write_text("PRIVATE changed source")
        if fault == "source-missing":
            (target / "tests/conftest.py").unlink()
        if fault == "network":
            guard.blocked = 1
        if fault == "process":
            guard.process_attempts = 1
        result = native_result("skipped" if fault == "skipped" else None, results=probe.StorageResults())
        if fault == "malformed":
            result["failureFacts"] = [{"case": "held_view", "phase": "call",
                                      "facts": {"message": "PRIVATE"}}]
        return result
    monkeypatch.setattr(probe, "storage_selection", selection)
    if fault == "cleanup":
        def refuse(path):
            raise PermissionError("PRIVATE cleanup")
        monkeypatch.setattr(probe, "shutil", types.SimpleNamespace(rmtree=refuse))
    before = Path.cwd()
    assert probe.storage_run(tmp_path / "candidate.whl", receipt, candidate_path) == (0 if fault is None else 1)
    assert Path.cwd() == before
    result = json.loads(receipt.read_text(encoding="utf-8"))
    probe.validate_storage_public(result)
    assert result["status"] == ("PASS" if fault is None else "FAIL")
    assert "PRIVATE" not in receipt.read_text() and str(tmp_path) not in receipt.read_text()
    assert json.loads(candidate_path.read_text()) == {"candidate": candidate}
    if fault == "candidate":
        assert not roots and not audited and not selected and not result["sourceBound"]
    else:
        assert len(roots) == len(selected) == 1 and len(audited) == 2
        assert roots[0].exists() == (fault == "cleanup")
        assert result["scratchRemoved"] == (fault != "cleanup")
        if fault == "cleanup":
            assert "PRIVATE" in (roots[0] / "diagnostics-storage.log").read_text()
        if fault == "malformed":
            assert result["selection"] is None


def test_storage_route_does_not_remove_uncreated_scratch_or_overwrite_inputs(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate.json"
    probe.write_json(candidate, {"candidate": {"head": "a"*40, "tree": "b"*40}})
    original = candidate.read_bytes()
    assert probe.storage_run(tmp_path / "wheel.whl", candidate, candidate) == 1
    assert candidate.read_bytes() == original
    root = tmp_path / ("us-" + "a"*12)
    root.mkdir()
    marker = root / "owned-elsewhere"
    marker.write_text("PRIVATE")
    monkeypatch.setattr(probe, "sys", types.SimpleNamespace(
        flags=types.SimpleNamespace(isolated=True, dont_write_bytecode=True, optimize=0), path=[]))
    monkeypatch.setattr(probe, "require_ci", lambda: None)
    monkeypatch.setattr(probe, "candidate", lambda source: json.loads(original)["candidate"])
    monkeypatch.setattr(probe, "canonical_temp", lambda value: tmp_path)
    monkeypatch.setattr(probe, "uuid", types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex="a"*32)))
    monkeypatch.setenv("TEMP", str(tmp_path))
    receipt = tmp_path / "result.json"
    assert probe.storage_run(tmp_path / "wheel.whl", receipt, candidate) == 1
    assert marker.read_text() == "PRIVATE"
    assert json.loads(receipt.read_text())["scratchRemoved"] is False


@pytest.mark.parametrize("fault", [None, "checkout", "unrecorded", "missing", "fake"])
def test_storage_import_audit_rejects_checkout_or_unowned_production_modules(tmp_path, monkeypatch, fault):
    prefix = tmp_path / "installed"
    package = prefix / "amplifier_web"
    root = types.ModuleType("amplifier_web")
    root.__file__ = str(package / "__init__.py")
    child = types.ModuleType("amplifier_web.service")
    child.__file__ = str(package / "service.py")
    files = ["amplifier_web/__init__.py", "amplifier_web/service.py"]
    if fault == "checkout":
        child.__file__ = str(tmp_path / "checkout/amplifier_web/service.py")
        files.append("../checkout/amplifier_web/service.py")
    elif fault == "unrecorded":
        files.pop()
    elif fault == "missing":
        del child.__file__
    elif fault == "fake":
        child = types.SimpleNamespace(__file__=child.__file__)
    monkeypatch.setattr(probe, "sys", types.SimpleNamespace(
        prefix=str(prefix), modules={"amplifier_web": root, "amplifier_web.service": child}))
    monkeypatch.setattr(probe, "metadata", types.SimpleNamespace(distribution=lambda name:
        types.SimpleNamespace(files=files, locate_file=lambda path: prefix / path)))
    if fault is None:
        assert probe.storage_imports() == 2
    else:
        with pytest.raises((AssertionError, KeyError)):
            probe.storage_imports()


@pytest.mark.parametrize("label", tuple(probe.STORAGE_CONTROLS))
def test_storage_publication_properties_are_exact_case_bound_deep_copies(label):
    original = storage_observation(label)
    # Actual production validator builds new errors/frame containers as well.
    if original["facts"]["errors"]:
        original["facts"]["errors"][0]["frames"] = [{"source": "storage.atomic", "offset": 8}]
    copied = probe.validate_publication_sharing(original, label)
    assert copied == original and copied is not original and copied["facts"] is not original["facts"]
    if copied["facts"]["errors"]:
        copied["facts"]["errors"][0]["frames"][0]["offset"] = 9
        assert original["facts"]["errors"][0]["frames"][0]["offset"] == 8
    properties = [("publication_sharing", json.dumps(original)), ("publication_sharing_verified", True)]
    captured = probe.publication_properties(properties, label)
    assert captured == original
    properties[0] = ("publication_sharing", "PRIVATE")
    assert captured == original  # Later pytest property mutation cannot rewrite retained facts.
    results = probe.StorageResults()
    result = native_result(results=results)
    case = result["cases"][label]
    assert case["status"] == "PASS" and case["publicationSharingVerified"] is True
    case["publicationSharing"]["facts"]["errors"].append({"class": "other", "frames": []})
    assert results.finish(0)["cases"][label]["publicationSharing"] == storage_observation(label)
    assert all(row["publicationSharingVerified"] is True for row in results.finish(0)["cases"].values())


@pytest.mark.parametrize("fault", [
    "missing", "missing-verified", "unverified", "integer-verified", "string-verified", "extra-property",
    "duplicate-property", "dict-property", "bad-json", "oversized", "duplicate-json-key", "secret", "schema",
    "reader", "scope", "platform", "posix", "result", "empty-raised", "wrong-class", "wrong-errno",
    "wrong-winerror", "bool-winerror", "truncated", "private-frame", "private-facts",
])
def test_storage_publication_bad_properties_cannot_pass_or_export_private_values(fault):
    def changed(label):
        properties = storage_properties(label)
        if label != "ordinary_view":
            return properties
        value = storage_observation(label)
        if fault == "missing":
            return []
        if fault == "missing-verified":
            return properties[:1]
        if fault in {"unverified", "integer-verified", "string-verified"}:
            return [properties[0], ("publication_sharing_verified",
                                   {"unverified": False, "integer-verified": 1, "string-verified": "True"}[fault])]
        if fault == "extra-property":
            return [*properties, ("PRIVATE-property", "/private/path")]
        if fault == "duplicate-property":
            return [properties[0], properties[0]]
        if fault == "dict-property":
            return [("publication_sharing", value), properties[1]]
        if fault in {"bad-json", "oversized", "duplicate-json-key"}:
            raw = {"bad-json": "{PRIVATE", "oversized": "x"*16385,
                   "duplicate-json-key": json.dumps(value)[:-1] + ', "schema": 1}'}[fault]
            return [("publication_sharing", raw), properties[1]]
        if fault == "secret":
            value["message"] = "/private/path PRIVATE-TOKEN"
        elif fault == "schema":
            value["schema"] = True
        elif fault == "reader":
            value["reader"] = "naming.legacy"
        elif fault == "scope":
            value["scope"] = "released"
        elif fault == "platform":
            value["platform"] = "PRIVATE"
        elif fault == "posix":
            value.update(platform="posix", result="returned", facts={"errors": [], "truncated": False})
        elif fault == "result":
            value["result"] = "returned"
        elif fault == "empty-raised":
            value["facts"]["errors"] = []
        elif fault == "wrong-class":
            value["facts"]["errors"][0]["class"] = "OSError"
        elif fault == "wrong-errno":
            value["facts"]["errors"][0]["errno"] = 22
        elif fault == "wrong-winerror":
            value["facts"]["errors"][0]["winerror"] = 32
        elif fault == "bool-winerror":
            value["facts"]["errors"][0]["winerror"] = True
        elif fault == "truncated":
            value["facts"]["truncated"] = True
        elif fault == "private-frame":
            value["facts"]["errors"][0]["frames"] = [{"source": "/private/path", "offset": 0}]
        elif fault == "private-facts":
            value["facts"]["message"] = "PRIVATE"
        return [("publication_sharing", json.dumps(value)), properties[1]]
    selection = native_result(results=probe.StorageResults(), properties=changed)
    assert selection["status"] == selection["cases"]["ordinary_view"]["status"] == "FAIL"
    assert selection["cases"]["ordinary_view"]["callsPassed"] == 1  # Original pytest pass stays observed.
    assert all(selection["cases"][label]["status"] == "PASS" for label in probe.STORAGE_CONTROLS if label != "ordinary_view")
    report = storage_public_report()
    report.update(status="FAIL", selection=selection)
    probe.validate_storage_public(report)
    assert "PRIVATE" not in json.dumps(report) and "/private/path" not in json.dumps(report)
    report["status"] = "PASS"
    with pytest.raises(AssertionError):
        probe.validate_storage_public(report)


@pytest.mark.parametrize("label", ("held_view", "held_naming", "released_view", "released_naming", "held_resource"))
def test_storage_production_cells_require_returned_empty_facts(label):
    def properties(case):
        value = storage_observation(case)
        if case == label:
            value.update(result="raised", facts=storage_observation("ordinary_view")["facts"])
        return [("publication_sharing", json.dumps(value)), ("publication_sharing_verified", True)]
    selection = native_result(results=probe.StorageResults(), properties=properties)
    assert selection["status"] == selection["cases"][label]["status"] == "FAIL"
    assert selection["cases"][label]["publicationSharing"]["result"] == "raised"


@pytest.mark.parametrize("mutation", [
    lambda c: c.pop("publicationSharing"),
    lambda c: c.update(publicationSharing=None, publicationSharingVerified=False),
    lambda c: c.update(publicationSharingVerified=1),
    lambda c: c.update(publicationSharingVerified=False),
    lambda c: c["publicationSharing"].update(message="PRIVATE"),
    lambda c: c["publicationSharing"].update(reader="naming.legacy"),
    lambda c: c["publicationSharing"].update(scope="released"),
    lambda c: c["publicationSharing"].update(platform="posix"),
    lambda c: c["publicationSharing"]["facts"]["errors"][0].update(winerror=32),
])
def test_storage_public_receipt_revalidates_each_successful_discriminator(mutation):
    report = storage_public_report()
    mutation(report["selection"]["cases"]["ordinary_view"])
    with pytest.raises((AssertionError, KeyError, TypeError, ValueError)):
        probe.validate_storage_public(report)


@pytest.mark.parametrize("missing", tuple(probe.STORAGE_FILES))
def test_storage_source_staging_cannot_certify_missing_fixture(tmp_path, monkeypatch, missing):
    source = tmp_path / "source"
    source.mkdir()
    for name in probe.STORAGE_FILES:
        if name != missing:
            (source / name).write_bytes((ROOT / "tests" / name).read_bytes())
    monkeypatch.setattr(probe, "private_directory", lambda path, root: path.mkdir(parents=True))
    with pytest.raises(FileNotFoundError):
        probe._stage_control_files(tmp_path / "scratch", source, probe.STORAGE_FILES)


def test_storage_unknown_case_and_other_phase_properties_are_not_published():
    results = probe.StorageResults()
    result = native_result(results=results)
    assert result["status"] == "PASS"
    node = next(iter(results.node_labels))
    for nodeid, phase in ((node, "teardown"), ("PRIVATE/node.py::private_test", "call")):
        results.pytest_runtest_logreport(types.SimpleNamespace(
            nodeid=nodeid, when=phase, outcome="passed", user_properties=[
                ("publication_sharing", "PRIVATE"), ("publication_sharing_verified", True)]))
    changed = results.finish(0)
    assert changed["status"] == "FAIL"  # Duplicate/unexpected pytest events are still failures.
    assert "PRIVATE" not in json.dumps(changed)
    assert changed["cases"]["held_view"]["publicationSharing"] == storage_observation("held_view")


@pytest.mark.parametrize("label", tuple(probe.STORAGE_CONTROLS))
@pytest.mark.parametrize("outcome", ("passed", "failed"))
def test_storage_unverified_observation_survives_later_assertion_without_pass(label, outcome):
    """Synthetic reports only: retention proves neither the native result nor its cause."""
    results = probe.StorageResults()
    properties = storage_properties(label)[:1]
    expected = storage_observation(label)
    assert probe.publication_properties(properties, label) is None  # Default stays strict.
    assert probe.publication_properties(properties, label, require_verified=False) == expected
    nodes = list(results.node_labels)
    results.pytest_collection_finish(types.SimpleNamespace(
        items=[types.SimpleNamespace(nodeid=node) for node in nodes]))
    for node in nodes:
        case_label = results.node_labels[node]
        for phase in ("setup", "call", "teardown"):
            failed = case_label == label and phase == "call" and outcome == "failed"
            if failed:
                results.pytest_runtest_makereport(types.SimpleNamespace(nodeid=node),
                    types.SimpleNamespace(when=phase,
                        excinfo=types.SimpleNamespace(value=AssertionError("PRIVATE later assertion"))))
            results.pytest_runtest_logreport(types.SimpleNamespace(
                nodeid=node, when=phase, outcome="failed" if failed else "passed",
                user_properties=(properties if case_label == label else storage_properties(case_label))
                                if phase == "call" else []))
    # Mutation of the source property and returned copy cannot change retained facts.
    properties[0] = ("publication_sharing", "PRIVATE invalid late replacement")
    selection = results.finish(1 if outcome == "failed" else 0)
    case = selection["cases"][label]
    assert case["publicationSharing"] == expected
    assert case["publicationSharingVerified"] is False
    assert case["status"] == selection["status"] == "FAIL"
    assert case["callsPassed"] == (1 if outcome == "passed" else 0)
    assert case["failures"] == (1 if outcome == "failed" else 0)
    assert all(row["status"] == "PASS" for name, row in selection["cases"].items() if name != label)
    if outcome == "failed":
        assert selection["failureFacts"] == [{"case": label, "phase": "call", "facts": {
            "errors": [{"class": "AssertionError", "frames": []}], "truncated": False}}]
    report = storage_public_report()
    report.update(status="FAIL", selection=selection)
    probe.validate_storage_public(report)
    assert "PRIVATE" not in json.dumps(report)
    case["publicationSharing"]["facts"]["errors"].append({"class": "other", "frames": []})
    assert results.finish(1)["cases"][label]["publicationSharing"] == expected
    case["publicationSharing"] = expected
    case["status"] = "PASS"
    # Even a successful pytest call with the expected Windows facts cannot pass
    # without the fixture's explicit final verification.
    with pytest.raises(AssertionError):
        probe.validate_storage_public(report)


@pytest.mark.parametrize("fault", ("malformed", "private", "duplicate", "unknown", "false-flag", "integer-flag"))
def test_storage_partial_capture_discards_malformed_or_private_observations(fault):
    label = "ordinary_view"
    properties = storage_properties(label)[:1]
    if fault == "malformed":
        properties[0] = ("publication_sharing", "{PRIVATE")
    elif fault == "private":
        value = storage_observation(label)
        value["facts"]["errors"][0]["message"] = "/private/path PRIVATE"
        properties[0] = ("publication_sharing", json.dumps(value))
    elif fault == "duplicate":
        properties.append(properties[0])
    elif fault == "unknown":
        properties.append(("PRIVATE-property", "/private/path"))
    else:
        properties.append(("publication_sharing_verified", False if fault == "false-flag" else 1))
    assert probe.publication_properties(properties, label, require_verified=False) is None
    selection = native_result(results=probe.StorageResults(), properties=lambda name:
                              properties if name == label else storage_properties(name))
    case = selection["cases"][label]
    assert case["publicationSharing"] is None and case["publicationSharingVerified"] is False
    assert case["status"] == selection["status"] == "FAIL"
    report = storage_public_report()
    report.update(status="FAIL", selection=selection)
    probe.validate_storage_public(report)
    assert "PRIVATE" not in json.dumps(report) and "/private/path" not in json.dumps(report)


@pytest.mark.parametrize("label", tuple(probe.STORAGE_CONTROLS))
@pytest.mark.parametrize("winerror", (None, 5, 32))
def test_storage_baselines_and_production_results_cannot_be_substituted(label, winerror):
    """Synthetic records: Win32 never qualifies either the fix or the Win5 baseline."""
    observed = storage_observation(label)
    observed.update(result="returned" if winerror is None else "raised", facts={
        "errors": [] if winerror is None else [
            {"class": "PermissionError", "errno": 13, "winerror": winerror, "frames": []}],
        "truncated": False})
    def properties(case):
        return ([("publication_sharing", json.dumps(observed)), ("publication_sharing_verified", True)]
                if case == label else storage_properties(case))
    baseline = label in {"ordinary_view", "ordinary_naming"}
    expected = (winerror == 5 if baseline else
                winerror is None and label not in probe.STORAGE_ROLLBACK_SCOPES)
    selection = native_result(results=probe.StorageResults(), properties=properties)
    case = selection["cases"][label]
    assert (case["status"] == "PASS") is expected
    assert (selection["status"] == "PASS") is expected
    assert case["publicationSharingVerified"] is True
    assert case["publicationSharing"] == observed  # Keep failures without relabeling them.
    report = storage_public_report()
    report.update(status="PASS" if expected else "FAIL", selection=selection)
    probe.validate_storage_public(report)
    assert len(json.dumps(report).encode("utf-8")) <= 65536
    if not expected:
        case["status"] = selection["status"] = report["status"] = "PASS"
        with pytest.raises(AssertionError):
            probe.validate_storage_public(report)


@pytest.mark.parametrize("label", tuple(probe.STORAGE_CONTROLS))
def test_storage_ordinary_scope_is_bound_only_to_ordinary_baseline_cells(label):
    observation = storage_observation(label)
    observation["scope"] = "held" if label.startswith("ordinary_") else "ordinary"
    assert probe.validate_publication_sharing(observation, label) is None
    properties = [("publication_sharing", json.dumps(observation)), ("publication_sharing_verified", True)]
    assert probe.publication_properties(properties, label) is None
    assert probe.publication_properties(properties[:1], label, require_verified=False) is None


@pytest.mark.parametrize("label", tuple(probe.STORAGE_ROLLBACK_SCOPES))
@pytest.mark.parametrize("fault", [None, "returned", "os-error", "truncated", "unverified", "wrong-scope", "private"])
def test_storage_rollback_cells_require_verified_original_failure_and_exact_scenario(label, fault):
    import sqlite3
    from amplifier_web.worker_diagnostics import command_failure_facts
    original = sqlite3.OperationalError("PRIVATE injected SQLite failure")
    value = storage_observation(label)
    value["facts"] = command_failure_facts(original)
    assert value["facts"] == {"errors": [{"class": "other", "frames": []}], "truncated": False}
    if fault == "returned":
        value.update(result="returned", facts={"errors": [], "truncated": False})
    elif fault == "os-error":
        value["facts"] = command_failure_facts(OSError(5, "PRIVATE rollback refusal"))
    elif fault == "truncated":
        value["facts"]["truncated"] = True
    elif fault == "wrong-scope":
        value["scope"] = "held"
    elif fault == "private":
        value["facts"]["errors"][0]["message"] = "/private/path"
    properties = [("publication_sharing", json.dumps(value))]
    if fault != "unverified":
        properties.append(("publication_sharing_verified", True))
    selection = native_result(results=probe.StorageResults(), properties=lambda case:
                              properties if case == label else storage_properties(case))
    assert (selection["status"] == "PASS") == (fault is None)
    assert (selection["cases"][label]["status"] == "PASS") == (fault is None)
    report = storage_public_report()
    report.update(status="PASS" if fault is None else "FAIL", selection=selection)
    probe.validate_storage_public(report)
    assert "PRIVATE" not in json.dumps(report) and "/private/path" not in json.dumps(report)
    if fault == "unverified":
        assert selection["cases"][label]["publicationSharing"] == value
        assert selection["cases"][label]["publicationSharingVerified"] is False
    assert len(json.dumps(report).encode("utf-8")) <= 65536