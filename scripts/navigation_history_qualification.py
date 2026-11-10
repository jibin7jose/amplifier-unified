"""Fixed Navigation/Automation controls and out-of-source wheel proof."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from browser_check_runner import run_scenarios
from web_test_harness import stage_web_test_harness


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/destination-history"


def run(name, argv, *, cwd=ROOT, env=None, timeout=180, expected=0):
    log = OUTPUT / f"{name}.log"
    with log.open("w") as stream:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"Stopping exact owned qualification process group {process.pid}", flush=True)
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            raise
    assert code == expected, f"{name}: exit {code}, expected {expected}; see {log.name}"
    return log.read_text()


def main():
    assert os.environ.get("GITHUB_ACTIONS") == "true", "Run only in the owned hosted qualification job"
    assert json.loads((OUTPUT / "qualification.json").read_text())["passed"], "Positive source checks must pass first"
    result = {"scope": "Navigation and Automation controls and installed wheel", "checks": {}}
    source = json.loads((OUTPUT / "source.json").read_text())
    def source_restored():
        assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
                   for name, digest in source["files"].items()
                   if not name.startswith("amplifier_web/static/"))
    try:
        source_restored()
        automation = ROOT / ".build/web-client/src/automation-hub.jsx"
        original_automation = automation.read_bytes()
        name = "hub Create selects a new draft, while manage and Review retain existing edits without retaining authority"
        command = ["node", "--test", "--test-reporter=tap",
                   "--test-name-pattern=^" + name + "$", "tests/automation-hub.test.mjs"]
        try:
            needle = b"if(create)prepareNewSchedule(id);"
            assert original_automation.count(needle) == 1
            automation.write_bytes(original_automation.replace(needle, b"if(create)void 0;"))
            text = run("negative-automation-create", command, cwd=ROOT / ".build/web-client", expected=1)
            assert f"not ok 1 - {name}" in text and "ERR_ASSERTION" in text
            assert "operator: 'match'" in text and "New schedule" in text
            counts = dict(re.findall(r"^# (tests|pass|fail|cancelled|skipped|todo) (\d+)$", text, re.M))
            assert counts == {"tests": "1", "pass": "0", "fail": "1",
                              "cancelled": "0", "skipped": "0", "todo": "0"}
        finally:
            automation.write_bytes(original_automation)
        assert automation.read_bytes() == original_automation
        text = run("restored-automation-create", command, cwd=ROOT / ".build/web-client")
        assert f"ok 1 - {name}" in text and "# fail 0" in text and "# pass 1" in text
        result["checks"]["automationCreateIntent"] = {
            "negative": "rejected at New schedule assertion",
            "restoredPositive": "passed", "sourceSha256": hashlib.sha256(original_automation).hexdigest()}

        layout = ROOT / ".build/web-client/src/settings-layout.jsx"
        original_layout = layout.read_bytes()
        try:
            needle = b"if(entry.key===latest.current.trail.at(-1)?.key)return;"
            assert original_layout.count(needle) == 1
            layout.write_bytes(original_layout.replace(needle, b""))
            text = run("negative-settings-reopen", ["node", "--test",
                       "--test-name-pattern=discard trail reconciliation",
                       "tests/settings-drafts.test.mjs"], cwd=ROOT / ".build/web-client", expected=1)
            assert "Reconciling the already-current parent must not publish a Settings reopen" in text
            assert "not ok" in text
            result["checks"]["settingsCloseReopen"] = "rejected at redundant Settings navigation assertion"
        finally:
            layout.write_bytes(original_layout)

        model = ROOT / ".build/web-client/src/destination-history.js"
        original = model.read_bytes()
        try:
            needle = b"frame.host===receipt.hostInstanceId&&frame.revision>=receipt.stateRevision"
            assert original.count(needle) == 1
            model.write_bytes(original.replace(needle, b"frame.host===receipt.hostInstanceId"))
            text = run("negative-revision", ["node", "--test", "--test-name-pattern=cursor commits only",
                       "tests/destination-history.test.mjs"], cwd=ROOT / ".build/web-client", expected=1)
            assert "old-revision" in text and "not ok" in text
            result["checks"]["revisionFence"] = "rejected at old-revision assertion"
        finally:
            model.write_bytes(original)

        entry = ROOT / ".build/web-client/src/main.jsx"
        original = entry.read_bytes()
        static = ROOT / "amplifier_web/static"
        with tempfile.TemporaryDirectory(prefix="history-negative-") as temporary:
            backup = Path(temporary) / "static"
            shutil.copytree(static, backup)
            try:
                needle = b"destinationHistory.current.observe(next,historyOptions);"
                assert original.count(needle) == 1
                entry.write_bytes(original.replace(
                    needle, b"destinationHistory.current.observe(conversationNavigation.current.apply(next),historyOptions);"))
                run("negative-build", [sys.executable, "scripts/web_client_build.py", "build-development"])
                plan = json.loads((OUTPUT / "plan.json").read_text())
                plan["scenarios"] = [row for row in plan["scenarios"] if row["id"] == "destination-history"]
                plan["scenarios"][0]["env"]["AMPLIFIER_TEST_OUTPUT"] = "{root}/output/destination-history/negative-browser"
                target = OUTPUT / "negative-plan.json"
                target.write_text(json.dumps(plan))
                run("negative-optimistic", [sys.executable, "scripts/browser_check_runner.py",
                    "--plan", str(target), "--output", str(OUTPUT / "negative-runner"),
                    "--budget-seconds", "90"], expected=1, timeout=120)
                report = json.loads((OUTPUT / "negative-runner/results.json").read_text())
                row, = report["scenarios"]
                text = (OUTPUT / "negative-runner" / row["log"]).read_text()
                assert report["complete"] and row["status"] == "failed" and row["exitCode"] == 1
                assert row["cleanup"] == {"remaining": [], "terminated": 0}
                assert "Unrelated authoritative state must not record an optimistic selection" in text
                result["checks"]["optimisticIsolation"] = "rejected at unchanged-trail assertion"
            finally:
                entry.write_bytes(original)
                if static.exists():
                    shutil.rmtree(static)
                shutil.copytree(backup, static)

        source_restored()
        run("wheel-build", ["uv", "build", "--wheel", "--out-dir", str(OUTPUT / "wheel")])
        wheel, = (OUTPUT / "wheel").glob("*.whl")
        result["wheelSha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        run("wheel-install", ["uv", "pip", "install", "--python", sys.executable,
                             "--no-deps", "--reinstall", str(wheel)])
        with tempfile.TemporaryDirectory(prefix="history-installed-") as temporary:
            harness = Path(temporary)
            helpers = stage_web_test_harness(ROOT, harness, python_tests=True,
                source_helpers=('destination-history.js', 'settings-navigation.js'))
            result['installedSourceOracles'] = helpers
            env = {**os.environ, "AMPLIFIER_TEST_PYTHON": sys.executable,
                   "AMPLIFIER_SHELL_NODE_MODULES": str(harness / ".build/web-client/node_modules"),
                   "AMPLIFIER_TEST_OUTPUT": str(OUTPUT / "installed-browser")}
            env.pop("PYTHONPATH", None)
            origins = json.loads(run("wheel-origin", [sys.executable, "-c",
                "import amplifier_web,amplifier_scheduling,json; print(json.dumps({"
                "'amplifier_web':amplifier_web.__file__,'amplifier_scheduling':amplifier_scheduling.__file__}))"],
                cwd=harness, env=env))
            package = Path(origins["amplifier_web"]).resolve().parent
            for name, origin in origins.items():
                installed = Path(origin).resolve().parent
                assert "site-packages" in installed.parts
                assert not installed.is_relative_to((ROOT / name).resolve())
                assert not installed.is_relative_to(harness.resolve())
                assert installed.parent == package.parent and not (harness / name).exists()
            with zipfile.ZipFile(wheel) as archive:
                payloads = [name for name in archive.namelist()
                            if name.startswith(("amplifier_web/", "amplifier_scheduling/")) and not name.endswith("/")]
                expected = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in payloads}
            def verify():
                assert all(hashlib.sha256((package.parent / name).read_bytes()).hexdigest() == digest
                           for name, digest in expected.items())
            verify()
            run("installed-auth", [sys.executable, "-m", "pytest", "--asyncio-mode=auto", "-q",
                "tests/test_history_scope.py", "tests/test_auth.py",
                "tests/test_automation_overview.py", "tests/test_schedules.py",
                "tests/test_schedule_new_tasks.py", "tests/test_schedule_policy.py",
                f"--junitxml={OUTPUT / 'installed-python.xml'}"], cwd=harness, env=env)
            cases = ET.parse(OUTPUT / "installed-python.xml").getroot().findall(".//testcase")
            assert cases and all(not any(case.find(tag) is not None for tag in ("skipped", "failure", "error")) for case in cases)
            rows = []
            for identity, evidence in (("destination-history", {"AMPLIFIER_TEST_OUTPUT": str(OUTPUT / "installed-browser")}),
                                       ("automation-hub", {"AMPLIFIER_TEST_ARTIFACTS": str(OUTPUT / "installed-automation")})):
                rows.append({"id": identity, "command": ["node", f"tests/web/{identity}-browser.mjs"],
                             "env": {"AMPLIFIER_TEST_PYTHON": sys.executable,
                                     "AMPLIFIER_SHELL_NODE_MODULES": str(harness / ".build/web-client/node_modules"), **evidence}})
            installed_plan = {"schemaVersion": 1, "scenarios": rows}
            (OUTPUT / "installed-plan.json").write_text(json.dumps(installed_plan, indent=2))
            previous = os.environ.pop("PYTHONPATH", None)
            try:
                code = run_scenarios(harness, installed_plan, OUTPUT / "installed-runner", budget=360, case_timeout=180,
                                     provenance={"wheelSha256": result["wheelSha256"],
                                                 "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
                                                 "applicationOutsideHarness": True})
            finally:
                if previous is not None:
                    os.environ["PYTHONPATH"] = previous
            report = json.loads((OUTPUT / "installed-runner/results.json").read_text())
            assert code == 0 and report["passed"] and report["complete"]
            assert [row["id"] for row in report["scenarios"]] == ["destination-history", "automation-hub"]
            for row in report["scenarios"]:
                assert row["status"] == "passed" and row["exitCode"] == 0 and row["reason"] is None
                assert row["cleanup"] == {"remaining": [], "terminated": 0}
            for folder in (ROOT / "output/browser-checks/automation-hub", OUTPUT / "installed-automation"):
                proof = json.loads((folder / "result.json").read_text())
                cleanup = json.loads((folder / "cleanup.json").read_text())
                assert proof["passed"] and proof["errors"] == [] and proof["browserTimezone"] == "Asia/Tokyo"
                intent = proof["createIntent"]
                mutation, = intent["mutations"]
                assert mutation["action"] == "schedule.create" and "id" not in mutation["args"]
                assert mutation["args"]["expectedRevision"] == 0
                assert next(row for row in intent["afterCreate"]["schedules"]
                            if row["id"] == intent["beforeCreate"]["id"]) == intent["beforeCreate"]
                assert proof["created"]["schedules"][0]["spec"]["localTime"] == "08:30"
                assert proof["created"]["schedules"][0]["nextDue"] == 1800289800
                assert cleanup["completed"] and cleanup["fixture"]["code"] == 0 and cleanup["fixture"]["signal"] is None
            for folder in (ROOT / "output/browser-checks/destination-history", OUTPUT / "installed-browser"):
                touch = json.loads((folder / "wide-touch-home.json").read_text())
                assert touch["passed"] and touch["input"] == {"width": 1024, "coarse": True}
                assert all(touch[key] for key in ("oldSelectorRejected", "restoredVisible",
                                                 "keyboardHome", "tapHome", "allWorkspacesLabel"))
                assert touch["pageErrors"] == [] and touch["box"]["width"] >= 44 and touch["box"]["height"] >= 44
            result["checks"]["wideTouchHome"] = "source and installed positive, old-selector rejection, keyboard and tap"
            verify()
            result["checks"]["installedWheel"] = {"payloadsVerifiedBeforeAfter": len(expected),
                "staticFiles": sum(name.startswith("amplifier_web/static/") for name in expected),
                "pythonCases": len(cases), "browsers": ["destination-history", "automation-hub"],
                "packageOrigins": origins, "applicationOutsideHarness": True,
                "installedPlanSha256": hashlib.sha256((OUTPUT / "installed-plan.json").read_bytes()).hexdigest()}
        source_restored()
        result["nonStaticSourceRestored"] = True
        result["passed"] = True
    except Exception as exc:
        result.update(passed=False, error=str(exc))
        raise
    finally:
        (OUTPUT / "additional-qualification.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()