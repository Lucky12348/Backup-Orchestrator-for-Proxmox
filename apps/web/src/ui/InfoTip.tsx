import { useId } from "react";

/**
 * The "i" next to a page title (replaces the subtitle): a tooltip shown on
 * hover and on keyboard focus, as in docs/design/mockups/dashboard.html.
 */
export function InfoTip({ text, label }: { text: string; label: string }) {
  const id = useId();
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        aria-label={label}
        aria-describedby={id}
        className="m-0 flex h-[22px] w-[22px] cursor-help items-center justify-center rounded-full border border-solid border-border-strong bg-transparent p-0 font-[inherit] text-xs font-semibold leading-none text-faint hover:text-fg"
      >
        i
      </button>
      <span
        id={id}
        role="tooltip"
        className="invisible absolute left-0 top-[30px] z-30 w-[300px] max-w-[80vw] rounded-[10px] border border-solid border-border-strong bg-hover px-3 py-2.5 text-[13px] font-normal leading-[1.45] text-muted opacity-0 shadow-2xl transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}
