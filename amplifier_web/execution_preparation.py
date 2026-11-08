"""Disposable, content-checked canonical encoding of execution fragments.

Private pickle bytes are only compared, NEVER unpickled, stored or accepted
from another source. Custom reducers are rejected before invocation. JSON and
its content hash remain the existing storage format and authority.
"""
from collections import OrderedDict
from dataclasses import dataclass, replace
import hashlib
import json
import io
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
    def include(value):
        witness = _witness(value)
        digest.update(len(witness).to_bytes(8, 'big'))
        digest.update(witness)
    try:
        for name in sorted(tree):
            if name == 'plan':
                continue  # stored_execution deliberately excludes native plans.
            value = tree[name]
            chunked = type(value) is list and len(value) >= ExecutionPreparation.CHUNK
            include((name, chunked))
            if chunked:
                for start in range(0, len(value), ExecutionPreparation.CHUNK):
                    include(value[start:start + ExecutionPreparation.CHUNK])
            else:
                include(value)
    except (Unsupported, pickle.PicklingError, RecursionError, TypeError, ValueError):
        return None
    return digest.digest()


class ExecutionPreparation:
    CHUNK = 128
    # Separate encoding and accounting budgets: at most 128 MiB retained here.
    MAX_BYTES = 64 * 1024 * 1024

    def __init__(self):
        self.entries = OrderedDict()
        self.bytes = 0
        self.accounting = OrderedDict()
        self.accounting_bytes = 0
        self.references = OrderedDict()

    def _reserved(self, pending):
        entries = {id(entry): entry for entry in self.references.values()}
        entries.update((id(entry), entry) for entry in pending.values() if entry is not None)
        return sum(entry.charge for entry in entries.values())

    def retired(self, owner, rows):
        """Private persistence-only projection; callers must not mutate it.

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
                parts.append(part)
            witness = tuple(parts)
        except (Unsupported, pickle.PicklingError, RecursionError, TypeError, ValueError):
            return None
        old = self.accounting.get(owner)
        if old is not None and old[0] == witness:
            # This exact builtin content already passed the eligibility checks
            # below. Witnessing still rejects custom values and nested edits.
            self.accounting.move_to_end(owner)
            return old[1]
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
            previous = old[3][index] if old is not None and index < len(old[3]) else None
            if previous is not None and old[0][index] == content:
                fragment, size = previous
            else:
                fragment = accounting_projection({'retiredUsageNodes':
                    rows[index * self.CHUNK:(index + 1) * self.CHUNK]})
                size = self._accounting_charge(fragment)
            fragments.append((fragment, size))
            result.extend(fragment)
            charge += sys.getsizeof(content) + sys.getsizeof(fragments[-1]) + sys.getsizeof(size) + size
        # Shared scalars across fragments are deliberately charged more than
        # once. A changed chunk never requires walking unchanged derived graphs.
        charge += sys.getsizeof(result) + sys.getsizeof(fragments)
        if old is not None:
            self.accounting_bytes -= self.accounting.pop(owner)[2]
        if charge <= self.MAX_BYTES:
            while self.accounting and self.accounting_bytes + charge > self.MAX_BYTES:
                self.accounting_bytes -= self.accounting.popitem(last=False)[1][2]
            self.accounting[owner] = (witness, result, charge, fragments)
            self.accounting_bytes += charge
        return result

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
                pending.extend(value.keys())
                pending.extend(value.values())
            elif type(value) is list:
                pending.extend(value)
            elif type(value) not in (str, int, float, bool, type(None)):
                return self.MAX_BYTES + 1
        return charge

    def _part(self, key, value, reserved=0, project=None):
        try:
            witness = _witness(value)
        except (Unsupported, pickle.PicklingError, RecursionError, TypeError, ValueError):
            return json.dumps(project(value) if project else value,
                              ensure_ascii=False, sort_keys=True, separators=(',', ':')), None
        old = self.entries.get(key)
        if old is not None and old[0] == witness:
            self.entries.move_to_end(key)
            return old[1], old[3]
        # Synchronous builtin traversal: no await or custom hook can mutate
        # the graph between witnessing it and applying the original encoder.
        text = json.dumps(project(value) if project else value,
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        token = object()
        charge = sys.getsizeof(witness) + sys.getsizeof(text) + sys.getsizeof(key) + 256
        if old is not None:
            self.bytes -= self.entries.pop(key)[2]
        if charge + reserved <= self.MAX_BYTES:
            while self.entries and self.bytes + charge + reserved > self.MAX_BYTES:
                self.bytes -= self.entries.popitem(last=False)[1][2]
            self.entries[key] = (witness, text, charge, token)
            self.bytes += charge
            return text, token
        return text, None

    def placements(self, owner, rows, turns, receipts, parts, reserved):
        """Reuse canonical JSON, within the existing encoding budget.

        Called only after the whole input passes the builtin content witness.
        A fragment also witnesses every receipt and turn membership it depends
        on. Decoding the saved JSON produces detached storage-only rows; no
        borrowed graph, revision counter or new authority is retained.
        """
        from .session_projection import _stored_nodes
        if (type(rows) is not list or len(rows) < self.CHUNK
                or any(type(row) is not dict or any(
                    type(row.get(key)) not in (str, type(None))
                    for key in ('id', 'sessionId', 'turnId', 'kind')) for row in rows)):
            return None
        result = []
        for start in range(0, len(rows), self.CHUNK):
            fragment = rows[start:start + self.CHUNK]
            dependencies = [(row.get('turnId') in turns,
                receipts.get((row.get('sessionId'), row.get('id')))
                if row.get('kind') in ('llm', 'worker') else None) for row in fragment]
            text, token = self._part((owner, 'nodes', start, 'projection'),
                (fragment, dependencies), reserved,
                project=lambda value: _stored_nodes(value[0], turns, receipts))
            result.extend(json.loads(text))
            if text != '[]':
                parts.append((text, token))
        return result

    def prepare(self, owner, tree):
        result, _ = self._prepare(owner, tree, reserved=self._reserved({}))
        return result

    def _prepare(self, owner, tree, *, previous=None, reserved=0, node_parts=None):
        if (type(owner) is not str or len(owner) > 200 or type(tree) is not dict
                or any(type(key) is not str or len(key) > 200 for key in tree)):
            return prepare(tree), None
        fragments, signature = [], []
        cacheable = True
        for name in sorted(tree):
            value = tree[name]
            if name == 'nodes' and node_parts is not None:
                parts, chunked = node_parts, True
            elif type(value) is list and len(value) >= self.CHUNK:
                parts = [self._part((owner, name, start), value[start:start + self.CHUNK], reserved)
                         for start in range(0, len(value), self.CHUNK)]
                chunked = True
            else:
                parts = [self._part((owner, name, None), value, reserved)]
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
        from .session_projection import stored_execution
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
        digest = _input_digest(tree) if large or (previous is not None and previous.input_digest is not None) else None
        if (digest is not None and previous is not None and previous.owner == owner
                and previous.input_digest == digest and previous.projection is not None):
            resource = resource_files.reuse_reference(db, previous.resource)
            if resource is not None:
                pending[identity] = previous
                return None, {**resource, **dict(previous.projection)}
        node_parts = None
        def project_nodes(rows, turns, receipts):
            nonlocal node_parts
            if len(receipts) < self.CHUNK:
                return None  # Inline values retain the canonical Python representation.
            parts = []
            result = self.placements(identity, rows, turns, receipts, parts, self._reserved(pending))
            if result is not None:
                node_parts = parts
            return result
        value = stored_execution(tree, _retired=lambda rows: self.retired(identity, rows),
            _nodes=project_nodes if digest is not None else None)
        if len(value.get('retiredUsageNodes', ())) < self.CHUNK:
            return value, None
        resource = self.reference(db, session, value, pending, node_parts=node_parts)
        projection = execution_reference(db, value, resource=resource)
        entry = pending.get(identity)
        if entry is not None and digest is not None:
            summary = tuple((key, projection[key]) for key in (
                'executionProjection', 'pendingObservation', 'pendingWork', 'pendingVoice'))
            charge = (sys.getsizeof(digest) + sys.getsizeof(summary)
                      + sum(sys.getsizeof(pair) + sys.getsizeof(pair[0])
                            + sys.getsizeof(pair[1]) for pair in summary))
            difference = charge - entry.input_charge
            updated = replace(entry, input_digest=digest, projection=summary, input_charge=charge,
                              charge=entry.charge + difference)
            reserved = self._reserved({**pending, identity: updated})
            if reserved <= self.MAX_BYTES:
                pending[identity] = updated
                while self.entries and self.bytes + reserved > self.MAX_BYTES:
                    self.bytes -= self.entries.popitem(last=False)[1][2]
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
                    while self.entries and self.bytes + reserved > self.MAX_BYTES:
                        self.bytes -= self.entries.popitem(last=False)[1][2]
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
