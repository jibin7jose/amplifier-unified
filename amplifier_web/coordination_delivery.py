"""Fresh coordination reference data in the fitted provider request only.

Hook ephemerality can still accumulate reminders within a native turn. Use the
existing provider delivery boundary, as surfaces and memory do, so observation
replacement does not depend on the orchestrator's reminder persistence mode.
"""
import asyncio
import copy
import hashlib
import json
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from .work_projection import input_identity_bytes
from .request_refit import RequestFitChanged, changed_components

WORK_BYTES = 12 * 1024
STUB_FIELDS = frozenset({
    'id', 'revision', 'promiseRevision', 'title', 'originSessionId', 'candidate',
    'latestOutcome', 'humanDecisionCurrent', 'attentionReasons', 'nextAction',
    'blockedReason', 'readRef',
})
DISCOVERY_FIELDS = frozenset({
    'workspaceId', 'version', 'snapshotVersion', 'observationVersion', 'items', 'total',
    'truncated', 'nextAfter', 'observedAt', 'coverage', 'detailRequired', 'authority',
})
REQUIRED_FIELDS = frozenset({
    'status', 'reason', 'version', 'rootSessionId', 'generationId', 'inputIds', 'inputCount',
    'associationRequestIds', 'workCount', 'requirementCount', 'requirementsRef',
    'effectAuthorized', 'coverage',
})
WORK_READS = frozenset({'workspace.work.invocation', 'workspace.work.promise', 'workspace.work.source'})
MAX_READS = 512
READ_BYTES = 4 * 1024 * 1024
PLAINTEXT_BYTES = 64 * 1024
WORK_ATTEMPT = ContextVar('unified_work_delivery_attempt', default=None)
WORK_NESTED = ContextVar('unified_work_nested_origin', default=None)
WORK_EXECUTION = ContextVar('unified_work_tool_execution', default=None)


def nested_checkpoint(coordinator, data):
    """Only our active, verified dispatch child belongs to its parent's save.

    Event fields alone are not proof. The native lease and private task-local
    observation were established before the tool ran. This is a persistence
    boundary, never permission to run a tool or replay an uncertain result.
    """
    nested = WORK_NESTED.get()
    if not nested or not nested['active'] or not nested['verified']:
        return False
    lease = nested['lease']
    source = data.get('dispatch_source')
    if not isinstance(source, dict):
        return False
    return (not lease.closed and lease.scope.active and lease.scope.run.active
            and lease.scope.run.coordinator is coordinator
            and source.get('session_id') == coordinator.session_id
            and source.get('run_id') == lease.scope.run.identity
            and source.get('parent_tool_call_id') == nested['parent']['tool_call_id']
            and data.get('parent_tool_call_id') == nested['parent']['tool_call_id']
            and source.get('tool_call_id') == data.get('tool_call_id') == nested['callId']
            and source.get('tool_name') == data.get('tool_name') == nested['name']
            and ('tool_input' not in data or _same_json(data['tool_input'], nested['arguments'])))

@dataclass(frozen=True)
class InvocationOrigin:
    """Private host value, never reconstructed from tool arguments."""
    scope_json: str
    admitted_call_id: str
    attempt_id: str
    fitted_sha256: str

    def public(self):
        return {**json.loads(self.scope_json), 'schemaVersion': 1,
                'admittedCallId': self.admitted_call_id, 'attemptId': self.attempt_id,
                'fittedSha256': self.fitted_sha256}

def _same_json(left, right):
    # Python equality alone treats False == 0 and True == 1. Transport proof
    # requires the same JSON types as well as values.
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(right, sort_keys=True, ensure_ascii=False)


def _work_pair_retained(request, row):
    from .output_image_delivery import pair_retained, successful_output
    if not pair_retained(request, row):
        return False
    for message in request.messages:
        if message.role == 'tool' and message.tool_call_id == row['tool_call_id']:
            if not _same_json(successful_output(message.content), row['receipt']):
                return False
        if message.role == 'assistant':
            for call in getattr(message, 'tool_calls', None) or []:
                call = call.model_dump() if hasattr(call, 'model_dump') else call
                if isinstance(call, dict) and call.get('id') == row['tool_call_id']:
                    if not _same_json(call.get('arguments'), row['input']):
                        return False
            for block in message.content if isinstance(message.content, list) else []:
                if block.type == 'tool_call' and block.id == row['tool_call_id']:
                    if not _same_json(block.input, row['input']):
                        return False
    return True


def _unavailable_work():
    return {'available': False,
            'discovery': {'status': 'unavailable', 'reason': 'Current Work projection is unavailable.'},
            'required': {'status': 'unavailable', 'version': None,
                         'reason': 'Read current Work details; absence or truncation is not permission.',
                         'workCount': None, 'requirementCount': None, 'requirementsRef': None,
                         'coverage': {'complete': False}, 'effectAuthorized': False}}


def _text(value, maximum=200, *, empty=False):
    return isinstance(value, str) and (empty or bool(value)) and len(value) <= maximum


def _integer(value, minimum=0, maximum=None):
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


def _texts(value, maximum=None):
    return (isinstance(value, list) and (maximum is None or len(value) <= maximum)
            and all(_text(v) for v in value))


