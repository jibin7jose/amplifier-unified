"""Owned-DTU publication benchmark: one to six large histories, no provider.

Compare the same retained state before/after a change. Content readback and
unmodified history are required; timings are measurements, not machine gates.
"""
import argparse
import asyncio
import cProfile
import hashlib
import json
import os
from pathlib import Path
import platform
import pstats
import resource
import sys
import tempfile
import time
import uuid


async def measure(root, output, source, owner_count=3, reservation_bytes=0,
                  size='large', cycles=1, profile=False, operation='publication'):
    sys.path[:0] = [str(source), str(source / 'tests')]
    from amplifier_web.cold_display import load
    from amplifier_web.service import AppService
    from amplifier_web.state_records import load_owner
    from amplifier_web.session_projection import stored_execution
    from test_service import Runtime
    from test_automatic_history import native_session
    from amplifier_web.session_files import project_slug

    workers, receipts_count, live_count, tools, turns_count = (
        (700, 20000, 5000, 10000, 200) if size == 'large' else (2, 4, 4, 6, 1))
    def io_bytes():
        path = Path('/proc/self/io')
        return {key: int(value) for key, value in
                (line.split(':') for line in path.read_text().splitlines())} if path.exists() else {}
    app = AppService(root / 'state', workspace=root, runtime=Runtime())
    records = []
    profiler = cProfile.Profile() if profile else None
    try:
        owners, transcripts = [], {}
        for number in range(owner_count):
            row = app._new_session({'title': f'Concurrent retained owner {number}'})
            row['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, f'publication-owner-{number}'))
            sid = row['id']
            row['messages'] = [dict(id='current', role='user', text='Current task',
                inputId='current', createdAt=1)]
            row['workers'] = [dict(id=f'child-{i}', sessionId=f'child-{i}', status='completed',
                task='Retained assignment. ' * 64,
                reportReceipts=[dict(id=f'report-{i}', text='Retained result. ' * 64)])
                for i in range(workers)]
            receipts = [dict(id=f'call-{i}', kind='llm', revision=1,
                producerId='native', turnId=f'old-{i // 100}', sessionId=sid, rootSessionId=sid,
                phase='completed', provider='synthetic', model='none', startedAt=i, endedAt=i + 1,
                usage=dict(inputTokens=1000, outputTokens=100, totalTokens=1100, costUsd=.01))
                for i in range(receipts_count)]
            if reservation_bytes:
                for index, receipt in enumerate(receipts):
                    receipt['assignmentReservation'] = dict(
                        id=f'reservation-{index}', tokens=index,
                        note=f'{index}:' + 'x' * reservation_bytes)
            row['execution'] = dict(currentTurnId='current', retiredUsageNodes=receipts,
                turns=[dict(id='current', inputId='current', anchorMessageId='current',
                    phase='running', startedAt=1)] + [dict(id=f'old-{i}', phase='completed')
                    for i in range(turns_count)],
                nodes=[dict(receipt, liveObservation=True) for receipt in receipts[-live_count:]] + [
                    dict(id=f'tool-{i}', kind='tool', phase='completed', turnId='current',
                        sessionId=sid, rootSessionId=sid, tool='read_file', toolCallId=f'call-{i}',
                        toolObservationId=str(uuid.uuid5(uuid.NAMESPACE_URL, f'scaling-{number}-{i}')),
                        liveObservation=True, revision=1, endedAt=i + 1)
                    for i in range(tools)])
            owners.append(row)
            app._state['sessions'].append(row)
            if operation == 'history-load':
                directory = native_session(root, sid, [{'role': 'user', 'content': 'Current task'}])
                transcripts[sid] = directory / 'transcript.jsonl'
                row.update(nativeProject=project_slug(root), nativeIdentity=sid, runtimeSessionId=sid,
                           historyManaged=False, historyLoaded=False)
        publications = 0
        original_publish = app._publish_changes
        def publish(*args, **kwargs):
            nonlocal publications
            publications += 1
            return original_publish(*args, **kwargs)
        app._publish_changes = publish
        phases = ('cold',) + (('refresh',) if operation == 'history-load' else
                             ('scalar', 'current', 'current', 'nested', 'current', 'report', 'scalar')) * cycles
        before_io = io_bytes()
        for phase in phases:
            for number, row in enumerate(owners):
                row['streaming'] = f'Token {len(records)}'
                if phase == 'nested':
                    row['execution']['nodes'][-1]['revision'] += 1
                    row['execution']['retiredUsageNodes'][-1]['usage']['inputTokens'] += 1
                if phase == 'current':
                    row['execution']['nodes'][-1]['revision'] += 1
                if phase == 'report':
                    row['workers'][-1]['reportReceipts'][-1]['text'] = f'Fresh report {len(records)}'
                if phase == 'refresh':
                    with transcripts[row['id']].open('a') as stream:
                        stream.write(json.dumps({'role': 'assistant', 'content': f'Fresh response {len(records)}'}) + '\n')
                if profiler:
                    profiler.enable()
                started, cpu = time.perf_counter(), time.process_time()
                if operation == 'history-load':
                    await app.history.ensure_loaded(row['id'])
                    assert row['historyLoaded'] and not row['historyLoading'] and not row['historyError']
                else:
                    app._publish_changes(sessions={row['id']}, detail_only=True)
                records.append(dict(owner=number, phase=phase, wallSeconds=time.perf_counter()-started,
                    cpuSeconds=time.process_time()-cpu))
                if profiler:
                    profiler.disable()
        after_io = io_bytes()
        execution_hashes = []
        for row in owners:
            descriptor, _ = load_owner(app.db, row['id'])
            saved = load(app.db, descriptor['$viewPayload'])
            assert saved['workers'] == row['workers']
            if operation == 'history-load':
                assert saved['messages'] == row['messages']
                assert len(saved['messages']) == cycles + 1
                assert saved['historyLoaded'] and not saved['historyLoading'] and not saved['historyError']
                assert [message['text'] for message in saved['messages']] == [
                    json.loads(line)['content'] for line in transcripts[row['id']].read_text().splitlines()]
            execution = (load(app.db, saved['_coldFields']['execution'])
                         if 'execution' in saved.get('_coldFields', {}) else saved['execution'])
            assert execution == stored_execution(row['execution'])
            assert len(execution['nodes']) == live_count + tools
            assert len(execution['retiredUsageNodes']) == receipts_count
            assert len(execution['turns']) == turns_count + 1
            execution_hashes.append(hashlib.sha256(json.dumps(
                execution, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
        assert not app.runtime.started and not app.runtime.sent and not app.queues
        cache = app._execution_preparation
        assert cache.bytes + cache.accounting_bytes + cache._reserved({}) <= 2 * cache.MAX_BYTES
        assert cache.accounting_bytes <= cache.MAX_BYTES
        assert cache._reserved({}) <= cache.MAX_BYTES
        receipt = dict(records=records, owners=owner_count, placementsPerOwner=live_count + tools,
            receiptsPerOwner=receipts_count, turnsPerOwner=turns_count + 1,
            size=size, cycles=cycles, profiled=profile, operation=operation,
            warmup='First operation per owner is separately labelled cold; subsequent events reuse that service.',
            totalWallSeconds=sum(row['wallSeconds'] for row in records),
            steadyWallSeconds=sum(row['wallSeconds'] for row in records if row['phase'] != 'cold'),
            executionSha256=execution_hashes, publicationCount=publications, operationCount=len(records),
            processIoDelta={key: after_io[key] - before_io[key] for key in before_io},
            peakRssKiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            python=sys.version, platform=platform.platform(),
            sourceFiles={name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in (
                'amplifier_web/session_projection.py', 'amplifier_web/execution_preparation.py',
                'amplifier_web/service.py', 'amplifier_web/automatic_history.py', 'uv.lock')},
            loadedServiceModule=sys.modules[AppService.__module__].__file__,
            syntheticReservationPaddingBytes=reservation_bytes,
            retainedWorkersPerOwner=workers, providerCalls=0, exactReadback=True,
            encodingBytes=cache.bytes, projectionBytes=cache.accounting_bytes,
            referenceBytes=cache._reserved({}), totalBudgetBytes=2 * cache.MAX_BYTES,
            accountingBudgetBytes=cache.MAX_BYTES, referenceBudgetBytes=cache.MAX_BYTES,
            scope='Synthetic actual AppService publications; no live data/provider/client.')
        (output / 'results.json').write_text(json.dumps(receipt, indent=2) + '\n')
        if profiler:
            profiler.dump_stats(str(output / 'publication.prof'))
            with (output / 'profile.txt').open('w') as stream:
                pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(60)
        print(json.dumps(receipt), flush=True)
    finally:
        await app.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--owners', type=int, default=3, choices=range(1, 7))
    parser.add_argument('--reservation-bytes', type=int, default=0, choices=(0, 512))
    parser.add_argument('--size', choices=('small', 'large'), default='large')
    parser.add_argument('--cycles', type=int, default=1)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--operation', choices=('publication', 'history-load'), default='publication')
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='publication-scaling-', dir=output) as temporary:
        root = Path(temporary)
        os.environ.update(HOME=str(root/'home'), AMPLIFIER_HOME=str(root/'amplifier'),
                          AMPLIFIER_WEB_HOME=str(root/'web'))
        asyncio.run(measure(root, output, args.source.resolve(), args.owners, args.reservation_bytes,
                            args.size, args.cycles, args.profile, args.operation))
