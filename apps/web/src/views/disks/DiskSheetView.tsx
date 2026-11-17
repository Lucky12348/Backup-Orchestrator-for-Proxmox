import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Camera, Loader2, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import {
  deleteDiskPhoto,
  dismissDiskVerify,
  getDiskContents,
  getRotationStaleDays,
  refreshDiskContents,
  setRotationStaleDays,
  startDiskVerify,
  updateDiskProfile,
  uploadDiskPhoto,
  type DiskAlert,
  type DiskContents,
} from "../../api";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { ApiError } from "../../notices";
import { openRunModal } from "../../runs/openRun";
import type { ExternalBackupRun, ExternalDisk } from "../../types";
import { useToast } from "../../ui/toast";
import { formatBytes, formatUsage } from "../../units";
import { fill, formatWhen } from "../format";
import { AlertBadges, CONFIG_ALERT_NOTE, DiskAvatar, PbsConfigNotice } from "./DiskBits";
import { diskHistory, diskRows, usagePercent, type Tone } from "./diskModel";
import {
  alertsFor,
  COLOR_CLASS,
  DISK_COLORS,
  diskColor,
  diskKind,
  retentionForm,
  retentionPayload,
  usageLevel,
  usageSeries,
  type DiskColor,
  type RetentionForm,
} from "./diskProfile";
import { StatusPill } from "./DisksView";
import { UsageChart } from "./UsageChart";

const card = "flex flex-col gap-3 rounded-[14px] border border-solid border-border bg-surface p-[18px]";
const input = "rounded-lg border border-solid border-border-strong bg-bg px-3 py-[9px] text-sm text-fg outline-none focus:border-accent";
const secondary =
  "flex items-center justify-center gap-2 rounded-lg border border-solid border-border bg-subtle px-3 py-[9px] text-[13px] font-medium text-fg hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50";
const primary = "flex items-center justify-center gap-2 rounded-lg border-0 bg-accent px-3.5 py-[9px] text-[13px] font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50";
const DOT: Record<Tone, string> = { ok: "bg-success", run: "bg-accent", warn: "bg-warning", err: "bg-danger", off: "bg-faint" };
const SMART_TONE: Record<string, string> = {
  ok: "bg-success/15 text-success-text",
  warning: "bg-warning/15 text-warning-text",
  danger: "bg-danger/15 text-danger-text",
  unknown: "bg-subtle text-muted",
};

export interface DiskSheetViewProps {
  disks: ExternalDisk[];
  runs: ExternalBackupRun[];
  alerts: DiskAlert[];
  language: Language;
  t: TranslationDictionary;
  onChanged: () => Promise<void> | void;
}

export function errorMessage(error: unknown, t: TranslationDictionary): string {
  const errors = t.diskSheet.errors as Record<string, string>;
  if (error instanceof ApiError && error.code && errors[error.code]) return errors[error.code];
  return error instanceof Error ? error.message : t.error;
}

