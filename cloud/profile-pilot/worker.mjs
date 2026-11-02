import { DurableObject, WorkerEntrypoint } from 'cloudflare:workers';
import bundle from './overlay.mjs';

const names = new Set([
  'mithril-abb-information-bounty', 'mithril-airtable-bounty', 'mithril-akeles-bounty',
  'mithril-alohi-bounty', 'mithril-amoeboids-bounty', 'mithril-agilepulse-bounty',
  'mithril-aelbox-bounty', 'mithril-alsco-bounty', 'mithril-accxia-bounty', 'mithril-adevinta-bounty',
]);
const encoder = new TextEncoder();
const hex = bytes => Array.from(new Uint8Array(bytes), x => x.toString(16).padStart(2, '0')).join('');
async function derive(secret, context) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, encoder.encode(context)));
}

function validBusinessActivity(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const keys = (o, expected) => Object.keys(o).length === expected.length &&
    expected.every(key => Object.hasOwn(o, key));
  if (value.state === 'unavailable') return keys(value, ['state']);
  if (value.state !== 'observed' || !keys(value, ['state', 'observedAt', 'value']) ||
      !Number.isSafeInteger(value.observedAt) || value.observedAt < 0 ||
      !value.value || typeof value.value !== 'object' || Array.isArray(value.value) ||
      !keys(value.value, ['result', 'lastRunAt', 'nextRunAt'])) return false;
  const time = v => v === null || (Number.isSafeInteger(v) && v >= 0 && v <= 253402300799999);
  return ['ok', 'error', 'unknown'].includes(value.value.result) &&
    time(value.value.lastRunAt) && time(value.value.nextRunAt);
}
const headers = { 'Cache-Control': 'private, no-store' };
const entry = "import os,time; from pathlib import Path\nwhile not Path('/tmp/pilot-ready').exists(): time.sleep(.1)\nos.execv('/opt/hermes/.venv/bin/python',['python','-u','/tmp/pilot-runtime.py'])";

export class ProfileContainer extends DurableObject {
  async token() { return derive(this.env.SERVICE_KEY, 'profile-control:' + await this.ctx.storage.get('profile')); }
  async bind(name) {
    if (!names.has(name)) throw new Error('Profile is outside the pilot allowlist');
    const prior = await this.ctx.storage.get('profile');
    if (prior && prior !== name) throw new Error('Container owner is immutable');
    await this.ctx.storage.put('profile', name);
  }

  async readBusinessActivity(expectedName) {
    // Never create a binding or start a container during a read.
    if (!names.has(expectedName) || await this.ctx.storage.get('profile') !== expectedName)
      return {state: 'unavailable'};
    const value = await this.ctx.storage.get('business_activity');
    return validBusinessActivity(value) ? value : {state: 'unavailable'};
  }

  async ensure() {
    if (this.starting) return this.starting;
    this.starting = this.startImpl().finally(() => { this.starting = null; });
    return this.starting;
  }

  async startImpl() {
    const c = this.ctx.container;
    const name = await this.ctx.storage.get('profile');
    if (!name || !c) throw new Error('Pilot container is not configured');
    // `running` becomes true before startup succeeds. Inspect the actual
    // instance before trusting a previous failed allocation's startup intent.
    const instance = await c.inspect();
    if (!instance) {
      if (c.running) await c.destroy(); // inspect() confirmed that no process is running.
      c.start({ enableInternet: true, entrypoint: ['/opt/hermes/.venv/bin/python', '-u', '-c', entry], env: {
        PILOT_PROFILE: name, PILOT_CONTROL_KEY: await this.token(),
        PILOT_TRANSFER_KEY: await derive(this.env.TRANSFER_KEY, 'profile-transfer:' + name), PILOT_ORIGIN: this.env.ORIGIN,
        PYTHONDONTWRITEBYTECODE: '1',
      }});
      this.ctx.waitUntil(c.monitor().catch(() => console.error('Pilot container exited unexpectedly')));
      for (let n = 0; n < 180 && !c.running; n++) await scheduler.wait(500);
      const installer = atob(bundle.installer);
      const process = await c.exec(['/opt/hermes/.venv/bin/python', '-c', installer, btoa(JSON.stringify(bundle)), 'container:' + this.ctx.id.toString()], {user: '0:0'});
      const result = await process.output();
      if (result.exitCode !== 0) {
        console.error('Placement overlay installation failed', new TextDecoder().decode(result.stderr).slice(-1200));
        await c.destroy(); // No agent or profile restore has started at this point.
        throw new Error('Placement overlay installation failed');
      }
    }
    await c.setInactivityTimeout(10 * 60 * 1000);
    const deadline = Date.now() + 90000;
    for (let n = 0; n < 180; n++) {
      if (Date.now() >= deadline || !(await c.inspect())) break;
      try {
        const response = await c.getTcpPort(7860).fetch('http://container/health', {signal: AbortSignal.timeout(2000)});
        if (response.ok) { await response.body?.cancel(); return; }
        await response.body?.cancel();
      } catch {}
      await scheduler.wait(500);
    }
    throw new Error('Pilot control plane did not become ready');
  }

