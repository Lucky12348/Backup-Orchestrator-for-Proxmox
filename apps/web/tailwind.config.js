import defaultTheme from "tailwindcss/defaultTheme";

/**
 * Tailwind is limited to the new UI (first-run wizard for now) so the
 * existing pages do not change:
 * - `content` only scans the new code: no utility is generated for class
 *   names used by the legacy pages;
 * - `important: "#pbo-wizard"` scopes every utility under the wizard root;
 * - preflight is off (a scoped reset lives in src/setup/wizard.css);
 * - rem values are converted to px: the legacy styles.css sets the root
 *   font size to 14px, which would shrink every rem-based utility.
 */
const PX_PER_REM = 16;

function remToPx(value) {
  if (typeof value === "string") {
    return value.replace(/(-?\d*\.?\d+)rem\b/g, (_, n) => `${parseFloat(n) * PX_PER_REM}px`);
  }
  if (Array.isArray(value)) return value.map(remToPx);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, remToPx(item)]));
  }
  return value;
}

const token = (name) => `rgb(var(--pbo-${name}) / <alpha-value>)`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./src/setup/**/*.{ts,tsx}", "./src/components/ui/**/*.{ts,tsx}"],
  important: "#pbo-wizard",
  corePlugins: { preflight: false },
  theme: {
    spacing: remToPx(defaultTheme.spacing),
    fontSize: remToPx(defaultTheme.fontSize),
    lineHeight: remToPx(defaultTheme.lineHeight),
    maxWidth: remToPx(defaultTheme.maxWidth({ theme: () => remToPx(defaultTheme.spacing), breakpoints: () => ({}) })),
    borderRadius: { none: "0px", sm: "6px", DEFAULT: "8px", md: "10px", lg: "12px", xl: "16px", full: "9999px" },
    extend: {
      colors: {
        bg: token("bg"),
        surface: token("surface"),
        subtle: token("subtle"),
        border: token("border"),
        fg: token("fg"),
        muted: token("muted"),
        faint: token("faint"),
        accent: { DEFAULT: token("accent"), fg: token("accent-fg") },
        success: token("success"),
        warning: token("warning"),
        danger: token("danger"),
      },
      fontFamily: {
        sans: ['"Geist Variable"', ...defaultTheme.fontFamily.sans],
        mono: ['"Geist Mono Variable"', ...defaultTheme.fontFamily.mono],
      },
      keyframes: {
        "pbo-fade-in": { from: { opacity: "0", transform: "translateY(4px)" }, to: { opacity: "1", transform: "none" } },
      },
      animation: { "fade-in": "pbo-fade-in 180ms ease-out" },
    },
  },
  plugins: [],
};
