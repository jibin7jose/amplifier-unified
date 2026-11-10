"""Shared-source adoption happens only inside the unpublished generation."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from .staged_sources import owned_git_checkout, owned_path, retarget_registry


def store_environment(home):
    # Feature detection keeps older Foundation installations functional during
    # the app-first transition. The component release enables shared storage.
    try:
        from amplifier_foundation.sources.shared import SharedSourceStore  # noqa: F401
    except ImportError:
        return {}
    return {
        "AMPLIFIER_SOURCE_STORE": os.environ.get(
            "AMPLIFIER_SOURCE_STORE", str(Path(home).resolve() / "source-store")
        ),
        # Importing modules must not mutate commit-addressed source objects.
        # Qualification and live workers share this policy; keep verification
        # strict rather than accepting generated files in immutable checkouts.
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def worker_supports_shared(project):
    """Feature detection uses the candidate interpreter's installed Foundation."""
    environment = Path(project) / ".venv"
    return (
        any(
            environment.glob(
                "lib/python*/site-packages/amplifier_foundation/sources/shared.py"
            )
        )
        or (
            environment / "Lib/site-packages/amplifier_foundation/sources/shared.py"
        ).is_file()
    )


async def bindings(home, base, configured):
    from .updates import pinned, safe_label, source_key

    environment = store_environment(home)
    if not environment:
        return []
    from amplifier_foundation.sources.shared import SharedSourceStore

    store = SharedSourceStore(environment["AMPLIFIER_SOURCE_STORE"])
    semaphore = asyncio.Semaphore(8)

    async def inspect(path):
        async with semaphore:
            return await inspect_one(path)

    async def inspect_one(path):
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
                raise ValueError("External shared source binding")
            data = json.loads(path.read_text())
            url, ref, revision = data["git_url"], data["ref"], data["commit"]
            await asyncio.to_thread(store.verify, url, revision)
            evidence = sorted(configured.get(source_key(url, ref), ()))
            return {
                "id": "binding:" + path.stem,
                "label": safe_label(url),
                "kind": "bundle / module",
                "path": str(path.relative_to(base)),
                "url": url,
                "ref": ref,
                "current": revision,
                "sharedSource": True,
                "status": "pinned" if pinned(ref) else "not_checked",
                "eligible": not pinned(ref),
                "usage": "configured" if evidence else "unknown",
                "usageEvidence": evidence,
            }
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            subprocess.SubprocessError,
        ):
            return {
                "id": "binding:" + path.stem,
                "label": "Unverified shared source",
                "kind": "source",
                "status": "check_failed",
                "eligible": False,
                "usage": "unknown",
                "usageEvidence": [],
            }

    paths = sorted((base / "cache/.source-bindings").glob("*.json"))
    return await asyncio.gather(*(inspect(path) for path in paths))


async def stage_binding(manager, stage, row):
    from amplifier_foundation.sources.shared import SharedSourceStore

    from .updates import source_key

    store = SharedSourceStore(store_environment(manager.home)["AMPLIFIER_SOURCE_STORE"])
    path = stage / "foundation" / row["path"]
    if path.is_symlink() or not path.resolve().is_relative_to(
        (stage / "foundation").resolve()
    ):
        raise ValueError("Invalid shared source binding")
    record = json.loads(path.read_text())
    if (
        source_key(record["git_url"], record["ref"])
        != source_key(row["url"], row["ref"])
        or record["commit"] != row["current"]
    ):
        raise ValueError("Shared source changed after checking")
    await asyncio.to_thread(store.verify, row["url"], row["current"])
    target = await store.bind(
        stage / "foundation/cache", row["url"], row["ref"], row["latest"]
    )
    retarget_registry(stage / "foundation/registry.json", row["url"], row["ref"],
                      store.checkout(row["url"], row["current"]), target)


def _remove_adopted_checkout(root, base):
    """Dispose only the already-adopted staged copy, including read-only Git objects."""
    root = owned_path(root, base)

    def retry_readonly_object(function, path, error):
        if sys.platform != "win32" or function is not os.unlink or not isinstance(error, PermissionError):
            raise error
        path = owned_path(Path(path), root)
        relative = path.relative_to(root)
        info = path.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        if (relative.parts[:2] != (".git", "objects") or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1 or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or not attributes & stat.FILE_ATTRIBUTE_READONLY):
            raise error
        # Git marks object files read-only on Windows. The exclusive regular
        # leaf belongs to this disposable staged copy, not a shared hardlink.
        # Never repair ACLs, writable-file failures, directories or link targets.
        os.chmod(path, stat.S_IWRITE, follow_symlinks=False)
        function(path)  # A genuine second failure remains a failed preparation.

    shutil.rmtree(root, onexc=retry_readonly_object)


async def adopt_clean_sources(manager, stage, *, selection=None):
    """Legacy checkouts remain intact in the serving and rollback generations.

    Only clean, owned staged copies enter the shared store. Unknown usage is
    not a deletion signal. Dirty/local sources stay as ordinary writable copies.
    """
    from .updates import process
    from .runtime_source_alignment import protected_selection

    environment = store_environment(manager.home)
    if not environment:
        return
    from amplifier_foundation.sources.shared import SharedSourceStore

    store = SharedSourceStore(environment["AMPLIFIER_SOURCE_STORE"])
    base = stage / "foundation"
    roots = sorted(
        (base / "cache").rglob(".amplifier_cache_meta.json"),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    # Preflight every copy before changing any binding: one dirty/conflicting
    # copy protects the entire repository/ref identity from automatic adoption.
    from amplifier_foundation.sources.shared import clean_checkout, source_id

    candidates, protected, revisions = [], set(), {}
    for meta in roots:
        root = meta.parent
        if protected_selection(root, selection):
            continue  # Effective local choices need this exact candidate path.
        key = None
        try:
            owned_path(meta, base)
            data = json.loads(meta.read_text())
            if not isinstance(data, dict):
                continue
            url, ref = data["git_url"], data.get("ref") or "HEAD"
            key = source_id(url, ref)
            if root.is_symlink() or not root.resolve().is_relative_to(base.resolve()):
                raise ValueError("External checkout")
            await owned_git_checkout(root, base)
            if not await asyncio.to_thread(clean_checkout, root):
                raise ValueError("Local work retained")
            revision = await process("git", "rev-parse", "HEAD", cwd=root, timeout=10)
            if revision != data.get("commit"):
                raise ValueError("Source metadata changed")
            revisions.setdefault(key, set()).add(revision)
            candidates.append((key, root, url, ref, revision))
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            TimeoutError,
            subprocess.SubprocessError,
        ):
            if key:
                protected.add(key)
    for key, root, url, ref, revision in candidates:
        if key in protected or len(revisions[key]) != 1 or not root.exists():
            continue
        await owned_git_checkout(root, base)
        target = await store.bind(base / "cache", url, ref, revision, existing=root)
        retained = retarget_registry(base / "registry.json", url, ref, root, target)
        # A local/pinned registration can share this cache path. Its authored
        # choice still needs the staged copy, even when a moving binding adopts.
        if not retained:
            await asyncio.to_thread(_remove_adopted_checkout, root, base)
