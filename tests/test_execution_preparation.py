import copy
import json

import pytest

from amplifier_web import resource_files
from amplifier_web.execution_preparation import ExecutionPreparation


def tree():
    return {'nodes': [{'id': str(i), 'text': 'é\\\"\n\t' * 200,
                      'nested': {'values': [None, True, 1, 1.0, -0.0]}} for i in range(300)],
            'turns': [], 'retiredUsageNodes': [], 'metadata': {'x': '漢字'}}


def test_canonical_output_and_mutable_nested_aliases(monkeypatch):
    value = tree()
    cache = ExecutionPreparation()
    assert cache.prepare('a', value) == resource_files.prepare(value)
    calls = []
    original = json.dumps
    def observe(v, *args, **kwargs):
        if type(v) is list:
            calls.append(len(v))
        return original(v, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(json, 'dumps', observe)
        assert cache.prepare('a', value).text
        assert calls == []
        value['nodes'][140]['nested']['values'][0] = 'changed'
        result = cache.prepare('a', value)
        assert calls == [128]  # Only the changed fragment is encoded again.
    assert result == resource_files.prepare(value)
    value['nodes'].reverse()
    assert cache.prepare('a', value) == resource_files.prepare(value)


@pytest.mark.parametrize('before,after', [(True, 1), (1, 1.0), (0.0, -0.0), ('x', 'y')])
def test_typed_mutations_cannot_reuse_previous_bytes(before, after):
    value = {'nodes': [{'nested': [before]}]}
    cache = ExecutionPreparation()
    prior = cache.prepare('a', value)
    value['nodes'][0]['nested'][0] = after
    result = cache.prepare('a', value)
    assert result == resource_files.prepare(value) and result != prior


def test_sharing_custom_types_and_cycles_preserve_encoder_behavior():
    shared = {'value': 'retained'}
    value = {'nodes': [shared, shared]}
    cache = ExecutionPreparation()
    assert cache.prepare('a', value) == resource_files.prepare(value)
    shared['value'] = 'updated'
    assert cache.prepare('a', value) == resource_files.prepare(value)
    class Custom(dict):
        pass
    value['nodes'][0] = Custom(value='custom')
    assert cache.prepare('a', value) == resource_files.prepare(value)
    value['nodes'][0] = value
    with pytest.raises(ValueError, match='Circular'):
        cache.prepare('a', value)


def test_bounds_owner_replacement_and_eviction():
    cache = ExecutionPreparation()
    cache.MAX_BYTES = 30000
    for i in range(5):
        value = tree()
        assert cache.prepare(str(i), value) == resource_files.prepare(value)
        assert cache.bytes + cache.accounting_bytes <= 2 * cache.MAX_BYTES
    value = tree()
    cache.MAX_BYTES = 1000000
    assert cache.prepare('same', value) == resource_files.prepare(value)
    replacement = copy.deepcopy(value)
    replacement['nodes'][-1]['text'] = 'replacement'
    assert cache.prepare('same', replacement) == resource_files.prepare(replacement)
    cache.retain({'same'})
    assert all(k[0] == 'same' for k in cache.entries)
    cache.retain(())
    assert cache.bytes == 0 and not cache.entries


def test_custom_reducers_and_buffers_are_not_invoked():
    class CustomList(list):
        def __reduce_ex__(self, protocol):
            raise AssertionError('Custom reduction must not run')
        def __buffer__(self, flags):
            raise AssertionError('Custom buffer must not run')
    class CustomString(str):
        def __getstate__(self):
            raise AssertionError('Custom state must not run')
        def __reduce__(self):
            raise AssertionError('Custom reduction must not run')
    for custom in (CustomList(['value']), CustomString('value')):
        value = {'nodes': [custom]}
        cache = ExecutionPreparation()
        assert cache.prepare('a', value) == resource_files.prepare(value)
        assert not cache.entries


def test_large_cycles_and_bounded_witness_preserve_json_errors(monkeypatch):
    from amplifier_web.execution_preparation import _witness, _BoundedBytes, Unsupported
    cycle = ['x' * 100000]
    cycle.append(cycle)
    assert len(_witness(cycle)) < 110000
    with pytest.raises(ValueError, match='Circular'):
        ExecutionPreparation().prepare('a', {'nodes': cycle})
    monkeypatch.setattr(_BoundedBytes, 'LIMIT', 100)
    with pytest.raises(Unsupported):
        _witness('x' * 1000)
    value = {'nodes': [{'text': 'x' * 1000}]}
    cache = ExecutionPreparation()
    assert cache.prepare('a', value) == resource_files.prepare(value)
    assert not cache.entries


def test_retired_accounting_cache_observes_nested_edits_and_live_receipt_precedence():
    from amplifier_web.session_projection import stored_execution
    cache = ExecutionPreparation()
    rows = [{'id': str(i), 'kind': 'llm', 'sessionId': 'a', 'revision': 1,
             'producerId': 'p', 'rootSessionId': 'a', 'assignmentId': 'original',
             'usage': {'inputTokens': 1}, 'assignmentReservation': {'tokens': 3}}
            for i in range(200)]
    tree = {'retiredUsageNodes': rows, 'nodes': [], 'turns': []}
    def prepared():
        return stored_execution(tree, _retired=lambda snapshot: cache.retired('a', snapshot))
    assert prepared() == stored_execution(tree)
    previous = cache.retired('a', rows)
    entry = cache.accounting['a']
    assert prepared() == stored_execution(tree) and cache.accounting['a'] is entry
    previous[0]['usage']['inputTokens'] = -1  # A caller cannot poison cached rows.
    assert prepared() == stored_execution(tree)
    rows[10]['usage']['inputTokens'] = 99
    rows[20]['assignmentReservation']['tokens'] = 80
    assert prepared() == stored_execution(tree)
    assert previous[10]['usage']['inputTokens'] == 1
    tree['nodes'].append({**copy.deepcopy(rows[10]), 'revision': 2, 'liveObservation': True,
                          'assignmentId': 'replacement', 'usage': {'outputTokens': 50}})
    assert prepared() == stored_execution(tree)
    assert cache.retired('a', rows)[10]['usage'] == {'inputTokens': 99}
    rows.reverse()
    assert prepared() == stored_execution(tree)
    cache.retain(())
    assert not cache.accounting and cache.accounting_bytes == 0


def test_retired_custom_values_and_nonstandard_keys_keep_original_projection():
    from amplifier_web.session_projection import stored_execution
    class Renamed(str):
        def __deepcopy__(self, memo):
            return 'changed-key'
        def __reduce__(self):
            raise AssertionError('Custom reducers must not run')
    cache = ExecutionPreparation()
    rows = [{'id': str(i), 'kind': 'llm', 'sessionId': 'a', 'revision': 1} for i in range(130)]
    rows[0]['id'] = Renamed('original-key')
    rows[0].update(producerId='producer', rootSessionId='a', turnId='turn')
    assert cache.retired('a', rows) is None
    tree = {'retiredUsageNodes': rows, 'turns': [{'id': 'turn'}],
            'nodes': [{**rows[0], 'revision': 2, 'liveObservation': True}]}
    # Placement lookup uses the copied receipt identity. The custom hook changes
    # that identity, so the original observation must not acquire the new receipt.
    for project in (lambda: stored_execution(tree), lambda: stored_execution(
            tree, _retired=lambda snapshot: cache.retired('a', snapshot))):
        saved = project()
        assert saved['retiredUsageNodes'][0]['id'] == 'changed-key'
        assert saved['nodes'] == []
    rows[0]['id'] = '0'
    tree['nodes'] = [{**rows[0], 'revision': 2, 'liveObservation': True}]
    assert stored_execution(tree)['nodes'][0]['id'] == '0'
    rows[1]['revision'] = float('nan')
    assert cache.retired('a', rows) is None
    rows[1]['revision'] = 1
    rows[0]['lifecycle'] = {'invalid': set(range(100))}
    cache.retired('a', rows)
    assert not cache.accounting


def test_retired_memory_budget_eviction_and_replacement():
    cache = ExecutionPreparation()
    cache.MAX_BYTES = 20000
    rows = [{'id': str(i), 'kind': 'llm', 'sessionId': 'a', 'revision': 1,
             'usage': {'inputTokens': i}} for i in range(130)]
    cache.retired('a', rows)
    assert not cache.accounting
    cache.MAX_BYTES = 300000
    cache.retired('a', rows)
    assert cache.accounting
    for i in range(10):
        cache.retired(str(i), copy.deepcopy(rows))
        assert cache.accounting_bytes <= cache.MAX_BYTES
    cache.retain(())
    assert cache.accounting_bytes == 0


def test_retired_snapshot_and_live_hook_order_match_uncached_projection():
    from amplifier_web.session_projection import accounting_projection
    def run(cached):
        rows = [{'id': str(i), 'kind': 'llm', 'revision': 1,
                 'usage': {'inputTokens': i}} for i in range(130)]
        class Live(dict):
            def get(self, key, default=None):
                if key == 'liveObservation':
                    rows[0]['usage']['inputTokens'] = 999
                    rows.pop()
                    rows.append({'id': 'new', 'kind': 'llm', 'revision': 1})
                    return False
                return super().get(key, default)
        tree = {'nodes': [Live()], 'retiredUsageNodes': rows}
        cache = ExecutionPreparation()
        return accounting_projection(tree, _retired=(
            lambda snapshot: cache.retired('a', snapshot)) if cached else None)
    assert run(True) == run(False)
    assert run(True)[0]['usage']['inputTokens'] == 999
    assert run(True)[-1]['id'] == '129'


def test_retired_tuple_does_not_retain_a_borrowed_container():
    from amplifier_web.session_projection import stored_execution
    cache = ExecutionPreparation()
    rows = [{'id': str(i), 'kind': 'llm', 'revision': 1,
             'lifecycle': {'tuple': tuple(['stable', i])}} for i in range(130)]
    assert cache.retired('a', rows) is None
    assert not cache.accounting and cache.accounting_bytes == 0
    tree = {'retiredUsageNodes': rows}
    actual = stored_execution(tree, _retired=lambda snapshot: cache.retired('a', snapshot))
    assert actual == stored_execution(tree)
    assert type(actual['retiredUsageNodes'][0]['lifecycle']['tuple']) is tuple


@pytest.mark.parametrize('value', [{1: 'number', '1': 'text'}, float('nan'), float('inf')])
def test_retired_json_ineligible_values_keep_original_projection(value):
    from amplifier_web.session_projection import stored_execution
    cache = ExecutionPreparation()
    rows = [{'id': str(i), 'kind': 'llm', 'revision': 1,
             'lifecycle': {'value': value}} for i in range(130)]
    assert cache.retired('a', rows) is None
    assert not cache.accounting
    tree = {'retiredUsageNodes': rows}
    actual = stored_execution(tree, _retired=lambda snapshot: cache.retired('a', snapshot))
    assert actual == stored_execution(tree)


def test_one_retired_change_only_reprojects_and_sizes_one_fragment(monkeypatch):
    from amplifier_web import session_projection
    for count in (256, 2048):
        cache = ExecutionPreparation()
        rows = [{'id': str(i), 'kind': 'llm', 'revision': 1, 'sessionId': 'root',
                 'usage': {'inputTokens': i}, 'assignmentReservation': {'tokens': [i]}}
                for i in range(count)]
        previous = cache.retired('root', rows)
        calls, sizes = [], []
        project, charge = session_projection.accounting_projection, cache._accounting_charge
        with monkeypatch.context() as patch:
            patch.setattr(session_projection, 'accounting_projection', lambda tree, **kw: (
                calls.append(len(tree['retiredUsageNodes'])), project(tree, **kw))[1])
            patch.setattr(cache, '_accounting_charge', lambda value: (sizes.append(len(value)), charge(value))[1])
            assert cache.retired('root', rows) == previous
            rows[-1]['assignmentReservation']['tokens'].append(9)
            current = cache.retired('root', rows)
        assert calls == sizes == [128]
        assert current == project({'retiredUsageNodes': rows})
        assert previous[-1]['assignmentReservation']['tokens'] == [count - 1]
        assert cache.accounting_bytes <= cache.MAX_BYTES
        rows.reverse()
        assert cache.retired('root', rows) == project({'retiredUsageNodes': rows})


def test_retired_duplicate_identities_crossing_fragments_keep_ordered_reduction():
    from amplifier_web.session_projection import stored_execution
    cache = ExecutionPreparation()
    rows = [{'id': str(i), 'kind': 'llm', 'revision': 1, 'sessionId': 'root',
             'assignmentId': 'original', 'usage': {'inputTokens': i}} for i in range(256)]
    tree = {'retiredUsageNodes': rows, 'nodes': []}
    cache.retired('root', rows)
    rows[-1].update(id='0', revision=2, assignmentId='replacement')
    assert cache.retired('root', rows) is None
    actual = stored_execution(tree, _retired=lambda snapshot: cache.retired('root', snapshot))
    assert actual == stored_execution(tree)
    assert actual['retiredUsageNodes'][0]['assignmentId'] == 'original'
    assert actual['retiredUsageNodes'][0]['usage']['inputTokens'] == 255


def test_fragment_witnesses_do_not_expand_total_witness_budget(monkeypatch):
    from amplifier_web.execution_preparation import _BoundedBytes
    cache = ExecutionPreparation()
    rows = [{'id': str(i), 'kind': 'llm', 'revision': 1,
             'assignmentReservation': {'note': f'{i}:' + 'x' * 100}} for i in range(256)]
    monkeypatch.setattr(_BoundedBytes, 'LIMIT', 20000)
    assert cache.retired('root', rows) is None
    assert not cache.accounting and not cache.accounting_bytes


def test_cross_fragment_aliases_invalidate_every_dependent_projection():
    from amplifier_web.session_projection import accounting_projection
    cache = ExecutionPreparation()
    rows = [{'id': str(i), 'kind': 'llm', 'revision': 1,
             'assignmentReservation': {'tokens': [i]}} for i in range(256)]
    shared = {'tokens': [7]}
    rows[127]['assignmentReservation'] = rows[128]['assignmentReservation'] = shared
    before = cache.retired('root', rows)
    shared['tokens'].append(9)
    after = cache.retired('root', rows)
    assert after == accounting_projection({'retiredUsageNodes': rows})
    assert before[127]['assignmentReservation'] == before[128]['assignmentReservation'] == {'tokens': [7]}
    assert after[127]['assignmentReservation']['tokens'] == [7, 9]
    assert after[128]['assignmentReservation']['tokens'] == [7, 9]


def test_six_interleaved_histories_reuse_fragments_within_existing_total_budget(monkeypatch):
    from amplifier_web import session_projection
    cache = ExecutionPreparation()
    cache.MAX_BYTES = 650000  # Scaled version of the existing 128 MiB total budget.
    histories = {
        str(owner): {'retiredUsageNodes': [
            {'id': str(i), 'kind': 'llm', 'revision': 1, 'sessionId': str(owner),
             'producerId': 'p', 'rootSessionId': str(owner), 'assignmentId': 'work',
             'usage': {'inputTokens': i}, 'assignmentReservation': {'tokens': [i]}}
            for i in range(256)], 'nodes': [], 'turns': []}
        for owner in range(6)}
    def save(owner, tree):
        value = session_projection.stored_execution(tree,
            _retired=lambda rows: cache.retired(owner, rows))
        return cache.prepare(owner, value)
    expected = {owner: resource_files.prepare(session_projection.stored_execution(tree))
                for owner, tree in histories.items()}
    for owner, tree in histories.items():
        assert save(owner, tree) == expected[owner]
    calls, encoded = [], []
    project, dumps = session_projection.accounting_projection, json.dumps
    with monkeypatch.context() as patch:
        patch.setattr(session_projection, 'accounting_projection', lambda tree, **kw: (
            calls.append(len(tree.get('retiredUsageNodes', []))), project(tree, **kw))[1])
        patch.setattr(json, 'dumps', lambda value, *a, **kw: (
            encoded.append(len(value)) if type(value) is list and value else None,
            dumps(value, *a, **kw))[1])
        for _ in range(2):
            for owner, tree in histories.items():
                assert save(owner, tree) == expected[owner]
                assert cache.bytes + cache.accounting_bytes <= 2 * cache.MAX_BYTES
    # The outer canonical projection still runs, but no retired fragment or
    # unchanged list is rebuilt merely because another active owner saved.
    assert calls == [256] * 12
    assert encoded == []


def test_interleaved_histories_larger_than_cache_keep_reusing_recent_fragments(monkeypatch):
    cache = ExecutionPreparation()
    cache.MAX_BYTES = 150000
    histories = {
        str(owner): {'nodes': [
            {'id': str(i), 'text': f'{owner}:{i}:' + 'x' * 256,
             'nested': {'values': [i]}}
            for i in range(256)], 'turns': [], 'retiredUsageNodes': []}
        for owner in range(6)}
    expected = {owner: resource_files.prepare(value) for owner, value in histories.items()}
    assert sum(len(value.text) for value in expected.values()) > 2 * cache.MAX_BYTES
    for owner, value in histories.items():
        assert cache.prepare(owner, value) == expected[owner]
    encoded = []
    dumps = json.dumps
    with monkeypatch.context() as patch:
        def observe(value, *args, **kwargs):
            if type(value) is list and value:
                encoded.append(len(value))
            return dumps(value, *args, **kwargs)
        patch.setattr(json, 'dumps', observe)
        for _ in range(2):
            for owner, value in histories.items():
                assert cache.prepare(owner, value) == expected[owner]
                assert cache.bytes + cache.accounting_bytes <= 2 * cache.MAX_BYTES
    # A cyclic scan larger than capacity must not cause every unchanged row to
    # be encoded again. This checks saved bytes and work, not a cache's ordering.
    assert sum(encoded) < 2 * 6 * 256
    for owner, value in histories.items():
        shared = {'values': ['changed']}
        value['nodes'][127]['nested'] = value['nodes'][128]['nested'] = shared
        assert cache.prepare(owner, value) == resource_files.prepare(value)
        shared['values'].append('changed again')
        assert cache.prepare(owner, value) == resource_files.prepare(value)
    cache.retain({'5'})
    assert all(key[0] == '5' for key in cache.entries)
    replacement = tree()
    assert cache.prepare('5', replacement) == resource_files.prepare(replacement)
    cache.retain(())
    assert cache.bytes == 0 and not cache.entries
