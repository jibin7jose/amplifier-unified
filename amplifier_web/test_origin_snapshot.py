"""Export repository inputs without mounting their Git metadata or changing the source checkout."""

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
from tempfile import NamedTemporaryFile, TemporaryDirectory, TemporaryFile
import time
import uuid
from typing import BinaryIO
import zlib

if __package__:
    from .test_origin_files import source_file, directory
else:  # The managed DTU interpreter invokes this installed owner with -I.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_origin_files import source_file, directory

# Working-tree algorithm adapted from the MIT-licensed DTU snapshot exporter
# (5235833098d9a630791452a46558d92f58af0389). No installed runner is needed.

MAX_FILES = 100_000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_CONTENT_BYTES = 1024 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 120


def _error(code: str, message: str) -> ValueError:
    return ValueError(message + " No source files or Git metadata were changed.")


@dataclass
class Git:
    scratch: Path

    def run(self, repository: Path, *arguments: str, data: bytes | BinaryIO | None = None) -> bytes:
        with TemporaryFile(dir=self.scratch) as output:
            self.write(repository, arguments, output, MAX_METADATA_BYTES, data)
            output.seek(0)
            return output.read()

    def write(
        self,
        repository: Path,
        arguments: tuple[str, ...],
        output: BinaryIO,
        limit: int,
        data: bytes | BinaryIO | None = None,
    ) -> None:
        # Do not inherit Git directory/index overrides, credentials, user hooks, or lazy-fetch behavior.
        environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR") if key in os.environ}
        environment.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            GIT_NO_LAZY_FETCH="1",
            GIT_OPTIONAL_LOCKS="0",
            GIT_AUTHOR_NAME="Digital Twin Universe",
            GIT_AUTHOR_EMAIL="dtu@digital-twin-universe.invalid",
            GIT_COMMITTER_NAME="Digital Twin Universe",
            GIT_COMMITTER_EMAIL="dtu@digital-twin-universe.invalid",
            GIT_AUTHOR_DATE="2000-01-01T00:00:00Z",
            GIT_COMMITTER_DATE="2000-01-01T00:00:00Z",
        )
        command = [
            "git",
            "--no-pager",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={self.scratch / 'no-hooks'}",
            "-c",
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repository),
            *arguments,
        ]
        with TemporaryFile(dir=self.scratch) as incoming, TemporaryFile(dir=self.scratch) as errors:
            if isinstance(data, bytes):
                incoming.write(data)
            elif data is not None:
                shutil.copyfileobj(data, incoming)
            incoming.seek(0)
            try:
                with subprocess.Popen(
                    command, stdin=incoming, stdout=output, stderr=errors, env=environment
                ) as process:
                    try:
                        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
                        while process.poll() is None:
                            if (
                                os.fstat(output.fileno()).st_size > limit
                                or os.fstat(errors.fileno()).st_size > MAX_METADATA_BYTES
                                or time.monotonic() > deadline
                            ):
                                raise _error(
                                    "repository-snapshot-limit",
                                    "Repository export exceeded its size or Git time limit.",
                                )
                            time.sleep(0.01)
                        if process.returncode:
                            raise _error(
                                "repository-snapshot-failed",
                                f"Git could not export {repository.name!r} ({arguments[0]}).",
                            )
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
            except FileNotFoundError as error:
                raise _error("git-missing", "Copying this project for testing needs Git on PATH.") from error
            if os.fstat(output.fileno()).st_size > limit or os.fstat(errors.fileno()).st_size > MAX_METADATA_BYTES:
                raise _error("repository-snapshot-limit", "Repository export exceeded its size limit.")


def _identity(git: Git, source: Path) -> tuple[bytes, bytes, bytes, bytes]:
    return (
        git.run(source, "rev-parse", "HEAD").strip(),
        git.run(source, "rev-parse", "--abbrev-ref", "HEAD").strip(),
        git.run(source, "for-each-ref", "--format=%(objectname) %(refname)", "refs/heads", "refs/tags"),
        git.run(source, "ls-files", "--stage", "-z"),
    )


