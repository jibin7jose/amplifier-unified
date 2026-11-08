"""Public byte representations of immutable saved output bodies."""
import base64
import json


CANVAS_JSON = 'canvas-json-v1'
CANVAS_IMAGE = 'canvas-image-v1'


def canvas_snapshot_bytes(body):
    """Preserve the original Canvas download encoding, not resource encoding."""
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode()


def body_bytes(body, body_format=None):
    """Decode one saved resource without changing its historical representation."""
    if body_format == CANVAS_JSON:
        return canvas_snapshot_bytes(body)
    if body_format == CANVAS_IMAGE:
        prefix = 'data:image/png;base64,'
        content = body.get('content')
        if not isinstance(content, str) or not content.startswith(prefix):
            raise ValueError('Saved image source is unavailable.')
        return base64.b64decode(content[len(prefix):], validate=True)
    return base64.b64decode(body['data'], validate=True) if body['encoding'] == 'base64' else body['data'].encode()