def _digest(value):
    return _text(value, 64) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _valid_required(required):
    if (not isinstance(required, dict) or set(required) - REQUIRED_FIELDS
            or required.get('effectAuthorized') is not False
            or not _text(required.get('status'), 20)
            or required['status'] not in {'available', 'unavailable'}):
        return False
    # Even unavailable headers must not smuggle source bodies in scalar slots.
    for field in ('reason', 'rootSessionId', 'generationId'):
        if required.get(field) is not None and not _text(required[field], empty=True):
            return False
    for field in ('inputIds', 'associationRequestIds'):
        if field in required and not _texts(required[field]):
            return False
    if 'inputCount' in required and not _integer(required['inputCount']):
        return False
    coverage = required.get('coverage')
    if (not isinstance(coverage, dict)
            or set(coverage) - {'complete', 'supportedInputIds', 'unsupportedInputs', 'reason', 'detail',
                               'missingInputIds', 'deliveredInputCount', 'projectedInputCount'}
            or type(coverage.get('complete')) is not bool
            or not _texts(coverage.get('supportedInputIds', []))
            or any(not _text(coverage[k]) for k in ('reason', 'detail') if k in coverage)):
        return False
    extra = {'missingInputIds', 'deliveredInputCount', 'projectedInputCount'} & set(coverage)
    if extra:
        delivered, projected = coverage.get('deliveredInputCount'), coverage.get('projectedInputCount')
        if (coverage['complete'] or not _integer(delivered, 1, 10**9)
                or not _integer(projected, 0, delivered)):
            return False
        if coverage.get('reason') == 'input_projection_incomplete':
            missing = coverage.get('missingInputIds')
            inputs = required.get('inputIds')
            if (extra != {'missingInputIds', 'deliveredInputCount', 'projectedInputCount'}
                    or not _texts(missing) or not missing or len(set(missing)) != len(missing)
                    or not _texts(inputs) or delivered != len(inputs)
                    or not set(missing) <= set(inputs) or len(missing) != delivered - projected):
                return False
        elif coverage.get('reason') == 'input_coverage_overflow':
            if (extra != {'deliveredInputCount', 'projectedInputCount'}
                    or delivered <= 16 or required.get('inputCount') != delivered
                    or required.get('inputIds') != []):
                return False
        else:
            return False
    unsupported = coverage.get('unsupportedInputs', [])
    if (not isinstance(unsupported, list)
            or any(not isinstance(row, dict) or set(row) != {'inputId', 'reason'}
                   or not _text(row['inputId']) or not _text(row['reason'], empty=True)
                   for row in unsupported)):
        return False
    if required['status'] == 'unavailable':
        return (coverage['complete'] is False and all(required.get(k) is None for k in
                ('version', 'workCount', 'requirementCount', 'requirementsRef')))
    # Available is an exact producer observation, not an optional collection of
    # optimistic labels. Its read handle and input coverage must agree.
    inputs = required.get('inputIds')
    return (REQUIRED_FIELDS - {'inputCount'} <= set(required)
            and _digest(required['version']) and required['reason'] is None
            and _text(required['rootSessionId']) and _text(required['generationId'])
            and _texts(inputs) and bool(inputs) and len(set(inputs)) == len(inputs)
            and len(required['associationRequestIds']) == len(inputs)
            and required.get('inputCount', len(inputs)) == len(inputs)
            and _integer(required['workCount'], 0, 8)
            and _integer(required['requirementCount'], 2 * required['workCount'],
                         4097 * required['workCount'])
            and coverage == {'complete': True, 'supportedInputIds': inputs, 'unsupportedInputs': []}
            and required['requirementsRef'] == {'action': 'workspace.work.invocation',
                'args': {'sessionId': required['rootSessionId'], 'expectedVersion': required['version']}})


def _valid_stub(row, workspace):
    if (not isinstance(row, dict) or set(row) != STUB_FIELDS
            or not _text(row['id']) or not _text(row['originSessionId'])
            or not _integer(row['revision'], 1) or not _integer(row['promiseRevision'], 1, row['revision'])
            or not _text(row['title'], 100)
            or not _text(row['nextAction'], empty=True) or not _text(row['blockedReason'], empty=True)
            or row['candidate'] is not None and not _text(row['candidate'], 100)
            or not _text(row['latestOutcome'], 100)
            or type(row['humanDecisionCurrent']) is not bool
            or not _texts(row['attentionReasons'], 16)):
        return False
    return row['readRef'] == {'action': 'workspace.work.read',
                              'args': {'workspaceId': workspace, 'id': row['id']}}


def _work_reference(value):
    """Validate the existing bounded producer DTO; never retain source bodies.

    This is a presentation fence, not an effect-admission decision. In particular,
    available discovery plus unavailable requirements cannot become empty scope.
    """
    if not isinstance(value, dict) or set(value) != {'available', 'discovery', 'required'}:
        return _unavailable_work()
    try:
        discovery, required = value['discovery'], value['required']
        if (value['available'] is not True or not isinstance(discovery, dict)
                or set(discovery) != DISCOVERY_FIELDS or not _valid_required(required)):
            return _unavailable_work()
        if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) - input_identity_bytes(required) > WORK_BYTES:
            return _unavailable_work()
        items = discovery.get('items')
        if (not isinstance(items, list) or len(items) > 20
                or not _text(discovery['workspaceId']) or not _integer(discovery['version'])
                or not _digest(discovery['snapshotVersion']) or not _digest(discovery['observationVersion'])
                or not _integer(discovery['total'], len(items))
                or type(discovery.get('truncated')) is not bool
                or discovery['truncated'] != bool(discovery.get('nextAfter'))
                or discovery['nextAfter'] is not None and not _text(discovery['nextAfter'], 4096)
                or type(discovery['observedAt']) not in (int, float) or not 0 <= discovery['observedAt'] < 10**15
                or not _text(discovery['coverage'], 500) or not _text(discovery['authority'], 500)
                or discovery['detailRequired'] is not True):
            return _unavailable_work()
        if any(not _valid_stub(row, discovery['workspaceId']) for row in items):
            return _unavailable_work()
        return copy.deepcopy(value)
    except (TypeError, ValueError, UnicodeError):
        return _unavailable_work()


