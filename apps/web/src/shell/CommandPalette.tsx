// B55: the ⌘K command palette (replaces the search field that did nothing):
// VM/CT, disks, runs, pages and settings, and actions (back up / eject /
// verify a disk, new channel, theme, language). Keyboard first; a magnifier
// button opens it on mobile.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BellPlus,
  CalendarDays,
  CornerDownLeft,
  HardDrive,
  HardDriveDownload,
  Languages,
  LayoutDashboard,
  Monitor,
  Moon,
  Plug,
  Bell,
  Play,
  Search,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  Box,
  History,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

import { startDiskVerify } from "../api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../components/ui/dialog";
import { cn } from "../components/ui/utils";
import type { Language, TranslationDictionary } from "../i18n";
import { openRunModal } from "../runs/openRun";
import type { ExternalBackupRun, ExternalDisk, VirtualMachine } from "../types";
import { useAppearance } from "../ui/AppearanceProvider";
import { useToast } from "../ui/toast";
import { useCommands } from "../views/commands";
import { diskActions, diskUiStatus, runKind } from "../views/disks/diskModel";
import { DiskMark } from "../views/disks/DiskTag";
import { fill, formatWhen } from "../views/format";
import { NEW_CHANNEL_HASH } from "../views/notifications/NotificationsView";
import { SECTIONS } from "../views/settings/settingsModel";
import {
  OPEN_PALETTE_EVENT,
  flatten,
  moveSelection,
  openCommandPalette,
  searchPalette,
  shortcutLabel,
  type PaletteEntry,
  type PaletteGroup,
} from "./paletteModel";

// A key name, the same in every language.
const ESC_KEY = "Esc";

const PAGE_ICONS: Record<string, typeof Search> = {
  "/": LayoutDashboard,
  "/assets": Server,
  "/disks": HardDrive,
  "/planning": CalendarDays,
  "/activity": Activity,
  "/integrations": Plug,
  "/notifications": Bell,
  "/settings": SlidersHorizontal,
};

