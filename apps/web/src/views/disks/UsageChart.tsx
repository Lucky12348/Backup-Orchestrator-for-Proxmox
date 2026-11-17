import { useState } from "react";

import type { Language, TranslationDictionary } from "../../i18n";
import { formatGib, formatUsage } from "../../units";
import { fill, serverDate } from "../format";
import { gibScale, nearestIndex, type UsagePoint } from "./diskProfile";

// Drawn in a 1000 × 100 box stretched to the container (preserveAspectRatio
// "none", strokes not scaled); every text is HTML at the page's font size.
const W = 1000;
const H = 100;

function day(iso: string, language: Language): string {
  return new Intl.DateTimeFormat(language === "fr" ? "fr-FR" : "en-GB", { day: "numeric", month: "short" }).format(serverDate(iso));
}

/** Tooltip anchoring: centred, but kept inside the chart near its edges. */
export function tooltipAlign(fraction: number): "start" | "center" | "end" {
  if (fraction < 0.18) return "start";
  if (fraction > 0.82) return "end";
  return "center";
}

/** Used space over time (Phase 5h.1): Y axis in GiB at the data's scale, the
 *  80/90 % marks only when inside it, crosshair + tooltip, a table view. */
export function UsageChart({ points, name, language, t }: { points: UsagePoint[]; name: string; language: Language; t: TranslationDictionary }) {
  const text = t.diskSheet;
  const [hover, setHover] = useState<number | null>(null);
  if (points.length === 0) return <p className="m-0 text-[13px] text-faint">{text.usageEmpty}</p>;
  const scale = gibScale(points);
  const first = points[0].time;
  const span = Math.max(1, points[points.length - 1].time - first);
  const xs = points.map((point) => (points.length === 1 ? W : (W * (point.time - first)) / span));
  const y = (gb: number) => H - (H * (gb - scale.min)) / Math.max(1e-9, scale.max - scale.min); // 0 = top
  const ys = points.map((point) => y(point.usedGb));
  const line = xs.map((x, index) => `${index ? "L" : "M"}${x.toFixed(1)},${ys[index].toFixed(2)}`).join(" ");
  const area = `${line} L${xs[xs.length - 1].toFixed(1)},${H} L${xs[0].toFixed(1)},${H} Z`;
  const active = hover === null ? null : points[hover];
  const at = (index: number) => xs[index] / W;
  const align = hover === null ? "center" : tooltipAlign(at(hover));

  return (
    <div className="flex flex-col gap-2">
      <div className="relative h-[180px] select-none text-[11px]" data-usage-chart>
        {scale.ticks.map((gb, index) => (
          <span key={index} className="absolute left-0 w-14 -translate-y-1/2 text-right text-faint" style={{ top: `calc(8px + (100% - 32px) * ${y(gb) / H})` }} data-tick>
            {formatGib(gb, language)}
          </span>
        ))}
        <div
          className="absolute bottom-6 left-16 right-2 top-2"
          role="img"
          aria-label={fill(text.chartLabel, { name })}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(event) => {
            const box = event.currentTarget.getBoundingClientRect();
            setHover(nearestIndex(xs, ((event.clientX - box.left) / box.width) * W));
          }}
        >
          {scale.ticks.map((gb, index) => (
            <div key={index} className="absolute inset-x-0 border-0 border-t border-solid border-border" style={{ top: `${y(gb)}%` }} />
          ))}
          {scale.thresholds.map((mark) => (
            <div
              key={mark.pct}
              className={`absolute inset-x-0 border-0 border-t border-dashed ${mark.pct === 90 ? "border-danger/45" : "border-warning/45"}`}
              style={{ top: `${y(mark.gb)}%` }}
              data-threshold={mark.pct}
            >
              <span className="absolute right-0 -translate-y-full pb-0.5 text-[10px] text-faint">{mark.pct === 90 ? text.threshold90 : text.threshold80}</span>
            </div>
          ))}
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible">
            <path d={area} className="fill-accent/15" />
            <path d={line} className="fill-none stroke-accent" strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
          </svg>
          {active && hover !== null ? (
            <>
              <div className="absolute inset-y-0 border-0 border-l border-solid border-border-strong" style={{ left: `${at(hover) * 100}%` }} />
              <span
                className="absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-solid border-surface bg-accent"
                style={{ left: `${at(hover) * 100}%`, top: `${ys[hover]}%` }}
              />
              <div
                role="status"
                className="pointer-events-none absolute top-0 z-10 whitespace-nowrap rounded-lg border border-solid border-border bg-surface px-2.5 py-1.5 text-xs shadow-lg"
                style={{
                  left: `${at(hover) * 100}%`,
                  transform: align === "start" ? "translateX(8px)" : align === "end" ? "translateX(calc(-100% - 8px))" : "translateX(-50%)",
                }}
                data-align={align}
              >
                <div className="text-faint">{day(active.at, language)}</div>
                <div className="font-medium text-fg">{formatUsage(active.usedGb, active.totalGb, language)}</div>
              </div>
            </>
          ) : null}
        </div>
        <span className="absolute bottom-0 left-16 text-faint">{day(points[0].at, language)}</span>
        <span className="absolute bottom-0 right-2 text-faint">{day(points[points.length - 1].at, language)}</span>
      </div>
      <details>
        <summary className="cursor-pointer text-xs text-muted">{text.table}</summary>
        <table className="mt-2 w-full border-collapse text-xs">
          <tbody>
            {points.map((point) => (
              <tr key={point.at} className="border-0 border-t border-solid border-border">
                <td className="py-1 text-muted">{day(point.at, language)}</td>
                <td className="py-1 text-right font-mono">{formatUsage(point.usedGb, point.totalGb, language)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
