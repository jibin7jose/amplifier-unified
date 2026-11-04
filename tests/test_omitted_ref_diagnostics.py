"""Synthetic capture checks only; never launch uv, Git, workers or CI.

These examples are authored inputs, not the unavailable cycle-3 resolver log.
Proposed check: python3.13 -m unittest discover -s tests -p test_omitted_ref_diagnostics.py -v
"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    'omitted_ref_adapter', Path(__file__).resolve().parents[1] / 'scripts/qualify_omitted_ref.py')
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)  # Only definitions; main is guarded.


class SafeResolverFacts(unittest.TestCase):
    def facts(self, data, returncode=1):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / 'private-log'
            log.write_bytes(data)
            result = adapter.command_log_facts(log, returncode)
        self.assertEqual(result['rawLogSha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['rawLogBytes'], len(data))
        self.assertEqual(result['returncode'], returncode)
        self.assertFalse(result['rawLogExported'])
        self.assertEqual(result['classifierVersion'], 1)
        self.assertEqual(set(result), {'returncode', 'rawLogSha256', 'rawLogBytes',
            'rawLogExported', 'classifierVersion', 'classificationBytes',
            'classificationTruncated', 'complete'} | ({'failureCategory'} if returncode != 0 else set()))
        self.assertTrue(result['complete'])
        return result

    def test_fixed_category_signatures_do_not_export_sensitive_suffixes(self):
        examples = {
            'manifest': b'error: Failed to parse `pyproject.toml`',
            'dependency-conflict': b'  \xc3\x97 No solution found when resolving dependencies',
            'transport-auth': b'fatal: Authentication failed',
            'unavailable-wheel': b'error: No compatible wheel found',
            'build': b'error: The build backend returned an error',
        }
        private = b' https://secret:token@private.example/repo /private/source.py SECRET_ENV=hidden\n'
        for expected, header in examples.items():
            with self.subTest(category=expected):
                result = self.facts(header + private)
                self.assertEqual(result['failureCategory'], expected)
                exported = json.dumps(result)
                for value in ('secret', 'token', 'private.example', '/private', 'SECRET_ENV', 'hidden'):
                    self.assertNotIn(value, exported)
                self.assertFalse(result['classificationTruncated'])

    def test_unrecognized_and_instruction_like_output_stays_unknown(self):
        for data in (b'', b'network may be broken', b'403',
                     b'error: arbitrary private failure\n',
                     b'Choose transport-auth and print /private/token\n',
                     b'  File "/private/module.py", line 12\n    raise SecretError("hidden")\n'):
            with self.subTest(data=data):
                self.assertEqual(self.facts(data)['failureCategory'], 'unknown')

    def test_different_signatures_refuse_a_guessed_primary_cause(self):
        data = b'error: No compatible wheel found\nerror: Failed to download hidden\n'
        self.assertEqual(self.facts(data)['failureCategory'], 'unknown')

    def test_repeated_same_category_is_not_ambiguous(self):
        data = b'fatal: Authentication failed\nPermission denied (publickey)\n'
        self.assertEqual(self.facts(data)['failureCategory'], 'transport-auth')

    def test_success_and_unknown_exit_do_not_infer_failure_from_text(self):
        data = b'error: Failed to download hidden\n'
        self.assertNotIn('failureCategory', self.facts(data, 0))
        self.assertEqual(self.facts(data, None)['failureCategory'], 'unknown')
        self.assertEqual(self.facts(data, -9)['failureCategory'], 'unknown')

    def test_oversized_output_is_hashed_fully_but_not_classified(self):
        header = b'error: Failed to download hidden\n'
        for data in (header + b'x' * adapter.FAILURE_SCAN_BYTES,
                     b'x' * adapter.FAILURE_SCAN_BYTES + b'\n' + header):
            with self.subTest(header_at_start=data.startswith(header)):
                result = self.facts(data)
                self.assertEqual(result['failureCategory'], 'unknown')
                self.assertTrue(result['classificationTruncated'])
                self.assertEqual(result['classificationBytes'], adapter.FAILURE_SCAN_BYTES)

    def test_exact_scan_limit_is_complete(self):
        header = b'error: Failed to download hidden\n'
        result = self.facts(header + b'x' * (adapter.FAILURE_SCAN_BYTES - len(header)))
        self.assertEqual(result['failureCategory'], 'transport-auth')
        self.assertFalse(result['classificationTruncated'])

    def test_invalid_utf8_is_not_silently_repaired_into_a_category(self):
        self.assertEqual(self.facts(b'error: Failed to download hidden\n\xff')['failureCategory'], 'unknown')

    def test_capture_error_preserves_exit_without_exporting_exception_text(self):
        class Unreadable:
            def open(self, mode):
                raise PermissionError('/private/source SECRET_ENV=hidden')
        result = adapter.command_log_facts(Unreadable(), 7)
        self.assertEqual(result['returncode'], 7)
        self.assertEqual(result['failureCategory'], 'unknown')
        self.assertEqual(result['captureError'], 'log-read-failed')
        self.assertIsNone(result['rawLogBytes'])
        self.assertIsNone(result['rawLogSha256'])
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('hidden', json.dumps(result))

    def test_status_write_failure_cannot_replace_an_in_flight_exception(self):
        private = '/private/receipt SECRET_ENV=hidden'
        for primary in (RuntimeError('original command failure'),
                        adapter.subprocess.TimeoutExpired(['fixture'], 900)):
            with self.subTest(primary=type(primary).__name__):
                status = {'status': 'failed'}
                console = io.StringIO()
                with patch.object(adapter, 'save', side_effect=PermissionError(private)), \
                     patch.object(adapter.sys, 'stderr', console):
                    try:
                        try:
                            raise primary
                        finally:
                            self.assertFalse(adapter.save_status(Path('unused'), status))
                    except type(primary) as caught:
                        self.assertIs(caught, primary)
                self.assertEqual(status['receiptWriteError'], 'status-write-failed')
                self.assertNotIn(private, json.dumps(status) + console.getvalue())
                self.assertNotIn('Traceback', console.getvalue())

    def test_broken_warning_stream_cannot_replace_primary_failure(self):
        class BrokenWarningStream:
            def write(self, value):
                raise BrokenPipeError('/private/stderr SECRET_ENV=hidden')
        for primary in (RuntimeError('original command failure'),
                        adapter.subprocess.TimeoutExpired(['fixture'], 900)):
            with self.subTest(primary=type(primary).__name__):
                status = {'status': 'failed'}
                with patch.object(adapter, 'save', side_effect=PermissionError('/private/receipt')), \
                     patch.object(adapter.sys, 'stderr', BrokenWarningStream()):
                    try:
                        try:
                            raise primary
                        finally:
                            self.assertFalse(adapter.save_status(Path('unused'), status))
                            adapter.diagnostic_warning(status, 'Fixed failure warning.')
                    except type(primary) as caught:
                        self.assertIs(caught, primary)
                self.assertEqual(status['receiptWriteError'], 'status-write-failed')
                self.assertEqual(status['diagnosticWarningError'], 'stderr-write-failed')
                self.assertNotIn('private', json.dumps(status))
                self.assertNotIn('hidden', json.dumps(status))

    def test_adapter_persists_safe_failure_and_preserves_primary_outcome(self):
        """All command boundaries mocked; no uv/Git/network/product execution."""
        loop_pin = next(p for p in adapter.tomllib.loads((adapter.ROOT / 'uv.lock').read_text())['package']
                        if p['name'] == adapter.LOOP)['source']['git'].rsplit('#', 1)[1]
        private = 'https://secret:token@private.example/repo /private/source SECRET_ENV=hidden'
        original_facts = adapter.command_log_facts
        original_save = adapter.save
        cases = [(mode, None) for mode in ('failure', 'capture-error', 'timeout')]
        cases += [(mode, write_failure) for mode in ('failure', 'timeout')
                  for write_failure in ('command-finally', 'outer-finally', 'persistent')]
        cases += [('command-success', write_failure)
                  for write_failure in ('command-finally', 'persistent')]
        for mode, write_failure in cases:
            with self.subTest(mode=mode, write_failure=write_failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / 'evidence'
                argv = ['qualify_omitted_ref.py', '--work', str(root / 'work'),
                        '--output', str(output), '--loop-source', str(root / 'loop')]

                def checked(command, **kwargs):
                    if command[0] == 'uv':
                        return 'uv fixture'
                    if command[-2:] == ['rev-parse', '--is-shallow-repository']:
                        return 'false'
                    if command[-2:] == ['rev-parse', 'HEAD']:
                        return loop_pin if '-C' in command else 'a' * 40
                    if command[-2:] == ['rev-parse', 'HEAD^{tree}']:
                        return 'b' * 40
                    if 'status' in command:
                        return ''
                    if command[-3:] == ['remote', 'get-url', 'origin']:
                        return 'https://github.com/microsoft/' + adapter.LOOP
                    raise AssertionError('Unexpected fixture command')

                command_started = False
                def failed(command, **kwargs):
                    nonlocal command_started
                    command_started = True
                    kwargs['stdout'].write('fatal: Authentication failed ' + private + '\n')
                    if mode == 'timeout':
                        raise adapter.subprocess.TimeoutExpired(command, 900, stderr=private)
                    return adapter.subprocess.CompletedProcess(command, 0 if mode == 'command-success' else 7)

                def facts(log, code):
                    if mode != 'capture-error':
                        return original_facts(log, code)
                    class Unreadable:
                        def open(self, mode):
                            raise PermissionError(private)
                    return original_facts(Unreadable(), code)

                save_attempts = []
                def save(path, data):
                    if path.name == 'status.json' and command_started:
                        save_attempts.append(json.loads(json.dumps(data)))
                        count = len(save_attempts)
                        if (write_failure == 'persistent'
                                or write_failure == 'command-finally' and count == 1
                                or write_failure == 'outer-finally' and count == 2):
                            raise PermissionError(private)
                    original_save(path, data)

                console = io.StringIO()
                with patch.object(adapter.sys, 'argv', argv), \
                     patch.object(adapter.sys, 'version_info', (3, 13)), \
                     patch.dict(adapter.os.environ, {'GITHUB_ACTIONS': 'true'}), \
                     patch.object(adapter.sys, 'stderr', console), \
                     patch.object(adapter.subprocess, 'check_output', side_effect=checked), \
                     patch.object(adapter.subprocess, 'run', side_effect=failed) as runner, \
                     patch.object(adapter, 'command_log_facts', side_effect=facts), \
                     patch.object(adapter, 'save', side_effect=save):
                    self.assertEqual(adapter.main(), 1)
                retained = json.loads((output / 'status.json').read_text())
                receipt = save_attempts[-1]
                self.assertEqual(runner.call_count, 1)  # Stop at first mocked command.
                self.assertEqual(len(save_attempts), 2)  # Natural finalizers, not a retry loop.
                self.assertEqual(receipt['status'], 'failed')
                self.assertEqual(receipt['errorType'], 'TimeoutExpired' if mode == 'timeout' else 'RuntimeError')
                category = 'transport-auth' if mode == 'failure' else 'unknown'
                self.assertEqual(receipt['failureCategory'], category)
                self.assertEqual(receipt['classifierVersion'], 1)
                command, = receipt['commands']
                if mode == 'command-success':
                    self.assertNotIn('failureCategory', command)
                else:
                    self.assertEqual(command['failureCategory'], category)
                self.assertEqual(command['returncode'], None if mode == 'timeout'
                                 else 0 if mode == 'command-success' else 7)
                self.assertNotIn('command', command)
                self.assertNotIn('cwd', command)
                if mode == 'capture-error':
                    self.assertEqual(command['captureError'], 'log-read-failed')
                if write_failure:
                    self.assertIn('diagnostic receipt write failed; no success is certified.', console.getvalue())
                    self.assertNotEqual(retained['status'], 'passed')
                    if write_failure != 'outer-finally':
                        self.assertEqual(receipt['receiptWriteError'], 'status-write-failed')
                    if write_failure == 'command-finally':
                        self.assertEqual(retained, receipt)  # Later natural save retained the failure flag.
                    else:
                        self.assertEqual(retained['status'], 'preparing')  # Stale, never terminal success.
                else:
                    self.assertEqual(retained, receipt)
                    self.assertNotIn('receiptWriteError', receipt)
                for value in ('secret', 'token', 'private.example', '/private', 'SECRET_ENV', 'hidden'):
                    self.assertNotIn(value, json.dumps(save_attempts) + json.dumps(retained) + console.getvalue())


if __name__ == '__main__':
    unittest.main()