export function DiskSheetView({ disks, runs, alerts, language, t, onChanged }: DiskSheetViewProps) {
  const { id } = useParams();
  const disk = disks.find((item) => String(item.id) === id);
  const text = t.diskSheet;
  if (!disk) {
    return (
      <div className="flex flex-col gap-4">
        <BackLink t={t} />
        <p className="m-0 text-[15px] text-muted">{text.notFound}</p>
      </div>
    );
  }
  const row = diskRows([disk], runs)[0];
  const diskAlerts = alertsFor(alerts, disk.id);
  return (
    <div className="flex flex-col gap-[18px]" data-disk-sheet={disk.id}>
      <BackLink t={t} />
      <header className="flex flex-wrap items-center gap-4">
        <DiskAvatar disk={disk} t={t} size="lg" />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="m-0 text-[26px] font-semibold tracking-[-0.02em]">{disk.display_name}</h1>
            <StatusPill status={row.status} label={t.disksV2.status[row.status]} />
          </div>
          <span className="text-[13px] text-muted">
            {[text.kind[diskKind(disk.kind)], disk.model_name, disk.serial_number].filter(Boolean).join(" · ")}
          </span>
          {disk.location_note ? <span className="text-[13px] text-faint">{fill(text.locationShown, { location: disk.location_note })}</span> : null}
          <AlertBadges alerts={diskAlerts} t={t} />
        </div>
      </header>

      {diskAlerts
        .filter((alert) => alert.code in CONFIG_ALERT_NOTE)
        .map((alert) => (
          <PbsConfigNotice
            key={alert.code}
            note={CONFIG_ALERT_NOTE[alert.code as keyof typeof CONFIG_ALERT_NOTE]}
            datastore={String(alert.params.datastore ?? disk.pbs_datastore_name ?? "")}
            serial={disk.serial_number}
            t={t}
          />
        ))}

      {disk.verify_recommended ? (
        <VerifyBanner disk={disk} t={t} language={language} onChanged={onChanged} failed={diskAlerts.some((alert) => alert.code === "verify_failed")} />
      ) : null}

      <div className="grid items-start gap-[18px] xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="flex min-w-0 flex-col gap-[18px]">
          <ContentsCard disk={disk} t={t} language={language} />
          <section className={card} aria-labelledby="usage-title">
            <div className="flex flex-wrap items-baseline gap-2">
              <h2 id="usage-title" className="m-0 text-[15px] font-semibold">{text.usage}</h2>
              <span className="flex-1" />
              {usagePercent(disk) !== null ? (
                <span
                  className={cn(
                    "text-[13px] font-medium",
                    { ok: "text-muted", warning: "text-warning-text", danger: "text-danger-text" }[usageLevel(usagePercent(disk))],
                  )}
                >
                  {fill(text.usageNow, { usage: formatUsage(disk.filesystem_used_gb, disk.filesystem_total_gb, language), pct: usagePercent(disk) ?? 0 })}
                </span>
              ) : null}
            </div>
            <UsageChart points={usageSeries(disk.usage_history)} name={disk.display_name} language={language} t={t} />
          </section>
          <section className={card}>
            <h2 className="m-0 text-[15px] font-semibold">{text.history}</h2>
            <History disk={disk} runs={runs} language={language} t={t} />
          </section>
        </div>
        <div className="flex min-w-0 flex-col gap-[18px]">
          <IdentityCard disk={disk} t={t} onChanged={onChanged} />
          <HealthCard disk={disk} t={t} language={language} />
          <RetentionCard disk={disk} t={t} onChanged={onChanged} />
        </div>
      </div>
    </div>
  );
}

function BackLink({ t }: { t: TranslationDictionary }) {
  return (
    <Link to="/disks" className="flex w-fit items-center gap-1.5 text-[13px] text-muted no-underline hover:text-fg">
      <ArrowLeft className="h-4 w-4" />
      {t.diskSheet.back}
    </Link>
  );
}

function when(iso: string | null | undefined, language: Language, t: TranslationDictionary): string {
  return formatWhen(iso ?? null, language, new Date(), t.dashboardV2);
}

