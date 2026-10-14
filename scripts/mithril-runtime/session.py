#!/usr/bin/env python3
"""Run one API-defined Mithril Agent session on a local or saved SSH Docker host."""
import argparse
import hashlib
import io
import json
import os
import re
import secrets
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import tarfile
import urllib.error
import urllib.request
import uuid

API = 'https://api.mithril.fund'
FILES = ('config.yaml', 'SOUL.md', 'memories/USER.md')
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
IMAGE = re.compile(r'(?:[a-zA-Z0-9][a-zA-Z0-9./:_-]*@)?sha256:[a-f0-9]{64}\Z')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Profile API redirects are refused')

def validate_profile(value, profile):
    required = {'schemaVersion', 'format', 'userId', 'profileId', 'name', 'revision', 'datasetGeneration', 'digest', 'files'}
    if not isinstance(value, dict) or set(value) != required or value.get('schemaVersion') != 1 or value['format'] != 'mithril-agent-profile/v1':
        raise ValueError('Unsupported Agent profile')
    if value['profileId'] != profile or not IDENTIFIER.fullmatch(str(value['userId'])):
        raise ValueError('Agent profile identity mismatch')
    for field, minimum in [('revision', 1), ('datasetGeneration', 0)]:
        if type(value[field]) is not int or not minimum <= value[field] <= 9007199254740991:
            raise ValueError('Invalid Agent profile revision')
    if not isinstance(value['name'], str) or not value['name'].strip() or len(value['name']) > 200: raise ValueError('Invalid Agent profile name')
    files = value['files']
    if not isinstance(files, dict) or set(files) != set(FILES) or any(not isinstance(files[p], str) for p in FILES):
        raise ValueError('Unsupported Agent profile files')
    body = {key: value[key] for key in ['schemaVersion', 'format', 'userId', 'profileId', 'name', 'revision', 'datasetGeneration']}
    body['files'] = {key: files[key] for key in FILES}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if value['digest'] != digest:
        raise ValueError('Agent profile integrity mismatch')
    config = json.loads(files['config.yaml'])
    if not isinstance(config, dict) or set(config) - {'model', 'custom_providers', 'display', 'terminal'} or config.get('display') != {'skin': 'mithril'} or config.get('terminal') != {'backend': 'local', 'cwd': '/workspace', 'home_mode': 'profile'}:
        raise ValueError('Unsupported Agent runtime configuration')
    if config.get('custom_providers') != [{'name': 'Mithril', 'base_url': API + '/v1', 'key_env': 'MITHRIL_API_KEY'}]:
        raise ValueError('Unsupported Agent inference endpoint')
    if 'model' not in config:
        raise ValueError('Select an explicit model for the API Profile before starting a session')
    if (not isinstance(config['model'], dict) or set(config['model']) != {'default', 'provider'} or config['model'].get('provider') != 'Mithril' or not isinstance(config['model']['default'], str) or not config['model']['default']):
        raise ValueError('Invalid Agent model')
    return value

