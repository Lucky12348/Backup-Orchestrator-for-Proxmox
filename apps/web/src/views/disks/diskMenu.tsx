import { Copy, ExternalLink, EyeOff, Palette, Play, RefreshCw, ShieldCheck, Unplug } from "lucide-react";
import { useInRouterContext, useNavigate } from "react-router-dom";

import { decideDisk, reactivateDisk, startDiskVerify } from "../../api";
import type { TranslationDictionary } from "../../i18n";
import { ApiError } from "../../notices";
import { openRunModal } from "../../runs/openRun";
import type { ExternalDisk } from "../../types";
import type { MenuAction } from "../../ui/ActionMenu";
import { useToast } from "../../ui/toast";
import { useCommands } from "../commands";
import { fill } from "../format";
import { diskActions, diskUiStatus, type RunLike } from "./diskModel";

/** Which entries a disk's menu offers, from its state (pure, node:test). */
export function diskMenuKeys(disk: ExternalDisk, runs: readonly RunLike[] = []): { key: string; disabled: boolean }[] {
  const status = diskUiStatus(disk, runs);
  const panel = diskActions(disk, status);
  const ready = status === "approved";
  const keys: { key: string; disabled: boolean }[] = [
    { key: "backup", disabled: !ready },
    { key: "verify", disabled: !(ready && disk.prepared_as_pbs_datastore && disk.pbs_datastore_name) },
  ];
  if (panel.includes("reactivate")) keys.push({ key: "reactivate", disabled: false });
  else if (panel.includes("eject")) keys.push({ key: "eject", disabled: status === "running" });
  keys.push({ key: "open", disabled: false }, { key: "rename", disabled: false }, { key: "copySerial", disabled: false });
  if (status !== "hidden") keys.push({ key: "hide", disabled: status === "running" });
  return keys;
}

/** The actions of a disk, the same in its card, the dashboard and its sheet. */
export function useDiskMenu(disk: ExternalDisk | undefined, t: TranslationDictionary, runs: readonly RunLike[] = []): MenuAction[] {
  // Rendered outside a router (component tests): plain navigation. The condition never changes for a mounted tree.
  const navigate = useInRouterContext() ? useNavigate() : (to: string) => window.location.assign(to); // eslint-disable-line react-hooks/rules-of-hooks
  const commands = useCommands();
  const { toast } = useToast();
  if (!disk) return [];
  const text = t.menu.disk;
  const fail = (error: unknown) => {
    const errors: Record<string, string> = Object.assign({}, t.disksV2.errors, t.readiness);
    const code = error instanceof ApiError ? error.code : undefined;
    toast({ tone: "danger", title: (code && errors[code]) || (error instanceof Error ? error.message : t.error), diskId: disk.id });
  };
  const run = async (task: () => Promise<unknown>) => {
    try {
      await task();
    } catch (error) {
      fail(error);
    }
  };
  const build: Record<string, Omit<MenuAction, "key" | "disabled">> = {
    backup: { label: text.backup, icon: Play, onSelect: () => commands.backup(disk) },
    verify: {
      label: text.verify,
      icon: ShieldCheck,
      onSelect: () =>
        run(async () => {
          const result = await startDiskVerify(disk.id, false);
          openRunModal(result.run_id);
          await commands.refresh();
        }),
    },
    eject: { label: text.eject, icon: Unplug, onSelect: () => commands.eject(disk) },
    reactivate: {
      label: t.disksV2.action.reactivate,
      icon: RefreshCw,
      onSelect: () =>
        run(async () => {
          await reactivateDisk(disk.id);
          toast({ tone: "success", title: fill(t.disksV2.action.reactivated, { name: disk.display_name }), diskId: disk.id });
          await commands.refresh();
        }),
    },
    open: { label: text.open, icon: ExternalLink, onSelect: () => navigate(`/disks/${disk.id}`) },
    rename: { label: text.rename, icon: Palette, onSelect: () => navigate(`/disks/${disk.id}#identity`) },
    copySerial: {
      label: text.copySerial,
      icon: Copy,
      onSelect: () =>
        run(async () => {
          await navigator.clipboard.writeText(disk.serial_number);
          toast({ tone: "success", title: fill(text.serialCopied, { serial: disk.serial_number }), diskId: disk.id });
        }),
    },
    hide: {
      label: t.disksV2.action.hide,
      icon: EyeOff,
      danger: true,
      confirm: { title: fill(text.hideTitle, { name: disk.display_name }), description: text.hideText, confirmLabel: t.disksV2.action.hide },
      onSelect: () =>
        run(async () => {
          await decideDisk(disk.id, "hide");
          toast({ tone: "success", title: fill(t.disksV2.approval.hiddenToast, { name: disk.display_name }), diskId: disk.id });
          await commands.refresh();
        }),
    },
  };
  return diskMenuKeys(disk, runs).map(({ key, disabled }) => ({
    key,
    disabled,
    ...build[key],
    // Why it is greyed out (Phase 5h.1): ejected at the end of the run, or after it.
    hint: key === "eject" && disabled ? text.ejectRunningHint : undefined,
  }));
}
