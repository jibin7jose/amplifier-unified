"""One observation per request, including its single pretransport refit."""
import asyncio
from contextvars import ContextVar

OPERATIONS = ('context.manifest', 'message.reactions.context', 'coordination.current', 'memory.context')
# Preserve the existing per-section read deadline on the host. Allow one more
# read interval for transport so its response can retain the completed sections.
SECTION_TIMEOUT = 1.5
TRANSPORT_TIMEOUT = SECTION_TIMEOUT * 2


class PreparationContext:
    def __init__(self, bridge):
        self.bridge = bridge
        self.current = ContextVar('unified_preparation_context', default=None)

    async def read(self, operation, args):
        current = self.current.get()
        if current is not None and operation in OPERATIONS and not args:
            item = current.get(operation, {})
            if item.get('available') is not True:
                raise ValueError('Current preparation context is unavailable')
            return item['value']
        return await self.bridge(operation, args)

    def wrap(self, delivery):
        owner = self

        class Delivery:
            def __getattr__(self, name):
                return getattr(delivery, name)

            async def _with_context(self, request, provider, values, *, commit=False):
                token = owner.current.set(values)
                try:
                    result = await delivery.prepare(request, provider, commit=commit)
                    work_request = getattr(delivery, 'work_request', None)
                    binding = work_request(result) if callable(work_request) else None
                    if binding is not None:
                        # Private, bounded by the existing request/refusal records.
                        # Never exported as authority or cached across requests.
                        binding['preparationContext'] = values
                    return result
                finally:
                    owner.current.reset(token)

            async def prepare_refit(self, request, provider, *, binding):
                values = binding.get('preparationContext')
                if not isinstance(values, dict) or binding['required']['status'] != 'available':
                    return await self.prepare(request, provider)
                # Finalization applies the same current policy as an ordinary
                # request: strict Work assurance in Ask; standing authority in
                # Full. Both retain native input/generation/Stop checks.
                return await self._with_context(request, provider, values)

            async def prepare(self, request, provider, *, commit=False):
                from .execution_events import CALL_PURPOSE
                if CALL_PURPOSE.get() or not any(getattr(t, 'name', None) == 'app_control'
                                                for t in request.tools or []):
                    return await delivery.prepare(request, provider, commit=commit)
                try:
                    values = await asyncio.wait_for(owner.bridge('context.prepare', {}), TRANSPORT_TIMEOUT)
                    if not isinstance(values, dict):
                        raise ValueError('Invalid preparation context')
                except Exception:
                    values = {}
                return await self._with_context(request, provider, values, commit=commit)

        return Delivery()
