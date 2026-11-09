"""Bounded inventory reuse; installation always repeats source preflight.

Status reads never build a runtime. A generation/configuration change or newly
materialized source invalidates the index. Periodic reconciliation discovers
out-of-band edits; cached availability is never permission to overwrite them.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

RECONCILE_SECONDS = 4 * 3600


def signature(manager):
    from . import __version__
    from .session_files import amplifier_home
    from .updates import active_release, foundation_home
    from .update_sequence import app_selection

    home = Path(manager.home)
    base = foundation_home(home)
    paths = [
        home / "config",
        amplifier_home() / "settings.yaml",
        home / "config/settings.yaml",
        base / "registry.json",
        base / "cache",
        base / "cache/.source-bindings",
        Path(__file__).with_name("runtime_deps") / "pyproject.toml",
    ]
    stamps = []
    for path in paths:
        try:
            value = path.stat()
            stamps.append((str(path), value.st_mtime_ns, value.st_size))
        except FileNotFoundError:
            stamps.append((str(path), None))
    # Connection/settings edits publish their own revision without reading keys.
    selection = manager.service.state.get("settings", {})
    smart = getattr(manager.service, "smart_tools", None)
    tools = smart.update_sources() if smart else []
    return hashlib.sha256(
        json.dumps(
            [
                __version__,
                app_selection(home)['witness'],
                active_release(home),
                stamps,
                tools,
                selection.get("bundle"),
                selection.get("workspace"),
                selection.get("revision"),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()


async def inventory(manager, scan):
    from .host.config import write_private

    path = manager.directory / "inventory-index.json"
    key = signature(manager)
    now = time.time()
    try:
        saved = json.loads(path.read_text())
        if (
            saved.get("schema") == 1
            and saved["signature"] == key
            and 0 <= now - saved["scannedAt"] < RECONCILE_SECONDS
        ):
            rows = saved["rows"]
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) for row in rows
            ):
                raise ValueError("Invalid component index")
            manager.inventory_evidence = {
                "source": "index",
                "scannedAt": saved["scannedAt"],
            }
            return rows
    except (OSError, ValueError, KeyError, TypeError):
        pass
    rows = await scan()
    # A concurrent registry edit must not be blessed with the new signature.
    if signature(manager) == key:
        write_private(
            path,
            json.dumps({"schema": 1, "signature": key, "scannedAt": now, "rows": rows}),
        )
    manager.inventory_evidence = {"source": "reconciliation", "scannedAt": now}
    return rows
