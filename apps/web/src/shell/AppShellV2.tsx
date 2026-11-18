import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  Bell,
  CalendarDays,
  ChevronsUpDown,
  HardDrive,
  LayoutDashboard,
  LogOut,
  Menu,
  Monitor,
  Moon,
  Plug,
  Server,
  SlidersHorizontal,
  Sun,
  X,
} from "lucide-react";

import { useAuth } from "../AuthContext";
import { cn } from "../components/ui/utils";
import type { Language, TranslationDictionary } from "../i18n";
import { diskPageUrl, toastForDiskEvent } from "../liveDisks";
import { LiveRunModal } from "../runs/LiveRunModal";
import { RunMonitorProvider } from "../runs/RunMonitor";
import { RunPill } from "../runs/RunPill";
import type { ExternalDisk, VirtualMachine } from "../types";
import { ConfirmProvider } from "../ui/confirm";
import { Popover } from "../ui/Popover";
import { Logo, LogoMark } from "../ui/Logo";
import { PortalContainerContext } from "../ui/portal";
import { StatusDot } from "../ui/StatusDot";
import { homeRedirect } from "../ui/appearance";
import { AppearanceProvider, useAppearance } from "../ui/AppearanceProvider";
import { nextThemePreference } from "../setup/logic";
import { ToastProvider, useToast } from "../ui/toast";
import { DiskAvatar } from "../views/disks/DiskBits";
import { DiskDirectoryProvider } from "../views/disks/DiskTag";
import { useEvents } from "../useEvents";
import { newDiskCount } from "./shellModel";
import { InboxButton, recordAlertToast } from "./Inbox";
import { PaletteButton } from "./CommandPalette";

type NavKey = keyof TranslationDictionary["nav"];

const OVERVIEW: { key: NavKey | "resources"; to: string; icon: typeof LayoutDashboard }[] = [
  { key: "dashboard", to: "/", icon: LayoutDashboard },
  { key: "resources", to: "/assets", icon: Server },
  { key: "disks", to: "/disks", icon: HardDrive },
  { key: "planning", to: "/planning", icon: CalendarDays },
  { key: "activity", to: "/activity", icon: Activity },
];
// Pages that use the whole width (the calendar); the others stop at 2200 px.
const FULL_WIDTH = ["/planning"];
const CONFIGURATION: { key: NavKey; to: string; icon: typeof LayoutDashboard }[] = [
  { key: "integrations", to: "/integrations", icon: Plug },
  { key: "notifications", to: "/notifications", icon: Bell },
  { key: "settings", to: "/settings", icon: SlidersHorizontal },
];

interface AppShellV2Props {
  children: ReactNode;
  language: Language;
  onLanguageChange: (language: Language) => void;
  t: TranslationDictionary;
  disks: ExternalDisk[];
  vms: VirtualMachine[];
}

/** A page not redesigned yet: its own styles.css, without the new UI reset. */
export function LegacyPage({ children }: { children: ReactNode }) {
  return <div className="pbo-legacy">{children}</div>;
}

/**
 * Common layout (docs/design/mockups/dashboard.html): sidebar, top bar
 * (breadcrumb, ⌘K search, running-task pill, theme, bell), user block with
 * the language selector. Legacy pages render inside `<LegacyPage>`
 * (`.pbo-legacy`) until their own redesign; redesigned ones (src/views) do not.
 */
export function AppShellV2(props: AppShellV2Props) {
  return (
    <AppearanceProvider>
      <ShellRoot {...props} />
    </AppearanceProvider>
  );
}

const HOME_REDIRECT_KEY = "pbo:home-redirected";

