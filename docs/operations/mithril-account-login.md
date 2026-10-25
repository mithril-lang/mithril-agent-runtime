# Mithril Fund sign-in for the Agent CLI

The first interactive `mithril` launch starts Mithril Fund device sign-in when
the active, user-managed Profile has no saved account token. The CLI opens the
existing Mithril Console device approval page. Sign in there, compare the
displayed code with the CLI, and approve **Mithril Agent CLI**. After approval,
the CLI saves the inference token for that Profile and continues into the
requested session. No API key needs to be copied.

Run `mithril login` to reconnect or sign in before starting a session. It uses
the same device approval flow.

```sh
mithril login
mithril account status
mithril
mithril logout
```

`mithril auth login`, `mithril auth status`, and `mithril auth logout` are aliases.
Explicit existing provider commands such as `mithril auth status openrouter`
and `mithril logout --provider spotify` retain their Hermes behavior.
For a headless machine, use `mithril login --no-browser` and open the displayed
approval address on another device. The code expires after ten minutes. Ctrl+C
cancels the local wait; a denied or expired request never changes configuration.

The current model is retained when it exists in the Mithril model catalog.
Otherwise the CLI uses the API's declared Profile default, or asks you to choose
a listed model. You can select one explicitly with `mithril login --model MODEL_ID`.
Signing in sends no inference request and makes no purchase.

Sign-in uses the launcher's `HERMES_HOME` (normally `~/.mithril`), honors the active
Profile, and accepts `mithril -p work login` or `mithril login --profile work`.
The Profile must already exist. Explicit `--profile default` selects the default
home. Credentials are never shared with other Profiles or uploaded to containers.

The browser approves the existing API device flow with `inference workspace:read`.
The CLI polls `/v1/device/token`, verifies `/v1/me`, then stores a dedicated
`MITHRIL_ACCOUNT_TOKEN` in the Profile's private `.env` (mode 0600). The existing
atomic config writer selects a named **Mithril Account** provider pointing only
to `https://api.mithril.fund/v1`. Existing API keys, other providers, comments,
settings, and conversations are preserved. For requests to the canonical
Mithril API, this device-approved account token takes precedence over a retained
legacy custom-provider key. The token is never sent to a different host.
Legacy credentials embedded directly in `model` are saved in a private
`backups/before-mithril-login-*.yaml` snapshot before the active route is changed.
The change applies to new sessions; running agents keep their current identity.
The CLI never approves its own code,
prints the device token, or follows API redirects.

`mithril logout` removes this Profile's local account token. It does not revoke
the server token or sign out browsers and already running processes. Revoke device
tokens from the Console account page when needed. The next interactive launch
asks for approval again. Alternative providers remain available through
`mithril setup`.

## Runtime updates

`mithril update --check` reports whether the installed runtime is behind its
selected update channel. The Mithril launcher applies available updates before
a new interactive session by default. Set `updates.auto_apply: false` in the
Profile's `config.yaml` (for example, `mithril config set updates.auto_apply false`)
to turn this off. It updates only a clean, self-updatable runtime in a TTY
session, honors `updates.check: false`, and stops before chat if an update fails.
Ordinary Hermes launches are unaffected.

The login and startup-update hooks are Mithril-owned additions. Ordinary Hermes
startup and login behavior remain unchanged. Shell and Homebrew installers use
the same login module and update policy from the pinned public runtime. Local
fixture tests do not prove a real account approval, inference, or release
publication; those are separate verification steps.
