"""Bounded JSON-lines transport, shared by both ends of the worker pipe."""
import json

# A 32 MiB attachment expands to roughly 45 MB in a tool's base64 action.
# Both ends of the agent bridge must accept it, just as the HTTP action does.
MAX_MESSAGE_BYTES = 48_000_000

# Existing worker controls that bind an input identity before submitting work.
# Keep admission and the worker's input-context binding on the same protocol set.
INPUT_CONTROLS = frozenset({
    "conversation.steer", "schedule.submit", "observation.submit",
    "coordination.submit", "coordination.steer", "coordination.deliver", "history.edit",
})


def encode_message(data):
    encoded = (json.dumps(data, ensure_ascii=False, default=str) + '\n').encode('utf-8')
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError('Runtime message exceeds the 48 MB transport limit. Request a smaller state page.')
    return encoded
