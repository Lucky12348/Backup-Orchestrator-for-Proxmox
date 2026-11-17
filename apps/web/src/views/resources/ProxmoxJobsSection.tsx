import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarClock, ListChecks, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { ApiError } from "../../notices";
import { ActionMenuArea, ActionMenuButton, type MenuAction } from "../../ui/ActionMenu";

import { createProxmoxBackupJob, deleteProxmoxBackupJob, replaceProxmoxBackupJob, getProxmoxTask, runProxmoxJobNow } from "../../api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../../components/ui/dialog";
import { cn } from "../../components/ui/utils";
import { CloseButton } from "../../ui/CloseButton";
import type { Language, TranslationDictionary } from "../../i18n";
import type { ProxmoxBackupJob, VirtualMachine } from "../../types";
import { useConfirm } from "../../ui/confirm";
import { useToast } from "../../ui/toast";
import { fill } from "../format";
import {
  DAY_CODES,
  blankJobForm,
  describeSchedule,
  formFromJob,
  formToValues,
  jobLabel,
  jobProblems,
  type DayCode,
  type JobForm,
} from "./jobsModel";
import { vmid } from "./resourcesModel";

const field = "rounded-lg border border-solid border-border-strong bg-bg px-3 py-2 text-sm text-fg outline-none focus:border-accent";
const ghost = "flex items-center gap-1.5 rounded-lg border border-solid border-border bg-subtle px-3 py-1.5 text-[13px] font-medium text-fg hover:brightness-110 disabled:opacity-50";

type Editing = { mode: "create" } | { mode: "edit"; job: ProxmoxBackupJob } | { mode: "selection"; job: ProxmoxBackupJob };

export function ProxmoxJobsSection({
  jobs,
  vms,
  language,
  t,
  onChanged,
}: {
  jobs: ProxmoxBackupJob[];
  vms: VirtualMachine[];
  language: Language;
  t: TranslationDictionary;
  onChanged: () => Promise<void> | void;
}) {
  const text = t.jobsV2;
  const { toast } = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<Editing | null>(null);
  const names = useMemo(() => new Map(vms.map((vm) => [vmid(vm), vm.name])), [vms]);

  async function remove(job: ProxmoxBackupJob) {
    const label = jobLabel(job, text.name, language);
    if (!(await confirm({ title: text.deleteTitle, description: fill(text.deleteText, { job: label }), confirmLabel: text.delete, tone: "danger" }))) return;
    try {
      await deleteProxmoxBackupJob(job.job_id);
      toast({ tone: "success", title: fill(text.deleted, { job: label }) });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    }
  }

  /** Phase 5h.1: "Run now" = vzdump with the job's parameters; the task is followed until it ends. */
  async function runNow(job: ProxmoxBackupJob) {
    const menu = t.menu.job;
    const label = jobLabel(job, text.name, language);
    try {
      const started = await runProxmoxJobNow(job.job_id);
      toast({ tone: "info", title: fill(menu.started, { job: label }) });
      for (let attempt = 0; attempt < 1440; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        const state = await getProxmoxTask(started.node, started.upid);
        if (state.running) continue;
        if (state.ok) toast({ tone: "success", title: fill(menu.finished, { job: label }) });
        else toast({ tone: "danger", title: fill(menu.failed, { job: label }), description: state.exitstatus ?? undefined });
        return;
      }
    } catch (error) {
      const code = error instanceof ApiError ? error.code : undefined;
      const known = code ? (menu.errors as Record<string, string>)[code] : undefined;
      toast({ tone: "danger", title: known ?? (error instanceof Error ? error.message : t.error), description: code === "job_run_failed" && error instanceof Error ? error.message : undefined });
    }
  }

  /** Phase 5h: the same actions on a right click, the ⋯ button, a long press, Shift+F10. */
  function jobMenu(job: ProxmoxBackupJob): MenuAction[] {
    const menu = t.menu.job;
    return [
      {
        key: "run_now",
        label: menu.runNow,
        icon: Play,
        confirm: { title: fill(menu.runNowTitle, { job: jobLabel(job, text.name, language) }), description: menu.runNowText, confirmLabel: menu.runNow },
        onSelect: () => runNow(job),
      },
      { key: "edit", label: text.edit, icon: Pencil, disabled: !job.supported, onSelect: () => setEditing({ mode: "edit", job }) },
      { key: "selection", label: text.manage, icon: ListChecks, disabled: !job.supported, onSelect: () => setEditing({ mode: "selection", job }) },
      { key: "delete", label: text.delete, icon: Trash2, danger: true, onSelect: () => remove(job) },
    ];
  }

  return (
    <section id="proxmox-jobs" aria-label={text.title} className="flex flex-col gap-4 rounded-lg border border-solid border-border bg-surface p-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="m-0 flex-1 text-sm font-semibold">{text.title}</h2>
        <button type="button" className={ghost} onClick={() => setEditing({ mode: "create" })}>
          <Plus className="h-4 w-4" />
          {text.create}
        </button>
      </div>
      {jobs.length === 0 ? <p className="m-0 text-[13px] text-faint">{text.empty}</p> : null}
      <ul className="m-0 grid list-none gap-3 p-0 xl:grid-cols-2">
        {jobs.map((job) => (
          <ActionMenuArea
            as="li"
            key={job.job_id}
            actions={jobMenu(job)}
            label={fill(t.menu.label, { name: jobLabel(job, text.name, language) })}
            className="flex flex-col gap-2 rounded-xl border border-solid border-border bg-subtle/40 p-4 outline-none focus-visible:ring-2 focus-visible:ring-accent"
            data-job={job.job_id}
          >
            <div className="flex items-start gap-3">
              <CalendarClock className="mt-0.5 h-4 w-4 flex-none text-muted" />
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="text-sm font-semibold">{jobLabel(job, text.name, language)}</span>
                <span className="text-xs text-faint">
                  {[job.comment, fill(text.guests, { n: job.selected_vmids.length }), job.enabled ? null : text.disabled].filter(Boolean).join(" · ")}
                </span>
              </div>
              <ActionMenuButton actions={jobMenu(job)} label={fill(t.menu.label, { name: jobLabel(job, text.name, language) })} className="-mr-2 -mt-1.5" />
            </div>
            {job.selected_vmids.length ? (
              <span className="text-xs text-muted">
                {job.selected_vmids
                  .slice(0, 8)
                  .map((id) => names.get(id) ?? String(id))
                  .join(", ")}
                {job.selected_vmids.length > 8 ? fill(text.more, { n: job.selected_vmids.length - 8 }) : ""}
              </span>
            ) : null}
            {!job.supported ? <span className="text-xs text-warning-text">{job.unsupported_reason}</span> : null}
            <details className="text-xs text-faint">
              <summary className="cursor-pointer">{text.details}</summary>
              <span className="mt-1 block font-mono">
                {job.job_id} · {job.schedule} · {job.retention ?? "—"}
              </span>
            </details>
            <div className="flex flex-wrap gap-2 pt-1">
              <button type="button" className={ghost} disabled={!job.supported} onClick={() => setEditing({ mode: "selection", job })}>
                <ListChecks className="h-4 w-4" />
                {text.manage}
              </button>
              <button type="button" className={ghost} disabled={!job.supported} onClick={() => setEditing({ mode: "edit", job })}>
                <Pencil className="h-4 w-4" />
                {text.edit}
              </button>
              <span className="flex-1" />
              <button type="button" className="flex items-center gap-1.5 border-0 bg-transparent text-[13px] font-medium text-danger-text hover:underline" onClick={() => void remove(job)}>
                <Trash2 className="h-4 w-4" />
                {text.delete}
              </button>
            </div>
          </ActionMenuArea>
        ))}
      </ul>
      <JobDialog editing={editing} jobs={jobs} vms={vms} language={language} t={t} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); await onChanged(); }} />
    </section>
  );
}

