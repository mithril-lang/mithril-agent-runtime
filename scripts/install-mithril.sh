#!/bin/sh
# Mithril's distribution boundary delegates runtime preparation to the pinned Hermes installer.
set -eu

revision='1f490be1f0e996a7f7d5930864395428b4312ed4'
branch='main'
installer_sha256='eb542ee6e9d35055223fc2b2fa84ae69a787f2e78b2fde0d3d50de699ff21c25'
repository='https://github.com/mithril-lang/mithril-agent-runtime.git'
profile_home="${HERMES_HOME:-${HOME:?HOME is required}/.mithril}"
install_dir=''
bin_dir="${HOME}/.local/bin"
dry_run=false

fail() { printf 'Mithril Agent: %s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' 'Mithril Agent installer (macOS and Linux)' \
        'Usage: sh install-mithril.sh [--home PATH] [--dir PATH] [--bin-dir PATH] [--dry-run]' \
        'Installs the Mithril fork and command. Provider/model setup is separate.' \
        'No gateway service or API key is created.'
}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --home|--dir|--bin-dir)
            [ "$#" -ge 2 ] && [ -n "$2" ] || fail "$1 requires a path"
            case "$1" in --home) profile_home=$2;; --dir) install_dir=$2;; --bin-dir) bin_dir=$2;; esac
            shift 2;;
        --dry-run) dry_run=true; shift;;
        -h|--help) usage; exit 0;;
        *) fail "Unknown option: $1";;
    esac
