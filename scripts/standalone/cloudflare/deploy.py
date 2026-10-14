"""Deploy an admitted immutable image to the isolated Cloudflare shared trial."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
ACCOUNT = '62e1fda53188460698f8c66d3aef59c6'
NAME = 'mithril-native-shared-trial'


def bootstrap_rollout(previous, headers, token, opener=urlopen):
    """Replace only a read-back bootstrap that Fund has never activated."""
    state = json.loads(previous.read_text())
    if state.get('worker') != 'mithril-hermes-runtime' or state.get('fundActivation') != 'disabled' or state.get('phase') not in {'secrets-publication-outcome-unknown', 'bootstrap-live-billing-disabled'}:
        raise ValueError('Previous runtime is not a disabled bootstrap')
    def read(url, authenticated=False):
        with opener(Request(url, headers={'Authorization': 'Bearer ' + token} if authenticated else headers), timeout=30) as response:
            body = json.load(response)
            if response.status != 200:
                raise ValueError('Bootstrap read-back failed')
            if authenticated:
                if body.get('success') is not True:
                    raise ValueError('Bootstrap provider read-back failed')
                return body['result']
            return body
    base = 'https://api.cloudflare.com/client/v4/accounts/' + ACCOUNT
    script = base + '/workers/scripts/mithril-hermes-runtime'
    health = read('https://mithril-hermes-runtime.cloud-kotoba.workers.dev/health')
    settings = read(script + '/settings', True)
    bindings = {row['name']: row for row in settings['bindings']}
    if health.get('service') != 'mithril-native-gateway' or health.get('sourceSha') != state.get('sourceSha') or bindings.get('SOURCE_SHA', {}).get('text') != state.get('sourceSha'):
        raise ValueError('Previous bootstrap source differs')
    fund = read(base + '/workers/scripts/mithril-api/settings', True)
    if any(row['name'].startswith('MITHRIL_NATIVE_') for row in fund['bindings']):
        raise ValueError('Fund native runtime is already activated')
    deployment = read(script + '/deployments', True)['deployments'][0]
    versions = deployment['versions']
    if len(versions) != 1 or versions[0]['percentage'] != 100:
        raise ValueError('Concrete sole bootstrap rollback required')
    apps = read(base + '/containers/dash/applications?per_page=100', True)
    selected = [row for row in apps if row.get('name') == 'mithril-hermes-runtime-managednativegateway']
    if len(selected) != 1 or len(apps) >= 100:
        raise ValueError('Bootstrap application inventory differs')
    app = selected[0]
    instances = app.get('health', {}).get('instances', {})
    if app.get('image') != state.get('cloudflareImage') or app.get('durable_objects', {}).get('namespace_id') != bindings.get('GATEWAY', {}).get('namespace_id') or any(instances.get(name) != 0 for name in ['active', 'assigned', 'starting', 'scheduling']):
        raise ValueError('Bootstrap has active compute or changed ownership')
    return {'rollbackVersion': versions[0]['version_id'], 'previousSource': state['sourceSha'],
            'previousImage': state['cloudflareImage'], 'applicationId': app['id'],
            'namespaceId': bindings['GATEWAY']['namespace_id'], 'fundActivation': 'disabled'}


def publication_secrets(value, managed):
    required = {'SERVICE_KEY', 'SETTLEMENT_KEY', 'CF_ANALYTICS_READ_TOKEN'} if managed else {'SERVICE_KEY', 'RUNTIME_REGISTRY'}
    if not isinstance(value, dict) or set(value) != required or any(not isinstance(v, str) or len(v) < 32 for v in value.values()):
        raise ValueError('Dedicated runtime secrets required')
    if managed and len(set(value.values())) != len(required):
        raise ValueError('Runtime credential purposes must remain distinct')
    return value


def qualify_analytics(token, opener=urlopen):
    """Read the fixed Fund account; empty usage is valid, another account is not."""
    end = datetime.now(timezone.utc)
    query = ('query Usage($start:Time,$end:Time,$label:String) { viewer { accounts(filter:{accountTag:"' + ACCOUNT + '"}) '
             '{ containersUsageAdaptiveGroups(limit:1,filter:{datetime_geq:$start,datetime_lt:$end,labels_has:$label}) '
             '{ sum { cpuTimeSec allocatedMemory allocatedDisk } } } } }')
    variables = {'start': (end - timedelta(hours=1)).isoformat(), 'end': end.isoformat(),
                 'label': 'mithril_job=native-launch:00000000-0000-4000-8000-000000000000'}
    request = Request('https://api.cloudflare.com/client/v4/graphql', method='POST',
                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                      data=json.dumps({'query': query, 'variables': variables}).encode())
    try:
        with opener(request, timeout=30) as response:
            body = json.load(response)
            accounts = (body.get('data') or {}).get('viewer', {}).get('accounts')
            if response.status != 200 or body.get('errors') or not isinstance(accounts, list) or len(accounts) != 1 or not isinstance(accounts[0].get('containersUsageAdaptiveGroups'), list):
                raise ValueError('Invalid analytics qualification response')
    except Exception:
        # Provider exceptions and reflected errors must not expose credentials.
        raise ValueError('Dedicated Cloudflare Analytics read qualification failed') from None


def main(managed=False):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ['state', 'receipt', 'secrets', 'wrangler', 'output']:
        parser.add_argument('--' + option, required=True, type=Path)
    parser.add_argument('--bootstrap-from', type=Path)
    args = parser.parse_args()
    for path in [args.secrets, args.state / 'owner.json', args.receipt]:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Owner-only configuration required')
    if args.bootstrap_from:
        info = args.bootstrap_from.lstat()
        if not managed or args.bootstrap_from.is_symlink() or not args.bootstrap_from.is_file() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Owner-only disabled bootstrap record required')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip():
        raise ValueError('Clean exact source required')
    publication_name = 'mithril-hermes-runtime' if managed else NAME
    if managed:
        sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        main_sha = subprocess.check_output(['git', 'ls-remote', 'origin', 'refs/heads/main'], cwd=ROOT, text=True).split()[0]
        if sha != main_sha:
            raise ValueError('Clean integrated current main is required')
    admitted = json.loads(subprocess.check_output(['node', 'scripts/standalone/gateway.mjs', 'admit', *(['--profile', 'managed'] if managed else []), '--state', str(args.state), '--owner', str(args.state / 'owner.json'), '--receipt', str(args.receipt)], cwd=ROOT))
    secrets = publication_secrets(json.loads(args.secrets.read_text()), managed)
    if managed:
        qualify_analytics(secrets['CF_ANALYTICS_READ_TOKEN'])
    validation = "import {registry} from './scripts/standalone/cloudflare/policy.mjs'; let input=''; for await (const chunk of process.stdin) input+=chunk; const secret=JSON.parse(input); const rows=registry(secret.RUNTIME_REGISTRY); if(Object.keys(rows).length<1 || Object.keys(rows).length>2 || Object.values(rows).some(row=>row.apiKey===secret.SERVICE_KEY || Object.hasOwn(row.members||{},secret.SERVICE_KEY))) throw Error('Invalid isolated trial access');"
    if not managed:
        subprocess.run(['node', '--input-type=module', '-e', validation], input=json.dumps(secrets).encode(), cwd=ROOT, check=True)
    origin = 'https://' + publication_name + '.cloud-kotoba.workers.dev'
    headers = {'User-Agent': 'Hermes-Native-Shared-Trial/1.0', 'Authorization': 'Bearer ' + secrets['SERVICE_KEY']}
    try:
        with urlopen(Request(origin + '/health', headers=headers), timeout=30) as response:
            exists = response.status == 200
    except HTTPError as error:
        if error.code != 404:
            raise ValueError('Cannot establish the previous runtime deployment state') from None
        exists = False
    bootstrap = None
    if managed and exists and args.bootstrap_from:
        bootstrap = bootstrap_rollout(args.bootstrap_from, headers, os.environ['CLOUDFLARE_API_TOKEN'])
    if managed and exists and not bootstrap:
        raise ValueError('Existing managed Worker requires a drained rollout and concrete rollback owner; no live replacement attempted')
    if exists and not managed:
        # An immediate image rollout must not interrupt a live connection or
        # replace writable state before its encrypted checkpoint is acknowledged.
        for row in json.loads(secrets['RUNTIME_REGISTRY']).values():
            identity = row['identity']
            prefix = '/users/' + identity['accountId'] if identity['kind'] == 'account' else '/organizations/' + identity['organizationId'] + '/computers/' + identity['computerId']
            actor = identity.get('accountId', 'control-plane')
            try:
                with urlopen(Request(origin + prefix + '/sleep', method='POST', headers={**headers, 'X-Mithril-Actor-Id': actor}), timeout=180) as response:
                    stopped = json.load(response)
                    if response.status != 200 or stopped.get('running') is not False:
                        raise ValueError('Previous runtime did not acknowledge sleep')
            except HTTPError:
                raise ValueError('Previous runtime is busy, unauthorized, or unable to checkpoint') from None
    image = admitted['imageId']
    owner = json.loads((args.state / 'owner.json').read_text())
    host = 'ssh://gad' + owner.get('socket', '') if owner['runner'] == 'gad' else 'unix://' + owner['socket'] if owner.get('socket') else None
    docker = ['docker'] + (['--host', host] if host else [])
    info = json.loads(subprocess.check_output([*docker, 'image', 'inspect', image]))[0]
    if info['Architecture'] != 'amd64' or info['Config']['Labels']['org.opencontainers.image.revision'] != admitted['sha'] or info['Config']['User'] != '1000:1000':
        raise ValueError('Image provenance mismatch')
    env = {**os.environ, 'WRANGLER_SEND_METRICS': 'false'}
    if host:
        env['DOCKER_HOST'] = host
    who = subprocess.check_output([str(args.wrangler), 'whoami'], env=env, cwd=ROOT, text=True)
    if ACCOUNT not in who:
        raise ValueError('Cloudflare account is unavailable')
    args.output.mkdir(parents=True, exist_ok=True)
    journal = args.output / 'publication-state.json'
    if managed and journal.exists():
        raise ValueError('A publication outcome already exists; reconcile it before another attempt')
    def save_phase(phase, **details):
        if managed:
            journal.write_text(json.dumps({'sourceSha': admitted['sha'], 'worker': publication_name,
                'phase': phase, 'fundActivation': 'disabled', **details}, indent=2) + '\n')
            journal.chmod(0o600)
    if managed and exists and not bootstrap:
        raise ValueError('Managed rollout requires independently confirmed drained tenancy')
    if bootstrap:
        (args.output / 'bootstrap-rollback.json').write_text(json.dumps(bootstrap, indent=2) + '\n')
    # Preserve any older deployment version for a concrete rollback.
    subprocess.run([str(args.wrangler), 'deployments', 'list', '--name', publication_name], cwd=ROOT, env=env, stdout=(args.output / 'previous-deployments.log').open('w'), stderr=subprocess.STDOUT, check=False)
    with tempfile.TemporaryDirectory(prefix='native-shared-publish-') as scratch:
        path = Path(scratch)
        config_home = path / 'docker'
        config_home.mkdir(mode=0o700)
        env['DOCKER_CONFIG'] = str(config_home)
        tag = publication_name + ':' + admitted['sha'][:12]
        subprocess.run([*docker, 'tag', image, tag], check=True)
        # Dedicated account configuration; no existing gateway is redeployed.
        config = {'name': publication_name, 'account_id': ACCOUNT, 'main': str(ROOT / 'scripts/standalone/cloudflare/worker.mjs'), 'compatibility_date': '2026-10-10', 'workers_dev': True, 'vars': {'SOURCE_SHA': admitted['sha'], 'PUBLIC_ORIGIN': 'https://' + publication_name + '.cloud-kotoba.workers.dev', 'IDLE_MS': '120000'}, 'containers': [{'class_name': 'NativeSharedGateway', 'scheduling_policy': 'default', 'image': 'registry.cloudflare.com/' + ACCOUNT + '/' + tag, 'instance_type': 'standard-1', 'max_instances': 2}], 'durable_objects': {'bindings': [{'name': 'GATEWAY', 'class_name': 'NativeSharedGateway'}]}, 'exports': {'NativeSharedGateway': {'type': 'durable-object', 'storage': 'sqlite'}}, 'r2_buckets': [{'binding': 'STATE', 'bucket_name': 'mithril-hermes-gateway-trial-state'}]}
        if managed:
            config['main'] = str(ROOT / 'scripts/standalone/cloudflare/managed-worker.mjs')
            config['containers'][0]['class_name'] = 'ManagedNativeGateway'
            config['durable_objects']['bindings'][0]['class_name'] = 'ManagedNativeGateway'
            config['exports'] = {'ManagedNativeGateway': {'type': 'durable-object', 'storage': 'sqlite'}}
            config['r2_buckets'][0]['bucket_name'] = 'mithril-hermes-runtime-state'
        config_path = path / 'wrangler.json'
        config_path.write_text(json.dumps(config))
        subprocess.run([str(args.wrangler), 'containers', 'push', tag, '--config', str(config_path)], env=env, cwd=ROOT, check=True)
        info = json.loads(subprocess.check_output([*docker, 'image', 'inspect', image]))[0]
        digest = next(value for value in info.get('RepoDigests', []) if value.startswith('registry.cloudflare.com/' + ACCOUNT + '/' + publication_name + '@sha256:'))
        published_index = digest.split('@')[1]
        manifest_command = [*docker, '--config', str(config_home), 'manifest', 'inspect']
        manifest = json.loads(subprocess.check_output([*manifest_command, digest]))
        if 'manifests' in manifest:
            selected = next(item['digest'] for item in manifest['manifests'] if item.get('platform', {}).get('os') == 'linux' and item.get('platform', {}).get('architecture') == 'amd64')
            digest = digest.split('@')[0] + '@' + selected
            manifest = json.loads(subprocess.check_output([*manifest_command, digest]))
        # Docker's containerd image store identifies the OCI index rather than
        # the config blob. Both forms must match the exact admitted image.
        index_admitted = info.get('Descriptor', {}).get('digest') == image == published_index
        if manifest.get('config', {}).get('digest') != image and not index_admitted:
            raise ValueError('Published manifest does not reference the admitted image')
        config['containers'][0]['image'] = digest
        config_path.write_text(json.dumps(config))
        if managed:
            current = subprocess.check_output(['git', 'ls-remote', 'origin', 'refs/heads/main'], cwd=ROOT, text=True).split()[0]
            if current != admitted['sha'] or subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip():
                raise ValueError('Current source changed before publication')
        if bootstrap and bootstrap_rollout(args.bootstrap_from, headers, os.environ['CLOUDFLARE_API_TOKEN']) != bootstrap:
            raise ValueError('Disabled bootstrap changed before publication')
        save_phase('worker-publication-outcome-unknown', cloudflareImage=digest, **({'rollback': bootstrap} if bootstrap else {}))
        subprocess.run([str(args.wrangler), 'deploy', '--containers-rollout=immediate', '--config', str(config_path)], cwd=ROOT, env=env, check=True)
        save_phase('secrets-publication-outcome-unknown', cloudflareImage=digest)
        subprocess.run([str(args.wrangler), 'secret', 'bulk', '--config', str(config_path)], input=json.dumps(secrets).encode(), cwd=ROOT, env=env, check=True)
        if managed:
            # This read-only qualification starts no container and keeps Fund admission disabled.
            with urlopen(Request(origin + '/_qualification/meter', method='POST', headers=headers), timeout=30) as response:
                qualification = json.load(response)
                if response.status != 200 or qualification.get('sourceSha') != admitted['sha'] or qualification.get('meterQualified') is not True:
                    raise ValueError('Managed Worker meter qualification differs')
            with urlopen(Request(origin + '/health', headers=headers), timeout=30) as response:
                health = json.load(response)
                if response.status != 200 or health.get('sourceSha') != admitted['sha'] or health.get('service') != 'mithril-native-gateway' or health.get('meterQualified') is not True:
                    raise ValueError('Managed Worker live source read-back differs')
            save_phase('bootstrap-live-billing-disabled', cloudflareImage=digest,
                meterQualified=health.get('meterQualified') is True)
        record = {'sourceSha': admitted['sha'], 'imageId': image, 'cloudflareImage': digest, 'accountId': ACCOUNT, 'worker': publication_name, 'qualificationReceipt': str(args.receipt), 'productionEligible': managed, 'liveVerificationComplete': False, 'workerSha256': hashlib.sha256((ROOT / ('scripts/standalone/cloudflare/managed-worker.mjs' if managed else 'scripts/standalone/cloudflare/worker.mjs')).read_bytes()).hexdigest(), 'configuration': config}
        (args.output / 'deployment.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps({key: value for key, value in record.items() if key != 'configuration'}))


if __name__ == '__main__':
    main()
