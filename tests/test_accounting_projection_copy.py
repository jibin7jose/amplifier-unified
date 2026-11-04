import copy

from amplifier_web.session_projection import accounting_projection, stored_execution


class MutableString(str):
    pass


class CustomUsage(dict):
    def __deepcopy__(self, memo):
        return {'inputTokens': self['inputTokens'] + 1, 'outputTokens': 2}


class CopiedName(str):
    copies = 0

    def __deepcopy__(self, memo):
        result = type(self)(self)
        result.copies = self.copies + 1
        return result


class UsageSubclass(dict):
    pass


class RenamedKey(str):
    def __deepcopy__(self, memo):
        return 'inputTokens'


class CopiedScalar(str):
    def __deepcopy__(self, memo):
        return str(self)


def test_accounting_projection_detaches_retained_nested_values_and_scalar_subclasses():
    model = MutableString('fixture')
    model.notes = ['original']
    receipt = {'id': 'call', 'kind': 'llm', 'sessionId': 'root', 'producerId': 'producer',
               'model': model, 'lifecycle': {'nested': ['original']}, 'revision': 1,
               'usage': {'inputTokens': 3, 'costUsd': .25}}
    result = accounting_projection({'retiredUsageNodes': [receipt]})[0]
    receipt['lifecycle']['nested'].append('later')
    model.notes.append('later')
    receipt['usage']['inputTokens'] = 8
    assert result['lifecycle'] == {'nested': ['original']}
    assert result['model'].notes == ['original']
    assert result['usage'] == {'inputTokens': 3, 'costUsd': .25}
    result['usage']['costUsd'] = 1
    assert receipt['usage']['costUsd'] == .25
    name = MutableString('inputTokens')
    name.notes = ['original']
    receipt['usage'] = {name: 8}
    copied_name = next(iter(accounting_projection({'retiredUsageNodes': [receipt]})[0]['usage']))
    name.notes.append('later')
    assert copied_name.notes == ['original']


def test_usage_filter_preserves_exact_types_custom_mapping_and_field_order():
    values = {'inputTokens': True, 'outputTokens': MutableString('2'),
              'totalTokens': 3, 'costUsd': .25, 'costType': 'reported',
              'cacheReadTokens': {'nested': [1]}, 'unknown': {'private': ['body']}}
    receipt = {'id': 'call', 'usage': values, 'kind': 'llm', 'sessionId': 'root',
               'revision': 1, 'requestInfo': {'private': 'excluded'}}
    result = accounting_projection({'retiredUsageNodes': [receipt]})[0]
    assert result['usage'] == {'totalTokens': 3, 'costUsd': .25, 'costType': 'reported'}
    assert list(result) == ['id', 'usage', 'kind', 'sessionId', 'revision']
    receipt['usage'] = CustomUsage(inputTokens=4)
    assert accounting_projection({'retiredUsageNodes': [receipt]})[0]['usage'] == {
        'inputTokens': 5, 'outputTokens': 2}
    receipt['usage'] = ['invalid']
    assert 'usage' not in accounting_projection({'retiredUsageNodes': [receipt]})[0]
    # Generic mapping copies already detach custom keys; do not copy them twice.
    receipt['usage'] = UsageSubclass({CopiedName('inputTokens'): 4})
    key = next(iter(accounting_projection({'retiredUsageNodes': [receipt]})[0]['usage']))
    assert key.copies == 1
    # Legacy filtering happens after custom deepcopy hooks transform values.
    receipt['usage'] = {RenamedKey('other'): CopiedScalar('4')}
    assert accounting_projection({'retiredUsageNodes': [receipt]})[0]['usage'] == {'inputTokens': '4'}


def test_fresh_projection_observes_mutated_receipts_without_borrowing_child_identity():
    root = {'id': 'same', 'kind': 'llm', 'sessionId': 'root', 'rootSessionId': 'root',
            'producerId': 'producer', 'revision': 1, 'phase': 'running',
            'usage': {'inputTokens': 2}}
    child = {**copy.deepcopy(root), 'sessionId': 'child'}
    tree = {'retiredUsageNodes': [root, child]}
    initial = accounting_projection(tree)
    root.update(revision=2, phase='completed', endedAt=10)
    root['usage']['outputTokens'] = 4
    latest = accounting_projection(tree)
    assert initial[0]['revision'] == 1 and initial[0]['usage'] == {'inputTokens': 2}
    assert latest[0]['revision'] == 2 and latest[0]['usage']['outputTokens'] == 4
    assert latest[1]['sessionId'] == 'child' and latest[1]['revision'] == 1


