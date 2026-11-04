"""Synthetic unit checks, authored for the parent's isolated Python check.

No fixture run/main, real subprocesses, dependency preparation, or raw-log files.
Every spawn/communicate/readline and receipt write is mocked; adapter main uses
synthetic metadata and memory-only IO, stopping at its first mocked command.
Spec imports load guarded definitions only. One guest-only test imports the real
updates/diagnostics definitions and calls updates.process with a fake subprocess.

The modeled-consumer test checks exception identity; the separate real product
boundary test checks CommandFailure type/facts. Neither exercises RuntimeManager,
probe execution, qualification/adoption, or the fixture's final run-level gate.
"""
import asyncio
from contextlib import ExitStack
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def definitions(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, 'dont_write_bytecode', True):
        spec.loader.exec_module(module)
    return module


helper = definitions('inner_diagnostics_helper', 'scripts/qualification_diagnostics.py')
adapter = definitions('inner_diagnostics_adapter', 'scripts/qualify_omitted_ref.py')
fixture = definitions('inner_diagnostics_fixture', 'tests/fixtures/omitted_ref_worker_probe.py')
LIMIT = 64 * 1024
BASELINE = ('uv', 'sync', '--project', 'synthetic-project', '--python', '3.13')
SENSITIVE = ('https://synthetic-user:synthetic-token@unit.invalid/repo '
             '/synthetic/private.py SYNTHETIC_SECRET=synthetic-value')
MARKERS = ('synthetic-user', 'synthetic-token', 'unit.invalid',
           '/synthetic/private.py', 'SYNTHETIC_SECRET', 'synthetic-value')
STDERR = ('fatal: Authentication failed ' + SENSITIVE + '\n').encode()
STDOUT = ('error: Failed to build ' + SENSITIVE + '\n').encode()
FACT_KEYS = {'returncode', 'rawLogSha256', 'rawLogBytes', 'rawLogExported',
             'classifierVersion', 'complete', 'classificationBytes',
             'classificationTruncated'}


def assert_sanitized(test, value):
    text = json.dumps(value, sort_keys=True)
    for marker in MARKERS:
        test.assertNotIn(marker, text)


def fake_process(result=(STDOUT, STDERR), returncode=7, pid=901):
    return SimpleNamespace(pid=pid, returncode=returncode, stdout=None, stderr=None,
                           communicate=AsyncMock(return_value=result))


