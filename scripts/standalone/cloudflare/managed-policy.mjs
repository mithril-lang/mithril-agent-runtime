const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
export function managedPrincipal(request, serviceKey) {
  const url = new URL(request.url);
  if (url.search || typeof serviceKey !== 'string' || serviceKey.length < 32 || request.headers.get('Authorization') !== 'Bearer ' + serviceKey) return null;
  const actor = request.headers.get('X-Mithril-Actor-Id');
  if (!uuid.test(actor || '')) return null;
  const account = /^\/users\/([^/]+)(\/.*)$/.exec(url.pathname);
  const team = /^\/organizations\/([^/]+)\/computers\/([^/]+)(\/.*)$/.exec(url.pathname);
  if (account && uuid.test(account[1]) && actor === account[1]) return {identity: {kind: 'account', accountId: account[1]}, actor, suffix: account[2]};
  if (team && uuid.test(team[1]) && uuid.test(team[2])) return {identity: {kind: 'team', organizationId: team[1], computerId: team[2]}, actor, suffix: team[3]};
  return null;
}
export function paidLaunch(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).sort().join() !== 'inference,meter') return null;
  const {inference, meter} = body;
  if (!inference || Object.keys(inference).sort().join() !== 'apiKey,model' || !/^mf_[A-Za-z0-9_-]{32,128}$/.test(inference.apiKey || '') || !/^[A-Za-z0-9_.:/-]{1,200}$/.test(inference.model || '')) return null;
  if (!meter || Object.keys(meter).sort().join() !== 'jobId,maximumSeconds' || !/^native-launch:[a-f0-9-]{36}$/.test(meter.jobId || '') || !uuid.test(meter.jobId.slice(14)) || !Number.isSafeInteger(meter.maximumSeconds) || meter.maximumSeconds < 120 || meter.maximumSeconds > 10800) return null;
  return {jobId: meter.jobId, maximumSeconds: meter.maximumSeconds, providerKey: inference.apiKey, model: inference.model};
}
export async function managedName(identity) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(identity)));
  return 'fund-' + Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, '0')).join('');
}
