"""Generated compatibility entrypoints and an editable test view of the client.

The selected component owns dependencies and production code. Unified owns its
integration commands/tests. This view keeps those tests' relative imports useful;
it is disposable, but never silently replaces a developer's experiments.
"""
from __future__ import annotations

import argparse
from contextlib import chdir
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from amplifier_web.reset import command

VIEW_DIRECTORIES = ('src', 'public', '.build-inputs')
VIEW_FILES = ('index.html', 'shell-validation.html', 'vite.config.js',
              'scripts/build-identity.mjs', 'scripts/sync-branding.mjs',
              'scripts/prepare-inputs.mjs', 'build-inputs.lock.json')
VIEW_PATHS = (*VIEW_DIRECTORIES, *VIEW_FILES, '.client-view.json')


def wrapper(root, source):
    package = json.loads((source / 'package.json').read_text())
    package['scripts'] = json.loads((root / 'frontend/commands.json').read_text())
    return (json.dumps(package, indent=2) + '\n').encode()


def verify_wrapper(root, source):
    for name, expected in (
        ('package.json', wrapper(root, source)),
        ('package-lock.json', (source / 'package-lock.json').read_bytes()),
    ):
        if (root / 'frontend' / name).read_bytes() != expected:
            raise ValueError('Regenerate frontend dependency entrypoints with '
                             'python scripts/web_client_development.py manifests')


def manifests(root):
    record = json.loads((root / 'components/web-client.source.json').read_text())
    if record['delivery']['type'] != 'directory':
        raise ValueError('Prepare the selected repository source before generating entrypoints')
    source = root / record['delivery']['path']
    (root / 'frontend/package.json').write_bytes(wrapper(root, source))
    shutil.copyfile(source / 'package-lock.json', root / 'frontend/package-lock.json')


def view_files(source):
    names = list(VIEW_FILES)
    for folder in VIEW_DIRECTORIES:
        names.extend(p.relative_to(source).as_posix() for p in (source / folder).rglob('*')
                     if p.is_file())
    return {name: (source / name).read_bytes() for name in names}


def hashes(files):
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


def path_hashes(frontend, name):
    """Inspect only a managed root; links and unexpected file types are edits."""
    root = frontend / name
    if not root.exists() and not root.is_symlink():
        return {}
    directory = name in VIEW_DIRECTORIES
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(root.lstat().st_mode):
        raise ValueError('Unexpected generated client path retained: ' + str(root))
    result = {}
    for path in root.rglob('*') if directory else [root]:
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError('Unexpected generated client path retained: ' + str(path))
        result[path.relative_to(frontend).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def unchanged_view(frontend, previous):
    observed = {key: value for name in (*VIEW_DIRECTORIES, *VIEW_FILES)
                for key, value in path_hashes(frontend, name).items()}
    for name, digest in previous.items():
        if observed.get(name) != digest:
            raise ValueError('Edited generated client view retained: ' + name +
                             '. Save the experiment in the client repository before refreshing.')
    extra = observed.keys() - previous.keys()
    if extra:
        raise ValueError('Unrecorded client experiment retained: ' + str(frontend / sorted(extra)[0]))


def restore_view(frontend, stage, previous, candidate):
    for name in reversed(VIEW_PATHS):
        backup, target = stage / 'previous' / name, frontend / name
        if not backup.exists() and name in previous:
            continue  # Not moved yet, or already restored.
        if target.exists() or target.is_symlink():
            expected = {key: value for key, value in candidate.items()
                        if key == name or key.startswith(name + '/')}
            if path_hashes(frontend, name) != expected:
                raise ValueError('Changed client files retained during rollback: ' + str(target))
            displaced = stage / 'displaced' / name
            displaced.parent.mkdir(parents=True, exist_ok=True)
            target.rename(displaced)
        if backup.exists():
            backup.rename(target)


def install_view(frontend, files):
    """Swap the fixed managed paths, retaining a recovery location on failure."""
    stage = Path(tempfile.mkdtemp(prefix='.web-client-view-', dir=frontend))
    retain = False
    try:
        ready = stage / 'ready'
        for name in VIEW_DIRECTORIES:
            (ready / name).mkdir(parents=True)
        for name, data in files.items():
            path = ready / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        previous = [name for name in VIEW_PATHS if (frontend / name).exists()]
        candidate = hashes(files)
        (stage / 'recovery.json').write_text(json.dumps({
            'managedPaths': VIEW_PATHS, 'previousPaths': previous, 'candidate': candidate,
            'instructions': 'Previous managed paths are in previous/. Candidate paths not yet installed '
                            'are in ready/. Preserve any newer edits before restoring previous paths; '
                            'frontend/tests, scripts other than the named files, and dependencies are not owned.'
        }, indent=2) + '\n')
        try:
            for name in VIEW_PATHS:
                target, backup = frontend / name, stage / 'previous' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    target.rename(backup)
                (ready / name).rename(target)
        except BaseException:
            try:
                restore_view(frontend, stage, previous, candidate)
            except BaseException as error:
                retain = True
                raise ValueError(f'Restore the previous client view using {stage / "recovery.json"}; '
                                 'its previous and candidate files were retained.') from error
            raise
    finally:
        if not retain:
            shutil.rmtree(stage)


def prepare_view(root, source):
    frontend = root / 'frontend'
    interrupted = sorted(frontend.glob('.web-client-view-*'))
    if interrupted:
        raise ValueError('Unfinished client view refresh retained at ' + str(interrupted[0]) +
                         '. Inspect recovery.json and previous/ before retrying; existing files were not changed.')
    receipt = frontend / '.client-view.json'
    if receipt.is_symlink():
        raise ValueError('Linked client view receipt retained: ' + str(receipt))
    previous = json.loads(receipt.read_text()) if receipt.exists() else {}
    unchanged_view(frontend, previous)
    command('node', source / 'scripts/sync-branding.mjs', timeout=30)
    files = view_files(source)
    current = hashes(files)
    if current == previous:
        return
    files['.client-view.json'] = (json.dumps(current, indent=2) + '\n').encode()
    install_view(frontend, files)


def verify_view(root, source):
    frontend = root / 'frontend'
    expected = hashes(view_files(source))
    if json.loads((frontend / '.client-view.json').read_text()) != expected:
        raise ValueError('Client test view is not prepared from the selected source')
    unchanged_view(frontend, expected)


def development(root, operation):
    frontend = root / 'frontend'
    if not (frontend / '.client-view.json').is_file():
        raise ValueError('Run npm run prepare:view --prefix frontend first')
    command('node', frontend / 'scripts/sync-branding.mjs', timeout=30)
    if operation == 'dev':
        with chdir(frontend):
            command('node', frontend / 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1',
                    timeout=24 * 60 * 60)
        return
    with chdir(frontend):
        command('node', frontend / 'node_modules/vite/bin/vite.js', 'build', timeout=240)
    # Deliberate experiments cannot claim the selected component's asset receipt.
    # The ordinary build restores the exact selected source and its receipt.
    destination = root / 'amplifier_web/static'
    shutil.rmtree(destination)
    shutil.copytree(frontend / 'dist', destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['manifests', 'prepare-view', 'verify-view',
                                            'dev', 'build-development'])
    args = parser.parse_args()
    if args.operation == 'manifests':
        manifests(ROOT)
    elif args.operation in {'dev', 'build-development'}:
        development(ROOT, args.operation)
    else:
        from web_client_build import prepare
        prepare(ROOT)
        source = ROOT / 'frontend/.client'
        if args.operation == 'prepare-view':
            prepare_view(ROOT, source)
        else:
            verify_view(ROOT, source)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
