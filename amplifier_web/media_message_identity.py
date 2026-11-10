"""Media-only canonical identity facts; never hydrate transcript bodies."""
import copy

from .media_saved_aliases import matches_body, valid_id


class StaleMediaIdentityError(ValueError):
    """Read-only qualification raced a changing transcript; restart all checks."""


ERROR = 'Choose an existing saved assistant message in this conversation.'
FIELDS = ('id', 'nativeProject', 'nativeIdentity', 'runtimeSessionId',
          'historyManaged', 'nativeRevision', 'executionRevision', 'workspace',
          'workingDirectory', 'sessionKind', 'parentId', 'forkTranscript', 'collaborationGeneration')


def snapshot(session, message_id):
    if not valid_id(message_id):
        raise ValueError(ERROR)
    # ColdRecord.get() and _session(hydrate=True) can read entire display
    # resources. Media must never invoke those virtual getters under its lock.
    messages = dict.get(session, 'messages', [])
    rows = []
    for row in messages:
        if not isinstance(row, dict):
            raise ValueError(ERROR)
        if row.get('id') == message_id:
            if rows:
                raise ValueError(ERROR)
            rows.append(row)
    if rows and (rows[0].get('role') != 'assistant'
                 or rows[0].get('observation') or rows[0].get('observation_id')):
        raise ValueError(ERROR)
    aliases = []
    for row in messages:
        if row.get('id') != message_id and (
                row.get('nativeMessageId') == message_id
                or rows and (
                    type(rows[0].get('nativeIndex')) is int and row.get('nativeIndex') == rows[0]['nativeIndex']
                    or rows[0].get('nativeMessageId') and row.get('nativeMessageId') == rows[0]['nativeMessageId'])):
            if len(rows) + len(aliases) == 2:
                raise ValueError(ERROR)  # at most canonical spelling + one retained alias
            aliases.append(row)
    return {**{key: copy.deepcopy(dict.get(session, key)) for key in FIELDS},
            'sessionKind': copy.deepcopy(dict.get(session, 'sessionKind', 'root')),
            'coldMessages': copy.deepcopy(dict.get(session, '_coldFields', {}).get('messages')),
            'claimants': [{key: copy.deepcopy(row.get(key)) for key in
                           ('id', 'role', 'nativeIndex', 'nativeMessageId', 'source', 'text',
                            'observation', 'observation_id')} for row in aliases],
            'rows': [{key: copy.deepcopy(row.get(key)) for key in
                      ('id', 'role', 'nativeIndex', 'nativeMessageId', 'source', 'text')}
                     for row in rows]}


def canonical_required(value):
    rows = value['rows']
    return bool(value.get('nativeProject') and
                (value.get('historyManaged') or not rows or rows[0].get('source') == 'native'
                 or rows[0].get('nativeMessageId') or rows[0].get('nativeIndex') is not None))


def detached(db, value):
    """SQLite capture only, on the owner thread while app.lock is held."""
    from .resource_files import detach
    reference = value.get('coldMessages')
    if reference is not None and (not isinstance(reference, dict) or '$resource' not in reference):
        raise ValueError(ERROR)
    return detach(db, reference['$resource']) if reference is not None else None


def check_claimants(value, message_id, fact, rows, canonical_ids):
    """Same namespace and exact-binding rules as saved-display discovery."""
    from .automatic_history import display_identity
    aliases, canonical = set(), 0
    if len(rows) > 2:
        raise ValueError(ERROR)
    for row in rows:
        text = row.get('text', '')
        if (not valid_id(row.get('id')) or row.get('role') != 'assistant'
                or row.get('observation') or row.get('observation_id')
                or type(row.get('nativeIndex')) is not int or row['nativeIndex'] != fact[3]
                or row.get('nativeMessageId') not in (None, fact[0])
                or not isinstance(text, str)
                or not matches_body(fact, row, display_identity(value, row['nativeIndex'], 'assistant', text))):
            raise ValueError(ERROR)
        if row['id'] == fact[0]:
            canonical += 1
        else:
            if (value.get('historyManaged') and row.get('source') == 'native'
                    or aliases or row['id'] in canonical_ids):
                raise ValueError(ERROR)
            aliases.add(row['id'])
    if canonical > 1 or message_id != fact[0] and message_id not in aliases:
        raise ValueError(ERROR)
    return aliases


def resident_claimants(session, message_id, fact):
    """Owner-locked, bounded capture after the worker identifies the index.

    Scan only resident metadata; retain at most two relevant rows and their
    immutable text references. Never invoke a ColdRecord virtual body getter.
    """
    messages = dict.get(session, 'messages', [])
    result = []
    fields = ('id', 'role', 'nativeIndex', 'nativeMessageId', 'source',
              'observation', 'observation_id')
    for row in messages:
        if not isinstance(row, dict):
            raise ValueError(ERROR)
        if (row.get('id') in (message_id, fact[0]) or row.get('nativeIndex') == fact[3]
                or row.get('nativeMessageId') == fact[0]):
            if len(result) == 2:
                raise ValueError(ERROR)
            result.append({**{name: copy.deepcopy(row.get(name)) for name in fields},
                           'text': row.get('text', '')})
    return result


