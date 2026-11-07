"""Export waits for admitted journal evidence without replaying shell work."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import threading

import pytest

from amplifier_foundation.session import SharedSessionStore
from amplifier_portability.capsule import read_capsule
from amplifier_web import portability, portability_data
from test_operations import event, final, output
from test_portability import export, host_env, setup_hosts


@pytest.mark.parametrize('write_fails', [False, True])
async def test_export_drains_shielded_journal_write_before_capturing_evidence(tmp_path, monkeypatch, write_fails):
    app, target, source, destination, workspace, _, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    release_write = threading.Event()
    entered_write = asyncio.Event()
    quiesced = asyncio.Event()
    observer = None
    job = None
    try:
        host_env(monkeypatch, source)
        await app.operations.observe(sid, sid, event())
        inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
        revision = inspected['repository']['sourceRevision']
        saved_workspace = await asyncio.to_thread(portability.capture_workspace, workspace, revision, 'carry_dirty')
        # Keep the real, unchanged fixture Git snapshot but remove unrelated Git
        # latency from the journal race. No provider/runtime admission is mocked.
        monkeypatch.setattr(portability, 'capture_workspace', lambda *_: copy.deepcopy(saved_workspace))
        ingest = app.operations.journal.ingest
        loop = asyncio.get_running_loop()

        def delayed_ingest(*args):
            # Block before the actual journal lock, as a queued executor job can.
            loop.call_soon_threadsafe(entered_write.set)
            if not release_write.wait(10):
                raise TimeoutError('Fixture journal write was not released')
            if write_fails:
                raise OSError('Fixture journal persistence failure')
            return ingest(*args)

        monkeypatch.setattr(app.operations.journal, 'ingest', delayed_ingest)
        observer = asyncio.create_task(app.operations.observe(sid, sid, final(sequence=2, total_output_bytes=0)))
        await asyncio.wait_for(entered_write.wait(), 2)
        # Worker shutdown cancels the bridge caller, but its shielded durable
        # write remains in Operations.pending and must remain part of export.
        observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)
        assert app.operations.pending

        quiesce = app.runtime.quiesce_for_handoff

        async def observed_quiesce(*args, **kwargs):
            result = await quiesce(*args, **kwargs)
            quiesced.set()
            return result

        monkeypatch.setattr(app.runtime, 'quiesce_for_handoff', observed_quiesce)
        started = (await app.dispatch('portability.export', {
            'sessionId': sid, 'destination': target.portability.node.identity['id'],
            'sourceRevision': revision,
            'expectedExecutionRevision': app._session(sid).get('executionRevision', 0),
            'mode': 'carry_dirty', 'reviewedContent': True}, command_id='pending-journal-export'))['result']
        job = next(iter(app.portability.jobs))
        await asyncio.wait_for(quiesced.wait(), 5)
        # The drain must leave app state readable while awaiting file evidence.
        await asyncio.wait_for(app.lock.acquire(), 1)
        app.lock.release()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(job), 0.5)
        assert SharedSessionStore(workspace, sid).transfer_fence()['phase'] == 'staged'

        release_write.set()
        await asyncio.wait_for(asyncio.shield(job), 5)
        receipt = app.portability.node.get(started['id'])
        if write_fails:
            assert receipt['phase'] == 'unknown'
            assert 'package' not in receipt
            assert app.portability.fenced(sid)
        else:
            assert receipt['phase'] == 'prepared'
            payload = read_capsule(Path(receipt['package']))['body']['payload']
            saved = next(row for row in payload['observations']['operations'] if row['record']['id'] == 'p1')
            assert saved['record']['state'] == 'completed'
            assert saved['record']['returncode'] == 0
            assert any(row['phase'] == 'finished' for row in saved['events'])
        assert not app.runtime.workers
    finally:
        release_write.set()
        if observer is not None:
            await asyncio.gather(observer, return_exceptions=True)
        if job is not None:
            await asyncio.gather(job, return_exceptions=True)
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


@pytest.mark.parametrize('expired', [False, True])
async def test_real_shell_output_preserves_exact_chunks_and_hash_only_event_evidence(tmp_path, monkeypatch, expired):
    app, target, source, destination, workspace, _, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        if expired:
            await app.dispatch('storage.cleanup.applyPreset', {'expectedRevision': 0,
                'preset': 'temporary-operation-output'}, command_id='output-lifetime')
        started = event()
        emitted = output(text='Exact saved shell output\n')
        completed = final(total_output_bytes=emitted['chunk']['source_bytes'])
        for observation in (started, emitted, completed):
            await app.operations.observe(sid, sid, observation)
        db = app.operations.journal.db
        saved_digest = db.execute('SELECT value FROM operation_events WHERE operation_id=? AND sequence=2', ('p1',)).fetchone()[0]
        expected_digest = hashlib.sha256(json.dumps(emitted).encode()).hexdigest()
        assert saved_digest == 'sha256:' + expected_digest
        saved_output = [json.loads(row[0]) for row in db.execute('SELECT value FROM operation_output WHERE operation_id=? ORDER BY cursor', ('p1',))]
        for invalid in ('sha256:short', 'sha256:' + 'z' * 64, saved_digest + '\n'):
            with app.operations.journal.lock, db:
                db.execute('UPDATE operation_events SET value=? WHERE operation_id=? AND sequence=2', (invalid, 'p1'))
            with pytest.raises(ValueError, match='operation event.*digest'):
                portability_data.capture(app, app._session(sid))
            assert db.execute('SELECT value FROM operation_events WHERE operation_id=? AND sequence=2', ('p1',)).fetchone()[0] == invalid
        with app.operations.journal.lock, db:
            db.execute('UPDATE operation_events SET value=? WHERE operation_id=? AND sequence=2', (saved_digest, 'p1'))
        if expired:
            row = app.operations.journal.status(sid, 'p1')
            assert app.operations.journal.expire_output(sid, 'p1', row['revision'], row['outputExpiresAt'])['status'] == 'expired'

        receipt, _ = await export(app, target, sid, workspace)
        payload = read_capsule(Path(receipt['package']))['body']['payload']
        saved = next(row for row in payload['observations']['operations'] if row['record']['id'] == 'p1')
        assert saved['events'] == [started, {'sequence': 2, 'eventId': 'p1:2', 'sha256': expected_digest}, completed]
        assert saved_output == [emitted['chunk']]
        assert saved['output'] == ([] if expired else saved_output)
        assert saved['record']['outputAvailability'] == ('expired' if expired else 'retained')
        assert saved['record']['outputComplete'] is not expired
        assert saved['record']['state'] == 'completed'
        assert not app.runtime.workers
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()
