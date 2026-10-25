# Mithril CLI signed updates

This is an unreleased candidate pending exact-source CI and installed-client
qualification. Jun approved the dedicated CLI signing root on 2026-10-25.
The private key was saved to 1Password Private and projected into the runtime
CircleCI project. Its public-key SHA-256 fingerprint is
`a04b726c147049b395338db49d884fd63b570bb87be14427c4508a080c575b34`.
This provisioning is separate from a published and verified release.

## Proposed release authority

The existing `mithril-lang/mithril-agent-runtime` GitHub repository owns preview
release assets. Use its existing Releases origin; no new API endpoint, bucket,
or Gateway update service is introduced. The accountable release owner must
approve a dedicated Ed25519 CLI update key and its public fingerprint before
the root is added to `update-trust.json` in a qualified release.

The private key belongs in the owner's existing 1Password vault, projected only
into this repository's CircleCI release job as
`MITHRIL_CLI_UPDATE_SIGNING_KEY`. It must never be committed, printed, included in
an artifact or inherited by an Agent/Gateway process. Catalog signing and CI
qualification receipts have separate authorities.

## Release document

Publish `mithril-update.json` and `install-mithril.sh` as assets of the same
preview release. The JSON envelope has `key_id`, base64 `signature`, and `signed`.
The signature covers UTF-8 JSON of `signed`, with sorted keys, ASCII escapes and
separators `,` / `:`. Duplicate JSON keys are rejected.

The signed payload binds schema 1, product `mithril-cli`, repository, channel
`preview`, preview version, monotonically increasing sequence, UTC issued and
expiry times (at most 31 days), exact runtime Git revision, installer asset URL,
length and SHA-256, canonical Fund source revision and the runtime provenance
manifest SHA-256. `previous_revisions` lists the exact installed revisions for
which update and rollback compatibility were qualified; an unlisted current
checkout cannot be updated implicitly. A signer entry has its raw 32-byte public key in base64 and
`revoked: false`; an absent or revoked signer is refused.

Release discovery is bounded to the existing GitHub API and the exact
repository's preview assets. Redirects may reach only GitHub's HTTPS release
storage hosts. Inference credentials are never sent to the release service.
Wrong owner, modified metadata, unknown/revoked keys, expiry, future timestamps,
lower sequence, changed content at an accepted sequence, modified/partial
installer bytes and mismatched installed provenance all fail before selection.

## Client lifecycle

Only the Mithril launcher opts into these updates. Before eligible interactive
chat, it checks `updates.check` and `updates.auto_apply` on the already selected
Profile. Managed Profiles, help, scripts and explicit alternate providers retain
their existing behavior. `mithril update --check` is explicit readback;
`mithril update` requests installation and `mithril update --rollback` selects
the previously recorded runtime. Ordinary Hermes retains its updater.

The Homebrew wrapper carries the same verifier and public root for the one-time
transition from preview.6, which predates the updater. After upgrading the
wrapper, run `mithril update` to adopt the signed runtime. Its registered PM
interpreter and normal bootstrap are used; no old checkout is rewritten. Later
interactive launches use the installed runtime's updater automatically.

The signed installer runs with a fresh HOME and Profile, sharing only PM tools
and dependency stores. It prepares a new immutable checkout on the existing
runtime's Work volume. No live token, configuration, DB or session is supplied
to the installer. The candidate's revision, provenance hash and actual `--help`
execution must pass before selection.

`agent-current` is an atomically replaced symlink under the original install
home. A journal is written before selection. Old checkouts, environment leases
and running CLI/Gateway processes stay in place. The new interactive process
reexecutes once with its original Profile arguments. No running Gateway is
self-updated or restarted. Interrupted selection requires explicit rollback;
failed preparations and their logs remain for review. Rollback preserves the
highest accepted signed sequence, so it does not permit network replay.

## Owner release and recovery checklist

1. Approve the public root fingerprint and the dedicated 1Password/CircleCI
   credential projection. No developer-generated test key is production trust.
2. Qualify exact source and integrated main through CircleCI, including signed
   metadata negative cases, active-old-runtime retention, rollback, Profile
   opt-outs, endpoint denial and the full real installer.
3. Publish assets only after the owned release job independently reads back
   successful exact-source qualification and artifact hashes. Sign only those
   reviewed bytes. Publishing scripts must not accept a locally fabricated
   receipt as release authority.
4. Independently download the public document and installer, verify with the
   approved root, then exercise an installed N-to-N+1 update, restart, config and
   credential retention, rollback and restored N-to-N+1 update on this Mac.
5. For planned rotation, qualify a release that carries both approved public
   roots before switching the publisher key. Revoke through a newly qualified
   trust-root release. Never accept a new key merely because a manifest names
   it. Emergency root compromise requires an out-of-band verified installer;
   do not bypass signatures or anti-rollback checks. The release owner must
   retain the offline recovery copy and record the rotation/revocation receipt.

Windows, Desktop and fleet rollout retain their own platform contracts. This
POSIX CLI slice does not complete those targets or MITHRIL-252 as a whole.
