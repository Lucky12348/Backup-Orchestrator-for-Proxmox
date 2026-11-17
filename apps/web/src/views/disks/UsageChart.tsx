import { useState } from "react";

import type { Language, TranslationDictionary } from "../../i18n";
import { formatUsage } from "../../units";
import { fill, serverDate } from "../format";
import { chartGeometry, nearestIndex, type UsagePoint } from "./diskProfile";

// The plot is drawn in a 1000 × 100 box stretched to the container
// (preserveAspectRatio="none", strokes not scaled); every text is HTML, so it
// keeps the page's font size at any width (B38).
const W = 1000;
const H = 100;

function day(iso: string, language: Language): string {
  return new Intl.DateTimeFormat(language === "fr" ? "fr-FR" : "en-GB", { day: "numeric", month: "short" }).format(serverDate(iso));
}

const pct = (value: number, language: Language) => `${(Math.round(value * 10) / 10).toString().replace(".", language === "fr" ? "," : ".")} %`;

/** Tooltip anchoring: centred, but kept inside the chart near its edges. */
export function tooltipAlign(fraction: number): "start" | "center" | "end" {
  if (fraction < 0.18) return "start";
  if (fraction > 0.82) return "end";
  return "center";
}

/** Usage of the disk's datastore over time: one series (percent, one axis),
 *  discreet 80 % / 90 % marks, crosshair + tooltip on hover, a table view. */
export function UsageChart({ points, name, language, t }: { points: UsagePoint[]; name: string; language: Language; t: TranslationDictionary }) {
  const text = t.diskSheet;
  const [hover, setHover] = useState<number | null>(null);
  const geometry = chartGeometry(points, W, H);
  if (points.length === 0) return <p className="m-0 text-[13px] text-faint">{text.usageEmpty}</p>;
  const active = hover === null ? null : points[hover];
  const at = (index: number) => geometry.xs[index] / W; // 0..1
  const align = hover === null ? "center" : tooltipAlign(at(hover));

  return (
    <div className="flex flex-col gap-2">
      <div className="relative h-[180px] select-none text-[11px]" data-usage-chart>
        {/* y axis: 0 / 50 / 100 % (normal text size) */}
        {[0, 50, 100].map((value) => (
          <span key={value} className="absolute left-0 w-8 -translate-y-1/2 text-right text-faint" style={{ top: `calc(8px + (100% - 32px) * ${(100 - value) / 100})` }}>
            {value} %
          </span>
        ))}
        <div
          className="absolute bottom-6 left-10 right-2 top-2"
          role="img"
          aria-label={fill(text.chartLabel, { name })}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(event) => {
            const box = event.currentTarget.getBoundingClientRect();
            setHover(nearestIndex(geometry.xs, ((event.clientX - box.left) / box.width) * W));
          }}
        >
          {/* recessive grid */}
          {[0, 50, 100].map((value) => (
            <div key={value} className="absolute inset-x-0 border-0 border-t border-solid border-border" style={{ top: `${100 - value}%` }} />
          ))}
          {/* alert thresholds: thin, dashed, faint, labelled on the right */}
          {[
            { value: 80, label: text.threshold80, cls: "border-warning/45" },
            { value: 90, label: text.threshold90, cls: "border-danger/45" },
          ].map((line) => (
            <div key={line.value} className={`absolute inset-x-0 border-0 border-t border-dashed ${line.cls}`} style={{ top: `${100 - line.value}%` }} data-threshold={line.value}>
              <span className="absolute right-0 -translate-y-full pb-0.5 text-[10px] text-faint">{line.label}</span>
            </div>
          ))}
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible">
            <path d={geometry.area} className="fill-accent/15" />
            <path d={geometry.line} className="fill-none stroke-accent" strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
          </svg>
          {(points.length === 1 ? [0] : active && hover !== null ? [hover] : []).map((index) => (
            <span
              key={index}
              className="absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-solid border-surface bg-accent"
              style={{ left: `${at(index) * 100}%`, top: `${geometry.ys[index]}%` }}
            />
          ))}
          {active && hover !== null ? (
            <>
              <div className="absolute inset-y-0 border-0 border-l border-solid border-border-strong" style={{ left: `${at(hover) * 100}%` }} />
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
                <div className="font-medium text-fg">
                  {pct(active.percent, language)} · {formatUsage(active.usedGb, active.totalGb, language)}
                </div>
              </div>
            </>
          ) : null}
        </div>
        <span className="absolute bottom-0 left-10 text-faint">{day(points[0].at, language)}</span>
        <span className="absolute bottom-0 right-2 text-faint">{day(points[points.length - 1].at, language)}</span>
      </div>
      <details>
        <summary className="cursor-pointer text-xs text-muted">{text.table}</summary>
        <table className="mt-2 w-full border-collapse text-xs">
          <tbody>
            {points.map((point) => (
              <tr key={point.at} className="border-0 border-t border-solid border-border">
                <td className="py-1 text-muted">{day(point.at, language)}</td>
                <td className="py-1 text-right font-mono">{pct(point.percent, language)}</td>
                <td className="py-1 text-right font-mono text-muted">{formatUsage(point.usedGb, point.totalGb, language)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
