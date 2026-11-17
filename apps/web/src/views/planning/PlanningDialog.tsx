import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, Check, ChevronRight } from "lucide-react";

import { createScheduledBackupEvent, deleteScheduledBackupEvent, getDatastores, updateScheduledBackupEvent, type SourceDatastore } from "../../api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../../components/ui/dialog";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { ApiError } from "../../notices";
import type { ExternalDisk, ScheduledBackupEvent, VirtualMachine } from "../../types";
import { useConfirm } from "../../ui/confirm";
import { useToast } from "../../ui/toast";
import { formatBytes, formatGib } from "../../units";
import { fill } from "../format";
import { DiskAvatar } from "../disks/DiskBits";
import { CloseButton, DayPills } from "../resources/ProxmoxJobsSection";
import { DAY_CODES, type DayCode } from "../resources/jobsModel";
import { zoned } from "./calendarModel";
import {
  PRESETS,
  WINDOW_CHOICES,
  autoTitle,
  effective,
  newPlanForm,
  planFormFromEvent,
  planPayload,
  planProblems,
  planSentence,
  windowEnd,
  type PlanForm,
  type Preset,
  type Unit,
} from "./planningForm";

/** "duplicate" (context menu): the event's settings in a new planning. */
export type DialogState = { mode: "create"; start: Date } | { mode: "edit"; event: ScheduledBackupEvent } | { mode: "duplicate"; event: ScheduledBackupEvent };

const field = "rounded-lg border border-solid border-border-strong bg-bg px-3 py-2 text-sm text-fg outline-none focus:border-accent";

export function vmGroup(vm: Pick<VirtualMachine, "vm_type" | "external_id">): string | null {
  return vm.external_id ? `${vm.vm_type === "ct" ? "ct" : "vm"}/${vm.external_id}` : null;
}

