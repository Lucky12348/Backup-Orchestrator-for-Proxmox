import { Fragment, useEffect, useMemo, useState } from "react";
import { ExternalLink, EyeOff, HardDrive, Shield } from "lucide-react";

import { getProxmoxApiUrl } from "../../api";
import { ActionMenuArea, ActionMenuButton, type MenuAction } from "../../ui/ActionMenu";
import { DiskTag } from "../disks/DiskTag";
import { Link } from "react-router-dom";

import { useNow } from "../../ui/useNow";
import { Search } from "lucide-react";

import { cn } from "../../components/ui/utils";
import { DOT_COLOR, StatusDot, type DotTone } from "../../ui/StatusDot";
import type { Language, TranslationDictionary } from "../../i18n";
import type { ExternalCopy, ProxmoxBackupJob, VirtualMachine } from "../../types";
import { PageTitle } from "../../ui/PageTitle";
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
  pveGuestUrl,
  protectionCounts,
  protectionOf,
  vmid,
  type Protection,
  type ResourceFilters,
} from "./resourcesModel";
import { jobLabel } from "./jobsModel";

const PROTECTION_TONE: Record<Protection, DotTone> = { protected: "ok", unprotected: "err", ignored: "off", infrastructure: "off" };

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
    <StatusDot tone={PROTECTION_TONE[protection]} data-protection={protection}>
      {t.resourcesV2.protection[protection]}
    </StatusDot>
  );
}

/** « Sauvegarde Proxmox → pbs-test · dim. 03:00 » (the raw id only in the job details). */
function readableJob(job: ProxmoxBackupJob, t: TranslationDictionary, language: Language): string {
  return jobLabel(job, t.jobsV2.name, language);
}

