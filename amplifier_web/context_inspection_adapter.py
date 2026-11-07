"""Optional native observation attached to the existing provider lifecycle owner."""
from __future__ import annotations

import asyncio
import inspect
import weakref

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

    def budget(self, request, options, decision, provider=None):
        signature = request_fingerprint(request, options)
        # Retain a bounded absence receipt too. None may mean unsupported input,
        # and a bounded fingerprint refusal is not absence of provider support.
        try:
            reference = weakref.ref(request)
        except TypeError:
            reference = lambda: None  # Exact value matching can still qualify.
        self.budgets = (self.budgets + [(reference, signature, budget_public(decision),
                                       provider, weakref.ref(asyncio.current_task()))])[-4:]

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
        task = asyncio.current_task()
        # Final revalidation may make an unchanged ChatRequest copy. The exact
        # bounded fingerprint includes all fields/options, not merely its text.
        # Never borrow a receipt from another provider or concurrent task.
        scoped = [v for v in self.budgets if v[3] is provider and v[4]() is task]
        exact = [v for v in scoped if v[0]() is request]
        copied = [v for v in scoped if signature and v[1] == signature]
        # Two identical candidates have no unique causal witness. Do not choose
        # by arrival order; retaining fewer numbers is better than false evidence.
        entry = exact[-1] if exact else copied[0] if len(copied) == 1 else None
        observed = (entry[1], entry[2]) if entry else None
        matched = bool(observed and signature and observed[0] == signature)
        budget = observed[1] if matched else {}
        budget_status = ('captured' if budget else 'not_reported' if matched else
                         'binding_bounded' if observed and (not signature or not observed[0]) else
                         'request_changed' if observed else 'ambiguous' if len(copied) > 1 else
                         'unsupported' if not callable(getattr(provider, 'request_budget', None)) else 'not_captured')
        # Any dispatch ends this provider/task's preflight interval. A later
        # identical call must not inherit a receipt left by an earlier attempt.
        self.budgets = [v for v in self.budgets if v[4]() is not None and not (v[3] is provider and v[4]() is task)]
        context = self.coordinator.get('context')
        context_config = getattr(context, 'config', {}) or {}
        context_limit = number(getattr(context, 'max_tokens', None))
        if context_limit is None and isinstance(context_config, dict):
            context_limit = number(context_config.get('max_tokens'))
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
            reserve = number((options or {}).get('max_output_tokens'))
        if reserve is None:
            reserve = number(getattr(request, 'max_output_tokens', None))
        missing_budget = {
            'captured': 'The provider budget decision did not report this field.',
            'not_reported': 'The provider returned no usable budget decision for this input.',
            'binding_bounded': 'The request exceeded bounded attribution or contained opaque values; its preflight receipt could not be bound safely.',
            'request_changed': 'The request or options changed after preflight; that count is not evidence for this captured request.',
            'not_captured': 'No matching preflight budget decision was retained for this request.',
            'unsupported': 'This mounted provider does not support preflight budget decisions.',
            'ambiguous': 'More than one identical preflight candidate was retained; no unique request association was proven.',
        }[budget_status]
        def evidence(value, source, reason):
            return {'status': 'captured' if value is not None else 'unavailable',
                    'source': source if value is not None else None, 'reason': reason}
        limit_evidence = {
            'modelWindow': evidence(window, 'provider_budget' if budget.get('context_window_tokens') else 'provider_defaults',
                'Provider metadata captured for this exact model.' if window is not None else
                'Provider defaults describe a different model; no exact-model window was reported by preflight.'
                if not same_model else 'The provider did not report a model window.'),
            'requestInputAllowance': evidence(budget.get('input_limit_tokens'), 'provider_budget', missing_budget if budget.get('input_limit_tokens') is None else 'Captured provider input allowance; not the model context window.'),
            'reservedOutput': evidence(reserve, 'provider_budget' if budget.get('max_output_tokens') is not None else 'request',
                'Captured output reservation.' if reserve is not None else 'No output reservation was reported by preflight or set on the host request. A model output maximum is not a reservation.'),
            'configuredContextBudget': evidence(context_limit, 'mounted_context',
                'Mounted context configuration at capture.' if context_limit is not None else 'No fixed context budget exposed by the mounted context. Automatic policy is not a configured token limit.'),
            'compactionThreshold': evidence(threshold, 'mounted_context',
                'Configured budget × configured summarize trigger at capture.' if threshold is not None else 'No fixed compaction threshold exposed at capture; context policy may be automatic.'),
            'providerCount': evidence(budget.get('measurement', {}).get('input_tokens'), 'provider_count',
                'Existing preflight count, separate from final reported usage.' if budget.get('measurement') else missing_budget if not budget else 'The provider decision did not contain a provider-count receipt.'),
            'providerEstimate': evidence(budget.get('estimated_input_tokens'), 'provider_budget',
                'Existing provider fit decision, not the local text heuristic.' if budget.get('estimated_input_tokens') is not None else missing_budget),
        }
        header = {key: row.get(key) for key in ('sessionId', 'provider', 'model', 'startedAt', 'phase')}
        header.update(callId=row['id'], usage={}, limits={
            'modelWindow': window,
            'requestInputAllowance': budget.get('input_limit_tokens'),
            'reservedOutput': reserve,
            'configuredContextBudget': context_limit, 'compactionThreshold': threshold},
            budget=budget, budgetStatus=budget_status, limitEvidence=limit_evidence,
            bodyRecording=enabled and bodies and config['bodies'])
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