done
install_dir="${install_dir:-$profile_home/agent}"
for path in "$profile_home" "$install_dir" "$bin_dir"; do
    case "$path" in /*) ;; *) fail 'Install paths must be absolute';; esac
done
if [ "$dry_run" = true ]; then
    printf 'Repository: %s\nRevision: %s\nProfile: %s\nRuntime: %s\nCommand: %s/mithril\n' \
        "$repository" "$revision" "$profile_home" "$install_dir" "$bin_dir"
    exit 0
fi
command -v git >/dev/null 2>&1 || fail 'git is required'
command -v curl >/dev/null 2>&1 || fail 'curl is required'
command -v bash >/dev/null 2>&1 || fail 'bash is required'

# An existing Hermes home is shared through an alias, never copied or moved while services run.
if [ "$profile_home" = "$HOME/.mithril" ] && [ ! -e "$profile_home" ] && [ ! -L "$profile_home" ] \
    && { [ -f "$HOME/.hermes/config.yaml" ] || [ -f "$HOME/.hermes/state.db" ] \
        || [ -e "$HOME/.hermes/profiles" ] || [ -e "$HOME/.hermes/active_profile" ]; }; then
    ln -s "$HOME/.hermes" "$profile_home"
fi
if [ -L "$profile_home" ] && [ ! -d "$profile_home" ]; then
    fail 'The profile symlink does not resolve to a directory'
fi
if [ -e "$install_dir" ] || [ -L "$install_dir" ]; then
    [ -d "$install_dir/.git" ] || fail 'Existing runtime path is not an owned Git checkout'
    [ "$(git -C "$install_dir" remote get-url origin)" = "$repository" ] || fail 'Existing runtime has a different owner'
    [ -z "$(git -C "$install_dir" status --porcelain)" ] || fail 'Runtime has local changes; preserve them before installing'
    [ "$(git -C "$install_dir" rev-parse HEAD)" = "$revision" ] || fail 'Runtime version differs; use mithril update'
fi

runtime="$install_dir/.hermes/bin/hermes"
if [ ! -x "$runtime" ]; then
    temporary=$(mktemp -d "${TMPDIR:-/tmp}/mithril-install.XXXXXX")
    trap 'rm -rf "$temporary"' EXIT HUP INT TERM
    bootstrap="$temporary/install.sh"
    curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
        "https://raw.githubusercontent.com/mithril-lang/mithril-agent-runtime/$revision/scripts/install.sh" \
        --output "$bootstrap"
    if command -v sha256sum >/dev/null 2>&1; then
        digest=$(sha256sum "$bootstrap" | awk '{print $1}')
    else
        command -v shasum >/dev/null 2>&1 || fail 'A SHA-256 tool is required'
        digest=$(shasum -a 256 "$bootstrap" | awk '{print $1}')
    fi
    [ "$digest" = "$installer_sha256" ] || fail 'Installer digest differs; no runtime code was executed'
    export HERMES_HOME="$profile_home" HERMES_INSTALL_DIR="$install_dir" HERMES_REPO_URL="$repository" MITHRIL_AGENT=1
    bash "$bootstrap" --branch "$branch" --commit "$revision" --dir "$install_dir" \
        --hermes-home "$profile_home" --non-interactive --skip-setup --skip-browser --skip-computer-use
fi
[ -x "$runtime" ] || fail 'Runtime preparation did not publish the CLI'

# Bind only the Mithril command to this install; other Hermes launchers retain their own owner.
quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
mkdir -p "$bin_dir"
staged="$bin_dir/.mithril-install.$$"
(set -C; : > "$staged") || fail 'Cannot stage the Mithril command'
{
    printf '%s\n' '#!/bin/sh' '# Mithril Agent launcher; the source installer owns the runtime.'
    printf '%s\n' 'if [ -z "${HERMES_HOME:-}" ]; then'
    printf '    export HERMES_HOME=%s\n' "$(quote "$profile_home")"
    printf '%s\n' 'fi'
    printf '%s\n' 'export MITHRIL_AGENT_CLI_LAUNCHER=1'
    printf '%s\n' 'export MITHRIL_AGENT=1' 'export MITHRIL_INSTALL_HOME="$HERMES_HOME"'
    printf 'runtime_root=%s\n' "$(quote "$install_dir")"
    printf '%s\n' 'if [ -L "$HERMES_HOME/agent-current" ]; then' \
        '    runtime_root="$HERMES_HOME/agent-current"' 'fi' \
        'runtime="$runtime_root/.hermes/bin/hermes"' 'export MITHRIL_RUNTIME_ROOT="$runtime_root"'
    printf '%s\n' 'if [ "${1:-}" = "session" ]; then' '    shift'
    printf '%s\n' '    exec python3 -I "$runtime_root/scripts/mithril-runtime/session.py" "$@"'
    printf '%s\n' 'fi'
    printf '%s\n' 'case "${1:-}" in' \
        '    update) exec "$runtime" --run-module hermes_cli.mithril_updates "$@";;' \
        '    login|logout|account) exec "$runtime" --run-module hermes_cli.mithril_auth "$@";;' \
        '    auth)' \
        '        case "${2:-}" in' \
        '            login|logout|status) exec "$runtime" --run-module hermes_cli.mithril_auth "$@";;' \
        '        esac;;' \
        '    -p|--profile)' \
        '        case "${3:-}" in' \
        '            update) exec "$runtime" --run-module hermes_cli.mithril_updates "$@";;' \
        '            login|logout|account) exec "$runtime" --run-module hermes_cli.mithril_auth "$@";;' \
        '            auth)' \
        '                case "${4:-}" in' \
        '                    login|logout|status) exec "$runtime" --run-module hermes_cli.mithril_auth "$@";;' \
        '                esac;;' \
        '        esac;;' \
        'esac'
    printf '%s\n' 'exec "$runtime" "$@"'
} > "$staged"
chmod 755 "$staged"
if [ -e "$bin_dir/mithril" ] || [ -L "$bin_dir/mithril" ]; then
    mv "$bin_dir/mithril" "$bin_dir/mithril.before-$(date -u +%Y%m%dT%H%M%SZ)-$$"
fi
mv "$staged" "$bin_dir/mithril"
printf 'Mithril Agent is ready. Run %s/mithril\n' "$bin_dir"
printf '%s\n' 'Run mithril to sign in and start a session, or configure another provider with mithril setup.'
