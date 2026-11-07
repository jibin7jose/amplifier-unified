"""Explicit navigation resource selection for the bundled browser transport."""
import json
from .adaptive_work import STATUS_RESOURCES

NAVIGATION_RESOURCES = frozenset({
    'chatNavigation', 'homeNavigation', 'pinnedNavigation', 'recentNavigation',
    'workspaceShortcuts', 'workspaceExplorer', 'sidebarSearch', 'sidebarWorkspace',
    'sidebarRecent', 'sidebarPinned', 'workspaceOverview', 'workHome', *STATUS_RESOURCES,
})


def parse_navigation_resources(value):
    """Require a bounded HTTP JSON array before constructing navigation."""
    from .errors import AppError
    if not isinstance(value, str) or len(value) > 4096:
        raise AppError('Invalid navigation resources.', 400)
    try:
        values = json.loads(value)
    except (ValueError, RecursionError):
        raise AppError('Invalid navigation resources.', 400) from None
    if (not isinstance(values, list) or len(values) > 16
            or any(not isinstance(item, str) or item not in NAVIGATION_RESOURCES for item in values)):
        raise AppError('Invalid navigation resources.', 400)
    return frozenset(values)


def resources_for(instance, requested):
    """Existing third-party packages retain their complete snapshot contract."""
    if requested is None or instance['package'] not in {'builtin.chats', 'builtin.workspaces'}:
        return None
    return requested
