import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, X } from "lucide-react";

import { createScheduledBackupEvent, deleteScheduledBackupEvent, updateScheduledBackupEvent } from "../../api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../../components/ui/dialog";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { ApiError } from "../../notices";
import type { ExternalDisk, ScheduledBackupEvent, VirtualMachine } from "../../types";
import { useConfirm } from "../../ui/confirm";
import { useToast } from "../../ui/toast";
import { fill } from "../format";
import {
  dayKey,
  emptyForm,
  formFromEvent,
  formProblems,
  formToPayload,
  recurrenceSentence,
  type PlanningForm,
  type Recurrence,
} from "./calendarModel";

export type DialogState = { mode: "create"; start: Date } | { mode: "edit"; event: ScheduledBackupEvent };

const RECURRENCES: Recurrence[] = ["once", "daily", "weekly", "monthly"];
const field = "rounded-lg border border-solid border-border-strong bg-bg px-3 py-2 text-sm text-fg outline-none focus:border-accent";
const labelClass = "text-[13px] font-medium text-fg";

export function vmGroup(vm: Pick<VirtualMachine, "vm_type" | "external_id">): string | null {
  return vm.external_id ? `${vm.vm_type === "ct" ? "ct" : "vm"}/${vm.external_id}` : null;
}

export function PlanningDialog({
  state,
  onClose,
  onSaved,
  disks,
  vms,
  defaultDatastore,
  tz,
  language,
  t,
}: {
  state: DialogState | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
  disks: ExternalDisk[];
  vms: VirtualMachine[];
  defaultDatastore: string;
  tz: string;
  language: Language;
  t: TranslationDictionary;
}) {
  const text = t.planningV2.dialog;
  const { toast } = useToast();
  const confirm = useConfirm();
  const [form, setForm] = useState<PlanningForm | null>(null);
  const [someVms, setSomeVms] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showProblems, setShowProblems] = useState(false);

  useEffect(() => {
    if (!state) {
      setForm(null);
      return;
    }
    const next = state.mode === "edit" ? formFromEvent(state.event as never, tz) : emptyForm(state.start, tz, defaultDatastore);
    setForm(next);
    setSomeVms(next.vmGroups.length > 0);
    setShowProblems(false);
  }, [state, tz, defaultDatastore]);

  const approved = useMemo(() => disks.filter((disk) => disk.trusted && !disk.reject_reason), [disks]);
  const nameOf = (serial: string) => disks.find((disk) => disk.serial_number === serial)?.display_name ?? serial;
  const guests = useMemo(
    () =>
      vms
        .map((vm) => ({ vm, group: vmGroup(vm) }))
        .filter((entry): entry is { vm: VirtualMachine; group: string } => entry.group !== null)
        .sort((a, b) => a.group.localeCompare(b.group, undefined, { numeric: true })),
    [vms],
  );

  if (!state || !form) return null;
  const update = (patch: Partial<PlanningForm>) => setForm({ ...form, ...patch });
  const problems = formProblems({ ...form, vmGroups: someVms ? form.vmGroups : [] });
  const sentence = recurrenceSentence(form, form.diskSerials.map(nameOf), t.planningV2.sentence, language);
  const editing = state.mode === "edit";

  function move(index: number, delta: number) {
    if (!form) return;
    const next = [...form.diskSerials];
    const [moved] = next.splice(index, 1);
    next.splice(index + delta, 0, moved);
    update({ diskSerials: next });
  }

  async function save() {
    if (!form || !state) return;
    if (problems.length) {
      setShowProblems(true);
      return;
    }
    setBusy(true);
    try {
      const payload = formToPayload({ ...form, vmGroups: someVms ? form.vmGroups : [] }, tz);
      if (state.mode === "edit") await updateScheduledBackupEvent(state.event.id, payload as never);
      else await createScheduledBackupEvent(payload as never);
      toast({ tone: "success", title: text.saved });
      await onSaved();
    } catch (error) {
      const table = t.planningV2.errors as Record<string, string>;
      const message = error instanceof ApiError && error.code && table[error.code] ? table[error.code] : error instanceof Error ? error.message : t.error;
      toast({ tone: "danger", title: message });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (state?.mode !== "edit") return;
    if (!(await confirm({ title: text.deleteTitle, description: text.deleteText, confirmLabel: text.delete, tone: "danger" }))) return;
    try {
      await deleteScheduledBackupEvent(state.event.id);
      toast({ tone: "success", title: text.deleted });
      await onSaved();
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    }
  }

  const unitKey = form.recurrence === "once" ? null : form.recurrence;
  const dateValue = dayKey(form.day);
  const timeValue = `${String(form.hour).padStart(2, "0")}:${String(form.minute).padStart(2, "0")}`;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[640px]" aria-describedby="planning-dialog-preview">
        <div className="flex items-center gap-2 border-b border-solid border-border px-5 py-4">
          <DialogTitle className="m-0 flex-1 text-[17px] font-semibold">{editing ? text.editTitle : text.createTitle}</DialogTitle>
          <button type="button" aria-label={t.planningV2.closeDetail} onClick={onClose} className="rounded-md border-0 bg-transparent p-1 text-muted hover:bg-hover">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex flex-col gap-[18px] overflow-y-auto px-5 py-4">
          <label className="flex flex-col gap-1.5">
            <span className={labelClass}>{text.name}</span>
            <input className={field} value={form.title} placeholder={text.namePlaceholder} onChange={(event) => update({ title: event.target.value })} />
            {showProblems && problems.includes("title") ? <span className="text-xs text-danger-text">{text.problem.title}</span> : null}
          </label>

          <div className="flex flex-col gap-1.5">
            <span className={labelClass}>{text.disks}</span>
            <span className="text-xs text-faint">{text.disksHelp}</span>
            {form.diskSerials.length ? (
              <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
                {form.diskSerials.map((serial, index) => (
                  <li key={serial} className="flex items-center gap-2 rounded-lg bg-subtle px-2.5 py-1.5 text-sm" data-rotation={index}>
                    <span className="w-5 text-center font-mono text-xs text-faint">{index + 1}</span>
                    <span className="flex-1 truncate">{nameOf(serial)}</span>
                    <button type="button" aria-label={text.moveUp} title={text.moveUp} disabled={index === 0} onClick={() => move(index, -1)} className="rounded border-0 bg-transparent p-1 text-muted hover:text-fg disabled:opacity-30">
                      <ArrowUp className="h-3.5 w-3.5" />
                    </button>
                    <button type="button" aria-label={text.moveDown} title={text.moveDown} disabled={index === form.diskSerials.length - 1} onClick={() => move(index, 1)} className="rounded border-0 bg-transparent p-1 text-muted hover:text-fg disabled:opacity-30">
                      <ArrowDown className="h-3.5 w-3.5" />
                    </button>
                    <button type="button" aria-label={text.remove} title={text.remove} onClick={() => update({ diskSerials: form.diskSerials.filter((item) => item !== serial) })} className="rounded border-0 bg-transparent p-1 text-muted hover:text-danger-text">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ol>
            ) : null}
            {approved.length ? (
              <select
                aria-label={text.addDisk}
                className={field}
                value=""
                onChange={(event) => event.target.value && update({ diskSerials: [...form.diskSerials, event.target.value] })}
              >
                <option value="">{text.addDisk}</option>
                {approved
                  .filter((disk) => !form.diskSerials.includes(disk.serial_number))
                  .map((disk) => (
                    <option key={disk.id} value={disk.serial_number}>
                      {disk.display_name}
                    </option>
                  ))}
              </select>
            ) : (
              <span className="text-xs text-warning-text">{text.noDisk}</span>
            )}
            {showProblems && problems.includes("disks") ? <span className="text-xs text-danger-text">{text.problem.disks}</span> : null}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5">
              <span className={labelClass}>{text.recurrence}</span>
              <select className={field} value={form.recurrence} onChange={(event) => update({ recurrence: event.target.value as Recurrence })}>
                {RECURRENCES.map((value) => (
                  <option key={value} value={value}>
                    {text.recurrenceKind[value]}
                  </option>
                ))}
              </select>
            </label>
            {unitKey ? (
              <label className="flex flex-col gap-1.5">
                <span className={labelClass}>{text.every}</span>
                <span className="flex items-center gap-2">
                  <input
                    type="number"
                    min={1}
                    max={52}
                    className={cn(field, "w-20")}
                    value={form.interval}
                    onChange={(event) => update({ interval: Number(event.target.value) })}
                  />
                  <span className="text-sm text-muted">{text.unit[unitKey]}</span>
                </span>
                {showProblems && problems.includes("interval") ? <span className="text-xs text-danger-text">{text.problem.interval}</span> : null}
              </label>
            ) : null}
            <label className="flex flex-col gap-1.5">
              <span className={labelClass}>{text.firstDate}</span>
              <input
                type="date"
                className={field}
                value={dateValue}
                onChange={(event) => {
                  const [y, m, d] = event.target.value.split("-").map(Number);
                  if (y && m && d) update({ day: { y, m, d } });
                }}
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className={labelClass}>{text.time}</span>
              <input
                type="time"
                className={field}
                value={timeValue}
                onChange={(event) => {
                  const [h, min] = event.target.value.split(":").map(Number);
                  if (Number.isFinite(h) && Number.isFinite(min)) update({ hour: h, minute: min });
                }}
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className={labelClass}>{text.window}</span>
              <span className="flex items-center gap-2">
                <input
                  type="number"
                  min={0.25}
                  max={168}
                  step={0.5}
                  className={cn(field, "w-24")}
                  value={form.windowMinutes / 60}
                  onChange={(event) => update({ windowMinutes: Math.round(Number(event.target.value) * 60) })}
                />
                <span className="text-sm text-muted">{text.hours}</span>
              </span>
              <span className="text-xs text-faint">{text.windowHelp}</span>
              {showProblems && problems.includes("window") ? <span className="text-xs text-danger-text">{text.problem.window}</span> : null}
            </label>
            <label className="flex flex-col gap-1.5">
              <span className={labelClass}>{text.datastore}</span>
              <input className={field} value={form.datastore} onChange={(event) => update({ datastore: event.target.value })} />
              {showProblems && problems.includes("datastore") ? <span className="text-xs text-danger-text">{text.problem.datastore}</span> : null}
            </label>
          </div>

          <div className="flex flex-col gap-2">
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="checkbox" className="m-0 h-4 w-4" checked={form.confirmation} onChange={(event) => update({ confirmation: event.target.checked })} />
              {text.confirmation}
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="checkbox" className="m-0 h-4 w-4" checked={form.autoEject} onChange={(event) => update({ autoEject: event.target.checked })} />
              {text.autoEject}
            </label>
            {editing ? (
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input type="checkbox" className="m-0 h-4 w-4" checked={form.enabled} onChange={(event) => update({ enabled: event.target.checked })} />
                {text.enabled}
              </label>
            ) : null}
          </div>

          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className={cn(labelClass, "mb-1.5 p-0")}>{text.vms}</legend>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="radio" name="vms" className="m-0" checked={!someVms} onChange={() => setSomeVms(false)} />
              {text.vmsAll}
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="radio" name="vms" className="m-0" checked={someVms} onChange={() => setSomeVms(true)} />
              {text.vmsSome}
            </label>
            {someVms ? (
              <div className="grid max-h-44 grid-cols-1 gap-1 overflow-y-auto rounded-lg border border-solid border-border p-2 sm:grid-cols-2">
                {guests.map(({ vm, group }) => (
                  <label key={group} className="flex cursor-pointer items-center gap-2 text-[13px]">
                    <input
                      type="checkbox"
                      className="m-0 h-3.5 w-3.5"
                      checked={form.vmGroups.includes(group)}
                      onChange={(event) =>
                        update({ vmGroups: event.target.checked ? [...form.vmGroups, group] : form.vmGroups.filter((item) => item !== group) })
                      }
                    />
                    <span className="font-mono text-xs text-faint">{group}</span>
                    <span className="truncate">{vm.name}</span>
                  </label>
                ))}
              </div>
            ) : null}
            <span className="text-xs text-faint">{text.vmsApiOnly}</span>
          </fieldset>

          <div className="rounded-[10px] bg-accent/10 p-3">
            <span className="text-[11px] font-medium uppercase tracking-[0.04em] text-accent-text">{text.preview}</span>
            <DialogDescription id="planning-dialog-preview" className="m-0 mt-1 text-sm text-fg" data-preview="">
              {sentence}
            </DialogDescription>
            <span className="mt-1 block text-xs text-faint">{fill(text.timezone, { tz })}</span>
          </div>
        </div>
        <div className="flex items-center gap-2 border-t border-solid border-border px-5 py-3.5">
          {editing ? (
            <button type="button" onClick={() => void remove()} className="border-0 bg-transparent px-1 py-2 text-[13px] font-medium text-danger-text hover:underline">
              {text.delete}
            </button>
          ) : null}
          <span className="flex-1" />
          <button type="button" onClick={onClose} className="rounded-lg border border-solid border-border bg-subtle px-3.5 py-2 text-sm font-medium text-fg hover:brightness-110">
            {t.confirmDialogs.cancel}
          </button>
          <button type="button" disabled={busy} onClick={() => void save()} className="rounded-lg border-0 bg-accent px-3.5 py-2 text-sm font-medium text-white hover:brightness-110 disabled:opacity-60">
            {editing ? text.save : text.create}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
