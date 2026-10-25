import { registry, authorize, prefix, storagePrefix } from './policy.mjs';
const json = (body, status = 200) => Response.json(body, {status, headers: {'Cache-Control': 'private, no-store'}});
const sha256 = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), x => x.toString(16).padStart(2, '0')).join('');

export function nativeSharedGateway(DurableObject) {
return class NativeSharedGateway extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.starting = null;
    this.stopping = false;
    this.destroying = null;
    this.destroyAcknowledged = false;
    this.runtimeGeneration = 0;
  }

  async bind(name) {
    const row = registry(this.env.RUNTIME_REGISTRY)[name];
    if (!row) throw Error('Unknown execution tenant');
    await this.ctx.blockConcurrencyWhile(async () => {
      const bound = await this.ctx.storage.get('identity');
      const identity = JSON.stringify(row.identity);
      if (bound && bound !== identity) throw Error('Execution tenant cannot change');
      await this.ctx.storage.put({identity, name});
    });
  }

  async row() {
    const name = await this.ctx.storage.get('name');
    const row = registry(this.env.RUNTIME_REGISTRY)[name];
    if (!row || JSON.stringify(row.identity) !== await this.ctx.storage.get('identity')) throw Error('Execution tenant configuration changed');
    return {name, ...row};
  }

  async checkpoint(request, token) {
    if (token !== await this.ctx.storage.get('storeToken')) return new Response('Stale storage lease', {status: 409});
    const {name} = await this.row();
    const root = storagePrefix(name);
    if (request.method === 'GET') {
      const pointer = await this.env.STATE.get(root + 'latest.json');
      if (!pointer) return new Response('No checkpoint', {status: 404});
      const {sha256: digest} = await pointer.json();
      if (!/^[a-f0-9]{64}$/.test(digest)) throw Error('Invalid checkpoint pointer');
      const object = await this.env.STATE.get(root + digest + '.enc');
      if (!object) throw Error('Checkpoint is missing');
      return new Response(object.body, {headers: {'X-Checkpoint-SHA256': digest, 'Cache-Control': 'no-store'}});
    }
    if (request.method !== 'PUT') return new Response(null, {status: 405});
    const bytes = await request.arrayBuffer();
    if (bytes.byteLength > 32 * 1024 * 1024) return new Response(null, {status: 413});
    const digest = await sha256(bytes);
    if (digest !== request.headers.get('X-Checkpoint-SHA256')) return new Response(null, {status: 400});
    // Serialize the final storage lease check and pointer publication. A previous
    // boot cannot publish after a new boot changes its lease.
    return this.ctx.blockConcurrencyWhile(async () => {
      if (token !== await this.ctx.storage.get('storeToken')) return new Response(null, {status: 409});
      await this.env.STATE.put(root + digest + '.enc', bytes);
      const readback = await this.env.STATE.get(root + digest + '.enc');
      if (!readback || await sha256(await readback.arrayBuffer()) !== digest) throw Error('Checkpoint readback failed');
      await this.env.STATE.put(root + 'latest.json', JSON.stringify({sha256: digest}));
      return json({sha256: digest});
    });
  }

  async wake() {
    if (this.stopping) return json({error: 'runtime-sleeping'}, 409);
    if (this.destroying) return json({error: 'runtime-destroy-pending'}, 409);
    if (!this.starting) this.starting = this.start().finally(() => { this.starting = null; });
    await this.starting;
    return this.status();
  }

  async start() {
    // A timed-out destroy remains an unknown in-flight operation. Do not let a
    // replay create a later container generation until that same destroy settles.
    if (this.stopping || this.destroying) throw Error('Runtime transition is unresolved');
    const row = await this.row();
    const container = this.ctx.container;
    if (!container.running) {
      this.runtimeGeneration++;
      this.destroyAcknowledged=false;
      const storeToken = crypto.randomUUID() + crypto.randomUUID();
      await this.ctx.storage.put('storeToken', storeToken);
      container.start({enableInternet: true, entrypoint: ['/opt/hermes/.venv/bin/python', '/opt/hermes/gateway-desktop.py'], ...(row.labels ? {labels:row.labels} : {}), env: {
        MITHRIL_AGENT: '1',
        API_SERVER_KEY: row.apiKey,
        HERMES_DASHBOARD_SESSION_TOKEN: row.apiKey,
        CUSTOM_PROVIDER_MITHRIL_KEY: row.providerKey,
        ...(row.model ? {MITHRIL_NATIVE_INITIAL_MODEL: row.model} : {}),
        CHECKPOINT_ENCRYPTION_KEY: row.encryptionKey,
        CHECKPOINT_STORE_KEY: storeToken,
        CHECKPOINT_STORE_URL: this.env.PUBLIC_ORIGIN + '/_state/' + row.name,
        MITHRIL_NATIVE_TENANT: JSON.stringify(row.identity),
      }});
      this.ctx.waitUntil(container.monitor().catch(() => {}));
    }
    // The alarm performs checkpoint-before-stop; the platform watchdog only
    // reclaims a stalled controller later, outside the graceful sleep window.
    await container.setInactivityTimeout(15 * 60 * 1000);
    for (let n = 0; n < 180; n++) {
      try {
        const response = await container.getTcpPort(7860).fetch('http://container/health', {signal: AbortSignal.timeout(1000)});
        if (response.ok) { await response.body?.cancel(); await this.touch(); return; }
        await response.body?.cancel();
      } catch {}
      await scheduler.wait(500);
    }
    throw Error('Native runtime readiness timed out');
  }

  async touch() {
    await this.ctx.storage.setAlarm(Date.now() + Number(this.env.IDLE_MS || 120000));
  }

  async status() {
    if (!this.ctx.container.running) return json({running: false});
    const row = await this.row();
    const actor = row.identity.kind === 'account' ? row.identity.accountId : 'control-plane';
    const response = await this.ctx.container.getTcpPort(7860).fetch('http://container' + prefix(row.identity) + '/dashboard', {headers: {'Authorization': 'Bearer ' + row.apiKey, 'X-Mithril-Actor-Id': actor}});
    return json(await response.json(), response.status);
  }

  async destroyContainer() {
    // Retry the wait, not a second unknown destroy against a potentially later generation.
    if(this.destroyAcknowledged)return;
    if(!this.destroying){
      const generation=this.runtimeGeneration;
      let pending;
      pending=Promise.resolve().then(()=>this.ctx.container.destroy()).then(()=>{
        if(this.runtimeGeneration===generation)this.destroyAcknowledged=true;
      }).finally(()=>{if(this.destroying===pending)this.destroying=null;});
      this.destroying=pending;
    }
    return this.stopOperation(()=>this.destroying);
  }

  async stopOperation(operation) {
    const abort=new AbortController();let timer;
    const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{abort.abort();reject(Error('Runtime stop deadline exceeded'));},5000);});
    try {return await Promise.race([Promise.resolve().then(()=>operation(abort.signal)),deadline]);}
    finally {clearTimeout(timer);}
  }

  async sleep() {
    if (this.starting || this.stopping) return json({error: 'runtime-busy'}, 409);
    if (!this.ctx.container.running) return json({running: false});
    this.stopping = true;
    try {
      const row = await this.row();
      const outcome=await this.stopOperation(async signal=>{
        const response=await this.ctx.container.getTcpPort(7860).fetch('http://container/_native/quiesce',{method:'POST',headers:{Authorization:'Bearer '+row.apiKey},signal});
        if(!response.ok)return {status:response.status};
        return {status:response.status,checkpoint:await response.json()};
      });
      if (!outcome.checkpoint) { await this.touch(); return json({error: 'checkpoint-or-runtime-busy'}, outcome.status); }
      const checkpoint=outcome.checkpoint;
      if (!/^[a-f0-9]{64}$/.test(checkpoint?.sha256 || '')) { await this.touch(); return json({error:'checkpoint-invalid'},503); }
      await this.destroyContainer();
      await this.ctx.storage.delete('storeToken');
      await this.ctx.storage.deleteAlarm();
      return json({running: false, checkpoint: checkpoint.sha256});
    } catch {
      // A thrown transport/destroy failure must leave a durable retry, including explicit sleep.
      await this.touch();
      return json({error:'runtime-stop-unavailable'},503);
    } finally { this.stopping = false; }
  }

  async alarm() {
    const response = await this.sleep();
    await response.body?.cancel();
  }

  async forward(request, actor) {
    if (this.stopping || !this.ctx.container.running) return json({error: 'runtime-asleep'}, 409);
    const row = await this.row();
    const url = new URL(request.url);
    url.protocol = 'http:'; url.host = 'container';
    const headers = new Headers(request.headers);
    headers.set('Authorization', 'Bearer ' + row.apiKey);
    headers.set('X-Mithril-Actor-Id', actor);
    headers.delete('X-Hermes-Session-Token');
    await this.touch();
    return this.ctx.container.getTcpPort(7860).fetch(new Request(url, new Request(request, {headers})));
  }

  async fetch(request) {
    // WebSocket upgrade responses cannot cross the RPC serializer. Admission
    // remains identical on the reserved HTTP fetch transport.
    const principal = authorize(request, registry(this.env.RUNTIME_REGISTRY), this.env.SERVICE_KEY);
    if (!principal || principal.name !== (await this.row()).name || principal.suffix !== '/runtime/socket' || request.method !== 'GET' || request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') return new Response(null, {status: 401});
    return this.forward(request, principal.actor);
  }
}

}