def fetch_profile(profile):
    token = os.environ.get('MITHRIL_API_KEY', '')
    if not token or '\n' in token or '\r' in token:
        raise ValueError('Set MITHRIL_API_KEY with workspace:read authorization')
    request = urllib.request.Request(f'{API}/v1/agent/profiles/{profile}', headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json', 'Cache-Control': 'no-cache'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
        raw = response.read(131073)
        if len(raw) > 131072:
            raise ValueError('Agent profile exceeds supported size')
    return validate_profile(json.loads(raw), profile)

CURATE_ENV = r"""import os,re,sys,tempfile,json,urllib.request
from pathlib import Path
with open(sys.argv[1],'rb') as f: raw=f.read(1048577)
if len(raw)>1048576: raise ValueError('Runtime environment file is too large')
values=[]
for line in raw.decode().splitlines():
 m=re.fullmatch(r'\s*(?:export\s+)?MITHRIL_API_KEY\s*=\s*(.*?)\s*',line)
 if m:
  value=m.group(1).strip()
  if value[:1] in (chr(34),chr(39)) and value[-1:]==value[:1]: value=value[1:-1]
  if not re.fullmatch(r'[A-Za-z0-9_-]{16,256}',value): raise ValueError('Invalid runtime credential')
  values.append(value)
if len(values)!=1: raise ValueError('Choose a file with one Mithril inference credential')
if len(sys.argv)!=3: raise ValueError('Runtime account identity is required')
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None
opener=urllib.request.build_opener(NoRedirect())
request=urllib.request.Request('https://api.mithril.fund/v1/me',headers={'Authorization':'Bearer '+values[0],'Cache-Control':'no-store'})
with opener.open(request,timeout=15) as response: identity_raw=response.read(131073)
if len(identity_raw)>131072: raise ValueError('Runtime account response is too large')
identity=json.loads(identity_raw)
if identity.get('user',{}).get('id')!=sys.argv[2] or not isinstance(identity.get('scopes'),list) or 'inference' not in identity['scopes']: raise ValueError('Choose an inference credential for the signed-in Profile account')
fd,path=tempfile.mkstemp(prefix='mithril-runtime-',suffix='.env',dir='/private/tmp' if sys.platform=='darwin' else None)
try:
 os.fchmod(fd,0o600)
 with os.fdopen(fd,'w') as f: f.write('MITHRIL_API_KEY='+values[0]+'\n')
 print(path)
except BaseException:
 os.unlink(path); raise
"""

class Runner:
    def __init__(self, host=None, port=22, key_path=None):
        if host and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}', host):
            raise ValueError('Use a saved SSH host without command options')
        if type(port) is not int or not 1 <= port <= 65535: raise ValueError('Invalid SSH port')
        if key_path and (not Path(key_path).is_absolute() or not Path(key_path).is_file()): raise ValueError('Configured SSH key is unavailable')
        self.host, self.port, self.key_path = host, port, key_path
    def command(self, args):
        command = ['docker', *args]
        return ['ssh', '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', '-p', str(self.port), *(['-i', self.key_path] if self.key_path else []), self.host, shlex.join(command)] if self.host else command
    def control(self, code, value):
        command = ['python3', '-c', code, value]
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', '-p', str(self.port), *(['-i', self.key_path] if self.key_path else []), self.host, shlex.join(command)] if self.host else command
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        if result.returncode: raise ValueError('Runtime credential binding could not be prepared')
        return result.stdout.decode().strip()
    def call(self, args, data=None, capture=True):
        result = subprocess.run(self.command(args), input=data, stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None, timeout=120)
        if result.returncode:
            # Docker stderr may contain environment values; never return it to the UI.
            raise ValueError('Container operation failed; existing session state was retained')
        return result.stdout or b''

def profile_archive(profile, session_id, image, host, dashboard=False):
    metadata = {key: profile[key] for key in ['userId', 'profileId', 'name', 'revision', 'datasetGeneration', 'digest']}
    metadata.update({'sessionId': session_id, 'image': image, 'runner': 'cloud' if host else 'local', 'host': host})
    additions = {'mithril-session.json': json.dumps(metadata)}
    if dashboard:
        additions['mithril-backend.py'] = Path(__file__).with_name('backend.py').read_text()
        additions['dashboard-session.token'] = secrets.token_urlsafe(32) + '\n'
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode='w') as archive:
        for path, content in {**profile['files'], **additions}.items():
            data = content.encode()
            entry = tarfile.TarInfo(path)
            entry.size, entry.mode, entry.uid, entry.gid = len(data), 0o600, 10000, 10000
            archive.addfile(entry, io.BytesIO(data))
    return payload.getvalue(), metadata

INIT = """import os,sys,tarfile
os.umask(0o077)
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as t:
 for m in t:
  if m.name not in ('config.yaml','SOUL.md','memories/USER.md','mithril-session.json','mithril-backend.py','dashboard-session.token') or not m.isfile() or m.size>131072: raise ValueError('Invalid profile input')
  p='/opt/data/'+m.name
  os.makedirs(os.path.dirname(p),mode=0o700,exist_ok=True)
  with open(p,'xb') as f: f.write(t.extractfile(m).read())
  os.chmod(p,0o600)
  os.chown(p,10000,10000)
  if '/' in m.name: os.chown(os.path.dirname(p),10000,10000)
os.chown('/opt/data',10000,10000)
os.chown('/workspace',10000,10000)
"""