def _selection(git: Git, source: Path, index: bytes) -> dict[str, tuple[str, str]]:
    selected = {}
    for entry in index.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, oid, stage = metadata.decode().split()
        if stage != "0":
            raise _error("repository-unmerged", "Resolve the source index's merge conflicts before publishing it.")
        selected[os.fsdecode(name)] = (mode, oid)
    for name in git.run(source, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if name:
            selected[os.fsdecode(name)] = ("", "")
    for name in prepared_client_inputs(source):
        selected[name] = ('100644', '')
    if len(selected) > MAX_FILES:
        raise _error("repository-snapshot-limit", f"Repository snapshot exceeds {MAX_FILES} selected files.")
    return selected


def prepared_client_inputs(source):
    """Carry only prepared dependency inputs for an exact repository delivery."""
    selected = source_file(source, 'components/web-client.source.json', '', 16 * 1024)
    if selected is None or selected[0] == '120000':
        return ()
    record = json.loads(selected[1])
    if record.get('delivery', {}).get('type') != 'repository':
        return ()
    prefix = '.build/web-client/'
    names = tuple(prefix + name for name in ('package.json', 'package-lock.json', '.client-source.json'))
    values = {}
    for name in names:
        content = source_file(source, name, '100644', 16 * 1024 * 1024)
        if content is None or content[0] != '100644':
            raise ValueError('Prepare the selected client before transferring source: '
                             'python scripts/web_client_build.py prepare')
        values[name] = content[1]
    if json.loads(values[prefix + '.client-source.json']) != record:
        raise ValueError('Prepared client inputs belong to another selection; run '
                         'python scripts/web_client_build.py prepare before transferring source.')
    return names




def _tree_entries(
    git: Git, source: Path, index: bytes, destination: Path | None, object_format: str
) -> dict[str, tuple[str, str]]:
    entries = {}
    total = 0
    for name, (mode, oid) in sorted(_selection(git, source, index).items()):
        if mode == "160000":
            entries[name] = (mode, oid)
            continue
        content = source_file(source, name, mode, MAX_FILE_BYTES)
        if content is None:
            continue
        mode, data = content
        total += len(data)
        if total > MAX_CONTENT_BYTES:
            raise _error("repository-snapshot-limit", f"Selected contents exceed {MAX_CONTENT_BYTES} bytes.")
        raw = f"blob {len(data)}\0".encode() + data
        oid = hashlib.new(object_format, raw).hexdigest()
        entries[name] = (mode, oid)
        if destination is not None:
            path = destination / "objects" / oid[:2] / oid[2:]
            path.parents[0].mkdir(exist_ok=True)
            path.write_bytes(zlib.compress(raw))
    return entries


def export(source: Path, destination: Path) -> dict[str, str | int | bool]:
    """A self-contained bare snapshot and provenance; failed exports are removed, never retried implicitly."""
    source = source.resolve()
    if destination.resolve().is_relative_to(source):
        raise _error("snapshot-destination-inside-source", "Keep snapshot output outside the source checkout.")
    destination.mkdir(parents=True, mode=0o700)
    git = Git(destination.parents[0])
    try:
        if Path(os.fsdecode(git.run(source, "rev-parse", "--show-toplevel").strip())).resolve() != source:
            raise _error("repository-not-root", "Publish a repository or linked worktree root, not its subdirectory.")
        identity = _identity(git, source)
        head, branch, refs, index = identity
        object_format = git.run(source, "rev-parse", "--show-object-format").decode().strip()
        shallow = git.run(source, "rev-parse", "--is-shallow-repository").strip() == b"true"
        git.run(destination, "init", "--bare", f"--object-format={object_format}", "--template=", "-q")
        entries = _tree_entries(git, source, index, destination, object_format)
        if (
            _identity(git, source) != identity
            or _tree_entries(git, source, index, None, object_format) != entries
            or _identity(git, source) != identity
        ):
            raise _error(
                "repository-source-changed", "The source files, index, HEAD, or local refs changed during export."
            )
        listing = b"".join(
            f"{mode} {oid}\t".encode() + os.fsencode(name) + b"\0" for name, (mode, oid) in entries.items()
        )
        git.run(destination, "read-tree", "--empty")
        git.run(destination, "update-index", "-z", "--index-info", data=listing)
        tree = git.run(destination, "write-tree").decode().strip()
        current = branch.decode() if branch != b"HEAD" else "detached"
        commit = (
            git.run(
                destination,
                "commit-tree",
                tree,
                data=b"The selected working tree at universe launch\n",
            )
            .decode()
            .strip()
        )
        git.run(destination, "update-ref", f"refs/heads/{current}", commit)
        git.run(destination, "symbolic-ref", "HEAD", f"refs/heads/{current}")
        provenance = {
            "source_head": head.decode(),
            "source_branch": branch.decode(),
            "snapshot_commit": commit,
            "snapshot_tree": tree,
            "files": len(entries),
            "shallow_source": shallow,
        }
        receipt = destination.with_name(f"{destination.name}.snapshot.json")
        receipt.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        receipt.chmod(0o600)
        return provenance
    except BaseException:
        shutil.rmtree(destination)
        raise


def _export_archive(source: Path, destination: Path, parent) -> dict:
    """Export selected raw tree contents as a private tar archive, without Git archive attribute transforms."""
    if destination.resolve().is_relative_to(source.resolve()):
        raise _error("snapshot-destination-inside-source", "Keep snapshot output outside the source checkout.")
    if destination.exists() or destination.is_symlink():
        raise _error("snapshot-destination-exists", f"Snapshot destination {destination} already exists.")
    if not destination.parents[0].is_dir():
        raise _error("snapshot-destination-missing", "Create the owner-managed archive destination directory first.")
    # Scratch is independent of the destination pathname. Only publication
    # writes to that directory, through its retained descriptor/Windows pin.
    with TemporaryDirectory(prefix="dtu-snapshot-") as scratch:
        repository = Path(scratch) / "repository"
        provenance = export(source, repository)
        git = Git(Path(scratch))
        listing = git.run(repository, "ls-tree", "-rz", "HEAD")
        with NamedTemporaryFile(dir=scratch, mode="w+b") as temporary:
            with tarfile.open(fileobj=temporary, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for entry in listing.split(b"\0"):
                    if not entry:
                        continue
                    metadata, name = entry.split(b"\t", 1)
                    mode, kind, oid = metadata.decode().split()
                    item = tarfile.TarInfo(os.fsdecode(name))
                    if kind == "commit":
                        item.type, item.mode = tarfile.DIRTYPE, 0o755
                        archive.addfile(item)
                        continue
                    # Every selected blob was written as a loose object by export, without copying the source repository object database.
                    raw = zlib.decompress((repository / "objects" / oid[:2] / oid[2:]).read_bytes())
                    header, data = raw.split(b"\0", 1)
                    if header != f"blob {len(data)}".encode():
                        raise _error("repository-snapshot-failed", "A snapshot blob has invalid content.")
                    if mode == "120000":
                        item.type, item.mode, item.linkname = tarfile.SYMTYPE, 0o777, os.fsdecode(data)
                        archive.addfile(item)
                    else:
                        item.mode, item.size = (0o755 if mode == "100755" else 0o644), len(data)
                        archive.addfile(item, io.BytesIO(data))
            temporary.flush()
            temporary.seek(0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: temporary.read(1024 * 1024), b""):
                digest.update(chunk)
            size = os.fstat(temporary.fileno()).st_size
            # Copy into an exclusive file relative to the pinned destination;
            # the scratch filesystem need not support cross-device hard links.
            name = '.' + uuid.uuid4().hex + '.snapshot'
            parent.assert_current()
            try:
                temporary.seek(0)
                with os.fdopen(parent.create(name), 'wb') as output:
                    shutil.copyfileobj(temporary, output)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    parent.link(name, destination.name)
                except FileExistsError as error:
                    raise _error(
                        "snapshot-destination-exists", "Snapshot destination appeared during export."
                    ) from error
                parent.sync()
                parent.assert_current()
            finally:
                try:
                    parent.unlink(name)
                except FileNotFoundError:
                    pass
        return {
                "path": str(destination),
                "archive_sha256": digest.hexdigest(),
                "archive_bytes": size,
                **provenance,
            }


def export_archive(source: Path, destination: Path) -> dict:
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.resolve().is_relative_to(source):
        raise _error('snapshot-destination-inside-source', 'Keep snapshot output outside the source checkout.')
    with directory(source), directory(destination.parent, private=True) as parent:
        return _export_archive(source, destination, parent)


if __name__ == '__main__':
    try:
        print(json.dumps(export_archive(Path(sys.argv[1]), Path(sys.argv[2]))))
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