  async call(path, request) {
    await this.ensure();
    const url = new URL(request.url);
    url.protocol = 'http:'; url.host = 'container'; url.pathname = path;
    return this.ctx.container.getTcpPort(7860).fetch(new Request(url, request));
  }

  async arm() {
    const response = await this.call('/status', new Request('http://container/status', {headers: {Authorization: 'Bearer ' + await this.token()}}));
    if (!response.ok) throw new Error('Cannot inspect assigned scheduler');
    const status = await response.json();
    if (status.placement.phase === 'active' && !status.draining && status.next_due_ms !== null) {
      await this.ctx.storage.setAlarm(Math.max(Date.now() + 1000, status.next_due_ms));
    } else {
      await this.ctx.storage.deleteAlarm();
    }
  }

  async alarm() {
    // Wake only this profile's assigned owner. Never changes ownership or falls back locally.
    try {
      await this.ctx.storage.put('last_alarm', {started_at: Date.now(), profile: await this.ctx.storage.get('profile')});
      const response = await this.call('/tick', new Request('http://container/tick', {method: 'POST', headers: {Authorization: 'Bearer ' + await this.token(), 'Content-Type': 'application/json'}, body: '{}'}));
      if (!response.ok) throw new Error('Assigned scheduler tick failed');
      const result = await response.json();
      const before = await this.ctx.storage.get('last_alarm');
      await this.ctx.storage.put('last_alarm', {...before, finished_at: Date.now(), exit_code: result.exit_code});
      await this.arm();
      // Reporting is best-effort: failure must never replay the business tick.
      if (validBusinessActivity(result.business_activity)) {
        try { await this.ctx.storage.put('business_activity', result.business_activity); }
        catch { /* The previous observation will age out. */ }
      }
    } catch {
      await this.ctx.storage.setAlarm(Date.now() + 60000);
    }
  }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === '/diagnostics' && request.method === 'GET') {
      await this.ensure();
      const name = await this.ctx.storage.get('profile');
      // A fixed, read-only diagnostic; never expose a general exec surface.
      const code = `import json,os,re; from pathlib import Path
home=Path('/opt/data/placed')/${JSON.stringify(name)}
if not home.exists(): home=Path('/opt/data/profiles')/${JSON.stringify(name)}
path=home/'pilot-gateway.log'
text=path.read_bytes()[-32768:].decode(errors='replace') if path.exists() else ''
values=list(os.environ.values())
env=home/'.env'
if env.exists(): values += [line.split('=',1)[1].strip().strip('"').strip("'") for line in env.read_text().splitlines() if '=' in line and not line.lstrip().startswith('#')]
for value in sorted(values,key=len,reverse=True):
 if len(value)>=8: text=text.replace(value,'[redacted]')
text=re.sub(r'(?i)(bearer\\s+|(?:token|api[_-]?key|secret|password)[=:\\s]+)[^\\s,;]+',r'\\1[redacted]',text)
print(json.dumps({'gateway_log_tail':text}))`;
      const process = await this.ctx.container.exec(['/opt/hermes/.venv/bin/python', '-c', code], {user: '0:0'});
      const result = await process.output();
      if (result.exitCode !== 0) return new Response('Diagnostics unavailable', {status: 503, headers});
      return new Response(result.stdout, {headers: {...headers, 'Content-Type': 'application/json'}});
    }
    if (path === '/activity' && request.method === 'GET') {
      // Read only DO storage. No container startup or schedule mutation.
      return Response.json({business_activity: await this.readBusinessActivity(await this.ctx.storage.get('profile'))}, {headers});
    }
    if (path === '/control-state') {
      return Response.json({profile: await this.ctx.storage.get('profile'), location: 'container:' + this.ctx.id.toString(), container_running: this.ctx.container?.running ?? false, alarm: await this.ctx.storage.get('last_alarm') ?? null}, {headers});
    }
    if (path === '/suspend' && request.method === 'POST') {
      const prior = await this.call('/status', new Request('http://container/status', {headers: {Authorization: 'Bearer ' + await this.token()}}));
      const status = await prior.json();
      const saved = await this.call('/prepare-restart', new Request('http://container/prepare-restart', {method: 'POST', headers: {Authorization: 'Bearer ' + await this.token(), 'Content-Type': 'application/json'}, body: '{}'}));
      if (!saved.ok) return new Response('Drain or checkpoint failed; container preserved', {status: 503, headers});
      await saved.body?.cancel();
      await this.ctx.container.destroy();
      const wake = status.next_due_ms === null ? null : Math.max(Date.now() + 1000, status.next_due_ms);
      if (wake !== null) await this.ctx.storage.setAlarm(wake); else await this.ctx.storage.deleteAlarm();
      return Response.json({suspended: true, wake_ms: wake}, {headers});
    }
    if (path === '/restart' && request.method === 'POST') {
      if (!this.restarting) this.restarting = this.restart().finally(() => { this.restarting = null; });
      return (await this.restarting).clone();
    }
    const response = await this.call(path, request);
    await this.arm();
    return response;
  }

  async restart() {
    const saved = await this.call('/prepare-restart', new Request('http://container/prepare-restart', {method: 'POST', headers: {Authorization: 'Bearer ' + await this.token(), 'Content-Type': 'application/json'}, body: '{}'}));
    if (!saved.ok) return new Response('Drain or checkpoint failed; container preserved', {status: 503, headers});
    await saved.body?.cancel();
    await this.ctx.container.destroy();
    await this.ensure();
    await this.arm();
    return this.call('/status', new Request('http://container/status', {headers: {Authorization: 'Bearer ' + await this.token()}}));
  }
}

