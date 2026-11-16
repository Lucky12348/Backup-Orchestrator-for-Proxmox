import { AlertTriangle, HardDrive, MemoryStick, Microchip, Server, ShieldAlert, Usb } from "lucide-react";
import type { ComponentType } from "react";

import { diskPhotoUrl, type DiskAlert } from "../../api";
import { cn } from "../../components/ui/utils";
import type { TranslationDictionary } from "../../i18n";
import type { ExternalDisk } from "../../types";
import { fill } from "../format";
import { COLOR_CLASS, diskColor, diskKind, type DiskKind } from "./diskProfile";

const KIND_ICON: Record<DiskKind, ComponentType<{ className?: string }>> = {
  hdd_portable: HardDrive,
  hdd_desktop: Server,
  ssd: MemoryStick,
  usb_stick: Usb,
  nvme_enclosure: Microchip,
  unknown: HardDrive,
};

/** Photo of the disk when there is one, else the icon of its kind, in its colour. */
export function DiskAvatar({
  disk,
  t,
  size = "md",
  toneClass,
}: {
  disk: ExternalDisk;
  t: TranslationDictionary;
  size?: "md" | "lg";
  toneClass?: string;
}) {
  const photo = diskPhotoUrl(disk);
  const kind = diskKind(disk.kind);
  const Icon = KIND_ICON[kind];
  const color = diskColor(disk.color);
  const box = size === "lg" ? "h-[88px] w-[88px] rounded-[14px]" : "h-[38px] w-[38px] rounded-[10px]";
  if (photo) {
    return (
      <img
        src={photo}
        alt={fill(t.diskSheet.photoAlt, { name: disk.display_name })}
        className={cn("flex-none object-cover", box, color ? `ring-2 ${COLOR_CLASS[color].ring}` : "")}
        data-photo={disk.photo_version}
      />
    );
  }
  return (
    <div
      className={cn("flex flex-none items-center justify-center", box, color ? COLOR_CLASS[color].soft : toneClass ?? "bg-subtle text-muted")}
      title={t.diskSheet.kind[kind]}
      data-kind={kind}
    >
      <Icon className={size === "lg" ? "h-9 w-9" : "h-[18px] w-[18px]"} />
    </div>
  );
}

/** Short guard badges of a card (usage 80/90 %, SMART, removal without eject, USB 2.0). */
export function AlertBadges({ alerts, t }: { alerts: DiskAlert[]; t: TranslationDictionary }) {
  if (!alerts.length) return null;
  const badges = t.guards.badge as Record<string, string>;
  return (
    <div className="flex flex-wrap gap-1.5" data-alerts={alerts.map((alert) => alert.code).join(",")}>
      {alerts.map((alert) => {
        const Icon = alert.code.startsWith("smart") ? ShieldAlert : AlertTriangle;
        const params = Object.fromEntries(
          Object.entries(alert.params).map(([key, value]) => [key, typeof value === "number" ? Math.round(value) : String(value ?? "")]),
        );
        return (
          <span
            key={alert.code}
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium",
              alert.level === "danger" ? "bg-danger/15 text-danger-text" : "bg-warning/15 text-warning-text",
            )}
          >
            <Icon className="h-3 w-3" />
            {fill(badges[alert.code] ?? alert.code, params)}
          </span>
        );
      })}
    </div>
  );
}
