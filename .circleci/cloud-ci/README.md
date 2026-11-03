# Cloud CI credential and validation bootstrap

This TypeScript job uses 1Password Service Account authentication inside hosted CircleCI. It never runs `op`, reads Keychain, contacts a desktop agent, or falls back to the Mac. Node 24 executes erasable TypeScript directly. SDK 0.5.0 and its core dependency are integrity-pinned; install uses `npm ci --ignore-scripts`.

## Authority and invocation

Owner: Jun Kawasaki. Linear: MITHRIL-2008. Context `mithril-ci-cloud-dispatch` (`831ced96-7200-4be1-8766-f5aa8a364536`) contains only `OP_SERVICE_ACCOUNT_TOKEN`. Its provider-enforced project restriction is `mithril-agent-runtime` (`47641148-9a81-471d-88dd-ee4f37a85df4`); its expression requires main, no SSH and no API-injected configuration. Normal GitHub VCS/main execution runs the bootstrap workflow. Pilot-validation child pipelines cannot enter it, preventing recursion. No new periodic schedule is enabled.

Service Account `VNK4OF3QVRDSDL56EP6UBNZ5HE` has only `read_items` on dedicated vault `smythsknhe3bz3sh4uj4cczs7i`, with 30-day expiry from 2026-11-03. Owner backup: Private item `g5whiwnoulfme24koz6fnflfii`. Rotate the owner backup and Context before expiry. The original CircleCI credential remains Private item `mnlzd2mbpknh5ikckdf5hivgt4`; the single dedicated-vault projection must rotate with it. No signing or publication secret is in this vault. The job proves the projected credential is readable and the original Private reference is denied before making a CI request.

The only mutation supported is a single runtime pilot-validation pipeline trigger, fixed definition `63df8e5b-cebe-4e5f-9ab9-10b87a0ac23f`, main config/checkout, `scheduled_profile_pilot_validation=true`, and no release parameters. Parent and child modern GitHub App source metadata must both match the exact hosted job SHA and canonical repository/ref. A main advance causes an explicit failure. The child pipeline ID is independently readable through CircleCI. This is a validation bootstrap, not a general Fund/native/CLI publisher or approval service.

## Failure, retry and evidence

HTTP requests reject redirects, cap responses at 256 KiB and have a 12-second deadline. Raw SDK, HTTP, environment and credential values are never logged. Failure receipts contain fixed reason codes only. The trigger is attempted once. An uncertain result is not retried, cancelled or replayed automatically; the operator must reconcile the CircleCI pipeline list before deliberately running another job. This lane only starts isolated validation, never product mutations. Generic durable publication/retry ownership and qualified remote publisher invocation remain separate work; do not infer those from this bootstrap.

The private CircleCI artifact `cloud-ci-dispatch/qualification.json` records source, parent/child pipeline, workflow/job identity, read denial and sanitized outcome. Retain its provider job URL and read it independently. `triggered_source_verified` means the child was created with the expected source; it does not mean child tests, integration or publication passed. Required completion evidence includes actual hosted SDK retrieval, child workflow success, a second fresh SDK process after runner restart, missing/invalid-token denial, Context branch/SSH denial, and independent readback. Provider identity/configuration alone is insufficient.
