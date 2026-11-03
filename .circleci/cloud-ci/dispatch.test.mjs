import { test } from 'node:test';
import assert from 'node:assert/strict';
import { credential, dispatch, environmentIdentity, sourceIdentity, circleClient, scope } from './dispatch.ts';

const sha = 'a'.repeat(40);
const id = '11111111-1111-4111-8111-111111111111';
const child = '22222222-2222-4222-8222-222222222222';
function metadata(source = sha) {
  return { number: 1, trigger_parameters: {
    github_app: { owner: 'mithril-lang', repo_name: 'mithril-agent-runtime', full_ref: 'refs/heads/main', commit_sha: source },
    git: { repo_owner: 'mithril-lang', repo_name: 'mithril-agent-runtime', ref: 'refs/heads/main', checkout_sha: source },
  }};
}
const env = {
  CIRCLECI: 'true', CIRCLE_PROJECT_USERNAME: 'mithril-lang',
  CIRCLE_PROJECT_REPONAME: 'mithril-agent-runtime', CIRCLE_BRANCH: 'main',
  CIRCLE_SHA1: sha, MITHRIL_CIRCLE_PIPELINE_ID: id,
  CIRCLE_WORKFLOW_ID: id, CIRCLE_WORKFLOW_JOB_ID: id,
  OP_SERVICE_ACCOUNT_TOKEN: 'synthetic-service-account-not-a-secret',
};

test('only hosted trusted main can enter the cloud credential lane', () => {
  assert.equal(environmentIdentity(env), sha);
  for (const patch of [
    { CIRCLECI: 'false' }, { CIRCLE_BRANCH: 'feature' },
    { CIRCLE_PROJECT_REPONAME: 'other' }, { OP_SERVICE_ACCOUNT_TOKEN: '' },
    { CIRCLE_SHA1: 'main' }, { SSH_CONNECTION: 'synthetic-ssh' },
    { MITHRIL_CIRCLE_PIPELINE_ID: '' },
  ]) assert.throws(() => environmentIdentity({ ...env, ...patch }), /hosted_identity_rejected/);
});
test('allowlisted secret resolves, Private access must be denied', async () => {
  const seen = [];
  const token = 'synthetic-circle-token-for-testing';
  assert.equal(await credential(async ref => {
    seen.push(ref); if (ref === scope.secret) return token; throw Error('synthetic denied');
  }), token);
  assert.deepEqual(seen, [scope.secret, scope.deniedSecret]);
  await assert.rejects(credential(async () => { throw Error('sensitive SDK detail'); }), /credential_denied/);
  await assert.rejects(credential(async () => token), /private_vault_access_not_denied/);
  await assert.rejects(credential(async () => ''), /credential_invalid/);
});
test('both modern provider source bindings and repository/ref must agree', () => {
  assert.equal(sourceIdentity(metadata()), sha);
  for (const mutate of [
    m => { m.trigger_parameters.git.checkout_sha = 'b'.repeat(40); },
    m => { m.trigger_parameters.git.ref = 'refs/heads/feature'; },
    m => { m.trigger_parameters.github_app.owner = 'other'; },
    m => { delete m.trigger_parameters.git; },
  ]) { const m = metadata(); mutate(m); assert.throws(() => sourceIdentity(m), /source_binding_rejected/); }
});
function fixture({ authority = scope.authority, parentSource = sha, childSource = sha, postFailure = false } = {}) {
  const calls = [];
  return { calls, async call(path, method = 'GET', body) {
    calls.push({ path, method, body });
    if (path === '/api/v2/me') return { id: authority };
    if (path === '/api/v2/pipeline/' + id) return metadata(parentSource);
    if (method === 'POST') {
      if (postFailure) throw Error('private transport detail');
      return { id: child };
    }
    if (path === '/api/v2/pipeline/' + child) return metadata(childSource);
    throw Error('unexpected request');
  }};
}
test('real trigger shape is fixed to validation, config and checkout main; child source is read back', async () => {
  const f = fixture();
  assert.deepEqual(await dispatch(f.call, id, sha), { pipelineId: child, number: 1, sourceSha: sha });
  const post = f.calls.find(c => c.method === 'POST');
  assert.equal(post.path, '/api/v2/project/gh/mithril-lang/mithril-agent-runtime/pipeline/run');
  assert.deepEqual(post.body, {
    definition_id: scope.definition, config: { branch: 'main' }, checkout: { branch: 'main' },
    parameters: { scheduled_profile_pilot_validation: true },
  });
  assert.equal(f.calls.filter(c => c.method === 'POST').length, 1);
  assert.equal(f.calls.at(-1).path, '/api/v2/pipeline/' + child);
});
test('authority or parent mismatch prevents any trigger; changed child does not become success', async () => {
  for (const options of [{ authority: 'other' }, { parentSource: 'b'.repeat(40) }]) {
    const f = fixture(options);
    await assert.rejects(dispatch(f.call, id, sha));
    assert.equal(f.calls.filter(c => c.method === 'POST').length, 0);
  }
  const f = fixture({ childSource: 'b'.repeat(40) });
  await assert.rejects(dispatch(f.call, id, sha), /trigger_source_changed/);
});
test('an uncertain trigger is never automatically retried or redirected to a desktop fallback', async () => {
  const f = fixture({ postFailure: true });
  await assert.rejects(dispatch(f.call, id, sha), /trigger_outcome_uncertain/);
  assert.equal(f.calls.filter(c => c.method === 'POST').length, 1);
});
test('HTTP client rejects arbitrary URLs, publication/approval and oversized bodies without leaking raw errors', async () => {
  const seen = [];
  const call = circleClient('synthetic', async (url, options) => {
    seen.push({ url, options }); return new Response(JSON.stringify({ id: scope.authority }));
  });
  for (const path of ['https://example.invalid/', '/api/v2/context', '/api/v2/workflow/' + id + '/approve/' + id]) {
    await assert.rejects(call(path), /circle_operation_rejected/);
  }
  assert.equal(seen.length, 0);
  await call('/api/v2/me');
  assert.equal(seen[0].options.redirect, 'error');
  const failure = circleClient('synthetic', async () => { throw Error('DO-NOT-LOG-RAW-ERROR'); });
  await assert.rejects(failure('/api/v2/me'), error => error.code === 'circle_request_failed' && !error.message.includes('DO-NOT'));
  const oversized = circleClient('synthetic', async () => new Response('x'.repeat(262145)));
  await assert.rejects(oversized('/api/v2/me'), /circle_response_too_large/);
});
