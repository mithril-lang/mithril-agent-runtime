import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

export const scope = Object.freeze({
  repo: 'mithril-lang/mithril-agent-runtime',
  definition: '63df8e5b-cebe-4e5f-9ab9-10b87a0ac23f',
  authority: 'e8dfef15-3d84-4bb8-a4f3-bbd9cb9aae90',
  secret: 'op://smythsknhe3bz3sh4uj4cczs7i/qydsaijhrvy3cxhyo65nanblbe/credential',
  deniedSecret: 'op://crbxe6d2wjxyl7gvtbtyglj74y/mnlzd2mbpknh5ikckdf5hivgt4/vg34c42ybsr5st4k5n42vzepwu',
});

type JsonObject = Record<string, unknown>;
type Call = (path: string, method?: 'GET' | 'POST', body?: JsonObject) => Promise<JsonObject>;
type ResolveSecret = (reference: string) => Promise<string>;
export class DispatchFailure extends Error {
  readonly code: string;
  constructor(code: string) { super(code); this.code = code; }
}

export function sourceIdentity(metadata: JsonObject): string {
  const t = metadata.trigger_parameters as JsonObject | undefined;
  const app = t?.github_app as JsonObject | undefined;
  const git = t?.git as JsonObject | undefined;
  if (!app || !git || app.owner !== 'mithril-lang' ||
      app.repo_name !== 'mithril-agent-runtime' ||
      git.repo_owner !== 'mithril-lang' || git.repo_name !== 'mithril-agent-runtime' ||
      app.full_ref !== 'refs/heads/main' || git.ref !== 'refs/heads/main' ||
      typeof app.commit_sha !== 'string' || app.commit_sha !== git.checkout_sha ||
      !/^[a-f0-9]{40}$/.test(app.commit_sha)) {
    throw new DispatchFailure('source_binding_rejected');
  }
  return app.commit_sha;
}

export function environmentIdentity(env: Record<string, string | undefined>): string {
  if (env.CIRCLECI !== 'true' ||
      env.CIRCLE_PROJECT_USERNAME + '/' + env.CIRCLE_PROJECT_REPONAME !== scope.repo ||
      env.CIRCLE_BRANCH !== 'main' || !/^[a-f0-9]{40}$/.test(env.CIRCLE_SHA1 ?? '') ||
      !env.MITHRIL_CIRCLE_PIPELINE_ID || !env.CIRCLE_WORKFLOW_ID || !env.CIRCLE_WORKFLOW_JOB_ID ||
      !env.OP_SERVICE_ACCOUNT_TOKEN || env.SSH_CONNECTION || env.SSH_CLIENT) {
    throw new DispatchFailure('hosted_identity_rejected');
  }
  return env.CIRCLE_SHA1!;
}

export async function credential(resolveSecret: ResolveSecret): Promise<string> {
  let token: string;
  try { token = await resolveSecret(scope.secret); }
  catch { throw new DispatchFailure('credential_denied'); }
  if (typeof token !== 'string' || token.length < 20 || token.includes('\n')) {
    throw new DispatchFailure('credential_invalid');
  }
  let denied = false;
  try { await resolveSecret(scope.deniedSecret); }
  catch { denied = true; }
  if (!denied) throw new DispatchFailure('private_vault_access_not_denied');
  return token;
}