def resolve(value, message_id, saved=None, aliases=None):
    """Worker-only IO. Reuse the navigator's cached, full-text-derived IDs.

    A warm old-window lookup reads no bodies. A cold projection streams compact
    facts off-thread through Foundation's revision-checked index, not a full
    read_transcript or a durable media identity cache.
    """
    if not canonical_required(value):
        if not value['rows']:
            raise ValueError(ERROR)
        return {'snapshot': value, 'fact': (message_id, 'assistant'), 'revision': None}
    from .automatic_history import revision, display_identity
    from .conversation_navigation import source
    for attempt in range(2):
        before = revision(value)
        try:
            facts, _ = source({**value, 'historyManaged': True})
        except ValueError as exc:
            # Only a lost indexed-read revision permits one fresh acquisition.
            # Qualification and final comparisons below must never be retried.
            if (attempt or type(exc) is not ValueError
                    or exc.args != ('Session history changed during indexed read',)):
                raise
        else:
            break
    if saved is not None:
        if aliases is None:
            raise ValueError(ERROR)
        fact, signature = aliases.resolve(saved, value, message_id, before or [], facts)
        from .resource_files import detached_signature
        if (before is None or revision(value) != before
                or detached_signature(saved) != signature):
            raise ValueError(ERROR)
        return {'snapshot': value, 'fact': fact, 'revision': before,
                'detached': saved, 'signature': signature,
                'canonicalIds': tuple(item[0] for item in facts)}
    identity = message_id
    if value['rows']:
        row = value['rows'][0]
        if not isinstance(row.get('text', ''), str):
            raise ValueError(ERROR)
        if type(row.get('nativeIndex')) is int:
            identity = display_identity(value, row['nativeIndex'], row['role'], row.get('text') or '')
        elif row.get('nativeMessageId'):
            raise ValueError(ERROR)
    matches = [fact for fact in facts if fact[0] == identity
               or value['rows'] and identity == fact[5]]
    if (before is None or revision(value) != before or len(matches) != 1
            or matches[0][1] != 'assistant' or not matches[0][2]
            or value['rows'] and type(value['rows'][0].get('nativeIndex')) is int
            and matches[0][3] != value['rows'][0]['nativeIndex']):
        raise ValueError(ERROR)
    if value['rows']:
        row = value['rows'][0]
        if (row.get('nativeMessageId') not in (None, matches[0][0])
                or value.get('historyManaged') and row.get('source') == 'native'
                and row['id'] != matches[0][0]):
            raise ValueError(ERROR)
    canonical_ids = tuple(item[0] for item in facts)
    check_claimants(value, message_id, matches[0],
                    [*value['rows'], *value.get('claimants', [])], canonical_ids)
    return {'snapshot': value, 'fact': matches[0], 'revision': before,
            'canonicalIds': canonical_ids}


def complete(proof, message_id, rows):
    """Worker-only qualification of bounded owner-captured resident evidence."""
    from .automatic_history import revision
    # A retained spelling can live solely in a verified cold resource.
    # Its resident subset still obeys the same binding/collision rules.
    requested = message_id if proof['snapshot']['rows'] else proof['fact'][0]
    check_claimants(proof['snapshot'], requested, proof['fact'],
                    rows, proof['canonicalIds'])
    # This must follow resolve(): canonical writers can change the transcript
    # immediately after the initial projection, including at final attachment.
    if revision(proof['snapshot']) != proof['revision']:
        raise StaleMediaIdentityError('The saved assistant identity changed during media access.')
    return {**proof, 'residentClaimants': rows}


def validate(session, message_id, proof, saved=None):
    """Owner-locked comparisons only; never seed or qualify canonical proof."""
    if proof['snapshot'] != snapshot(session, message_id):
        raise StaleMediaIdentityError('The saved assistant identity changed during media access.')
    if proof.get('detached') != saved:
        raise ValueError('The saved assistant resource index changed during media access.')
    if proof.get('revision') is not None:
        if 'residentClaimants' not in proof:
            raise ValueError('The saved assistant identity requires worker qualification.')
        rows = resident_claimants(session, message_id, proof['fact'])
        if proof['residentClaimants'] != rows:
            raise StaleMediaIdentityError('The saved assistant identity changed during media access.')
        # The worker qualified these IDs. Count across ALL resident rows: an
        # alias duplicate at another index lies outside the bounded capture.
        alias_ids = {row['id'] for row in proof['residentClaimants']
                     if row['id'] != proof['fact'][0]}
        if alias_ids:
            count = sum(row.get('id') in alias_ids
                        for row in dict.get(session, 'messages', []))
            if count != 1:
                raise ValueError(ERROR)
    return {'id': message_id, 'role': 'assistant'}
