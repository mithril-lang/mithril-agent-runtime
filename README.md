# Mithril Agent runtime

The MIT-licensed runtime used by the Mithril CLI and Mithril Desktop, built on
Hermes Agent by Nous Research. Configuration and conversations use `~/.mithril`.

This is a candidate runtime source snapshot for installation qualification.
Public installer availability is announced at https://console.mithril.fund/agent.

## Sign in to Mithril Fund

After installation, run:

```sh
mithril login
mithril account status
mithril
```

Approve the code shown in the terminal in your own browser. You do not need to
copy an API key. For a remote terminal, use `mithril login --no-browser` and open
the displayed approval address on another device. `mithril logout` removes this
Profile's local account credential. Revoke device access in the
[Console account page](https://console.mithril.fund/account) when needed.
Existing API keys, other providers and conversations are preserved.

## Runtime updates

`mithril update --check` reports whether the installed runtime is behind its
selected update channel. The Mithril launcher applies available updates before a
new interactive session by default. It updates only a clean, self-updatable
runtime in a TTY session, honors `updates.check: false`, and stops before chat if
an update fails. Set `updates.auto_apply: false` in the Profile's `config.yaml`
to turn automatic updates off. See
[the sign-in and update policy](docs/operations/mithril-account-login.md).

The runtime provenance manifest binds source files to their source revision and
lists any distribution-owned runtime overrides separately.

Upstream project: https://github.com/NousResearch/hermes-agent
License: [MIT](LICENSE).
