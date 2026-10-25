import { useEffect, useLayoutEffect, useState } from "react";
import { ExternalLink } from "lucide-react";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { agentDocsUrlForDistribution, HERMES_AGENT_DOCS_URL } from "@/lib/agent-docs";
import { PluginSlot } from "@/plugins";

const DS_BUTTON_OUTLINED_LINK_CN = cn(
  "group relative inline-grid grid-cols-[auto_1fr_auto] items-center",
  "px-[.9em_.75em] py-[1.25em] gap-2",
  "leading-0 font-bold tracking-[0.2em] uppercase",
  "text-midground bg-transparent shadow-midground",
  "shadow-[inset_-1px_-1px_0_0_#00000080,inset_1px_1px_0_0_#ffffff80]",
);

export default function DocsPage() {
  const { t } = useI18n();
  const { setEnd } = usePageHeader();
  const [docsUrl, setDocsUrl] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    api.getStatus()
      .then((status) => {
        if (mounted) {
          setDocsUrl(
            agentDocsUrlForDistribution(status.distribution),
          );
        }
      })
      // If the distribution cannot be identified, keep the Mithril surface
      // on Mithril-owned documentation instead of opening Hermes docs.
      .catch(() => {
        if (mounted) setDocsUrl(agentDocsUrlForDistribution(null));
      });
    return () => {
      mounted = false;
    };
  }, []);

  useLayoutEffect(() => {
    if (!docsUrl) {
      setEnd(null);
      return () => setEnd(null);
    }
    setEnd(
      <a
        href={docsUrl}
        target="_blank"
        rel="noopener noreferrer"
        className={DS_BUTTON_OUTLINED_LINK_CN}
      >
        <ExternalLink className="size-3.5" />
        {t.app.openDocumentation}
      </a>,
    );
    return () => {
      setEnd(null);
    };
  }, [docsUrl, setEnd, t]);

  return (
    <div
      className={cn(
        "flex min-h-0 w-full min-w-0 flex-1 flex-col",
        "pt-1 sm:pt-2",
      )}
    >
      <PluginSlot name="docs:top" />
      {docsUrl ? (
        <iframe
          title={t.app.nav.documentation}
          src={docsUrl}
          className={cn(
            "min-h-0 w-full min-w-0 flex-1",
            "rounded-sm border border-current/20",
            docsUrl === HERMES_AGENT_DOCS_URL && "[color-scheme:light] bg-white",
          )}
          sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
          referrerPolicy="no-referrer-when-downgrade"
        />
      ) : (
        <div
          className="min-h-0 w-full min-w-0 flex-1"
          role="status"
          aria-live="polite"
          aria-label={t.app.nav.documentation}
        />
      )}
      <PluginSlot name="docs:bottom" />
    </div>
  );
}
