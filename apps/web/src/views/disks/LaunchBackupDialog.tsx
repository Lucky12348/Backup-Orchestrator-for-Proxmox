import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Play } from "lucide-react";

import {
  getExternalBackupPreview,
  getExternalBackupReadiness,
  type BackupLaunchOptions,
  type ExternalBackupReadiness,
} from "../../api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../../components/ui/dialog";
import { cn } from "../../components/ui/utils";
import { useAppearance } from "../../ui/AppearanceProvider";
import { CloseButton } from "../../ui/CloseButton";
import type { Language, TranslationDictionary } from "../../i18n";
import { reasonText } from "../../readiness";
import type { ExternalBackupPreview, ExternalDisk } from "../../types";
import { formatBytes } from "../../units";
import { fill } from "../format";
import { diskSecondary, estimateKind, type GuestCommand } from "./diskProfile";
import { DiskAvatar } from "./DiskBits";
import { launchState } from "./diskProfile";

export interface LaunchChoice extends BackupLaunchOptions {
  autoEject: boolean;
}

/**
 * Start a backup (Phase 5f): what will happen, the estimated copy vs free
 * space, warnings (USB 2.0, SMART to watch, removal without eject), the
 * explicit confirmation a failing SMART needs, and the options (eject at the
 * end, verify the copy). Readiness is decided server-side.
 */
