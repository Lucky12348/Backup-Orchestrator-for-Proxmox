import { useCallback, useEffect, useState } from "react";
import { ArrowUpCircle, Loader2 } from "lucide-react";

import { getAgentUpdates, updateAgentNow, type AgentUpdatesOverview } from "../../api";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { useToast } from "../../ui/toast";
import { fill, formatWhen } from "../format";
import { canUpdateNow, shortBuild, UPDATE_TONE } from "./integrationsModel";

const TONE_CLASS = { ok: "bg-success/15 text-success-text", warn: "bg-warning/15 text-warning-text", err: "bg-danger/15 text-danger-text", run: "bg-accent/15 text-accent-text", off: "bg-subtle text-faint" } as const;
const button = "flex items-center gap-1.5 rounded-lg border border-solid border-border bg-subtle px-3 py-1.5 text-[13px] font-medium text-fg hover:brightness-110 disabled:opacity-50";
const POLL_MS = 3000;

/**
 * Phase 5g, Integrations › Agents: version of each agent against the build
 * this app ships, its update state, "Update now", and the update history.
 */
export function AgentUpdates({
  language,
  t,
  now,
  onReinstall,
  load = getAgentUpdates,
}: {
  language: Language;
  t: TranslationDictionary;
  now: Date;
  onReinstall: () => void;
  load?: typeof getAgentUpdates;
}) {
  const text = t.integrationsV2.update;
  const { toast } = useToast();
  const [overview, setOverview] = useState<AgentUpdatesOverview | null>(null);
  const [starting, setStarting] = useState<number | null>(null);
  const refresh = useCallback(() => load().then(setOverview, () => undefined), [load]);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  const updating = Boolean(overview?.agents.some((agent) => agent.state === "updating"));
  useEffect(() => {
    if (!updating) return undefined;
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [updating, refresh]);

  async function start(agentId: number, host: string) {
    setStarting(agentId);
    try {
      await updateAgentNow(agentId);
      toast({ tone: "success", title: fill(text.started, { host }) });
    } catch (error) {
      const code = (error as { code?: string }).code;
      toast({ tone: "danger", title: (code && (text.errors as Record<string, string>)[code]) || (error instanceof Error ? error.message : String(error)) });
    } finally {
      setStarting(null);
      await refresh();
    }
  }

  if (!overview) return null;
  return (
    <div className="flex flex-col gap-3" data-agent-updates>
      <p className="m-0 flex flex-wrap gap-x-4 text-[13px] text-muted">
        {overview.shipped ? <span>{fill(text.shipped, { version: overview.shipped.version, build: shortBuild(overview.shipped.build_id) })}</span> : null}
        <span>{overview.auto_update ? text.auto : text.autoOff}</span>
      </p>
      {overview.agents.map((agent) => (
        <div key={agent.id} className="flex flex-col gap-2 rounded-xl border border-solid border-border px-4 py-3 text-[13px]" data-agent-update={agent.state}>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="font-medium">{agent.hostname}</span>
            <span className="text-muted">
              {agent.version ? fill(text.version, { version: agent.version }) : text.unknownVersion}
              {agent.build_id ? ` · ${fill(text.build, { build: shortBuild(agent.build_id) })}` : ""}
            </span>
            <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", TONE_CLASS[UPDATE_TONE[agent.state]])}>{text.state[agent.state]}</span>
            <span className="flex-1" />
            <button
              type="button"
              className={button}
              disabled={!canUpdateNow(agent.state) || starting === agent.id}
              onClick={() => void start(agent.id, agent.hostname)}
            >
              {agent.state === "updating" || starting === agent.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUpCircle className="h-4 w-4" />}
              {agent.state === "updating" ? text.running : text.now}
            </button>
          </div>
          {agent.state === "too_old" ? (
            <div className="flex flex-wrap items-center gap-2 rounded-lg bg-warning/15 p-2.5 text-warning-text">
              <span className="flex-1">{text.tooOld}</span>
              <button type="button" className={button} onClick={onReinstall}>
                {t.integrationsV2.reinstall}
              </button>
            </div>
          ) : null}
        </div>
      ))}
      <details className="rounded-xl border border-solid border-border">
        <summary className="cursor-pointer px-4 py-3 text-[13px] font-medium text-muted">{text.history}</summary>
        {overview.history.length === 0 ? <p className="m-0 px-4 pb-3 text-[13px] text-faint">{text.noHistory}</p> : null}
        <ul className="m-0 flex list-none flex-col gap-1.5 px-4 pb-3 pt-0">
          {overview.history.map((row) => (
            <li key={row.id} className="flex flex-wrap gap-x-3 text-[13px]" data-update-status={row.status}>
              <span className="text-faint">{formatWhen(row.started_at, language, now, t.dashboardV2)}</span>
              <span>{row.to_version ? fill(text.version, { version: row.to_version }) : null} · {shortBuild(row.to_build)}</span>
              <span className="text-muted">{text.trigger[row.trigger]}</span>
              <span className={row.status === "success" || row.status === "up_to_date" ? "text-success-text" : row.status === "running" || row.status === "busy" ? "text-muted" : "text-danger-text"}>
                {text.status[row.status]}
              </span>
              {row.message && row.status !== "success" ? <span className="w-full text-xs text-faint">{row.message}</span> : null}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
