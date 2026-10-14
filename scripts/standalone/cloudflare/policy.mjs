const id = /^[A-Za-z0-9_-]{1,128}$/;

export function registry(encoded) {
  const rows = JSON.parse(encoded);
  const used = new Set();
  const identities = new Set();
  for (const [name, row] of Object.entries(rows)) {
    if (!id.test(name) || !row || !row.identity || !row.apiKey || !row.providerKey || !/^[a-f0-9]{64}$/.test(row.encryptionKey)) throw Error('Invalid runtime registry');
    const identity = row.identity;
    const fields = identity.kind === 'account' ? ['kind', 'accountId'] : identity.kind === 'team' ? ['kind', 'organizationId', 'computerId'] : [];
    if (!fields.length || Object.keys(identity).length !== fields.length || fields.slice(1).some(k => !id.test(identity[k]))) throw Error('Invalid execution identity');
    if (identities.has(prefix(identity))) throw Error('Duplicate execution identity');
    identities.add(prefix(identity));
    for (const key of [row.apiKey, ...Object.keys(row.members || {})]) {
      if (key.length < 32 || used.has(key)) throw Error('Invalid runtime credential');
      used.add(key);
    }
    if (Object.values(row.members || {}).some(actor => !id.test(actor) || (identity.kind === 'account' && actor !== identity.accountId))) throw Error('Invalid actor');
  }
  return rows;
}

export function prefix(identity) {
  return identity.kind === 'account' ? `/users/${identity.accountId}` : `/organizations/${identity.organizationId}/computers/${identity.computerId}`;
}

export function authorize(request, rows, serviceKey) {
  const url = new URL(request.url);
  if (url.search) return null;
  const entry = Object.entries(rows).find(([, row]) => url.pathname.startsWith(prefix(row.identity) + '/'));
  if (!entry) return null;
  const [name, row] = entry;
  const key = request.headers.get('Authorization')?.replace(/^Bearer /, '');
  const actor = typeof serviceKey === 'string' && serviceKey.length >= 32 && key === serviceKey ? request.headers.get('X-Mithril-Actor-Id') : row.members?.[key];
  if (!id.test(actor || '') || (row.identity.kind === 'account' && actor !== row.identity.accountId)) return null;
  return {name, actor, suffix: url.pathname.slice(prefix(row.identity).length)};
}

export const storagePrefix = name => `native-shared-v1/${name}/`;