export function LaunchBackupDialog({
  disk,
  onClose,
  onStart,
  language,
  t,
  initialReadiness,
  initialPreview,
}: {
  disk: ExternalDisk | null;
  onClose: () => void;
  onStart: (disk: ExternalDisk, choice: LaunchChoice) => void;
  language: Language;
  t: TranslationDictionary;
  initialReadiness?: ExternalBackupReadiness;
  initialPreview?: ExternalBackupPreview;
}) {
  const text = t.launch;
  const [readiness, setReadiness] = useState<ExternalBackupReadiness | null>(initialReadiness ?? null);
  const [preview, setPreview] = useState<ExternalBackupPreview | null>(initialPreview ?? null);
  // Phase 5j: Settings › Backups › defaults.
  const { prefs } = useAppearance();
  const [autoEject, setAutoEject] = useState(prefs.defaultEject);
  const [verify, setVerify] = useState(prefs.defaultVerify);
  const [smartOverride, setSmartOverride] = useState(false);

  useEffect(() => {
    if (!disk || initialReadiness) return;
    let cancelled = false;
    setReadiness(null);
    setPreview(null);
    setAutoEject(prefs.defaultEject);
    setVerify(prefs.defaultVerify);
    setSmartOverride(false);
    getExternalBackupReadiness(disk.id).then(
      (result) => !cancelled && setReadiness(result),
      () => !cancelled && setReadiness({ disk_id: disk.id, ready: false, pbs_mode: "", reasons: [] }),
    );
    getExternalBackupPreview(disk.id).then(
      (result) => !cancelled && setPreview(result),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disk, initialReadiness]);

  if (!disk) return null;
  const state = launchState(readiness, smartOverride);
  const reasons = t.readiness as Record<string, string>;
  const estimate = readiness?.estimate ?? null;
  const warnings = (readiness?.warnings ?? []).filter((warning) => warning.code in text.warning);

  return (
    <Dialog open onOpenChange={(value) => !value && onClose()}>
      {/* The focus goes to the dialog itself, not to the close button (it showed a stray ring on open). */}
      <DialogContent className="max-w-[520px]" aria-describedby="launch-text" onOpenAutoFocus={(event) => event.preventDefault()}>
        <div className="flex items-center gap-3 border-0 border-b border-solid border-border px-5 py-4">
          <DiskAvatar disk={disk} t={t} />
          <div className="flex min-w-0 flex-1 flex-col">
            <DialogTitle className="m-0 text-[17px] font-semibold">{text.title}</DialogTitle>
            <span className="truncate text-[14px] font-medium text-fg" data-launch-disk={disk.id}>{disk.display_name}</span>
            {diskSecondary(disk) ? <span className="truncate text-xs text-faint">{diskSecondary(disk)}</span> : null}
          </div>
          <CloseButton label={t.shell.close} onClick={onClose} />
        </div>
        <div className="flex flex-col gap-3 px-5 py-4 text-[13px] leading-[1.45]">
          <DialogDescription id="launch-text" className="m-0 text-sm text-muted">
            {disk.prepared_as_pbs_datastore ? text.reuse : text.firstUse}
          </DialogDescription>
          {preview ? <span className="font-mono text-xs text-faint">{fill(text.target, { path: preview.target_path })}</span> : null}

          {readiness === null ? (
            <span className="flex items-center gap-2 text-faint">
              <Loader2 className="h-4 w-4 animate-spin" />
              {reasons.checking}
            </span>
          ) : null}

          {estimate ? (
            <div
              className={cn("rounded-md p-3", estimate.sufficient === false ? "bg-danger/15 text-danger-text" : "bg-subtle text-fg")}
              data-estimate={estimateKind(estimate)}
            >
              {estimateKind(estimate) === "impossible"
                ? text.estimateImpossible
                : estimateKind(estimate) === "nothing"
                  ? text.nothingNew
                  : estimate.free_bytes != null
                    ? fill(estimate.known ? text.estimate : text.estimateUpper, {
                        needed: formatBytes(estimate.needed_bytes, language),
                        free: formatBytes(estimate.free_bytes, language),
                        count: estimate.new_snapshots,
                      })
                    : fill(text.estimateNoFree, { needed: formatBytes(estimate.needed_bytes, language), count: estimate.new_snapshots })}
            </div>
          ) : null}

          {warnings.map((warning) => (
            <div key={warning.code} className="flex gap-2 rounded-md bg-warning/15 p-3 text-warning-text" data-warning={warning.code}>
              <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
              <span className="flex min-w-0 flex-col gap-1">
                <span>{fill((text.warning as Record<string, string>)[warning.code], { vmid: String(warning.vmid ?? "") })}</span>
                {warning.code === "pbs_guest_agent_missing" && Array.isArray(warning.commands)
                  ? (warning.commands as GuestCommand[]).map((item, index, all) => (
                      <span key={item.command} className="flex flex-col">
                        {index === 0 || all[index - 1].where !== item.where ? (
                          <span className="text-xs opacity-80">{item.where === "pbs" ? text.guestAgentInPbs : text.guestAgentOnHost}</span>
                        ) : null}
                        <code className="break-all font-mono text-xs">{item.command}</code>
                      </span>
                    ))
                  : null}
              </span>
            </div>
          ))}

          {state.blocking.length ? (
            <ul className="m-0 flex list-none flex-col gap-1 rounded-md bg-danger/15 p-3 text-danger-text" data-blocking={state.blocking.map((r) => r.code).join(",")}>
              {state.blocking.map((reason) => (
                <li key={reason.code}>{reasonText(reason as { code: string; message: string }, reasons, language)}</li>
              ))}
            </ul>
          ) : null}

          {state.smartFailed ? (
            <label className="flex items-start gap-2.5 rounded-md border border-solid border-danger/40 p-3 text-danger-text">
              <input type="checkbox" className="m-0 mt-0.5" checked={smartOverride} onChange={(event) => setSmartOverride(event.target.checked)} />
              <span>{text.smartConfirm}</span>
            </label>
          ) : null}

          <div className="flex flex-col gap-2 border-0 border-t border-solid border-border pt-3">
            <label className="flex items-center gap-2.5 text-fg">
              <input type="checkbox" className="m-0" checked={autoEject} onChange={(event) => setAutoEject(event.target.checked)} />
              {text.autoEject}
            </label>
            <label className="flex items-start gap-2.5 text-fg">
              <input type="checkbox" className="m-0 mt-0.5" checked={verify} onChange={(event) => setVerify(event.target.checked)} />
              <span className="flex flex-col">
                {text.verify}
                <span className="text-xs text-faint">{text.verifyHelp}</span>
              </span>
            </label>
          </div>
        </div>
        <div className="flex justify-end gap-2 border-0 border-t border-solid border-border px-5 py-3.5">
          <button type="button" onClick={onClose} className="rounded-lg border border-solid border-border bg-subtle px-3.5 py-2 text-sm font-medium text-fg hover:brightness-110">
            {t.confirmDialogs.cancel}
          </button>
          <button
            type="button"
            disabled={!state.canStart}
            onClick={() => onStart(disk, { autoEject, verifyAfterBackup: verify, smartOverride: state.smartFailed && smartOverride })}
            className="flex items-center gap-2 rounded-lg border-0 bg-accent px-3.5 py-2 text-sm font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Play className="h-4 w-4" />
            {text.start}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
