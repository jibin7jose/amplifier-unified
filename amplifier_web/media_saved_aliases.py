"""Bounded saved-display alias discovery. Hints never grant media authority."""
import codecs
from collections import OrderedDict
import copy
import hashlib
import json
import re
import threading

from .resource_files import detached_signature, project_detached


ERROR = 'Choose an existing saved assistant message in this conversation.'
SCALAR_FIELDS = ('id', 'role', 'nativeIndex', 'nativeMessageId', 'source',
                 'observation', 'observation_id')
OVERSIZED = object()


def valid_id(value):
    return (isinstance(value, str) and 0 < len(value) <= 200
            and not any(ord(char) < 32 or 0xd800 <= ord(char) <= 0xdfff for char in value))


def native_scope(value):
    return dict.get(value, 'nativeIdentity') or dict.get(value, 'runtimeSessionId') or dict.__getitem__(value, 'id')


def matches_body(fact, row, identity):
    # Public text alone cannot witness a private-only canonical rewrite.
    return (identity == fact[0] or identity == fact[5]
            and fact[0] in (row.get('id'), row.get('nativeMessageId')))


def binding_hint(session, reference, rows):
    """Producer-only, bounded/body-free metadata inside the existing reference.

    No text hash or canonical IO on the synchronous producer. Discovery still
    validates the actual complete saved body, including omissions/duplicates.
    """
    bindings = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get('nativeIndex')) is not int:
            continue
        if len(bindings) == 128:
            break
        if valid_id(row.get('id')) and row.get('role') in ('user', 'assistant'):
            bindings.append({name: row[name] for name in SCALAR_FIELDS
                             if name in row and type(row[name]) in (str, int, bool, type(None))
                             and (not isinstance(row[name], str) or len(row[name]) <= 200)})
    return {**reference, 'mediaBindings': {
        'resource': reference['$resource'], 'scope': native_scope(session),
        'project': dict.get(session, 'nativeProject'),
        'revision': copy.deepcopy(dict.get(session, 'nativeRevision')), 'bindings': bindings}}


