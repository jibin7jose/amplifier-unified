"""Explicit bounded resident Work pages; no native history or body cache."""
import base64
from collections import OrderedDict
import hashlib
import hmac
import json
from copy import deepcopy

from .browser_detail import NODE_LIMIT, SUMMARY_LIMIT, compact, detail_version, owned_nodes, work_segments
from .cold_display import raw

MAX_GROUPS = 8
MAX_RANGES = 8
MAX_STEPS = NODE_LIMIT * MAX_RANGES


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


class ResidentWorkPages:
    def __init__(self, windows):
        self.windows = windows
        self.groups = OrderedDict()
        # Disposable discovery receipts, not a second execution/history index.
        # Each explicit read visits one <=100-position range. Passive state
        # keeps its existing tail and the browser owns the older-page overlay.
        self.discovery = OrderedDict()
        self.discovery_epoch = 0
        self.tails = OrderedDict()
        self.interests = set()
        from .open_work_fields import OpenWorkFields
        self.open_fields = OpenWorkFields(self)

    def _client(self):
        clients = getattr(self.windows.service, 'clients', None)
        return clients.current.get() if clients is not None else None

    def _key(self, session, group):
        return self._client(), session['id'], group

    def _source(self, session, start, end):
        source = self.windows.live_view(session, include_nodes=False)
        nodes = self.windows.resident_tree(session).get('nodes', [])
        source['execution']['nodes'] = [self.windows.live_node(session, row) for row in nodes[start:end]
            if row.get('liveObservation') or not row.get('canonicalHistory') and not row.get('nativeHistory')]
        return source

    def _stamp(self, session):
        from .work_windows import source_identity
        source = self.windows.live_view(session, include_nodes=False)
        # Stable resident positions and bounded grouping facts, never node bodies.
        facts = [id(session), id(self.windows.resident_tree(session).get('nodes')),
            source_identity(session),
            [{key: row.get(key) for key in ('id', 'createdAt', 'timestampKnown')}
             for row in source['messages']],
            [{key: row.get(key) for key in ('id', 'anchorMessageId', 'startedAt')}
             for row in source['execution']['turns']]]
        return hashlib.sha256(_encode(facts)).hexdigest()

    def _owner_stamp(self, session):
        from .work_windows import source_identity
        return hashlib.sha256(_encode([id(session), source_identity(session)])).hexdigest()

    def _token(self, value):
        body = _encode(value)
        return base64.urlsafe_b64encode(hmac.digest(self.windows.key, body, 'sha256') + body).decode()

    def _parse(self, token):
        try:
            if not isinstance(token, str) or not 0 < len(token) <= 4096:
                raise ValueError()
            value = base64.b64decode(token, altchars=b'-_', validate=True)
            if not hmac.compare_digest(value[:32], hmac.digest(self.windows.key, value[32:], 'sha256')):
                raise ValueError()
            return json.loads(value[32:])
        except (ValueError, TypeError):
            raise ValueError('This Work cursor expired. Close and reopen its steps.') from None

    def observe_tail(self, session, live):
        # Reuse the just-computed bounded projection. Absence from a truncated
        # group page is not evidence of absence from the physical tail.
        key = self._client(), session['id']
        self.tails.pop(key, None)
        self.tails[key] = {
            'stamp': self._stamp(session), 'fence': self.windows._fence(session['id']),
            'length': len(self.windows.resident_tree(session).get('nodes', [])),
            'groups': {row['id'] for row in live['items']},
            'complete': live.get('before') is None and live.get('total') == len(live['items'])}
        while len(self.tails) > MAX_GROUPS:
            self.tails.popitem(last=False)

    def group_navigation(self, session, page, live):
        # Absence is still loading, not the end of canonical history. Anchor
        # the combined chain to an accepted (possibly empty) canonical page.
        if page is None:
            return {}
        count = len(self.windows.resident_tree(session).get('nodes', []))
        within = live.get('before')
        end = count if within else max(0, count - NODE_LIMIT)
        if not end:
            return {}
        cursor = self._group_cursor(session, end, page.get('group') if page else None, within)
        return {'before': cursor, 'hasEarlier': True}

    def _group_cursor(self, session, end, saved_group, within=None):
        token = self._token({
            'version': 1,
            'discoveryEpoch': self.discovery_epoch,
            'length': len(self.windows.resident_tree(session).get('nodes', [])),
            'client': self._client(), 'owner': session['id'], 'stamp': self._stamp(session),
            'fence': self.windows._fence(session['id']), 'end': end, 'savedGroup': saved_group,
            'within': within})
        if len(token) > 4096:
            raise ValueError('This Work cursor exceeds its bounded identity budget.')
        return 'resident-groups:' + token

    def read_groups(self, session, before):
        from .browser_detail import page
        if (self.windows.closed or getattr(self.windows.service, 'closed', False)
                or self.windows._owner(session['id']) is not session):
            raise ValueError('This Work cursor expired. Open the conversation again.')
        cursor = self._parse(before.removeprefix('resident-groups:'))
        nodes = self.windows.resident_tree(session, touch=True).get('nodes', [])
        stamp, fence = self._stamp(session), self.windows._fence(session['id'])
        end = cursor.get('end') if isinstance(cursor, dict) else None
        if (not isinstance(cursor, dict)
                or set(cursor) != {'version', 'discoveryEpoch', 'length', 'client', 'owner', 'stamp', 'fence', 'end', 'savedGroup', 'within'}
                or type(cursor.get('version')) is not int or cursor.get('version') != 1
                or cursor.get('discoveryEpoch') != self.discovery_epoch
                or type(cursor.get('length')) is not int or cursor['length'] != len(nodes)
                or cursor.get('client') != self._client()
                or cursor.get('owner') != session['id'] or cursor.get('stamp') != stamp
                or cursor.get('fence') != fence or type(end) is not int or not 0 < end <= len(nodes)
                or cursor.get('within') is not None and not isinstance(cursor['within'], str)
                or cursor.get('savedGroup') is not None and not isinstance(cursor['savedGroup'], str)):
            raise ValueError('This Work changed. Open the conversation again.')
        saved_group = cursor.get('savedGroup')
        saved = self.windows._page(session['id'], saved_group) if saved_group else None
        start = max(0, end - NODE_LIMIT)
        source = self._source(session, start, end)
        # An older slice cannot identify the last segment of a whole turn.
        # Summarize its rows without inheriting a later turn failure/liveness.
        source['execution']['turns'] = [{key: value for key, value in row.items()
            if key not in {'phase', 'status', 'endedAt'}} for row in source['execution']['turns']]
        source['generations'] = []
        result = page(source, 'groups', before=cursor.get('within'))
        # Summaries describe this range only; never imply a complete turn total.
        items = [{**row, 'partialWindow': True, 'nodeCounts': None, 'aggregateUsage': None}
                 for row in result['items']]
        key = (self._client(), session['id'], start, end, cursor.get('within'))
        if items:
            if key not in self.discovery and len(self.discovery) == MAX_RANGES:
                # An older surviving receipt must never replace an evicted
                # newer starting range for the same group. Expire discovery,
                # not already-admitted node groups or their exact fields.
                self.discovery.clear()
                self.discovery_epoch += 1
                raise ValueError('This Work discovery reached its page limit. Open the conversation again.')
            self.discovery.pop(key, None)
            self.discovery[key] = {'stamp': stamp, 'fence': fence, 'length': len(nodes),
                                   'groups': {row['id'] for row in items}}
        next_cursor = (self._group_cursor(session, end, saved_group, result['before']) if result['before']
                       else self._group_cursor(session, start, saved_group) if start
                       else saved.get('before') if saved else None)
        return {'items': items, 'turns': [{key: value for key, value in row.items()
                    if key not in {'nodeCounts', 'aggregateUsage'}} for row in result['turns']],
                'mode': 'cursor', 'offset': None, 'total': None, 'before': next_cursor,
                'hasEarlier': next_cursor is not None,
                'coverage': {'kind': 'resident-live', 'historyComplete': False,
                             'sourceStart': start, 'sourceEnd': end}}

    def _discovered_range(self, session, group, stamp, fence):
        # Only ranges explicitly observed by this client may admit an older
        # group. Current rows/grouping are re-derived below, not trusted here.
        ranges = [(start, end) for (client, identity, start, end, _), receipt in self.discovery.items()
                  if client == self._client() and identity == session['id']
                  and receipt['stamp'] == stamp and receipt['fence'] == fence
                  and receipt['length'] == len(self.windows.resident_tree(session).get('nodes', []))
                  and group in receipt['groups']]
        return max(ranges, default=None)

    def _initial_range(self, session, group, stamp, fence, count):
        tail = max(0, count - NODE_LIMIT), count
        witness = self.tails.get((self._client(), session['id']))
        if (witness is not None and witness['stamp'] == stamp and witness['fence'] == fence
                and witness['length'] == count and witness['complete']
                and group not in witness['groups']):
            return self._discovered_range(session, group, stamp, fence) or tail
        return tail

    @staticmethod
    def _rows(source, group):
        rows = owned_nodes(source)
        anchors, segments = work_segments(source, rows,
            select_groups=lambda values: [row for row in values if row['id'] == group])
        selected = [row for row in rows if row['id'] in anchors
            and str(row.get('turnId')) + '@' + (anchors[row['id']] or 'start') == group]
        return selected, anchors, next(iter(segments), None)

    def _entry(self, session, group, *, reconcile=False):
        entry = self.groups.get(self._key(session, group))
        if (entry is None or entry['owner'] != self._owner_stamp(session)
                or not reconcile and entry['stamp'] != self._stamp(session)):
            raise ValueError('This Work window expired. Close and reopen its steps.')
        self.groups.move_to_end(self._key(session, group))
        return entry

    def _compact(self, session, row, anchor, entry):
        item = compact(row, session['id'], 'nodes', SUMMARY_LIMIT)
        item.update(anchorMessageId=anchor, detailVersion=str(detail_version(row)))
        for key, value in item.items():
            if (key.endswith('Detail') and isinstance(value, dict) and value.get('part') == 'nodes'
                    and not str(value.get('windowKey', '')).startswith(('event-window:', 'tool-window:', 'open-work:'))):
                item[key] = {**value, 'windowKey': entry['key']}
        if row.get('kind') == 'llm':
            # These references belong to the accepted canonical window, not
            # this resident page. Keep its ID, generation and field reader.
            recordings = self.windows.provider_details(session, row)
            if recordings.get('requestDetail') or recordings.get('responseDetail'):
                # One exact attempt replaces the whole provider-record set.
                # A partial/new small capture cannot inherit a prior attempt's
                # response, full-download digest or capture flags.
                item.pop('requestInfo', None)
                for field in ('request', 'response'):
                    for suffix in ('', 'Detail', 'Capture', 'Recording'):
                        item.pop(field + suffix, None)
            item.update(recordings)
            item['detailVersion'] = str(detail_version([row, recordings]))
        nodes = self.windows.resident_tree(session).get('nodes', [])
        def reference(identity):
            position = entry['positions'].get(identity)
            original = nodes[position] if type(position) is int and 0 <= position < len(nodes) else None
            if (original is not None and original.get('id') == identity and original.get('kind') == 'tool'
                    and not original.get('canonicalHistory') and not original.get('nativeHistory')):
                return {'sessionId': session['id'], 'part': 'tool', 'group': entry['group'], 'id': identity}
            return None
        def exchange_refs(header, depth=0):
            if not isinstance(header, dict) or depth > 20:
                return header
            result = dict(header)
            exchange = result.get('exchange')
            if isinstance(exchange, dict) and (ref := reference(exchange.get('id'))):
                original = nodes[entry['positions'][exchange['id']]]
                result['exchange'] = self.open_fields.retain(session, entry, original,
                    {**exchange, 'toolDetail': ref})
            if isinstance(result.get('ancestors'), list):
                result['ancestors'] = [exchange_refs(value, depth+1) for value in result['ancestors'][:20]]
            return result
        if row.get('kind') == 'tool' and (ref := reference(row['id'])):
            item['toolDetail'] = ref
        item = exchange_refs(item)
        if 'delegate' in item:
            item['delegate'] = exchange_refs(item['delegate'])
        return self.open_fields.retain(session, entry, row, item)

    def read(self, session, group, before=None, revision=None):
        if not isinstance(group, str) or not 0 < len(group) <= 1024:
            raise ValueError('Choose a current Work group.')
        nodes = self.windows.resident_tree(session, touch=True).get('nodes', [])
        stamp, fence = self._stamp(session), self.windows._fence(session['id'])
        if before is None:
            start, end = self._initial_range(session, group, stamp, fence, len(nodes))
            within = None
            entry = {'group': group, 'stamp': stamp, 'key': 'resident-work:' + self._token([self._client(), session['id'], group, stamp]),
                     'owner': self._owner_stamp(session), 'ranges': {}, 'ids': {}, 'positions': {}, 'revision': None}
        else:
            cursor = self._parse(before)
            entry = self._entry(session, group)
            if (not isinstance(cursor, dict) or cursor.get('owner') != session['id'] or cursor.get('client') != self._client()
                    or cursor.get('group') != group or cursor.get('stamp') != stamp
                    or cursor.get('fence') != fence):
                raise ValueError('This Work changed. Close and reopen its latest steps.')
            start, end, within = cursor.get('start'), cursor.get('end'), cursor.get('within')
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start <= end <= len(nodes) or end - start > NODE_LIMIT):
                raise ValueError('Invalid resident Work range.')
        rows, anchors, segment = self._rows(self._source(session, start, end), group)
        if before is None:
            if segment is None:
                raise ValueError('This Work is no longer available. Refresh this conversation.')
            entry['revision'] = segment['detailRevision']
        if revision is not None and revision != entry['revision']:
            raise ValueError('This Work changed. Reload its latest steps.')
        stop = len(rows)
        if within is not None:
            stop = next((i for i, row in enumerate(rows) if row['id'] == within), None)
            if stop is None:
                raise ValueError('This Work changed. Close and reopen its steps.')
        begin = max(0, stop - NODE_LIMIT)
        selected = rows[begin:stop]
        scope = (start, end)
        ranges = set(entry['ranges']) | ({scope} if selected else set())
        identities = set(entry['ids']) | {row['id'] for row in selected}
        if len(ranges) > MAX_RANGES or len(identities) > MAX_STEPS:
            raise ValueError('This open Work window reached its page limit. Close and reopen its steps to release it.')
        if selected:
            positions = {row['id']: index for index, row in enumerate(nodes[start:end], start)}
            admitted = {row['id']: positions.get(row['id'], positions.get(row.get('parentId')))
                        for row in selected}
            if any(index is None for index in admitted.values()):
                raise ValueError('This Work changed. Close and reopen its steps.')
            entry['ranges'][scope] = True
            entry['ids'].update((row['id'], scope) for row in selected)
            entry['positions'].update(admitted)
        self.groups.pop(self._key(session, group), None)
        self.groups[self._key(session, group)] = entry
        while len(self.groups) > MAX_GROUPS:
            self.groups.popitem(last=False)
        if begin:
            next_start, next_end, next_within = start, end, selected[0]['id']
        else:
            next_start, next_end, next_within = max(0, start - NODE_LIMIT), start, None
        entry['older'] = (next_start, next_end, next_within) if begin or start else None
        cursor = self._cursor(session, group, entry)
        return {'items': [self._compact(session, row, anchors.get(row['id']), entry) for row in selected],
            'mode': 'cursor', 'offset': None, 'total': None, 'before': cursor,
            'hasEarlier': cursor is not None, 'group': group, 'revision': entry['revision'],
            'coverage': {'kind': 'resident-live', 'historyComplete': False,
                         'sourceStart': start, 'sourceEnd': end}}

    def _cursor(self, session, group, entry):
        if entry.get('older') is None:
            return None
        start, end, within = entry['older']
        return self._token({'client': self._client(), 'owner': session['id'], 'group': group, 'stamp': entry['stamp'],
            'fence': self.windows._fence(session['id']), 'start': start, 'end': end, 'within': within})

    def sync(self, session, group, known):
        if (not isinstance(known, dict) or len(known) > MAX_STEPS
                or not all(isinstance(key, str) and isinstance(value, str) for key, value in known.items())):
            raise ValueError('Choose a bounded open Work window.')
        entry = self._entry(session, group, reconcile=True)
        if not set(known) <= entry['ids'].keys():
            raise ValueError('This Work window expired. Close and reopen its steps.')
        nodes = self.windows.resident_tree(session, touch=True).get('nodes', [])
        tail_scope = (max(0, len(nodes) - NODE_LIMIT), len(nodes))
        scopes = set(entry['ids'][key] for key in known) | {tail_scope}
        source = self.windows.live_view(session)
        positions = {}
        for start, end in sorted(scopes):
            if end > len(nodes):
                raise ValueError('This Work changed. Close and reopen its steps.')
            positions.update((i, row) for i, row in enumerate(nodes[start:end], start)
                if row.get('liveObservation') or not row.get('canonicalHistory') and not row.get('nativeHistory'))
        source['execution']['nodes'] = [self.windows.live_node(session, positions[i]) for i in sorted(positions)]
        rows, anchors, segment = self._rows(source, group)
        latest, _, latest_segment = self._rows(self._source(session, *tail_scope), group)
        latest = latest[-NODE_LIMIT:]
        desired = set(known) | {row['id'] for row in latest}
        selected = [row for row in rows if row['id'] in desired]
        # Appends slide the tail by one position. Coalesce overlapping physical
        # ranges instead of spending a new page receipt for each small update.
        merged = []
        for start, end in sorted(scopes):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        normalized = [(start, min(start + NODE_LIMIT, end))
                      for low, end in merged for start in range(low, end, NODE_LIMIT)]
        by_id = {row['id']: index for index, row in positions.items()}
        ids = {}
        current_positions = {}
        for row in selected:
            # Returned delegate observations use their admitted worker's range;
            # their actual message field remains owned by the message reader.
            index = by_id.get(row['id'], by_id.get(row.get('parentId')))
            scope = next((value for value in normalized if index is not None and value[0] <= index < value[1]), None)
            if scope is None:
                raise ValueError('This Work changed. Close and reopen its steps.')
            ids[row['id']] = scope
            current_positions[row['id']] = index
        # A new message or canonical reconciliation may replace the list without
        # moving these steps. Revalidate only the admitted ranges: the same IDs
        # must still occupy their accepted positions in this exact group. Never
        # search the historical graph to recover a moved or replaced window.
        if any(current_positions.get(identity) != entry['positions'][identity] for identity in known):
            raise ValueError('This Work changed. Close and reopen its steps.')
        ranges = dict.fromkeys(ids.values(), True)
        if len(selected) > MAX_STEPS or len(ranges) > MAX_RANGES:
            raise ValueError('This open Work window reached its page limit. Close and reopen its steps to release it.')
        entry['ids'], entry['ranges'], entry['positions'] = ids, ranges, current_positions
        for key in tuple(entry.get('fields', {})):
            if key[0] not in ids:
                del entry['fields'][key]
        entry['stamp'] = self._stamp(session)
        entry['revision'] = (latest_segment or segment or {}).get('detailRevision', entry['revision'])
        older = self._cursor(session, group, entry)
        compacted = [self._compact(session, row, anchors.get(row['id']), entry) for row in selected]
        return {'items': [row for row in compacted if known.get(row['id']) != row['detailVersion']],
            'order': [row['id'] for row in selected], 'offset': None, 'total': None, 'before': older,
            'group': group, 'revision': entry['revision'],
            'coverage': {'kind': 'resident-live', 'historyComplete': False}}

    def read_text(self, session, args):
        from .browser_detail import read_text
        entry = self._field_entry(session, args)
        return read_text(self._source(session, *entry['ids'][args['id']]), args)

    def _field_entry(self, session, args):
        reference = str(args.get('windowKey', ''))
        entry = next((value for (client, identity, _), value in self.groups.items()
                      if client == self._client() and identity == session['id'] and value['key'] == reference), None)
        if entry is None or entry['stamp'] != self._stamp(session) or args.get('id') not in entry['ids']:
            raise ValueError('This Work field expired. Close and reopen its steps.')
        return entry

    def recording(self, session, args):
        entry = self._field_entry(session, args)
        position = entry['positions'][args['id']]
        start, end = entry['ids'][args['id']]
        nodes = self.windows.resident_tree(session).get('nodes', [])
        row = nodes[position] if start <= position < min(end, len(nodes)) else None
        if (row is None or row.get('id') != args['id'] or row.get('kind') != 'llm'
                or not (row.get('liveObservation') or not row.get('canonicalHistory') and not row.get('nativeHistory'))):
            raise ValueError('This provider recording moved. Reopen its Work steps.')
        field = args.get('field')
        reference = (row or {}).get('_eventFields', {}).get(field)
        recording = (row or {}).get(str(field) + 'Recording', {})
        if field not in {'request', 'response'} or reference is None or args.get('digest') != recording.get('sha256'):
            raise ValueError('This provider recording is no longer available.')
        # Opening the file runs in a thread. Freeze only the accepted position,
        # attribution and canonical evidence, not an alias to the mutable node.
        node = {key: deepcopy(row.get(key)) for key in ('id', 'kind', 'sessionId', 'rootSessionId',
                                                       'parentId', 'provider', 'model')}
        reference = deepcopy(reference)
        receipt = {'position': position, 'node': node, 'reference': reference, 'recording': deepcopy(recording)}
        return entry, node, reference, receipt

    def check_recording(self, session, args, entry, receipt):
        if self.windows.closed or self.windows._owner(session['id']) is not session:
            raise ValueError('This provider recording expired. Reopen its Work steps.')
        current, _, _, proof = self.recording(session, args)
        if current is not entry or proof != receipt:
            raise ValueError('This provider recording changed. Reopen its Work steps.')

    def retain_interest(self, identities):
        departed = self.interests - identities
        self.interests = set(identities)
        for key in tuple(self.groups):
            if key[1] in departed:
                self.groups.pop(key, None)
        for key in tuple(self.discovery):
            if key[1] in departed:
                self.discovery.pop(key, None)
        for key in tuple(self.tails):
            if key[1] in departed:
                self.tails.pop(key, None)
