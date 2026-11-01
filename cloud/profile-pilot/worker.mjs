import { DurableObject } from 'cloudflare:workers';
import bundle from './overlay.mjs';

const names = new Set([
  'mithril-abb-information-bounty', 'mithril-airtable-bounty', 'mithril-akeles-bounty',
  'mithril-alohi-bounty', 'mithril-amoeboids-bounty', 'mithril-agilepulse-bounty',
  'mithril-aelbox-bounty', 'mithril-alsco-bounty', 'mithril-accxia-bounty', 'mithril-adevinta-bounty',
]);
const encoder = new TextEncoder();
const hex = bytes => Array.from(new Uint8Array(bytes), x => x.toString(16).padStart(2, '0')).join('');
const headers = { 'Cache-Control': 'private, no-store' };
const entry = "import os,time; from pathlib import Path\nwhile not Path('/tmp/pilot-ready').exists(): time.sleep(.1)\nos.execv('/opt/hermes/.venv/bin/python',['python','-u','/tmp/pilot-runtime.py'])";

export class ProfileContainer extends DurableObject {
  async bind(name) {
    if (!names.has(name)) throw new Error('Profile is outside the pilot allowlist');
    const prior = await this.ctx.storage.get('profile');
    if (prior && prior !== name) throw new Error('Container owner is immutable');
    await this.ctx.storage.put('profile', name);
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
    if (!c.running) {
      c.start({ enableInternet: true, entrypoint: ['/opt/hermes/.venv/bin/python', '-u', '-c', entry], env: {
        PILOT_PROFILE: name, PILOT_CONTROL_KEY: this.env.SERVICE_KEY,
        PILOT_TRANSFER_KEY: this.env.TRANSFER_KEY, PILOT_ORIGIN: this.env.ORIGIN,
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
    for (let n = 0; n < 180; n++) {
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
    const response = await this.call('/status', new Request('http://container/status', {headers: {Authorization: 'Bearer ' + this.env.SERVICE_KEY}}));
    if (!response.ok) throw new Error('Cannot inspect assigned scheduler');
    const status = await response.json();
    if (status.placement.phase === 'active' && status.next_due_ms !== null) {
      await this.ctx.storage.setAlarm(Math.max(Date.now() + 1000, status.next_due_ms));
    } else {
      await this.ctx.storage.deleteAlarm();
    }
  }

  async alarm() {
    // Wake only this profile's assigned owner. Never changes ownership or falls back locally.
    try {
      const response = await this.call('/tick', new Request('http://container/tick', {method: 'POST', headers: {Authorization: 'Bearer ' + this.env.SERVICE_KEY, 'Content-Type': 'application/json'}, body: '{}'}));
      if (!response.ok) throw new Error('Assigned scheduler tick failed');
      await response.body?.cancel();
      await this.arm();
    } catch {
      await this.ctx.storage.setAlarm(Date.now() + 60000);
    }
  }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === '/restart' && request.method === 'POST') {
      const saved = await this.call('/checkpoint', new Request('http://container/checkpoint', {method: 'POST', headers: {Authorization: 'Bearer ' + this.env.SERVICE_KEY, 'Content-Type': 'application/json'}, body: '{}'}));
      if (!saved.ok) return new Response('Checkpoint failed; container preserved', {status: 503, headers});
      await saved.body?.cancel();
      await this.ctx.container.destroy();
      await this.ensure();
      await this.arm();
      return this.call('/status', new Request('http://container/status', {headers: {Authorization: 'Bearer ' + this.env.SERVICE_KEY}}));
    }
    const response = await this.call(path, request);
    await this.arm();
    return response;
  }
}

export default {
  async fetch(request, env) {
    if (!env.SERVICE_KEY || request.headers.get('Authorization') !== 'Bearer ' + env.SERVICE_KEY) return new Response('Unauthorized', {status: 401, headers});
    const url = new URL(request.url);
    const store = /^\/_store\/([a-z0-9-]+)$/.exec(url.pathname);
    if (store) {
      if (!names.has(store[1])) return new Response('Not found', {status: 404, headers});
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
    const route = /^\/profiles\/([a-z0-9-]+)\/(status|stage|activate|chat|cron-smoke|cron-result|checkpoint|restart)$/.exec(url.pathname);
    if (!route || !names.has(route[1])) return new Response('Not found', {status: 404, headers});
    const owner = env.PROFILES.getByName('scheduled-profile-pilot-v1:' + route[1]);
    await owner.bind(route[1]);
    url.pathname = '/' + route[2];
    try { return await owner.fetch(new Request(url, request)); }
    catch { return Response.json({error: 'Assigned profile runtime unavailable'}, {status: 503, headers}); }
  },
};