function iconOf(entry: PaletteEntry): typeof Search {
  const target = entry.target;
  switch (target.kind) {
    case "navigate":
      if (entry.group === "guests") return Box;
      if (entry.group === "disks") return HardDrive;
      if (entry.id === "action:new-channel") return BellPlus;
      return PAGE_ICONS[target.to.split(/[?#]/)[0]] ?? SlidersHorizontal;
    case "backup":
      return Play;
    case "eject":
      return HardDriveDownload;
    case "verify":
      return ShieldCheck;
    case "run":
      return History;
    case "theme":
      return target.value === "dark" ? Moon : target.value === "light" ? Sun : Monitor;
    case "language":
      return Languages;
  }
}

/** The header field (desktop) that opens the palette, shortcut shown. */
export function PaletteButton({ t }: { t: TranslationDictionary }) {
  const shortcut = shortcutLabel(typeof navigator === "undefined" ? "" : navigator.platform || navigator.userAgent);
  return (
    <>
      <button
        type="button"
        onClick={openCommandPalette}
        className="hidden h-[34px] w-60 items-center gap-2 rounded-lg border border-solid border-border bg-subtle pl-2.5 pr-2 text-left text-[13px] text-faint hover:border-border-strong md:flex"
        aria-keyshortcuts="Meta+K Control+K"
        data-palette-open
      >
        <Search className="h-3.5 w-3.5" aria-hidden />
        <span className="min-w-0 flex-1 truncate">{t.shell.search}</span>
        <kbd className="rounded border border-solid border-border-strong px-[5px] font-mono text-[11px]">{shortcut}</kbd>
      </button>
      <button
        type="button"
        onClick={openCommandPalette}
        aria-label={t.shell.search}
        title={t.shell.search}
        className="flex h-[34px] w-[34px] items-center justify-center rounded-lg border border-solid border-border bg-transparent text-muted hover:bg-subtle md:hidden"
      >
        <Search className="h-4 w-4" aria-hidden />
      </button>
    </>
  );
}

export function CommandPalette({
  t,
  language,
  onLanguageChange,
  disks,
  vms,
  runs,
}: {
  t: TranslationDictionary;
  language: Language;
  onLanguageChange: (language: Language) => void;
  disks: ExternalDisk[];
  vms: VirtualMachine[];
  runs: ExternalBackupRun[];
}) {
  const text = t.shell.palette;
  const navigate = useNavigate();
  const commands = useCommands();
  const { toast } = useToast();
  const { update } = useAppearance();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const list = useRef<HTMLDivElement | null>(null);
  const now = new Date();

  useEffect(() => {
    const show = () => {
      setQuery("");
      setSelected(0);
      setOpen(true);
    };
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => {
          if (!current) show();
          return !current;
        });
      }
    };
    window.addEventListener(OPEN_PALETTE_EVENT, show);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener(OPEN_PALETTE_EVENT, show);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const entries = useMemo<PaletteEntry[]>(() => {
    const result: PaletteEntry[] = [];
    const pages: [string, string][] = [
      ["/", t.nav.dashboard],
      ["/assets", t.shell.resources],
      ["/disks", t.nav.disks],
      ["/planning", t.nav.planning],
      ["/activity", t.nav.activity],
      ["/integrations", t.nav.integrations],
      ["/notifications", t.nav.notifications],
      ["/settings", t.nav.settings],
    ];
    for (const [to, label] of pages) result.push({ id: `page:${to}`, group: "pages", label, target: { kind: "navigate", to }, suggested: true });

    result.push({ id: "action:new-channel", group: "actions", label: text.newChannel, keywords: text.newChannelKeywords, target: { kind: "navigate", to: `/notifications${NEW_CHANNEL_HASH}` }, suggested: true });
    for (const value of ["system", "light", "dark"] as const) {
      result.push({ id: `action:theme:${value}`, group: "actions", label: fill(text.theme, { theme: t.settingsV2.themes[value] }), keywords: text.themeKeywords, target: { kind: "theme", value } });
    }
    const otherLanguage = language === "fr" ? "en" : "fr";
    result.push({
      id: "action:language",
      group: "actions",
      label: fill(text.language, { language: t.settingsV2.languages[otherLanguage] }),
      keywords: "language langue english français",
      target: { kind: "language", value: otherLanguage },
    });

    const lang = language === "fr" ? "fr" : "en";
    for (const disk of disks) {
      const status = diskUiStatus(disk, runs);
      const actions = diskActions(disk, status);
      const name = disk.display_name || disk.serial_number;
      const keywords = [disk.serial_number, disk.model_name, disk.pbs_datastore_name].filter(Boolean).join(" ");
      result.push({ id: `disk:${disk.id}`, group: "disks", label: name, hint: [disk.model_name, disk.serial_number].filter(Boolean).join(" · "), keywords, target: { kind: "navigate", to: `/disks/${disk.id}` }, diskId: disk.id });
      if (actions.includes("backup")) {
        result.push({ id: `action:backup:${disk.id}`, group: "actions", label: fill(text.backup, { disk: name }), keywords, target: { kind: "backup", diskId: disk.id }, diskId: disk.id, suggested: true });
      }
      if (actions.includes("eject") && status !== "running") {
        result.push({ id: `action:eject:${disk.id}`, group: "actions", label: fill(text.eject, { disk: name }), keywords, target: { kind: "eject", diskId: disk.id }, diskId: disk.id });
      }
      if (status === "approved" && disk.prepared_as_pbs_datastore) {
        result.push({ id: `action:verify:${disk.id}`, group: "actions", label: fill(text.verify, { disk: name }), keywords, target: { kind: "verify", diskId: disk.id }, diskId: disk.id });
      }
    }
    for (const vm of vms) {
      const kind = vm.vm_type === "ct" ? "CT" : "VM";
      result.push({
        id: `guest:${vm.id}`,
        group: "guests",
        label: vm.name,
        hint: [vm.external_id ? `${kind} ${vm.external_id}` : kind, vm.node_name].filter(Boolean).join(" · "),
        keywords: [vm.external_id, kind, vm.node_name].filter(Boolean).join(" "),
        target: { kind: "navigate", to: `/assets?q=${encodeURIComponent(vm.name)}` },
      });
    }
    const diskName = (id: number) => disks.find((disk) => disk.id === id)?.display_name ?? `#${id}`;
    for (const run of [...runs].sort((a, b) => b.id - a.id).slice(0, 60)) {
      const kind = runKind(run as Parameters<typeof runKind>[0]);
      result.push({
        id: `run:${run.id}`,
        group: "runs",
        label: fill(text.run[kind], { disk: diskName(run.disk_id) }),
        hint: [`#${run.id}`, (t.disksV2.historyStatus as Record<string, string>)[run.current_step === "cancelled" && run.status === "failed" ? "cancelled" : run.status] ?? run.status, formatWhen(run.finished_at ?? run.started_at, language, now, t.dashboardV2)].join(" · "),
        keywords: `${run.id} ${diskName(run.disk_id)} ${lang === "fr" ? "exécution" : "run"}`,
        target: { kind: "run", runId: run.id },
        diskId: run.disk_id,
      });
    }
    for (const key of SECTIONS) {
      result.push({
        id: `settings:${key}`,
        group: "settings",
        label: `${t.settingsV2.title} › ${t.settingsV2.sections[key]}`,
        keywords: text.settingsKeywords[key],
        target: { kind: "navigate", to: `/settings#${key}` },
      });
    }
    return result;
    // `now` only formats run hints; recomputing on every render is not needed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disks, vms, runs, language, t]);

  const sections = useMemo(() => searchPalette(entries, query), [entries, query]);
  const flat = flatten(sections);
  const active = flat[Math.min(selected, flat.length - 1)];

  useEffect(() => setSelected(0), [query]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-palette-index="${selected}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  async function run(entry: PaletteEntry) {
    setOpen(false);
    const target = entry.target;
    const disk = "diskId" in target ? disks.find((item) => item.id === target.diskId) : undefined;
    try {
      switch (target.kind) {
        case "navigate":
          navigate(target.to);
          break;
        case "backup":
          if (disk) commands.backup(disk);
          break;
        case "eject":
          if (disk) commands.eject(disk);
          break;
        case "verify": {
          const started = await startDiskVerify(target.diskId, false);
          openRunModal(started.run_id);
          commands.refresh();
          break;
        }
        case "run":
          openRunModal(target.runId);
          break;
        case "theme":
          await update({ theme: target.value });
          break;
        case "language":
          onLanguageChange(target.value);
          break;
      }
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error, inbox: false });
    }
  }

  let index = -1;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {open ? (
        <DialogContent
          className="top-[12vh] max-w-[640px] translate-y-0 p-0"
          aria-describedby="palette-help"
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            (event.currentTarget as HTMLElement | null)?.querySelector<HTMLInputElement>("input")?.focus();
          }}
          data-palette
        >
          <DialogTitle className="sr-only">{text.title}</DialogTitle>
          <DialogDescription id="palette-help" className="sr-only">
            {text.help}
          </DialogDescription>
          <div className="flex items-center gap-2.5 border-0 border-b border-solid border-border px-4">
            <Search className="h-4 w-4 flex-none text-faint" aria-hidden />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                // Home / End stay the text field's (cursor); ↑ ↓ move the selection.
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  event.preventDefault();
                  setSelected(moveSelection(selected, flat.length, event.key));
                } else if (event.key === "Enter" && active) {
                  event.preventDefault();
                  void run(active);
                }
              }}
              placeholder={text.placeholder}
              aria-label={text.placeholder}
              role="combobox"
              aria-expanded
              aria-controls="palette-results"
              aria-activedescendant={active ? `palette-${active.id}` : undefined}
              aria-autocomplete="list"
              className="h-12 min-w-0 flex-1 border-0 bg-transparent text-[15px] text-fg outline-none placeholder:text-faint focus-visible:outline-none"
              data-palette-input
            />
            <kbd className="flex-none rounded border border-solid border-border-strong px-1.5 font-mono text-[11px] text-faint">{ESC_KEY}</kbd>
          </div>
          <div ref={list} id="palette-results" role="listbox" aria-label={text.results} className="max-h-[min(440px,60vh)] overflow-y-auto py-1.5">
            {sections.length === 0 ? (
              <p className="m-0 px-4 py-8 text-center text-[13px] text-faint">{fill(text.noResult, { query })}</p>
            ) : (
              sections.map((section) => (
                <div key={section.group} role="group" aria-label={text.groups[section.group as PaletteGroup]} className="pb-1">
                  <div className="px-4 pb-1 pt-2 text-[11px] font-medium uppercase tracking-[0.04em] text-faint" aria-hidden>
                    {text.groups[section.group as PaletteGroup]}
                  </div>
                  {section.entries.map((entry) => {
                    index += 1;
                    const position = index;
                    const Icon = iconOf(entry);
                    const isActive = active?.id === entry.id;
                    return (
                      <div
                        key={entry.id}
                        id={`palette-${entry.id}`}
                        role="option"
                        aria-selected={isActive}
                        data-palette-index={position}
                        data-palette-entry={entry.id}
                        onMouseMove={() => (selected !== position ? setSelected(position) : undefined)}
                        onClick={() => void run(entry)}
                        className={cn("mx-1.5 flex cursor-pointer items-center gap-3 rounded-md px-2.5 py-2", isActive ? "bg-hover" : "")}
                      >
                        {entry.diskId !== undefined && entry.group !== "actions" ? (
                          <DiskMark id={entry.diskId} t={t} />
                        ) : (
                          <Icon className="h-4 w-4 flex-none text-muted" aria-hidden />
                        )}
                        <span className="flex min-w-0 flex-1 flex-col">
                          <span className="truncate text-[13px] text-fg">{entry.label}</span>
                          {entry.hint ? <span className="truncate text-xs text-faint">{entry.hint}</span> : null}
                        </span>
                        {isActive ? <CornerDownLeft className="h-3.5 w-3.5 flex-none text-faint" aria-hidden /> : null}
                      </div>
                    );
                  })}
                </div>
              ))
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-0 border-t border-solid border-border px-4 py-2 text-[11px] text-faint" aria-hidden>
            <span>
              <kbd className="font-mono">↑↓</kbd> {text.keys.move}
            </span>
            <span>
              <kbd className="font-mono">↵</kbd> {text.keys.open}
            </span>
            <span>
              <kbd className="font-mono">{ESC_KEY}</kbd> {text.keys.close}
            </span>
          </div>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
