import { useState } from "react";

import type { Language, TranslationDictionary } from "../../i18n";
import { formatUsage } from "../../units";
import { fill } from "../format";
import { chartGeometry, nearestIndex, type UsagePoint } from "./diskProfile";

const WIDTH = 600;
const HEIGHT = 160;
const LEFT = 36; // y-axis labels
const BOTTOM = 22; // x-axis labels

function day(iso: string, language: Language): string {
  return new Intl.DateTimeFormat(language === "fr" ? "fr-FR" : "en-GB", { day: "numeric", month: "short" }).format(new Date(iso));
}

const pct = (value: number, language: Language) => `${(Math.round(value * 10) / 10).toString().replace(".", language === "fr" ? "," : ".")} %`;

/** Usage of the disk's datastore over time: one series (percent, one axis),
 *  80 % / 90 % alert lines, crosshair + tooltip on hover, a table view. */
export function UsageChart({ points, name, language, t }: { points: UsagePoint[]; name: string; language: Language; t: TranslationDictionary }) {
  const text = t.diskSheet;
  const [hover, setHover] = useState<number | null>(null);
  const plotWidth = WIDTH - LEFT - 8;
  const plotHeight = HEIGHT - BOTTOM - 8;
  const geometry = chartGeometry(points, plotWidth, plotHeight);
  if (points.length === 0) return <p className="m-0 text-[13px] text-faint">{text.usageEmpty}</p>;
  const y = (value: number) => 8 + plotHeight - (plotHeight * value) / 100;
  const active = hover === null ? null : points[hover];

  return (
    <div className="flex flex-col gap-2">
      <div className="relative">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="block h-auto w-full overflow-visible"
          role="img"
          aria-label={fill(text.chartLabel, { name })}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(event) => {
            const box = event.currentTarget.getBoundingClientRect();
            const x = ((event.clientX - box.left) / box.width) * WIDTH - LEFT;
            setHover(nearestIndex(geometry.xs, x));
          }}
        >
          {/* recessive grid: 0 / 50 / 100 % */}
          {[0, 50, 100].map((value) => (
            <g key={value}>
              <line x1={LEFT} x2={WIDTH - 8} y1={y(value)} y2={y(value)} className="stroke-border" strokeWidth={1} />
              <text x={LEFT - 6} y={y(value) + 4} textAnchor="end" className="fill-faint text-[10px]">
                {value} %
              </text>
            </g>
          ))}
          {/* alert thresholds (status colours, labelled) */}
          {[
            { value: 80, cls: "stroke-warning", label: text.threshold80 },
            { value: 90, cls: "stroke-danger", label: text.threshold90 },
          ].map((line) => (
            <g key={line.value}>
              <line x1={LEFT} x2={WIDTH - 8} y1={y(line.value)} y2={y(line.value)} className={line.cls} strokeWidth={1} strokeDasharray="4 4" />
              <text x={LEFT + 4} y={y(line.value) - 4} className="fill-muted text-[10px]">
                {line.label}
              </text>
            </g>
          ))}
          <g transform={`translate(${LEFT} 8)`}>
            <path d={geometry.area} className="fill-accent/15" />
            <path d={geometry.line} className="fill-none stroke-accent" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {points.length === 1 ? <circle cx={geometry.xs[0]} cy={geometry.ys[0]} r={4} className="fill-accent stroke-surface" strokeWidth={2} /> : null}
            {active && hover !== null ? (
              <g>
                <line x1={geometry.xs[hover]} x2={geometry.xs[hover]} y1={0} y2={plotHeight} className="stroke-border-strong" strokeWidth={1} />
                <circle cx={geometry.xs[hover]} cy={geometry.ys[hover]} r={4} className="fill-accent stroke-surface" strokeWidth={2} />
              </g>
            ) : null}
          </g>
          <text x={LEFT} y={HEIGHT - 4} className="fill-faint text-[10px]">
            {day(points[0].at, language)}
          </text>
          <text x={WIDTH - 8} y={HEIGHT - 4} textAnchor="end" className="fill-faint text-[10px]">
            {day(points[points.length - 1].at, language)}
          </text>
        </svg>
        {active && hover !== null ? (
          <div
            role="status"
            className="pointer-events-none absolute top-0 -translate-x-1/2 rounded-lg border border-solid border-border bg-surface px-2.5 py-1.5 text-xs shadow-lg"
            style={{ left: `${((LEFT + geometry.xs[hover]) / WIDTH) * 100}%` }}
          >
            <div className="text-faint">{day(active.at, language)}</div>
            <div className="font-medium text-fg">
              {pct(active.percent, language)} · {formatUsage(active.usedGb, active.totalGb, language)}
            </div>
          </div>
        ) : null}
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
