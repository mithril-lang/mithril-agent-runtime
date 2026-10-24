# Mithril Agent runtime

The MIT-licensed runtime used by the Mithril CLI and Mithril Desktop, built on
Hermes Agent by Nous Research. Configuration and conversations use `~/.mithril`.

This is a candidate runtime source snapshot for installation qualification.
Public installer availability is announced at https://console.mithril.fund/agent.
The runtime provenance manifest binds each original file to its source revision.

Upstream project: https://github.com/NousResearch/hermes-agent
License: [MIT](LICENSE).

## Sign in to Mithril Fund

After installation, run:

```sh
mithril login
mithril account status
mithril
```

Approve the code shown in the terminal in your own browser. You do not need to
copy an API key. For a remote terminal, use `mithril login --no-browser` and open
the displayed approval address on another device.

`mithril logout` removes this Profile's local account credential. Revoke device
access in the [Console account page](https://console.mithril.fund/account) when
needed. Existing API keys, other providers and conversations are preserved.
Use `mithril setup` for other providers, or `mithril -p work login` for an existing
named local Profile. Explicit provider commands, including `mithril auth status
openrouter`, retain their existing behavior.

## Native Linear connection candidate

This section describes an unqualified source candidate, not an available or
verified hosted Linear connection. A runtime flag or local test does not
provision the Mithril connector API, its OAuth provider, or an installed release.

The candidate adds `mithril login --connectors` to request connector permissions
for the current Profile without changing its inference model or provider.
It requests `connectors:read` and `connectors:write` in addition to the existing
`inference` and `workspace:read` scopes; it does not itself connect a Linear
account, approve a Linear action, or grant access to another Profile.

Before using a hosted connection, the original service owners must establish
the Mithril-owned catalog, consent, account, policy and execution routes; qualify
the exact runtime/API source and integrated main; and verify the installed
runtime plus authenticated provider read-back. Signing in alone does not prove
that `manage_connections` can connect Linear or that reads/writes work. Keep the
candidate Draft until those existing gates are met. Local MCP servers use their
own configuration and authorization and are separate from this hosted route.

Current readiness and remaining contracts are tracked in
[MITHRIL-450](https://linear.app/kotobalabs/issue/MITHRIL-450),
[MITHRIL-58](https://linear.app/kotobalabs/issue/MITHRIL-58) and
[MITHRIL-248](https://linear.app/kotobalabs/issue/MITHRIL-248).
