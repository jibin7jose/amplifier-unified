"""Opt-in provider event capture, separate from request construction and routing.

Providers own the emitted SDK/wire payload. This host never reconstructs one
from ChatRequest, and never edits the request that goes to a provider.
"""
from __future__ import annotations

import json
import asyncio
import re
from pathlib import Path

RAW_MODULES = frozenset('provider-' + name for name in (
    'anthropic', 'openai', 'openai-chatgpt', 'azure-openai',
    'chat-completions', 'gemini', 'github-copilot', 'ollama', 'vllm',
))
MAX_CAPTURE_CHARS = 1_000_000
_DEFAULT_MARKER = '_unified_request_recording_default'
_SECRET_KEY = re.compile(r'(?i)(api.?key|password|secret|authorization|cookie|credential|access.?token|refresh.?token|id.?token|(?:^|[-_])token$)')
_TOKEN_ASSIGNMENT = re.compile(
    r'''(?ix)(?<![\w-])(["']?(?:token|[\w-]+[-_]token)["']?\s*[:=]\s*)
    (?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')


def recording_enabled(home=None):
    if home is None:
        from .host.config import app_home
        home = app_home()
    try:
        value = json.loads((Path(home) / 'diagnostics' / 'config.json').read_text())
        return isinstance(value, dict) and value.get('providerRequests') is True
    except (OSError, ValueError):
        return False


def apply_provider_recording(plan, *, enabled=None, home=None):
    """Apply defaults to every declared instance, retaining explicit opt-outs.

    Do not guess unknown adapters' config contracts. Mock has no wire request;
    LiteLLM uses raw_debug. The other catalog adapters implement raw. Custom
    adapters can opt in through their own documented config without this default.
    """
    enabled = recording_enabled(home) if enabled is None else enabled
    from .provider_environment import iter_provider_rows
    for row in iter_provider_rows(plan):
        # Snapshots and retained children can contain an earlier host default.
        # Keep its provenance outside provider config so switching the app
        # setting off does not turn that default into a permanent opt-in.
        previous = row.pop(_DEFAULT_MARKER, None)
        config = row.get('config', {})
        if previous in ('raw', 'raw_debug') and config.get(previous) is True:
            config.pop(previous)
        if not enabled:
            continue
        if row.get('enabled') is False:
            continue
        module = row['module']
        # LiteLLM now also accepts the canonical raw flag. Retain an explicit
        # choice in either spelling instead of enabling the other alias.
        if module == 'provider-litellm' and 'raw' in row.get('config', {}):
            continue
        key = 'raw' if module in RAW_MODULES else 'raw_debug' if module == 'provider-litellm' else None
        if key and key not in row.setdefault('config', {}):
            row['config'][key] = True
            row[_DEFAULT_MARKER] = key
    return plan


def redact_request(value, *, limit=MAX_CAPTURE_CHARS):
    """Bound stored content and scrub recognizable secrets before CI sees it."""
    from .worker_diagnostics import diagnostic_redactor
    from .provider_recording_media import child_context, element_context, fingerprint
    redact_diagnostic = diagnostic_redactor()
    remaining, truncated = limit, False
    def visit(item, depth=0, context=None):
        nonlocal remaining, truncated
        if remaining <= 0 or depth > 24:
            truncated = True
            return '[capture limit]'
        remaining -= 16
        image = fingerprint(item, context)
        if image is not None:
            return visit(image, depth + 1)
        if isinstance(item, str):
            # Scrub a bounded window, including a small lookahead for a token
            # spanning the cutoff. Never copy an unbounded inline image/audio.
            window = item[:max(0, remaining) + 512]
            safe = redact_diagnostic(window)
            safe = _TOKEN_ASSIGNMENT.sub(r'\1"[REDACTED]"', safe)
            if len(item) > remaining or len(safe) > remaining:
                truncated = True
                safe = safe[:max(0, remaining)] + '[capture limit]'
            remaining -= len(safe)
            return safe
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if remaining <= 0:
                    truncated = True
                    result['__capture_limit__'] = True
                    break
                name = redact_diagnostic(str(key)[:200])
                remaining -= len(name)
                result[name] = '[REDACTED]' if _SECRET_KEY.search(name) else visit(child, depth + 1, child_context(item, key, context))
            return result
        if isinstance(item, (list, tuple)):
            result = []
            for child in item:
                if remaining <= 0:
                    truncated = True
                    result.append('[capture limit]')
                    break
                result.append(visit(child, depth + 1, element_context(context)))
            return result
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return '[unsupported capture value]'
    result = visit(value, context='root')
    return result, truncated


def install_request_redaction(coordinator, *, directory=None):
    """Sanitize diagnostic copies ahead of CI's priority-100 logging hook."""
    from amplifier_core import HookResult
    if coordinator.get_capability('web.provider_request_redaction'):
        return
    coordinator.register_capability('web.provider_request_redaction', True)
    async def observe(event, data):
        from .execution_events import CURRENT_CALL
        result = dict(data)
        # This receipt comes from the wrapped host call, never from a provider.
        # Clear an untrusted value even for summary-only/unrecorded events.
        result.pop('unified_call_id', None)
        call = CURRENT_CALL.get()
        if isinstance(call, str) and call.startswith('llm:') and len(call) <= 512:
            result['unified_call_id'] = call
        fields = [key for key in ('raw', 'raw_request', 'raw_response') if key in data]
        if not fields:
            return HookResult(action='modify', data=result)
        # A provider cannot supply trusted host file references.
        result.pop('provider_payloads', None)
        truncated = False
        for key in fields:
            try:
                result[key], limited = redact_request(data[key])
                if limited and directory is not None:
                    from .provider_payloads import save
                    try:
                        saved = await asyncio.to_thread(save, directory, data[key])
                        result.setdefault('provider_payloads', {})[key] = saved
                    except Exception:
                        # Preserve the bounded redacted log copy and the real
                        # provider operation when diagnostic storage fails.
                        result['provider_payload_unavailable'] = True
            except Exception:
                # A diagnostic failure must never pass the unsanitized copy
                # to later loggers or interrupt the actual provider call.
                result[key], limited = '[capture unavailable]', True
            truncated = truncated or limited
        result['request_capture'] = {'redacted': True, 'truncated': truncated,
                                     'limit_chars': MAX_CAPTURE_CHARS}
        return HookResult(action='modify', data=result)
    for event in ('llm:request', 'llm:response'):
        coordinator.hooks.register(event, observe,
            name='unified-provider-request-redaction', priority=-100)