/** Private named service entrypoint; caller must authorize the exact Profile. */
export class PilotActivityService extends WorkerEntrypoint {
  async readBusinessActivity(profile) {
    if (!names.has(profile)) return {state: 'unavailable'};
    const owner = this.env.PROFILES.getByName('scheduled-profile-pilot-v1:' + profile);
    return owner.readBusinessActivity(profile);
  }
}

export default {
  async fetch(request, env) {
    if (!env.SERVICE_KEY || !env.TRANSFER_KEY) return new Response('Not configured', {status: 503, headers});
    const operator = request.headers.get('Authorization') === 'Bearer ' + env.SERVICE_KEY;
    const url = new URL(request.url);
    const store = /^\/_store\/([a-z0-9-]+)$/.exec(url.pathname);
    if (store) {
      if (!names.has(store[1])) return new Response('Not found', {status: 404, headers});
      if (!operator && request.headers.get('Authorization') !== 'Bearer ' + await derive(env.SERVICE_KEY, 'profile-control:' + store[1])) return new Response('Unauthorized', {status: 401, headers});
      const key = 'scheduled-profile-pilot-20261101/' + store[1] + '/checkpoint';
      if (request.method === 'GET') {
        const object = await env.STATE.get(key);
        if (!object) return new Response('No checkpoint', {status: 404, headers});
        return new Response(object.body, {headers: {...headers, 'X-Checkpoint-SHA256': object.customMetadata.sha256}});
      }
      if (request.method === 'PUT') {
        const data = await request.arrayBuffer();
        if (data.byteLength > 32 * 1024 * 1024) return new Response('Too large', {status: 413, headers});
        const sha256 = hex(await crypto.subtle.digest('SHA-256', data));
        if (request.headers.get('X-Checkpoint-SHA256') !== sha256) return new Response('Digest mismatch', {status: 400, headers});
        await env.STATE.put(key, data, {customMetadata: {sha256}});
        return Response.json({sha256}, {headers});
      }
      return new Response('Method not allowed', {status: 405, headers});
    }
    if (!operator) return new Response('Unauthorized', {status: 401, headers});
    const route = /^\/profiles\/([a-z0-9-]+)\/(status|control-state|activity|diagnostics|stage|activate|chat|cron-smoke|cron-result|cron-run|checkpoint|restart|suspend)$/.exec(url.pathname);
    if (!route || !names.has(route[1])) return new Response('Not found', {status: 404, headers});
    const owner = env.PROFILES.getByName('scheduled-profile-pilot-v1:' + route[1]);
    await owner.bind(route[1]);
    url.pathname = '/' + route[2];
    const forwardedHeaders = new Headers(request.headers);
    forwardedHeaders.set('Authorization', 'Bearer ' + await derive(env.SERVICE_KEY, 'profile-control:' + route[1]));
    try { return await owner.fetch(new Request(url, new Request(request, {headers: forwardedHeaders}))); }
    catch { return Response.json({error: 'Assigned profile runtime unavailable'}, {status: 503, headers}); }
  },
};
