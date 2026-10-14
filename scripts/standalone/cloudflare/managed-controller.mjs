import { nativeSharedGateway } from './controller.mjs';
import { managedPrincipal, managedName } from './managed-policy.mjs';
const json = (body, status=200) => Response.json(body, {status, headers: {'Cache-Control': 'private, no-store'}});
const hexKey = () => Array.from(crypto.getRandomValues(new Uint8Array(32)), n => n.toString(16).padStart(2, '0')).join('');

/** Fund alone admits membership and holds. Private DO state binds identity permanently. */
export function managedNativeGateway(DurableObject) {
return class ManagedNativeGateway extends nativeSharedGateway(DurableObject) {
  async meterQualified() {return await this.ctx.storage.get('meterQualifiedSource') === this.env.SOURCE_SHA;}
  async usage(query,variables) {
    if(!this.env.CF_ANALYTICS_READ_TOKEN)throw Error('Scoped Cloudflare Analytics credential required');
    const response=await fetch('https://api.cloudflare.com/client/v4/graphql',{method:'POST',redirect:'manual',headers:{Authorization:'Bearer '+this.env.CF_ANALYTICS_READ_TOKEN,'Content-Type':'application/json'},body:JSON.stringify({query,variables}),signal:AbortSignal.timeout(15000)});
    const body=await response.json();
    const accounts=body.data?.viewer?.accounts;
    if(!response.ok || body.errors?.length || accounts?.length!==1 || !Array.isArray(accounts[0].containersUsageAdaptiveGroups))throw Error('Cloudflare billing usage unavailable');
    return accounts[0].containersUsageAdaptiveGroups;
  }
  async qualifyMeter() {
    await this.usage('query { viewer { accounts(filter:{accountTag:"62e1fda53188460698f8c66d3aef59c6"}) { containersUsageAdaptiveGroups(limit:1,filter:{datetime_geq:"'+new Date(Date.now()-3600000).toISOString()+'",datetime_lt:"'+new Date().toISOString()+'"}) { sum { cpuTimeSec allocatedMemory allocatedDisk } } } } }',{});
    await this.ctx.storage.put('meterQualifiedSource',this.env.SOURCE_SHA);
    return {meterQualified:true,sourceSha:this.env.SOURCE_SHA};
  }
  async bindManaged(identity) {
    const name = await managedName(identity);
    await this.ctx.blockConcurrencyWhile(async () => {
      const existing = await this.ctx.storage.get('managedRow');
      if (existing && JSON.stringify(existing.identity) !== JSON.stringify(identity)) throw Error('Execution identity cannot change');
      if (!existing) await this.ctx.storage.put({managedRow: {name, identity, apiKey: crypto.randomUUID()+crypto.randomUUID(), encryptionKey: hexKey()}, name, identity: JSON.stringify(identity)});
    });
  }
  async row() {
    const row = await this.ctx.storage.get('managedRow');
    const lease = await this.ctx.storage.get('paidLease');
    if (!row) throw Error('Execution identity is not bound');
    return {...row, providerKey: lease?.providerKey, model: lease?.model, labels: lease ? {mithril_job:lease.jobId,mithril_service:'hermes-runtime'} : undefined};
  }
  async admit(launch) {
    const now = Date.now();
    const lease = await this.ctx.blockConcurrencyWhile(async () => {
      if (await this.ctx.storage.get('cancelled:'+launch.jobId)) return {jobId:launch.jobId,state:'cancelled'};
      const existing = await this.ctx.storage.get('paidLease');
      if (existing) return existing;
      if (this.ctx.container.running || this.stopping) throw Error('Runtime is busy');
      const admitted = {...launch, startedAt: now, deadline: now + launch.maximumSeconds*1000, state: 'running'};
      await this.ctx.storage.put('paidLease', admitted);
      return admitted;
    });
    if (lease.jobId !== launch.jobId) return json({error: 'runtime-has-active-lease'}, 409);
    if (lease.state !== 'running' || Date.now() >= lease.deadline) return json({error: 'lease-ended'}, 409);
    // Replays never swap credentials, price terms or create another lease.
    if (lease.maximumSeconds !== launch.maximumSeconds || lease.model !== launch.model) return json({error:'lease-terms-conflict'},409);
    try { await this.wake(); } catch { await this.touch(); return json({jobId: lease.jobId, status:'starting'},202); }
    return this.leaseStatus(lease.jobId);
  }
  async cancel(jobId) {
    await this.ctx.blockConcurrencyWhile(() => this.ctx.storage.put('cancelled:'+jobId,true));
    const lease = await this.ctx.storage.get('paidLease');
    if (!lease || lease.jobId !== jobId) return json({jobId,cancelled:true,running:false,settlementPending:false});
    const response=await this.sleep(); await response.body?.cancel();
    return json({jobId,cancelled:true,running:this.ctx.container.running,settlementPending:true});
  }
  async leaseStatus(jobId) {
    const lease = await this.ctx.storage.get('paidLease');
    if (!lease || lease.jobId !== jobId) return json({error:'lease-not-found'},404);
    if (lease.state !== 'running') return json({jobId, running:false, status:lease.state});
    const response = await this.status(); const runtime = await response.json();
    if (runtime.sandboxId && !lease.sandboxId) {lease.sandboxId = runtime.sandboxId; await this.ctx.storage.put('paidLease',lease);}
    return json({jobId, running: runtime.running === true, sandboxId: runtime.sandboxId, expiresAt: Math.floor(lease.deadline/1000)});
  }
  async start() {
    const lease = await this.ctx.storage.get('paidLease');
    if (!lease || lease.state !== 'running' || Date.now() >= lease.deadline) throw Error('Paid lease required');
    return super.start();
  }
  async touch() {
    const lease = await this.ctx.storage.get('paidLease');
    if (!lease) return;
    await this.ctx.storage.setAlarm(lease.state === 'running' ? Math.min(Date.now()+Number(this.env.IDLE_MS || 120000),lease.deadline) : Date.now()+30000);
  }
  async sleep() {
    const response = await super.sleep();
    if (response.ok && !this.ctx.container.running) await this.endLease('checkpointed');
    return response;
  }
  async endLease(recovery) {
    const lease = await this.ctx.storage.get('paidLease');
    if (!lease) return;
    // Save settlement before the callback. Retrying cannot alter measured seconds.
    if (lease.state === 'running') {
      lease.state = 'settlement-pending'; lease.seconds = Math.min(lease.maximumSeconds,Math.max(0,Math.ceil((Date.now()-lease.startedAt)/1000)));
      lease.recovery = recovery; lease.endedAt = Date.now();
      delete lease.providerKey;
      await this.ctx.storage.put('paidLease',lease);
    }
    await this.settle(lease);
  }
  async settle(lease) {
    if(!lease.providerUsage) {
      if(Date.now()-lease.endedAt<120000) {await this.touch();return;}
      try {
        const rows=await this.usage('query Usage($start:Time,$end:Time,$label:String) { viewer { accounts(filter:{accountTag:"62e1fda53188460698f8c66d3aef59c6"}) { containersUsageAdaptiveGroups(limit:100,filter:{datetime_geq:$start,datetime_lt:$end,labels_has:$label}) { sum { cpuTimeSec allocatedMemory allocatedDisk } } } } }',{start:new Date(lease.startedAt-60000).toISOString(),end:new Date(lease.endedAt+60000).toISOString(),label:'mithril_job='+lease.jobId});
        if(!rows.length) {await this.touch();return;}
        const value=rows.reduce((a,r)=>({cpuTimeSec:a.cpuTimeSec+r.sum.cpuTimeSec,memoryByteSeconds:a.memoryByteSeconds+r.sum.allocatedMemory,diskByteSeconds:a.diskByteSeconds+r.sum.allocatedDisk}),{cpuTimeSec:0,memoryByteSeconds:0,diskByteSeconds:0});
        if(Object.values(value).some(v=>!Number.isFinite(v) || v<0) || !value.memoryByteSeconds)throw Error('Invalid provider usage');
        const snapshot=JSON.stringify(value);
        if(lease.usageSnapshot!==snapshot) {lease.usageSnapshot=snapshot;lease.usageObservedAt=Date.now();await this.ctx.storage.put('paidLease',lease);await this.touch();return;}
        if(Date.now()-(lease.usageObservedAt || 0)<60000) {await this.touch();return;}
        lease.providerUsage=value;await this.ctx.storage.put('paidLease',lease);
      } catch {await this.touch();return;}
    }
    const row = await this.row();
    const response = await fetch('https://api.mithril.fund/v1/hermes/native-runtime/usage', {method:'POST',redirect:'manual',headers:{Authorization:'Bearer '+this.env.SETTLEMENT_KEY,'Content-Type':'application/json'},body:JSON.stringify({jobId:lease.jobId,identity:row.identity,sandboxId:lease.sandboxId || null,seconds:lease.seconds,providerUsage:lease.providerUsage,recovery:lease.recovery}),signal:AbortSignal.timeout(15000)}).catch(()=>null);
    if (!response?.ok) {await response?.body?.cancel(); await this.touch(); return;}
    await response.body?.cancel();
    await this.ctx.blockConcurrencyWhile(async () => {
      const current = await this.ctx.storage.get('paidLease');
      if (current?.jobId !== lease.jobId || current.state === 'running') return;
      await this.ctx.storage.delete('paidLease'); await this.ctx.storage.deleteAlarm();
    });
  }
  async alarm() {
    const lease = await this.ctx.storage.get('paidLease');
    if (!lease) return;
    if (lease.state !== 'running') {await this.settle(lease); return;}
    const response = await this.sleep(); await response.body?.cancel();
    if (!this.ctx.container.running) return;
    if (Date.now() >= lease.deadline) {
      // The budget is a hard cap even with an open socket or failed checkpoint.
      // Recovery explicitly reports the last durable checkpoint, never a clean stop.
      await this.ctx.container.destroy(); await this.ctx.storage.delete('storeToken');
      await this.endLease('last-durable-checkpoint');
    } else {await this.ctx.storage.setAlarm(Math.min(Date.now()+5000,lease.deadline));}
  }
  async fetch(request) {
    const p = managedPrincipal(request,this.env.SERVICE_KEY);
    const row = await this.row(); const lease = await this.ctx.storage.get('paidLease');
    if (!p || JSON.stringify(p.identity) !== JSON.stringify(row.identity) || p.suffix !== '/runtime/socket' || request.method !== 'GET' || request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') return new Response(null,{status:401});
    if (!lease || lease.state !== 'running' || Date.now() >= lease.deadline) return new Response(null,{status:409});
    return this.forward(request,p.actor);
  }
};
}