function ShellRoot({ children, language, onLanguageChange, t, disks, vms }: AppShellV2Props) {
  // Phase 5j: theme, accent, density, sidebar, motion = the account's settings.
  const { prefs, attributes, loaded, update } = useAppearance();
  const preference = prefs.theme;
  const setPreference = (theme: typeof preference) => void update({ theme });
  const cycle = () => setPreference(nextThemePreference(preference));
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    if (!loaded) return;
    let done = false;
    try {
      done = window.sessionStorage.getItem(HOME_REDIRECT_KEY) === "1";
      window.sessionStorage.setItem(HOME_REDIRECT_KEY, "1");
    } catch {
      /* private mode */
    }
    const target = homeRedirect(location.pathname, prefs, done);
    if (target) navigate(target, { replace: true });
    // Only on the first page of the session, once the settings are known.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded]);
  const [portal, setPortal] = useState<HTMLElement | null>(null);
  const diskNames = Object.fromEntries(disks.map((disk) => [disk.id, disk.display_name]));
  const groupNames = Object.fromEntries(
    vms.filter((vm) => vm.external_id).map((vm) => [`${vm.vm_type === "ct" ? "ct" : "vm"}/${vm.external_id}`, vm.name]),
  );

  return (
    <div className="pbo-ui min-h-screen" {...attributes}>
      <PortalContainerContext.Provider value={portal}>
        <DiskDirectoryProvider disks={disks}>
        <ToastProvider
          closeLabel={t.shell.close}
          viewLabel={t.shell.view}
          renderDisk={(diskId) => {
            const disk = disks.find((item) => item.id === diskId);
            return disk ? <DiskAvatar disk={disk} t={t} size="sm" /> : null;
          }}
          onAlert={recordAlertToast}
        >
          <ConfirmProvider defaultCancelLabel={t.confirmDialogs.cancel}>
            <RunMonitorProvider diskNames={diskNames} groupNames={groupNames}>
              <ShellLayout
                t={t}
                language={language}
                onLanguageChange={onLanguageChange}
                themePreference={preference}
                onThemeCycle={cycle}
                onThemeChange={setPreference}
                newDisks={newDiskCount(disks)}
                collapsed={prefs.sidebarCollapsed}
              >
                {children}
              </ShellLayout>
              <DiskEventToasts language={language} />
              <LiveRunModal t={t} language={language} />
            </RunMonitorProvider>
          </ConfirmProvider>
        </ToastProvider>
        </DiskDirectoryProvider>
      </PortalContainerContext.Provider>
      {/* Radix portals land here: inside .pbo-ui, so tokens and utilities apply. */}
      <div ref={setPortal} />
    </div>
  );
}

