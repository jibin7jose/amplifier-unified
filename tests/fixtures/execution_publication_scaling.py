"""Owned-DTU publication benchmark: one to three large histories, no provider.

Compare the same retained state before/after a change. Content readback and
unmodified history are required; timings are measurements, not machine gates.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid


async def measure(root, output, source, owner_count=3, reservation_bytes=0):
    sys.path[:0] = [str(source), str(source / 'tests')]
    from amplifier_web.cold_display import load
    from amplifier_web.service import AppService
    from amplifier_web.state_records import load_owner
    from amplifier_web.session_projection import stored_execution
    from test_service import Runtime

    app = AppService(root / 'state', workspace=root, runtime=Runtime())
    records = []
    try:
        owners = []
        for number in range(owner_count):
            row = app._new_session({'title': f'Concurrent retained owner {number}'})
            sid = row['id']
            row['messages'] = [dict(id='current', role='user', text='Current task',
                inputId='current', createdAt=1)]
            row['workers'] = [dict(id=f'child-{i}', sessionId=f'child-{i}', status='completed',
                task='Retained assignment. ' * 64,
                reportReceipts=[dict(id=f'report-{i}', text='Retained result. ' * 64)])
                for i in range(700)]
            receipts = [dict(id=f'call-{i}', kind='llm', revision=1,
                producerId='native', turnId=f'old-{i // 100}', sessionId=sid, rootSessionId=sid,
                phase='completed', provider='synthetic', model='none', startedAt=i, endedAt=i + 1,
                usage=dict(inputTokens=1000, outputTokens=100, totalTokens=1100, costUsd=.01))
                for i in range(20000)]
            if reservation_bytes:
                for index, receipt in enumerate(receipts):
                    receipt['assignmentReservation'] = dict(
                        id=f'reservation-{index}', tokens=index,
                        note=f'{index}:' + 'x' * reservation_bytes)
            row['execution'] = dict(currentTurnId='current', retiredUsageNodes=receipts,
                turns=[dict(id='current', inputId='current', anchorMessageId='current',
                    phase='running', startedAt=1)] + [dict(id=f'old-{i}', phase='completed')
                    for i in range(200)],
                nodes=[dict(receipt, liveObservation=True) for receipt in receipts[-5000:]] + [
                    dict(id=f'tool-{i}', kind='tool', phase='completed', turnId='current',
                        sessionId=sid, rootSessionId=sid, tool='read_file', toolCallId=f'call-{i}',
                        toolObservationId=str(uuid.uuid5(uuid.NAMESPACE_URL, f'scaling-{number}-{i}')),
                        liveObservation=True, revision=1, endedAt=i + 1)
                    for i in range(10000)])
            owners.append(row)
            app._state['sessions'].append(row)
        for phase in ('cold', 'scalar', 'current', 'current', 'nested', 'current', 'report', 'scalar'):
            for number, row in enumerate(owners):
                row['streaming'] = f'Token {len(records)}'
                if phase == 'nested':
                    row['execution']['nodes'][-1]['revision'] += 1
                    row['execution']['retiredUsageNodes'][-1]['usage']['inputTokens'] += 1
                if phase == 'current':
                    row['execution']['nodes'][-1]['revision'] += 1
                if phase == 'report':
                    row['workers'][-1]['reportReceipts'][-1]['text'] = f'Fresh report {number}'
                started, cpu = time.perf_counter(), time.process_time()
                app._publish_changes(sessions={row['id']}, detail_only=True)
                records.append(dict(owner=number, phase=phase, wallSeconds=time.perf_counter()-started,
                    cpuSeconds=time.process_time()-cpu))
        for row in owners:
            descriptor, _ = load_owner(app.db, row['id'])
            saved = load(app.db, descriptor['$viewPayload'])
            assert saved['workers'] == row['workers']
            execution = load(app.db, saved['_coldFields']['execution'])
            assert execution == stored_execution(row['execution'])
            assert len(execution['nodes']) == 15000
            assert len(execution['retiredUsageNodes']) == 20000
            assert len(execution['turns']) == 201
        assert not app.runtime.started and not app.runtime.sent and not app.queues
        cache = app._execution_preparation
        assert cache.bytes + cache._reserved({}) <= cache.MAX_BYTES
        assert cache.accounting_bytes <= cache.MAX_BYTES
        receipt = dict(records=records, owners=owner_count, placementsPerOwner=15000, receiptsPerOwner=20000,
            syntheticReservationPaddingBytes=reservation_bytes,
            retainedWorkersPerOwner=700, providerCalls=0, exactReadback=True,
            encodingBytes=cache.bytes, projectionBytes=cache.accounting_bytes,
            referenceBytes=cache._reserved({}), eachBudgetBytes=cache.MAX_BYTES,
            scope='Synthetic actual AppService publications; no live data/provider/client.')
        (output / 'results.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt), flush=True)
    finally:
        await app.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--owners', type=int, default=3, choices=range(1, 4))
    parser.add_argument('--reservation-bytes', type=int, default=0, choices=(0, 512))
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='publication-scaling-', dir=output) as temporary:
        root = Path(temporary)
        os.environ.update(HOME=str(root/'home'), AMPLIFIER_HOME=str(root/'amplifier'),
                          AMPLIFIER_WEB_HOME=str(root/'web'))
        asyncio.run(measure(root, output, args.source.resolve(), args.owners, args.reservation_bytes))
