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

const binding = { profileIdentity: 'a'.repeat(32), generation: 1 };
const snapshot = { state: 'observed', binding, businessActivity: projection };

function fixture({ failCache = false } = {}) {
  const values = new Map([['profile', 'mithril-abb-information-bounty'], ['activity_snapshot_epoch', 0]]);
  let chain = Promise.resolve();
  const alarms = [];
  return {
    values, alarms,
    storage: {
      async get(key) { return values.get(key); },
      async put(key, value) {
        if (key === 'activity_snapshot' && failCache) throw Error('synthetic storage failure');
        values.set(key, value);
      },
      async setAlarm(value) { alarms.push(value); },
      transaction(fn) {
        const run = chain.then(() => fn(this));
        chain = run.catch(() => {});
        return run;
      },
    },
    get container() { throw Error('activity read must not touch the container'); },
  };
}

test('activity reads the prior observation without starting a container', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  ctx.values.set('activity_snapshot', snapshot);
  const object = new ProfileContainer(ctx, {});
  const response = await object.fetch(new Request('https://unit.invalid/activity'));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { business_activity: snapshot });
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
  object.call = async () => { calls++; return Response.json({ exit_code: 0, business_activity: projection, activity_binding: binding }); };
  object.arm = async () => { arms++; };
  await object.alarm();
  assert.equal(calls, 1);
  assert.equal(arms, 1);
  assert.equal(ctx.alarms.length, 0);
  assert.equal(ctx.values.has('activity_snapshot'), false);
});

test('unrecognized or extra fields never enter the observation cache', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  const object = new ProfileContainer(ctx, {});
  object.token = async () => 'synthetic';
  object.call = async () => Response.json({ exit_code: 0, business_activity: { ...projection, prompt: 'private' }, activity_binding: binding });
  object.arm = async () => {};
  await object.alarm();
  assert.equal(ctx.values.get('activity_snapshot').state, 'unavailable');
  assert.equal(ctx.alarms.length, 0);
});

test('private binding rejects unknown or mismatched profile without allocating a container', async () => {
  const { ProfileContainer, PilotActivityService } = await loadWorker();
  const ctx = fixture();
  ctx.values.set('activity_snapshot', snapshot);
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
  assert.deepEqual(await service.readBusinessActivity('mithril-abb-information-bounty'), snapshot);
  assert.equal((await object.readBusinessActivity('mithril-accxia-bounty')).state, 'unavailable');
  assert.equal(ctx.alarms.length, 0);
});

test('a successful tick caches exactly the independently observed business result', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  const object = new ProfileContainer(ctx, {});
  object.token = async () => 'synthetic';
  object.call = async () => Response.json({ exit_code: 0, business_activity: projection, activity_binding: binding });
  object.arm = async () => {};
  await object.alarm();
  assert.deepEqual(JSON.parse(JSON.stringify(ctx.values.get('activity_snapshot'))), snapshot);
  assert.equal(ctx.alarms.length, 0);
});

test('a missing or corrupted runtime identity never exposes a cached result', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  const object = new ProfileContainer(ctx, {});
  ctx.values.set('activity_snapshot', { ...snapshot, binding: { ...binding, profileIdentity: 'invalid' } });
  assert.equal((await object.readBusinessActivity('mithril-abb-information-bounty')).state, 'unavailable');
  ctx.values.set('activity_snapshot', { ...snapshot, privatePayload: 'must not leak' });
  assert.equal((await object.readBusinessActivity('mithril-abb-information-bounty')).state, 'unavailable');
  object.token = async () => 'synthetic';
  object.call = async () => Response.json({ exit_code: 0, business_activity: projection, activity_binding: null });
  object.arm = async () => {};
  await object.alarm();
  assert.equal(ctx.values.get('activity_snapshot').state, 'unavailable');
  assert.equal(ctx.alarms.length, 0);
});