function ShellLayout({
  children,
  t,
  language,
  onLanguageChange,
  themePreference,
  onThemeCycle,
  onThemeChange,
  newDisks,
  collapsed = false,
}: {
  children: ReactNode;
  t: TranslationDictionary;
  language: Language;
  onLanguageChange: (language: Language) => void;
  themePreference: "system" | "light" | "dark";
  onThemeCycle: () => void;
  onThemeChange: (preference: "system" | "light" | "dark") => void;
  newDisks: number;
  /** Phase 5j: icons only (desktop); the mobile drawer is always full. */
  collapsed?: boolean;
}) {
  const location = useLocation();
  const { username, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const ThemeIcon = themePreference === "system" ? Monitor : themePreference === "light" ? Sun : Moon;

  useEffect(() => setMobileOpen(false), [location.pathname]);

  const current = [...OVERVIEW, ...CONFIGURATION].find((item) => (item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to)));
  const label = (key: NavKey | "resources") => (key === "resources" ? t.shell.resources : t.nav[key]);

  const renderSidebar = (mini: boolean) => (
    // No text caret / selection in the menu (a caret showed under "Settings").
    <aside
      className={cn(
        "flex h-full select-none flex-col gap-0.5 border-r border-border bg-surface pb-4 pt-5 [caret-color:transparent]",
        mini ? "w-16 items-center px-2" : "w-[248px] px-4",
      )}
      data-collapsed={mini ? "" : undefined}
    >
      <div className={cn("flex items-center gap-2 pb-5", mini ? "justify-center" : "px-1.5")}>{mini ? <LogoMark /> : <Logo />}</div>
      {mini ? null : <SectionLabel>{t.shell.overview}</SectionLabel>}
      {OVERVIEW.map((item) => (
        <NavItem key={item.to} to={item.to} icon={item.icon} label={label(item.key)} mini={mini} badge={item.key === "disks" && newDisks > 0}>
          {item.key === "disks" && newDisks > 0 && !mini ? (
            <StatusDot tone="warn" className="text-xs">
              {t.shell.newDisks.replace("{n}", String(newDisks))}
            </StatusDot>
          ) : null}
        </NavItem>
      ))}
      {mini ? <div className="my-2 h-px w-8 bg-border" /> : <SectionLabel className="pt-4">{t.shell.configuration}</SectionLabel>}
      {CONFIGURATION.map((item) => (
        <NavItem key={item.to} to={item.to} icon={item.icon} label={label(item.key)} mini={mini} />
      ))}
      <div className="min-h-6 flex-1" />
      <UserMenu
        t={t}
        username={username ?? "?"}
        language={language}
        onLanguageChange={onLanguageChange}
        themePreference={themePreference}
        onThemeChange={onThemeChange}
        onLogout={() => void logout()}
        mini={mini}
      />
    </aside>
  );

  return (
    <div className="flex min-h-screen">
      <div className="sticky top-0 hidden h-screen shrink-0 lg:block">{renderSidebar(collapsed)}</div>
      {mobileOpen ? (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="h-full">{renderSidebar(false)}</div>
          <button type="button" aria-label={t.shell.close} className="flex-1 bg-black/50" onClick={() => setMobileOpen(false)}>
            <X className="ml-auto mr-4 mt-4 h-5 w-5 text-white" />
          </button>
        </div>
      ) : null}

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-[60px] flex-wrap items-center gap-3 border-b border-border px-4 py-3 sm:px-7">
          <button type="button" aria-label={t.shell.menu} className="rounded-md p-1.5 text-muted hover:bg-subtle lg:hidden" onClick={() => setMobileOpen(true)}>
            <Menu className="h-5 w-5" />
          </button>
          <nav aria-label={t.shell.breadcrumb} className="flex flex-1 items-center gap-2 text-sm">
            <span className="text-faint">PBO</span>
            <span className="text-faint">/</span>
            <span className="font-medium">{current ? label(current.key) : ""}</span>
          </nav>
          {/* B55: ⌘K palette (search + actions); a magnifier on mobile. */}
          <PaletteButton t={t} />
          {/* Small screens: the running task gets its own line, under the icons. */}
          <RunPill t={t} className="order-last w-full sm:order-none sm:w-auto [&>button]:min-w-0 [&>button]:flex-1 sm:[&>button]:flex-none" />
          <button
            type="button"
            aria-label={t.shell.theme[themePreference]}
            title={t.shell.theme[themePreference]}
            onClick={onThemeCycle}
            className="flex h-[34px] w-[34px] items-center justify-center rounded-lg border border-border text-muted hover:bg-subtle"
          >
            <ThemeIcon className="h-4 w-4" />
          </button>
          {/* B55: the inbox replaces the bell. */}
          <InboxButton t={t} language={language} />
        </header>
        {/* B27: fluid content, side padding, a wide cap centred only beyond it. */}
        <div
          data-shell-content
          className={cn(
            // B27: the whole useful width (side padding only), up to ~2200 px; no centring.
            "w-full px-4 py-5 sm:px-7 sm:py-7 2xl:px-10",
            !FULL_WIDTH.some((path) => location.pathname.startsWith(path)) && "max-w-[2200px]",
          )}
        >
          {children}
        </div>
      </main>
    </div>
  );
}

const THEMES = ["system", "light", "dark"] as const;
const THEME_ICON = { system: Monitor, light: Sun, dark: Moon };