class DisplayJSON:
    """Strict incremental JSON walk. Never retain text or the row collection.

    Only bounded scalar row descriptors and string byte offsets survive a row.
    Arbitrary string values are decoded in small chunks; nesting/keys/numbers
    have explicit limits rather than allocating with input size.
    """
    def __init__(self, stream):
        self.stream = stream
        self.buffer = b''
        self.position = 0

    def offset(self):
        return self.stream.tell() - len(self.buffer) + self.position

    def peek(self):
        if self.position == len(self.buffer):
            self.buffer = self.stream.read(8192)
            self.position = 0
        return self.buffer[self.position:self.position + 1]

    def take(self):
        value = self.peek()
        if not value:
            raise ValueError(ERROR)
        self.position += 1
        return value

    def expect(self, value):
        if self.take() != value:
            raise ValueError(ERROR)

    def space(self):
        while self.peek() and self.peek() in b' \t\r\n':
            self.take()

    def string(self, *, capture=False, sink=None):
        self.expect(b'"')
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        parts, count, oversized = [], 0, False

        def emit(text):
            nonlocal count, oversized
            if sink:
                sink(text)
            if capture and not oversized:
                count += len(text)
                if count > 200:
                    parts.clear()
                    oversized = True
                else:
                    parts.append(text)

        def unicode_escape():
            raw = b''.join(self.take() for _ in range(4))
            if not re.fullmatch(b'[a-fA-F0-9]{4}', raw):
                raise ValueError(ERROR)
            return int(raw, 16)

        while True:
            char = self.peek()
            if not char:
                raise ValueError(ERROR)
            if char == b'"':
                self.take()
                emit(decoder.decode(b'', final=True))
                break
            if char == b'\\':
                self.take()
                emit(decoder.decode(b'', final=True))
                decoder = codecs.getincrementaldecoder('utf-8')('strict')
                escape = self.take()
                simple = {b'"': '"', b'\\': '\\', b'/': '/', b'b': '\b',
                          b'f': '\f', b'n': '\n', b'r': '\r', b't': '\t'}
                if escape in simple:
                    emit(simple[escape])
                elif escape == b'u':
                    code = unicode_escape()
                    # Reject lone surrogates; UTF-8 canonical identity cannot
                    # encode them. A valid pair has json.loads' exact semantics.
                    if 0xd800 <= code <= 0xdbff:
                        self.expect(b'\\')
                        self.expect(b'u')
                        low = unicode_escape()
                        if not 0xdc00 <= low <= 0xdfff:
                            raise ValueError(ERROR)
                        code = 0x10000 + ((code - 0xd800) << 10) + low - 0xdc00
                    elif 0xdc00 <= code <= 0xdfff:
                        raise ValueError(ERROR)
                    emit(chr(code))
                else:
                    raise ValueError(ERROR)
            else:
                start = self.position
                while self.position < len(self.buffer):
                    code = self.buffer[self.position]
                    if code < 32 or code in (34, 92):
                        break
                    self.position += 1
                if start == self.position:
                    raise ValueError(ERROR)
                emit(decoder.decode(self.buffer[start:self.position]))
        return OVERSIZED if oversized else ''.join(parts) if capture else None

    def value(self, depth=0, *, capture=False):
        if depth > 64:
            raise ValueError(ERROR)
        self.space()
        char = self.peek()
        if char == b'"':
            return self.string(capture=capture)
        if char in (b'{', b'['):
            opening = self.take()
            closing = b'}' if opening == b'{' else b']'
            self.space()
            if self.peek() == closing:
                self.take()
                return False
            while True:
                self.space()
                if opening == b'{':
                    self.string()
                    self.space()
                    self.expect(b':')
                self.value(depth + 1)
                self.space()
                if self.take() == closing:
                    return True
                # The consumed separator must be a comma.
                if self.buffer[self.position - 1:self.position] != b',':
                    raise ValueError(ERROR)
        token = bytearray()
        while self.peek() and self.peek() not in b' \t\r\n,]}':
            if len(token) == 64:
                raise ValueError(ERROR)
            token.extend(self.take())
        raw = bytes(token)
        if raw in (b'true', b'false', b'null'):
            return {b'true': True, b'false': False, b'null': None}[raw]
        if not re.fullmatch(b'-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', raw):
            raise ValueError(ERROR)
        return json.loads(raw) if capture else None

    def rows(self):
        self.space()
        self.expect(b'[')
        self.space()
        if self.peek() == b']':
            self.take()
        else:
            while True:
                self.space()
                self.expect(b'{')
                row, names = {}, set()
                self.space()
                if self.peek() != b'}':
                    while True:
                        self.space()
                        name = self.string(capture=True)
                        if name is OVERSIZED or name in names or len(names) >= 256:
                            raise ValueError(ERROR)
                        names.add(name)
                        self.space()
                        self.expect(b':')
                        self.space()
                        if name == 'text' and self.peek() == b'"':
                            row['textOffset'] = self.offset()
                            self.string()
                        else:
                            scalar = self.peek() not in (b'{', b'[')
                            item = self.value(capture=name in SCALAR_FIELDS)
                            if name in SCALAR_FIELDS:
                                row[name] = item if scalar else OVERSIZED
                            elif name == 'text':
                                row['badText'] = True
                        self.space()
                        separator = self.take()
                        if separator == b'}':
                            break
                        if separator != b',':
                            raise ValueError(ERROR)
                else:
                    self.take()
                yield row
                self.space()
                separator = self.take()
                if separator == b']':
                    break
                if separator != b',':
                    raise ValueError(ERROR)
        self.space()
        if self.peek():
            raise ValueError(ERROR)


def row_identity(stream, value, row):
    if (type(row.get('nativeIndex')) is not int or row['nativeIndex'] < 0
            or row.get('role') not in ('user', 'assistant') or row.get('badText')):
        raise ValueError(ERROR)
    # Match display_identity's default separators AND ensure_ascii=False,
    # including escaping control characters, quotes and backslashes.
    prefix = json.dumps([native_scope(value), row['nativeIndex'], row.get('role')],
                        ensure_ascii=False)[:-1] + ', "'
    digest = hashlib.sha256(prefix.encode())
    if 'textOffset' in row:
        stream.seek(row['textOffset'])
        DisplayJSON(stream).string(sink=lambda text: digest.update(
            json.dumps(text, ensure_ascii=False)[1:-1].encode()))
    digest.update(b'"]')
    return digest.hexdigest()[:32]


