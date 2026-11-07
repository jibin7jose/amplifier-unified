"""Installed authoring companions must not absorb a contributor's npm output."""
import json
import shutil
import tomllib
import zipfile
from pathlib import Path

from hatchling.builders.wheel import WheelBuilder


def test_shell_authoring_wheel_excludes_local_dependencies_and_builds(tmp_path):
    root = Path(__file__).resolve().parents[1]
    expected = {
        'packages/shell-sdk': {'index.js', 'index.d.ts', 'package.json', 'README.md', 'LICENSE'},
        'examples/shell-controls': {'index.js', 'manifest.json', 'README.md', 'package.json', 'build.mjs'},
    }
    # Exercise the real mappings, including directory mappings if they regress.
    mappings = tomllib.loads((root / 'pyproject.toml').read_text())['tool']['hatch']['build']['targets']['wheel']['force-include']
    selected = {source: target for source, target in mappings.items()
                if any(source == folder or source.startswith(folder + '/') for folder in expected)}
    project = tmp_path / 'project'
    project.mkdir()
    for folder, files in expected.items():
        target = project / folder
        target.mkdir(parents=True)
        for name in files:
            shutil.copyfile(root / folder / name, target / name)
        for name in ['node_modules/dependency/index.js', 'dist/module.mjs', 'local-sdk.tgz', 'package-lock.json']:
            extra = target / name
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text('Local development output must not ship')
    (project / 'pyproject.toml').write_text(
        '[project]\nname = "shell-authoring-probe"\nversion = "0.0.0"\n'
        '[tool.hatch.build.targets.wheel]\nbypass-selection = true\n'
        '[tool.hatch.build.targets.wheel.force-include]\n' +
        ''.join(f'{json.dumps(source)} = {json.dumps(target)}\n' for source, target in selected.items())
    )
    [wheel] = WheelBuilder(str(project)).build(directory=str(tmp_path), versions=['standard'])
    with zipfile.ZipFile(tmp_path / wheel) as archive:
        for folder, files in expected.items():
            prefix = 'amplifier_web/bundle_data/' + folder + '/'
            assert {name[len(prefix):] for name in archive.namelist() if name.startswith(prefix)} == files
            for name in files:
                assert archive.read(prefix + name) == (root / folder / name).read_bytes()
