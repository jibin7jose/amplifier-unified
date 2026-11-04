"""Diagnostic host-save scaling probe. Run only in an owned test environment.

No latency threshold: profiles and work-specific regressions guide later changes.
One current turn stays fixed while completed history grows along separate axes.
"""
import argparse
import asyncio
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import sys
import tempfile
import time
import uuid


async def measure(root, output):
    source = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(source), str(source / 'tests')]
    from amplifier_web.cold_display import load
    from amplifier_web.service import AppService
    from amplifier_web.state_records import load_owner
    from test_service import Runtime

    records = []
    cases = ((16, 0, 0, 0), (1024, 0, 0, 0), (4096, 0, 0, 0),
             (16, 504, 0, 0), (16, 0, 20000, 0), (16, 0, 20000, 5000))
    for messages, workers, receipts, observations in cases:
        name = f'{messages}-{workers}-{receipts}-{observations}'
        case = root / name
        case.mkdir()
        app = AppService(case / 'state', workspace=case, runtime=Runtime())
        try:
            owner = app._new_session({'title': 'History scaling fixture'})
            sid = owner['id']
            app._state['sessions'].append(owner)
            app._state['selectedSessionId'] = sid
            owner['messages'] = [dict(id=f'm-{i}', role='assistant',
                text=f'Historical reply {i}: ' + 'saved evidence. ' * 1024,
                createdAt=i, inputId=f'i-{i}') for i in range(messages)]
            owner['messages'].append(dict(id='current', role='user', text='Current task',
                                          inputId='current', createdAt=messages))
            owner['workers'] = [dict(id=f'child-{i}', sessionId=f'child-{i}', status='completed',
                task='Historical worker assignment. ' * 256,
                reportReceipts=[dict(id=f'report-{i}', text='Retained completed result. ' * 128)])
                for i in range(workers)]
            owner['execution'] = dict(nodes=[], currentTurnId='current',
                turns=[dict(id='current', inputId='current', anchorMessageId='current',
                            phase='running', startedAt=messages)],
                retiredUsageNodes=[dict(id=f'call-{i}', kind='llm', revision=1,
                    producerId='native', turnId=f'old-{i // 100}', sessionId=sid, rootSessionId=sid,
                    phase='completed', provider='synthetic', model='no-provider-used',
                    startedAt=i, endedAt=i + 1, usage=dict(inputTokens=1000, outputTokens=100,
                        totalTokens=1100, costUsd=.01)) for i in range(receipts)])
            if observations:
                tree = owner['execution']
                tree['turns'].extend(dict(id=f'old-{i}', phase='completed')
                                     for i in range((receipts + 99) // 100))
                tree['nodes'] = [dict(row, liveObservation=True)
                                for row in tree['retiredUsageNodes'][-observations:]]
                tree['nodes'].extend(dict(id=f'tool-{i}', kind='tool', phase='completed',
                    turnId='current', sessionId=sid, rootSessionId=sid, tool='read_file',
                    toolCallId=f'call-{i}', toolObservationId=str(uuid.uuid5(
                        uuid.NAMESPACE_URL, f'history-scaling-tool-{i}')),
                    liveObservation=True, revision=1, endedAt=i + 1)
                    for i in range(observations * 2))
            times = []
            for step in range(4):
                owner['streaming'] = f'Current answer {step}'
                if observations:
                    owner['execution']['nodes'][-1]['revision'] += 1
                started = time.perf_counter()
                app._publish_changes(sessions={sid}, detail_only=True)
                times.append(time.perf_counter() - started)
            profiler = cProfile.Profile()
            with profiler:
                owner['streaming'] = 'One more current token'
                app._publish_changes(sessions={sid}, detail_only=True)
            stream = io.StringIO()
            pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(35)
            (output / f'profile-{name}.txt').write_text(stream.getvalue())
            # Exact durable readback is outside the measured save. No authority
            # is inferred from a recent display window or in-memory row count.
            descriptor, _ = load_owner(app.db, sid)
            saved = load(app.db, descriptor['$viewPayload'])
            original = load(app.db, saved['_coldFields']['messages'])
            assert original == owner['messages']
            assert saved['workers'] == owner['workers']
            execution = (load(app.db, saved['_coldFields']['execution'])
                         if 'execution' in saved.get('_coldFields', {}) else saved['execution'])
            assert execution['retiredUsageNodes'] == owner['execution']['retiredUsageNodes']
            assert execution['turns'] == owner['execution']['turns']
            assert len(execution['nodes']) == observations * 3
            assert [row for row in execution['nodes'] if row['kind'] == 'tool'] == [
                row for row in owner['execution']['nodes'] if row['kind'] == 'tool']
            if observations:
                assert [row['usage'] for row in execution['nodes'] if row['kind'] == 'llm'] == [
                    row['usage'] for row in owner['execution']['retiredUsageNodes'][-observations:]]
            assert owner['execution']['currentTurnId'] == 'current'
            assert not app.runtime.started and not app.runtime.sent and not app.queues
            result = dict(historicalMessages=messages, historicalWorkers=workers,
                historicalAccountingReceipts=receipts,
                historicalLivePlacements=observations * 3,
                historicalTextBytes=sum(len(row['text'].encode()) for row in owner['messages']),
                firstSaveSeconds=times[0], warmSaveSeconds=times[1:], historyPreserved=True,
                providerCalls=0, subscribers=0)
            records.append(result)
            print(json.dumps(result), flush=True)
        finally:
            await app.close()
    (output / 'results.json').write_text(json.dumps(dict(records=records,
        scope='Synthetic actual host save/publication; no subscribers or provider. Not live-host acceptance.'), indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='history-scaling-', dir=output) as temporary:
        root = Path(temporary)
        os.environ.update(HOME=str(root / 'home'), AMPLIFIER_HOME=str(root / 'amplifier'),
                          AMPLIFIER_WEB_HOME=str(root / 'web'))
        asyncio.run(measure(root, output))
