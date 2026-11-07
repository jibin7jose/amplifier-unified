"""Pure decision metadata checks and isolated provider-catalog discovery."""
import contextlib
from importlib.metadata import entry_points
import json
import os
import re
import sys


def backend_metadata(value):
    """Read the shared declaration shape without executing its validator."""
    if value.get("schema_version") != 2 or not callable(value.get("validate")):
        return None
    module, rows = value.get("provider_module"), value.get("backends", [])
    if not isinstance(module, str) or len(rows) != 1:
        return None
    return module, rows[0], value["validate"]


def runtime_metadata(value):
    """Return a declared hook and exact false condition, without mounting it."""
    runtime = value.get("runtime") or {}
    conditional = (runtime.get("when") == {"chat_enabled": False}
                   and runtime["when"]["chat_enabled"] is False)
    hook = runtime.get("module")
    valid = (set(runtime) == ({"kind", "module", "when"} if conditional else {"kind", "module"})
             and runtime.get("kind") == "hook"
             and isinstance(hook, str) and re.fullmatch(r"hooks-[a-z0-9-]+", hook))
    return hook if valid else None, conditional


def discover():
    result = {}
    for entry in list(entry_points(group="amplifier.decision_backends"))[:1024]:
        try:
            value = entry.load()()
            metadata = backend_metadata(value)
            if metadata is None:
                continue
            module, row, _ = metadata
            hook, conditional = runtime_metadata(value)
            if (not re.fullmatch(r"provider-[a-z0-9-]{1,160}", module)
                    or (row.get("decision_only") is not True and not conditional)):
                continue
            # Runtime mounting retains its bounded names and ambiguity refusal.
            if hook and len(hook.removeprefix("hooks-")) > 160:
                hook = None
            if module in result:
                result[module] = None
            else:
                result[module] = ({"module": hook, "when": {"chat_enabled": False}} if conditional
                                  else hook) if hook else None
        except Exception:
            continue
    return result


if __name__ == "__main__":
    # Imported metadata must not contaminate the protocol with logs or make a
    # network request. The parent supplies neither account configuration nor keys.
    def offline(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            raise RuntimeError("Decision catalog discovery cannot access the network")
    sys.addaudithook(offline)
    with open(os.devnull, "w") as silent, contextlib.redirect_stdout(silent), contextlib.redirect_stderr(silent):
        result = discover()
    sys.stdout.write(json.dumps(result, separators=(",", ":")))
