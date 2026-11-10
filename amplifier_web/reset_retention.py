"""Standard-library-only reset recovery ownership and keep-one retention.

The per-data-directory lease is distinct from the host's shared repair lease.
Lock order: retention -> tool-recovery repair -> publishing -> runtime -> service.
CLI releases the live data repair lease before starting the replacement host.
Cleanup holds only retention and (for external members) tool-recovery leases.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

VERSION = 1
MARKER = ".reset-owner.json"


def canonical(home):
    return Path(home).expanduser().resolve()


def identity(home):
    return hashlib.sha256(str(canonical(home)).encode()).hexdigest()


def _no_links(path):
    """Reject substituted ancestors, but allow a final member to be a link."""
    path = Path(path)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f"Linked recovery ancestor: {parent}")
    return path


def _stamp(path):
    info = path.lstat()
    return {"device": info.st_dev, "inode": info.st_ino, "mode": stat.S_IFMT(info.st_mode),
            **({"bytes": info.st_size} if stat.S_ISREG(info.st_mode) else {})}


def _ancestors(path):
    return {str(parent): _stamp(parent) for parent in (path, *path.parents)}


def _check_ancestors(expected):
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Missing recovery structural identities")
    for name, stamp in expected.items():
        if _stamp(Path(name)) != stamp:
            raise ValueError("Recovery structural root was substituted")


def _assert_unused(paths):
    """Recovery namespaces are not runtime launch targets, including old hosts."""
    for path in paths:
        for current in (Path(sys.prefix).resolve(), Path(sys.executable).resolve()):
            if current == path or current.is_relative_to(path):
                raise ValueError("Recovery member is used by the current installation")
    listing = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True,
                             text=True, check=True, timeout=30).stdout
    for line in listing.splitlines():
        if any(str(path) + "/" in line for path in paths):
            raise ValueError("Recovery member is still used by a process; stop that copy before cleanup")


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, value):
    _no_links(path.parent)
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read(path):
    _no_links(path)
    with path.open() as stream:
        return json.load(stream)


class Retention:
    """An exclusive process lease, held until completion/cleanup actually settles."""
    def __init__(self, home):
        self.home = canonical(home)
        self.root = self.home / "reset-recovery"
        self.stream = None

    def acquire(self):
        _no_links(self.root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        self.stream = os.fdopen(fd, "a")
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise ValueError("The retention lock is not a private regular file")
            # Resets are supported on POSIX only, including the in-app destructive
            # cleanup route. Do not pretend a threading lock serializes processes.
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.load()
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None

    def load(self):
        path = self.root / "state.json"
        self.state = _read(path) if path.exists() or path.is_symlink() else {
            "version": VERSION, "dataDir": str(self.home), "dataIdentity": identity(self.home),
            "sequence": 0, "operations": {}, "ancestors": _ancestors(self.root),
        }
        if (not isinstance(self.state, dict) or self.state.get("version") != VERSION or self.state.get("dataDir") != str(self.home)
                or self.state.get("dataIdentity") != identity(self.home)
                or type(self.state.get("sequence")) is not int or self.state["sequence"] < 0
                or not isinstance(self.state.get("operations"), dict)
                or any(not isinstance(row, dict) for row in self.state["operations"].values())):
            raise ValueError("Unrecognized reset recovery state; retained for explicit review")
        _check_ancestors(self.state["ancestors"])
        for operation, row in self.state["operations"].items():
            if (not re.fullmatch(r"[a-f0-9]{32}", operation)
                    or row.get("state") not in {"pending", "unresolved", "successful"}
                    or row.get("cleanup") not in {"pending", "retained", "removing", "removed", "incomplete"}
                    or not isinstance(row.get("members"), list)
                    or any(not isinstance(member, dict) or type(member.get("removed")) is not bool
                           or type(member.get("deleting", False)) is not bool for member in row["members"])):
                raise ValueError("Malformed reset lifecycle; no recovery files removed")
        return self.state

    def save(self):
        if self.stream is None:
            raise RuntimeError("Reset retention requires its process lease")
        _write(self.root / "state.json", self.state)

    def begin(self, origin, *, tool_root=None):
        if origin not in {"cli", "in-app"}:
            raise ValueError("Unrecognized reset entry point")
        operation = uuid.uuid4().hex
        row = {"id": operation, "origin": origin, "state": "pending", "successOrder": None,
               "members": [], "cleanup": "pending", "errors": []}
        if origin == "cli":
            tool_root = canonical(tool_root)
            # Enrolled by CLI's uv-discovered installation path, separately from
            # operation manifests. A forged row cannot grant a new external root.
            recovery_root = tool_root.parent / (tool_root.name + "-amplifier-unified-recovery")
            _no_links(recovery_root)
            recovery_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            approvals_path = self.root / "tool-roots.json"
            approvals = _read(approvals_path) if approvals_path.exists() else {}
            info = recovery_root.stat()
            approvals[str(tool_root)] = {"recoveryRoot": str(recovery_root),
                                         "device": info.st_dev, "inode": info.st_ino,
                                         "dataIdentity": identity(self.home)}
            _write(approvals_path, approvals)
            row["toolRoot"] = str(tool_root)
            locations = [("data-snapshot", self.home / "reset-backups" / operation),
                         ("tool-recovery", tool_root.parent / (tool_root.name + "-amplifier-unified-recovery") / operation)]
        else:
            locations = [("private-archive", self.home / "backups" / operation),
                         ("retained-originals", self.home / "backups" / ("reset-" + operation))]
        # Record intent before creating any member. Interrupted creation remains
        # pending and cannot be adopted as a successful reset by a later cleanup.
        self.state["operations"][operation] = row
        self.save()
        for role, path in locations:
            _no_links(path)
            path.mkdir(parents=True, mode=0o700)
            owner = {"version": VERSION, "operationId": operation, "role": role,
                     "dataDir": str(self.home), "dataIdentity": identity(self.home)}
            _write(path / MARKER, owner)
            info = path.lstat()
            row["members"].append({"role": role, "path": str(path),
                                   "device": info.st_dev, "inode": info.st_ino, "removed": False,
                                   "ancestors": _ancestors(path.parent)})
            self.save()
        if origin == "cli":
            row["toolMembers"] = {"previous-installation": str(locations[1][1] / "previous"),
                                  "failed-replacement": str(locations[1][1] / "failed")}
            self.save()
        return operation

    def fail(self, operation):
        self.state["operations"][operation]["state"] = "unresolved"
        self.save()

    def succeed(self, operation):
        row = self.state["operations"][operation]
        if row["state"] == "successful":
            return  # A retry must never assign a new completion order.
        if row["state"] != "pending":
            raise ValueError("Only a pending verified reset can commit success")
        self.validate(operation, row)
        # Capture actual payload roots, not just empty ownership containers.
        # This is bounded by selected stores (not the size of their trees).
        for member in row["members"]:
            path = Path(member["path"])
            if member["role"] == "private-archive" and (
                    not (path / "private-state.tar.gz").is_file()
                    or (path / "private-state.tar.gz").is_symlink()):
                raise ValueError("Reset archive did not complete")
            member["payload"] = {child.name: _stamp(child) for child in path.iterdir() if child.name != MARKER}
            # New archive/SQLite/launcher bytes and renamed recovery entries
            # must reach durable storage before the success-order ledger does.
            for name, stamp in member["payload"].items():
                child = path / name
                if stamp["mode"] == stat.S_IFREG:
                    fd = os.open(child, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                elif stamp["mode"] == stat.S_IFDIR:
                    _sync_directory(child)
            _sync_directory(path)
            _sync_directory(path.parent)
        _sync_directory(self.home)  # Source sides of reset's data renames.
        if row["origin"] == "cli":
            _sync_directory(Path(row["toolRoot"]))
        self.intact(operation, row)
        self.state["sequence"] += 1
        row.update(state="successful", successOrder=self.state["sequence"], cleanup="retained")
        self.save()  # Durable successful-completion order BEFORE any deletion.

    def intact(self, operation, row):
        self.validate(operation, row)
        if any(member.get("removed") or member.get("deleting")
               or not Path(member["path"]).is_dir() or Path(member["path"]).is_symlink()
               for member in row["members"]):
            raise ValueError("The retained recovery set is incomplete; older copies preserved")
        for member in row["members"]:
            path = Path(member["path"])
            for name, stamp in member["payload"].items():
                if Path(name).name != name or name in {".", ".."} or _stamp(path / name) != stamp:
                    raise ValueError("Retained reset payload is missing or substituted; older copies preserved")

    def _expected(self, operation, row):
        if not re.fullmatch(r"[a-f0-9]{32}", operation) or row.get("id") != operation:
            raise ValueError("Invalid reset operation identity")
        if row.get("origin") == "in-app":
            return {"private-archive": self.home / "backups" / operation,
                    "retained-originals": self.home / "backups" / ("reset-" + operation)}
        if row.get("origin") != "cli":
            raise ValueError("Invalid reset entry point")
        tool = Path(row["toolRoot"])
        if not tool.is_absolute() or canonical(tool) != tool or tool == self.home or tool.is_relative_to(self.home):
            raise ValueError("Invalid tool recovery namespace")
        recovery = tool.parent / (tool.name + "-amplifier-unified-recovery") / operation
        approvals = _read(self.root / "tool-roots.json")
        approved = approvals.get(str(tool), {})
        _no_links(recovery.parent)
        info = recovery.parent.stat()
        if approved != {"recoveryRoot": str(recovery.parent), "device": info.st_dev,
                        "inode": info.st_ino, "dataIdentity": identity(self.home)}:
            raise ValueError("Tool recovery root was not enrolled by this data directory's CLI")
        if row.get("toolMembers") != {"previous-installation": str(recovery / "previous"),
                                      "failed-replacement": str(recovery / "failed")}:
            raise ValueError("Invalid paired installation identity")
        return {"data-snapshot": self.home / "reset-backups" / operation, "tool-recovery": recovery}

    def validate(self, operation, row):
        expected = self._expected(operation, row)
        members = row.get("members")
        if (not isinstance(members, list) or len(members) != len(expected)
                or any(not isinstance(member, dict) for member in members)):
            raise ValueError("Incomplete reset ownership metadata")
        if {member.get("role") for member in members} != set(expected):
            raise ValueError("Invalid reset member roles")
        for member in members:
            path = expected[member["role"]]
            if member.get("path") != str(path):
                raise ValueError("Reset member is outside its exact owned namespace")
            _no_links(path.parent)
            _check_ancestors(member["ancestors"])
            if not path.exists() and not path.is_symlink():
                continue  # Crash after removal, before progress save.
            info = path.lstat()
            if path.is_symlink():
                # A substituted final link may be unlinked, never traversed.
                continue
            if (info.st_dev, info.st_ino) != (member.get("device"), member.get("inode")):
                raise ValueError("Reset member was substituted")
            marker = path / MARKER
            if member.get("deleting") and not marker.exists() and not marker.is_symlink():
                # Only the final empty-directory removal may have lost its
                # marker. A "deleting" ledger bit never authorizes a new walk.
                if any(path.iterdir()):
                    raise ValueError("Interrupted member lost its ownership marker; explicit review required")
            else:
                owner = _read(marker)
                if owner != {"version": VERSION, "operationId": operation, "role": member["role"],
                             "dataDir": str(self.home), "dataIdentity": identity(self.home)}:
                    raise ValueError("Reset member ownership does not match this data directory")
        return expected

    def cleanup(self):
        """Retry only obsolete committed sets; unknown material is never disposable."""
        self.load()
        rows = self.state["operations"]
        successful = [(row.get("successOrder"), operation) for operation, row in rows.items()
                      if row.get("state") == "successful"]
        orders = [order for order, _ in successful]
        if any(type(order) is not int or order <= 0 or order > self.state["sequence"] for order in orders) or len(set(orders)) != len(orders):
            raise ValueError("Ambiguous successful reset order; no recovery files removed")
        latest = max(successful)[1] if successful else None
        if latest:
            self.intact(latest, rows[latest])
        errors = []
        for operation, row in rows.items():
            if operation == latest or row.get("state") != "successful":
                continue
            try:
                with ExitStack() as locks:
                    if row.get("origin") == "cli":
                        # Local import only: emergency module remains stdlib-only.
                        if __package__:
                            from .reset import repair_lock
                        else:
                            import importlib.util
                            spec = importlib.util.spec_from_file_location("reset", Path(__file__).with_name("reset.py"))
                            repair = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(repair)
                            repair_lock = repair.repair_lock
                        self._expected(operation, row)
                        tool = Path(row["toolRoot"])
                        locks.enter_context(repair_lock(tool.parent / (tool.name + "-amplifier-unified-recovery")))
                    self.validate(operation, row)  # Validate the WHOLE pair first.
                    if any(member["removed"] and (Path(member["path"]).exists() or Path(member["path"]).is_symlink())
                           for member in row["members"]):
                        raise ValueError("Removed recovery member reappeared; preserved for explicit review")
                    if row["cleanup"] == "removed":
                        if not all(member["removed"] for member in row["members"]):
                            raise ValueError("Inconsistent removed recovery state")
                        continue
                    _assert_unused([Path(member["path"]) for member in row["members"]])
                    row.update(cleanup="removing", errors=[])
                    self.save()
                    for member in row["members"]:
                        if member["removed"]:
                            continue
                        self.validate(operation, row)
                        member["deleting"] = True
                        self.save()
                        remove_member(Path(member["path"]), member)
                        member["removed"] = True
                        self.save()
                    row["cleanup"] = "removed"
                    self.save()
            except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
                message = f"{operation}: {error}"
                row.update(cleanup="incomplete", errors=[message])
                self.save()
                errors.append(message)
        preserved = [operation for operation, row in rows.items() if row.get("state") != "successful"]
        legacy = self.legacy()
        return {"retainedOperationId": latest, "cleanupComplete": not errors,
                "errors": errors, "preservedUnresolved": preserved, "legacyReview": legacy,
                "reviewRequired": bool(preserved or legacy)}

    def legacy(self):
        """Legacy formats have no committed success witness: report, never guess."""
        result = []
        known = set(self.state["operations"])
        for root, pattern in ((self.home / "reset-backups", r"[a-f0-9]{32}"),
                              (self.home / "backups", r"reset-[a-f0-9]{32}")):
            _no_links(root)
            if not root.is_dir():
                continue
            for path in root.iterdir():
                if not re.fullmatch(pattern, path.name) or path.name.removeprefix("reset-") in known:
                    continue
                item = {"path": str(path), "reason": "No durable successful-completion or archive pairing evidence"}
                if root.name == "reset-backups" and not path.is_symlink():
                    try:
                        manifest = _read(path / "manifest.json")
                        tool = Path(manifest["tool"])
                        saved = Path(manifest["toolBackup"])
                        approved = {tool.parent.parent / (tool.parent.name + "-amplifier-unified-recovery") / path.name / "previous",
                                    tool.parent / (".amplifier-unified-reset-" + path.name),
                                    tool.parent / (".amplifier-unified-failed-" + path.name)}
                        if (manifest.get("dataDir") == str(self.home) and tool.name == "amplifier-unified"
                                and tool.is_absolute() and saved in approved):
                            _no_links(saved.parent)
                            item["pairedInstallation"] = str(saved)
                            failed = tool.parent / (".amplifier-unified-failed-" + path.name)
                            if failed.exists() or failed.is_symlink():
                                item["failedInstallation"] = str(failed)
                    except (OSError, ValueError, KeyError, TypeError):
                        item["reason"] = "Unverified legacy manifest; explicit review required"
                result.append(item)
        return result


def remove_member(path, member):
    """Open every ancestor without following links, then fd-relative deletion."""
    if not shutil.rmtree.avoids_symlink_attacks or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("Safe reset cleanup is unavailable on this host")
    with ExitStack() as stack:
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, fd)
        for part in path.parent.parts[1:]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
            # Compare the actual opened directory, not a prior pathname stat.
        parent = os.fstat(fd)
        expected = member["ancestors"][str(path.parent)]
        if (parent.st_dev, parent.st_ino) != (expected["device"], expected["inode"]):
            raise ValueError("Recovery parent changed before deletion")
        try:
            info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode):
            os.unlink(path.name, dir_fd=fd)
        elif stat.S_ISDIR(info.st_mode):
            owned = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, owned)
            actual = os.fstat(owned)
            if (actual.st_dev, actual.st_ino) != (member["device"], member["inode"]):
                raise ValueError("Recovery member changed before deletion")
            # Traverse from the checked inode, not a pathname checked earlier.
            # A concurrent rename cannot redirect this walk into a new root.
            with os.scandir(owned) as entries:
                for entry in entries:
                    if entry.name == MARKER:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        shutil.rmtree(entry.name, dir_fd=owned)
                    else:
                        os.unlink(entry.name, dir_fd=owned)
            os.fsync(owned)
            current = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (actual.st_dev, actual.st_ino):
                raise ValueError("Recovery member moved during cleanup; inspect original location")
            try:
                os.unlink(MARKER, dir_fd=owned)
            except FileNotFoundError:
                pass  # Crash after last marker unlink: only an empty rmdir.
            os.rmdir(path.name, dir_fd=fd)
        else:
            raise ValueError("Recovery member is no longer a directory or link")
        os.fsync(fd)


def archive_ids(home):
    """Protect reset archives from the independent manual-private-backup policy."""
    root = canonical(home) / "reset-recovery"
    if not root.exists():
        return set()
    try:
        state = _read(root / "state.json")
        if (not isinstance(state, dict) or state.get("dataIdentity") != identity(home)
                or not isinstance(state.get("operations"), dict)
                or any(not isinstance(row, dict) for row in state["operations"].values())):
            raise ValueError("Invalid reset ownership state")
        return {operation for operation, row in state["operations"].items()
                if row.get("origin") == "in-app"}
    except (OSError, ValueError, KeyError, TypeError):
        # Fail closed; markers also protect archives when a ledger is unavailable.
        return None


def recovery_members(home):
    """Validated catalog projection, not deletion authority or a reset census."""
    owner = Retention(home)
    if not owner.root.exists():
        return []
    result = []
    try:
        state = owner.load()
        for operation, row in state["operations"].items():
            try:
                owner.validate(operation, row)
                for member in row["members"]:
                    if not member["removed"]:
                        result.append({**member, "operationId": operation, "state": row["state"]})
            except (OSError, ValueError, KeyError, TypeError):
                continue
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result


@contextmanager
def lease(home):
    owner = Retention(home).acquire()
    try:
        yield owner
    finally:
        owner.close()


def retry_cleanup(home):
    with lease(home) as owner:
        return owner.cleanup()