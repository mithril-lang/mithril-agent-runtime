"""Exercise a deployed shared runtime through real Hermes RPC and Mithril inference."""
import argparse
import asyncio
import json
from pathlib import Path
import uuid

from aiohttp import ClientSession, ClientTimeout, WSServerHandshakeError


class Rpc:
    def __init__(self, socket):
        self.socket, self.sequence, self.pending = socket, 0, []

    async def frame(self):
        if not self.pending:
            self.pending.extend(json.loads(line) for line in (await self.socket.receive_str(timeout=180)).splitlines() if line)
        return self.pending.pop(0)

    async def call(self, method, params):
        self.sequence += 1
        await self.socket.send_json({'jsonrpc': '2.0', 'id': self.sequence, 'method': method, 'params': params})
        while True:
            frame = await self.frame()
            if frame.get('id') == self.sequence:
                if 'error' in frame:
                    raise RuntimeError('Hermes RPC rejected ' + method)
                return frame['result']

    async def turn(self, session, text):
        await self.call('prompt.submit', {'session_id': session, 'profile': 'default', 'text': text, 'surface': 'desktop'})
        while True:
            frame = await self.frame()
            params = frame.get('params', {})
            if params.get('type') in {'message.complete', 'message.error', 'error'}:
                if params.get('type') != 'message.complete':
                    raise RuntimeError('Hermes inference turn failed')
                return json.dumps(params)


async def run(args):
    values = json.loads(args.secrets.read_text())
    row = json.loads(values['RUNTIME_REGISTRY'])[args.tenant]
    members = list(row['members'])
    if row['identity']['kind'] != 'team' or len(members) != 2:
        raise ValueError('A dedicated two-member trial team is required')
    identity = row['identity']
    prefix = '/organizations/' + identity['organizationId'] + '/computers/' + identity['computerId']
    base = args.origin.rstrip('/')
    report = {'origin': base, 'tenant': args.tenant, 'productionEligible': False, 'complete': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, indent=2) + '\n')

    async with ClientSession(timeout=ClientTimeout(total=240), headers={'User-Agent': 'Hermes-Native-Shared-Trial/1.0'}) as client:
        async def http(member, suffix, method='GET'):
            async with client.request(method, base + prefix + suffix, headers={'Authorization': 'Bearer ' + member}) as response:
                data = await response.json() if response.headers.get('Content-Type', '').startswith('application/json') else {}
                return response.status, data

        async def wait_for_sleep(member):
            # Client close precedes the relay's checkpoint/connection cleanup.
            # A busy response must never count as a successful durable stop.
            for _ in range(30):
                status, data = await http(member, '/sleep', 'POST')
                if status != 409:
                    return status, data
                await asyncio.sleep(1)
            raise AssertionError('Runtime did not quiesce after socket close')

        status, boot = await http(members[0], '/wake', 'POST')
        assert status == 200 and boot['running']
        generation = boot['sandboxId']
        report['wake'] = {'status': status, 'generation': generation}; save()
        status, same = await http(members[1], '/dashboard')
        assert status == 200 and same['sandboxId'] == generation
        report['twoMembersSameRuntime'] = True; save()
        socket_url = base.replace('https:', 'wss:') + prefix + '/runtime/socket'
        async def connect(member, runtime):
            return await client.ws_connect(socket_url, headers={'Authorization': 'Bearer ' + member, 'X-Mithril-Runtime-Id': runtime})

        marker = 'SHARED_' + uuid.uuid4().hex
        async with await connect(members[0], generation) as socket:
            rpc = Rpc(socket)
            created = await rpc.call('session.create', {'profile': 'default', 'source': 'desktop', 'title': 'Shared Cloudflare recovery trial'})
            reply = await rpc.turn(created['session_id'], 'Remember this marker and reply with it only: ' + marker + '. Do not call tools.')
            assert marker in reply
            stored = created['stored_session_id']
            report['realMithrilReplyBeforeSleep'] = True; save()
            status, _ = await http(members[1], '/sleep', 'POST')
            assert status == 409
            report['sleepDeniedWhileConnected'] = True; save()
        status, sleep = await wait_for_sleep(members[1])
        assert status == 200 and sleep['running'] is False and sleep['checkpoint']
        report['durableSleep'] = True; save()
        status, asleep = await http(members[0], '/dashboard')
        assert status == 200 and asleep['running'] is False
        report['lookupDoesNotWake'] = True; save()
        status, resumed = await http(members[1], '/wake', 'POST')
        assert status == 200 and resumed['sandboxId'] != generation
        report['freshGenerationAfterRecovery'] = True; save()
        try:
            socket = await connect(members[0], generation)
        except WSServerHandshakeError as error:
            assert error.status == 409
            report['staleRuntimeRejected'] = True
        else:
            await socket.close()
            raise AssertionError('Stale runtime accepted')
        async with await connect(members[1], resumed['sandboxId']) as socket:
            rpc = Rpc(socket)
            session = await rpc.call('session.resume', {'session_id': stored, 'profile': 'default'})
            assert marker in json.dumps(session)
            reply = await rpc.turn(session['session_id'], 'What exact marker did I ask you to remember? Reply with the marker only. Do not use tools.')
            assert marker in reply
            report['sharedHistoryRestored'] = True
            report['realMithrilReplyAfterRecovery'] = True; save()
        # Release compute at the end of the trial.
        status, end = await wait_for_sleep(members[0])
        assert status == 200 and end['running'] is False
        report['complete'] = True; save()
        print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ['secrets', 'output']:
        parser.add_argument('--' + option, type=Path, required=True)
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--origin', default='https://mithril-native-shared-trial.cloud-kotoba.workers.dev')
    asyncio.run(run(parser.parse_args()))
