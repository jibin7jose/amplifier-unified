"""Execution validation for host-prepared computer images, not budget evidence."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar

_CURRENT = ContextVar('unified_prepared_projection', default=None)


@contextmanager
def dispatch(provider, request, fitted, validator, options):
    from .provider_capture import normalized_options
    scope = None
    if validator is not None:
        scope = {'provider': provider, 'request': request, 'task': asyncio.current_task(),
                 'fitted': fitted, 'validator': validator,
                 'options': normalized_options(options), 'claimed': False,
                 'active': True, 'encoded': None}
    token = _CURRENT.set(scope)
    try:
        yield
    finally:
        if scope is not None:
            scope['active'] = False
            for key in ('provider', 'request', 'task', 'fitted', 'validator', 'options', 'encoded'):
                scope[key] = None
        _CURRENT.reset(token)


def claim(execution, provider, request):
    from .provider_capture import matches_provider
    scope = _CURRENT.get()
    if scope is None or not scope['active']:
        return None
    if scope['task'] is not asyncio.current_task():
        raise ValueError('Prepared screenshot crossed an execution task')
    if (scope['claimed'] or scope['request'] is not request
            or not matches_provider(execution, scope['provider'], provider)):
        raise ValueError('Prepared screenshot dispatch identity changed')
    scope['claimed'] = True
    return scope


def release(scope):
    if scope is not None:
        scope['claimed'] = False
        scope['encoded'] = None


def validate(scope, request, options):
    """Synchronous after admission; failures must not be swallowed as diagnostics."""
    if scope is None:
        return
    if not isinstance(scope['fitted'], str):
        raise ValueError('Prepared screenshot fitting boundary is unavailable')
    from .provider_capture import normalized_options
    if (not scope['active'] or scope['task'] is not asyncio.current_task()
            or scope['request'] is not request or scope['options'] is None
            or normalized_options(options) != scope['options']):
        raise ValueError('Prepared screenshot dispatch options changed')
    scope['validator']()
    encoded = request.model_dump_json()
    if encoded != scope['fitted']:
        raise ValueError('Prepared screenshot request changed after fitting')
    # The optional numeric observer can reuse this exact complete encoding.
    scope['encoded'] = encoded


def validated_encoding(request):
    scope = _CURRENT.get()
    if (scope is not None and scope['active'] and scope['task'] is asyncio.current_task()
            and scope['request'] is request):
        return scope['encoded']
    return None
