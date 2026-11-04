"""Copy saved file locations on the app host, without opening or reading files."""
from pathlib import Path

from .shell_modules import fail


def paths(state, canvas):
    path = Path(canvas.get('path') or '')
    if not path.is_absolute():
        return {}
    result = {'absolute': canvas['path']}
    owner = next((row for row in state.get('sessions', []) if row['id'] == canvas.get('sessionId')), None)
    # A saved path is historical evidence, but a relative path is useful only
    # against the conversation's current execution folder. Never rebase the
    # absolute path or consult the filesystem to reconstruct it.
    workspace = owner.get('workspace') if owner is not None else canvas.get('workspacePath')
    root = (owner.get('workingDirectory') or workspace) if owner is not None else workspace
    if workspace and Path(workspace).is_absolute():
        result['workspace'] = workspace
    if root and Path(root).is_absolute():
        result['base'] = root
        if '..' not in path.parts and '..' not in Path(root).parts:
            try:
                result['relative'] = str(path.relative_to(root))
            except ValueError:
                pass
    return result


def copy_path(state, args):
    canvas = state.get('canvas', {})
    if canvas.get('id') != args['id']:
        fail('This canvas has been replaced.', 409)
    value = paths(state, canvas).get(args['format'])
    if value is None:
        fail('This artifact has no saved ' + args['format'] + ' file path.')
    return {'path': value, 'host': 'server'}, [{'type': 'clipboard.write', 'content': value, 'canvasId': canvas['id']}]