def launch(profile, image, session_id, runner, interactive=True, runtime_env_file=None, dashboard=False):
    if not IMAGE.fullmatch(image) or not IDENTIFIER.fullmatch(session_id):
        raise ValueError('An immutable image digest and valid session ID are required')
    if runtime_env_file and (not runtime_env_file.startswith('/') or any(c in runtime_env_file for c in '\n\r\x00')):
        raise ValueError('Use an existing absolute runtime environment file on the selected host')
    namespace = hashlib.sha256(json.dumps([profile['userId'], session_id], separators=(',', ':')).encode()).hexdigest()[:32]
    name = 'mithril-session-' + namespace
    data_volume, work_volume = name + '-data', name + '-workspace'
    runner.call(['image', 'inspect', image])
    # Never reuse a running writer or silently overwrite a volume from an earlier attempt.
    existing = runner.call(['volume', 'ls', '--filter', f'name={name}', '--format', '{{.Name}}']).decode().splitlines()
    if data_volume in existing or work_volume in existing:
        raise ValueError('Session already exists; resume its container instead of overwriting it')
    claimed = False
    curated = None
    try:
        if runtime_env_file: curated = runner.control(CURATE_ENV, runtime_env_file, profile['userId'])
        if curated and (not curated.startswith('/') or '\n' in curated or not Path(curated).name.startswith('mithril-runtime-')): raise ValueError('Invalid runtime credential binding')
        mounts = ['--mount', f'type=volume,src={data_volume},dst=/opt/data,volume-nocopy', '--mount', f'type=volume,src={work_volume},dst=/workspace,volume-nocopy']
        archive, metadata = profile_archive(profile, session_id, image, runner.host, dashboard)
        args = ['create', '-i', *(['-t'] if interactive else []), '--name', name, *(['--env-file', curated] if curated else []), '--label', 'fund.mithril.session=' + session_id, '--label', 'fund.mithril.profile=' + profile['profileId'], '--label', 'fund.mithril.profile-revision=' + str(profile['revision']), '--user=10000:10000', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2', '--memory=4g', '--pids-limit=256', '--tmpfs', '/tmp:rw,exec,size=1g', '--env', 'HERMES_HOME=/opt/data', '--env', 'HOME=/opt/data/home', '--env', 'PYTHONDONTWRITEBYTECODE=1', '--workdir', '/workspace', *(['--publish', '127.0.0.1::8864', '--entrypoint=python3'] if dashboard else ['--entrypoint=/opt/hermes/bin/hermes']), *mounts, image, *(['/opt/data/mithril-backend.py'] if dashboard else ['chat'])]
        # Docker atomically reserves the container name before any profile write.
        runner.call(args)
        claimed = True
        runner.call(['run', '--rm', '-i', '--network=none', '--read-only', '--cap-drop=ALL', '--cap-add=CHOWN', '--user=0:0', '--entrypoint=python3', *mounts, image, '-c', INIT], archive)
    except Exception:
        # Only newly created volumes, before a chat can execute, are removable.
        if claimed:
            try: runner.call(['rm', name])
            except Exception: raise
            for volume in [work_volume, data_volume]:
                try: runner.call(['volume', 'rm', volume])
                except Exception: pass
        raise
    finally:
        if curated: runner.control("import os,sys; os.unlink(sys.argv[1])", curated)
    print(json.dumps({**metadata, 'container': name}), flush=True)
    return name

def main():
    parser = argparse.ArgumentParser(description='Start an API-defined Mithril Agent container session')
    parser.add_argument('--profile', required=True)
    parser.add_argument('--profile-stdin', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--dashboard', action='store_true', help='Prepare the authenticated Desktop session backend')
    parser.add_argument('--image', required=True, help='Qualified Mithril-compatible image@sha256:digest or image ID')
    parser.add_argument('--session', default=str(uuid.uuid4()))
    parser.add_argument('--ssh-port', type=int, default=22)
    parser.add_argument('--ssh-key-path')
    parser.add_argument('--ssh-host', help='Existing saved SSH host; its Docker daemon owns the cloud session')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--runtime-env-file', help='Existing provider environment file on the selected host; no API credential is copied automatically')
    args = parser.parse_args()
    if not IDENTIFIER.fullmatch(args.profile): raise ValueError('Invalid Agent profile ID')
    if not args.prepare_only and not args.runtime_env_file:
        raise ValueError('Choose an existing runtime environment file before starting inference')
    if args.profile_stdin:
        raw = sys.stdin.buffer.read(131073)
        if len(raw) > 131072: raise ValueError('Agent profile exceeds supported size')
        profile = validate_profile(json.loads(raw), args.profile)
    else:
        profile = fetch_profile(args.profile)
    runner = Runner(args.ssh_host, args.ssh_port, args.ssh_key_path)
    name = launch(profile, args.image, args.session, runner, sys.stdin.isatty() and sys.stdout.isatty(), args.runtime_env_file, args.dashboard)
    if not args.prepare_only:
        return subprocess.call(runner.command(['start', '-ai', name]))
    return 0

if __name__ == '__main__':
    try: sys.exit(main())
    except ValueError as error:
        print('Mithril Agent: ' + str(error), file=sys.stderr)
        sys.exit(1)
    except (urllib.error.URLError, subprocess.TimeoutExpired):
        print('Mithril Agent: session setup could not be confirmed; no chat was replayed.', file=sys.stderr)
        sys.exit(1)
