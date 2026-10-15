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