def discover(stream, value, message_id, facts):
    requested = []
    for row in DisplayJSON(stream).rows():
        if row.get('id') == message_id:
            if requested:
                raise ValueError(ERROR)
            requested.append(row)
    matches = [fact for fact in facts if fact[0] == message_id]
    if requested:
        identity = row_identity(stream, value, requested[0])
        matches = [fact for fact in facts if identity in (fact[0], fact[5])]
    if len(matches) != 1 or matches[0][1] != 'assistant' or not matches[0][2]:
        raise ValueError(ERROR)
    fact = matches[0]
    stream.seek(0)
    claimants = []
    for row in DisplayJSON(stream).rows():
        if (row.get('id') in (message_id, fact[0]) or row.get('nativeIndex') == fact[3]
                or row.get('nativeMessageId') == fact[0]):
            if len(claimants) == 2:
                raise ValueError(ERROR)
            claimants.append(row)
    aliases, canonical = set(), 0
    for row in claimants:
        if (not valid_id(row.get('id')) or row.get('role') != 'assistant'
                or row.get('observation') or row.get('observation_id')
                or type(row.get('nativeIndex')) is not int or row['nativeIndex'] != fact[3]
                or row.get('nativeMessageId') not in (None, fact[0])
                or not matches_body(fact, row, row_identity(stream, value, row))):
            raise ValueError(ERROR)
        if row['id'] == fact[0]:
            canonical += 1
        else:
            if value.get('historyManaged') and row.get('source') == 'native':
                raise ValueError(ERROR)
            if aliases or any(item[0] == row['id'] for item in facts):
                raise ValueError(ERROR)
            aliases.add(row['id'])
    if canonical > 1 or message_id != fact[0] and message_id not in aliases:
        raise ValueError(ERROR)
    if aliases:
        # A canonical-spelling request must also reject its retained claimant
        # when that alias ID is duplicated at a DIFFERENT transcript index.
        # One additional bounded walk avoids retaining an all-history ID set.
        stream.seek(0)
        count = 0
        for row in DisplayJSON(stream).rows():
            if row.get('id') in aliases:
                count += 1
                if count > 1:
                    raise ValueError(ERROR)
    return fact, tuple(aliases)


def _indexed_evidence(text):
    """Fresh UTF-8 fingerprint without a body-sized encoded copy.

    The owner-thread SQLite capture still holds the full inline string. This
    worker-only loop is O(N), with at most 8192 characters encoded per chunk.
    """
    digest, size = hashlib.sha256(), 0
    for start in range(0, len(text), 8192):
        chunk = text[start:start + 8192].encode('utf-8')
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


class SavedAliases:
    """Small instance-local cache; worker lock coalesces concurrent discovery."""
    LIMIT = 32

    def __init__(self):
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def resolve(self, source, value, message_id, revision, facts):
        with self.lock:
            # Validate pointer/path evidence even on a hit. Inline classification
            # comes from indexed content, not whether SQLite is file-backed.
            signature = detached_signature(source)
            indexed_hash, indexed_bytes = _indexed_evidence(source.indexed)
            if signature == ('inline', source.identity) and indexed_hash != source.identity:
                raise ValueError('Artifact content conflicts with its identity.')
            # Fresh compact evidence links only a previously complete projection.
            # Never retain the Detached read or its potentially large inline body.
            cache_key = (source.directory, source.identity, indexed_hash, indexed_bytes,
                         message_id, json.dumps(
                             {name: item for name, item in value.items()
                              if name not in ('rows', 'claimants')},
                             sort_keys=True, ensure_ascii=False), tuple(revision))
            cached = self.cache.get(cache_key)
            if cached and cached[1] == signature:
                fact = cached[0]
                if ([item for item in facts if item[0] == fact[0]] != [fact]
                        or any(item[0] in cached[2] for item in facts)):
                    raise ValueError(ERROR)
                self.cache.move_to_end(cache_key)
                return fact, signature
            self.cache.pop(cache_key, None)
            projection, signature = project_detached(
                source, lambda stream: discover(stream, value, message_id, facts))
            fact, alias_ids = projection
            self.cache[cache_key] = (fact, signature, alias_ids)
            while len(self.cache) > self.LIMIT:
                self.cache.popitem(last=False)
            return fact, signature
