"""Disposable, content-checked canonical encoding of execution fragments.

Private pickle witnesses are hashed, NEVER unpickled, stored or accepted from
another source. Custom reducers are rejected before invocation. JSON and its
content hash remain the existing storage format and authority.
"""
from collections import OrderedDict
from dataclasses import dataclass, replace
import hashlib
import json
import io
import math
import pickle
import sys

from .resource_files import Prepared, prepare


class Unsupported(Exception):
    pass


@dataclass(frozen=True)
class ReferenceEntry:
    owner: tuple
    signature: tuple
    resource: object
    charge: int
    input_digest: bytes | None = None
    retired_digest: bytes | None = None
    projection: tuple | None = None
    input_charge: int = 0


class _BoundedBytes(io.BytesIO):
    LIMIT = 8 * 1024 * 1024
    pickler = None

    def write(self, value):
        if (self.tell() + len(value) > self.LIMIT
                or self.pickler is not None and self.pickler.__sizeof__() > self.LIMIT * 4):
            raise Unsupported
        return super().write(value)


class _BuiltinsOnly(pickle.Pickler):
    # The C pickler handles exact builtins directly; every custom type reaches
    # this hook before __reduce__, __getstate__ or a buffer method can run.
    def reducer_override(self, value):
        raise Unsupported


def _witness(value):
    if sys.implementation.name != 'cpython':
        raise Unsupported
    output = _BoundedBytes()
    output.pickler = _BuiltinsOnly(output, protocol=4)
    try:
        output.pickler.dump(value)
        return output.getvalue()
    finally:
        output.pickler = None  # Do not leave the writer/pickler cycle for GC.


def _input_digest(tree):
    """Private content comparison only; JSON remains the durable authority.

    Bound temporary witnesses by the existing chunk size. A digest retains no
    borrowed source graph or history bytes, and unsupported values keep the
    original projection/encoder path without calling their reduction hooks.
    """
    if (type(tree) is not dict or any(type(key) is not str or len(key) > 200 for key in tree)):
        return None
    digest = hashlib.sha256()
    retired = None
    def include(value, field_digest=None):
        witness = _witness(value)
        length = len(witness).to_bytes(8, 'big')
        digest.update(length)
        digest.update(witness)
        if field_digest is not None:
            field_digest.update(length)
            field_digest.update(witness)
    try:
        for name in sorted(tree):
            if name == 'plan':
                continue  # stored_execution deliberately excludes native plans.
            value = tree[name]
            chunked = type(value) is list and len(value) >= ExecutionPreparation.CHUNK
            include((name, chunked))
            field_digest = hashlib.sha256() if name == 'retiredUsageNodes' and chunked else None
            if chunked:
                for start in range(0, len(value), ExecutionPreparation.CHUNK):
                    include(value[start:start + ExecutionPreparation.CHUNK], field_digest)
            else:
                include(value)
            if field_digest is not None:
                retired = field_digest.digest()
    except (Unsupported, pickle.PicklingError, RecursionError, TypeError, ValueError):
        return None
    return digest.digest(), retired


def _canonical_retired(rows):
    """Borrow already canonical rows for one synchronous, builtin-only save.

    Called only after the complete input witness excludes custom hooks. Nothing
    here is retained or returned as an inline projection; ordinary callers keep
    detached accounting. Rows needing filtering or ordered reduction fall back.
    """
    from .session_projection import ACCOUNTING_FIELDS, ACCOUNTING_USAGE_FIELDS
    if len(rows) < ExecutionPreparation.CHUNK:
        return None
    identities = set()
    for row in rows:
        if (type(row) is not dict or row.keys() - ACCOUNTING_FIELDS
                or row.get('kind') not in ('llm', 'worker')
                or type(row.get('id')) is not str or not row['id']
                or row.get('sessionId') is not None and type(row['sessionId']) is not str
                or type(row.get('revision', 0)) is not int):
            return None
        if 'usage' in row:
            usage = row['usage']
            if (type(usage) is not dict or usage.keys() - ACCOUNTING_USAGE_FIELDS
                    or any(type(amount) not in (str, int, float) for amount in usage.values())):
                return None
        identity = (row.get('sessionId'), row['id'])
        if identity in identities:
            return None
        identities.add(identity)
    return rows