export function ResourcesView({ vms, jobs, language, t, onIgnore, onJobSelection, now: fixedNow }: ResourcesViewProps) {
  // "today / yesterday" follow the clock even when the page stays open overnight.
  const now = useNow(fixedNow);
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
  const [pveUrl, setPveUrl] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  useEffect(() => {
    getProxmoxApiUrl().then(setPveUrl, () => setPveUrl(null));
  }, []);

  /** Phase 5h: right click / ⋯ / long press / Shift+F10 on a row. */
  function resourceMenu(vm: VirtualMachine): MenuAction[] {
    const menu = t.menu.resource;
    const id = vmid(vm);
    const inJob = Boolean(targetJob && id !== null && targetJob.selected_vmids.includes(id));
    const link = pveGuestUrl(pveUrl, vm);
    const guard = async (task: () => Promise<void> | void) => {
      try {
        await task();
      } catch (error) {
        toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
      }
    };
    return [
      {
        key: "copies",
        label: expanded.has(vm.id) ? menu.hideCopies : menu.copies,
        icon: HardDrive,
        disabled: !(vm.external_copies ?? []).length,
        onSelect: () => setExpanded((current) => {
          const next = new Set(current);
          if (next.has(vm.id)) next.delete(vm.id);
          else next.add(vm.id);
          return next;
        }),
      },
      { key: "pve", label: menu.openPve, icon: ExternalLink, disabled: !link, onSelect: () => void window.open(link ?? "", "_blank", "noopener,noreferrer") },
      {
        key: "protect",
        label: targetJob ? fill(inJob ? menu.unprotect : menu.protect, { job: readableJob(targetJob, t, language) }) : menu.noJob,
        icon: Shield,
        disabled: !targetJob || id === null || vm.ignored,
        confirm: inJob ? { title: fill(menu.unprotect, { job: targetJob ? readableJob(targetJob, t, language) : "" }), description: menu.unprotectText, confirmLabel: menu.confirm } : undefined,
        onSelect: () =>
          guard(async () => {
            if (!targetJob || id === null) return;
            await onJobSelection(targetJob.job_id, nextSelection(targetJob, [id], inJob ? "remove" : "add"));
            toast({ tone: "success", title: fill(text.done, { n: 1 }) });
          }),
      },
      {
        key: "ignore",
        label: vm.ignored ? text.include : text.ignore,
        icon: EyeOff,
        danger: !vm.ignored,
        confirm: vm.ignored ? undefined : { title: fill(menu.ignoreTitle, { name: vm.name }), description: menu.ignoreText, confirmLabel: text.ignore },
        onSelect: () =>
          guard(async () => {
            await onIgnore(vm, !vm.ignored);
            toast({ tone: "success", title: fill(text.done, { n: 1 }) });
          }),
      },
    ];
  }

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
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <PageTitle tip={text.info}>{text.title}</PageTitle>
        <span className="flex-1" />
        <span className="flex flex-wrap gap-1.5">
          {PROTECTIONS.map((protection) => (
            <button
              key={protection}
              type="button"
              onClick={() => set({ protection: filters.protection === protection ? "" : protection })}
              aria-pressed={filters.protection === protection}
              className={cn(
                "flex items-center gap-1.5 rounded-md border border-solid px-2.5 py-1 text-[13px]",
                filters.protection === protection ? "border-border-strong bg-hover text-fg" : "border-border bg-transparent text-muted hover:text-fg",
              )}
            >
              <span aria-hidden className={cn("h-[7px] w-[7px] rounded-full", DOT_COLOR[PROTECTION_TONE[protection]])} />
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

      <section className="overflow-x-auto rounded-lg border border-solid border-border bg-surface">
        {shown.length === 0 ? (
          <p className="m-0 px-5 py-8 text-center text-[13px] text-faint">{vms.length ? text.noMatch : text.empty}</p>
        ) : (
          <table className="w-full min-w-[900px] border-collapse text-[13px]">
            <thead>
              <tr className="bg-subtle text-left text-[13px] text-muted">
                <th className="w-10 border-0 border-b border-solid border-border px-4 py-2">
                  <input
                    type="checkbox"
                    aria-label={text.selectAll}
                    checked={allShownSelected}
                    onChange={() => setSelected(allShownSelected ? new Set() : new Set(shown.map((vm) => vm.id)))}
                    className="m-0 h-4 w-4"
                  />
                </th>
                {[text.col.name, text.col.type, text.col.node, text.col.state, text.col.protection, text.col.size, text.col.lastCopy, text.col.onDisks, text.col.job].map((label) => (
                  <th key={label} className="whitespace-nowrap border-0 border-b border-solid border-border px-3 py-2 font-medium">
                    {label}
                  </th>
                ))}
                <th className="w-10 border-0 border-b border-solid border-border px-2 py-2">
                  <span className="sr-only">{t.menu.actions}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((vm) => {
                const protection = protectionOf(vm);
                const jobText = jobsFor(vm, jobs)
                  .map((job) => readableJob(job, t, language))
                  .join(", ");
                const actions = resourceMenu(vm);
                const menuLabel = fill(t.menu.label, { name: vm.name });
                return (
                  <Fragment key={vm.id}>
                  <ActionMenuArea
                    as="tr"
                    actions={actions}
                    label={menuLabel}
                    data-vm={vm.id}
                    className={cn("outline-none hover:bg-subtle focus-visible:bg-subtle", (protection === "ignored" || protection === "infrastructure") && "text-muted")}
                  >
                    <td className="border-0 border-b border-solid border-border px-4 py-2.5">
                      <input type="checkbox" aria-label={fill(text.selectOne, { name: vm.name })} checked={selected.has(vm.id)} onChange={() => toggle(vm.id)} className="m-0 h-4 w-4" />
                    </td>
                    <td className="border-0 border-b border-solid border-border px-3 py-2.5">
                      <span className="flex items-center gap-2">
                        <span className="text-xs text-faint">{vm.external_id ?? "—"}</span>
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
                    <td className="whitespace-nowrap border-0 border-b border-solid border-border px-3 py-2.5 text-xs text-muted">{formatGib(vm.size_gb, language)}</td>
                    <td className="whitespace-nowrap border-0 border-b border-solid border-border px-3 py-2.5 text-muted">
                      {formatWhen(vm.last_external_copy_at ?? null, language, now, t.dashboardV2)}
                    </td>
                    <td className="border-0 border-b border-solid border-border px-3 py-2.5" data-copies={(vm.external_copies ?? []).length}>
                      <DiskCopies copies={vm.external_copies ?? []} language={language} now={now} t={t} />
                    </td>
                    <td className="border-0 border-b border-solid border-border px-3 py-2.5 text-muted">
                      {jobText || <span className="text-faint">{text.noJob}</span>}
                    </td>
                    <td className="border-0 border-b border-solid border-border px-2 py-1.5">
                      <ActionMenuButton actions={actions} label={menuLabel} />
                    </td>
                  </ActionMenuArea>
                  {expanded.has(vm.id) ? (
                    <tr data-copies-of={vm.id}>
                      <td colSpan={11} className="border-0 border-b border-solid border-border bg-subtle/50 px-4 py-3">
                        <AllCopies copies={vm.external_copies ?? []} language={language} now={now} t={t} />
                      </td>
                    </tr>
                  ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

/** Phase 5f: the rotation disks holding a copy of this VM/CT, newest first. */
export function DiskCopies({ copies, language, now, t }: { copies: ExternalCopy[]; language: Language; now: Date; t: TranslationDictionary }) {
  if (!copies.length) return <span className="text-faint">{t.resourcesV2.noDiskCopy}</span>;
  const shown = copies.slice(0, 3);
  const more = copies.length - shown.length;
  return (
    <div className="flex flex-col gap-1">
      {shown.map((copy) => {
        return (
          <Link
            key={copy.disk_id}
            to={`/disks/${copy.disk_id}`}
            title={fill(copy.source === "runs" ? t.resourcesV2.copyFromRuns : t.resourcesV2.copyAsOf, {
              when: formatWhen(copy.source === "runs" ? copy.newest : copy.cached_at, language, now, t.dashboardV2),
            })}
            data-source={copy.source ?? "contents"}
            className="flex items-center gap-1.5 whitespace-nowrap text-xs text-muted no-underline hover:text-fg"
          >
            <DiskTag id={copy.disk_id} fallback={copy.disk_name} color={copy.color} t={t} nameClassName="font-medium text-fg" />
            <span>{formatWhen(copy.newest, language, now, t.dashboardV2)}</span>
          </Link>
        );
      })}
      {more > 0 ? <span className="text-xs text-faint">{fill(t.resourcesV2.moreDisks, { n: more })}</span> : null}
    </div>
  );
}

/** "See its copies per disk" (context menu): every disk holding a copy. */
function AllCopies({ copies, language, now, t }: { copies: ExternalCopy[]; language: Language; now: Date; t: TranslationDictionary }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0" aria-label={t.menu.resource.copies}>
      {copies.map((copy) => (
        <li key={copy.disk_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px]">
          <Link to={`/disks/${copy.disk_id}`} className="min-w-0 text-fg no-underline hover:underline">
            <DiskTag id={copy.disk_id} fallback={copy.disk_name} t={t} size="sm" secondary nameClassName="font-medium" />
          </Link>
          <span className="text-muted">{fill(t.menu.resource.newest, { when: formatWhen(copy.newest, language, now, t.dashboardV2) })}</span>
          {copy.versions != null ? <span className="text-faint">{fill(t.menu.resource.versions, { n: copy.versions })}</span> : null}
        </li>
      ))}
    </ul>
  );
}
