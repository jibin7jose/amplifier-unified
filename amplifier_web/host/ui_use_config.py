"""UI-use bundle wiring shared by isolated workers, without server imports."""


def configure_bundle(bundle):
    """Wire capabilities already included by the user's bundle; install nothing."""
    rows = [row for row in bundle.tools if row.get('module') == 'tool-ui-use']
    if not rows:
        return
    for row in rows:
        row.setdefault('config', {}).setdefault('browser', {}).setdefault('host_adapter', True)
    for row in bundle.tools:
        if row.get('module') == 'tool-computer-use':
            row.setdefault('config', {}).setdefault('observed_control', True)