function Question({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="m-0 flex items-center gap-2.5 text-sm font-semibold">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent/15 text-xs font-bold text-accent-text">{n}</span>
        {title}
      </h3>
      <div className="flex flex-col gap-3 pl-[34px]">{children}</div>
    </section>
  );
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
  now: fixedNow,
  loadDatastores = getDatastores,
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
  now?: Date;
  loadDatastores?: typeof getDatastores;
}) {
  const text = t.planningV2.form;
  const { toast } = useToast();
  const confirm = useConfirm();
  const [form, setForm] = useState<PlanForm | null>(null);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  const [datastores, setDatastores] = useState<SourceDatastore[] | null>(null);
  const nameInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!state) return setForm(null);
    setTouched(false);
    if (state.mode === "edit") setForm(planFormFromEvent(state.event as never, tz));
    else if (state.mode === "duplicate") {
      const copy = planFormFromEvent(state.event as never, tz);
      setForm({ ...copy, title: fill(text.copyOf, { title: state.event.title }), titleTouched: true });
    } else {
      const local = zoned(state.start, tz);
      setForm(newPlanForm(defaultDatastore, { day: local, hour: local.h }));
    }
    void loadDatastores().then(
      (result) => setDatastores(result.items),
      () => setDatastores([]),
    );
  }, [state, tz, defaultDatastore, loadDatastores]);

  const approved = useMemo(() => disks.filter((disk) => disk.trusted && !disk.reject_reason), [disks]);
  const guests = useMemo(
    () =>
      vms
        .map((vm) => ({ vm, group: vmGroup(vm) }))
        .filter((entry): entry is { vm: VirtualMachine; group: string } => entry.group !== null)
        .sort((a, b) => a.group.localeCompare(b.group, undefined, { numeric: true })),
    [vms],
  );

  if (!state || !form) return null;
  const editing = state.mode === "edit";
  const update = (patch: Partial<PlanForm>) => setForm({ ...form, ...patch });
  const problems = planProblems(form);
  const show = (key: (typeof problems)[number]) => touched && problems.includes(key);
  const nameOf = (serial: string) => disks.find((disk) => disk.serial_number === serial)?.display_name ?? serial;
  const sentence = planSentence(form, form.diskSerials.map(nameOf), text, language);
  const generated = autoTitle(form, text, language);
  const { unit } = effective(form);
  const until = windowEnd(form);
  const ordered = [...form.diskSerials.map((serial) => approved.find((disk) => disk.serial_number === serial)).filter((disk): disk is ExternalDisk => Boolean(disk)), ...approved.filter((disk) => !form.diskSerials.includes(disk.serial_number))];

  function toggleDisk(serial: string) {
    if (!form) return;
    update({ diskSerials: form.diskSerials.includes(serial) ? form.diskSerials.filter((item) => item !== serial) : [...form.diskSerials, serial] });
  }

  function move(serial: string, delta: number) {
    if (!form) return;
    const next = [...form.diskSerials];
    const index = next.indexOf(serial);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    update({ diskSerials: next });
  }

  async function save() {
    if (!form || !state) return;
    setTouched(true);
    if (problems.length) return;
    setBusy(true);
    try {
      const payload = planPayload(form, text, language, fixedNow ?? new Date(), tz);
      if (state.mode === "edit") await updateScheduledBackupEvent(state.event.id, payload as never);
      else await createScheduledBackupEvent(payload as never);
      toast({ tone: "success", title: t.planningV2.dialog.saved });
      await onSaved();
    } catch (error) {
      const table = t.planningV2.errors as Record<string, string>;
      toast({ tone: "danger", title: error instanceof ApiError && error.code && table[error.code] ? table[error.code] : error instanceof Error ? error.message : t.error });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (state?.mode !== "edit") return;
    const dialog = t.planningV2.dialog;
    if (!(await confirm({ title: dialog.deleteTitle, description: dialog.deleteText, confirmLabel: dialog.delete, tone: "danger" }))) return;
    try {
      await deleteScheduledBackupEvent(state.event.id);
      toast({ tone: "success", title: dialog.deleted });
      await onSaved();
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    }
  }

  const days = form.weekdays.map((day) => DAY_CODES[day]);
  const toggleDay = (code: DayCode) => {
    const day = DAY_CODES.indexOf(code);
    update({ weekdays: form.weekdays.includes(day) ? form.weekdays.filter((item) => item !== day) : [...form.weekdays, day].sort() });
  };
  const sourceKnown = datastores?.some((item) => item.name === form.datastore);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="max-w-[680px]"
        aria-describedby="planning-dialog-preview"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          nameInput.current?.focus();
        }}
      >
        <div className="flex items-center gap-2 border-0 border-b border-solid border-border px-5 py-4">
          <DialogTitle className="m-0 flex-1 text-[17px] font-semibold">{editing ? text.editTitle : state.mode === "duplicate" ? text.duplicateTitle : text.createTitle}</DialogTitle>
          <CloseButton label={t.planningV2.closeDetail} onClick={onClose} />
        </div>

        <div className="flex flex-col gap-6 overflow-y-auto px-5 py-5">
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium">{text.name}</span>
            <input
              ref={nameInput}
              className={field}
              value={form.titleTouched ? form.title : generated}
              onChange={(event) => update({ title: event.target.value, titleTouched: true })}
            />
            {show("title") ? <span className="text-xs text-danger-text">{text.problem.title}</span> : null}
          </label>

          <Question n={1} title={text.when}>
            <div role="radiogroup" aria-label={text.when} className="flex flex-wrap gap-1.5">
              {PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  role="radio"
                  aria-checked={form.preset === preset}
                  onClick={() => update({ preset })}
                  className={cn(
                    "rounded-full border border-solid px-3 py-1.5 text-[13px] font-medium",
                    form.preset === preset ? "border-accent bg-accent/15 text-accent-text" : "border-border bg-transparent text-muted hover:text-fg",
                  )}
                >
                  {text.preset[preset as Preset]}
                </button>
              ))}
            </div>
            {form.preset === "custom" ? (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted">{text.every}</span>
                <input type="number" min={1} max={52} aria-label={text.every} className={cn(field, "w-20")} value={form.interval} onChange={(event) => update({ interval: Number(event.target.value) })} />
                <select aria-label={text.unitLabel} className={field} value={form.unit} onChange={(event) => update({ unit: event.target.value as Unit })}>
                  {(["daily", "weekly", "monthly"] as const).map((value) => (
                    <option key={value} value={value}>
                      {text.unit[value]}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
            {unit === "weekly" ? (
              <>
                <DayPills days={days} onToggle={toggleDay} lang={language} />
                {show("weekdays") ? <span className="text-xs text-danger-text">{text.problem.weekdays}</span> : null}
              </>
            ) : null}
            <div className="flex flex-wrap items-center gap-3">
              {unit === "monthly" ? (
                <label className="flex items-center gap-2 text-sm text-muted">
                  {text.monthDay}
                  <input type="number" min={1} max={31} className={cn(field, "w-20")} value={form.monthDay} onChange={(event) => update({ monthDay: Math.max(1, Math.min(31, Number(event.target.value) || 1)) })} />
                </label>
              ) : null}
              <label className="flex items-center gap-2 text-sm text-muted">
                {text.at}
                <input
                  type="time"
                  className={cn(field, "w-32")}
                  value={`${String(form.hour).padStart(2, "0")}:${String(form.minute).padStart(2, "0")}`}
                  onChange={(event) => {
                    const [h, m] = event.target.value.split(":").map(Number);
                    if (Number.isFinite(h) && Number.isFinite(m)) update({ hour: h, minute: m });
                  }}
                />
              </label>
            </div>
          </Question>

          <Question n={2} title={text.which}>
            {approved.length === 0 ? <span className="text-sm text-warning-text">{t.planningV2.dialog.noDisk}</span> : null}
            <div className="grid gap-2 sm:grid-cols-2">
              {ordered.map((disk) => {
                const position = form.diskSerials.indexOf(disk.serial_number);
                const selected = position >= 0;
                const ranked = selected && form.diskSerials.length >= 2;
                return (
                  <div
                    key={disk.id}
                    data-disk-card={disk.serial_number}
                    className={cn(
                      "flex items-center gap-3 rounded-xl border border-solid px-3 py-2.5",
                      selected ? "border-accent bg-accent/[0.07]" : "border-border",
                    )}
                  >
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked={selected}
                      aria-label={disk.display_name}
                      onClick={() => toggleDisk(disk.serial_number)}
                      className={cn(
                        "flex h-6 w-6 flex-none items-center justify-center rounded-md border border-solid text-xs font-bold",
                        selected ? "border-accent bg-accent text-white" : "border-border-strong bg-transparent text-transparent",
                      )}
                    >
                      {ranked ? position + 1 : <Check className="h-3.5 w-3.5" />}
                    </button>
                    <DiskAvatar disk={disk} t={t} size="sm" />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate text-sm font-medium">{disk.display_name}</span>
                      <span className="text-xs text-faint">
                        {formatGib(disk.capacity_gb, language)} · {disk.connected ? text.plugged : text.unplugged}
                      </span>
                    </span>
                    {ranked ? (
                      <span className="flex flex-none gap-0.5">
                        <button type="button" aria-label={t.planningV2.dialog.moveUp} title={t.planningV2.dialog.moveUp} disabled={position === 0} onClick={() => move(disk.serial_number, -1)} className="rounded border-0 bg-transparent p-1 text-muted hover:text-fg disabled:opacity-30">
                          <ArrowUp className="h-3.5 w-3.5" />
                        </button>
                        <button type="button" aria-label={t.planningV2.dialog.moveDown} title={t.planningV2.dialog.moveDown} disabled={position === form.diskSerials.length - 1} onClick={() => move(disk.serial_number, 1)} className="rounded border-0 bg-transparent p-1 text-muted hover:text-fg disabled:opacity-30">
                          <ArrowDown className="h-3.5 w-3.5" />
                        </button>
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </div>
            {form.diskSerials.length > 1 ? <span className="text-xs text-muted">{text.rotationHint}</span> : null}
            {show("disks") ? <span className="text-xs text-danger-text">{text.problem.disks}</span> : null}
          </Question>

          <Question n={3} title={text.what}>
            {form.allGuests ? (
              <div className="flex flex-wrap items-center gap-3 rounded-xl border border-solid border-border px-3 py-2.5 text-sm">
                <Check className="h-4 w-4 text-success-text" />
                <span className="flex-1">{fill(text.allGuests, { datastore: form.datastore || "—" })}</span>
                <button type="button" className="flex items-center gap-1 border-0 bg-transparent text-[13px] font-medium text-accent-text hover:underline" onClick={() => update({ allGuests: false })}>
                  {text.chooseGuests}
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <div className="grid max-h-48 grid-cols-1 gap-1 overflow-y-auto rounded-lg border border-solid border-border p-2 sm:grid-cols-2">
                  {guests.map(({ vm, group }) => (
                    <label key={group} className="flex cursor-pointer items-center gap-2 text-[13px]">
                      <input
                        type="checkbox"
                        className="m-0 h-3.5 w-3.5"
                        checked={form.vmGroups.includes(group)}
                        onChange={(event) => update({ vmGroups: event.target.checked ? [...form.vmGroups, group] : form.vmGroups.filter((item) => item !== group) })}
                      />
                      <span className="font-mono text-xs text-faint">{group}</span>
                      <span className="truncate">{vm.name}</span>
                    </label>
                  ))}
                </div>
                <span className="text-xs text-faint">{t.planningV2.dialog.vmsApiOnly}</span>
                <button type="button" className="self-start border-0 bg-transparent p-0 text-[13px] text-accent-text hover:underline" onClick={() => update({ allGuests: true, vmGroups: [] })}>
                  {text.backToAll}
                </button>
                {show("guests") ? <span className="text-xs text-danger-text">{text.problem.guests}</span> : null}
              </div>
            )}
          </Question>

          <details className="rounded-xl border border-solid border-border" open={show("datastore") || undefined}>
            <summary className="cursor-pointer px-4 py-3 text-[13px] font-medium text-muted">{text.advanced}</summary>
            <div className="flex flex-col gap-3 px-4 pb-4">
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium">{text.waitUntil}</span>
                <select className={field} value={form.windowMinutes} onChange={(event) => update({ windowMinutes: Number(event.target.value) })}>
                  {[...new Set([...WINDOW_CHOICES, form.windowMinutes])].sort((a, b) => a - b).map((minutes) => {
                    const end = windowEnd({ ...form, windowMinutes: minutes });
                    return (
                      <option key={minutes} value={minutes}>
                        {fill(end.nextDay ? text.untilNextDay : text.until, { time: end.time, hours: Math.round((minutes / 60) * 10) / 10 })}
                      </option>
                    );
                  })}
                </select>
                <span className="text-xs text-faint">{fill(until.nextDay ? text.waitHintNextDay : text.waitHint, { time: until.time })}</span>
              </label>
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input type="checkbox" className="m-0 h-4 w-4" checked={form.confirmation} onChange={(event) => update({ confirmation: event.target.checked })} />
                {t.planningV2.dialog.confirmation}
              </label>
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input type="checkbox" className="m-0 h-4 w-4" checked={form.autoEject} onChange={(event) => update({ autoEject: event.target.checked })} />
                {t.planningV2.dialog.autoEject}
              </label>
              <label className="flex cursor-pointer items-start gap-2 text-sm">
                <input type="checkbox" className="m-0 mt-0.5 h-4 w-4" checked={form.verify} onChange={(event) => update({ verify: event.target.checked })} />
                <span className="flex flex-col">
                  {t.launch.verify}
                  <span className="text-xs text-faint">{t.launch.verifyHelp}</span>
                </span>
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium">{t.planningV2.dialog.datastore}</span>
                <select className={field} value={form.datastore} onChange={(event) => update({ datastore: event.target.value })}>
                  {!sourceKnown ? <option value={form.datastore}>{form.datastore ? fill(text.datastoreMissing, { name: form.datastore }) : "—"}</option> : null}
                  {(datastores ?? []).map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.total ? fill(text.datastoreOption, { name: item.name, used: formatBytes(item.used ?? 0, language), total: formatBytes(item.total, language) }) : item.name}
                    </option>
                  ))}
                </select>
                {datastores && !sourceKnown && form.datastore ? <span className="text-xs text-warning-text">{text.datastoreUnknown}</span> : null}
                {show("datastore") ? <span className="text-xs text-danger-text">{text.problem.datastore}</span> : null}
              </label>
              {editing ? (
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <input type="checkbox" className="m-0 h-4 w-4" checked={form.enabled} onChange={(event) => update({ enabled: event.target.checked })} />
                  {t.planningV2.dialog.enabled}
                </label>
              ) : null}
            </div>
          </details>
        </div>

        <div className="flex flex-col gap-3 border-0 border-t border-solid border-border px-5 py-3.5">
          <DialogDescription id="planning-dialog-preview" className="m-0 rounded-md bg-accent/10 px-3 py-2 text-sm text-fg" data-preview="">
            {sentence}
          </DialogDescription>
          <div className="flex items-center gap-2">
            {editing ? (
              <button type="button" onClick={() => void remove()} className="border-0 bg-transparent px-1 py-2 text-[13px] font-medium text-danger-text hover:underline">
                {t.planningV2.dialog.delete}
              </button>
            ) : null}
            <span className="flex-1 text-xs text-faint">{fill(t.planningV2.dialog.timezone, { tz })}</span>
            <button type="button" onClick={onClose} className="rounded-lg border border-solid border-border bg-subtle px-3.5 py-2 text-sm font-medium text-fg hover:brightness-110">
              {t.confirmDialogs.cancel}
            </button>
            <button type="button" disabled={busy} onClick={() => void save()} className="rounded-lg border-0 bg-accent px-3.5 py-2 text-sm font-medium text-white hover:brightness-110 disabled:opacity-60">
              {editing ? text.save : text.create}
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
