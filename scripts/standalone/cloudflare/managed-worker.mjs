import { DurableObject } from 'cloudflare:workers';
import { managedNativeGateway } from './managed-controller.mjs';
import { managedPrincipal, managedName, paidLaunch } from './managed-policy.mjs';
export const ManagedNativeGateway = managedNativeGateway(DurableObject);
const json = (body,status=200) => Response.json(body,{status,headers:{'Cache-Control':'private, no-store'}});
export default {
  async fetch(request,env) {
    try {
      const url = new URL(request.url);
      if (url.pathname === '/health') return json({service:'mithril-native-gateway',sourceSha:env.SOURCE_SHA,meterQualified:await env.GATEWAY.getByName('qualification:'+env.SOURCE_SHA).meterQualified()});
      if(url.pathname === '/_qualification/meter' && request.method === 'POST' && !url.search) {
        if(!env.SERVICE_KEY || env.SERVICE_KEY.length<32 || request.headers.get('Authorization')!=='Bearer '+env.SERVICE_KEY)return new Response(null,{status:401});
        return json(await env.GATEWAY.getByName('qualification:'+env.SOURCE_SHA).qualifyMeter());
      }
      const checkpoint = /^\/_state\/(fund-[a-f0-9]{64})$/.exec(url.pathname);
      if (checkpoint && !url.search) return await env.GATEWAY.getByName('native-shared-v1:'+checkpoint[1]).checkpoint(request,request.headers.get('Authorization')?.replace(/^Bearer /,''));
      const p = managedPrincipal(request,env.SERVICE_KEY);
      if (!p) return new Response(null,{status:401});
      const gateway = env.GATEWAY.getByName('native-shared-v1:'+await managedName(p.identity));
      await gateway.bindManaged(p.identity);
      if (p.suffix === '/dashboard' && request.method === 'GET') return await gateway.status();
      if (p.suffix === '/dashboard' && request.method === 'POST') {
        if (Number(request.headers.get('Content-Length') || 0)>4096) return new Response(null,{status:413});
        const text=await request.text(); if(text.length>4096)return new Response(null,{status:413});
        const launch = paidLaunch(JSON.parse(text));
        return launch ? await gateway.admit(launch) : json({error:'invalid-paid-launch'},400);
      }
      const job = /^\/leases\/(native-launch:[a-f0-9-]{36})$/.exec(p.suffix);
      if (job && request.method === 'GET') return await gateway.leaseStatus(job[1]);
      if (job && request.method === 'POST') return await gateway.cancel(job[1]);
      if (p.suffix === '/sleep' && request.method === 'POST') return await gateway.sleep();
      if (p.suffix === '/runtime/socket') return await gateway.fetch(request);
      return new Response(null,{status:404});
    } catch {return json({error:'native-runtime-unavailable'},502);}
  },
};
