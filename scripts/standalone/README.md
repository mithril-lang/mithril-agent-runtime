# Independent Docker gateway qualification

## Mithril OCI fleet entrypoint

The Mithril-owned OCI image uses the explicit `mithril_gateway` target in the
repository Dockerfile. That target copies
`scripts/standalone/mithril-gateway-oci-entrypoint.sh`, scopes
`MITHRIL_AGENT=1` to the Mithril image before `/init` bootstrap, and sets the
launcher as its command while retaining the generic Docker ENTRYPOINT
dispatcher. The launcher runs one multiplexing Gateway in the foreground
with external supervision. The OCI operator owns restart, health, stop,
rollout, and rollback policy; the container must not register a competing
per-Profile background Gateway service. The default Docker build and generic
Compose example remain Hermes-compatible and are not a Mithril Gateway image.

Build and qualification must bind the launcher revision and immutable image
digest to the exact Fund source/export and catalog snapshot. Profile state and
credentials remain scoped to the owning Mithril Profile. This launcher is a
source artifact only; it does not qualify or activate the existing bot fleet.

This Mac-controlled profile qualifies the explicit `mithril_gateway` target
for the `linux/amd64` gateway trial. It does not turn the organization's held full
Python/Rust/JS/native release matrix into a completed production pipeline.
No GitHub Actions execution is used.

The controller runs only a clean committed source. Its fixed recipe streams
an exact Git archive to a disposable runner directory and builds the explicit
Mithril target without controller credentials, host source mounts or registry
login. It reads back the target image's dispatcher, command, and Mithril-mode
environment before admitting the build stage, then runs the image with its
default command so the qualification exercises the actual Mithril launcher.
The install stamp records the actual source SHA. Test dependency setup uses
Hermes PM and the frozen source lock. Mithril branding, launcher, installer and audited runtime-export regressions run alongside the API gateway, configuration, custom-provider and supervision regressions through
`scripts/run_tests.sh` in a separate container with no external network,
2 CPUs, 4 GiB memory, dropped capabilities and no Docker socket.

The controller also derives an API-only cloud trial image with UID/GID 1000,
an isolated home and an explicit Mithril inference provider definition. Its
foreground gateway delegates supervision to the hosting platform. It copies
no installed profiles, schedules, message history or messaging credentials.

Qualification starts the Mithril target with its baked dispatcher and launcher,
then separately exercises the derived trial image with its external-supervisor
Gateway command. Both use disposable named volumes and no external network;
SQLite WAL/FTS5, API authentication and persistence across restart are read
back with fixture credentials. There are no provider calls. Live inference
and platform-specific bot delivery belong to the later cloud trial.

## Configure the owning Mac

Use a private external owner JSON containing:

```json
{
  "repository": "mithril-lang/mithril-agent",
  "hostname": "ACTUAL_MAC_HOSTNAME",
  "checkout": "/absolute/path/to/clean/checkout",
  "runner": "gad"
}
```

The hostname and canonical checkout must match the actual controlling Mac.
Owner files and the state directory must be owned by the current user and have
mode 0600 and 0700, respectively. Existing SSH known-hosts and the configured
`gad` alias provide transport identity. A trial-specific secondary Docker daemon
may be selected with `socket: "/run/mithril-agent-trial/docker.sock"`; its lifetime,
capacity and memory reservation must be configured separately. The controller
never resets Docker storage or deletes unrelated volumes, containers or images.

```sh
node scripts/standalone/gateway.mjs plan
node --test tests/scripts/standalone/gateway-contract.test.mjs
node scripts/standalone/gateway.mjs verify --state /PRIVATE/gateway-ci --owner /PRIVATE/gateway-owner.json
node scripts/standalone/gateway.mjs admit --state /PRIVATE/gateway-ci --owner /PRIVATE/gateway-owner.json --receipt /PRIVATE/gateway-ci/RUN.json
```

