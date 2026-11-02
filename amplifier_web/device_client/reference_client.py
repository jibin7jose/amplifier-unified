#!/usr/bin/env python3
"""Independent connection-v1 client. Python 3.10+; no Amplifier imports."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

PREFIX = '/api/connections/v1'


class Failure(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--name', default='Reference client on ' + socket.gethostname())
    parser.add_argument('--state', type=Path)
    parser.add_argument('--ca', help='Explicitly trusted CA certificate for a private host')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--rotate', action='store_true')
    args = parser.parse_args()
    origin = args.host.rstrip('/')
    url = urllib.parse.urlsplit(origin)
    if (url.username or url.password or url.path or url.query or url.fragment or not url.hostname
            or not (url.scheme == 'https' or (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'}))):
        parser.error('Supply a trusted HTTPS origin, or loopback HTTP for development.')
    path = args.state or Path.home() / '.local/state/amplifier-connect' / (hashlib.sha256(origin.encode()).hexdigest()[:16] + '.json')
    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=args.ca)))
    state = json.loads(path.read_text()) if path.exists() else {'host': origin}
    if state['host'] != origin:
        parser.error('The credential file belongs to a different host.')

    def api(suffix='', payload=None, authenticated=True):
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer ' + state['token']
        request = urllib.request.Request(origin + PREFIX + suffix,
            data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            with opener.open(request, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise Failure(exc.code, exc.read(4096).decode(errors='replace')) from None

    if 'token' not in state:
        discovery = api(authenticated=False)
        if discovery.get('protocolVersion') != 1:
            raise Failure(409, 'This host does not support connection-v1.')
        verifier = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        pending = api('/enroll', {'name': args.name, 'appId': 'reference-python-v1',
                                  'challenge': challenge, 'grants': ['connection.ping']}, authenticated=False)
        verification = pending['verificationPath']
        if not verification.startswith('/connect/verify?id=') or '?' not in verification or '://' in verification:
            raise Failure(400, 'Unexpected approval destination.')
        print(json.dumps({'status': 'approval_required', 'code': pending['code'], 'url': origin + verification}), flush=True)
        if not args.no_browser:
            webbrowser.open(origin + verification)
        deadline = time.monotonic() + min(pending['expiresIn'], 600)
        while time.monotonic() < deadline:
            result = api('/token', {'id': pending['id'], 'verifier': verifier}, authenticated=False)
            if result['status'] == 'approved':
                if result.get('scope') != 'device-v1' or not result['token'].startswith('amd_'):
                    raise Failure(400, 'The host returned an unexpected credential.')
                state.update(token=result['token'], deviceID=result['deviceID'])
                save(path, state)
                break
            if result['status'] != 'pending':
                raise Failure(403, 'Connection ' + result['status'])
            time.sleep(max(5, min(pending['interval'], 30)))
        else:
            raise Failure(410, 'Approval expired. Start again explicitly.')

    if args.rotate and 'rotationId' not in state:
        state['rotationId'] = str(uuid.uuid4())
        save(path, state)
    if 'rotationId' in state:
        rotated = api('/self/rotate', {'rotationId': state['rotationId']})
        state['token'] = rotated['token']
        del state['rotationId']
        save(path, state)
    api('/self')
    instance = str(uuid.uuid4())
    targets = [{'id': 'connection', 'revision': instance, 'label': 'Reference client connection',
                'capabilities': ['connection.ping'], 'ready': True}]
    announced = False
    while True:
        try:
            api('/self/heartbeat', {'instanceId': instance, 'targets': targets})
            if not announced:
                print(json.dumps({'status': 'connected', 'deviceID': state['deviceID'], 'capability': 'connection.ping'}), flush=True)
                announced = True
            delivery = api('/self/poll', {})
            operation = delivery.get('operation')
            if operation:
                status, result = 'failed', {'message': 'Unsupported or expired operation.'}
                if (operation['action'] == 'connection.ping' and operation['revision'] == instance
                        and operation['deadline'] > time.time()):
                    status, result = 'completed', {'message': 'Connection confirmed by the independent Python client.'}
                # Retry only this receipt; never the effect. Server redelivery is forbidden.
                receipt = {'id': operation['id'], 'lease': operation['lease'], 'status': status, 'result': result}
                for attempt in range(3):
                    try:
                        api('/self/result', receipt)
                        break
                    except Failure as exc:
                        if operation.get('retention') == 'transient' and exc.status in {404, 410}:
                            break  # The read expired; continue without executing it again.
                        raise
                    except (urllib.error.URLError, TimeoutError):
                        if attempt == 2:
                            raise
                        time.sleep(1)
        except Failure:
            raise
        except (urllib.error.URLError, TimeoutError):
            announced = False
            print(json.dumps({'status': 'offline', 'retryInSeconds': 5}), flush=True)
            time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Client stopped; the host will mark this connection offline.')
    except (Failure, OSError, ValueError) as exc:
        print('Connection stopped: ' + str(exc), file=sys.stderr)
        sys.exit(1)
