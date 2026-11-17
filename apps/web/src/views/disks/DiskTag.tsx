import { createContext, useContext, type ReactNode } from "react";

import { cn } from "../../components/ui/utils";
import type { TranslationDictionary } from "../../i18n";
import type { ExternalDisk } from "../../types";
import { DiskAvatar } from "./DiskBits";
import { COLOR_CLASS, diskColor, diskSecondary, findDisk } from "./diskProfile";

/**
 * Phase 5h: every disk shown anywhere (toasts, run modal, dashboard, activity,
 * agenda, resources…) carries its identity — name, colour, photo or icon. The
 * shell provides the disks once; any view finds a disk by id or serial.
 */
const DiskDirectoryContext = createContext<readonly ExternalDisk[]>([]);

export function DiskDirectoryProvider({ disks, children }: { disks: readonly ExternalDisk[]; children: ReactNode }) {
  return <DiskDirectoryContext.Provider value={disks}>{children}</DiskDirectoryContext.Provider>;
}

export function useDiskDirectory(): readonly ExternalDisk[] {
  return useContext(DiskDirectoryContext);
}

export function useDisk(ref: { id?: number | null; serial?: string | null }): ExternalDisk | undefined {
  return findDisk(useDiskDirectory(), ref);
}

/** Avatar + name (+ model · serial in small when `secondary`). Unknown disk: the fallback text. */
export function DiskTag({
  id,
  serial,
  fallback,
  t,
  size = "xs",
  secondary = false,
  className,
  nameClassName,
  color: fallbackColor,
}: {
  id?: number | null;
  serial?: string | null;
  fallback?: string | null;
  /** Colour to show for a disk missing from the directory (e.g. a copy's). */
  color?: string | null;
  t: TranslationDictionary;
  size?: "xs" | "sm" | "md";
  secondary?: boolean;
  className?: string;
  nameClassName?: string;
}) {
  const disk = useDisk({ id, serial });
  if (!disk) {
    const swatch = diskColor(fallbackColor);
    return (
      <span className={cn("inline-flex min-w-0 items-center gap-1.5", className)}>
        {swatch ? <span className={cn("h-2 w-2 flex-none rounded-full", COLOR_CLASS[swatch].swatch)} /> : null}
        <span className={cn("min-w-0 truncate", nameClassName)}>{fallback ?? serial ?? (id != null ? `#${id}` : "")}</span>
      </span>
    );
  }
  const second = secondary ? diskSecondary(disk) : "";
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1.5 align-middle", className)} data-disk-tag={disk.id}>
      <DiskAvatar disk={disk} t={t} size={size} />
      <span className="flex min-w-0 flex-col leading-tight">
        <span className={cn("truncate", nameClassName)}>{disk.display_name}</span>
        {second ? <span className="truncate text-[11px] font-normal text-faint">{second}</span> : null}
      </span>
    </span>
  );
}

/** Compact places (agenda chips): the disk's colour as a dot, nothing when it has none. */
export function DiskDot({ id, serial, className }: { id?: number | null; serial?: string | null; className?: string }) {
  const disk = useDisk({ id, serial });
  const color = diskColor(disk?.color);
  if (!color) return null;
  return <span className={cn("inline-block h-2 w-2 flex-none rounded-full align-middle", COLOR_CLASS[color].swatch, className)} data-disk-dot={disk?.id} />;
}

/** The avatar alone, found by id or serial (nothing for an unknown disk). */
export function DiskMark({ id, serial, t, size = "xs" }: { id?: number | null; serial?: string | null; t: TranslationDictionary; size?: "xs" | "sm" | "md" }) {
  const disk = useDisk({ id, serial });
  return disk ? <DiskAvatar disk={disk} t={t} size={size} /> : null;
}

/** "Backup running on {disk}" with the disk's tag in place of {disk}. */
export function withDisk(template: string, disk: ReactNode): ReactNode {
  const [before, ...rest] = template.split("{disk}");
  if (!rest.length) return template;
  return (
    <>
      {before}
      {disk}
      {rest.join("{disk}")}
    </>
  );
}
