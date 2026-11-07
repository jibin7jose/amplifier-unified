#!/usr/bin/env python3
"""Bounded views and compare-and-swap updates for a development workspace manifest.

This tool never creates, stops or deletes an application, checkout or resource.
It is a migration aid for the workspace ledger, not product storage authority.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


class ManifestError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ManifestError(f"Invalid JSON constant: {value}")


def decode(raw: bytes):
    return json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def record_digest(manifest: dict, key: str) -> str:
    value = {"exists": key in manifest, "value": manifest.get(key)}
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())


def load(path: Path):
    raw = path.read_bytes()
    value = decode(raw)
    if not isinstance(value, dict):
        raise ManifestError("Manifest must be a JSON object")
    return raw, value


def brief(value, limit: int):
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return {"text": text[:limit], "truncated": len(text) > limit}


def summary(raw: bytes, manifest: dict, *, query="", limit=10, offset=0):
    """Project metadata before serialization; never emit nested record bodies."""
    keys = sorted(key for key in manifest if query.casefold() in key.casefold())
    rows = []
    for key in keys[offset:offset + limit]:
        value = manifest[key]
        row = {"key": key, "type": type(value).__name__}
        if isinstance(value, dict):
            for field, size in (("owner", 64), ("status", 96)):
                snippet = brief(value.get(field), size)
                if snippet is not None:
                    row[field] = snippet
        rows.append(row)
    end = offset + len(rows)
    return {
        "manifestSha256": digest(raw), "manifestBytes": len(raw),
        "totalRecords": len(manifest), "matchedRecords": len(keys),
        "offset": offset, "nextOffset": end if end < len(keys) else None,
        "records": rows,
        "coverage": "Declared metadata only; status text is not verified resource state or deletion authority.",
    }


def select(value, pointer: str):
    if not pointer:
        return value
    if not pointer.startswith("/"):
        raise ManifestError("Field must be a JSON Pointer, for example /development/dtu")
    for part in pointer[1:].split("/"):
        # Reject malformed escapes rather than silently choosing a different key.
        i = 0
        while i < len(part):
            if part[i] == "~":
                if i + 1 == len(part) or part[i + 1] not in "01":
                    raise ManifestError("Invalid JSON Pointer escape")
                i += 1
            i += 1
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if part not in value:
                raise ManifestError(f"No field: {part}")
            value = value[part]
        elif isinstance(value, list) and part.isascii() and part.isdigit() and (part == "0" or not part.startswith("0")):
            index = int(part)
            if index >= len(value):
                raise ManifestError(f"No array entry: {part}")
            value = value[index]
        else:
            raise ManifestError(f"Cannot select field: {part}")
    return value


@contextmanager
def locked(path: Path):
    # This is the existing workspace-wide cooperative lock, not a second lock.
    lock_path = path.parent / ".workspace-manifest.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def update(path: Path, key: str, value, expected: str):
    """Replace one explicitly selected record without dropping concurrent edits."""
    with locked(path):
        before, manifest = load(path)
        if record_digest(manifest, key) != expected:
            raise ManifestError("Record changed; reread it and reconcile before retrying with its current recordSha256")
        identity = path.lstat()
        if not stat.S_ISREG(identity.st_mode):
            raise ManifestError("Manifest must be a regular file, not a symbolic link")
        if key in manifest and record_digest({key: value}, key) == expected:
            return {"changed": False, "key": key, "recordSha256": expected, "manifestSha256": digest(before)}
        manifest[key] = value
        after = (json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
        fd, name = tempfile.mkstemp(prefix=".workspace-manifest-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(after)
                stream.flush()
                os.fchmod(stream.fileno(), stat.S_IMODE(identity.st_mode))
                os.fsync(stream.fileno())
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino) or path.read_bytes() != before:
                raise ManifestError("Uncoordinated writer changed the manifest; update was not applied")
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return {"changed": True, "key": key, "recordSha256": record_digest(manifest, key),
                "manifestSha256": digest(after), "manifestBytes": len(after)}


def natural(value):
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("Must be non-negative")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Exact development workspace manifest; never inferred from another checkout")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="Bounded current-record discovery without nested evidence")
    listing.add_argument("--filter", default="", help="Case-insensitive substring of the record key")
    listing.add_argument("--limit", type=natural, default=10)
    listing.add_argument("--offset", type=natural, default=0)
    reading = commands.add_parser("get", help="Read one selected record or field")
    reading.add_argument("key")
    reading.add_argument("--field", default="", help="JSON Pointer within the selected record")
    writing = commands.add_parser("put", help="Replace one record with a structured value; no resource effects")
    writing.add_argument("key")
    writing.add_argument("--value-file", type=Path, required=True)
    writing.add_argument("--expect", required=True, help="recordSha256 returned by get; unrelated record changes do not conflict")
    args = parser.parse_args(argv)
    try:
        if args.command == "put":
            result = update(args.manifest, args.key, decode(args.value_file.read_bytes()), args.expect)
        else:
            raw, manifest = load(args.manifest)
            if args.command == "list":
                result = summary(raw, manifest, query=args.filter, limit=args.limit, offset=args.offset)
            else:
                if args.key not in manifest and args.field:
                    raise ManifestError(f"No record: {args.key}")
                result = {"manifestSha256": digest(raw), "key": args.key, "field": args.field,
                          "exists": args.key in manifest, "recordSha256": record_digest(manifest, args.key),
                          "value": select(manifest.get(args.key), args.field)}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ManifestError, OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
