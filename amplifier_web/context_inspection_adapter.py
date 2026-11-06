"""Optional native observation attached to the existing provider lifecycle owner."""
from __future__ import annotations

import asyncio
import inspect

from .context_inspection import projection, budget_public, number, request_fingerprint
from .context_inspection_store import InspectionStore, preferences


def provider_policy(provider):
    """Respect explicit raw opt-outs through host wrappers; never enable raw."""
    seen, opted_in = set(), False
    while provider is not None and id(provider) not in seen and len(seen) < 8:
        seen.add(id(provider))
        config = getattr(provider, 'config', {})
        if isinstance(config, dict):
            if config.get('raw') is False or config.get('raw_debug') is False:
                return False, False
            opted_in = opted_in or config.get('raw') is True or config.get('raw_debug') is True
        provider = getattr(provider, 'original', None)
    return True, opted_in


class ContextInspection:
    def __init__(self, coordinator, directory, home):
        self.coordinator, self.home = coordinator, home
        self.store = InspectionStore(directory)
        self.budgets = []
        self.pending = {}
        cleanup = getattr(coordinator, 'register_cleanup', None)
        if callable(cleanup):
            cleanup(self.drain)

    def budget(self, request, options, decision):
        signature = request_fingerprint(request, options)
        if signature:
            self.budgets = (self.budgets + [(request, signature, budget_public(decision))])[-4:]

    async def begin(self, row, request, provider, options, provider_defaults=None):
        config = preferences(self.home)
        metadata, bodies = provider_policy(provider)
        enabled = config['enabled'] and metadata
        # Auxiliary and child calls never enter this root's ring; caller also
        # scopes the observer to the exact coordinator/session identity.
        if row.get('purpose') or row.get('lifecycle') == 'background':
            return
        if len(self.pending) >= 4:
            return  # Observation pressure never limits model execution.
        signature = request_fingerprint(request, options) if self.budgets else None
        budget = next((b for r, s, b in reversed(self.budgets) if r is request and s == signature), {})
        self.budgets = [v for v in self.budgets if v[0] is not request]
        context = self.coordinator.get('context')
        context_config = getattr(context, 'config', {}) or {}
        context_limit = number(context_config.get('max_tokens')) if isinstance(context_config, dict) else None
        threshold = context_config.get('summarize_trigger') if isinstance(context_config, dict) else None
        threshold = (int(context_limit * threshold) if context_limit and type(threshold) in (int, float)
                     and 0 < threshold <= 1 else None)
        provider_defaults = provider_defaults or {}
        same_model = row['model'] == (provider_defaults.get('model') or provider_defaults.get('default_model'))
        window = (budget.get('context_window_tokens') if budget.get('context_window_tokens') is not None else
                  number(provider_defaults.get('context_window')) if same_model else None)
        window = window if type(window) is int and window > 0 else None
        reserve = budget.get('max_output_tokens')
        if reserve is None:
            reserve = number(getattr(request, 'max_output_tokens', None))
        header = {key: row.get(key) for key in ('sessionId', 'provider', 'model', 'startedAt', 'phase')}
        header.update(callId=row['id'], usage={}, limits={
            'modelWindow': window,
            'requestInputAllowance': budget.get('input_limit_tokens'),
            'reservedOutput': reserve,
            'configuredContextBudget': context_limit, 'compactionThreshold': threshold},
            budget=budget, bodyRecording=enabled and bodies and config['bodies'])
        # Build synchronously from the actual admitted object, before any await
        # can permit another task to mutate a shared request. Storage alone runs
        # off-thread. Only bounded text and schema fields are projected.
        manifest, texts = projection(request, bodies=header['bodyRecording']) if enabled else ({
            'categories': [], 'items': [], 'estimatedTextTokens': None, 'coverage': 'unavailable',
            'reason': 'Diagnostic capture is disabled or this provider explicitly opted out.'}, {})
        # No new await between the final guard and provider entry. Storage runs
        # alongside the call; finish drains this exact write before its outcome.
        # A crash before persistence honestly leaves no retained observation.
        task = asyncio.create_task(asyncio.to_thread(self.store.begin, header, manifest, texts, days=config['days']))
        self.pending[row['id']] = task
        def consume_failure(done):
            if not done.cancelled():
                done.exception()
        task.add_done_callback(consume_failure)

    def finish(self, row):
        task = self.pending.get(row['id'])
        if task is None:
            return
        async def settle():
            try:
                await asyncio.shield(task)
                await asyncio.to_thread(self.store.finish, row['id'], row['phase'], row.get('usage', {}))
            finally:
                self.pending.pop(row['id'], None)
        settled = asyncio.create_task(settle())
        self.pending[row['id']] = settled
        settled.add_done_callback(lambda done: None if done.cancelled() else done.exception())

    async def drain(self):
        if self.pending:
            await asyncio.gather(*(asyncio.shield(task) for task in tuple(self.pending.values())), return_exceptions=True)


async def safe_observe(observer, method, *args):
    """Diagnostic failure never changes provider execution or cancellation."""
    if observer is None:
        return
    try:
        result = getattr(observer, method)(*args)
        if inspect.isawaitable(result):
            await result
    except Exception:
        # Existing call identity/usage remains authoritative and inspectable.
        # No request or exception payload is logged here.
        return
