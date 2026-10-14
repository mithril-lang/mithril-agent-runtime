# Cloudflare native shared runtime trial

This adapter runs the original Hermes gateway and Desktop RPC server in one
Linux container per execution tenant. A tenant is a personal account or an
explicitly shared organization computer. Profiles live inside that tenant.
The trial is separate from `mithril-hermes-gateway-trial` and existing bots.

`NativeSharedGateway` owns the container lifecycle. Authenticated GET dashboard
lookups do not wake sleeping compute. POST wake restores the encrypted R2 archive
before readiness. POST sleep refuses open WebSocket connections, quiesces the
gateway, saves and reads back an immutable encrypted checkpoint, then destroys
the container. An idle alarm follows the same path. A new boot receives a fresh
runtime generation and checkpoint lease; stale runtime and storage requests fail.
The checkpoint restores the allowlisted Hermes profiles, SQLite databases,
conversation history, credentials and configuration. Arbitrary workspace trees,
browser caches and active background jobs are not covered by this trial contract.
Do not enable background schedules in the trial tenant.

## Private configuration

Create an owner-only JSON outside source with these two secret bindings:

- `SERVICE_KEY`: a dedicated private API-to-runtime bearer. The Fund API must
  perform fresh membership checks before asserting `X-Mithril-Actor-Id`.
- `RUNTIME_REGISTRY`: an encoded JSON object keyed by stable trial tenant name.
  Each row has `identity`, `apiKey`, `providerKey`, `encryptionKey` and `members`.
  `identity` uses the account/team shape documented in the parent README.
  `apiKey` is a unique container-internal bearer of at least 32 characters;
  `providerKey` is an operator-approved inference token for
  `https://api.mithril.fund/v1`; `encryptionKey` is 32 random bytes in hex.
  `members` maps unique trial capability bearers to actor IDs. A member cannot
  change its actor with a header or choose another tenant or profile by query.

Trial member capabilities are a bounded live-test access mechanism. They do not
represent deployed Fund organization membership or automatic billing admission.
All members of an explicitly shared tenant can access its profiles and files.
Individual private conversations belong in personal tenants.

## Qualification and publication

Run the approved local Mac gateway profile against a clean exact commit. Then:

```sh
python scripts/standalone/cloudflare/deploy.py \
  --state /PRIVATE/native-shared-ci \
  --receipt /PRIVATE/native-shared-ci/RUN.json \
  --secrets /PRIVATE/native-shared-secrets.json \
  --wrangler /APPROVED/wrangler \
  --output /Volumes/MithrilWork/scratch/native-shared-deployment
```

The publisher verifies the signed receipt, clean source, image SHA/architecture,
runtime UID and Cloudflare account before deploying a digest-pinned image. It
preserves previous deployment metadata and uses new Durable Objects plus the
`native-shared-v1/` R2 prefix. The maximum is two trial containers. It never
changes the existing gateway configuration or treats a focused test as a release
receipt. Current main must be separately qualified for a production publication.

Run the live probe with an approved PM-managed Python environment containing
aiohttp, a dedicated two-member team and the same private secret configuration:

```sh
python scripts/standalone/cloudflare/live-probe.py \
  --secrets /PRIVATE/native-shared-secrets.json --tenant TEAM_NAME \
  --output /Volumes/MithrilWork/scratch/native-shared-live-result.json
```

It verifies two actors share one runtime, real Hermes inference, refusal to sleep
while connected, durable sleep, non-waking lookups, new generation on recovery,
stale-generation rejection, shared SQLite-backed history and a second real
inference turn after restore. It leaves compute sleeping. It emits booleans and
runtime identities without credentials or conversation contents. A passing
direct inference request outside Cloudflare does not satisfy this probe.

Before general availability, qualify automatic membership revocation through the
deployed Fund API, paid launch admission, background task draining and scheduling,
workspace/browser-state persistence, concurrency leases, deployment rollback and
the actual installed Desktop connection. These remain separate delivery gates.

## Fund-managed runtime candidate

`managed-worker.mjs` and `managed-controller.mjs` add permanently bound Fund account/team tenants behind the dedicated service credential. Fund derives membership and billing ownership; browser and Desktop credentials never enter this control plane. Client tenant/profile overrides and query credentials are refused.

A paid launch claims one job, an inference-only credential and a bounded lease. Replays read the same job; another job is refused until settlement completes. The controller stops at the budget deadline even if active sockets or a failed checkpoint prevent graceful quiescence. Such a stop reports `last-durable-checkpoint`, preserving the distinction from an acknowledged final checkpoint. An idempotent private Fund callback settles measured seconds and revokes the inference token. Failed callback responses retain the exact duration and retry without waking compute.

This candidate is not deployed by the isolated static-registry publisher. It needs its own managed Worker owner/configuration, current-main signed qualification, a fixed two-instance initial cap, private settlement credential and live Fund/installed-client read-back before activation. The existing shared trial remains unchanged.


## Dedicated managed publication

The `managed-gateway-linux-amd64` profile separately qualifies the managed source and publishing contract. The original trial profile and its receipts remain production-ineligible. A dedicated private owner must bind the fixed Fund account, `mithril-hermes-runtime`, `mithril-hermes-runtime-state`, and at most two instances. The managed publisher requires clean exact integrated current main and a fresh signed managed receipt. It refuses replacement of an existing managed Worker until a separately verified drained rollout/rollback contract is available; it never repurposes a trial Worker.

`deploy-managed.py` prepares the first publication with private service and settlement credentials. Fund must remain disabled until Worker health, immutable image provenance, tenant isolation and settlement configuration are read back. Later rollout remains held rather than assuming that a version list is rollback proof. Initial model injection initializes fresh tenant configuration; restored profile preferences remain intact.


Managed publication requires three distinct owner-only secret values: `SERVICE_KEY`, `SETTLEMENT_KEY`, and `CF_ANALYTICS_READ_TOKEN`. The Analytics token is restricted to Fund Account Analytics Read; it is separate from the local Wrangler deployment credential and never enters the container. Before uploading, the publisher reads the fixed Fund account's label-filtered Containers usage query. Empty usage validates access and schema, not a measured charge. After private secret publication, the owning service authenticates `/_qualification/meter` and reads the exact source and qualified meter back from `/health`. These reads start no container. Fund activation and real account/team launch, inference, recovery and settlement remain separate gates.

A disabled bootstrap can be corrected with `--bootstrap-from /PRIVATE/previous/publication-state.json`. This is limited to a runtime that Fund has not activated: live API settings must contain no native bindings, the current runtime source/image/namespace must match the private bootstrap record, the application must have no active, assigned, starting or scheduling instances, and there must be exactly one 100% Worker rollback version. The publisher repeats those checks immediately before upload and preserves the previous image, namespace and actual rollback version. An activated or busy runtime still refuses replacement. Unknown bootstrap publication is reconciled by actual live read-back; an input file alone cannot authorize replacement.

Workers fetch uses `redirect: 'manual'` for provider metering and Fund settlement. Redirect responses fail qualification or leave settlement pending; credentials are never forwarded to another origin. The unsupported Workers `error` redirect mode must not be used.