export async function dispatch(call: Call, parent: string, expected: string) {
  if (!/^[a-f0-9]{40}$/.test(expected)) throw new DispatchFailure('expected_source_invalid');
  const identity = await call('/api/v2/me');
  if (identity.id !== scope.authority) throw new DispatchFailure('circle_authority_rejected');
  const parentMetadata = await call('/api/v2/pipeline/' + parent);
  if (sourceIdentity(parentMetadata) !== expected) throw new DispatchFailure('parent_source_rejected');
  let result: JsonObject;
  try {
    // One attempt only. There is no retry or desktop/CLI fallback.
    result = await call('/api/v2/project/gh/' + scope.repo + '/pipeline/run', 'POST', {
      definition_id: scope.definition,
      config: { branch: 'main' },
      checkout: { branch: 'main' },
      parameters: { scheduled_profile_pilot_validation: true },
    });
  } catch { throw new DispatchFailure('trigger_outcome_uncertain'); }
  if (typeof result.id !== 'string' || !/^[a-f0-9-]{36}$/.test(result.id)) {
    throw new DispatchFailure('trigger_outcome_uncertain');
  }
  let metadata: JsonObject;
  try { metadata = await call('/api/v2/pipeline/' + result.id); }
  catch { throw new DispatchFailure('trigger_readback_uncertain'); }
  if (sourceIdentity(metadata) !== expected) {
    throw new DispatchFailure('trigger_source_changed');
  }
  return { pipelineId: result.id, number: metadata.number, sourceSha: expected };
}

export function circleClient(token: string, fetcher: typeof fetch = fetch): Call {
  return async (path, method = 'GET', body) => {
    if (!/^\/api\/v2\/(?:me$|pipeline\/[a-f0-9-]{36}$|project\/gh\/mithril-lang\/mithril-agent-runtime\/pipeline\/run$)/.test(path) ||
        (method === 'POST') !== path.endsWith('/pipeline/run')) {
      throw new DispatchFailure('circle_operation_rejected');
    }
    try {
      const response = await fetcher('https://circleci.com' + path, {
        method, redirect: 'error', signal: AbortSignal.timeout(12000),
        headers: { 'Circle-Token': token, 'Content-Type': 'application/json' },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (!response.ok || !response.body) throw new DispatchFailure('circle_request_failed');
      const reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        for (;;) {
          const chunk = await reader.read();
          if (chunk.done) break;
          size += chunk.value.byteLength;
          if (size > 262144) throw new DispatchFailure('circle_response_too_large');
          chunks.push(chunk.value);
        }
      } finally { await reader.cancel(); }
      const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new DispatchFailure('circle_response_invalid');
      return data;
    } catch (error) {
      if (error instanceof DispatchFailure) throw error;
      throw new DispatchFailure('circle_request_failed');
    }
  };
}

async function main() {
  const out = '/tmp/mithril-cloud-ci';
  await mkdir(out, { recursive: true, mode: 0o700 });
  const receipt: JsonObject = {
    schema: 1, provider: 'circleci', sdk: '@1password/sdk@0.5.0',
    status: 'failed', operation: 'runtime_pilot_validation',
    parentPipelineId: process.env.MITHRIL_CIRCLE_PIPELINE_ID,
    workflowId: process.env.CIRCLE_WORKFLOW_ID,
    jobId: process.env.CIRCLE_WORKFLOW_JOB_ID,
    desktopFallback: false, publicationSupported: false,
  };
  try {
    const source = environmentIdentity(process.env);
    receipt.sourceSha = source;
    const sdk = await import('@1password/sdk');
    const client = await sdk.createClient({
      auth: process.env.OP_SERVICE_ACCOUNT_TOKEN!,
      integrationName: 'Mithril Cloud CI Dispatcher',
      integrationVersion: '1.0.0',
    });
    const token = await credential(reference => client.secrets.resolve(reference));
    receipt.privateVaultDenied = true;
    const call = circleClient(token);
    Object.assign(receipt, await dispatch(call, process.env.MITHRIL_CIRCLE_PIPELINE_ID!, source));
    receipt.status = 'triggered_source_verified';
  } catch (error) {
    receipt.reason = error instanceof DispatchFailure ? error.code : 'cloud_identity_failed';
    process.exitCode = 1;
  }
  // Fixed content-free receipt. Never serialize SDK/HTTP errors, tokens or environment.
  await writeFile(out + '/qualification.json', JSON.stringify(receipt, null, 2) + '\n', { mode: 0o600 });
  console.log(JSON.stringify(receipt));
}
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) await main();
