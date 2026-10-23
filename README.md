# Mithril Agent runtime

The MIT-licensed runtime used by the Mithril CLI and Mithril Desktop, built on
Hermes Agent by Nous Research. Configuration and conversations use `~/.mithril`.

The Homebrew formula and shell installer select the same immutable runtime commit.
See [releases](https://github.com/mithril-lang/mithril-agent-runtime/releases) for CLI distribution updates.
The runtime provenance manifest binds each original file to its source revision.

Upstream project: https://github.com/NousResearch/hermes-agent
License: [MIT](LICENSE).

## Sign in to Mithril Fund

The first interactive `mithril` launch asks you to sign in to Mithril Fund in
your browser. Approve the displayed device code to continue into your session.
The approval is saved only for the selected local Profile. Cancellation, denied
approval, expiration, or a sign-in error stops the launch.

After installation, run:

```sh
mithril
mithril account status
```

Approve the code shown in the terminal in your own browser. You do not need to
copy an API key. For a remote terminal, use `mithril login --no-browser` and open
the displayed approval address on another device.

Run `mithril login` to reconnect explicitly. Saved account credentials skip the
startup prompt. Help, scripts, explicit alternative providers, managed Profiles,
and the ordinary `hermes` command keep their existing startup behavior.

`mithril logout` removes this Profile's local account credential. Revoke device
access in the [Console account page](https://console.mithril.fund/account) when
needed. Existing API keys, other providers and conversations are preserved.
Use `mithril setup` for other providers, or `mithril -p work login` for an existing
named local Profile. Explicit provider commands, including `mithril auth status
openrouter`, retain their existing behavior.
