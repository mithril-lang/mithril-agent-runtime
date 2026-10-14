import { DurableObject } from 'cloudflare:workers';
import { registry, authorize } from './policy.mjs';
import { nativeSharedGateway } from './controller.mjs';

export const NativeSharedGateway = nativeSharedGateway(DurableObject);
const json = (body, status = 200) => Response.json(body, {status, headers: {'Cache-Control': 'private, no-store'}});

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      if (url.pathname === '/health') return json({service: 'mithril-native-shared-trial', sourceSha: env.SOURCE_SHA});
      const rows = registry(env.RUNTIME_REGISTRY);
      const state = /^\/_state\/([A-Za-z0-9_-]{1,128})$/.exec(url.pathname);
      if (state) {
        if (!rows[state[1]] || url.search) return new Response(null, {status: 404});
        const token = request.headers.get('Authorization')?.replace(/^Bearer /, '');
        if (!token) return new Response(null, {status: 401});
        const gateway = env.GATEWAY.getByName('native-shared-v1:' + state[1]);
        return await gateway.checkpoint(request, token);
      }
      const principal = authorize(request, rows, env.SERVICE_KEY);
      if (!principal) return new Response('Unauthorized', {status: 401});
      const gateway = env.GATEWAY.getByName('native-shared-v1:' + principal.name);
      await gateway.bind(principal.name);
      if (principal.suffix === '/dashboard' && request.method === 'GET') return await gateway.status();
      if (principal.suffix === '/wake' && request.method === 'POST') return await gateway.wake();
      if (principal.suffix === '/sleep' && request.method === 'POST') return await gateway.sleep();
      if (principal.suffix === '/runtime/socket' && request.method === 'GET' && request.headers.get('Upgrade')?.toLowerCase() === 'websocket') return await gateway.fetch(request);
      return new Response(null, {status: 404});
    } catch {
      // Runtime errors can contain injected credentials. Keep them private.
      return json({error: 'native-runtime-unavailable'}, 502);
    }
  },
};