def test_assignment_reservations_remain_detached_and_prior_receipt_wins():
    prior = {'id': 'call', 'kind': 'llm', 'sessionId': 'root', 'revision': 1,
             'assignmentId': 'original', 'assignmentReservation': {'tokens': 100, 'cost': .2}}
    latest = {**prior, 'revision': 2, 'liveObservation': True,
              'assignmentId': 'replacement', 'assignmentReservation': {'tokens': 999}}
    projected = accounting_projection({'retiredUsageNodes': [prior], 'nodes': [latest]})[0]
    assert projected['assignmentId'] == 'original'
    assert projected['assignmentReservation'] == {'tokens': 100, 'cost': .2}
    prior['assignmentReservation']['tokens'] = 200
    assert projected['assignmentReservation']['tokens'] == 100
    projected['assignmentReservation']['cost'] = .5
    assert prior['assignmentReservation']['cost'] == .2
    prior['assignmentReservation'] = {RenamedKey('other'): CopiedScalar('4')}
    projected = accounting_projection({'retiredUsageNodes': [prior]})[0]
    assert projected['assignmentReservation'] == {'inputTokens': '4'}
    nested = {'tokens': [100]}
    prior['assignmentReservation'] = nested
    projected = accounting_projection({'retiredUsageNodes': [prior]})[0]
    nested['tokens'].append(200)
    assert projected['assignmentReservation'] == {'tokens': [100]}


def test_saved_observations_do_not_recursively_recopy_plain_historical_receipts(monkeypatch):
    """Growing old live placements must not add generic recursive copy work."""
    counts = []
    original = copy.deepcopy
    for size in (1, 200):
        rows = [{'id': f'call-{i}', 'kind': 'llm', 'sessionId': 'root',
                 'rootSessionId': 'root', 'turnId': 'turn', 'producerId': 'producer',
                 'revision': 2, 'phase': 'completed', 'liveObservation': True,
                 'model': 'synthetic', 'usage': {'inputTokens': i, 'costUsd': .1},
                 'assignmentReservation': {'tokens': 100, 'state': 'settled'}}
                for i in range(size)]
        tree = {'nodes': rows, 'turns': [{'id': 'turn'}]}
        recursive = []
        def observe(value, *args, **kwargs):
            recursive.append(type(value))
            return original(value, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(copy, 'deepcopy', observe)
            saved = stored_execution(tree)
        counts.append(len(recursive))
        assert len(saved['nodes']) == len(saved['retiredUsageNodes']) == size
        for live, node, receipt in zip(rows, saved['nodes'], saved['retiredUsageNodes']):
            assert node['usage'] == receipt['usage'] == live['usage']
            assert node['assignmentReservation'] == receipt['assignmentReservation']
            live['usage']['inputTokens'] = -1
            node['assignmentReservation']['tokens'] = -1
            receipt['usage']['costUsd'] = -1
            assert node['usage']['costUsd'] == .1
            assert receipt['assignmentReservation']['tokens'] == 100
            assert node['usage']['inputTokens'] >= 0
    assert counts == [0, 0]


def test_observation_receipt_copy_keeps_aliases_and_custom_deepcopy_behavior():
    # These are already detached accounting receipts at the placement boundary.
    from amplifier_web.session_projection import _observed_work
    row = {'id': 'call', 'kind': 'llm', 'sessionId': 'root', 'rootSessionId': 'root',
           'turnId': 'turn', 'liveObservation': True}
    for child in ({'tokens': 100}, {'nested': [100]}, CustomUsage(inputTokens=4)):
        receipt = {'id': 'call', 'producerId': 'producer',
                   'lifecycle': child, 'assignmentReservation': child}
        expected = copy.deepcopy(receipt)
        node = _observed_work(row, {'turn'}, {('root', 'call'): receipt})
        assert node['lifecycle'] == expected['lifecycle']
        assert node['lifecycle'] is node['assignmentReservation']
        assert node['lifecycle'] is not child
    receipt = {'producerId': 'producer', CopiedName('model'): CopiedScalar('model')}
    node = _observed_work(row, {'turn'}, {('root', 'call'): receipt})
    key = next(key for key in node if isinstance(key, CopiedName))
    assert key.copies == 1 and type(node[key]) is str
