import { useCallback, useEffect, useState } from "react";
import { Bot, Database, Loader2, RefreshCw, Server, Settings2, Stethoscope, X } from "lucide-react";
import { useNavigate } from "react-router-dom";

import {
  deleteRevokedAgents,
  getIntegrationsHealth,
  getRegisteredAgents,
  revokeRegisteredAgent,
  testIntegration,
  type IntegrationHealth,
  type IntegrationKey,
  type RegisteredAgent,
} from "../../api";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { AgentStep } from "../../setup/steps/AgentStep";
import { WIZARD_TEXT } from "../../setup/i18n";
import { TextContext } from "../../setup/shared";
import { RECONFIGURE_PATH } from "../../setup/logic";
import { useConfirm } from "../../ui/confirm";
import { InfoTip } from "../../ui/InfoTip";
import { useToast } from "../../ui/toast";
import { fill, formatWhen } from "../format";
import { integrationState, splitAgents, type IntegrationState } from "./integrationsModel";

const ICON: Record<IntegrationKey, typeof Server> = { pve: Server, pbs: Database, agent: Bot };
const DOT: Record<IntegrationState, string> = { ok: "bg-success", down: "bg-danger", unconfigured: "bg-faint", checking: "bg-warning animate-soft-pulse" };
const button = "flex items-center gap-1.5 rounded-lg border border-solid border-border bg-subtle px-3 py-1.5 text-[13px] font-medium text-fg hover:brightness-110 disabled:opacity-50";

export interface IntegrationsViewProps {
  language: Language;
  t: TranslationDictionary;
  /** The inventory / backups syncs of the app (toasts come from useAppData). */
  onSync: (key: "pve" | "pbs") => Promise<void> | void;
  syncing: (key: "pve" | "pbs") => boolean;
  now?: Date;
  loadHealth?: typeof getIntegrationsHealth;
  loadAgents?: typeof getRegisteredAgents;
}