function JobDialog({
  editing,
  jobs,
  vms,
  language,
  t,
  onClose,
  onSaved,
}: {
  editing: Editing | null;
  jobs: ProxmoxBackupJob[];
  vms: VirtualMachine[];
  language: Language;
  t: TranslationDictionary;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}) {
  const text = t.jobsV2;
  const { toast } = useToast();
  const [form, setForm] = useState<JobForm | null>(null);
  const [busy, setBusy] = useState(false);
  const [showProblems, setShowProblems] = useState(false);
  const first = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!editing) return setForm(null);
    setShowProblems(false);
    setForm(editing.mode === "create" ? blankJobForm(jobs[0]?.storage ?? "") : formFromJob(editing.job));
  }, [editing, jobs]);

  if (!editing || !form) return null;
  const selectionOnly = editing.mode === "selection";
  const update = (patch: Partial<JobForm>) => setForm({ ...form, ...patch });
  const problems = jobProblems(form);
  const guests = vms
    .map((vm) => ({ vm, id: vmid(vm) }))
    .filter((entry): entry is { vm: VirtualMachine; id: number } => entry.id !== null)
    .sort((a, b) => a.id - b.id);
  const storages = [...new Set(jobs.map((job) => job.storage).filter((value): value is string => Boolean(value)))];
  const title = editing.mode === "create" ? text.createTitle : selectionOnly ? text.selectionTitle : text.editTitle;

  async function save() {
    if (!form || !editing) return;
    if (problems.length) return setShowProblems(true);
    setBusy(true);
    try {
      const values = formToValues(form);
      if (editing.mode === "create") await createProxmoxBackupJob(values);
      else await replaceProxmoxBackupJob(editing.job.job_id, values);
      toast({ tone: "success", title: text.saved });
      await onSaved();
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    } finally {
      setBusy(false);
    }
  }

  const toggleDay = (day: DayCode) => update({ days: form.days.includes(day) ? form.days.filter((item) => item !== day) : [...form.days, day] });
  const toggleGuest = (id: number) => update({ vmids: form.vmids.includes(id) ? form.vmids.filter((item) => item !== id) : [...form.vmids, id] });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="max-w-[600px]"
        aria-describedby="job-dialog-summary"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          first.current?.focus();
        }}
      >
        <div className="flex items-center gap-2 border-0 border-b border-solid border-border px-5 py-4">
          <DialogTitle className="m-0 flex-1 text-[17px] font-semibold">{title}</DialogTitle>
          <CloseButton label={text.close} onClick={onClose} />
        </div>
        <div className="flex flex-col gap-4 overflow-y-auto px-5 py-4">
          <DialogDescription id="job-dialog-summary" className="m-0 text-sm text-muted">
            {jobLabel({ job_id: "", storage: form.storage, schedule: `${form.days.join(",")} ${form.time}` }, text.name, language)}
          </DialogDescription>
          {selectionOnly ? null : (
            <>
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium">{text.storage}</span>
                <input ref={first} className={field} list="pbo-job-storages" value={form.storage} onChange={(event) => update({ storage: event.target.value })} />
                <datalist id="pbo-job-storages">
                  {storages.map((storage) => (
                    <option key={storage} value={storage} />
                  ))}
                </datalist>
                {showProblems && problems.includes("storage") ? <span className="text-xs text-danger-text">{text.problem.storage}</span> : null}
              </label>
              <div className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium">{text.days}</span>
                <DayPills days={form.days} onToggle={toggleDay} lang={language} />
                {showProblems && problems.includes("days") ? <span className="text-xs text-danger-text">{text.problem.days}</span> : null}
              </div>
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium">{text.time}</span>
                <input type="time" className={cn(field, "w-36")} value={form.time} onChange={(event) => update({ time: event.target.value })} />
              </label>
              <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
                <legend className="mb-1.5 p-0 text-[13px] font-medium">{text.retention}</legend>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                  {(["keepLast", "keepDaily", "keepWeekly", "keepMonthly", "keepYearly"] as const).map((key) => (
                    <label key={key} className="flex flex-col gap-1 text-xs text-muted">
                      {text.keep[key]}
                      <input type="number" min={0} className={field} value={form[key]} onChange={(event) => update({ [key]: event.target.value } as Partial<JobForm>)} />
                    </label>
                  ))}
                </div>
                {showProblems && problems.includes("keep") ? <span className="text-xs text-danger-text">{text.problem.keep}</span> : null}
              </fieldset>
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium">{text.comment}</span>
                <input className={field} value={form.comment} onChange={(event) => update({ comment: event.target.value })} />
              </label>
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input type="checkbox" className="m-0 h-4 w-4" checked={form.enabled} onChange={(event) => update({ enabled: event.target.checked })} />
                {text.enabled}
              </label>
            </>
          )}
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-1.5 p-0 text-[13px] font-medium">{fill(text.guestsTitle, { n: form.vmids.length })}</legend>
            <div className="grid max-h-56 grid-cols-1 gap-1 overflow-y-auto rounded-lg border border-solid border-border p-2 sm:grid-cols-2">
              {guests.map(({ vm, id }) => (
                <label key={id} className="flex cursor-pointer items-center gap-2 text-[13px]">
                  <input type="checkbox" className="m-0 h-3.5 w-3.5" checked={form.vmids.includes(id)} onChange={() => toggleGuest(id)} />
                  <span className="font-mono text-xs text-faint">{id}</span>
                  <span className="truncate">{vm.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>
        <div className="flex items-center justify-end gap-2 border-0 border-t border-solid border-border px-5 py-3.5">
          <button type="button" onClick={onClose} className="rounded-lg border border-solid border-border bg-subtle px-3.5 py-2 text-sm font-medium text-fg hover:brightness-110">
            {t.confirmDialogs.cancel}
          </button>
          <button type="button" disabled={busy} onClick={() => void save()} className="rounded-lg border-0 bg-accent px-3.5 py-2 text-sm font-medium text-white hover:brightness-110 disabled:opacity-60">
            {editing.mode === "create" ? text.createAction : text.save}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

const PILL: Record<"fr" | "en", string[]> = { fr: ["L", "M", "M", "J", "V", "S", "D"], en: ["M", "T", "W", "T", "F", "S", "S"] };
const LONG: Record<"fr" | "en", string[]> = {
  fr: ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
  en: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
};

/** L M M J V S D pills (also used by the planning form). */
export function DayPills({ days, onToggle, lang }: { days: readonly DayCode[]; onToggle: (day: DayCode) => void; lang: Language }) {
  return (
    <div role="group" className="flex flex-wrap gap-1.5">
      {DAY_CODES.map((day, index) => {
        const active = days.includes(day);
        return (
          <button
            key={day}
            type="button"
            aria-pressed={active}
            aria-label={LONG[lang][index]}
            title={LONG[lang][index]}
            onClick={() => onToggle(day)}
            className={cn(
              "h-9 w-9 rounded-full border border-solid text-[13px] font-semibold",
              active ? "border-accent bg-accent text-white" : "border-border bg-transparent text-muted hover:border-border-strong hover:text-fg",
            )}
          >
            {PILL[lang][index]}
          </button>
        );
      })}
    </div>
  );
}

/** A plain icon button: no focus ring drawn as a bar next to the icon ("|x"). */
export { CloseButton };

export { describeSchedule };