/** B27: avatar + name + role; opens Language / Theme / Log out. */
export function UserMenu({
  t,
  username,
  language,
  onLanguageChange,
  themePreference,
  onThemeChange,
  onLogout,
  mini = false,
}: {
  t: TranslationDictionary;
  username: string;
  language: Language;
  onLanguageChange: (language: Language) => void;
  themePreference: "system" | "light" | "dark";
  onThemeChange: (preference: "system" | "light" | "dark") => void;
  onLogout: () => void;
  /** Collapsed sidebar: the avatar only. */
  mini?: boolean;
}) {
  const option = (active: boolean) =>
    cn(
      "flex flex-1 items-center justify-center gap-1.5 rounded-md border-0 px-2 py-1.5 text-xs font-medium",
      active ? "bg-surface text-fg shadow-sm" : "bg-transparent text-muted hover:text-fg",
    );
  return (
    <Popover
      label={t.shell.userMenu}
      role="menu"
      placement="top-start"
      className="w-[232px] p-2"
      triggerClassName="flex w-full min-w-0 items-center gap-2.5 rounded-lg border-0 bg-transparent px-1.5 py-2 text-left text-fg hover:bg-hover"
      trigger={
        <>
          <span className="flex h-[30px] w-[30px] flex-none items-center justify-center rounded-full bg-hover text-[13px] font-semibold">
            {username.slice(0, 1).toUpperCase()}
          </span>
          <span className={mini ? "sr-only" : "flex min-w-0 flex-1 flex-col"}>
            <span className="truncate text-[13px] font-medium">{username}</span>
            <span className="truncate text-xs text-faint">{t.shell.administrator}</span>
          </span>
          {mini ? null : <ChevronsUpDown className="h-4 w-4 flex-none text-faint" />}
        </>
      }
    >
      {(close) => (
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-1 px-1 pt-1">
            <span className="text-[11px] font-medium uppercase tracking-[0.04em] text-faint">{t.shell.language}</span>
            <div role="group" aria-label={t.shell.language} className="flex gap-1 rounded-lg bg-subtle p-[3px]">
              {(["fr", "en"] as const).map((code) => (
                <button
                  key={code}
                  type="button"
                  role="menuitemradio"
                  aria-checked={language === code}
                  className={option(language === code)}
                  onClick={() => onLanguageChange(code)}
                >
                  {t.shell.languageName[code]}
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-1 px-1">
            <span className="text-[11px] font-medium uppercase tracking-[0.04em] text-faint">{t.shell.themeLabel}</span>
            <div role="group" aria-label={t.shell.themeLabel} className="flex gap-1 rounded-lg bg-subtle p-[3px]">
              {THEMES.map((value) => {
                const Icon = THEME_ICON[value];
                return (
                  <button
                    key={value}
                    type="button"
                    role="menuitemradio"
                    aria-checked={themePreference === value}
                    aria-label={t.shell.theme[value]}
                    title={t.shell.theme[value]}
                    className={option(themePreference === value)}
                    onClick={() => onThemeChange(value)}
                  >
                    <Icon className="h-3.5 w-3.5" />
                  </button>
                );
              })}
            </div>
          </div>
          <div className="my-0.5 h-px bg-border" />
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              close();
              onLogout();
            }}
            className="flex items-center gap-2 rounded-md border-0 bg-transparent px-2 py-2 text-left text-[13px] font-medium text-fg hover:bg-hover"
          >
            <LogOut className="h-4 w-4 text-muted" />
            {t.shell.logout}
          </button>
        </div>
      )}
    </Popover>
  );
}

function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("px-2.5 pb-1.5 pt-2 text-[11px] font-medium uppercase tracking-[0.04em] text-faint", className)}>{children}</div>;
}

function NavItem({
  to,
  icon: Icon,
  label,
  children,
  mini = false,
  badge = false,
}: {
  to: string;
  icon: typeof LayoutDashboard;
  label: string;
  children?: ReactNode;
  mini?: boolean;
  badge?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      title={mini ? label : undefined}
      className={({ isActive }) =>
        cn(
          "relative flex items-center gap-2.5 rounded-md text-sm transition-colors hover:bg-hover",
          mini ? "h-9 w-9 justify-center" : "px-2.5 py-2",
          isActive ? "bg-hover font-medium text-fg" : "text-muted",
        )
      }
    >
      <Icon className="h-4 w-4" />
      <span className={mini ? "sr-only" : "flex-1"}>{label}</span>
      {mini && badge ? <span aria-hidden className="absolute right-1.5 top-1.5 h-[7px] w-[7px] rounded-full bg-warning" /> : null}
      {children}
    </NavLink>
  );
}

/** Live disk events → global toasts ("New disk detected … — Review"). */
function DiskEventToasts({ language }: { language: Language }) {
  const { toast } = useToast();
  useEvents(["disk.added", "disk.removed"], (event) => {
    const item = toastForDiskEvent(event, language === "fr" ? "fr" : "en");
    if (!item) return;
    toast({
      id: `disk-${item.key}`,
      tone: item.tone === "warning" ? "warning" : item.tone === "neutral" ? "neutral" : "info",
      title: item.message,
      diskId: (event.data as { id?: number }).id,
      action: item.action ? { label: item.action.label, href: diskPageUrl(item.action.diskId) } : undefined,
      inbox: item.inbox,
    });
  });
  return null;
}
