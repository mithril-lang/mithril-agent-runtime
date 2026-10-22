#!/bin/sh
# Mithril-only OCI entrypoint. Invoke it as the image CMD so the generic
# Docker ENTRYPOINT dispatcher and s6 shutdown path remain in place.
set -eu

# Select Mithril-owned account, provider, catalog, billing, and diagnostics
# behavior before the shared Agent runtime is imported.
export MITHRIL_AGENT=1

# The OCI operator owns container restart and health policy. Keep the gateway
# in the foreground and return updates/restarts to that external owner instead
# of creating a second per-Profile s6 service inside the fleet container.
exec hermes gateway run --no-supervise --external-supervisor "$@"
