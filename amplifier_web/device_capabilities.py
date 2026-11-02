"""Small domain registry for the optional connection protocol.

The delivery service depends on these public schemas, never on a browser,
native app, model provider or an agent runtime.
"""
from aiohttp import web

GRANTS = {
    'connection.ping': 'Check this app’s connection',
    'browser.observe': 'Read the browser document you choose in the extension',
    'browser.task': 'Read and operate one locally selected tab across website navigation until you stop it',
    'browser.act': 'Click and type in that chosen browser document',
}
ACTIONS = {
    'connection.ping': ('connection.ping', {}),
    'browser.observe': ('browser.observe', {}),
    'browser.task.observe': ('browser.task', {}),
    'browser.task.open': ('browser.task', {'url':4000}),
    'browser.task.act': ('browser.task', {'revision':120, 'candidate':120, 'text':10000}),
    'browser.task.screenshot': ('browser.task', {}),
    'browser.click': ('browser.act', {'selector': 500}),
    'browser.type': ('browser.act', {'selector': 500, 'text': 4000}),
}
# Domain-owned retention policy. These actions have no input/navigation effect;
# bounded, expiring receipts are sufficient and must not consume write history.
TRANSIENT_ACTIONS = frozenset({
    'connection.ping', 'browser.observe', 'browser.task.observe', 'browser.task.screenshot',
})


def transient(value):
    return value in TRANSIENT_ACTIONS


def grants(value):
    if (not isinstance(value, list) or not value or len(value) > len(GRANTS)
            or any(not isinstance(item, str) or item not in GRANTS for item in value)):
        raise web.HTTPBadRequest(text='Choose supported access for this connection.')
    result = sorted(set(value) | {'connection.ping'})
    if ('browser.act' in result or 'browser.task' in result) and 'browser.observe' not in result:
        raise web.HTTPBadRequest(text='Browser control also requires permission to observe it.')
    return result


def action(value, args):
    if not isinstance(value, str) or value not in ACTIONS or not isinstance(args, dict):
        raise web.HTTPBadRequest(text='Unsupported device action.')
    permission, fields = ACTIONS[value]
    if set(args) != set(fields):
        raise web.HTTPBadRequest(text='Invalid device action arguments.')
    for key, limit in fields.items():
        if not isinstance(args[key], str) or len(args[key]) > limit or (key == 'selector' and not args[key].strip()):
            raise web.HTTPBadRequest(text='Invalid device action argument: ' + key)
    return permission