test('instance inspection is read-only, identity bound and does not claim readiness', async () => {
  const { ProfileContainer, PilotActivityService } = await loadWorker();
  const ctx = fixture();
  ctx.values.set('activity_snapshot', snapshot);
  let instance = { image: 'synthetic-image', labels: { private: 'not returned' } };
  let inspections = 0;
  Object.defineProperty(ctx, 'container', { get: () => ({
    inspect: async () => { inspections++; return instance; },
    start: () => { throw Error('must never start'); },
  }) });
  const object = new ProfileContainer(ctx, {});
  const service = new PilotActivityService({}, { PROFILES: { getByName: () => object } });
  const result = await service.readContainerActivity('mithril-abb-information-bounty');
  assert.equal(result.state, 'observed');
  assert.equal(result.binding.profileIdentity, binding.profileIdentity);
  assert.equal(result.containerActivity.value, 'running');
  assert.equal(typeof result.containerActivity.observedAt, 'number');
  assert.equal(JSON.stringify(result).includes('synthetic-image'), false);
  instance = null;
  assert.equal((await object.readContainerActivity('mithril-abb-information-bounty')).containerActivity.value, 'inactive');
  instance = { image: '', labels: {} };
  assert.equal((await object.readContainerActivity('mithril-abb-information-bounty')).containerActivity.value, 'unknown');
  assert.equal((await object.readContainerActivity('mithril-accxia-bounty')).state, 'unavailable');
  assert.equal(inspections, 3);
  assert.equal(ctx.alarms.length, 0);
});

test('failed instance inspection does not invent a state', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  ctx.values.set('activity_snapshot', snapshot);
  Object.defineProperty(ctx, 'container', { get: () => ({ inspect: async () => { throw Error('unavailable'); } }) });
  const object = new ProfileContainer(ctx, {});
  assert.equal((await object.readContainerActivity('mithril-abb-information-bounty')).state, 'unavailable');
});

test('a control mutation fences an old concurrent tick and blocks reads while pending', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  ctx.values.set('activity_snapshot', snapshot);
  const object = new ProfileContainer(ctx, {});
  object.token = async () => 'synthetic';
  object.arm = async () => {};
  let finishTick, startedTick;
  const started = new Promise(resolve => { startedTick = resolve; });
  object.call = async () => {
    startedTick();
    return new Promise(resolve => { finishTick = resolve; });
  };
  const alarm = object.alarm();
  await started;
  let finishControl, startedControl;
  const controlStarted = new Promise(resolve => { startedControl = resolve; });
  const control = object.guardActivityMutation(() => {
    startedControl();
    return new Promise(resolve => { finishControl = resolve; });
  });
  await controlStarted;
  assert.equal((await object.readBusinessActivity('mithril-abb-information-bounty')).state, 'unavailable');
  finishTick(Response.json({exit_code: 0, business_activity: projection, activity_binding: binding}));
  await alarm;
  finishControl('done');
  assert.equal(await control, 'done');
  assert.equal((await object.readBusinessActivity('mithril-abb-information-bounty')).state, 'unavailable');
  object.call = async () => Response.json({exit_code: 0, business_activity: projection, activity_binding: binding});
  await object.alarm();
  assert.deepEqual(JSON.parse(JSON.stringify(await object.readBusinessActivity('mithril-abb-information-bounty'))), snapshot);
  assert.equal(ctx.alarms.length, 0);
});

test('corrupt or retained mutation holds never expose an observation', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  ctx.values.set('activity_snapshot', snapshot);
  const object = new ProfileContainer(ctx, {});
  ctx.values.set('activity_fence', {epoch: 1, pending: 1});
  assert.equal((await object.readBusinessActivity('mithril-abb-information-bounty')).state, 'unavailable');
  ctx.values.set('activity_fence', {epoch: 'bad', pending: 0});
  await assert.rejects(object.readBusinessActivity('mithril-abb-information-bounty'));
});

test('a tick beginning during a control mutation cannot publish after it completes', async () => {
  const { ProfileContainer } = await loadWorker();
  const ctx = fixture();
  ctx.values.set('activity_snapshot', snapshot);
  const object = new ProfileContainer(ctx, {});
  object.token = async () => 'synthetic';
  object.arm = async () => {};
  let releaseControl, controlStarted;
  const startedControl = new Promise(resolve => { controlStarted = resolve; });
  const control = object.guardActivityMutation(() => {
    controlStarted();
    return new Promise(resolve => { releaseControl = resolve; });
  });
  await startedControl;
  let releaseTick, tickStarted;
  const startedTick = new Promise(resolve => { tickStarted = resolve; });
  object.call = async () => {
    tickStarted();
    return new Promise(resolve => { releaseTick = resolve; });
  };
  const alarm = object.alarm();
  await startedTick;
  releaseControl('ok');
  await control;
  releaseTick(Response.json({exit_code: 0, business_activity: projection, activity_binding: binding}));
  await alarm;
  assert.equal((await object.readBusinessActivity('mithril-abb-information-bounty')).state, 'unavailable');
  assert.equal(ctx.alarms.length, 0);
});
