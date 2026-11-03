"""Content-free reasons at the local, before-transport fitting boundary."""
import json

COMPONENTS = ('messages', 'tools', 'tool_choice', 'model', 'max_output_tokens',
              'temperature', 'response_format', 'metadata')
CAUSES = ('bridge_timeout', 'bridge_error', 'invalid_response', 'required_unavailable')
REASONS = ('binding_missing', 'binding_changed', 'content_changed',
           'authority_changed', 'authority_unavailable', 'native_scope_changed')


def public_refusal(value):
    """Only the adapter's bounded, local refusal facts may cross diagnostics."""
    if not isinstance(value, dict) or value.get('reason') not in REASONS:
        return None
    components = value.get('components', [])
    if (not isinstance(components, list) or len(components) > len(COMPONENTS) + 2
            or any(name not in (*COMPONENTS, 'other', 'unavailable') for name in components)
            or value.get('cause') not in (None, *CAUSES)):
        return None
    return {'reason': value['reason'], 'components': components,
            **({'cause': value['cause']} if value.get('cause') else {})}


class RequestFitChanged(ValueError):
    """A local refusal, not evidence that a provider request was sent."""

    def __init__(self, reason, *, components=(), cause=None):
        if reason not in REASONS:
            raise ValueError('Unknown request fitting reason')
        if any(name not in (*COMPONENTS, 'other', 'unavailable') for name in components):
            raise ValueError('Unknown request fitting component')
        if cause is not None and cause not in CAUSES:
            raise ValueError('Unknown request fitting cause')
        self.reason = reason
        self.components = tuple(components)
        self.cause = cause
        # Only the adapter can confirm this particular error preceded transport.
        # A provider raising the same exception after sending cannot claim it.
        self.local_refusal = False
        detail = f" ({', '.join(self.components)})" if self.components else ''
        if cause:
            detail += f' [{cause}]'
        super().__init__(f'Work request refused before transport: {reason}{detail}; refit before transport.')


def changed_components(before, after):
    """Compare only on refusal; never retain or log prompt/image contents."""
    try:
        left, right = json.loads(before), json.loads(after)
        names = COMPONENTS
        changed = [name for name in names if left.get(name) != right.get(name)]
        if any(left.get(name) != right.get(name) for name in left.keys() | right.keys()
               if name not in names):
            changed.append('other')
        return tuple(changed)
    except (TypeError, ValueError, AttributeError):
        return ('unavailable',)
