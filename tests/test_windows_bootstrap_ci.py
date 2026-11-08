"""Portable CI-route checks; native fixture execution is deliberately not mocked as PASS."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/platform-mechanisms.yml"
SPEC = importlib.util.spec_from_file_location("bootstrap_ci", ROOT / "tests/fixtures/windows_bootstrap_ci.py")
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)
OLD_JOBS = ("passive-process-check", "windows-private-config-tls",
            "windows-user-trial", "windows-default-runtime")
OLD_GUARD = "${{ !inputs.bootstrap_only && !inputs.foreground_only }}"
DEFAULT_GUARD = "${{ !inputs.bootstrap_only && !cancelled() && (inputs.foreground_only || needs.windows-user-trial.result == 'success') }}"
NEW_GUARD = "${{ github.event_name == 'workflow_dispatch' && inputs.bootstrap_only }}"


class RouteTests(unittest.TestCase):
    def test_each_existing_job_is_excluded_and_new_job_is_manual_only(self):
        source = WORKFLOW.read_text()
        jobs = re.findall(r"^  ([a-z][a-z0-9-]+):\n(.*?)(?=^  [a-z][a-z0-9-]+:|\Z)",
                          source.split("\njobs:\n", 1)[1], re.M | re.S)
        self.assertEqual([name for name, _ in jobs], [*OLD_JOBS, "windows-bootstrap-offline"])
        for name, body in jobs:
            expected = (NEW_GUARD if name == "windows-bootstrap-offline" else
                        DEFAULT_GUARD if name == "windows-default-runtime" else OLD_GUARD)
            self.assertEqual(re.search(r"^    if: (.*)$", body, re.M).group(1), expected)
        for event, bootstrap, foreground, selected in (
            ("workflow_dispatch", True, False, ["windows-bootstrap-offline"]),
            ("workflow_dispatch", True, True, ["windows-bootstrap-offline"]),
            ("workflow_dispatch", False, False, list(OLD_JOBS)),
            ("workflow_dispatch", False, True, ["windows-default-runtime"]),
            ("pull_request", False, False, list(OLD_JOBS)),
        ):
            # For the dependent job, retain 1c4's success/cancellation sequencing.
            # The ordinary table assumes the required user-trial job succeeded.
            observed = [name for name, _ in jobs if
                        (event == "workflow_dispatch" and bootstrap if name == "windows-bootstrap-offline"
                         else not bootstrap if name == "windows-default-runtime"
                         else not bootstrap and not foreground)]
            self.assertEqual(observed, selected)
        self.assertRegex(source, r"(?s)workflow_dispatch:\n    inputs:\n      bootstrap_only:.*?type: boolean.*?default: false")

    def test_custom_shells_no_installs_and_pinned_action_allowlist(self):
        body = WORKFLOW.read_text().split("  windows-bootstrap-offline:\n", 1)[1]
        shells = re.findall(r"^        shell: (.*)$", body, re.M)
        self.assertEqual(shells, [
            "python -I -S -B {0}",
            "powershell -NoLogo -NoProfile -NonInteractive -File {0}",
            "pwsh -NoLogo -NoProfile -NonInteractive -File {0}",
            "python -I -S -B {0}",
        ])
        self.assertIn("id: bootstrap-init", body)
        self.assertIn("steps.bootstrap-init.outcome == 'success'", body)
        self.assertIn("steps.bootstrap-desktop.outcome == 'success'", body)
        for forbidden in ("Set-ExecutionPolicy", "-ExecutionPolicy", "Unblock-File",
                          "pip install", "uv sync", "setup-uv", "pytest", "npm", "secrets."):
            self.assertNotIn(forbidden, body)
        self.assertEqual(re.findall(r"uses: ([^\s]+)", body), [
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        ])
        self.assertIn("if: always()", body)
        self.assertIn("persist-credentials: false", body)
        self.assertIn("result.json", body)
        self.assertIn("if-no-files-found: error", body)
        self.assertIn("timeout-minutes: 12", body)


class HarnessTests(unittest.TestCase):
    def report(self):
        return {"initialize": "PASS", "source": {"sha256": {"fixture": "abc"}},
                "editions": {name: {"status": "NOT_RUN"} for name in CI.HOSTS}}

    def test_two_fixed_standalone_fixtures_only_no_policy_override(self):
        self.assertEqual([row[0] for row in CI.FIXTURES],
                         ["tests/fixtures/windows_bootstrap_prototype.ps1", "tests/windows/install.Tests.ps1"])
        source = (ROOT / "tests/fixtures/windows_bootstrap_ci.py").read_text()
        self.assertNotIn("Set-ExecutionPolicy", source)
        self.assertNotRegex(source, r"""["']-ExecutionPolicy\b""")
        self.assertNotIn("Unblock-File", source)
        self.assertIn("Get-ExecutionPolicy -List", CI.POLICY_PROBE)
        self.assertIn("IsInRole", CI.POLICY_PROBE)

    def test_native_path_snapshots_stay_private_and_distinct(self):
        private_roots = ["C:\\Users\\synthetic\\one", "C:\\Users\\synthetic\\two"]
        with mock.patch.object(CI, "run", return_value={
            "exitCode": 0, "timedOut": False, "outputTruncated": False,
            "stdout": json.dumps(private_roots),
        }) as run:
            self.assertEqual(CI.query("powershell.exe", CI.ROOTS_PROBE, "desktop"), private_roots)
            self.assertIs(run.call_args.kwargs["public"], False)

    def test_sanitized_output_removes_identity_and_private_paths(self):
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": r"C:\temp\private", "USERPROFILE": r"C:\Users\synthetic"}):
            raw = r"C:\Users\synthetic\AppData\Local\test S-1-5-21-123-456-789-1001" + "\n" + "C:\\temp\\private\\result"
            safe = CI.sanitize(raw)
            self.assertNotIn("synthetic", safe)
            self.assertNotIn("S-1-5", safe)
            self.assertNotIn("C:\\", safe)
            self.assertIn("<sid>", safe)

    def test_wrapper_denial_cannot_be_summarized_as_success(self):
        report = self.report()
        with mock.patch.object(CI, "save"):
            self.assertFalse(CI.summarize(report))
            self.assertEqual(report["status"], "FAIL")
            report["editions"] = {name: {"status": "PASS"} for name in CI.HOSTS}
            self.assertTrue(CI.summarize(report))

    def test_policy_denied_fixture_stays_failure_with_no_second_fixture(self):
        report = self.report()
        denied = {"exitCode": 1, "timedOut": False, "outputTruncated": False,
                  "stdout": "", "stderr": "running scripts is disabled",
                  "directChildExited": True}
        with mock.patch.object(CI, "hashes", return_value={"fixture": "abc"}), \
             mock.patch.object(CI, "save"), mock.patch.object(CI.shutil, "which", return_value="powershell.exe"), \
             mock.patch.object(CI, "query", return_value=[]), \
             mock.patch.object(CI, "run", return_value=denied) as run, \
             self.assertRaisesRegex(RuntimeError, "did not qualify"):
            CI.run_edition(report, "desktop")
        self.assertEqual(run.call_count, 1)
        row = report["editions"]["desktop"]
        self.assertEqual(row["status"], "FAIL")
        self.assertEqual(row["fixtures"][0]["exitCode"], 1)

    def test_fixture_deadline_retains_unknown_cleanup_and_blocks_other_host(self):
        report = self.report()
        timeout = {"exitCode": 1, "timedOut": True, "outputTruncated": False,
                   "stdout": "", "stderr": "", "directChildExited": True}
        with mock.patch.object(CI, "hashes", return_value={"fixture": "abc"}), \
             mock.patch.object(CI, "save"), mock.patch.object(CI.shutil, "which", return_value="powershell.exe"), \
             mock.patch.object(CI, "query", return_value=[]), mock.patch.object(CI, "run", return_value=timeout), \
             self.assertRaisesRegex(RuntimeError, "deadline"):
            CI.run_edition(report, "desktop")
        self.assertFalse(report["editions"]["desktop"]["fixtures"][0]["cleanupObserved"])
        with mock.patch.object(CI, "hashes", return_value={"fixture": "abc"}), \
             mock.patch.object(CI, "run") as run, self.assertRaisesRegex(RuntimeError, "unknown"):
            CI.run_edition(report, "core")
        run.assert_not_called()

    def test_running_receipt_is_saved_before_spawn_and_blocks_interrupted_followup(self):
        report = self.report()
        snapshots = []

        def save(value):
            snapshots.append(json.loads(json.dumps(value)))

        def interrupted(*_args, **_kwargs):
            self.assertEqual(snapshots[-1]["editions"]["desktop"]["fixtures"][0]["executionState"], "RUNNING")
            raise KeyboardInterrupt

        with mock.patch.object(CI, "hashes", return_value={"fixture": "abc"}), \
             mock.patch.object(CI, "save", side_effect=save), \
             mock.patch.object(CI.shutil, "which", return_value="powershell.exe"), \
             mock.patch.object(CI, "query", return_value=[]), \
             mock.patch.object(CI, "run", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            CI.run_edition(report, "desktop")
        self.assertEqual(report["editions"]["desktop"]["status"], "FAIL")
        self.assertEqual(report["editions"]["desktop"]["fixtures"][0]["executionState"], "RUNNING")
        with mock.patch.object(CI, "hashes", return_value={"fixture": "abc"}), \
             mock.patch.object(CI, "run") as run, self.assertRaisesRegex(RuntimeError, "unknown"):
            CI.run_edition(report, "core")
        run.assert_not_called()

    def test_cleanup_observation_interruption_blocks_next_host(self):
        report = self.report()
        completed = {"exitCode": 0, "timedOut": False, "outputTruncated": False,
                     "stdout": "PROTOTYPE OFFLINE MODEL PASS", "stderr": "",
                     "directChildExited": True, "captureDirectoryRemoved": True}
        with mock.patch.object(CI, "hashes", return_value={"fixture": "abc"}), \
             mock.patch.object(CI, "save"), mock.patch.object(CI.shutil, "which", return_value="powershell.exe"), \
             mock.patch.object(CI, "query", side_effect=[[], RuntimeError("interrupted")]), \
             mock.patch.object(CI, "run", return_value=completed), self.assertRaises(RuntimeError):
            CI.run_edition(report, "desktop")
        self.assertEqual(report["editions"]["desktop"]["operationState"], "RUNNING")
        with mock.patch.object(CI, "hashes", return_value={"fixture": "abc"}), \
             mock.patch.object(CI, "run") as run, self.assertRaisesRegex(RuntimeError, "unknown"):
            CI.run_edition(report, "core")
        run.assert_not_called()

    def test_missing_native_prerequisite_keeps_initial_failure_receipt(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"RUNNER_TEMP": temporary}), \
             mock.patch.object(CI, "source_identity", side_effect=RuntimeError("missing")):
            with self.assertRaises(RuntimeError):
                CI.initialize()
            record = json.loads(CI.report_path().read_text())
            self.assertEqual(record["status"], "FAIL")
            self.assertEqual(record["initialize"], "FAIL")
            self.assertEqual(record["editions"]["desktop"]["status"], "NOT_RUN")

    def test_report_publication_preserves_prior_receipt_on_interrupted_replace(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"RUNNER_TEMP": temporary}):
            CI.save({"status": "RUNNING"})
            with mock.patch.object(CI.os, "replace", side_effect=OSError("interrupted")), self.assertRaises(OSError):
                CI.save({"status": "PASS"})
            self.assertEqual(json.loads(CI.report_path().read_text()), {"status": "RUNNING"})


if __name__ == "__main__":
    unittest.main()