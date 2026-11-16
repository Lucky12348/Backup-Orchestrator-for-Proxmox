import { useMemo, useState } from "react";
import { Search, Server, ShieldCheck } from "lucide-react";

import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import type { ProxmoxBackupJob, VirtualMachine } from "../../types";
import { InfoTip } from "../../ui/InfoTip";
import { useToast } from "../../ui/toast";
import { formatGib } from "../../units";
import { fill, formatWhen } from "../format";
import {
  PROTECTIONS,
  editableJobs,
  filterResources,
  jobsFor,
  nextSelection,
  nodesOf,
  protectionCounts,
  protectionOf,
  vmid,
  type Protection,
  type ResourceFilters,
} from "./resourcesModel";
import { jobLabel } from "./jobsModel";

const BADGE: Record<Protection, string> = {
  protected: "bg-success/15 text-success-text",
  unprotected: "bg-danger/15 text-danger-text",
  ignored: "bg-subtle text-muted",
  infrastructure: "bg-accent/15 text-accent-text",
};

const select = "h-8 rounded-lg border border-solid border-border bg-subtle px-2 text-[13px] text-fg outline-none focus:border-accent";
const ghost = "rounded-lg border border-solid border-border bg-subtle px-3 py-1.5 text-[13px] font-medium text-fg hover:brightness-110 disabled:opacity-50";

export interface ResourcesViewProps {
  vms: VirtualMachine[];
  jobs: ProxmoxBackupJob[];
  language: Language;
  t: TranslationDictionary;
  onIgnore: (vm: VirtualMachine, ignored: boolean) => Promise<void> | void;
  onJobSelection: (jobId: string, vmids: number[]) => Promise<void> | void;
  now?: Date;
}

export function ProtectionBadge({ protection, t }: { protection: Protection; t: TranslationDictionary }) {
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium", BADGE[protection])} data-protection={protection}>
      {protection === "infrastructure" ? <Server className="h-3 w-3" /> : protection === "protected" ? <ShieldCheck className="h-3 w-3" /> : null}
      {t.resourcesV2.protection[protection]}
    </span>
  );
}

/** « Sauvegarde Proxmox → pbs-test · dim. 03:00 » (the raw id only in the job details). */
function readableJob(job: ProxmoxBackupJob, t: TranslationDictionary, language: Language): string {
  return jobLabel(job, t.jobsV2.name, language);
}