class ExecutionPreparation:
    CHUNK = 128
    # At most 128 MiB retained across encoding, accounting and references.
    # Accounting and reference metadata each remain capped at 64 MiB; encoding
    # can use the remaining space instead of thrashing at a separate 64 MiB cap.
    MAX_BYTES = 64 * 1024 * 1024

    def __init__(self):
        self.entries = OrderedDict()
        self.bytes = 0
        self.admission_bytes = 0
        self.accounting = OrderedDict()
        self.accounting_bytes = 0
        self.references = OrderedDict()

    def _reserved(self, pending):
        entries = {id(entry): entry for entry in self.references.values()}
        entries.update((id(entry), entry) for entry in pending.values() if entry is not None)
        return sum(entry.charge for entry in entries.values())

    def retired(self, owner, rows, reserved=None):
        """Private persistence-only projection from compact detached fragments.

        Content is witnessed on every use, including edits through borrowed
        aliases. These detached derived rows carry no storage/commit authority.
        They exist only while this service prepares its synchronous JSON save.
        """
        from .session_projection import accounting_projection
        if (type(owner) is not str or len(owner) > 200
                or type(rows) is not list or len(rows) < self.CHUNK):
            return None  # Keep the original projection and key/hook behavior.
        try:
            parts, witness_bytes = [], 0
            for start in range(0, len(rows), self.CHUNK):
                part = _witness(rows[start:start + self.CHUNK])
                witness_bytes += len(part)
                if witness_bytes > _BoundedBytes.LIMIT:
                    return None  # Do not expand the prior whole-witness budget.
                parts.append(hashlib.sha256(part).digest())
            witness = tuple(parts)
        except (Unsupported, pickle.PicklingError, RecursionError, TypeError, ValueError):
            return None
        old = self.accounting.get(owner)
        if old is not None and old[0] == witness:
            # This exact builtin content already passed the eligibility checks
            # below. Witnessing still rejects custom values and nested edits.
            self.accounting.move_to_end(owner)
            self._trim_encoding(self._reserved({}) if reserved is None else reserved)
            return [row for text, _ in old[1] for row in json.loads(text)]
        # Witness first: custom keys/values must not gain extra lookup hooks.
        if any(type(row) is not dict or type(row.get('id')) is not str
               or row.get('sessionId') is not None and type(row['sessionId']) is not str
               or type(row.get('revision', 0)) is not int for row in rows):
            return None
        # Revision/assignment precedence can cross fragments. Only independent
        # identities may be projected separately; duplicate input keeps the
        # original ordered reduction over the entire snapshot.
        if len({(row.get('sessionId'), row['id']) for row in rows}) != len(rows):
            return None
        result, fragments = [], []
        charge = sys.getsizeof(witness) + sys.getsizeof(owner) + 512
        for index, content in enumerate(witness):
            previous = old[1][index] if old is not None and index < len(old[1]) else None
            if previous is not None and old[0][index] == content:
                text, size = previous
                fragment = json.loads(text)
            else:
                fragment = accounting_projection({'retiredUsageNodes':
                    rows[index * self.CHUNK:(index + 1) * self.CHUNK]})
                if self._accounting_charge(fragment) > self.MAX_BYTES:
                    return None  # Preserve non-JSON Python values on the canonical path.
                try:
                    # Keep insertion order here: live accounting still merges
                    # these rows before the final canonical sorted encoding.
                    text = json.dumps(fragment, ensure_ascii=False, separators=(',', ':'))
                except (TypeError, ValueError, OverflowError, RecursionError):
                    return None
                size = sys.getsizeof(text)
                # Cold and reused fragments must have the same detached form:
                # source aliasing is not part of the accounting value, but can
                # otherwise invalidate downstream placement/encoding witnesses.
                fragment = json.loads(text)
            fragments.append((text, size))
            result.extend(fragment)
            charge += sys.getsizeof(content) + sys.getsizeof(fragments[-1]) + sys.getsizeof(size) + size
        # Only text and bounded digests survive this save, not the decoded graph.
        # A changed chunk never requires walking unchanged derived graphs.
        charge += sys.getsizeof(fragments)
        if old is not None:
            self.accounting_bytes -= self.accounting.pop(owner)[2]
        if charge <= self.MAX_BYTES:
            while self.accounting and self.accounting_bytes + charge > self.MAX_BYTES:
                self.accounting_bytes -= self.accounting.popitem(last=False)[1][2]
            self.accounting[owner] = (witness, fragments, charge)
            self.accounting_bytes += charge
        self._trim_encoding(self._reserved({}) if reserved is None else reserved)
        return result

    def _trim_encoding(self, reserved):
        while self.entries and self.bytes + self.accounting_bytes + reserved > 2 * self.MAX_BYTES:
            self.bytes -= self.entries.popitem(last=False)[1][2]

    def _accounting_charge(self, result):
        """Conservative detached-fragment size; unsupported containers stay uncached."""
        pending, seen, charge = [result], set(), 0
        while pending and charge <= self.MAX_BYTES:
            value = pending.pop()
            if id(value) in seen:
                continue
            seen.add(id(value))
            charge += sys.getsizeof(value)
            if type(value) is dict:
                if any(type(key) is not str for key in value):
                    return self.MAX_BYTES + 1  # JSON would change these keys.
                pending.extend(value.keys())
                pending.extend(value.values())
            elif type(value) is list:
                pending.extend(value)
            elif type(value) is float and not math.isfinite(value):
                return self.MAX_BYTES + 1
            elif type(value) not in (str, int, float, bool, type(None)):
                return self.MAX_BYTES + 1
        return charge

    def _part(self, key, value, reserved=0, project=None, summarize=None):
        try:
            witness = hashlib.sha256(_witness(value)).digest()
        except (Unsupported, pickle.PicklingError, RecursionError, TypeError, ValueError):
            projected = project(value) if project else value
            return (json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                    None, summarize(projected) if summarize else None)
        old = self.entries.get(key)
        if old is not None and old[0] == witness:
            self.entries.move_to_end(key)
            return old[1], old[3], old[4]
        # Synchronous builtin traversal: no await or custom hook can mutate
        # the graph between witnessing it and applying the original encoder.
        projected = project(value) if project else value
        summary = summarize(projected) if summarize else None
        text = json.dumps(projected,
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        token = object()
        charge = sys.getsizeof(witness) + sys.getsizeof(text) + sys.getsizeof(key) + sys.getsizeof(summary) + 256
        if old is not None:
            self.bytes -= self.entries.pop(key)[2]
        if charge + reserved + self.accounting_bytes <= 2 * self.MAX_BYTES:
            while self.entries and self.bytes + charge + reserved + self.accounting_bytes > 2 * self.MAX_BYTES:
                self.bytes -= self.entries.popitem(last=False)[1][2]
            self.entries[key] = (witness, text, charge, token, summary)
            self.bytes += charge
            # Most new fragments enter at the eviction end, protecting reuse
            # from large one-pass scans. Once a cache's worth of new bytes has
            # arrived, give one fragment normal recency so a later working set
            # can displace an idle one. Only this byte counter is retained.
            if old is None:
                self.admission_bytes += charge
                if self.admission_bytes >= 2 * self.MAX_BYTES - reserved - self.accounting_bytes:
                    self.admission_bytes = 0
                else:
                    self.entries.move_to_end(key, last=False)
            return text, token, summary
        return text, None, summary

    def placements(self, owner, rows, turns, receipts, reserved):
        """Reuse canonical JSON, within the existing encoding budget.

        Called only after the whole input passes the builtin content witness.
        A fragment also witnesses every receipt and turn membership it depends
        on. Pending facts accompany that encoding, so unchanged fragments do
        not need decoding. No borrowed graph or new authority is retained.
        """
        from .cold_display import pending_execution_nodes
        from .session_projection import _stored_nodes
        if (type(rows) is not list or len(rows) < self.CHUNK
                or any(type(row) is not dict or any(
                    type(row.get(key)) not in (str, type(None))
                    for key in ('id', 'sessionId', 'turnId', 'kind')) for row in rows)):
            return None
        parts, observation, voice = [], False, False
        for start in range(0, len(rows), self.CHUNK):
            fragment = rows[start:start + self.CHUNK]
            dependencies = [(row.get('turnId') in turns,
                receipts.get((row.get('sessionId'), row.get('id')))
                if row.get('kind') in ('llm', 'worker') else None) for row in fragment]
            text, token, summary = self._part((owner, 'nodes', start, 'projection'),
                (fragment, dependencies), reserved,
                project=lambda value: _stored_nodes(value[0], turns, receipts),
                summarize=pending_execution_nodes)
            observation |= summary[0]
            voice |= summary[1]
            if text != '[]':
                parts.append((text, token))
        return parts, (observation, voice)

    def prepare(self, owner, tree):
        result, _ = self._prepare(owner, tree, reserved=self._reserved({}))
        return result

    def _prepare(self, owner, tree, *, previous=None, reserved=0, node_parts=None):
        if (type(owner) is not str or len(owner) > 200 or type(tree) is not dict
                or any(type(key) is not str or len(key) > 200 for key in tree)):
            if node_parts is not None:
                tree = {**tree, 'nodes': [row for text, _ in node_parts for row in json.loads(text)]}
            return prepare(tree), None
        fragments, signature = [], []
        cacheable = True
        for name in sorted(set(tree) | ({'nodes'} if node_parts is not None else set())):
            value = tree.get(name)
            if name == 'nodes' and node_parts is not None:
                parts, chunked = node_parts, True
            elif type(value) is list and len(value) >= self.CHUNK:
                parts = [self._part((owner, name, start), value[start:start + self.CHUNK], reserved)[:2]
                         for start in range(0, len(value), self.CHUNK)]
                chunked = True
            else:
                parts = [self._part((owner, name, None), value, reserved)[:2]]
                chunked = False
            tokens = tuple(token for _, token in parts)
            cacheable &= all(token is not None for token in tokens)
            signature.append((name, chunked, tokens))
            fragments.append((name, chunked, parts))
        signature = tuple(signature) if cacheable else None
        if signature is not None and signature == previous:
            return None, signature
        fields = []
        for name, chunked, parts in fragments:
            text = ('[' + ','.join(part[1:-1] for part, _ in parts) + ']'
                    if chunked else parts[0][0])
            fields.append(json.dumps(name, ensure_ascii=False) + ':' + text)
        text = '{' + ','.join(fields) + '}'
        encoded = text.encode()
        return Prepared(text, hashlib.sha256(encoded).hexdigest(), len(encoded)), signature

    def stored(self, db, session, tree, pending):
        """Prepare or reuse the existing committed execution projection.

        Every use witnesses original content before any conversion. Only a
        matching owner, successful pointer commit and verified resource allow
        skipping conversion. Changed input follows the canonical projection.
        """
        from .cold_display import execution_reference
        from .session_projection import _stored_execution_fields, _stored_nodes
        from . import resource_files
        identity = session['id']
        owner = (id(session), session.get('runtimeSessionId'), session.get('workspace'))
        previous = (self.references.get(identity)
                    if all(type(value) in (str, type(None)) for value in owner[1:]) else None)
        # A small ordinary tree cannot produce a resource-sized accounting
        # projection. Do not add a content pass there. Witness before projection
        # otherwise: custom projection hooks may mutate their own source.
        large = (type(tree) is dict and all(type(key) is str for key in tree)
                 and sum(len(rows) for rows in (tree.get('nodes'), tree.get('retiredUsageNodes'))
                         if type(rows) is list) >= self.CHUNK)
        witnessed = _input_digest(tree) if large or (previous is not None and previous.input_digest is not None) else None
        digest, retired_digest = witnessed if witnessed is not None else (None, None)
        if (digest is not None and previous is not None and previous.owner == owner
                and previous.input_digest == digest and previous.projection is not None):
            resource = resource_files.reuse_reference(db, previous.resource)
            if resource is not None:
                pending[identity] = previous
                return None, {**resource, **dict(previous.projection)}
        validated_retired = None
        def retired(rows):
            nonlocal validated_retired
            # The existing input pass already witnessed these exact values.
            # Reuse their shape validation, not a retained copy of old rows.
            if (retired_digest is not None and previous is not None
                    and previous.owner == owner and previous.retired_digest == retired_digest):
                canonical = rows
            else:
                canonical = _canonical_retired(rows) if digest is not None else None
            if canonical is not None:
                validated_retired = retired_digest
            return canonical if canonical is not None else self.retired(identity, rows, self._reserved(pending))
        value, turns, receipts = _stored_execution_fields(tree, _retired=retired)
        # Encoded nodes are an explicit part of preparation, not a partial
        # canonical tree masquerading as complete. Inline/unsupported values
        # still take the ordinary projection and retain Python representations.
        placements = (self.placements(identity, tree.get('nodes', []), turns, receipts, self._reserved(pending))
                      if digest is not None and len(receipts) >= self.CHUNK else None)
        node_parts, node_summary = placements if placements is not None else (None, (False, False))
        if node_parts is None:
            value['nodes'] = _stored_nodes(tree.get('nodes', []), turns, receipts)
        if len(value.get('retiredUsageNodes', ())) < self.CHUNK:
            return value, None
        resource = self.reference(db, session, value, pending, node_parts=node_parts)
        projection = execution_reference(db, value, resource=resource, node_summary=node_summary)
        entry = pending.get(identity)
        if entry is not None and digest is not None:
            summary = tuple((key, projection[key]) for key in (
                'executionProjection', 'pendingObservation', 'pendingWork', 'pendingVoice'))
            charge = (sys.getsizeof(digest) + sys.getsizeof(validated_retired) + sys.getsizeof(summary)
                      + sum(sys.getsizeof(pair) + sys.getsizeof(pair[0])
                            + sys.getsizeof(pair[1]) for pair in summary))
            difference = charge - entry.input_charge
            updated = replace(entry, input_digest=digest, retired_digest=validated_retired,
                              projection=summary, input_charge=charge,
                              charge=entry.charge + difference)
            reserved = self._reserved({**pending, identity: updated})
            if reserved <= self.MAX_BYTES:
                pending[identity] = updated
                self._trim_encoding(reserved)
        return None, projection

    def reference(self, db, session, tree, pending, *, node_parts=None):
        """Reuse only complete freshly witnessed, successfully committed files."""
        from . import resource_files
        identity = session['id']
        owner = (id(session), session.get('runtimeSessionId'), session.get('workspace'))
        eligible = all(type(value) in (str, type(None)) for value in owner[1:])
        previous = self.references.get(identity) if eligible else None
        if previous is not None and previous.owner != owner:
            previous = None
        prepared, signature = self._prepare(identity, tree,
            previous=previous.signature if previous is not None else None,
            reserved=self._reserved(pending), node_parts=node_parts)
        if prepared is None:
            reference = resource_files.reuse_reference(db, previous.resource)
            if reference is not None:
                pending[identity] = previous
                return reference
            # Missing or changed storage evidence keeps ordinary preparation,
            # adoption and complete file validation, never a dangling pointer.
            prepared, signature = self._prepare(identity, tree, reserved=self._reserved(pending),
                                               node_parts=node_parts)
        reference = resource_files.put_prepared(db, prepared)
        pending[identity] = None
        if signature is not None and eligible:
            resource = resource_files.validate_reference(db, reference)
            if resource is not None:
                charge = (1024 + sys.getsizeof(identity) + sys.getsizeof(owner)
                    + sum(sys.getsizeof(value) for value in owner)
                    + sys.getsizeof(signature) + sys.getsizeof(resource.source.indexed)
                    + sys.getsizeof(str(resource.source.directory))
                    + sum(sys.getsizeof(field) + sys.getsizeof(field[0]) + sys.getsizeof(field[2])
                          + sum(sys.getsizeof(token) for token in field[2]) for field in signature))
                if charge + self._reserved(pending) <= self.MAX_BYTES:
                    pending[identity] = ReferenceEntry(owner, signature, resource, charge)
                    reserved = self._reserved(pending)
                    self._trim_encoding(reserved)
        return reference

    def committed(self, pending, owners, deleted=()):
        """Failed transactions never promote or replace reference evidence."""
        from .cold_display import MARKER
        def current(identity, entry):
            owner = owners.get(identity)
            if identity in deleted or owner is None or owner.get('historyManaged'):
                return False
            key = (id(owner), owner.get('runtimeSessionId'), owner.get('workspace'))
            return (all(type(value) in (str, type(None)) for value in key[1:])
                    and key == entry.owner and dict.__contains__(owner, 'execution')
                    and 'execution' not in dict.get(owner, MARKER, {}))
        for identity, entry in tuple(self.references.items()):
            if not current(identity, entry):
                self.references.pop(identity)
        for identity, entry in pending.items():
            self.references.pop(identity, None)
            if entry is not None and current(identity, entry):
                self.references[identity] = entry

    def retain(self, owners):
        for owner in tuple(self.references):
            if owner not in owners:
                self.references.pop(owner)
        for key in tuple(self.entries):
            if key[0] not in owners:
                self.bytes -= self.entries.pop(key)[2]
        for owner in tuple(self.accounting):
            if owner not in owners:
                self.accounting_bytes -= self.accounting.pop(owner)[2]
