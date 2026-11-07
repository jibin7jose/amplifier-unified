"""Public byte representation of an immutable saved Canvas body."""
import json


CANVAS_JSON = 'canvas-json-v1'


def canvas_snapshot_bytes(body):
    """Preserve the original Canvas download encoding, not resource encoding."""
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
