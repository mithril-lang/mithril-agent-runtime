# Mithril VPN CLI

The existing Mithril CLI now registers `vpn status`, `vpn diagnostics`,
`vpn connect PROFILE_ID`, `vpn disconnect`, `vpn import FILE` and
`vpn remove PROFILE_ID --yes`. Each subcommand accepts `--json`.
Use profile IDs returned by `mithril vpn status`. Imports and OS authorization
remain explicit. No command starts a Desktop renderer or another vendor app.

The Linux package carries the Fund NetworkManager host pinned in
`hermes_cli/data/vpn/SOURCE.json`. It uses the existing native metadata,
private keyfiles and per-user operation lock, so Desktop and CLI share device
identity and serialize native work. NetworkManager 1.46+ and OS permission remain
system dependencies. Source tests do not establish a real gateway connection.

On macOS the CLI verifies the expected Apple signing team and
`fund.mithril.vpn-helper` identity before invoking the fixed helper path.
A qualified helper may be packaged at
`sys.prefix/share/mithril/vpn/MithrilVPNHelper.app`; an installed signed
`/Applications/Mithril.app` helper is also supported without starting its renderer.
Standalone Apple helper packaging and native installed QA remain release gates.

Windows CLI helper packaging/publisher verification is not qualified; the
command fails closed rather than executing an unsigned or guessed binary.
This does not claim Windows VPN delivery.

Only validated public state is printed. Native paths, imported profile content,
credentials and native stderr stay out of command output. Human-readable profile
names are JSON quoted to prevent terminal escape injection. Unknown outcomes
are never replayed automatically; connect/import/remove first reconcile OS
state, and profile removal requires explicit confirmation. Diagnostics queries
native state; the adapter may stop an expired tunnel under its existing expiry
policy. It does not establish public egress, DNS or leak protection.

An actual non-GCP gateway, private enrollment/revocation, signed platform
packages and independent installed traffic/lifecycle/update QA are still
required by MITHRIL-1813. No provider purchase, tunnel activation or release is
established by this source change.

The Apple helper must report controllerVersion 2. An older signed helper without the cross-process operation lock is rejected before CLI mutation.

The branded POSIX launcher dispatches `mithril vpn` directly to the standalone VPN module, as it already does for account/update operations. VPN commands do not initialize Agent configuration or require chat dependencies. The normal Agent parser retains VPN help for other entry points.