class CoordinationDelivery:
    def __init__(self, previous, bridge, *, origin_scope=None, full_access=None):
        self.previous, self.bridge = previous, bridge
        self.origin_scope = origin_scope
        self.full_access = full_access
        # Transient observations, not another canonical Work store. Dropped or
        # reopened observations cannot count as delivered content.
        self.reads = []
        self.requests = {}
        self.attempts, self.calls, self.seen_calls = {}, {}, set()
        self.binding_generation = None
        self.binding_overflow = False
        self.stream_epoch = 0
        self.active_streams = 0
        self.executed_calls = set()
        self.execution_generation = None

    def execution_scope(self):
        value = self.origin_scope() if callable(self.origin_scope) else None
        return copy.deepcopy(value) if isinstance(value, dict) else None

    def current_full(self, scope):
        return bool(scope is not None and self.execution_scope() == scope
                    and callable(self.full_access) and self.full_access())

    def check_execution_scope(self, scope):
        if scope is not None and self.execution_scope() != scope:
            raise RequestFitChanged('native_scope_changed')

    def call_consumed(self, identity):
        # Execution evidence outlives missing/recovered Work observations and
        # bounded proof retention. Only a real native lifecycle change resets it.
        scope = self.execution_scope()
        if scope is not None:
            generation = tuple(scope.get(key) for key in
                ('rootSessionId', 'runtimeSessionId', 'generationId', 'stopRevision'))
            if self.execution_generation is not None and generation != self.execution_generation:
                self.executed_calls.clear()
            self.execution_generation = generation
        return identity in self.executed_calls

    def __getattr__(self, name):
        return getattr(self.previous, name)

    def remember_work(self, receipt, tool_input, *, execution_id=None):
        """Only the actual AppControl bridge result may enter this ledger."""
        args = tool_input.get('args', {})
        if tool_input.get('operation') != 'dispatch' or args.get('action') not in WORK_READS:
            return receipt
        if not isinstance(receipt, dict) or receipt.get('accepted') is not True:
            return receipt
        receipt = copy.deepcopy(receipt)
        # Core's hook JSON roundtrip can change a binary float by one ULP.
        # These observation-only timestamps have a lossless decimal transport
        # presentation; compare the entire returned receipt exactly thereafter.
        # The canonical service records and all content/authority fields stay
        # unchanged. Never fuzzy-match timestamps, body text, hashes or IDs.
        result = receipt.get('result', {})
        if args['action'] == 'workspace.work.invocation' and type(result.get('observedAt')) is float:
            result['observedAt'] = repr(result['observedAt'])
        source = result.get('source', {})
        if args['action'] == 'workspace.work.source' and type(source.get('capturedAt')) is float:
            source['capturedAt'] = repr(source['capturedAt'])
        size = len(json.dumps(receipt, ensure_ascii=False).encode())
        header = {k: result[k] for k in REQUIRED_FIELDS if k in result}
        identity_size = (input_identity_bytes(header) if args['action'] == 'workspace.work.invocation'
                         and _valid_required(header) else 0)
        if size - identity_size > WORK_BYTES or not _text(execution_id):
            return receipt  # Unbounded/raw output cannot establish delivery.
        self.reads.append({'receipt': copy.deepcopy(receipt), 'input': copy.deepcopy(tool_input),
                           'execution_id': execution_id, 'tool_call_id': None, 'bytes': size})
        while len(self.reads) > MAX_READS or sum(row['bytes'] for row in self.reads) > READ_BYTES:
            self.reads.pop(0)
        return receipt

    def bind_work(self, data):
        from .output_image_delivery import successful_output
        # Nested execution success alone does not prove that tool_exec printed
        # the page. It also cannot authorize an effect inside that same batch.
        if data.get('tool_name') != 'app_control' or not data.get('tool_call_id'):
            return
        if data.get('parent_tool_call_id') or data.get('dispatch_source'):
            self.reads = [row for row in self.reads if row['execution_id'] != data['tool_call_id']]
            return
        matches = [row for row in self.reads if row['execution_id'] == data['tool_call_id']
                   and _same_json(row['input'], data.get('tool_input'))
                   and _same_json(row['receipt'], successful_output(data.get('result')))]
        if len(matches) == 1:
            matches[0]['tool_call_id'] = data['tool_call_id']

    def _request(self, request):
        observations = [m for m in request.messages if (m.metadata or {}).get('workRequestObservation')]
        if len(observations) != 1:
            return None
        message = observations[0]
        saved = self.requests.get(message.metadata['workRequestObservation'])
        # A copied identifier with modified prefix data is not the same request.
        return saved if saved and message.content == saved['content'] else None

    def work_request(self, request):
        return self._request(request)

    def discard_work(self, binding):
        self.requests = {key: row for key, row in self.requests.items() if row is not binding}

    def _scope(self):
        value = self.origin_scope() if callable(self.origin_scope) else None
        if (not isinstance(value, dict) or value.get('inputCoverage') != 'complete'
                or not all(_text(value.get(k)) for k in ('rootSessionId', 'runtimeSessionId', 'generationId'))
                or not _texts(value.get('inputIds')) or not value['inputIds']
                or len(set(value['inputIds'])) != len(value['inputIds'])):
            return None
        # Membership is a set. Preserve every ID, with the same canonical order
        # as the service producer rather than delivery chronology.
        return json.dumps({**value, 'inputIds': sorted(value['inputIds'])},
                          sort_keys=True, ensure_ascii=False)

    def admit_work(self, attempt, row, *, guarded):
        """Join only inside the actual post-capacity-admission provider call."""
        if self.attempts.get(attempt['id']) is not attempt:
            raise ValueError('Work origin expired before admission.')
        if attempt.get('fullAssurance') and not self.current_full(attempt.get('executionScope')):
            raise ValueError('Full authority or native execution changed before admission; refit before transport.')
        scope = self._scope()
        if attempt['scope'] != scope:
            if attempt['required']['status'] == 'available':
                raise ValueError('Work origin changed during admission; refit before transport.')
            return  # Untasked requests may continue, but cannot gain proof.
        if not guarded or not scope:
            return  # An observation-only provider wrapper is not an admission.
        fields = json.loads(scope)
        if (row.get('sessionId') != fields['runtimeSessionId']
                or row.get('rootSessionId') != fields['rootSessionId'] or not _text(row.get('id'))):
            raise ValueError('Work origin changed at provider admission.')
        if attempt['origin'] is not None:
            raise ValueError('Work origin already has an admitted call; no automatic replay.')
        attempt['origin'] = InvocationOrigin(scope, row['id'], attempt['id'], attempt['requestSha256'])

    def origin_current(self, attempt):
        origin = attempt.get('origin')
        return (self.attempts.get(attempt['id']) is attempt
                and isinstance(origin, InvocationOrigin) and self._scope() == origin.scope_json)

    def begin_work(self, request, *, streaming=False):
        saved = self._request(request)
        required = saved['required'] if saved else _unavailable_work()['required']
        available = required['status'] == 'available'
        generation = (required['rootSessionId'], required['generationId']) if available else None
        if available and generation != self.binding_generation:
            self.attempts.clear()
            self.calls.clear()
            self.seen_calls.clear()
            self.binding_overflow = False
            self.binding_generation = generation
        if streaming:
            # Stream tool origins are not qualified. Invalidate before the
            # first outward yield, not merely at its terminal response. No
            # consumer-side ContextVar is needed or allowed to span that yield.
            self.stream_epoch += 1
            self.active_streams += 1
            self.calls.clear()
        identity = uuid.uuid4().hex
        attempt = {'id': identity, 'required': copy.deepcopy(required),
                   'coverage': self.request_coverage(request), 'status': 'sending',
                   'requestSha256': hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
                   'transport': 'streaming_origin_unavailable' if streaming else 'host_complete_boundary',
                   'streaming': streaming, 'stream_epoch': self.stream_epoch,
                   'overlapped_stream': bool(self.active_streams), 'stream_closed': False,
                   'responses': set(), 'native': False, 'scope': self._scope(), 'origin': None}
        attempt['executionScope'] = copy.deepcopy(saved.get('executionScope')) if saved else self.execution_scope()
        attempt['fullAssurance'] = bool(saved and saved.get('assuranceUnavailable'))
        attempt['plaintext'] = saved.get('plaintext') if saved else None
        attempt['native_send'] = None
        self.attempts[identity] = attempt
        while len(self.attempts) > 8:
            expired = next(iter(self.attempts))
            self.attempts.pop(expired)
            self.calls = {key: row for key, row in self.calls.items() if row['attemptId'] != expired}
        return attempt

    def begin_steer(self):
        # Steering has no fitted ChatRequest. Record its own admitted call, but
        # never inherit the parent's current-detail proof or pretend measurement.
        identity = uuid.uuid4().hex
        attempt = {'id': identity, 'required': _unavailable_work()['required'],
                   'coverage': {'complete': False, 'effectAuthorized': False,
                                'reason': 'Steering has no independently fitted detail request.'},
                   'status': 'sending', 'requestSha256': None, 'scope': self._scope(),
                   'origin': None, 'native': True, 'responses': set(), 'plaintext': None,
                   'native_send': None, 'streaming': False}
        attempt['executionScope'] = self.execution_scope()
        self.attempts[identity] = attempt
        while len(self.attempts) > 8:
            expired = next(iter(self.attempts))
            self.attempts.pop(expired)
            self.calls = {k: v for k, v in self.calls.items() if v['attemptId'] != expired}
        return attempt

    async def prepare_native(self, attempt):
        scope = attempt.get('executionScope')
        self.check_execution_scope(scope)
        if attempt['origin'] is not None and not self.origin_current(attempt):
            raise ValueError('Native host origin is unavailable or changed.')
        required = attempt['required']
        if self.current_full(scope):
            attempt['fullAssurance'] = True
            attempt['coverage'] = {**attempt['coverage'], 'status': 'unavailable',
                'complete': False, 'effectAuthorized': False,
                'reason': 'Full accepts Work context without current-version assurance.'}
        elif required['status'] == 'available':
            check = await asyncio.wait_for(self.bridge('work.invocation.revalidate',
                                                       {'version': required['version']}), 1.5)
            if (check.get('current') is not True or check.get('required') != required
                    or check.get('effectAuthorized') is not False):
                raise ValueError('Native Work requirements changed.')
        self.check_execution_scope(scope)
        if attempt['origin'] is not None and not self.origin_current(attempt):
            raise ValueError('Native host origin changed while checking currentness.')

    def admit_native(self, attempt, envelope, *, send_id):
        """Observe the exact immutable send argument, not a trace or checkpoint."""
        if (attempt.get('fullAssurance') and not self.current_full(attempt.get('executionScope'))
                or attempt['origin'] is not None and not self.origin_current(attempt)
                or envelope.attempt_id != attempt['id']
                or envelope.kind not in {'response.create', 'response.steer'}
                or attempt['native_send'] is not None):
            return False
        payload = json.loads(envelope.payload)
        complete = False
        if envelope.kind == 'response.create' and len(envelope.payload.encode()) <= READ_BYTES:
            # Only a real text input item counts. A matching string in tool
            # schemas, encrypted checkpoints or arbitrary JSON is not delivery.
            texts = [block.get('text') for item in payload.get('input', [])
                     if isinstance(item, dict) and item.get('role') == 'user'
                     for block in item.get('content', []) if isinstance(block, dict)
                     and block.get('type') == 'input_text']
            complete = bool(self.origin_current(attempt) and attempt['coverage'].get('complete') and
                            (attempt['required'].get('requirementCount') == 0
                             or attempt['plaintext'] and texts.count(attempt['plaintext']) == 1))
        attempt['native'] = True
        attempt['transport'] = 'native_immutable_send'
        attempt['native_send'] = {'sendId': send_id, 'kind': envelope.kind,
            'outboundSha256': hashlib.sha256(envelope.payload.encode()).hexdigest(),
            'complete': complete, 'entered': False}
        return True  # Local send admission only; never an effect grant.

    def native_response(self, attempt, response):
        if (_text(response) and len(attempt['responses']) < 128 and attempt.get('native_send')
                and self.attempts.get(attempt['id']) is attempt):
            attempt['native_send']['entered'] = True
            attempt['responses'].add(response)

    def _bind_call(self, attempt, identity, name, arguments, *, response=None):
        if self.binding_overflow or attempt['id'] not in self.attempts:
            return
        if not _text(identity) or not _text(name) or not isinstance(arguments, dict):
            return
        fingerprint = hashlib.sha256(json.dumps([name, arguments], sort_keys=True,
                                               ensure_ascii=False).encode()).hexdigest()
        existing = self.calls.get(identity)
        row = {'attemptId': attempt['id'], 'name': name, 'argsSha256': fingerprint,
               'responseId': response}
        if identity in self.seen_calls:
            # Repeated native terminal reporting is the same observation. Any
            # competing attempt/content invalidates the call for both requests.
            if existing != row:
                self.calls.pop(identity, None)
            return
        self.seen_calls.add(identity)
        if len(self.seen_calls) > 4096 or len(self.calls) >= 128:
            self.binding_overflow = True
            self.calls.clear()
            return
        self.calls[identity] = row

    def finish_work(self, attempt, response=None, *, failed=False, streaming=False):
        if attempt is None:
            # Unbound/failed-projection calls still consume their output IDs.
            # They must not inherit an earlier covered call with the same ID.
            value = response.model_dump() if hasattr(response, 'model_dump') else response
            for block in (value.get('content') or []) if isinstance(value, dict) else []:
                if isinstance(block, dict) and block.get('type') == 'tool_call' and _text(block.get('id')):
                    self.calls.pop(block['id'], None)
                    if len(self.seen_calls) < 4096:
                        self.seen_calls.add(block['id'])
                    else:
                        self.binding_overflow = True
                        self.calls.clear()
            return
        if failed:
            attempt['status'] = 'unknown'
            self.calls = {key: row for key, row in self.calls.items() if row['attemptId'] != attempt['id']}
            return
        attempt['status'] = 'returned'
        if attempt['required']['status'] != 'available':
            self.finish_work(None, response)
            return
        if attempt['native'] or streaming:
            # Native calls may already have executed; never assign the final
            # aggregate response identity to all returned tool calls.
            if streaming:
                attempt['transport'] = 'streaming_origin_unavailable'
            return
        if (attempt['overlapped_stream'] or self.active_streams
                or attempt['stream_epoch'] != self.stream_epoch):
            self.finish_work(None, response)
            return
        value = response.model_dump() if hasattr(response, 'model_dump') else response
        if not isinstance(value, dict):
            return
        for block in value.get('content') or []:
            if isinstance(block, dict) and block.get('type') == 'tool_call':
                self._bind_call(attempt, block.get('id'), block.get('name'), block.get('input'))

    def native_work_event(self, attempt, kind, value):
        """Observe existing callbacks without claiming a nonexistent send proof."""
        if attempt is None or attempt['id'] not in self.attempts:
            return
        attempt['native'] = True
        if not attempt.get('native_send'):
            attempt['transport'] = 'native_immutable_send_proof_unavailable'
        if kind == 'native.send':
            attempt['outboundSha256'] = hashlib.sha256(json.dumps(
                value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            # trace() precedes an awaited lifecycle callback and socket.send.
            # Its mutable object cannot be promoted to an immutable send receipt.
            return
        if kind != 'native.receive' or not isinstance(value, dict):
            return
        event = value.get('type')
        if event == 'response.created':
            # The installed adapter joins creation/steering lineage through the
            # awaited lifecycle before any early tool item can be dispatched.
            return
        terminal = value.get('response', {})
        response = terminal.get('id') if event in {'response.completed', 'response.done'} else value.get('response_id')
        if response not in attempt['responses']:
            return  # A mutable "latest response" is not enough attribution.
        items = terminal.get('output', []) if terminal else [value.get('item')]
        for item in items:
            if not isinstance(item, dict) or item.get('type') != 'function_call':
                continue
            try:
                arguments = json.loads(item['arguments'])
            except (KeyError, TypeError, ValueError):
                continue
            self._bind_call(attempt, item.get('call_id'), item.get('name'), arguments, response=response)

    def effect_coverage(self, data):
        """Private adapter predicate, NOT an execution grant or action policy.

        Backend applicability/current permission must be checked independently.
        Nested provenance is not qualified by this direct-call implementation.
        """
        unavailable = {'complete': False, 'effectAuthorized': False, 'status': 'unavailable'}
        nested = WORK_NESTED.get()
        if nested and nested['delivery'] is self:
            if not nested.get('coverageEligible'):
                return {**unavailable, 'reason': 'Nested causal delivery is not qualified.'}
            source = nested['lease']
            if (not nested['active'] or source.closed or not source.scope.active
                    or not source.scope.run.active or not nested['verified']
                    or not _same_json(source.scope.call.arguments, nested['parent']['tool_input'])
                    or data.get('tool_call_id') != nested['callId']
                    or data.get('tool_name') != nested['name']
                    or not _same_json(data.get('tool_input'), nested['arguments'])):
                return {**unavailable, 'reason': 'Nested call is not its active host dispatch.'}
            token = WORK_NESTED.set(None)
            try:
                parent = self.effect_coverage(nested['parent'])
            finally:
                WORK_NESTED.reset(token)
            if not parent.get('complete'):
                return parent
            return {**parent, 'parentToolCallId': nested['parent']['tool_call_id'],
                    'dispatchRunId': source.scope.run.identity,
                    'toolCallId': data['tool_call_id'], 'toolName': data['tool_name'],
                    'argsSha256': hashlib.sha256(json.dumps(
                        [data['tool_name'], data['tool_input']], sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
        if self.active_streams:
            return {**unavailable, 'reason': 'An unqualified stream can emit tool calls; origin is unavailable.'}
        if data.get('parent_tool_call_id') or data.get('dispatch_source'):
            return {**unavailable, 'reason': 'Nested causal delivery is not qualified.'}
        row = self.calls.get(data.get('tool_call_id'))
        if row is None:
            return {**unavailable, 'reason': 'No unambiguous originating request.'}
        attempt = self.attempts.get(row['attemptId'])
        try:
            fingerprint = hashlib.sha256(json.dumps([data.get('tool_name'), data.get('tool_input')],
                                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        except (TypeError, ValueError):
            return {**unavailable, 'reason': 'Tool arguments are not exact JSON.'}
        native = (attempt or {}).get('native_send')
        native_ok = bool(native and native['complete'] and native['entered']
                         and row['responseId'] in attempt['responses']
                         and attempt['status'] in {'sending', 'returned'})
        ordinary_ok = bool(attempt and not attempt['native'] and attempt['status'] == 'returned'
                           and not attempt['overlapped_stream'] and attempt['stream_epoch'] == self.stream_epoch
                           and attempt['transport'] == 'host_complete_boundary')
        active = WORK_ATTEMPT.get()
        if active and active[1] is not attempt and active[1] and active[1]['status'] != 'returned':
            return {**unavailable, 'reason': 'Executing request is not this call origin.'}
        if (not attempt or row['argsSha256'] != fingerprint or not (native_ok or ordinary_ok)):
            return {**unavailable, 'reason': 'Origin, transport or exact tool arguments are unavailable.'}
        if not self.origin_current(attempt):
            return {**unavailable, 'reason': 'Admitted host origin or full input scope is unavailable.'}
        origin = attempt['origin'].public()
        required = attempt['required']
        if (any(origin[k] != required[k] for k in ('rootSessionId', 'generationId'))
                or sorted(origin['inputIds']) != sorted(required['inputIds'])):
            return {**unavailable, 'reason': 'Required input scope differs from the admitted origin.'}
        return {**copy.deepcopy(attempt['coverage']), 'attemptId': attempt['id'],
                'requestSha256': attempt['requestSha256'], 'origin': origin,
                'toolCallId': data['tool_call_id'], 'toolName': row['name'],
                'argsSha256': row['argsSha256'], 'responseId': row['responseId'],
                'sendId': native['sendId'] if native else None,
                'outboundSha256': native['outboundSha256'] if native else attempt['requestSha256'],
                'effectAuthorized': False}

    async def recheck_effect_coverage(self, data):
        """Host-only observation seam for the existing effect-policy owner."""
        before = self.effect_coverage(data)
        if before.get('complete') is not True:
            return before
        attempt = self.attempts[before['attemptId']]
        try:
            current = await asyncio.wait_for(self.bridge('work.invocation.revalidate',
                                                        {'version': before['version']}), 1.5)
            if (current.get('current') is not True or current.get('effectAuthorized') is not False
                    or current.get('required') != attempt['required']
                    or self.effect_coverage(data) != before):
                raise ValueError('Work origin or authority changed during effect check.')
        except Exception:
            return {'status': 'unavailable', 'complete': False, 'effectAuthorized': False,
                    'reason': 'Work currentness or originating request became unavailable.'}
        return before

    async def claim_effect_origin(self, data):
        """One actual execution consumes its proof; diagnostic reads do not."""
        identity = data.get('tool_call_id')
        if self.call_consumed(identity):
            return {'complete': False, 'effectAuthorized': False, 'status': 'unavailable',
                    'reason': 'Tool execution identity was reused.'}
        if _text(identity):
            self.executed_calls.add(identity)
        return await self.recheck_effect_coverage(data)

    async def execution_origin(self, data):
        execution = WORK_EXECUTION.get()
        if execution and execution['delivery'] is self:
            if not execution['active'] or not _same_json(execution['data'], data):
                return {'complete': False, 'effectAuthorized': False, 'status': 'unavailable',
                        'reason': 'Executing tool changed after its proof was consumed.'}
            proof = execution['proof']
            if proof.get('complete') and await self.recheck_effect_coverage(data) != proof:
                return {'complete': False, 'effectAuthorized': False, 'status': 'unavailable',
                        'reason': 'Executing tool origin changed during its admission.'}
            return copy.deepcopy(proof)
        return await self.claim_effect_origin(data)

    def observe_nested_pre(self, data):
        nested = WORK_NESTED.get()
        if not nested or nested['delivery'] is not self:
            return
        source = data.get('dispatch_source', {})
        lease = nested['lease']
        if (nested['callId'] is None and nested['active'] and not lease.closed
                and lease.scope.active and lease.scope.run.active
                and source.get('session_id') == lease.scope.run.coordinator.session_id
                and source.get('run_id') == lease.scope.run.identity
                and source.get('parent_tool_call_id') == nested['parent']['tool_call_id']
                and data.get('parent_tool_call_id') == nested['parent']['tool_call_id']
                and source.get('tool_call_id') == data.get('tool_call_id')
                and source.get('tool_name') == data.get('tool_name') == nested['name']
                and _same_json(data.get('tool_input'), nested['arguments'])):
            nested['callId'], nested['verified'] = data['tool_call_id'], True

    def install_dispatch(self, coordinator):
        original = coordinator.get_capability('tools.dispatch')
        if original is None or getattr(original, '_work_delivery', None) is self:
            return
        delivery = self
        class BoundDispatch:
            _work_delivery = delivery
            def __getattr__(self, name):
                return getattr(original, name)
            def bind(self):
                lease = original.bind()
                from amplifier_module_loop_live.dispatch import DispatchLease
                if not isinstance(lease, DispatchLease) or lease.scope.run.coordinator is not coordinator:
                    return lease  # Unknown v1 implementation receives no proof.
                call = lease.scope.call
                parent = {'tool_call_id': call.id, 'tool_name': call.name, 'tool_input': copy.deepcopy(call.arguments)}
                dispatch = getattr(coordinator, '_tool_dispatch_contexts', {}).get(asyncio.current_task(), {})
                execution = WORK_EXECUTION.get()
                if execution and execution['delivery'] is delivery:
                    eligible = (execution['active'] and not execution['leaseIssued']
                                and _same_json(execution['data'], {**dispatch, **parent}))
                    # Full can execute without complete Work assurance. Native
                    # nesting still shares its parent's transcript checkpoint;
                    # it must not acquire the assurance that execution lacked.
                    coverage_eligible = eligible and execution['proof'].get('complete') is True
                    execution['leaseIssued'] = True
                else:
                    eligible = (dispatch.get('tool_call_id') == call.id
                                and not delivery.call_consumed(call.id))
                    coverage_eligible = eligible
                if eligible and not execution:
                    delivery.executed_calls.add(call.id)
                class BoundLease:
                    def __getattr__(self, name):
                        return getattr(lease, name)
                    async def call(self, name, arguments, *, request_id=None):
                        if not eligible:
                            return await lease.call(name, arguments, request_id=request_id)
                        nested = {'delivery': delivery, 'lease': lease, 'parent': parent,
                                  'name': name, 'arguments': copy.deepcopy(arguments),
                                  'active': True, 'verified': False, 'callId': None,
                                  'coverageEligible': coverage_eligible}
                        token = WORK_NESTED.set(nested)
                        execution_token = WORK_EXECUTION.set(None)
                        try:
                            return await lease.call(name, arguments, request_id=request_id)
                        finally:
                            nested['active'] = False
                            WORK_EXECUTION.reset(execution_token)
                            WORK_NESTED.reset(token)
                return BoundLease()
        coordinator.register_capability('tools.dispatch', BoundDispatch())

    def close_work_stream(self, attempt):
        if attempt is not None and attempt.get('streaming') and not attempt['stream_closed']:
            attempt['stream_closed'] = True
            self.active_streams -= 1

    def request_coverage(self, request, *, used=None):
        """Inspect exact fitted host bytes; this is NOT native/effect authority.

        The native adapter can replace history with opaque checkpoint content.
        Effect admission additionally needs an authenticated causal request
        binding and supported actual-transport proof from that adapter.
        """
        saved = self._request(request)
        result = {'status': 'unavailable', 'complete': False, 'effectAuthorized': False,
                  'scope': 'fitted_host_request_only', 'reason': 'No bound available Work observation.'}
        if not saved or saved['required']['status'] != 'available':
            return result
        if saved.get('assuranceUnavailable'):
            return {**result, 'reason': 'Full accepts Work context without current-version assurance.'}
        required = saved['required']
        result.update(version=required['version'], rootSessionId=required['rootSessionId'],
                      generationId=required['generationId'], inputIds=required['inputIds'],
                      requirementCount=required['requirementCount'], coveredCount=0)
        retained = [row for row in self.reads if _work_pair_retained(request, row)]
        def retain(page, *, source=False):
            if used is None:
                return
            for row in retained:
                value = row['receipt'].get('result', {})
                if source:
                    value = value.get('source', {})
                if _same_json(value, page) and row not in used:
                    used.append(row)
        try:
            requirements, cursor, visited = [], None, set()
            while True:
                pages = []
                for row in retained:
                    action = row['input']['args']
                    args, page = action.get('args', {}), row['receipt'].get('result', {})
                    if (action['action'] == 'workspace.work.invocation'
                            and args.get('after') == cursor and page.get('version') == required['version']
                            and all(page.get(k) == required.get(k) for k in
                                    ('rootSessionId', 'generationId', 'inputIds', 'workCount', 'requirementCount', 'coverage'))):
                        pages.append(page)
                # Duplicate genuine reads are harmless only when their content
                # page agrees. observedAt is not content or authority.
                pages = {json.dumps({k: p.get(k) for k in ('items', 'truncated', 'nextAfter')},
                                    sort_keys=True, ensure_ascii=False): p for p in pages}
                if len(pages) != 1:
                    raise ValueError('Required manifest page is absent or ambiguous in fitted content.')
                page = next(iter(pages.values()))
                retain(page)
                items = page.get('items')
                if not isinstance(items, list) or len(items) > 20:
                    raise ValueError('Invalid required manifest page.')
                requirements.extend(items)
                if len(requirements) > required['requirementCount']:
                    raise ValueError('Required manifest count changed.')
                if page.get('truncated') is False and page.get('nextAfter') is None:
                    break
                cursor = page.get('nextAfter')
                if not items or not _text(cursor, 4096) or cursor in visited:
                    raise ValueError('Required manifest cannot progress.')
                visited.add(cursor)
            if len(requirements) != required['requirementCount']:
                raise ValueError('Not every required manifest page is retained.')
            identities = set()
            for requirement in requirements:
                ref = requirement['readRef']
                identity = json.dumps(ref, sort_keys=True)
                if identity in identities or not _digest(requirement['contentSha256']):
                    raise ValueError('Required content identity is invalid or duplicated.')
                identities.add(identity)
                offset, total, pieces = 0, None, []
                while True:
                    pages = {}
                    for row in retained:
                        call = row['input']['args']
                        args = call.get('args', {})
                        if (call['action'] != ref['action'] or args.get('offset', 0) != offset
                                or any(args.get(k) != v for k, v in ref['args'].items())):
                            continue
                        page = row['receipt']['result']
                        if requirement['kind'] == 'source':
                            page = page.get('source', {})
                        pages[json.dumps(page, sort_keys=True, ensure_ascii=False)] = page
                    if len(pages) != 1:
                        raise ValueError('Required body page is absent or ambiguous in fitted content.')
                    page = next(iter(pages.values()))
                    retain(page, source=requirement['kind'] == 'source')
                    if (page.get('offset') != offset or not isinstance(page.get('text'), str)
                            or not _integer(page.get('total')) or page.get('sha256') != requirement['contentSha256']
                            or total is not None and page['total'] != total):
                        raise ValueError('Required body page identity changed.')
                    total = page['total']
                    text = page['text']
                    end = offset + len(text)
                    if (end > total or page.get('truncated') != (end < total)
                            or page.get('nextOffset') != (end if end < total else None)):
                        raise ValueError('Required body page is not contiguous.')
                    pieces.append(text)
                    if end == total:
                        break
                    if end <= offset:
                        raise ValueError('Required body page cannot progress.')
                    offset = end
                if hashlib.sha256(''.join(pieces).encode()).hexdigest() != requirement['contentSha256']:
                    raise ValueError('Required body bytes do not match the full content hash.')
                result['coveredCount'] += 1
            result.update(status='available', complete=True, reason=None)
        except (ValueError, TypeError, KeyError) as exc:
            result['reason'] = str(exc)
        return result

    async def revalidate(self, request):
        if hasattr(self.previous, 'revalidate'):
            request = await self.previous.revalidate(request)
        return request

    async def revalidate_work(self, binding):
        """Fresh currentness, bounded by the bridge's existing response deadline."""
        scope = binding.get('executionScope')
        self.check_execution_scope(scope)
        try:
            # This required check must not use the optional observation deadline.
            # The bridge owns timeout/cancellation; Stop is rechecked afterward.
            check = await self.bridge('work.invocation.revalidate',
                                      {'version': binding['required']['version']})
        except TimeoutError:
            raise RequestFitChanged('authority_unavailable', cause='bridge_timeout') from None
        except Exception:
            raise RequestFitChanged('authority_unavailable', cause='bridge_error') from None
        self.check_execution_scope(scope)
        if not isinstance(check, dict) or not isinstance(check.get('required'), dict):
            raise RequestFitChanged('authority_unavailable', cause='invalid_response')
        if check['required'].get('status') != 'available':
            raise RequestFitChanged('authority_unavailable', cause='required_unavailable')
        if (check.get('current') is not True or check.get('effectAuthorized') is not False
                or check['required'] != binding['required']):
            raise RequestFitChanged('authority_changed')

    async def finalize_work(self, request, *, fitted, binding):
        """Last awaited check after every outer wrapper, before commit/send."""
        if not binding:
            return request  # Unavailable scope is not a universal deny or grant.
        scope = binding.get('executionScope')
        self.check_execution_scope(scope)
        full = self.current_full(scope)
        if binding.get('assuranceUnavailable') and not full:
            raise RequestFitChanged('authority_changed')
        if binding['required']['status'] != 'available' and not full:
            return request
        saved = self._request(request)
        before = request.model_dump_json()
        if saved is not binding:
            raise RequestFitChanged('binding_missing' if saved is None else 'binding_changed')
        if before != fitted:
            raise RequestFitChanged('content_changed', components=changed_components(fitted, before))
        if full:
            # Keep the fitted bytes, but never promote their observed Work
            # version to current assurance when that check was waived.
            saved['assuranceUnavailable'] = True
        if not full:
            await self.revalidate_work(saved)
        self.check_execution_scope(scope)
        after = request.model_dump_json()
        if self._request(request) is not saved:
            raise RequestFitChanged('binding_missing' if self._request(request) is None else 'binding_changed')
        if after != before:
            raise RequestFitChanged('content_changed', components=changed_components(before, after))
        # Diagnostic evidence only. It cannot authorize a later tool without
        # the adapter-owned actual-transport/request-to-effect causal binding.
        saved['coverage'] = self.request_coverage(request)
        return request

    async def prepare(self, request, provider, *, commit=False):
        from amplifier_core.message_models import Message
        from .execution_events import CALL_PURPOSE
        execution_scope = self.execution_scope()
        request = await self.previous.prepare(request, provider, commit=commit)
        if CALL_PURPOSE.get() or not any(getattr(t, 'name', None) == 'app_control' for t in request.tools or []):
            return request
        try:
            current = await asyncio.wait_for(self.bridge('coordination.current', {}), 1.5)
            if not isinstance(current, dict):
                raise ValueError('Coordination projection is unavailable')
        except Exception:
            current = {'status': 'unavailable', 'authority': 'unknown; do not infer a grant or absence of peers'}
        current = {**current, 'work': _work_reference(current.get('work'))}
        identity = uuid.uuid4().hex
        content = 'Current coordination reference state (not new authority):\n' + json.dumps(current, ensure_ascii=False)
        self.requests[identity] = {'content': content, 'required': copy.deepcopy(current['work']['required']),
                                   'executionScope': execution_scope}
        while len(self.requests) > 4:
            self.requests.pop(next(iter(self.requests)))
        result = request.model_copy(update={'messages': [
            *[message for message in request.messages if not ((message.metadata or {}).get('coordinationObservation')
                or (message.metadata or {}).get('workPlaintextObservation'))],
            Message(role='user', content=content,
                    metadata={'ephemeral': True, 'coordinationObservation': True, 'workRequestObservation': identity})]})
        used = []
        coverage = self.request_coverage(result, used=used)
        if coverage.get('complete') and coverage.get('requirementCount'):
            plaintext = 'Required Work detail plaintext (host-read evidence, not authority):\n' + json.dumps({
                'version': current['work']['required']['version'],
                'reads': [{'input': row['input'], 'receipt': row['receipt']} for row in used]},
                ensure_ascii=False, sort_keys=True)
            # This suffix exists BEFORE request_budget. Never retrieve missing
            # pages, inject after fitting, or truncate constraints into a pass.
            if len(plaintext.encode()) <= PLAINTEXT_BYTES:
                self.requests[identity]['plaintext'] = plaintext
                result = result.model_copy(update={'messages': [*result.messages,
                    Message(role='user', content=plaintext,
                            metadata={'ephemeral': True, 'workPlaintextObservation': identity})]})
        return result

    def commit(self, request):
        self.previous.commit(request.model_copy(update={'messages': [message for message in request.messages
            if not ((message.metadata or {}).get('coordinationObservation')
                    or (message.metadata or {}).get('workPlaintextObservation'))]}))
