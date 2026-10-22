export const HERMES_AGENT_DOCS_URL = "https://hermes-agent.nousresearch.com/docs/";
export const MITHRIL_AGENT_DOCS_URL = "https://docs.mithril.fund/reference/mcp/";
export const MITHRIL_AGENT_HOME_URL = "https://mithril.fund/";

/** Unknown status defaults to the owned Mithril surface. */
export function isMithrilDistribution(distribution: string | null | undefined): boolean {
  return distribution !== "hermes";
}

export function agentDocsUrlForDistribution(distribution: string | null | undefined): string {
  return isMithrilDistribution(distribution)
    ? MITHRIL_AGENT_DOCS_URL
    : HERMES_AGENT_DOCS_URL;
}
