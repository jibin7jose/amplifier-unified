"""Bounded browser observations, separate from immutable artifact save success.

No parser, execution, network access or cross-client report discovery lives here.
Renderer reports are untrusted display evidence, not content/safety validation.
"""
from __future__ import annotations

import re

KINDS = frozenset({'markdown', 'text', 'code', 'json', 'jsonl', 'image',
                   'pdf', 'html', 'babylon', 'mermaid', 'dot'})
PART = re.compile(r'^(?:preview|fence-\d+)$')
MESSAGES = {
    'not_checked': 'Saved; rendering has not been checked.',
    'pending': 'Saved; rendering is pending.',
    'rendered': 'Saved; the browser reported rendering. Content correctness is not verified.',
    'issues': 'Saved; the preview needs attention.',
}


def summary(kind, reports=None, *, binding=None, source_error=None, tool_state_error=None):
    """Only exact-target reports count; mount and clipboard events do not."""
    observed = {
        part: report for part, report in (reports or {}).items()
        if PART.fullmatch(part) and isinstance(report, dict)
        and binding is not None and report.get('binding') == binding
    }
    # Host source retrieval is separate from browser evidence. It can explain a
    # failure, never qualify a render. Only the selected source owner supplies it.
    if source_error and source_error.get('status') == 'error':
        observed['stored-source'] = source_error
    if tool_state_error and tool_state_error.get('status') == 'error':
        observed['stored-tool-state'] = tool_state_error
    preview = observed.get('preview', {})
    expected = ['preview']
    inventory_known = kind != 'markdown'
    if kind == 'markdown' and isinstance(preview.get('parts'), list):
        expected += preview['parts']
        inventory_known = preview.get('partsComplete') is True
    diagnostics = []
    truncated = False
    for part, report in observed.items():
        if report.get('status') not in {'error', 'unverified'}:
            continue
        if len(diagnostics) == 10:
            truncated = True
            continue
        message = str(report.get('message', ''))
        diagnostics.append({
            'part': part, 'severity': 'error' if report['status'] == 'error' else 'warning',
            'message': message[:600], 'untrustedData': True,
            **({'sourceLine': report['sourceLine']} if 'sourceLine' in report else {}),
        })
        truncated |= len(message) > 600
    statuses = [observed.get(part, {}).get('status') for part in expected]
    if any(report.get('status') == 'error' for report in observed.values()):
        status = 'issues'
    elif any(report.get('status') == 'pending' for report in observed.values()):
        status = 'pending'
    elif kind not in KINDS or not observed or not inventory_known or 'unverified' in statuses:
        status = 'not_checked'
    elif any(value is None for value in statuses):
        status = 'pending'
    elif all(value == 'ready' for value in statuses):
        status = 'rendered'
    else:
        status = 'not_checked'
    return {
        'status': status, 'message': MESSAGES[status],
        'basis': ('source_unavailable' if 'stored-source' in observed else
                  'tool_state_unavailable' if 'stored-tool-state' in observed else 'browser_reports'),
        'sourceValidation': 'not_checked',
        'coverage': 'reported_parts' if observed else 'none',
        'reportedParts': len(observed), 'expectedParts': len(expected) if inventory_known else None,
        'diagnostics': diagnostics, 'truncated': truncated,
    }


def saved(service, row, version):
    """A same-client observation readback, never a request to render or save."""
    client_id = service.clients.current.get()
    return {
        'saved': True,
        'quality': service.canvas_views.feedback(row, version),
        'readback': {'action': 'canvas.versions.inspect',
                     'args': {'id': row['id'], 'sessionId': row['sessionId'], 'version': version,
                              **({'clientId': client_id} if client_id is not None else {})}},
    }