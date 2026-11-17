import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentType,
  type HTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  type TouchEvent as ReactTouchEvent,
} from "react";
import { createPortal } from "react-dom";
import { MoreHorizontal } from "lucide-react";

import { cn } from "../components/ui/utils";
import { isMenuKey, LONG_PRESS_MS, menuPosition, nextIndex } from "./actionMenuModel";
import { useConfirm } from "./confirm";
import { usePortalContainer } from "./portal";

/**
 * Context menus (Phase 5h). The SAME actions open from:
 *  - a right click on the element (`ActionMenuArea`),
 *  - the visible "⋯" button (`ActionMenuButton`),
 *  - a long press on touch screens,
 *  - Shift+F10 or the Menu key when the element has the focus.
 * Risky actions carry `confirm`: an AlertDialog asks first.
 */
export interface MenuAction {
  key: string;
  label: string;
  icon?: ComponentType<{ className?: string }>;
  /** May return a promise (errors are the caller's to show). */
  onSelect: () => unknown;
  danger?: boolean;
  disabled?: boolean;
  /** Small second line (why an entry is disabled, where else to do it). */
  hint?: string;
  /** Asked before running (risky actions). */
  confirm?: { title: string; description?: string; confirmLabel: string };
}

type OpenAt = { x: number; y: number; returnFocus: HTMLElement | null };

function MenuPanel({ actions, label, at, onClose }: { actions: MenuAction[]; label: string; at: OpenAt; onClose: () => void }) {
  const container = usePortalContainer();
  const confirm = useConfirm();
  const panel = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState({ left: at.x, top: at.y });
  const [active, setActive] = useState(() => nextIndex(-1, "Home", actions.map((action) => Boolean(action.disabled))));
  const items = useRef<(HTMLButtonElement | null)[]>([]);

  useLayoutEffect(() => {
    const rect = panel.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition(menuPosition(at, { width: rect.width, height: rect.height }, { width: window.innerWidth, height: window.innerHeight }));
  }, [at]);
  useEffect(() => {
    items.current[active]?.focus();
  }, [active]);

  const close = useCallback(() => {
    onClose();
    at.returnFocus?.focus();
  }, [at, onClose]);

  useEffect(() => {
    const outside = (event: Event) => {
      if (panel.current && !panel.current.contains(event.target as Node)) onClose();
    };
    const dismiss = () => onClose();
    document.addEventListener("mousedown", outside);
    document.addEventListener("touchstart", outside);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("touchstart", outside);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  }, [onClose]);

  async function run(action: MenuAction) {
    if (action.disabled) return;
    close();
    if (action.confirm && !(await confirm({ ...action.confirm, tone: action.danger ? "danger" : "warning" }))) return;
    await action.onSelect();
  }

  function onKeyDown(event: ReactKeyboardEvent) {
    const disabled = actions.map((action) => Boolean(action.disabled));
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      setActive((current) => nextIndex(current, event.key, disabled));
    } else if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      close();
    }
  }

  const menu = (
    <div
      ref={panel}
      role="menu"
      aria-label={label}
      onKeyDown={onKeyDown}
      onContextMenu={(event) => event.preventDefault()}
      style={{ left: position.left, top: position.top }}
      className="fixed z-[70] flex min-w-[220px] max-w-[min(320px,calc(100vw-16px))] flex-col gap-0.5 rounded-xl border border-solid border-border-strong bg-surface p-1.5 text-fg shadow-[0_16px_48px_rgba(0,0,0,0.45)] animate-fade-in"
      data-action-menu
    >
      {actions.map((action, index) => {
        const Icon = action.icon;
        return (
          <button
            key={action.key}
            ref={(element) => {
              items.current[index] = element;
            }}
            type="button"
            role="menuitem"
            tabIndex={index === active ? 0 : -1}
            aria-disabled={action.disabled || undefined}
            disabled={action.disabled}
            onClick={() => void run(action)}
            onMouseEnter={() => !action.disabled && setActive(index)}
            className={cn(
              "flex items-center gap-2.5 rounded-lg border-0 bg-transparent px-2.5 py-2 text-left text-[13px] font-medium outline-none",
              action.danger ? "text-danger-text" : "text-fg",
              action.disabled ? "cursor-not-allowed opacity-45" : "hover:bg-hover focus-visible:bg-hover",
            )}
            data-action={action.key}
          >
            {Icon ? <Icon className={cn("h-4 w-4 flex-none", action.danger ? "" : "text-muted")} /> : <span className="h-4 w-4 flex-none" />}
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate">{action.label}</span>
              {action.hint ? <span className="text-[11px] font-normal leading-snug text-faint [overflow-wrap:anywhere]">{action.hint}</span> : null}
            </span>
          </button>
        );
      })}
    </div>
  );
  return container ? createPortal(menu, container) : menu;
}

