"""Bounded projection of the host-final ChatRequest, never a wire reconstruction.

Only explicit public text/schema fields are inspected. No history, files, model
calls, reasoning blocks or opaque provider metadata are read by this projection.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

CATEGORIES = (('instructions', 'Instructions & files'), ('conversation', 'Conversation & results'),
              ('summary', 'Compacted summary'), ('reminders', 'Reminders'), ('tools', 'Tools & media'))
MAX_ITEMS = 256
MAX_TEXT_CHARS = 1_000_000
MAX_ITEM_CHARS = 120_000
MAX_VISITS = 1024
METHOD = 'ceil(Unicode code points / 4) per text span; not a tokenizer'


def field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def number(value):
    return value if type(value) is int and 0 <= value <= 10**10 else None


def bounded_schema(value, *, limit=MAX_ITEM_CHARS):
    """Encode a schema without first copying or escaping an oversized string."""
    remaining, nodes = min(MAX_ITEM_CHARS, limit), 4096
    parts = []
    def emit(text):
        nonlocal remaining
        remaining -= len(text)
        if remaining < 0:
            raise ValueError('oversized schema')
        parts.append(text)
    def visit(item, depth=0):
        nonlocal nodes
        nodes -= 1
        if depth > 24 or nodes < 0:
            raise ValueError('oversized schema')
        if isinstance(item, str):
            if len(item) > remaining:
                raise ValueError('oversized schema')
            emit(json.dumps(item, ensure_ascii=False))
        elif item is None or type(item) in (bool, int, float):
            emit(json.dumps(item, allow_nan=False))
        elif isinstance(item, dict):
            if len(item) > nodes:
                raise ValueError('oversized schema')
            emit('{')
            for index, (key, child) in enumerate(item.items()):
                if not isinstance(key, str):
                    raise ValueError('unsupported schema')
                if index: emit(',')
                visit(key, depth + 1); emit(':'); visit(child, depth + 1)
            emit('}')
        elif isinstance(item, (list, tuple)):
            if len(item) > nodes:
                raise ValueError('oversized schema')
            emit('[')
            for index, child in enumerate(item):
                if index: emit(',')
                visit(child, depth + 1)
            emit(']')
        else:
            raise ValueError('unsupported schema')
    visit(value)
    return ''.join(parts)


def projection(request, *, bodies=False, work=None):
    """One bounded pass over the request's inspectable text, not saved history.

    Without body consent, labels are generic and no text/path/hash is retained.
    The result deliberately has no percentage of the provider's total.
    """
    from .provider_recording import redact_request
    items, texts = [], {}
    remaining = MAX_TEXT_CHARS
    scan_remaining, visits = MAX_TEXT_CHARS, MAX_VISITS
    work = work if work is not None else {}
    work.update(messages=0, blocks=0, tools=0, wrapperCharacters=0)
    omitted = False

    def add(category, label, text=None, *, source=None, unavailable=None):
        nonlocal remaining, omitted
        if len(items) >= MAX_ITEMS:
            omitted = True
            return
        identity = str(len(items))
        row = {'id': identity, 'category': category, 'label': redact_request(label[:240], limit=1024)[0],
               'tokens': None, 'characters': None, 'bodyAvailable': False,
               'bodyUnavailable': unavailable or 'Request text was not recorded.', 'method': 'unavailable'}
        if isinstance(text, str):
            length = len(text)
            # No scanning/copying of arbitrarily large text. Omitted spans are
            # unscaled, not misleading estimates for a truncated fragment.
            complete = length <= min(remaining, MAX_ITEM_CHARS)
            if complete:
                remaining -= length
                safe, limited = redact_request(text, limit=max(length * 2 + 1024, 2048))
                redacted = limited or safe != text
                row['characters'] = length
                if not redacted:
                    row.update(tokens=(length + 3) // 4, method='local_heuristic')
                else:
                    row['bodyUnavailable'] = 'Recognizable secrets were redacted; original size attribution is unavailable.'
                if bodies:
                    row.update(bodyAvailable=True, bodyUnavailable=None, redacted=redacted,
                               sha256=hashlib.sha256(safe.encode()).hexdigest(), bytes=len(safe.encode()))
                    texts[identity] = safe
                    if source:
                        row['sourceLabel'] = redact_request(source[:600], limit=2048)[0]
                        row['sourceLabelTruncated'] = len(source) > 600
                        row['sourceKind'] = 'captured wrapper label; not verified filesystem provenance'
                    if redacted:
                        row['note'] = 'Redacted captured copy; hash describes this copy, not the original.'
            else:
                omitted = True
                row['bodyUnavailable'] = 'Text span exceeds the bounded observation allowance; no partial size is shown.'
        items.append(row)

    for index, message in enumerate(field(request, 'messages', ()) or ()):
        if len(items) >= MAX_ITEMS or remaining <= 0 or visits <= 0:
            omitted = True
            break
        visits -= 1
        work['messages'] += 1
        role = field(message, 'role', '')
        metadata = field(message, 'metadata', {}) or {}
        metadata = metadata if isinstance(metadata, dict) else {}
        # Provider-owned compaction is never decoded or copied into an inspector.
        if len(metadata) > 32:
            add('tools', 'Unclassified message', unavailable='Message metadata exceeds the observation allowance.')
            omitted = True
            continue
        if any(isinstance(k, str) and 'compaction' in k[:200] for k in metadata):
            add('summary', 'Native compacted state', unavailable='Provider-owned opaque state; not inspectable.')
            continue
        category = ('summary' if metadata.get('source') == 'context-managed'
                    else 'reminders' if metadata.get('ephemeral') else
                    'instructions' if role in {'system', 'developer'} else 'conversation')
        content = field(message, 'content')
        blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else content or []
        if not isinstance(blocks, (list, tuple)):
            add(category, f'Message {index + 1}', unavailable='Unsupported message representation.')
            continue
        for block in blocks:
            if len(items) >= MAX_ITEMS or remaining <= 0 or visits <= 0:
                omitted = True
                break
            visits -= 1
            work['blocks'] += 1
            kind = field(block, 'type')
            if kind != 'text':
                add('tools', 'Opaque or media block', unavailable='Media, reasoning and provider-owned blocks are not recorded or sized.')
                continue
            text = field(block, 'text', '')
            if not isinstance(text, str):
                continue
            label = f'{role.title() if role in {"system", "developer", "user", "assistant", "tool"} else "Message"} {index + 1}'
            # Monotonic bounded search, no regex suffix backtracking or eager
            # match list. Each successful wrapper advances beyond its closing
            # tag. An incomplete/malformed opening ends wrapper recognition.
            # All inspected spans share one scan allowance, including empties.
            position = 0
            if category == 'instructions' and len(text) <= min(scan_remaining, remaining):
                scan_remaining -= len(text)
                work['wrapperCharacters'] += len(text)
                opening, closing = '<context_file paths="', '</context_file>'
                while len(items) < MAX_ITEMS and remaining > 0:
                    start = text.find(opening, position)
                    if start < 0:
                        break
                    label_start = start + len(opening)
                    label_end = text.find('">', label_start, min(len(text), label_start + 2002))
                    if label_end < 0:
                        break
                    body_start = label_end + 2
                    end = text.find(closing, body_start)
                    if end < 0:
                        break
                    if start > position:
                        add(category, label + ' · instruction text', text[position:start])
                    source = text[label_start:label_end]
                    add(category, source if bodies else f'Instruction file {len(items) + 1}', text[body_start:end], source=source)
                    position = end + len(closing)
            if position < len(text) or position == 0:
                add(category, label, text[position:])
        # Tool call arguments are model context too, but arbitrary arguments
        # may contain secrets. They stay unscaled in this first projection.
        if field(message, 'tool_calls'):
            add('tools', 'Tool call framing & arguments', unavailable='Structured call arguments are not attributed in this projection.')
    for index, tool in enumerate(field(request, 'tools', ()) or ()):
        if len(items) >= MAX_ITEMS or remaining <= 0 or visits <= 0:
            omitted = True
            break
        visits -= 1
        work['tools'] += 1
        description = field(tool, 'description', '')
        schema = field(tool, 'parameters', field(tool, 'input_schema', {}))
        # Bounded encoder stops before materializing an unbounded schema.
        try:
            if not isinstance(description, str) or len(description) + 1 > min(MAX_ITEM_CHARS, remaining):
                raise ValueError('oversized schema description')
            text = description + '\n' + bounded_schema(schema, limit=min(MAX_ITEM_CHARS, remaining) - len(description) - 1)
            name = field(tool, 'name')
            label = f'Tool · {name}' if bodies and isinstance(name, str) else f'Tool schema {index + 1}'
            add('tools', label, text)
        except (ValueError, TypeError):
            add('tools', f'Tool schema {index + 1}', unavailable='Schema unavailable within the observation allowance.')
    categories = []
    for identity, label in CATEGORIES:
        rows = [r for r in items if r['category'] == identity]
        categories.append({'id': identity, 'label': label, 'items': len(rows),
                           'tokens': sum(r['tokens'] for r in rows if r['tokens'] is not None),
                           'unknownItems': sum(r['tokens'] is None for r in rows)})
    return {'categories': categories, 'items': items,
            'estimatedTextTokens': sum(r['tokens'] for r in categories),
            'method': METHOD, 'coverage': 'partial' if omitted else 'inspectable_text',
            'bounded': omitted, 'unscaled': 'Provider serialization, framing, media, opaque state and redacted or omitted spans are outside this estimate.'}, texts


def budget_public(decision):
    """Allowlist an already obtained provider decision; never initiate a count."""
    if not isinstance(decision, dict):
        return {}
    result = {key: number(decision.get(key)) for key in
              ('estimated_input_tokens', 'input_limit_tokens', 'max_output_tokens', 'context_window_tokens')}
    measurement = decision.get('measurement')
    if isinstance(measurement, dict) and measurement.get('kind') == 'provider_count':
        count = number(measurement.get('input_tokens'))
        if count is not None:
            result['measurement'] = {'kind': 'provider_count', 'input_tokens': count}
    return result


@dataclass(frozen=True)
class RequestFingerprint:
    """Private equality witness plus fixed, content-free failure evidence."""
    signature: str | None
    status: str
    reason: str | None = None

    def public(self):
        return {'status': self.status, 'reason': self.reason}


class _FingerprintUnavailable(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


def fingerprint_request(request, options, *, work=None):
    """Complete bounded binding, never a digest of an inspected prefix.

    Large/opaque requests simply lose optional pre-send count attribution.
    Only fixed failure codes can be published; no content, keys or hash escape.
    """
    digest = hashlib.sha256()
    remaining, nodes = MAX_TEXT_CHARS, 20_000
    work = work if work is not None else {}
    work.update(nodes=0, encodedCharacters=0)

    def unavailable(status, reason):
        raise _FingerprintUnavailable(status, reason)

    def visit(value, depth=0):
        nonlocal remaining, nodes
        nodes -= 1
        if nodes < 0:
            unavailable('bounded', 'node_limit')
        work['nodes'] += 1
        if depth > 24:
            unavailable('bounded', 'depth_limit')
        fields = getattr(type(value), 'model_fields', None)
        if isinstance(fields, dict):
            if getattr(type(value), 'model_computed_fields', None):
                unavailable('unsupported', 'computed_fields')
            if len(fields) > 4096:
                unavailable('bounded', 'container_limit')
            digest.update(b'm')
            for name in sorted(fields):
                visit(name, depth + 1)
                visit(getattr(value, name), depth + 1)
            visit(getattr(value, 'model_extra', None), depth + 1)
        elif isinstance(value, dict):
            if len(value) > 4096:
                unavailable('bounded', 'container_limit')
            if any(not isinstance(k, str) for k in value):
                unavailable('unsupported', 'mapping_key')
            digest.update(b'd')
            for name in sorted(value):
                visit(name, depth + 1)
                visit(value[name], depth + 1)
        elif isinstance(value, (list, tuple)):
            if len(value) > 4096:
                unavailable('bounded', 'container_limit')
            digest.update(b'l')
            for child in value:
                visit(child, depth + 1)
        elif value is None or type(value) in (str, bool, int, float):
            if isinstance(value, str) and len(value) > remaining:
                unavailable('bounded', 'character_limit')
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            remaining -= len(encoded)
            if remaining < 0:
                unavailable('bounded', 'character_limit')
            work['encodedCharacters'] += len(encoded)
            digest.update(encoded.encode())
        else:
            unavailable('unsupported', 'value_type')
        digest.update(b';')
    try:
        options = options or {}
        if not isinstance(options, dict):
            unavailable('unsupported', 'options_type')
        nested = options.get('request_options') or {}
        if not isinstance(nested, dict):
            unavailable('unsupported', 'options_type')
        if len(options) > 4096 or len(nested) > 4096:
            unavailable('bounded', 'container_limit')
        options = {**nested, **{k: v for k, v in options.items()
                               if k not in {'context_estimate', 'request_options'}}}
        visit(request)
        visit(options)
    except _FingerprintUnavailable as error:
        return RequestFingerprint(None, error.status, error.reason)
    except (ValueError, TypeError):
        return RequestFingerprint(None, 'unsupported', 'encoding')
    return RequestFingerprint(digest.hexdigest(), 'complete')


def request_fingerprint(request, options):
    """Compatibility for internal equality callers; None is never equality."""
    return fingerprint_request(request, options).signature