function VerifyBanner({
  disk,
  t,
  language,
  onChanged,
  failed = false,
}: {
  disk: ExternalDisk;
  t: TranslationDictionary;
  language: Language;
  onChanged: () => Promise<void> | void;
  failed?: boolean;
}) {
  const text = t.diskSheet.verify;
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  async function verify() {
    setBusy(true);
    try {
      const result = await startDiskVerify(disk.id, false);
      openRunModal(result.run_id);
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  async function dismiss() {
    setBusy(true);
    try {
      await dismissDiskVerify(disk.id);
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-[14px] bg-warning/15 p-4 text-[13px] leading-[1.45] text-warning-text" data-verify-banner>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <b>{failed ? text.failedTitle : text.title}</b>
        <span>{failed ? text.failedText : fill(text.text, { when: when(disk.unsafe_removal_at, language, t) })}</span>
        {disk.connected ? null : <span>{text.needsPlug}</span>}
      </div>
      <button type="button" className={primary} disabled={busy || !disk.connected} onClick={() => void verify()}>
        <ShieldCheck className="h-4 w-4" />
        {text.action}
      </button>
      <button type="button" className={secondary} disabled={busy} onClick={() => void dismiss()}>
        {text.dismiss}
      </button>
    </div>
  );
}

export function ContentsCard({ disk, t, language, initial }: { disk: ExternalDisk; t: TranslationDictionary; language: Language; initial?: DiskContents }) {
  const text = t.diskSheet;
  const { toast } = useToast();
  const [contents, setContents] = useState<DiskContents | null>(initial ?? null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (initial) return;
    let cancelled = false;
    getDiskContents(disk.id).then(
      (result) => !cancelled && setContents(result),
      () => !cancelled && setContents(null),
    );
    return () => {
      cancelled = true;
    };
  }, [disk.id, disk.contents_cached_at, initial]);
  async function refresh() {
    setBusy(true);
    try {
      setContents(await refreshDiskContents(disk.id));
    } catch (error) {
      toast({ tone: "warning", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  const items = contents?.items ?? [];
  const date = (iso: string) => new Intl.DateTimeFormat(language === "fr" ? "fr-FR" : "en-GB", { dateStyle: "medium" }).format(new Date(iso));
  return (
    <section className={card} aria-labelledby="contents-title">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="contents-title" className="m-0 text-[15px] font-semibold">{text.contents}</h2>
        {contents?.cached_at ? <span className="text-xs text-faint">{fill(text.contentsAsOf, { when: when(contents.cached_at, language, t) })}</span> : null}
        <span className="flex-1" />
        {disk.pbs_datastore_name ? (
          <button type="button" className={secondary} disabled={busy} onClick={() => void refresh()}>
            <RefreshCw className={cn("h-3.5 w-3.5", busy && "animate-spin")} />
            {text.refresh}
          </button>
        ) : null}
      </div>
      {items.length === 0 ? (
        <p className="m-0 text-[13px] text-faint">{contents?.cached_at ? text.contentsEmpty : text.contentsNever}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] border-collapse text-[13px]" data-contents={items.length}>
            <thead>
              <tr className="text-left text-xs text-faint">
                <th className="py-1.5 font-medium">{text.col.guest}</th>
                <th className="py-1.5 text-right font-medium">{text.col.versions}</th>
                <th className="py-1.5 font-medium">{text.col.oldest}</th>
                <th className="py-1.5 font-medium">{text.col.newest}</th>
                <th className="py-1.5 text-right font-medium">{text.col.size}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.group} className="border-0 border-t border-solid border-border">
                  <td className="py-2">
                    <span className="font-medium">{item.name ?? item.group}</span>
                    {item.name ? <span className="ml-2 rounded bg-subtle px-1.5 py-px font-mono text-[11px] text-muted">{item.group}</span> : null}
                  </td>
                  <td className="py-2 text-right font-mono">{item.versions}</td>
                  <td className="py-2 text-muted">{date(item.oldest)}</td>
                  <td className="py-2">{date(item.newest)}</td>
                  <td className="py-2 text-right font-mono text-muted">{formatBytes(item.size_bytes, language)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function History({ disk, runs, language, t }: { disk: ExternalDisk; runs: ExternalBackupRun[]; language: Language; t: TranslationDictionary }) {
  const items = diskHistory(disk, runs, 20);
  if (!items.length) return <p className="m-0 text-[13px] text-faint">{t.disksV2.noHistory}</p>;
  return (
    <div className="flex flex-col">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          onClick={() => openRunModal(item.id)}
          className="flex items-center gap-2.5 rounded-md border-0 bg-transparent px-1 py-1.5 text-left text-[13px] text-fg hover:bg-hover"
        >
          <span className={cn("h-[7px] w-[7px] flex-none rounded-full", DOT[item.tone])} />
          <span className="flex-1 text-muted">
            {(t.disksV2.historyKind as Record<string, string>)[item.kind] ?? item.kind} ·{" "}
            {(t.disksV2.historyStatus as Record<string, string>)[item.status] ?? item.status}
          </span>
          <span className="text-xs text-faint">{when(item.at, language, t)}</span>
        </button>
      ))}
    </div>
  );
}

function IdentityCard({ disk, t, onChanged }: { disk: ExternalDisk; t: TranslationDictionary; onChanged: () => Promise<void> | void }) {
  const text = t.diskSheet;
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(disk.display_name);
  const [color, setColor] = useState<DiskColor | null>(diskColor(disk.color));
  const [location, setLocation] = useState(disk.location_note ?? "");
  const [busy, setBusy] = useState(false);
  const dirty = name.trim() !== disk.display_name || color !== diskColor(disk.color) || location.trim() !== (disk.location_note ?? "");

  async function save() {
    if (!name.trim()) {
      toast({ tone: "warning", title: text.errors.disk_name_required });
      return;
    }
    setBusy(true);
    try {
      await updateDiskProfile(disk.id, { display_name: name.trim(), color, location_note: location.trim() || null });
      toast({ tone: "success", title: text.saved });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  async function upload(file: File | undefined) {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      toast({ tone: "warning", title: text.errors.photo_too_large });
      return;
    }
    setBusy(true);
    try {
      await uploadDiskPhoto(disk.id, file);
      toast({ tone: "success", title: text.photoSaved });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }
  async function removePhoto() {
    setBusy(true);
    try {
      await deleteDiskPhoto(disk.id);
      toast({ tone: "success", title: text.photoRemoved });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={card} aria-labelledby="identity-title">
      <h2 id="identity-title" className="m-0 text-[15px] font-semibold">{text.identity}</h2>
      <label className="flex flex-col gap-1.5 text-[13px] text-muted">
        {text.name}
        <input className={input} value={name} maxLength={255} onChange={(event) => setName(event.target.value)} />
      </label>
      <div className="flex flex-col gap-1.5 text-[13px] text-muted">
        <span id="color-label">{text.color}</span>
        <div role="radiogroup" aria-labelledby="color-label" className="flex gap-2">
          {DISK_COLORS.map((value) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={color === value}
              aria-label={text.colors[value]}
              title={text.colors[value]}
              onClick={() => setColor(color === value ? null : value)}
              className={cn(
                "h-7 w-7 rounded-full border-2 border-solid",
                COLOR_CLASS[value].swatch,
                color === value ? "border-fg" : "border-transparent hover:border-border-strong",
              )}
            />
          ))}
        </div>
      </div>
      <label className="flex flex-col gap-1.5 text-[13px] text-muted">
        {text.location}
        <input className={input} value={location} maxLength={500} placeholder={text.locationPlaceholder} onChange={(event) => setLocation(event.target.value)} />
      </label>
      <button type="button" className={primary} disabled={!dirty || busy} onClick={() => void save()}>
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
        {text.save}
      </button>
      <div className="flex flex-col gap-2 border-0 border-t border-solid border-border pt-3">
        <span className="text-[13px] text-muted">{text.photo}</span>
        <div className="flex items-center gap-3">
          <DiskAvatar disk={disk} t={t} />
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            className="hidden"
            aria-label={text.addPhoto}
            onChange={(event) => void upload(event.target.files?.[0])}
          />
          <button type="button" className={secondary} disabled={busy} onClick={() => fileRef.current?.click()}>
            <Camera className="h-4 w-4" />
            {disk.photo_version ? text.changePhoto : text.addPhoto}
          </button>
          {disk.photo_version ? (
            <button type="button" className={secondary} disabled={busy} onClick={() => void removePhoto()} aria-label={text.removePhoto}>
              <Trash2 className="h-4 w-4" />
            </button>
          ) : null}
        </div>
        <span className="text-xs text-faint">{text.photoHint}</span>
      </div>
    </section>
  );
}

function HealthCard({ disk, t, language }: { disk: ExternalDisk; t: TranslationDictionary; language: Language }) {
  const text = t.diskSheet;
  const status = disk.smart_status ?? null;
  const details = disk.smart_details ?? {};
  const reasons = (details.reasons ?? []).filter((reason) => reason in text.smartReason);
  const facts = [
    details.power_on_hours != null ? fill(text.powerOn, { hours: details.power_on_hours }) : null,
    details.temperature_c != null ? fill(text.temperature, { temp: details.temperature_c }) : null,
    details.reallocated ? fill(text.reallocated, { n: details.reallocated }) : null,
  ].filter(Boolean);
  const speed = disk.usb_speed_mbps;
  return (
    <section className={card} aria-labelledby="health-title">
      <h2 id="health-title" className="m-0 text-[15px] font-semibold">{text.health}</h2>
      <div className="flex flex-col gap-1.5">
        <span className={cn("w-fit rounded-full px-2.5 py-[3px] text-xs font-medium", SMART_TONE[status ?? "unknown"])} data-smart={status ?? "none"}>
          {status ? text.smart[status] : text.smart.none}
        </span>
        <span className="text-[13px] leading-[1.45] text-muted">{status ? text.smartHelp[status] : text.smartHelp.none}</span>
        {reasons.length ? (
          <ul className="m-0 list-disc pl-5 text-[13px] text-muted">
            {reasons.map((reason) => (
              <li key={reason}>{(text.smartReason as Record<string, string>)[reason]}</li>
            ))}
          </ul>
        ) : null}
        {facts.length ? <span className="text-xs text-faint">{facts.join(" · ")}</span> : null}
        {disk.smart_checked_at ? <span className="text-xs text-faint">{fill(text.checkedAt, { when: when(disk.smart_checked_at, language, t) })}</span> : null}
      </div>
      <div className="flex flex-col gap-1 border-0 border-t border-solid border-border pt-3 text-[13px]">
        <span className="text-muted">{text.link}</span>
        <span className={cn(speed && speed <= 480 ? "text-warning-text" : "text-fg")}>
          {speed ? (speed <= 480 ? text.usb2 : fill(text.usbFast, { speed })) : text.linkUnknown}
        </span>
      </div>
    </section>
  );
}

function RetentionCard({ disk, t, onChanged }: { disk: ExternalDisk; t: TranslationDictionary; onChanged: () => Promise<void> | void }) {
  const text = t.diskSheet;
  const { toast } = useToast();
  const [form, setForm] = useState<RetentionForm>(() => retentionForm(disk.effective_retention));
  const [days, setDays] = useState<string>("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    getRotationStaleDays().then((value) => setDays(String(value)), () => setDays("30"));
  }, []);
  const payload = retentionPayload(form);
  const daysValue = Number(days);
  const daysValid = /^\d+$/.test(days) && daysValue >= 1 && daysValue <= 365;

  async function save() {
    if (!payload || !daysValid) return;
    setBusy(true);
    try {
      await updateDiskProfile(disk.id, { retention: payload });
      await setRotationStaleDays(daysValue);
      toast({ tone: "success", title: text.saved });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  const field = (key: keyof RetentionForm, label: string) => (
    <label className="flex items-center gap-2 text-[13px] text-muted">
      <span className="flex-1">{label}</span>
      <input
        className={cn(input, "w-20 text-right")}
        inputMode="numeric"
        value={form[key]}
        onChange={(event) => setForm({ ...form, [key]: event.target.value })}
      />
    </label>
  );
  return (
    <section className={card} aria-labelledby="retention-title">
      <h2 id="retention-title" className="m-0 text-[15px] font-semibold">{text.retention}</h2>
      <span className="text-[13px] leading-[1.45] text-muted">{text.retentionHelp}</span>
      {field("keepLast", text.keepLast)}
      {field("keepWeekly", text.keepWeekly)}
      {field("keepMonthly", text.keepMonthly)}
      {payload ? null : <span className="text-xs text-danger-text">{text.retentionInvalid}</span>}
      <label className="flex items-center gap-2 border-0 border-t border-solid border-border pt-3 text-[13px] text-muted">
        <span className="flex-1">{text.staleDays}</span>
        <input className={cn(input, "w-20 text-right")} inputMode="numeric" value={days} onChange={(event) => setDays(event.target.value)} />
      </label>
      {daysValid || days === "" ? null : <span className="text-xs text-danger-text">{text.staleDaysInvalid}</span>}
      <button type="button" className={primary} disabled={!payload || !daysValid || busy} onClick={() => void save()}>
        {text.save}
      </button>
    </section>
  );
}