The native amd64 runner must have at least 12 GiB available in its actual Docker
storage. Insufficient capacity fails before archive transfer or build. Each
verification attempt records a private HMAC-signed success or failure receipt
with source, recipe, owner, stages, image identity and completion time. Failure,
changed identities, stale receipts and forged signatures cannot be admitted.
Receipts identify the derived trial image and always have
`productionEligible: false`. The offline fixture supplies no provider key;
provider calls are verified separately during the authorized live cloud trial.

Trial publication needs the same qualified image, scoped trial secrets,
resource-owner checks, rollback or an immutable prior artifact, actual rollout
read-back and authenticated live inference. Integrated current main must be
qualified again before production publication. A failed receipt, focused unit
tests or a prototype Docker daemon is not an active release pipeline.

## Cloud trial checkpoints

The trial proxy restores a SHA-256 checked archive before readiness. SQLite stays
on local disk; online backup includes committed WAL rows. Configuration, routing,
trial bot state, cron, memories, skills and API idempotency state are checkpointed.
Secrets, process locks and caches are excluded. Non-streaming API writes are
checkpointed before acknowledgement; background changes are checkpointed every
60 seconds. Missing mounts or invalid checkpoints fail startup closed. Store
failures return 503. The single-writer trial does not prove exactly-once external
side effects, streaming support or zero-loss background crash recovery.

Cloudflare uses an authenticated HTTPS store. Spaces overrides the store argument
with a private mounted bucket path. Offline runtime probes bypass cloud storage
and test the underlying gateway on a disposable volume. Real proxy/store recovery
is verified separately on both live platforms.

### Native execution-tenant adapter

The encrypted Desktop gateway (`gateway-desktop.py`) can opt into the private
native protocol with operator-injected `MITHRIL_NATIVE_TENANT` JSON:

```json
{"kind":"account","accountId":"account-id"}
```

or:

```json
{"kind":"team","organizationId":"organization-id","computerId":"shared-workspace-id"}
```

One container has one execution identity. Several Hermes profiles may operate
inside it; this does not isolate their files from arbitrary shell code. Restore
checks the checkpointed `.native-tenant.json` before starting children and refuses
a different configured tenant. Boot generates a fresh `rt-` identity.

The private API bearer authenticates `/users/:id/dashboard` or
`/organizations/:id/computers/:id/dashboard`. Native `/runtime/socket` additionally
requires `X-Mithril-Runtime-Id` for the current boot and `X-Mithril-Actor-Id`, maps
only to the original `/api/ws`, and rejects query-based profile selection.
Personal actors must match the account. Team membership must be verified by the
Mithril API on every admission; possession of the private service bearer is not
public member authentication. Other native helper paths remain unsupported.

Select the encrypted Desktop entrypoint explicitly; the ordinary trial
persistence entrypoint does not enable native tenant routing. Its required
dashboard token, API bearer, checkpoint encryption key, provider credential and
private checkpoint store stay operator-owned. The provider is api.mithril.fund.

`test_gateway_tenant.py` covers checkpoint tenant reassignment refusal, changed
boot generations, stale-generation refusal, personal actors, shared workspace
routing and unsupported paths. This source adapter is not a deployed Spaces or
Cloudflare control plane, paid-launch admission, shared conversation ACLs or
live inference qualification. An earlier signed image receipt does not qualify
these new source changes.

### Shared-profile checkpoint regression details

`tests/scripts/standalone/test_gateway_persistence.py` also exercises the native
`SessionDB` in a default home and two named profile homes with the same durable
session ID. It checkpoints while the WAL databases remain open and checks the
restored message rows, usage counters, profile model, full-text search, memory,
SOUL and config independently. Ordinary checkpoints omit each profile's `.env`.
This covers native local database/file roundtrips, not member authorization,
hostile shell isolation, shared browser state, running-turn crash recovery,
real provider communication or cloud runtime capacity.
