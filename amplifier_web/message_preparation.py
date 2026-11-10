"""Disposable preparation of previously committed, content-checked messages.

Mutable aliases remain supported. Neither object identity, an execution revision,
length nor Python's type-erasing container equality proves unchanged content.
"""
from collections import OrderedDict
from dataclasses import dataclass
import math
import sys

from . import resource_files


class Unsupported(Exception):
    pass


def _snapshot(value, budget, ancestors, depth=0):
    kind = type(value)
    if not any(kind is item for item in (dict, list, str, int, float, bool, type(None))):
        raise Unsupported
    budget[0] -= 1
    budget[1] -= sys.getsizeof(value)
    if budget[0] < 0 or budget[1] < 0 or depth > 32:
        raise Unsupported
    if kind is str or kind is int or kind is bool or value is None:
        return value
    if kind is float:
        if not math.isfinite(value):
            raise Unsupported
        return value
    if kind is not dict and kind is not list:
        raise Unsupported
    if id(value) in ancestors:
        raise Unsupported
    ancestors.add(id(value))
    try:
        if kind is list:
            result = [_snapshot(item, budget, ancestors, depth + 1) for item in value]
        else:
            result = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise Unsupported
                budget[1] -= sys.getsizeof(key)
                result[key] = _snapshot(item, budget, ancestors, depth + 1)
        # Charge actual copied containers, including any allocation difference.
        budget[1] -= max(0, sys.getsizeof(result) - sys.getsizeof(value))
        if budget[1] < 0:
            raise Unsupported
        return result
    finally:
        ancestors.remove(id(value))


def _same(current, previous, budget, depth=0):
    budget[0] -= 1
    if budget[0] < 0 or depth > 32 or type(current) is not type(previous):
        return False
    kind = type(previous)
    if kind is float:
        return current.hex() == previous.hex()
    if kind is dict:
        if len(current) != len(previous):
            return False
        # Also preserve insertion order; never invoke a custom key's equality.
        return all(type(key) is str and key == old_key and _same(item, old_item, budget, depth + 1)
                   for (key, item), (old_key, old_item) in zip(current.items(), previous.items()))
    if kind is list:
        return len(current) == len(previous) and all(
            _same(item, old_item, budget, depth + 1) for item, old_item in zip(current, previous))
    return current == previous  # Previous leaves are exact immutable builtins.


def _message_snapshot(messages, budget, chunk, nodes):
    if type(messages) is not list:
        return _snapshot(messages, budget, set())
    # The byte budget spans the complete snapshot. Bound traversal per group
    # of messages so an ordinary long conversation does not always miss merely
    # because earlier messages consumed its node allowance.
    budget[1] -= sys.getsizeof(messages)
    if budget[1] < 0:
        raise Unsupported
    result, ancestors = [], {id(messages)}
    for start in range(0, len(messages), chunk):
        budget[0] = nodes
        result.extend(_snapshot(item, budget, ancestors, 1)
                      for item in messages[start:start + chunk])
    budget[1] -= max(0, sys.getsizeof(result) - sys.getsizeof(messages))
    if budget[1] < 0:
        raise Unsupported
    return result


def _same_messages(current, previous, chunk, nodes):
    if type(current) is not list or type(previous) is not list:
        return _same(current, previous, [nodes])
    if len(current) != len(previous):
        return False
    budget = [nodes]
    for index, (item, old_item) in enumerate(zip(current, previous)):
        if index % chunk == 0:
            budget[0] = nodes
        if not _same(item, old_item, budget, 1):
            return False
    return True


@dataclass(frozen=True)
class Entry:
    owner: int
    snapshot: list
    prepared: object
    charge: int


class MessagePreparation:
    CHUNK = 128
    MAX_ENTRIES = 16
    MAX_BYTES = 64 * 1024 * 1024
    MAX_NODES = 20000

    def __init__(self):
        self.entries = OrderedDict()

    def prepare(self, session, messages, pending):
        identity = session['id']
        previous = self.entries.get(identity)
        if (previous is not None and previous.owner == id(session)
                and _same_messages(messages, previous.snapshot, self.CHUNK, self.MAX_NODES)):
            pending[identity] = previous
            return previous.prepared
        pending[identity] = None
        # Bound both committed and provisional entries, counting shared hits
        # once. Saturation bypasses caching; failed saves never evict committed
        # preparation to make room for a not-yet-committed replacement.
        retained = {id(entry): entry for entry in self.entries.values()}
        retained.update((id(entry), entry) for entry in pending.values() if entry is not None)
        retained = list(retained.values())
        remaining = self.MAX_BYTES - sum(entry.charge for entry in retained)
        if len(retained) >= self.MAX_ENTRIES or remaining <= 0:
            return resource_files.prepare(messages)
        budget = [self.MAX_NODES, remaining]
        try:
            snapshot = _message_snapshot(messages, budget, self.CHUNK, self.MAX_NODES)
        except Unsupported:
            # Unsupported values retain the original encoder/error behavior.
            return resource_files.prepare(messages)
        prepared = resource_files.prepare(snapshot)
        charge = remaining - budget[1] + sys.getsizeof(prepared.encoded) + sys.getsizeof(prepared) + 256
        if charge <= remaining:
            pending[identity] = Entry(id(session), snapshot, prepared, charge)
        return prepared

    def committed(self, pending, owners, deleted=()):
        """Only the successful transaction promotes prepared input snapshots."""
        for identity, entry in tuple(self.entries.items()):
            owner = owners.get(identity)
            if (identity in deleted or owner is None or id(owner) != entry.owner
                    or not dict.__contains__(owner, 'messages')):
                self.entries.pop(identity)
        for identity, entry in pending.items():
            self.entries.pop(identity, None)
            if entry is not None and identity not in deleted:
                self.entries[identity] = entry
        total = sum(entry.charge for entry in self.entries.values())
        while self.entries and (len(self.entries) > self.MAX_ENTRIES or total > self.MAX_BYTES):
            _, entry = self.entries.popitem(last=False)
            total -= entry.charge