const MenuContext = createContext<{ open: (at: OpenAt) => void } | null>(null);

/** Hook behind ActionMenuArea / ActionMenuButton (a single menu per element). */
function useMenu(actions: MenuAction[], label: string) {
  const [at, setAt] = useState<OpenAt | null>(null);
  const visible = actions.length > 0;
  const panel = at && visible ? <MenuPanel actions={actions} label={label} at={at} onClose={() => setAt(null)} /> : null;
  return { open: (next: OpenAt) => visible && setAt(next), panel, visible };
}

/**
 * Right click / long press / Shift+F10 handlers to spread on ANY element
 * (`{...menu.props}`), plus the panel to render next to it.
 */
export function useContextMenu(actions: MenuAction[], label: string) {
  const menu = useMenu(actions, label);
  const timer = useRef<number | undefined>(undefined);
  const pressed = useRef(false);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const cancel = () => window.clearTimeout(timer.current);
  const props = {
    onContextMenu: (event: ReactMouseEvent<HTMLElement>) => {
      if (!menu.visible || (event.target as HTMLElement).closest("input, textarea, select")) return;
      event.preventDefault();
      event.stopPropagation();
      menu.open({ x: event.clientX, y: event.clientY, returnFocus: event.currentTarget });
    },
    onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => {
      if (!menu.visible || !isMenuKey(event)) return;
      event.preventDefault();
      event.stopPropagation();
      const rect = event.currentTarget.getBoundingClientRect();
      menu.open({ x: rect.left + 16, y: rect.top + Math.min(rect.height, 40), returnFocus: event.currentTarget });
    },
    onTouchStart: (event: ReactTouchEvent<HTMLElement>) => {
      if (!menu.visible) return;
      const touch = event.touches[0];
      const target = event.currentTarget;
      pressed.current = false;
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => {
        pressed.current = true;
        menu.open({ x: touch.clientX, y: touch.clientY, returnFocus: target });
      }, LONG_PRESS_MS);
    },
    onTouchEnd: cancel,
    onTouchMove: cancel,
    onTouchCancel: cancel,
    // A long press must not also "click" the element under the finger.
    onClickCapture: (event: ReactMouseEvent<HTMLElement>) => {
      if (pressed.current) {
        event.preventDefault();
        event.stopPropagation();
        pressed.current = false;
      }
    },
    "aria-haspopup": menu.visible ? ("menu" as const) : undefined,
    "data-context-menu": menu.visible ? "" : undefined,
  };
  return { props, panel: menu.panel, open: menu.open };
}

/** An element (div by default) with the context menu; focusable for Shift+F10. */
export function ActionMenuArea({
  actions,
  label,
  children,
  className,
  as: Tag = "div",
  ...rest
}: {
  actions: MenuAction[];
  label: string;
  children: ReactNode;
  className?: string;
  as?: "div" | "li" | "tr" | "article" | "section";
} & Omit<HTMLAttributes<HTMLElement>, "onContextMenu" | "children" | "className">) {
  const menu = useContextMenu(actions, label);
  return (
    <MenuContext.Provider value={{ open: menu.open }}>
      <Tag
        {...rest}
        {...menu.props}
        onKeyDown={(event: ReactKeyboardEvent<HTMLElement>) => {
          rest.onKeyDown?.(event);
          menu.props.onKeyDown(event);
        }}
        tabIndex={rest.tabIndex ?? 0}
        className={className}
      >
        {children}
      </Tag>
      {menu.panel}
    </MenuContext.Provider>
  );
}

/** The visible "⋯" button: same actions as the right click. */
export function ActionMenuButton({ actions, label, className }: { actions: MenuAction[]; label: string; className?: string }) {
  const menu = useMenu(actions, label);
  if (!menu.visible) return null;
  return (
    <>
      <button
        type="button"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        onClick={(event) => {
          event.stopPropagation();
          const rect = event.currentTarget.getBoundingClientRect();
          menu.open({ x: rect.right - 4, y: rect.bottom + 4, returnFocus: event.currentTarget });
        }}
        onKeyDown={(event) => event.stopPropagation()}
        className={cn(
          "flex h-8 w-8 flex-none items-center justify-center rounded-lg border-0 bg-transparent text-faint hover:bg-hover hover:text-fg",
          className,
        )}
        data-menu-button
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {menu.panel}
    </>
  );
}

/** For a child of an ActionMenuArea that wants to open the same menu. */
export function useAreaMenu() {
  return useContext(MenuContext);
}