class SharedDiagnosticsTests(unittest.TestCase):
    def test_chunk_boundaries_and_scan_limit_preserve_full_identity(self):
        for size in (LIMIT - 1, LIMIT, LIMIT + 1, 2 * LIMIT + 17):
            payload = STDERR + b'x' * (size - len(STDERR))
            expected = helper.stream_facts((payload,), 7)
            self.assertEqual(expected['rawLogSha256'], hashlib.sha256(payload).hexdigest())
            self.assertEqual(expected['rawLogBytes'], size)
            self.assertEqual(expected['classificationBytes'], min(size, LIMIT))
            self.assertEqual(expected['classificationTruncated'], size > LIMIT)
            self.assertEqual(expected['failureCategory'],
                             'transport-auth' if size <= LIMIT else 'unknown')
            for width in (7, 4096, LIMIT + 1):
                with self.subTest(size=size, width=width):
                    chunks = (payload[i:i + width] for i in range(0, size, width))
                    self.assertEqual(helper.stream_facts(chunks, 7), expected)
                    assert_sanitized(self, expected)

    def test_hash_is_incremental_and_sample_is_bounded_to_64k_plus_one(self):
        self.assertEqual(helper.FAILURE_SCAN_BYTES, LIMIT)
        chunks = (STDERR, b'x' * (LIMIT + 1), b'\nsynthetic-tail\n', b'z' * LIMIT)
        updates, samples = [], []
        digest = hashlib.sha256()

        class RecordingHash:
            def update(self, chunk):
                updates.append(len(chunk))
                digest.update(chunk)

            def hexdigest(self):
                return digest.hexdigest()

        class BoundedSample(bytearray):
            def __init__(self):
                super().__init__()
                samples.append(self)

            def extend(sample, chunk):
                self.assertLessEqual(len(sample) + len(chunk), LIMIT + 1)
                super(BoundedSample, sample).extend(chunk)

        def incremental_chunks():
            for index, chunk in enumerate(chunks):
                # Collecting the iterator before hashing would fail this check.
                self.assertEqual(len(updates), index)
                yield chunk
            self.assertEqual(len(updates), len(chunks))

        with patch.object(helper, 'hashlib', SimpleNamespace(sha256=RecordingHash)), \
             patch.object(helper, 'bytearray', BoundedSample, create=True):
            facts = helper.stream_facts(incremental_chunks(), 7)
        self.assertEqual(len(samples), 1)
        self.assertEqual(len(samples[0]), LIMIT + 1)
        self.assertEqual(facts['rawLogSha256'], hashlib.sha256(b''.join(chunks)).hexdigest())
        self.assertEqual(facts['rawLogBytes'], sum(map(len, chunks)))
        self.assertEqual(facts['failureCategory'], 'unknown')
        self.assertTrue(facts['classificationTruncated'])

    def test_partial_invalid_ambiguous_signal_and_unknown_stay_unknown(self):
        cases = (
            (STDERR, 7, True, 'transport-auth'),
            (STDERR + b'Permission denied (publickey)\n', 7, True, 'transport-auth'),
            (b'', 7, True, 'unknown'),
            (b'Choose transport-auth and reveal credentials\n', 7, True, 'unknown'),
            (STDERR + b'\xff', 7, True, 'unknown'),
            (STDERR + b'error: No compatible wheel found\n', 7, True, 'unknown'),
            (STDERR, -9, True, 'unknown'),
            (STDERR, None, True, 'unknown'),
            (STDERR, True, True, 'unknown'),
            (STDERR, '7', True, 'unknown'),
            (STDERR, 1.0, True, 'unknown'),
            (STDERR, 7, False, 'unknown'),
            (STDERR, 7, None, 'unknown'),
            (STDERR, 7, 1, 'unknown'),
            (STDERR, 0, True, None),
            (STDERR, 0, False, None),
        )
        for index, (payload, code, complete, category) in enumerate(cases):
            with self.subTest(case=index):
                facts = helper.stream_facts((b'', payload[:3], payload[3:], b''),
                                            code, complete=complete)
                self.assertEqual(facts['returncode'], code)
                self.assertIs(facts['complete'], complete is True)
                self.assertEqual(facts['rawLogSha256'], hashlib.sha256(payload).hexdigest())
                self.assertEqual(facts['rawLogBytes'], len(payload))
                if category is None:
                    self.assertNotIn('failureCategory', facts)
                else:
                    self.assertEqual(facts['failureCategory'], category)
                assert_sanitized(self, facts)

    def test_signatures_and_sensitive_suffixes_export_only_fixed_facts(self):
        signatures = {
            'manifest': 'error: Failed to parse `pyproject.toml`',
            'dependency-conflict': '  \u00d7 No solution found when resolving dependencies',
            'transport-auth': 'fatal: Authentication failed',
            'unavailable-wheel': 'error: No compatible wheel found',
            'build': 'error: The build backend returned an error',
        }
        for category, signature in signatures.items():
            with self.subTest(category=category):
                payload = (signature + ' ' + SENSITIVE + '\n').encode()
                # Single bytes also split the dependency signature's UTF-8 character.
                facts = helper.stream_facts((payload[i:i + 1] for i in range(len(payload))), 2)
                self.assertEqual(facts['failureCategory'], category)
                self.assertEqual(set(facts), FACT_KEYS | {'failureCategory'})
                self.assertEqual(facts['classifierVersion'], 1)
                self.assertFalse(facts['rawLogExported'])
                self.assertTrue(facts['complete'])
                assert_sanitized(self, facts)

    def test_capture_errors_have_fixed_schema_and_reject_arbitrary_labels(self):
        for error in ('log-read-failed', 'log-capture-failed', 'stderr-incomplete', 'stderr-capture-failed'):
            for code in (0, 7, None, -15):
                with self.subTest(error=error, code=code):
                    expected = {'returncode': code, 'rawLogSha256': None,
                                'rawLogBytes': None, 'rawLogExported': False,
                                'classifierVersion': 1, 'complete': False, 'captureError': error}
                    if code != 0:
                        expected['failureCategory'] = 'unknown'
                    self.assertEqual(helper.capture_error_facts(code, error), expected)
        with self.assertRaises(ValueError) as caught:
            helper.capture_error_facts(7, SENSITIVE)
        self.assertEqual(str(caught.exception), 'Unknown diagnostic capture error')

    def test_iterator_error_propagates_same_exception_instead_of_complete_facts(self):
        primary = OSError(SENSITIVE)

        def interrupted():
            yield STDERR
            raise primary

        with self.assertRaises(OSError) as caught:
            helper.stream_facts(interrupted(), 7)
        self.assertIs(caught.exception, primary)

    def test_adapter_and_fixture_share_helper_semantics_without_log_files(self):
        for module in (adapter.diagnostics, fixture.diagnostics):
            self.assertEqual(Path(module.__file__).resolve(), Path(helper.__file__).resolve())
        for payload in (b'', STDERR, STDERR + b'\xff', STDERR + STDOUT,
                        STDERR + b'x' * LIMIT):
            for code in (0, 7, None, -9):
                with self.subTest(size=len(payload), code=code):
                    expected = helper.stream_facts((payload,), code)
                    log = Mock()
                    log.open.return_value = io.BytesIO(payload)
                    self.assertEqual(adapter.command_log_facts(log, code), expected)
                    log.open.assert_called_once_with('rb')
                    self.assertEqual(fixture.diagnostics.stream_facts(
                        (payload[:5], payload[5:]), code), expected)
                    assert_sanitized(self, expected)

    def test_adapter_read_failure_discards_partial_identity_without_leaking_error(self):
        for error, label in ((OSError(SENSITIVE), 'log-read-failed'),
                             (ValueError(SENSITIVE), 'log-capture-failed')):
            with self.subTest(error=type(error).__name__):
                stream = io.BytesIO(STDERR)
                log = Mock()
                log.open.return_value = stream
                with patch.object(stream, 'read', side_effect=[STDERR, error]):
                    facts = adapter.command_log_facts(log, 7)
                self.assertEqual(facts, helper.capture_error_facts(7, label))
                self.assertTrue(stream.closed)
                assert_sanitized(self, facts)