export function IntegrationsView({
  language,
  t,
  onSync,
  syncing,
  now = new Date(),
  loadHealth = getIntegrationsHealth,
  loadAgents = getRegisteredAgents,
}: IntegrationsViewProps) {
  const text = t.integrationsV2;
  const { toast } = useToast();
  const confirm = useConfirm();
  const [health, setHealth] = useState<Partial<Record<IntegrationKey, IntegrationHealth>>>({});
  const [testing, setTesting] = useState<Set<IntegrationKey>>(new Set(["pve", "pbs", "agent"]));
  const [agents, setAgents] = useState<RegisteredAgent[] | null>(null);
  const [installing, setInstalling] = useState(false);

  const refreshAgents = useCallback(async () => {
    try {
      setAgents(await loadAgents());
    } catch {
      setAgents([]);
    }
  }, [loadAgents]);

  useEffect(() => {
    void loadHealth()
      .then(
        (items) => setHealth(Object.fromEntries(items.map((item) => [item.key, item]))),
        // The page itself failed: each card says so instead of "Checking…" forever.
        (error: unknown) =>
          setHealth(
            Object.fromEntries(
              (["pve", "pbs", "agent"] as const).map((key) => [key, unknownHealth(key, error instanceof Error ? error.message : null)]),
            ),
          ),
      )
      .finally(() => setTesting(new Set()));
    void refreshAgents();
  }, [loadHealth, refreshAgents]);

  async function test(key: IntegrationKey) {
    setTesting((current) => new Set(current).add(key));
    try {
      const result = await testIntegration(key);
      setHealth((current) => ({ ...current, [key]: result }));
      toast({ tone: result.ok ? "success" : "danger", title: fill(result.ok ? text.testOk : text.testFailed, { name: text.name[key] }) });
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    } finally {
      setTesting((current) => {
        const next = new Set(current);
        next.delete(key);
        return next;
      });
    }
  }

  const { active, revoked } = splitAgents(agents ?? []);

  async function purge() {
    if (!(await confirm({ title: text.purgeTitle, description: text.purgeText, confirmLabel: text.purge, tone: "danger" }))) return;
    try {
      const { deleted } = await deleteRevokedAgents();
      toast({ tone: "success", title: fill(text.purged, { n: deleted }) });
      await refreshAgents();
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    }
  }

  async function revoke(agent: RegisteredAgent) {
    if (!(await confirm({ title: text.revokeTitle, description: fill(text.revokeText, { host: agent.hostname }), confirmLabel: text.revoke, tone: "danger" }))) return;
    try {
      await revokeRegisteredAgent(agent.id);
      await refreshAgents();
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="m-0 text-[26px] font-semibold tracking-[-0.02em]">{text.title}</h1>
        <InfoTip text={text.info} label={t.shell.about} />
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        {(["pve", "pbs", "agent"] as const).map((key) => (
          <IntegrationCard
            key={key}
            integration={key}
            health={health[key]}
            checking={testing.has(key)}
            syncing={key !== "agent" && syncing(key)}
            language={language}
            t={t}
            now={now}
            onTest={() => void test(key)}
            onSync={key === "agent" ? undefined : () => void onSync(key)}
          />
        ))}
      </div>

      <section aria-label={text.agents} className="flex flex-col gap-4 rounded-2xl border border-solid border-border bg-surface p-[22px]">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="m-0 flex-1 text-[15px] font-semibold">{text.agents}</h2>
          <button type="button" className={button} onClick={() => setInstalling(!installing)}>
            {installing ? <X className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
            {installing ? text.closeInstall : active.length ? text.reinstall : text.install}
          </button>
        </div>
        {agents === null ? <p className="m-0 text-[13px] text-faint">{text.loading}</p> : null}
        {agents !== null && active.length === 0 ? <p className="m-0 text-[13px] text-faint">{text.noAgent}</p> : null}
        {active.map((agent) => (
          <div key={agent.id} className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl bg-subtle px-4 py-3 text-[13px]" data-agent={agent.id}>
            <span className="flex items-center gap-2 font-medium">
              <span className="h-2 w-2 rounded-full bg-success" />
              {agent.hostname}
            </span>
            <span className="text-muted">{text.role[agent.role]}</span>
            <span className="font-mono text-xs text-faint">{agent.base_url}</span>
            <span className="text-muted">{fill(text.lastSeen, { when: formatWhen(agent.last_seen_at, language, now, t.dashboardV2) })}</span>
            <span className="flex-1" />
            <button type="button" className="border-0 bg-transparent text-[13px] text-danger-text hover:underline" onClick={() => void revoke(agent)}>
              {text.revoke}
            </button>
          </div>
        ))}
        {installing ? (
          // The very same block as the setup wizard (pairing code, commands, live wait).
          <div className="rounded-xl border border-solid border-border p-1" data-install="">
            <TextContext.Provider value={WIZARD_TEXT[language]}>
              <AgentStep
                onNext={() => {
                  setInstalling(false);
                  void refreshAgents();
                }}
              />
            </TextContext.Provider>
          </div>
        ) : null}
        {revoked.length ? (
          <details className="rounded-xl border border-solid border-border">
            <summary className="cursor-pointer px-4 py-3 text-[13px] font-medium text-muted">{fill(text.history, { n: revoked.length })}</summary>
            <ul className="m-0 flex list-none flex-col gap-1 px-4 pb-3 pt-0">
              {revoked.map((agent) => (
                <li key={agent.id} className="flex flex-wrap gap-x-4 text-[13px] text-faint">
                  <span>{agent.hostname}</span>
                  <span>{text.role[agent.role]}</span>
                  <span>{fill(text.revokedAt, { when: formatWhen(agent.revoked_at, language, now, t.dashboardV2) })}</span>
                </li>
              ))}
            </ul>
            <div className="border-0 border-t border-solid border-border px-4 py-3">
              <button type="button" className="border-0 bg-transparent text-[13px] font-medium text-danger-text hover:underline" onClick={() => void purge()}>
                {text.purge}
              </button>
            </div>
          </details>
        ) : null}
      </section>
    </div>
  );
}

function unknownHealth(key: IntegrationKey, message: string | null): IntegrationHealth {
  return {
    key, configured: true, ok: false, code: "unreachable", url: null, fingerprint: null, node: null,
    version: null, latency_ms: null, last_sync_at: null, last_sync_error: null, message,
  };
}

export function IntegrationCard({
  integration,
  health,
  checking,
  syncing,
  language,
  t,
  now,
  onTest,
  onSync,
}: {
  integration: IntegrationKey;
  health: IntegrationHealth | undefined;
  checking: boolean;
  syncing: boolean;
  language: Language;
  t: TranslationDictionary;
  now: Date;
  onTest: () => void;
  onSync?: () => void;
}) {
  const text = t.integrationsV2;
  const navigate = useNavigate();
  const Icon = ICON[integration];
  const state = integrationState(health, checking);
  const rows: [string, string | null | undefined][] = [
    [text.field.version, health?.version],
    [text.field.latency, health?.latency_ms != null ? fill(text.ms, { n: health.latency_ms }) : null],
    [text.field[integration === "pbs" ? "datastore" : integration === "agent" ? "host" : "node"], health?.node],
    [text.field.url, health?.url],
    [text.field.lastSync, integration === "agent" ? null : formatWhen(health?.last_sync_at ?? null, language, now, t.dashboardV2)],
  ];
  return (
    <section aria-label={text.name[integration]} className="flex flex-col rounded-2xl border border-solid border-border bg-surface" data-integration={integration} data-state={state}>
      <div className="flex items-center gap-3 border-0 border-b border-solid border-border px-[22px] py-4">
        <span className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-subtle text-muted">
          <Icon className="h-[18px] w-[18px]" />
        </span>
        <span className="flex flex-1 flex-col">
          <span className="text-[15px] font-semibold">{text.name[integration]}</span>
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <span className={cn("h-1.5 w-1.5 rounded-full", DOT[state])} />
            {text.state[state]}
          </span>
        </span>
      </div>
      <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 px-[22px] py-4 text-[13px]">
        {rows
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-faint">{label}</dt>
              <dd className="m-0 truncate text-right text-fg [overflow-wrap:anywhere]">{value}</dd>
            </div>
          ))}
      </dl>
      {health?.fingerprint ? (
        <div className="flex flex-col gap-1 px-[22px] pb-3">
          <span className="text-xs text-faint">{text.field.fingerprint}</span>
          {/* Selectable, copied by hand (no clipboard write). */}
          <input
            readOnly
            aria-label={text.field.fingerprint}
            value={health.fingerprint}
            onFocus={(event) => event.currentTarget.select()}
            className="w-full rounded-lg border border-solid border-border bg-bg px-2.5 py-1.5 font-mono text-[11px] text-muted outline-none focus:border-accent"
          />
        </div>
      ) : null}
      {state === "down" && health?.message ? (
        <p className="m-0 mx-[22px] mb-3 rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger-text [overflow-wrap:anywhere]">{health.message}</p>
      ) : null}
      {health?.last_sync_error ? (
        <p className="m-0 mx-[22px] mb-3 text-xs text-warning-text [overflow-wrap:anywhere]">{fill(text.syncError, { reason: health.last_sync_error })}</p>
      ) : null}
      <span className="flex-1" />
      <div className="flex flex-wrap gap-2 border-0 border-t border-solid border-border px-[22px] py-3.5">
        <button type="button" className={button} disabled={checking} onClick={onTest}>
          {checking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Stethoscope className="h-4 w-4" />}
          {text.test}
        </button>
        {onSync ? (
          <button type="button" className={button} disabled={syncing || state !== "ok"} onClick={onSync}>
            <RefreshCw className={cn("h-4 w-4", syncing && "animate-spin")} />
            {text.sync}
          </button>
        ) : null}
        <span className="flex-1" />
        <button type="button" className={button} onClick={() => navigate(`${RECONFIGURE_PATH}/${integration}`)}>
          <Settings2 className="h-4 w-4" />
          {text.reconfigure}
        </button>
      </div>
    </section>
  );
}
