"""Pinned execution tenant for the private native gateway protocol (not profile isolation)."""
import json
import re
import uuid


class NativeTenant:
    def __init__(self, identity, home):
        if not isinstance(identity, dict):
            raise ValueError('Native tenant must be an object')
        fields = {'account': {'kind', 'accountId'}, 'team': {'kind', 'organizationId', 'computerId'}}
        kind = identity.get('kind')
        if kind not in fields or set(identity) != fields[kind]:
            raise ValueError('Invalid native tenant fields')
        if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) for key, value in identity.items() if key != 'kind'):
            raise ValueError('Invalid native tenant identifier')
        marker = home / '.native-tenant.json'
        if marker.is_symlink():
            raise ValueError('Unsafe native tenant marker')
        if marker.exists() and json.loads(marker.read_text()) != identity:
            raise ValueError('Checkpoint belongs to a different execution tenant')
        marker.write_text(json.dumps(identity, sort_keys=True) + '\n')
        marker.chmod(0o600)
        self.identity = dict(identity)
        self.generation = 'rt-' + uuid.uuid4().hex
        self.prefix = ('/users/' + identity['accountId'] if kind == 'account' else
                       '/organizations/' + identity['organizationId'] + '/computers/' + identity['computerId'])

    def route(self, path, method, actor, generation, query):
        """Called after private API bearer authentication. Team membership remains API-owned."""
        if not isinstance(actor, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', actor):
            return 403, None
        if self.identity['kind'] == 'account' and actor != self.identity['accountId']:
            return 403, None
        if query or not path.startswith(self.prefix + '/'):
            return 404, None
        if path == self.prefix + '/dashboard' and method == 'GET':
            return 200, None
        if generation != self.generation:
            return 409, None
        if path == self.prefix + '/runtime/socket' and method == 'GET':
            return 200, '/api/ws'
        # Native helpers must be explicitly integrated; never forward arbitrary host paths.
        return 501, None
