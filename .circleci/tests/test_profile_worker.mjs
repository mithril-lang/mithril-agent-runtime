import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

async function loadWorker() {
  const context = vm.createContext({
    Request, Response, URL, TextEncoder, TextDecoder, Uint8Array, ArrayBuffer,
    crypto: webcrypto, console, atob, btoa,
  });
  class DurableObject {
    constructor(ctx, env) { this.ctx = ctx; this.env = env; }
  }
  class WorkerEntrypoint { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }
  const worker = new vm.SourceTextModule(
    await readFile('cloud/profile-pilot/worker.mjs', 'utf8'), { context },
  );
  await worker.link(async (specifier) => {
    if (specifier === 'cloudflare:workers')
      return new vm.SyntheticModule(['DurableObject', 'WorkerEntrypoint'], function () { this.setExport('DurableObject', DurableObject); this.setExport('WorkerEntrypoint', WorkerEntrypoint); }, { context });
    if (specifier === './overlay.mjs')
      return new vm.SyntheticModule(['default'], function () { this.setExport('default', {}); }, { context });
    throw Error('Unexpected import');
  });
  await worker.evaluate();
  return worker.namespace;
}

const projection = {
  state: 'observed', observedAt: 1000,
  value: { result: 'ok', lastRunAt: 900, nextRunAt: 2000 },
};

function fixture({ failCache = false } = {}) {
  const values = new Map([['profile', 'mithril-abb-information-bounty']]);
  const alarms = [];
  return {
    values, alarms,
    storage: {
      async get(key) { return values.get(key); },
      async put(key, value) {
        if (key === 'business_activity' && failCache) throw Error('synthetic storage failure');
        values.set(key, value);
      },
      async setAlarm(value) { alarms.push(value); },
    },
    get container() { throw Error('activity read must not touch the container'); },
  };
}

test('activity reads the prior observation without starting a container', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  ctx.values.set('business_activity', projection);
  const object = new ProfileContainer(ctx, {});
  const response = await object.fetch(new Request('https://unit.invalid/activity'));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { business_activity: projection });
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
});

test('an absent observation is unavailable and unauthorized access is refused', async () => {
  const { ProfileContainer, default: worker } = await loadWorker();
  const ctx = fixture();
  const object = new ProfileContainer(ctx, {});
  const response = await object.fetch(new Request('https://unit.invalid/activity'));
  assert.deepEqual(await response.json(), { business_activity: { state: 'unavailable' } });
  const forbidden = await worker.fetch(new Request('https://unit.invalid/profiles/mithril-abb-information-bounty/activity'),
    { SERVICE_KEY: 'operator-secret', TRANSFER_KEY: 'transfer-secret', PROFILES: { getByName() { throw Error('must not route'); } } });
  assert.equal(forbidden.status, 401);
});

test('a failed observation write cannot retry the business tick', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture({ failCache: true });
  const object = new ProfileContainer(ctx, {});
  let calls = 0, arms = 0;
  object.token = async () => 'synthetic';
  object.call = async () => { calls++; return Response.json({ exit_code: 0, business_activity: projection }); };
  object.arm = async () => { arms++; };
  await object.alarm();
  assert.equal(calls, 1);
  assert.equal(arms, 1);
  assert.equal(ctx.alarms.length, 0);
  assert.equal(ctx.values.has('business_activity'), false);
});

test('unrecognized or extra fields never enter the observation cache', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  const object = new ProfileContainer(ctx, {});
  object.token = async () => 'synthetic';
  object.call = async () => Response.json({ exit_code: 0, business_activity: { ...projection, prompt: 'private' } });
  object.arm = async () => {};
  await object.alarm();
  assert.equal(ctx.values.has('business_activity'), false);
  assert.equal(ctx.alarms.length, 0);
});

test('private binding rejects unknown or mismatched profile without allocating a container', async () => {
  const { ProfileContainer, PilotActivityService } = await loadWorker();
  const ctx = fixture();
  ctx.values.set('business_activity', projection);
  const object = new ProfileContainer(ctx, {});
  let routed = 0;
  const service = new PilotActivityService({}, { PROFILES: {
    getByName(name) {
      routed++;
      assert.equal(name, 'scheduled-profile-pilot-v1:mithril-abb-information-bounty');
      return object;
    },
  } });
  assert.equal((await service.readBusinessActivity('not-allowlisted')).state, 'unavailable');
  assert.equal(routed, 0);
  assert.deepEqual(await service.readBusinessActivity('mithril-abb-information-bounty'), projection);
  assert.equal((await object.readBusinessActivity('mithril-accxia-bounty')).state, 'unavailable');
  assert.equal(ctx.alarms.length, 0);
});

test('a successful tick caches exactly the independently observed business result', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  const object = new ProfileContainer(ctx, {});
  object.token = async () => 'synthetic';
  object.call = async () => Response.json({ exit_code: 0, business_activity: projection });
  object.arm = async () => {};
  await object.alarm();
  assert.deepEqual(ctx.values.get('business_activity'), projection);
  assert.equal(ctx.alarms.length, 0);
});
