"""Portable checks of the future cold driver; no real install is modeled as PASS."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cold_driver", ROOT / "tests/fixtures/windows_prototype_cold.py")
COLD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COLD)


class InputsTests(unittest.TestCase):
    def test_native_gate_refuses_server_elevation_arch_and_hidden_tools(self):
        facts = {"ready": True, "platform": "Win32NT", "process64": True,
                 "edition": "Desktop", "version": "5.1.26100.1", "elevated": False,
                 "osProductType": 1, "osBuild": 26100, "processorArchitectures": [9],
                 "commands": {name: {"resolvable": False}
                     for name in ("uv", "git", "python", "python3", "py", "pwsh")}}
        with patch.object(COLD, "save"):
            COLD.require_ready(facts, {})
            for key, value in (("elevated", True), ("osProductType", 3),
                               ("osBuild", 21999), ("processorArchitectures", [12]),
                               ("ready", False)):
                report = {}
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, "prerequisites"):
                    COLD.require_ready({**facts, key: value}, report)
                self.assertEqual(report["readiness"], "REFUSED_BEFORE_INSTALL")
            with self.assertRaises(ValueError):
                COLD.require_ready({**facts, "commands": {}}, {})
        ps = (ROOT / "tests/fixtures/windows_prototype_cold.ps1").read_text()
        self.assertIn("if ($elevated)", ps)
        self.assertIn("$os.ProductType -ne 1", ps)
        self.assertIn("$architectures[0] -ne 9", ps)

    def test_public_preflight_projection_excludes_private_addresses_and_ids(self):
        keys = ("version", "edition", "process64", "platform", "elevated", "osBuild",
                "effectivePolicy", "scopes", "commands", "profile", "pathIsExactSystem32Windows",
                "osProductType", "processorArchitectures", "ready", "refusals")
        facts = {key: True for key in keys}
        facts.update(roots=[r"C:\Users\Private\root"], localAppData=r"C:\Users\Private",
                     processIds=[17], source={"private": "not public"})
        self.assertEqual(set(COLD.public_native(facts)), set(keys))
        self.assertNotIn("Private", json.dumps(COLD.public_native(facts)))

    def test_child_path_has_only_os_and_preserves_real_profile_not_tokens(self):
        env = {"SystemRoot": "/Windows", "USERPROFILE": "/Users/Trial", "HOMEDRIVE": "C:",
               "HOMEPATH": "/Users/Trial", "PATH": "/developer/bin", "PSModulePath": "/custom",
               "GH_TOKEN": "never", "GITHUB_TOKEN": "never", "OPENAI_API_KEY": "never",
               "UV_CONFIG_FILE": "/bad", "PYTHONPATH": "/bad", "GIT_EXEC_PATH": "/bad"}
        result = COLD.child_environment(env)
        self.assertEqual(result["PATH"], os.pathsep.join([str(Path('/Windows/System32')), '/Windows']))
        for key in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
            self.assertEqual(result[key], env[key])
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "UV_CONFIG_FILE", "PYTHONPATH", "GIT_EXEC_PATH", "PSModulePath"):
            self.assertNotIn(key, result)
        self.assertEqual(env["PATH"], "/developer/bin")

    def test_missing_profile_refuses_instead_of_synthesizing_home(self):
        with self.assertRaisesRegex(ValueError, "profile"):
            COLD.child_environment({"SYSTEMROOT": "/Windows"})

    def test_exact_publication_url_rejects_moving_host_query_and_auth(self):
        good = 'https://github.com/microsoft/amplifier-unified/releases/download/trial-abc/windows-prototype.json'
        self.assertEqual(COLD.validate_url(good, 'windows-prototype.json'), good)
        for url in (good.replace('https:', 'http:'), good.replace('github.com', 'example.test'),
                    good.replace('trial-abc', 'latest'), good.replace('trial-abc', 'main'),
                    good + '?token=secret', good + '#fragment', good.replace('github.com', 'user@github.com')):
            with self.subTest(url=url), self.assertRaises(ValueError):
                COLD.validate_url(url, 'windows-prototype.json')

    def test_unpublished_wrong_bytes_and_wrong_candidate_never_install(self):
        url = 'https://github.com/microsoft/amplifier-unified/releases/download/trial-abc/windows-prototype.json'
        response = Mock(url=url)
        response.read.return_value = b'not-published-manifest'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(COLD.urllib.request, 'urlopen', return_value=response), \
             self.assertRaisesRegex(ValueError, 'Published manifest bytes'):
            COLD.published_manifest(url, COLD.MANIFEST_SHA)
        with patch.object(COLD.urllib.request, 'urlopen') as get, self.assertRaises(ValueError):
            COLD.published_manifest(url, '0' * 64)
        get.assert_not_called()

    def test_natural_success_cannot_hide_force_timeout_truncation_or_children(self):
        result = types.SimpleNamespace(payload_returncode=0, active_processes=0, output_discarded=False)
        self.assertTrue(COLD.natural_success(result, False, False, False))
        for flags in ((True, False, False), (False, True, False), (False, False, True)):
            self.assertFalse(COLD.natural_success(result, *flags))
        result.active_processes = 1
        self.assertFalse(COLD.natural_success(result, False, False, False))
        result.active_processes = 0; result.payload_returncode = 1
        self.assertFalse(COLD.natural_success(result, False, False, False))


class CleanupTests(unittest.TestCase):
    def test_fresh_root_requires_one_new_exact_direct_child(self):
        parent = Path('/local')
        root = parent / ('amplifier-install-' + 'a' * 32)
        self.assertEqual(COLD.fresh_root({'old'}, {'old', str(root)}, parent), root)
        for before, after in (({'old'}, {'old'}), (set(), {str(root), 'other'}), (set(), {'/elsewhere/' + root.name}),
                              (set(), {'/local/arbitrary'})):
            with self.assertRaises(ValueError):
                COLD.fresh_root(before, after, parent)

    def test_refuses_root_delete_on_changed_identity_or_live_process(self):
        root = Path('/synthetic-root')
        with patch.object(COLD, 'root_identity', return_value=(1, 2)), patch.object(COLD.shutil, 'rmtree') as remove:
            for expected, processes in (((1, 3), []), ((1, 2), [17])):
                with self.assertRaises(ValueError):
                    COLD.delete_verified_root(root, expected, processes, Mock())
            remove.assert_not_called()

    def test_link_in_tree_prevents_cleanup_and_unrelated_delete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'owned'; root.mkdir()
            target = Path(temp) / 'unrelated'; target.mkdir()
            (root / 'link').symlink_to(target, target_is_directory=True)
            @contextmanager
            def directory(*args, **kwargs):
                yield None
            files = types.SimpleNamespace(directory=directory)
            with self.assertRaisesRegex(ValueError, 'link'):
                COLD.delete_verified_root(root, COLD.root_identity(root), [], files)
            self.assertTrue(root.exists()); self.assertTrue(target.exists())

    def test_report_publication_is_atomic_and_no_raw_exception(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'RUNNER_TEMP': temp}):
            COLD.save({'status': 'FAIL', 'stage': 'before-spawn'})
            self.assertEqual(json.loads(COLD.output_path().read_text())['stage'], 'before-spawn')
            with patch.object(COLD.os, 'replace', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                COLD.save({'status': 'PASS'})
            self.assertEqual(json.loads(COLD.output_path().read_text())['status'], 'FAIL')


class OwnedProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_diagnostics_do_not_corrupt_json_stdout(self):
        class FakeOwner:
            @classmethod
            async def start(cls, *args, **kwargs):
                owner = cls()
                owner.stdin = Mock(); owner.state = 'closed'; owner.io_finalized = True
                owner.stdout = asyncio.StreamReader()
                owner.stdout.feed_data(b'{"status":"PASS"}'); owner.stdout.feed_eof()
                owner.stderr = asyncio.StreamReader()
                owner.stderr.feed_data(b'private diagnostic'); owner.stderr.feed_eof()
                return owner
            async def wait(self):
                return types.SimpleNamespace(payload_returncode=0, active_processes=0, output_discarded=False)
        report = {'operations': []}; log = io.BytesIO()
        with patch.object(COLD, 'save'):
            output = await COLD.owned_run(types.SimpleNamespace(WindowsOwnedProcess=FakeOwner),
                                         ['/absolute/powershell.exe'], {}, 1, log, report, 'inspect')
        self.assertEqual(json.loads(output), {'status': 'PASS'})
        self.assertIn(b'private diagnostic', log.getvalue())

    async def test_receipt_precedes_spawn_and_zero_cleanup_required(self):
        report = {'operations': []}
        async def start(*args, **kwargs):
            self.assertEqual(report['operations'][-1]['state'], 'RUNNING')
            self.assertEqual(report['operations'][-1]['cleanup'], 'UNKNOWN')
            raise RuntimeError('native start refused')
        jobs = types.SimpleNamespace(WindowsOwnedProcess=type("FakeOwner", (), {
            "start": staticmethod(start)}))
        with patch.object(COLD, 'save'), self.assertRaises(RuntimeError):
            await COLD.owned_run(jobs, ['/absolute/powershell.exe'], {}, 1, io.BytesIO(), report, 'install')
        self.assertEqual(report['operations'][0]['state'], 'FAIL')
        self.assertEqual(report['operations'][0]['cleanup'], 'UNKNOWN')

    async def test_timeout_settlement_never_passes_and_preserves_failed_receipt(self):
        owner = Mock(state='running', io_finalized=True)
        owner.stdin = Mock()
        owner.stdout = asyncio.StreamReader(); owner.stdout.feed_eof()
        owner.stderr = asyncio.StreamReader(); owner.stderr.feed_eof()
        owner._guard_handle = 3
        owner._native = Mock()
        owner._native.k.TerminateJobObject = Mock(return_value=True)
        future = asyncio.Future()
        result = types.SimpleNamespace(payload_returncode=1, active_processes=0, output_discarded=True)
        owner.wait = AsyncMock(side_effect=lambda: future)
        async def wait():
            return await future
        owner.wait = wait
        async def close():
            owner.state = 'closed'; future.set_result(result)
            return result
        owner.close = AsyncMock(side_effect=close)
        jobs = types.SimpleNamespace(WindowsOwnedProcess=type("FakeOwner", (), {
            "start": staticmethod(AsyncMock(return_value=owner))}))
        report = {'operations': []}
        with patch.object(COLD, 'save'), self.assertRaisesRegex(RuntimeError, 'forceful'):
            await COLD.owned_run(jobs, ['/absolute/powershell.exe'], {}, .001, io.BytesIO(), report, 'install')
        self.assertTrue(report['operations'][0]['timedOut'])
        self.assertTrue(report['operations'][0]['forcedPayloadCleanup'])
        self.assertEqual(report['operations'][0]['state'], 'FAIL')
        owner.close.assert_awaited_once()

    async def test_early_monitor_cleanup_is_observed_before_start_returns(self):
        result = types.SimpleNamespace(payload_returncode=0, active_processes=0, output_discarded=False)
        class FakeOwner:
            @classmethod
            async def start(cls, *args, **kwargs):
                owner = cls()
                owner._assigned = True; owner._job = 1; owner._guard_handle = 2
                owner._native = types.SimpleNamespace(active=lambda _: 2, exited=lambda _: False)
                owner.stdin = Mock(); owner.state = 'closed'; owner.io_finalized = True
                owner.stdout = asyncio.StreamReader(); owner.stdout.feed_eof()
                owner.stderr = asyncio.StreamReader(); owner.stderr.feed_eof()
                await owner._cleanup()  # deliberately before caller receives owner
                return owner
            async def _cleanup(self):
                return result
            async def wait(self):
                return result
        report = {'operations': []}
        with patch.object(COLD, 'save'), self.assertRaisesRegex(RuntimeError, 'forceful'):
            await COLD.owned_run(types.SimpleNamespace(WindowsOwnedProcess=FakeOwner),
                                 ['/absolute/powershell.exe'], {}, 1, io.BytesIO(), report, 'install')
        self.assertTrue(report['operations'][0]['forcedPayloadCleanup'])

    async def test_observation_error_still_runs_canonical_cleanup_before_failure(self):
        canonical = []
        result = types.SimpleNamespace(payload_returncode=0, active_processes=0, output_discarded=False)
        class FakeOwner:
            @classmethod
            async def start(cls, *args, **kwargs):
                owner = cls()
                owner._assigned = True; owner._job = 1; owner._guard_handle = 2
                owner._native = types.SimpleNamespace(active=Mock(side_effect=OSError('query failed')))
                owner.stdin = Mock(); owner.state = 'closed'; owner.io_finalized = True
                owner.stdout = asyncio.StreamReader(); owner.stdout.feed_eof()
                owner.stderr = asyncio.StreamReader(); owner.stderr.feed_eof()
                await owner._cleanup()
                return owner
            async def _cleanup(self):
                canonical.append('settled')
                return result
            async def wait(self):
                return result
        report = {'operations': []}
        with patch.object(COLD, 'save'), self.assertRaisesRegex(RuntimeError, 'forceful'):
            await COLD.owned_run(types.SimpleNamespace(WindowsOwnedProcess=FakeOwner),
                                 ['/absolute/powershell.exe'], {}, 1, io.BytesIO(), report, 'inspect')
        self.assertEqual(canonical, ['settled'])
        self.assertEqual(report['operations'][0]['naturalExitObservation'], 'FAILED')


class WorkflowTests(unittest.TestCase):
    def test_one_manual_job_literal_shell_pinned_actions_no_policy_bypass(self):
        source = (ROOT / '.github/workflows/windows-prototype-cold.yml').read_text()
        self.assertIn('workflow_dispatch:', source)
        self.assertNotIn('pull_request:', source); self.assertNotIn('push:', source)
        self.assertEqual(source.count('runs-on:'), 1)
        self.assertIn('contents: read', source)
        self.assertIn('shell: python -I -S -B {0}', source)
        for action in ('checkout@11d5960a326750d5838078e36cf38b85af677262',
                       'setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',
                       'upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'):
            self.assertIn(action, source)
        self.assertIn('timeout-minutes: 25', source); self.assertIn('timeout-minutes: 22', source)
        self.assertIn('if: always()', source)
        self.assertIn('unified-prototype-cold/result.json', source)
        for forbidden in ('ExecutionPolicy', 'Unblock-File', 'secrets.', 'pip install', 'npm ', 'gh release'):
            self.assertNotIn(forbidden, source)
        code = (ROOT / 'tests/fixtures/windows_prototype_cold.py').read_text()
        self.assertIn('trusted_python=sys._base_executable', code)
        self.assertIn('natural_success(result, forced, truncated, timed_out)', code)
        self.assertNotIn('os.kill(', code)
        self.assertIn('COLD_EXPECTED_HEAD', code)

    def test_frozen_installer_is_not_changed(self):
        # Git checkout may translate LF to CRLF on Windows; candidate content is
        # immutable. This is not a hash of arbitrary normalization or a new pin.
        raw = (ROOT / 'scripts/install.ps1').read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(hashlib.sha256(raw).hexdigest(), COLD.INSTALLER_SHA)
        self.assertEqual(COLD.CAPSULE_BYTES, 53046193)
        self.assertLess(COLD.INSTALL_SECONDS + COLD.AUDIT_SECONDS + 4*COLD.OBSERVE_SECONDS,
                        COLD.OVERALL_SECONDS)
        self.assertLess(COLD.OVERALL_SECONDS, 22 * 60)


if __name__ == '__main__':
    unittest.main()
