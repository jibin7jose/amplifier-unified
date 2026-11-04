"""Metadata-only subprocess entrypoint; never imports provider code in a worker."""
import contextlib
from importlib.metadata import entry_points
import json
import os
import re
import sys


def discover():
    result = {}
    for entry in list(entry_points(group="amplifier.decision_backends"))[:1024]:
        try:
            value = entry.load()()
            rows = value.get("backends", [])
            module = value.get("provider_module")
            runtime = value.get("runtime") or {}
            conditional = (runtime.get("when") == {"chat_enabled": False}
                           and runtime["when"]["chat_enabled"] is False)
            if (value.get("schema_version") != 2 or not callable(value.get("validate"))
                    or not isinstance(module, str) or not re.fullmatch(r"provider-[a-z0-9-]{1,160}", module)
                    or len(rows) != 1 or (rows[0].get("decision_only") is not True and not conditional)):
                continue
            hook = runtime.get("module")
            valid = (set(runtime) == ({"kind", "module", "when"} if conditional else {"kind", "module"})
                     and runtime.get("kind") == "hook"
                     and isinstance(hook, str) and re.fullmatch(r"hooks-[a-z0-9-]{1,160}", hook))
            if module in result:
                result[module] = None
            else:
                result[module] = ({"module": hook, "when": {"chat_enabled": False}} if conditional
                                  else hook) if valid else None
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
