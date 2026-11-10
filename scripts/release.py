"""Build/release checks for the immutable GitHub Releases application channel."""
from __future__ import annotations

import argparse
import ast
from email.parser import Parser
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import time
import tomllib
import zipfile

VERSION = re.compile(r"\d+\.\d+\.\d+\Z")
RELEASE_SOURCE = "amplifier_web/release-source.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))


def run(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise ValueError(f"Release command failed: {args[0]} {args[1]}")
    return result.stdout.strip()


def package_version(project, module):
    version = tomllib.loads(project)["project"]["version"]
    values = [ast.literal_eval(node.value) for node in ast.parse(module).body
              if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)]
    if not VERSION.fullmatch(version) or values != [version]:
        raise ValueError("Release requires matching stable versions in pyproject.toml and amplifier_web/__init__.py")
    return version


def version_at(root, ref=None):
    def read(path):
        return run("git", "show", f"{ref}:{path}", cwd=root) if ref else (root / path).read_text()
    return package_version(read("pyproject.toml"), read("amplifier_web/__init__.py"))


def notes_module(root):
    spec = importlib.util.spec_from_file_location('release_history', root / 'amplifier_web/release_notes.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replace_version(text, field, old, new):
    pattern = rf'(?m)^([ \t]*{re.escape(field)}[ \t]*=[ \t]*)([\"\']){re.escape(old)}\2([ \t]*(?:#[^\n]*)?)$'
    updated, count = re.subn(pattern, lambda match: match[1] + match[2] + new + match[2] + match[3], text)
    if count != 1:
        raise ValueError(f'Expected one unambiguous {field} assignment; source left unchanged')
    return updated


def prepare(root, version, notes_file, build=False):
    """Prepare local candidate files; no dependency installation or publication."""
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError('Choose a stable release version')
    names = ['pyproject.toml', 'amplifier_web/__init__.py', 'uv.lock', 'amplifier_web/release-notes.json']
    original = {name: (root / name).read_text() for name in names}
    old = package_version(original[names[0]], original[names[1]])
    if tuple(map(int, version.split('.'))) < tuple(map(int, old.split('.'))):
        raise ValueError('Release preparation cannot downgrade the current version')
    if tomllib.loads(original[names[0]])['project'].get('name') != 'amplifier-unified':
        raise ValueError('Release preparation requires the amplifier-unified project')
    tomllib.loads(original['uv.lock'])

    # Update only the local root package, never an unrelated dependency with
    # the same version. Keep comments and all other manifest bytes unchanged.
    blocks = list(re.finditer(r'(?ms)^\[\[package\]\][ \t]*\n.*?(?=^\[\[package\]\]|\Z)', original['uv.lock']))
    roots = [block for block in blocks if (row := tomllib.loads(block[0])['package'][0]).get('name') == 'amplifier-unified'
             and row.get('source') in ({'editable': '.'}, {'virtual': '.'})]
    if len(roots) != 1 or tomllib.loads(roots[0][0])['package'][0].get('version') != old:
        raise ValueError('Release requires one local uv.lock root matching the package version')
    project = re.search(r'(?ms)^\[project\][ \t]*\n.*?(?=^\[|\Z)', original[names[0]])
    if project is None:
        raise ValueError('Release preparation requires an explicit project table')
    updated = dict(original)
    for name, block in ((names[0], project), ('uv.lock', roots[0])):
        updated[name] = original[name][:block.start()] + replace_version(block[0], 'version', old, version) + original[name][block.end():]
    updated[names[1]] = replace_version(original[names[1]], '__version__', old, version)

    validator = notes_module(root)
    history = json.loads(original[names[3]])
    validator.parse(original[names[3]])
    entry = json.loads(notes_file.read_text())
    if not isinstance(entry, dict) or entry.get('version') != version:
        raise ValueError('Authored notes must be one release entry for the chosen version')
    existing = next((item for item in history['releases'] if item['version'] == version), None)
    if existing is not None and existing != entry:
        raise ValueError('Different notes already exist for this version; review them explicitly')
    if existing is None:
        history['releases'].insert(0, entry)
        updated[names[3]] = '{\n  "schemaVersion": 1,\n  "releases": [\n    ' + ',\n    '.join(
            json.dumps(item, ensure_ascii=False, separators=(',', ':')) for item in history['releases']) + '\n  ]\n}\n'
    validator.parse(updated[names[3]], version)

    from historical_web_layout import build_command
    command = build_command(root)
    changed = [name for name in names if updated[name] != original[name]]
    for name in changed:
        (root / name).write_text(updated[name])
    result = {'version': version, 'changedFiles': changed, 'buildCommand': command, 'buildStatus': 'not-run'}
    if build:
        started = time.monotonic()
        completed = subprocess.run(command, cwd=root)
        if completed.returncode:
            raise ValueError(f'Frontend build failed (exit {completed.returncode}); prepared metadata retained. Fix the build issue and rerun the build command.')
        identity = json.loads((root / 'amplifier_web/static/build.json').read_text())
        if (root / 'components/web-client.source.json').is_file():
            # Client versions are independent. Verify its selected source and
            # built artifact, rather than restamping it with the host version.
            from web_client_build import verify
            verify(root)
        elif identity.get('version') != version:
            raise ValueError('Built frontend version does not match the prepared candidate')
        result.update(buildStatus='passed', build=identity, buildSeconds=round(time.monotonic() - started, 3))
    result['nextSteps'] = ([] if build else [
        'Run buildCommand once in the owned prepared test environment; retain its generated assets.'
    ]) + [
        'Run uv sync --locked --dry-run --group dev --group artifacts in that environment; '
        'this checks lock consistency, not that all optional packages are installed.',
        'Commit the prepared metadata and generated assets in the change PR where practical; push early. '
        'Include the version rationale, authored notes and affected validation.',
        'While required CI runs, review remaining integration, version, notes and generated-output risks. '
        'Reuse independent feature review; merge the exact reviewed head through repository protections.',
        'After merge, release.yml publishes automatically; post-release.yml verifies the published package. '
        'Record those results without repeating unchanged checks. Live adoption is separate.'
    ]
    result['guidance'] = 'docs/validation/release-qualification.md#preparing-a-version-change'
    return result


def plan(root, selected_revision=None, *, fetch_tag=False):
    if selected_revision:
        if not re.fullmatch(r'[0-9a-f]{40}', selected_revision):
            raise ValueError('Select a full immutable commit SHA, not a branch or abbreviated revision')
        try:
            if run('git', 'rev-parse', selected_revision+'^{commit}', cwd=root) != selected_revision:
                raise ValueError('Not a commit object')
            run('git', 'merge-base', '--is-ancestor', selected_revision, 'HEAD', cwd=root)
        except ValueError:
            raise ValueError('The selected release commit must be an ancestor of the checked-out main commit') from None
    version = version_at(root, selected_revision)
    tag = "v" + version
    # Ordinary publication needs only the selected tree and its exact version
    # tag, not every historical tree/tag. Remote errors remain release failures.
    if fetch_tag and run('git', 'ls-remote', 'origin', 'refs/tags/' + tag, cwd=root):
        depth = ['--depth=1'] if run('git', 'rev-parse', '--is-shallow-repository', cwd=root) == 'true' else []
        run('git', 'fetch', '--no-tags', *depth, 'origin',
            f'refs/tags/{tag}:refs/tags/{tag}', cwd=root)
    tags = run("git", "tag", "--list", tag, cwd=root).splitlines()
    revision = selected_revision or run("git", "rev-parse", "HEAD", cwd=root)
    if tags:
        tagged_revision = run("git", "rev-parse", tag + "^{commit}", cwd=root)
        if version_at(root, tagged_revision) != version:
            raise ValueError("Existing immutable release tag does not match its package version")
        if tagged_revision != revision:
            raise ValueError(
                "Existing immutable release tag does not match the selected commit. "
                "Increment the package version to release this commit, or select the "
                "original tagged commit to resume that release."
            )
    return {"version": version, "tag": tag, "revision": revision, "existing_tag": bool(tags)}


def verify_frontend(root, names, read, label):
    """Both release formats carry the complete, already-built frontend."""
    prefix = "amplifier_web/static/"
    files = {path.relative_to(root).as_posix(): path
             for path in (root / prefix).rglob("*") if path.is_file()}
    packaged = {name for name in names if name.startswith(prefix) and not name.endswith("/")}
    if prefix + "index.html" not in files or packaged != files.keys():
        raise ValueError(label + " frontend file list differs from validated assets")
    for name, path in files.items():
        if read(name) != path.read_bytes():
            raise ValueError(label + " does not contain the validated frontend bytes: " + name)
    index = read(prefix + "index.html").decode()
    for asset in re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', index):
        if prefix.rstrip("/") + asset not in packaged:
            raise ValueError(label + " frontend references a missing asset")


def source_record_bytes(root, repository, revision, version):
    """Source facts, never publication proof; old candidates remain unstamped."""
    current = run('git', 'rev-parse', 'HEAD', cwd=root)
    selected = revision or current
    if not isinstance(selected, str) or not re.fullmatch(r'[0-9a-f]{40}', selected):
        raise ValueError('Source record requires a full selected commit SHA')
    if selected != current:
        raise ValueError('Source record does not match the selected checkout')
    # Only the selected commit can opt into the new packaging contract.
    project = run('git', 'show', selected + ':pyproject.toml', cwd=root)
    build = tomllib.loads(project).get('tool', {}).get('hatch', {}).get('build', {})
    if '/' + RELEASE_SOURCE not in build.get('artifacts', []):
        return None
    if not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Source record requires the selected release repository')
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Source record requires a full selected commit SHA')
    if version_at(root) != version:
        raise ValueError('Source record does not match the selected checkout')
    run('git', 'diff', '--exit-code', 'HEAD', '--', cwd=root)
    if run('git', 'ls-files', '--others', '--exclude-standard', cwd=root):
        raise ValueError('Source record requires a clean candidate without untracked inputs')
    value = {'schema': 1, 'repository': repository, 'revision': revision, 'version': version}
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def stage_source(root, repository, revision, version):
    """Stage generated package facts after the workflow verifies its candidate."""
    data = source_record_bytes(root, repository, revision, version)
    path = root / RELEASE_SOURCE
    if path.is_symlink():
        raise ValueError('Source record destination must not be a link')
    if data is None:
        if path.exists():
            raise ValueError('Historical candidate cannot carry a source record')
        return {'staged': False, 'reason': 'candidate predates source-record packaging'}
    path.write_bytes(data)
    return {'staged': True, 'path': RELEASE_SOURCE}


def verify_source_record(names, read, expected, label):
    count = names.count(RELEASE_SOURCE)
    if expected is None:
        if count:
            raise ValueError(label + ' unexpectedly carries a source record for a historical candidate')
    elif count != 1 or read(RELEASE_SOURCE) != expected:
        raise ValueError(label + ' source record does not match the selected candidate')


def verify_dist(root, dist, expected, repository=None, revision=None):
    if version_at(root) != expected:
        raise ValueError("Checkout version changed during release")
    notes = release_notes(root, expected)
    source_record = source_record_bytes(root, repository, revision, expected)
    wheels = list(dist.glob("*.whl"))
    sources = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Release requires exactly one wheel and one source archive")
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        reject_ci_checkouts(names)
        verify_source_record(names, archive.read, source_record, 'Wheel')
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        info = Parser().parsestr(archive.read(metadata[0]).decode()) if len(metadata) == 1 else {}
        if info.get("Name") != "amplifier-unified" or info.get("Version") != expected:
            raise ValueError("Wheel metadata does not match release version")
        verify_frontend(root, names, archive.read, "Wheel")
        files = [path for path in (root / "amplifier_web").rglob("*")
                 if path.is_file() and not path.is_relative_to(root / "amplifier_web/static")
                 and (path.suffix in {".py", ".js"} or path.name == 'release-notes.json'
                                       or path.is_relative_to(root / "amplifier_web/runtime_deps"))
                 and "__pycache__" not in path.parts and path.suffix != ".pyc"]
        for path in files:
            relative = path.relative_to(root).as_posix()
            if relative not in names or archive.read(relative) != path.read_bytes():
                raise ValueError("Wheel does not contain the validated source/assets: " + relative)
    with tarfile.open(sources[0]) as archive:
        names = archive.getnames()
        reject_ci_checkouts(names)
        prefix = names[0].split("/", 1)[0] + "/"
        verify_source_record([entry.name.removeprefix(prefix) for entry in archive.getmembers() if entry.isfile()],
                             lambda name: archive.extractfile(prefix + name).read(), source_record, 'Source archive')
        project = archive.extractfile(prefix + "pyproject.toml").read().decode()
        module = archive.extractfile(prefix + "amplifier_web/__init__.py").read().decode()
        if package_version(project, module) != expected:
            raise ValueError("Source archive does not match release version")
        if notes and archive.extractfile(prefix + 'amplifier_web/release-notes.json').read() != (root / 'amplifier_web/release-notes.json').read_bytes():
            raise ValueError('Source archive does not contain validated release notes')
        verify_frontend(root, [entry.name.removeprefix(prefix) for entry in archive.getmembers() if entry.isfile()],
                        lambda name: archive.extractfile(prefix + name).read(), "Source archive")
    (dist / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in [*wheels, *sources]))


