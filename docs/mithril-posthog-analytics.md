# Optional Mithril PostHog analytics

Each active Profile has an independent choice, disabled by default. Run `mithril analytics enable --usage-consent` to enable coarse usage metadata, `mithril analytics status` to inspect it without a provider request, and `mithril analytics disable` to stop future reports. The choice is stored privately in the existing Profile home; a missing mount/home does not create a fallback. Mithril's existing Profile-scoped account credential is resolved only after consent. A local CRM bearer or another Profile's credential is never substituted.

Reports go only to `https://api.mithril.fund/v1/analytics/events`; that API projects the closed event into PostHog US project 703898. An opaque per-Profile UUID, fixed client/channel/outcome/feature and event UUID/time are the only default dimensions. The server pseudonymizes actor/Profile references. No command arguments, prompts, session transcripts, names, paths, contact records, recipient identifiers or files are exported. This does not enable Hermes/Nous shared-metrics transmission.

Normal CLI dispatch records only whether the command handler returned or failed. This is client-reported command completion, not proof that a remote job, gateway, campaign or deployment completed. Fast chat/serve entrypoints and per-turn/tool execution are not covered by this hook. `mithril analytics record` supports explicit Bot/CRM/campaign/X outcomes with an opaque campaign SHA-256 and optional independent evidence digest. It never posts to X, sends a campaign or modifies CRM records. Unknown/failed requests are not retried, and acceptance is not indexing or a business conversion.

CLI Session Replay is not applicable. Mobile/Desktop renderer Replay belongs to the Fund client implementation and requires separate user choice and installed-client qualification. No collection starts merely because this source is configured or a new release is available.

Required delivery receipts: exact-source CircleCI, integrated main CircleCI, signed runtime release, deliberate Fund/runtime pin update and actual installed-client read-back. Track coverage and remaining gaps in MITHRIL-2043.
