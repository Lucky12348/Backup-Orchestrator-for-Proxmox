import { Download, Eye, RotateCcw, Square } from "lucide-react";

import { getRunJournal, repairDisk, startDiskVerify } from "../api";
import type { Language, TranslationDictionary } from "../i18n";
import { ApiError } from "../notices";
import type { MenuAction } from "../ui/ActionMenu";
import { useToast } from "../ui/toast";
import { useCommands } from "../views/commands";
import { useDiskDirectory } from "../views/disks/DiskTag";
import { runKind } from "../views/disks/diskModel";
import { canCancel, cancelConfirmOptions, cancelKind, useCancelRun } from "./CancelRun";
import { openRunModal } from "./openRun";
import { journalText, type JournalEntry } from "./runModel";

interface RunRef {
  id: number;
  disk_id: number;
  status: string;
  verify_only?: boolean | null;
  repair?: boolean | null;
  auto_eject_after_success?: boolean;
  current_step?: string | null;
  started_at: string;
  finished_at?: string | null;
  progress_percent?: number | null;
  cancellable?: boolean;
  cancel_requested?: boolean;
}

/** Saves the run journal as a text file, every time in PBO's time zone. */
export async function downloadRunJournal(runId: number, t: TranslationDictionary, language: Language): Promise<void> {
  const journal = await getRunJournal(runId);
  const lang = language === "fr" ? "fr" : "en";
  const text = journalText(journal.entries as JournalEntry[], t.runLog as Record<string, string>, journal.timezone, lang);
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `pbo-run-${runId}.log`;
  link.click();
  URL.revokeObjectURL(url);
}

/** A run's actions (dashboard "Latest runs", activity, disk history). */
export function useRunMenu(run: RunRef | undefined, t: TranslationDictionary, language: Language): MenuAction[] {
  const commands = useCommands();
  const disks = useDiskDirectory();
  const { toast } = useToast();
  const { cancel } = useCancelRun(t, () => void commands.refresh());
  if (!run) return [];
  const text = t.menu.run;
  const disk = disks.find((item) => item.id === run.disk_id);
  const kind = runKind(run as Parameters<typeof runKind>[0]);
  const active = run.status === "pending" || run.status === "running";
  const fail = (error: unknown) => {
    const code = error instanceof ApiError ? error.code : undefined;
    const tables: Record<string, string> = Object.assign({}, t.readiness, t.disksV2.errors);
    toast({ tone: "danger", title: (code && tables[code]) || (error instanceof Error ? error.message : t.error), diskId: run.disk_id });
  };
  const relaunch = async () => {
    try {
      if (!disk) return;
      if (kind === "backup") commands.backup(disk);
      else if (kind === "eject") commands.eject(disk);
      else {
        const started = kind === "repair" ? await repairDisk(disk.id) : await startDiskVerify(disk.id, false);
        openRunModal(started.run_id);
        await commands.refresh();
      }
    } catch (error) {
      fail(error);
    }
  };
  return [
    { key: "open", label: active ? text.follow : text.open, icon: Eye, onSelect: () => openRunModal(run.id) },
    // B56: cancel a running backup / verification / repair (confirmed by the menu).
    ...(canCancel(run)
      ? [{ key: "cancel", label: t.runModal.cancelKind[cancelKind(run)], icon: Square, danger: true, confirm: cancelConfirmOptions(run, t), onSelect: () => cancel(run) }]
      : []),
    {
      key: "download",
      label: text.download,
      icon: Download,
      onSelect: async () => {
        try {
          await downloadRunJournal(run.id, t, language);
        } catch (error) {
          fail(error);
        }
      },
    },
    {
      key: "relaunch",
      label: text.relaunch[kind],
      icon: RotateCcw,
      disabled: active || !disk,
      confirm: kind === "repair" ? { title: text.relaunch.repair, description: text.repairConfirm, confirmLabel: text.relaunch.repair } : undefined,
      onSelect: relaunch,
    },
  ];
}