def reject_ci_checkouts(names):
    """CI dependencies are test inputs, never application distribution files."""
    for name in names:
        if '.ci' in PurePosixPath(name.replace('\\', '/')).parts:
            raise ValueError('Distribution contains a CI checkout: ' + name)


def release_notes(root, expected):
    path = root / 'amplifier_web/release_notes.py'
    # Rerunning an immutable historical tag must still use its original assets.
    if not path.exists() and tuple(map(int, expected.split('.'))) <= (0, 11, 2):
        return None
    if not path.is_file():
        raise ValueError('Release notes module is required')
    module = notes_module(root)
    entries = module.parse((root / 'amplifier_web/release-notes.json').read_text(), expected)
    return module.markdown(entries, expected)


def publish(root, repository, tag, revision, dist):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid release repository")
    if tag != "v" + version_at(root) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Release identity does not match validated checkout")
    if run("git", "rev-parse", "HEAD", cwd=root) != revision:
        raise ValueError("Release commit changed after validation")
    verify_dist(root, dist, tag[1:], repository, revision)
    # Recheck remote immediately before mutation. Tags are never moved or deleted.
    remote = run("git", "ls-remote", "origin", "refs/tags/" + tag, "refs/tags/" + tag + "^{}", cwd=root)
    rows = [line.split() for line in remote.splitlines()]
    resolved = next((row[0] for row in rows if row[1].endswith("^{}")), rows[0][0] if rows else None)
    if resolved is not None and resolved != revision:
        raise ValueError("Remote release tag points elsewhere; refusing to move an immutable tag")
    if resolved is None:
        if not run("git", "tag", "--list", tag, cwd=root):
            run("git", "tag", tag, revision, cwd=root)
        elif run("git", "rev-parse", tag + "^{commit}", cwd=root) != revision:
            raise ValueError("Local release tag points elsewhere")
        run("git", "push", "origin", "refs/tags/" + tag, cwd=root)
    releases = json.loads(run("gh", "release", "list", "--repo", repository, "--limit", "1000", "--json", "tagName,isDraft,isPrerelease"))
    release = next((item for item in releases if item["tagName"] == tag), None)
    if release and release.get("isPrerelease"):
        raise ValueError("Existing release is a prerelease; stable publication requires explicit review")
    if release and not release["isDraft"]:
        print("Release already published; immutable tag and assets left unchanged.")
        return
    notes = release_notes(root, tag[1:])
    notes_file = dist / 'RELEASE_NOTES.md'
    if notes:
        notes_file.write_text(notes)
    if release is None:
        run("gh", "release", "create", tag, "--repo", repository, "--verify-tag", "--draft", "--title",
            "Amplifier Unified " + tag[1:], *(['--notes-file', str(notes_file)] if notes else ['--generate-notes']))
    elif notes:
        run('gh', 'release', 'edit', tag, '--repo', repository, '--notes-file', str(notes_file))
    assets = [*sorted(dist.glob("*.whl")), *sorted(dist.glob("*.tar.gz")), dist / "SHA256SUMS"]
    run("gh", "release", "upload", tag, *map(str, assets), "--repo", repository, "--clobber")
    # Do not make an older repair release latest over an already published newer version.
    newer = any(VERSION.fullmatch(item["tagName"].removeprefix("v"))
                and tuple(map(int, item["tagName"].removeprefix("v").split("."))) > tuple(map(int, tag[1:].split(".")))
                and not item["isDraft"] and not item.get("isPrerelease") for item in releases)
    run("gh", "release", "edit", tag, "--repo", repository, "--draft=false", "--latest=" + str(not newer).lower())
    print("Published " + tag + " at " + revision)


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'In an owned prepared isolated environment, start: '
        'prepare --version X.Y.Z --notes-file /path/to/entry.json --build. '
        'The entry is one JSON object with version, title, changes (a nonempty list), '
        'and optional notices. Then run uv sync --locked --dry-run --group dev --group artifacts. '
        'Coordinate one owner, version and publisher; reuse matching dependencies. '
        'Output names the remaining steps; merging the prepared PR starts existing publication '
        'and installed-package smoke workflows. Preparation never commits or publishes. '
        'See docs/validation/release-qualification.md for version choice and recovery.'
        ' For source-record candidates, stage-source and verify also require '
        '--version X.Y.Z --repository OWNER/REPO --revision FULL_COMMIT_SHA. '
        'The workflow verifies its candidate receipt before staging; the record is not publication proof.'
    ))
    parser.add_argument("command", choices=["prepare", "plan", "stage-source", "verify", "publish"])
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--version")
    parser.add_argument("--notes-file", type=Path, help="One authored release entry as JSON (prepare only)")
    parser.add_argument("--build", action="store_true", help="Run the canonical frontend build in the current prepared environment (prepare only)")
    parser.add_argument("--tag")
    parser.add_argument("--revision", help="Full selected commit SHA (plan, stage-source, verify, publish)")
    parser.add_argument("--fetch-tag", action="store_true", help="Fetch only the exact remote version tag before planning")
    parser.add_argument("--repository", help="Selected GitHub owner/repo (stage-source, verify, publish)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path.cwd()
    if args.command == "prepare":
        if not args.version or not args.notes_file:
            parser.error('prepare requires --version and --notes-file')
        try:
            result = prepare(root, args.version, args.notes_file, args.build)
        except (ValueError, OSError) as exc:
            parser.exit(1, f'Release preparation failed: {exc}\n')
        print(json.dumps(result))
    elif args.command == "plan":
        result = plan(root, args.revision, fetch_tag=args.fetch_tag)
        if args.output:
            with args.output.open("a") as output:
                output.write("".join(f"{key}={value}\n" for key, value in result.items()))
        print(json.dumps(result))
    elif args.command == 'stage-source':
        try:
            result = stage_source(root, args.repository, args.revision, args.version)
        except (ValueError, OSError) as exc:
            parser.exit(1, f'Source record staging failed: {exc}\n')
        print(json.dumps(result))
    elif args.command == "verify":
        try:
            verify_dist(root, args.dist, args.version, args.repository, args.revision)
        except (ValueError, OSError) as exc:
            parser.exit(1, f'Release verification failed: {exc}\n')
    else:
        publish(root, args.repository, args.tag, args.revision, args.dist)


if __name__ == "__main__":
    main()