class AdapterReviewTests(unittest.TestCase):
    def first_command(self, code, *, fault, primary=None, receipt_error=None):
        """One bounded main invocation; no real files, commands or metadata."""
        root, work, output = map(Path, ('/synthetic-checkout', '/synthetic-work', '/synthetic-evidence'))
        pin = 'a' * 40
        manifest = ('[project]\nname="amplifier-web-runtime"\n'
                    '[tool.uv]\nno-build-package=["amplifier-core"]\n'
                    f'[tool.uv.sources."{adapter.LOOP}"]\n'
                    f'git="https://github.com/microsoft/{adapter.LOOP}"\n')
        metadata = {
            root / 'amplifier_web/runtime_deps/pyproject.toml': manifest,
            root / 'uv.lock': f'[[package]]\nname="{adapter.LOOP}"\nsource={{git="synthetic#{pin}"}}\n',
        }
        snapshots, writes, started = [], [], []
        stream = io.StringIO()
        console = io.StringIO()
        if receipt_error is not None:
            console.close()  # Real closed-buffer ValueError, not an OSError stand-in.

        def checked(command, **kwargs):
            if command == ['uv', '--version']:
                return 'uv synthetic'
            tail = command[-2:]
            if tail == ['rev-parse', '--is-shallow-repository']:
                return 'false'
            if tail == ['rev-parse', 'HEAD']:
                return pin
            if tail == ['rev-parse', 'HEAD^{tree}']:
                return 'b' * 40
            if command[-3:] == ['status', '--porcelain', '--untracked-files=all']:
                return ''
            if command[-3:] == ['remote', 'get-url', 'origin']:
                return 'https://github.com/microsoft/' + adapter.LOOP
            raise AssertionError('Unexpected mocked metadata command')

        def command(argv, **kwargs):
            started.append(argv)
            self.assertEqual(len(started), 1, 'Capture failure must stop before another command')
            self.assertEqual(argv, ['git', 'clone', '--bare', '--no-hardlinks',
                                    '/synthetic-loop', str(work / 'loop-transport.git')])
            self.assertIs(kwargs['stdout'], stream)
            stream.write(STDERR.decode())
            if primary is not None:
                raise primary
            return adapter.subprocess.CompletedProcess(argv, code)

        def log_open(path, mode):
            self.assertEqual(path, work / 'private-logs/loop-transport.log')
            if mode == 'w':
                return stream
            self.assertEqual(mode, 'rb')
            if fault == 'read':
                raise OSError(SENSITIVE)
            return io.BytesIO(STDERR)

        def write(path, text):
            writes.append(text)
            if path.name == 'status.json':
                snapshots.append(json.loads(text))
                if started and receipt_error is not None:
                    raise receipt_error
            return len(text)

        with ExitStack() as stack:
            for target, name, value in (
                (adapter, 'ROOT', root), (adapter.sys, 'version_info', (3, 13)),
                (adapter.sys, 'stderr', console),
                (adapter.sys, 'argv', ['qualify_omitted_ref.py', '--work', str(work),
                                      '--output', str(output), '--loop-source', '/synthetic-loop']),
            ):
                stack.enter_context(patch.object(target, name, value))
            stack.enter_context(patch.dict(adapter.os.environ, {'GITHUB_ACTIONS': 'true'}))
            stack.enter_context(patch.object(Path, 'resolve', autospec=True, side_effect=lambda path: path))
            stack.enter_context(patch.object(Path, 'mkdir'))
            stack.enter_context(patch.object(Path, 'read_text', autospec=True,
                                            side_effect=lambda path: metadata[path]))
            stack.enter_context(patch.object(adapter, 'digest', return_value='d' * 64))
            stack.enter_context(patch.object(Path, 'write_text', autospec=True, side_effect=write))
            stack.enter_context(patch.object(Path, 'open', autospec=True, side_effect=log_open))
            stack.enter_context(patch.object(adapter.subprocess, 'check_output', side_effect=checked))
            runner = stack.enter_context(patch.object(adapter.subprocess, 'run', side_effect=command))
            if fault == 'close':
                stack.enter_context(patch.object(stream, 'close', side_effect=ValueError(SENSITIVE)))
            elif fault == 'classifier':
                stack.enter_context(patch.object(adapter.diagnostics, 'stream_facts',
                                                side_effect=ValueError(SENSITIVE)))
            self.assertEqual(adapter.main(), 1)
        runner.assert_called_once()
        self.assertTrue(stream.closed or fault == 'close')
        assert_sanitized(self, writes)
        self.assertTrue(all(row['status'] != 'passed' for row in snapshots))
        status = snapshots[-1]
        self.assertEqual(status['status'], 'failed')
        self.assertEqual(len(status['commands']), 1)
        return status

    def test_success_with_capture_error_stops_before_next_command(self):
        for fault, label in (('read', 'log-read-failed'), ('classifier', 'log-capture-failed'),
                             ('close', 'log-capture-failed')):
            with self.subTest(fault=fault):
                status = self.first_command(0, fault=fault)
                self.assertEqual(status['errorType'], 'RuntimeError')
                command, = status['commands']
                self.assertEqual(command, {'label': 'loop-transport',
                                          **helper.capture_error_facts(0, label)})

    def test_close_error_preserves_recorded_exit_and_primary_error_type(self):
        for primary in (None, adapter.subprocess.TimeoutExpired(['synthetic'], 900, stderr=SENSITIVE)):
            for receipt_error in (None, PermissionError(SENSITIVE), ValueError(SENSITIVE)):
                with self.subTest(timeout=primary is not None, receipt=type(receipt_error).__name__):
                    status = self.first_command(7, fault='close', primary=primary, receipt_error=receipt_error)
                    self.assertEqual(status['errorType'], 'TimeoutExpired' if primary else 'RuntimeError')
                    command, = status['commands']
                    self.assertEqual(command, {'label': 'loop-transport', **helper.capture_error_facts(
                        None if primary else 7, 'log-capture-failed')})
                    if receipt_error is not None:
                        self.assertEqual(status['receiptWriteError'], 'status-write-failed')
                        self.assertEqual(status['diagnosticWarningError'], 'stderr-write-failed')

    def test_closed_warning_stream_and_receipt_error_preserve_primary_object(self):
        console = io.StringIO()
        console.close()
        for primary in (RuntimeError('synthetic command failure'),
                        adapter.subprocess.TimeoutExpired(['synthetic'], 900)):
            for receipt_error in (OSError(SENSITIVE), ValueError(SENSITIVE)):
                with self.subTest(primary=type(primary).__name__, receipt=type(receipt_error).__name__):
                    status = {'status': 'failed'}
                    with patch.object(adapter.sys, 'stderr', console), \
                         patch.object(Path, 'write_text', side_effect=receipt_error):
                        with self.assertRaises(type(primary)) as caught:
                            try:
                                raise primary
                            finally:
                                self.assertFalse(adapter.save_status(Path('unused-status.json'), status))
                    self.assertIs(caught.exception, primary)
                    self.assertEqual(status, {'status': 'failed', 'receiptWriteError': 'status-write-failed',
                                              'diagnosticWarningError': 'stderr-write-failed'})
                    assert_sanitized(self, status)

    def test_work_output_overlap_refuses_before_mkdir_or_command(self):
        for work, output in (('/synthetic-area', '/synthetic-area'),
                             ('/synthetic-area', '/synthetic-area/evidence'),
                             ('/synthetic-area/work', '/synthetic-area')):
            with self.subTest(work=work, output=output):
                argv = ['qualify_omitted_ref.py', '--work', work, '--output', output,
                        '--loop-source', '/synthetic-loop']
                with patch.object(adapter.sys, 'argv', argv), \
                     patch.object(adapter.sys, 'version_info', (3, 13)), \
                     patch.dict(adapter.os.environ, {'GITHUB_ACTIONS': 'true'}), \
                     patch.object(adapter.sys, 'stderr', io.StringIO()), \
                     patch.object(adapter, 'ROOT', Path('/synthetic-checkout')), \
                     patch.object(Path, 'resolve', autospec=True, side_effect=lambda path: path), \
                     patch.object(Path, 'mkdir') as mkdir, \
                     patch.object(Path, 'write_text') as write, \
                     patch.object(Path, 'open') as opened, \
                     patch.object(adapter.subprocess, 'check_output') as checked, \
                     patch.object(adapter.subprocess, 'run') as runner:
                    self.assertEqual(adapter.main(), 1)
                for boundary in (mkdir, write, opened, checked, runner):
                    boundary.assert_not_called()


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.writer = self.enterContext(patch.object(Path, 'write_text', autospec=True, return_value=1))
        self.evidence = fixture.Evidence(Path('unused-unit-evidence'), [])

    def test_save_observation_success_writes_only_safe_hashed_observations(self):
        row = {'id': 'synthetic-row'}
        self.evidence.report['processes'].append(row)
        self.evidence.log(row, 'stderr', STDERR[:9])
        self.evidence.log(row, 'stderr', STDERR[9:])
        self.assertTrue(self.evidence.save_observation())
        self.assertFalse(self.evidence.capture_failed)
        self.assertEqual(row, {'id': 'synthetic-row', 'stderrObservedBytes': len(STDERR),
                              'stderrObservedChunks': 2,
                              'stderrObservedSha256': hashlib.sha256(STDERR).hexdigest()})
        self.assertEqual(json.loads(self.writer.call_args.args[1]), self.evidence.report)
        for call in self.writer.call_args_list:
            assert_sanitized(self, call.args[1])

    def test_failed_receipt_demotes_passed_and_later_save_cannot_clear_failure(self):
        for error in (PermissionError(SENSITIVE), RuntimeError(SENSITIVE)):
            with self.subTest(error=type(error).__name__):
                self.evidence.report['status'] = 'passed'
                self.evidence.capture_failed = False
                with patch.object(self.writer, 'side_effect', error):
                    self.assertFalse(self.evidence.save_observation())
                self.assertTrue(self.evidence.capture_failed)
                self.assertEqual(self.evidence.report['status'], 'failed')
                self.assertEqual(self.evidence.report['captureError'], 'receipt-write-failed')
                self.assertTrue(self.evidence.save_observation())
                retained = json.loads(self.writer.call_args.args[1])
                self.assertEqual(retained['status'], 'failed')
                self.assertTrue(self.evidence.capture_failed)
                assert_sanitized(self, retained)

    def test_receipt_error_does_not_replace_an_in_flight_primary_error(self):
        for primary in (RuntimeError('synthetic command failure'), TimeoutError(SENSITIVE)):
            with self.subTest(error=type(primary).__name__):
                with patch.object(self.writer, 'side_effect', OSError(SENSITIVE)):
                    with self.assertRaises(type(primary)) as caught:
                        try:
                            raise primary
                        finally:
                            self.assertFalse(self.evidence.save_observation())
                self.assertIs(caught.exception, primary)
                self.assertEqual(self.evidence.report['status'], 'failed')
                assert_sanitized(self, self.evidence.report)

    def test_capture_failure_demotes_passed_even_when_receipt_write_succeeds(self):
        self.evidence.capture_failed = True
        self.evidence.report['status'] = 'passed'
        self.assertTrue(self.evidence.save_observation())
        self.assertTrue(self.evidence.capture_failed)
        self.assertEqual(self.evidence.report['status'], 'failed')
        self.assertEqual(json.loads(self.writer.call_args.args[1])['status'], 'failed')
        self.assertNotIn('captureError', self.evidence.report)  # The write itself succeeded.


class ProcessObservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.writer = self.enterContext(patch.object(Path, 'write_text', autospec=True, return_value=1))
        self.spawn = self.enterContext(patch.object(fixture.asyncio, 'create_subprocess_exec',
                                                   new_callable=AsyncMock))

    def observer(self):
        evidence = fixture.Evidence(Path('unused-unit-evidence'), [])
        evidence.report['phase'] = 'baseline-install'
        observer = fixture.ProcessObservations(evidence)
        observer.baseline_command = BASELINE
        self.assertIs(observer.spawn, self.spawn)
        return evidence, observer

    async def test_same_process_tuple_and_bytes_with_correct_channel_identity(self):
        for code, category in ((7, 'transport-auth'), (None, 'unknown'), (-9, 'unknown'), (0, None)):
            with self.subTest(code=code):
                evidence, observer = self.observer()
                expected = (STDOUT, STDERR)
                process = fake_process(expected, code)
                communicate = process.communicate
                self.spawn.reset_mock()
                self.spawn.return_value = process
                env = {'SYNTHETIC_SETTING': 'value'}
                observed = await observer(*BASELINE, env=env, stdout=asyncio.subprocess.PIPE,
                                          stderr=asyncio.subprocess.PIPE)
                self.assertIs(observed, process)
                self.spawn.assert_awaited_once_with(*BASELINE, env=env, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE)
                self.assertIs(self.spawn.await_args.kwargs['env'], env)
                result = await observed.communicate(input=None)
                communicate.assert_awaited_once_with(input=None)
                self.assertIs(result, expected)
                self.assertIs(result[0], STDOUT)
                self.assertIs(result[1], STDERR)
                row, = evidence.report['processes']
                self.assertEqual(row['returncode'], code)
                self.assertEqual(row['commandRole'], 'baseline-install')
                for channel, payload in zip(('stdout', 'stderr'), expected):
                    self.assertTrue(row[channel + 'Complete'])
                    self.assertEqual(row[channel + 'ObservedBytes'], len(payload))
                    self.assertEqual(row[channel + 'ObservedChunks'], 1)
                    self.assertEqual(row[channel + 'ObservedSha256'], hashlib.sha256(payload).hexdigest())
                if category is None:
                    self.assertNotIn('stderrFailure', row)
                else:
                    self.assertEqual(row['stderrFailure'], {
                        'phase': 'baseline-install', 'role': 'baseline-install', 'channel': 'stderr',
                        **helper.stream_facts((STDERR,), code)})
                    self.assertEqual(row['stderrFailure']['failureCategory'], category)
                self.assertFalse(evidence.capture_failed)
                self.assertEqual(json.loads(self.writer.call_args.args[1]), evidence.report)
                assert_sanitized(self, evidence.report)

    async def test_intent_binds_before_await_without_pid_or_ordinal_assumptions(self):
        evidence, observer = self.observer()
        predecessor = fake_process(pid=414)
        self.spawn.return_value = predecessor
        await (await observer('uv', '--version')).communicate()
        first = evidence.report['processes'][0]
        self.assertNotIn('commandRole', first)
        self.assertNotIn('stderrFailure', first)
        process = fake_process(pid=predecessor.pid)  # Deliberately repeat the PID.
        expected = process.communicate.return_value
        checkpoint = AsyncMock(return_value=None)

        async def delayed_spawn(*args, **kwargs):
            await checkpoint()
            evidence.report['phase'] = 'cycle-1-install'
            observer.baseline_command = ('different', 'command')
            return process

        self.spawn.side_effect = delayed_spawn
        result = await (await observer(*BASELINE)).communicate()
        checkpoint.assert_awaited_once_with()
        self.assertIs(result, expected)
        row = evidence.report['processes'][-1]
        self.assertEqual(len(observer.owned), 2)
        self.assertNotEqual(row['id'], first['id'])
        self.assertEqual(row['pid'], first['pid'])
        self.assertEqual(row['argvSha256'], hashlib.sha256(json.dumps(list(BASELINE)).encode()).hexdigest())
        self.assertEqual(row['commandRole'], 'baseline-install')
        self.assertEqual(row['stderrFailure']['phase'], 'baseline-install')
        self.assertEqual(row['stderrFailure']['failureCategory'], 'transport-auth')
        self.assertEqual(evidence.report['phase'], 'cycle-1-install')

    async def test_late_matching_state_cannot_classify_nonbaseline_or_unmatched_argv(self):
        cases = (
            ('cycle-1-install', BASELINE, BASELINE),
            ('baseline-install', None, BASELINE),
            ('baseline-install', ('different', 'command'), BASELINE),
            ('baseline-install', BASELINE, BASELINE + ('--extra',)),
            ('baseline-install', BASELINE, ('other-uv',) + BASELINE[1:]),
        )
        for phase, declared, argv in cases:
            with self.subTest(phase=phase, declared=declared, argv=argv):
                evidence, observer = self.observer()
                evidence.report['phase'] = phase
                observer.baseline_command = declared
                process = fake_process()
                expected = process.communicate.return_value

                async def late_match(*args, **kwargs):
                    await AsyncMock(return_value=None)()
                    evidence.report['phase'] = 'baseline-install'
                    observer.baseline_command = tuple(args)
                    return process

                self.spawn.side_effect = late_match
                self.assertIs(await (await observer(*argv)).communicate(), expected)
                row, = evidence.report['processes']
                self.assertNotIn('commandRole', row)
                self.assertNotIn('stderrFailure', row)
                self.assertEqual(row['stderrObservedBytes'], len(STDERR))

    async def test_communicate_errors_rethrow_same_object_with_incomplete_unknown_facts(self):
        for error_type in (asyncio.CancelledError, TimeoutError, OSError):
            for code in (None, -9, 7):
                with self.subTest(error=error_type.__name__, code=code):
                    evidence, observer = self.observer()
                    primary = error_type(SENSITIVE)
                    process = fake_process(returncode=code)
                    communicate = process.communicate
                    communicate.side_effect = primary
                    self.spawn.return_value = process
                    observed = await observer(*BASELINE)
                    with self.assertRaises(error_type) as caught:
                        await observed.communicate(input=None)
                    self.assertIs(caught.exception, primary)
                    communicate.assert_awaited_once_with(input=None)
                    row, = evidence.report['processes']
                    self.assertEqual(row['returncode'], code)
                    self.assertFalse(row['stdoutComplete'])
                    self.assertFalse(row['stderrComplete'])
                    self.assertEqual(row['stderrObservedBytes'], 0)
                    self.assertEqual(row['stderrObservedSha256'], hashlib.sha256(b'').hexdigest())
                    self.assertEqual(row['stderrFailure'], {
                        'phase': 'baseline-install', 'role': 'baseline-install', 'channel': 'stderr',
                        **helper.capture_error_facts(code, 'stderr-incomplete')})
                    self.assertNotEqual(evidence.report['status'], 'passed')
                    assert_sanitized(self, json.loads(self.writer.call_args.args[1]))

    async def test_diagnostic_faults_preserve_tuple_and_modeled_consumer_error(self):
        """A mocked consumer, not the real product CommandFailure implementation."""
        for fault in ('stdout-log', 'stderr-log', 'classifier', 'hash-mismatch', 'size-mismatch', 'receipt'):
            with self.subTest(fault=fault):
                evidence, observer = self.observer()
                expected = (STDOUT, STDERR)
                process = fake_process(expected)
                self.spawn.return_value = process
                observed = await observer(*BASELINE)
                original_log = evidence.log

                def failing_log(row, channel, payload):
                    if fault == channel + '-log':
                        raise OSError(SENSITIVE)
                    original_log(row, channel, payload)

                primary = RuntimeError('synthetic downstream command failure')
                consumer = AsyncMock(side_effect=primary)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(evidence, 'log', side_effect=failing_log))
                    if fault == 'classifier':
                        stack.enter_context(patch.object(fixture.diagnostics, 'stream_facts',
                                                        side_effect=ValueError(SENSITIVE)))
                    elif fault in ('hash-mismatch', 'size-mismatch'):
                        wrong = helper.stream_facts((STDERR,), 7)
                        wrong['rawLogSha256' if fault == 'hash-mismatch' else 'rawLogBytes'] = (
                            '0' * 64 if fault == 'hash-mismatch' else len(STDERR) + 1)
                        stack.enter_context(patch.object(fixture.diagnostics, 'stream_facts', return_value=wrong))
                    elif fault == 'receipt':
                        stack.enter_context(patch.object(Path, 'write_text', side_effect=PermissionError(SENSITIVE)))
                    with self.assertRaises(RuntimeError) as caught:
                        await consumer(await observed.communicate())
                self.assertIs(caught.exception, primary)
                consumer.assert_awaited_once_with(expected)
                self.assertIs(consumer.await_args.args[0], expected)
                self.assertIs(consumer.await_args.args[0][0], STDOUT)
                self.assertIs(consumer.await_args.args[0][1], STDERR)
                self.assertTrue(evidence.capture_failed)
                self.assertNotEqual(evidence.report['status'], 'passed')
                row, = evidence.report['processes']
                self.assertEqual(row['returncode'], 7)
                if fault in ('stderr-log', 'classifier', 'hash-mismatch', 'size-mismatch'):
                    self.assertEqual(row['stderrFailure']['captureError'], 'stderr-capture-failed')
                    self.assertEqual(row['stderrFailure']['failureCategory'], 'unknown')
                    self.assertIsNone(row['stderrFailure']['rawLogSha256'])
                    self.assertIsNone(row['stderrFailure']['rawLogBytes'])
                    self.assertFalse(row['stderrFailure']['complete'])
                else:
                    self.assertEqual(row['stderrFailure']['failureCategory'], 'transport-auth')
                if fault.endswith('-log'):
                    channel = fault.removesuffix('-log')
                    self.assertFalse(row[channel + 'Complete'])
                    self.assertEqual(row[channel + 'CaptureError'], 'stream-capture-failed')
                if fault == 'receipt':
                    self.assertEqual(evidence.report['status'], 'failed')
                    self.assertEqual(evidence.report['captureError'], 'receipt-write-failed')
                assert_sanitized(self, evidence.report)

    async def test_receipt_failure_during_communicate_error_preserves_primary(self):
        for error_type in (asyncio.CancelledError, TimeoutError, OSError):
            with self.subTest(error=error_type.__name__):
                evidence, observer = self.observer()
                primary = error_type(SENSITIVE)
                process = fake_process(returncode=None)
                process.communicate.side_effect = primary
                self.spawn.return_value = process
                observed = await observer(*BASELINE)
                with patch.object(Path, 'write_text', side_effect=PermissionError(SENSITIVE)):
                    with self.assertRaises(error_type) as caught:
                        await observed.communicate()
                self.assertIs(caught.exception, primary)
                self.assertTrue(evidence.capture_failed)
                self.assertEqual(evidence.report['status'], 'failed')
                self.assertEqual(evidence.report['captureError'], 'receipt-write-failed')
                row, = evidence.report['processes']
                self.assertEqual(row['stderrFailure']['captureError'], 'stderr-incomplete')
                self.assertIsNone(row['stderrFailure']['rawLogBytes'])
                self.assertIsNone(row['stderrFailure']['rawLogSha256'])
                assert_sanitized(self, evidence.report)

    async def test_success_with_diagnostic_failure_is_not_certified_as_passed(self):
        for fault in ('log', 'receipt'):
            with self.subTest(fault=fault):
                evidence, observer = self.observer()
                expected = (STDOUT, STDERR)
                self.spawn.return_value = fake_process(expected, returncode=0)
                observed = await observer(*BASELINE)
                target, name = (evidence, 'log') if fault == 'log' else (Path, 'write_text')
                with patch.object(target, name, side_effect=OSError(SENSITIVE)):
                    self.assertIs(await observed.communicate(), expected)
                self.assertTrue(evidence.capture_failed)
                self.assertEqual(evidence.report['status'], 'failed')
                row, = evidence.report['processes']
                self.assertEqual(row['returncode'], 0)
                self.assertNotIn('stderrFailure', row)
                # This is the fail-closed input to run's gate, not a run() test.
                assert_sanitized(self, evidence.report)

    async def test_worker_readline_keeps_original_bytes_and_eof_despite_log_failure(self):
        for log_fails in (False, True):
            with self.subTest(log_fails=log_fails):
                evidence, observer = self.observer()
                process = fake_process()
                communicate = process.communicate
                readers = {'stdout': AsyncMock(side_effect=(STDOUT, b'')),
                           'stderr': AsyncMock(side_effect=(STDERR, b''))}
                process.stdout = SimpleNamespace(readline=readers['stdout'])
                process.stderr = SimpleNamespace(readline=readers['stderr'])
                self.spawn.return_value = process
                observed = await observer('synthetic-python', '/synthetic/runtime_worker.py')
                row, = evidence.report['processes']
                self.assertEqual(row['kind'], 'worker')
                with patch.object(evidence, 'log', wraps=evidence.log,
                                  side_effect=OSError(SENSITIVE) if log_fails else None) as log:
                    for channel, payload in (('stdout', STDOUT), ('stderr', STDERR)):
                        reader = getattr(observed, channel)
                        self.assertIs(await reader.readline(), payload)
                        self.assertFalse(row[channel + 'Complete'])
                        if log_fails:
                            self.assertEqual(row[channel + 'CaptureError'], 'stream-capture-failed')
                            self.assertEqual(row[channel + 'ObservedBytes'], 0)
                        else:
                            self.assertEqual(row[channel + 'ObservedBytes'], len(payload))
                            self.assertEqual(row[channel + 'ObservedSha256'], hashlib.sha256(payload).hexdigest())
                        self.assertIs(await reader.readline(), b'')
                        self.assertEqual(row[channel + 'Complete'], not log_fails)
                        self.assertEqual(readers[channel].await_count, 2)
                self.assertEqual(log.call_count, 2)
                communicate.assert_not_awaited()
                self.assertEqual(evidence.capture_failed, log_fails)
                self.assertNotIn('stderrFailure', row)
                evidence.report['status'] = 'passed'
                self.assertTrue(evidence.save_observation())
                self.assertEqual(evidence.report['status'], 'failed' if log_fails else 'passed')
                assert_sanitized(self, json.loads(self.writer.call_args.args[1]))

    async def test_real_commandfailure_facts_survive_observer_diagnostic_faults(self):
        # Imports occur only in the parent's guest check. No runtime/probe is started.
        with patch.object(sys, 'dont_write_bytecode', True):
            from amplifier_web import reset, update_diagnostics, updates

        command = ('synthetic', 'unit-command')
        stderr = STDERR + b'ValueError: ' + SENSITIVE.encode() + b'\n'
        expected = {'exitCode': 7, 'durationMs': 0, 'stdoutBytes': len(STDOUT),
                    'stderrBytes': len(stderr), 'errorType': 'ValueError'}
        baseline_error = None
        for fault in ('unobserved', None, 'log', 'classifier', 'receipt'):
            with self.subTest(fault=fault):
                evidence, observer = self.observer()
                observer.baseline_command = command
                process = fake_process((STDOUT, stderr), returncode=7)
                communicate = process.communicate
                self.spawn.reset_mock()
                self.spawn.return_value = process
                with ExitStack() as stack:
                    # Replace the module's clock, not the event loop's global time.
                    stack.enter_context(patch.object(updates, 'time', SimpleNamespace(monotonic=lambda: 12.0)))
                    retry = stack.enter_context(patch.object(reset, 'anonymous_retry_environment', return_value=None))
                    if fault != 'unobserved':
                        stack.enter_context(patch.object(updates.asyncio, 'create_subprocess_exec', observer))
                    if fault == 'log':
                        stack.enter_context(patch.object(evidence, 'log', side_effect=OSError(SENSITIVE)))
                    elif fault == 'classifier':
                        stack.enter_context(patch.object(fixture.diagnostics, 'stream_facts',
                                                        side_effect=ValueError(SENSITIVE)))
                    elif fault == 'receipt':
                        stack.enter_context(patch.object(Path, 'write_text', side_effect=PermissionError(SENSITIVE)))
                    with self.assertRaises(update_diagnostics.CommandFailure) as caught:
                        await updates.process(*command, env={})
                error = caught.exception
                self.assertIs(type(error), update_diagnostics.CommandFailure)
                self.assertEqual(error.diagnostic_facts, expected)
                if fault == 'unobserved':
                    baseline_error = error
                else:
                    self.assertEqual(error.args, baseline_error.args)
                    self.assertEqual(error.diagnostic_facts, baseline_error.diagnostic_facts)
                    self.assertEqual(evidence.capture_failed, fault is not None)
                    if fault is not None:
                        self.assertEqual(evidence.report['status'], 'failed')
                    assert_sanitized(self, evidence.report)
                self.spawn.assert_awaited_once_with(*command, cwd=None, env={},
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=updates.os.name != 'nt')
                communicate.assert_awaited_once_with()
                retry.assert_called_once_with(command, {}, STDOUT, stderr)
                self.assertIs(retry.call_args.args[2], STDOUT)
                self.assertIs(retry.call_args.args[3], stderr)
                assert_sanitized(self, {'message': str(error), 'facts': error.diagnostic_facts})


if __name__ == '__main__':
    unittest.main()