export function ResourcesView({ vms, jobs, language, t, onIgnore, onJobSelection, now = new Date() }: ResourcesViewProps) {
  const text = t.resourcesV2;
  const { toast } = useToast();
  const [filters, setFilters] = useState<ResourceFilters>({ query: "", type: "", protection: "", node: "" });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [jobId, setJobId] = useState("");
  const [busy, setBusy] = useState(false);
  const shown = filterResources(vms, filters);
  const counts = protectionCounts(vms);
  const nodes = nodesOf(vms);
  const editable = editableJobs(jobs);
  const targetJob = editable.find((job) => job.job_id === jobId) ?? editable[0];
  const chosen = useMemo(() => vms.filter((vm) => selected.has(vm.id)), [vms, selected]);
  const set = (patch: Partial<ResourceFilters>) => setFilters((current) => ({ ...current, ...patch }));
  const allShownSelected = shown.length > 0 && shown.every((vm) => selected.has(vm.id));

  function toggle(id: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function bulk(action: "add" | "remove" | "ignore" | "include") {
    setBusy(true);
    try {
      if (action === "ignore" || action === "include") {
        for (const vm of chosen) if (vm.ignored !== (action === "ignore")) await onIgnore(vm, action === "ignore");
      } else if (targetJob) {
        const ids = chosen.map(vmid).filter((id): id is number => id !== null);
        await onJobSelection(targetJob.job_id, nextSelection(targetJob, ids, action));
      }
      toast({ tone: "success", title: fill(text.done, { n: chosen.length }) });
      setSelected(new Set());
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-[18px]">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="m-0 text-[26px] font-semibold tracking-[-0.02em]">{text.title}</h1>
        <InfoTip text={text.info} label={t.shell.about} />
        <span className="flex-1" />
        <span className="flex flex-wrap gap-1.5">
          {PROTECTIONS.map((protection) => (
            <button
              key={protection}
              type="button"
              onClick={() => set({ protection: filters.protection === protection ? "" : protection })}
              aria-pressed={filters.protection === protection}
              className={cn("rounded-full border-0 px-2.5 py-1 text-xs font-medium", BADGE[protection], filters.protection === protection && "ring-2 ring-accent")}
            >
              {t.resourcesV2.protection[protection]} · {counts[protection]}
            </button>
          ))}
        </span>
      </div>

      <div role="group" aria-label={text.filters} className="flex flex-wrap items-center gap-2">
        <label className="flex h-8 min-w-[220px] flex-1 items-center gap-2 rounded-lg border border-solid border-border bg-subtle px-2.5 text-faint sm:max-w-[320px]">
          <Search className="h-3.5 w-3.5" />
          <input
            aria-label={text.search}
            placeholder={text.search}
            value={filters.query}
            onChange={(event) => set({ query: event.target.value })}
            className="min-w-0 flex-1 border-0 bg-transparent text-[13px] text-fg outline-none placeholder:text-faint"
          />
        </label>
        <select aria-label={text.allTypes} className={select} value={filters.type} onChange={(event) => set({ type: event.target.value as ResourceFilters["type"] })}>
          <option value="">{text.allTypes}</option>
          <option value="vm">{text.type.vm}</option>
          <option value="ct">{text.type.ct}</option>
        </select>
        <select aria-label={text.allStates} className={select} value={filters.protection} onChange={(event) => set({ protection: event.target.value as ResourceFilters["protection"] })}>
          <option value="">{text.allStates}</option>
          {PROTECTIONS.map((protection) => (
            <option key={protection} value={protection}>
              {text.protection[protection]}
            </option>
          ))}
        </select>
        {nodes.length > 1 ? (
          <select aria-label={text.allNodes} className={select} value={filters.node} onChange={(event) => set({ node: event.target.value })}>
            <option value="">{text.allNodes}</option>
            {nodes.map((node) => (
              <option key={node} value={node}>
                {node}
              </option>
            ))}
          </select>
        ) : null}
      </div>

      {selected.size ? (
        <div role="region" aria-label={text.bulk} className="flex flex-wrap items-center gap-2 rounded-xl border border-solid border-accent/40 bg-accent/10 px-4 py-2.5" data-bulk={selected.size}>
          <span className="text-[13px] font-medium">{fill(text.selected, { n: selected.size })}</span>
          <span className="flex-1" />
          {editable.length ? (
            <>
              <select aria-label={text.job} className={select} value={targetJob?.job_id ?? ""} onChange={(event) => setJobId(event.target.value)}>
                {editable.map((job) => (
                  <option key={job.job_id} value={job.job_id}>
                    {readableJob(job, t, language)}
                  </option>
                ))}
              </select>
              <button type="button" disabled={busy} className={ghost} onClick={() => void bulk("add")}>
                {text.addToJob}
              </button>
              <button type="button" disabled={busy} className={ghost} onClick={() => void bulk("remove")}>
                {text.removeFromJob}
              </button>
            </>
          ) : null}
          <button type="button" disabled={busy} className={ghost} onClick={() => void bulk("ignore")}>
            {text.ignore}
          </button>
          <button type="button" disabled={busy} className={ghost} onClick={() => void bulk("include")}>
            {text.include}
          </button>
          <button type="button" className="border-0 bg-transparent px-1 text-[13px] text-muted hover:text-fg" onClick={() => setSelected(new Set())}>
            {text.clear}
          </button>
        </div>
      ) : null}

      <section className="overflow-x-auto rounded-2xl border border-solid border-border bg-surface">
        {shown.length === 0 ? (
          <p className="m-0 px-5 py-8 text-center text-[13px] text-faint">{vms.length ? text.noMatch : text.empty}</p>
        ) : (
          <table className="w-full min-w-[900px] border-collapse text-[13px]">
            <thead>
              <tr className="text-left text-xs text-faint">
                <th className="w-10 border-0 border-b border-solid border-border px-4 py-2.5">
                  <input
                    type="checkbox"
                    aria-label={text.selectAll}
                    checked={allShownSelected}
                    onChange={() => setSelected(allShownSelected ? new Set() : new Set(shown.map((vm) => vm.id)))}
                    className="m-0 h-4 w-4"
                  />
                </th>
                {[text.col.name, text.col.type, text.col.node, text.col.state, text.col.protection, text.col.size, text.col.lastCopy, text.col.job].map((label) => (
                  <th key={label} className="border-0 border-b border-solid border-border px-3 py-2.5 font-medium">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((vm) => {
                const protection = protectionOf(vm);
                const jobText = jobsFor(vm, jobs)
                  .map((job) => readableJob(job, t, language))
                  .join(", ");
                return (
                  <tr key={vm.id} data-vm={vm.id} className={cn("hover:bg-subtle", (protection === "ignored" || protection === "infrastructure") && "text-muted")}>
                    <td className="border-0 border-b border-solid border-border px-4 py-2.5">
                      <input type="checkbox" aria-label={fill(text.selectOne, { name: vm.name })} checked={selected.has(vm.id)} onChange={() => toggle(vm.id)} className="m-0 h-4 w-4" />
                    </td>
                    <td className="border-0 border-b border-solid border-border px-3 py-2.5">
                      <span className="flex items-center gap-2">
                        <span className="font-mono text-xs text-faint">{vm.external_id ?? "—"}</span>
                        <span className="font-medium text-fg">{vm.name}</span>
                      </span>
                    </td>
                    <td className="border-0 border-b border-solid border-border px-3 py-2.5">
                      <span className="rounded bg-subtle px-1.5 py-0.5 text-[11px] font-semibold uppercase text-muted">{vm.vm_type === "ct" ? text.type.ct : text.type.vm}</span>
                    </td>
                    <td className="border-0 border-b border-solid border-border px-3 py-2.5 text-muted">{vm.node_name ?? "—"}</td>
                    <td className="border-0 border-b border-solid border-border px-3 py-2.5">
                      <span className="flex items-center gap-1.5 text-muted">
                        <span className={cn("h-1.5 w-1.5 rounded-full", vm.runtime_status === "running" ? "bg-success" : "bg-faint")} />
                        {vm.runtime_status === "running" ? text.running : vm.runtime_status ? text.stopped : "—"}
                      </span>
                    </td>
                    <td className="border-0 border-b border-solid border-border px-3 py-2.5">
                      <ProtectionBadge protection={protection} t={t} />
                    </td>
                    <td className="whitespace-nowrap border-0 border-b border-solid border-border px-3 py-2.5 font-mono text-xs text-muted">{formatGib(vm.size_gb, language)}</td>
                    <td className="whitespace-nowrap border-0 border-b border-solid border-border px-3 py-2.5 text-muted">
                      {formatWhen(vm.last_external_copy_at ?? null, language, now, t.dashboardV2)}
                    </td>
                    <td className="border-0 border-b border-solid border-border px-3 py-2.5 text-muted">
                      {jobText || <span className="text-faint">{text.noJob}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
