// F1 pieces: driver chips in team colours, tyre compounds, and the race charts.
// Colour follows the entity, never its rank: team colours for drivers (teammates share one,
// so the second driver's line is dashed), Pirelli colours for compounds.

import * as Plot from "@observablehq/plot";
import { useCallback, useContext, type ReactNode } from "react";
import { color } from "../colors";
import { SESSION_LABEL, SESSION_SHORT, type AnySession, type LapRow, type ResultRow, type StintRow } from "../data";
import { useProfiles, type ProfileTarget } from "../profiles";
import { SiteContext } from "../site";
import { logoUrl, teamLogo, teamShort } from "../teams";
import { Chart, plotDefaults } from "./ui";

export const COMPOUND: Record<string, { name: string; color: string; ink: string }> = {
  S: { name: "Soft", color: "#da291c", ink: "#fff" },
  M: { name: "Medium", color: "#ffd12e", ink: "#111" },
  H: { name: "Hard", color: "#d9d9d6", ink: "#111" },
  I: { name: "Intermediate", color: "#43b02a", ink: "#fff" },
  W: { name: "Wet", color: "#0067ad", ink: "#fff" },
  "?": { name: "Unknown", color: "#7f7f7f", ink: "#fff" },
};
const LONG_TO_SHORT: Record<string, string> = { SOFT: "S", MEDIUM: "M", HARD: "H", INTERMEDIATE: "I", WET: "W" };
export const compoundKey = (c: string) => LONG_TO_SHORT[c] ?? (COMPOUND[c] ? c : "?");

/** Black or white text, whichever reads on `bg`. */
function inkOn(bg: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(bg);
  if (!m) return "#fff";
  const n = parseInt(m[1], 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "#111" : "#fff";
}

/** A driver's code in their team colour. A click opens their profile (components/Profiles.tsx): any chip on
 * this season's pages, and on the History pages a chip given the driver's `historyRef` (codes repeat across
 * eras). `plain`: never a button (inside another control, or a picker). */
export function DriverChip({ code, color: bg, title, historyRef, plain }: {
  code: string; color?: string | null; title?: string; historyRef?: string | null; plain?: boolean;
}) {
  const p = useProfiles();
  const c = bg || "var(--chip)";
  const style = { background: c, color: bg ? inkOn(bg) : "var(--ink)" };
  const target: ProfileTarget | null = plain || !p ? null
    : historyRef ? { kind: "history-driver", ref: historyRef } : p.scope === "season" ? { kind: "driver", code } : null;
  if (!target) return <span className="club" style={style} title={title}>{code}</span>;
  return (
    <button type="button" className="club club-open" style={style} title={title ?? `${code}: profile`}
            onClick={(e) => { e.stopPropagation(); p!.open(target); }} onKeyDown={(e) => e.stopPropagation()}>
      {code}
    </button>
  );
}

/** A sentence with this season's drivers shown as their chips: a code ("VER") becomes the chip, a full name
 * ("Max Verstappen") gets the chip in front. `colors` gives the session's own team colours (a driver who
 * changed team); any other code is looked up in the standings, and a three-letter word that isn't a driver
 * ("DNF", "VSC") stays text. Not for History (codes repeat across eras). */
export function DriverText({ text, colors }: { text: string; colors?: Map<string, string> | Record<string, string> }) {
  const site = useContext(SiteContext);
  const colorOf = (c: string) => (colors instanceof Map ? colors.get(c) : colors?.[c]) ?? site?.driver.get(c)?.color;
  const byName = new Map((site?.drivers ?? []).map((d) => [d.name, d.driver]));
  const names = [...byName.keys()].sort((x, y) => y.length - x.length).map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`${names.map((n) => `${n}|`).join("")}\\b[A-Z]{3}\\b`, "g");
  const parts: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    const code = byName.get(m[0]) ?? m[0];
    const col = colorOf(code);
    if (!col) continue;
    if (m.index! > last) parts.push(text.slice(last, m.index));
    parts.push(<DriverChip key={m.index} code={code} color={col} />);
    if (code !== m[0]) parts.push(` ${m[0]}`);
    last = m.index! + m[0].length;
  }
  if (!parts.length) return <>{text}</>;
  parts.push(text.slice(last));
  return <>{parts}</>;
}

/** Which kind of session: Grand Prix, Sprint, Qualifying, Sprint Qualifying (text and a tint, never colour alone). */
export function SessionBadge({ code, short }: { code: AnySession; short?: boolean }) {
  return <span className={`session-badge session-${code}`} title={SESSION_LABEL[code]}>{short ? SESSION_SHORT[code] : SESSION_LABEL[code]}</span>;
}

export function TeamDot({ color: c }: { color?: string | null }) {
  return <span className="team-dot" style={{ background: c ?? "var(--neutral)" }} aria-hidden />;
}

/** A team's logo on a light tile edged in its colour, or (no free logo) its colour with a short code.
 *  `year` picks the era's logo and tells apart teams that shared a name (Lotus); without it, the latest. */
export function TeamBadge({ team, color: bg, year }: { team: string; color?: string | null; year?: number | null }) {
  const logo = teamLogo(team, year);
  const c = bg || "var(--neutral)";
  if (logo) {
    return (
      <span className="team-badge team-badge-logo" style={{ borderLeftColor: c }} title={team}>
        <img src={logoUrl(logo)} alt={team} loading="lazy" />
      </span>
    );
  }
  return (
    <span className="team-badge" style={{ background: c, borderLeftColor: c, color: bg ? inkOn(bg) : "#fff" }} title={team} role="img" aria-label={team}>
      {teamShort(team, year)}
    </span>
  );
}

/** A team column's cell: badge, then the name. */
export function TeamName({ team, color: c, year }: { team: string | null | undefined; color?: string | null; year?: number | null }) {
  if (!team) return null;
  return <span className="team-name"><TeamBadge team={team} color={c} year={year} />{team}</span>;
}

export function Tyre({ compound, label }: { compound: string; label?: string }) {
  const k = compoundKey(compound);
  const c = COMPOUND[k];
  return <span className="tyre" style={{ borderColor: c.color }} title={c.name}>{label ?? k}</span>;
}

/** A plan as tyre badges with stint lengths: (M)22 (H)35. */
export function Plan({ stints }: { stints: { compound: string; laps: number }[] }) {
  return (
    <span className="plan">
      {stints.map((s, i) => <span key={i} className="plan-stint"><Tyre compound={s.compound} />{s.laps}</span>)}
    </span>
  );
}

// ---------------------------------------------------------------- shared chart bits

type Neutral = { SC: number[]; VSC: number[]; RED: number[] };

/** Shaded bands behind laps run under the Safety Car (darker), VSC or red flag. */
function neutralBands(n: Neutral): Plot.Markish[] {
  const bands = [
    ...n.SC.map((l) => ({ lap: l, kind: "SC", o: 0.16 })),
    ...n.VSC.map((l) => ({ lap: l, kind: "VSC", o: 0.08 })),
    ...n.RED.map((l) => ({ lap: l, kind: "Red flag", o: 0.16 })),
  ];
  return bands.length
    ? [Plot.rectX(bands, { x1: (d) => d.lap - 1, x2: "lap", fill: (d) => (d.kind === "Red flag" ? "#d03b3b" : "#fab219"), fillOpacity: "o" })]
    : [];
}

/** What the race charts need of a driver: their team colour, and whether they're the team's second car. */
type ChartDriver = Pick<ResultRow, "driver" | "color" | "second_driver">;
type PositionLap = Pick<LapRow, "driver" | "lap" | "pos" | "pit">;

const legendLine = (r: ChartDriver | undefined) => (r?.second_driver ? "4,3" : undefined);
const tyreTip = (l: LapRow) => `${l.driver} · lap ${l.lap}\nP${l.pos} on ${COMPOUND[l.compound]?.name ?? l.compound} (${l.tyre_life ?? "?"} laps old)${l.pit ? "\nPitted" : ""}`;

// ---------------------------------------------------------------- position chart

/** Running order lap by lap, one line per driver, labelled at the end. `tip` defaults to the
 * position and tyre, for this season's race files. */
export function PositionChart<L extends PositionLap>({ laps, results, neutral, highlight, tip }: {
  laps: L[]; results: ChartDriver[]; neutral?: Neutral; highlight?: string | null; tip?: (l: L) => string;
}) {
  const make = useCallback((width: number) => {
    const by = new Map(results.map((r) => [r.driver, r]));
    const drivers = results.map((r) => r.driver);
    const total = Math.max(...laps.map((l) => l.lap));
    const fade = (d: string) => (highlight && highlight !== d ? 0.15 : 0.95);
    const last = drivers.map((d) => laps.filter((l) => l.driver === d).at(-1)).filter((l): l is L => !!l);
    return Plot.plot({
      ...plotDefaults(width),
      height: Math.max(360, drivers.length * 19),
      marginRight: 44,
      x: { label: "Lap", domain: [0, total], grid: false },
      y: { label: "Position", reverse: true, domain: [1, drivers.length], ticks: Array.from({ length: drivers.length }, (_, i) => i + 1), grid: true },
      color: { type: "identity" },
      marks: [
        ...(neutral ? neutralBands(neutral) : []),
        ...drivers.map((d) =>
          Plot.line(laps.filter((l) => l.driver === d), {
            x: "lap", y: "pos", stroke: by.get(d)?.color ?? color.neutral, strokeWidth: highlight === d ? 3 : 1.75,
            strokeDasharray: legendLine(by.get(d)), strokeOpacity: fade(d), curve: "monotone-x",
          })),
        Plot.dot(laps.filter((l) => l.pit), { x: "lap", y: "pos", r: 2.5, fill: color.surface, stroke: (l: L) => by.get(l.driver)?.color, strokeOpacity: (l: L) => fade(l.driver) }),
        Plot.text(last, { x: "lap", y: "pos", text: "driver", dx: 6, textAnchor: "start", fill: color.ink2, fontSize: 11, fillOpacity: (l: L) => fade(l.driver) }),
        Plot.tip(laps, Plot.pointer({ x: "lap", y: "pos", title: tip ?? ((l: L) => tyreTip(l as unknown as LapRow)) })),
      ],
    });
  }, [laps, results, neutral, highlight, tip]);
  return <Chart make={make} ariaLabel="Running order by lap" />;
}

/** A row of driver chips that picks one out on the race charts (tap again to clear). */
export function DriverPicker({ results, value, onChange }: { results: ChartDriver[]; value: string | null; onChange: (d: string | null) => void }) {
  return (
    <div className="driver-picker" role="group" aria-label="Pick out a driver">
      {results.map((r) => (
        <button key={r.driver} className={value === r.driver ? "on" : undefined} aria-pressed={value === r.driver}
                onClick={() => onChange(value === r.driver ? null : r.driver)}>
          <DriverChip code={r.driver} color={r.color} plain />
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- gap chart

/** Seconds behind the leader at each lap end; capped so a lapped car doesn't squash the rest. */
export function GapChart({ laps, results, neutral, drivers, highlight }: {
  laps: LapRow[]; results: ResultRow[]; neutral: Neutral; drivers: string[]; highlight?: string | null;
}) {
  const make = useCallback((width: number) => {
    const by = new Map(results.map((r) => [r.driver, r]));
    const shown = laps.filter((l) => drivers.includes(l.driver) && l.gap !== null);
    const cap = Math.min(Math.max(...shown.map((l) => l.gap!), 10), 90);
    const fade = (d: string) => (highlight && highlight !== d ? 0.15 : 0.95);
    const last = drivers.map((d) => shown.filter((l) => l.driver === d).at(-1)).filter((l): l is LapRow => !!l && l.gap! <= cap);
    return Plot.plot({
      ...plotDefaults(width),
      height: 380,
      marginRight: 44,
      x: { label: "Lap" },
      y: { label: "Seconds behind the leader", reverse: true, domain: [0, cap], clamp: true, grid: true },
      marks: [
        ...neutralBands(neutral),
        ...drivers.map((d) =>
          Plot.line(shown.filter((l) => l.driver === d), {
            x: "lap", y: "gap", stroke: by.get(d)?.color ?? color.neutral, strokeWidth: highlight === d ? 3 : 1.75,
            strokeDasharray: legendLine(by.get(d)), strokeOpacity: fade(d), clip: true,
          })),
        Plot.text(last, { x: "lap", y: "gap", text: "driver", dx: 6, textAnchor: "start", fill: color.ink2, fontSize: 11 }),
        Plot.tip(shown, Plot.pointer({ x: "lap", y: "gap", title: (l: LapRow) => `${l.driver} · lap ${l.lap}\n+${l.gap!.toFixed(1)} s to the leader` })),
      ],
    });
  }, [laps, results, neutral, drivers, highlight]);
  return <Chart make={make} ariaLabel="Gap to the leader by lap" />;
}

// ---------------------------------------------------------------- tyre strategy chart

/** Each driver's stints as compound-coloured bars, finishing order top to bottom; cliffs marked. */
export function StrategyChart({ stints, results, totalLaps, neutral }: {
  stints: StintRow[]; results: ResultRow[]; totalLaps: number; neutral: Neutral;
}) {
  const make = useCallback((width: number) => {
    const order = results.map((r) => r.driver);
    const bars = stints.map((s) => ({ ...s, k: compoundKey(s.compound) }));
    const cliffs = bars.filter((s) => s.cliff_lap !== null);
    return Plot.plot({
      ...plotDefaults(width),
      height: order.length * 22 + 50,
      marginLeft: 44,
      x: { label: "Lap", domain: [0, totalLaps], grid: true },
      y: { label: null, domain: order, padding: 0.2 },
      marks: [
        ...neutralBands(neutral),
        Plot.barX(bars, {
          x1: (s) => s.first - 1, x2: "last", y: "driver", fill: (s) => COMPOUND[s.k].color, inset: 0.5, rx: 3,
          stroke: color.surface, strokeWidth: 1,
        }),
        Plot.text(bars.filter((s) => s.laps >= 4), {
          x: (s) => (s.first - 1 + s.last) / 2, y: "driver", text: (s) => `${s.k}${s.laps}`,
          fill: (s) => COMPOUND[s.k].ink, fontSize: 10, fontWeight: 600,
        }),
        Plot.dot(cliffs, { x: "cliff_lap", y: "driver", symbol: "triangle", r: 4, fill: (s) => (s.cliff_cause === "weather" ? "#0067ad" : color.ink), stroke: color.surface }),
        Plot.tip(bars, Plot.pointerY({
          x: (s) => (s.first - 1 + s.last) / 2, y: "driver",
          title: (s) => `${s.driver} · stint ${s.stint}: ${COMPOUND[s.k].name}\nLaps ${s.first}–${s.last} (${s.laps})` +
            (s.deg !== null ? `\nDeg ${s.deg >= 0 ? "+" : ""}${s.deg.toFixed(3)} s/lap over ${s.clean_laps} clean laps` : "") +
            (s.cliff_lap !== null ? `\n${s.cliff_cause === "weather" ? "Rain-related drop" : "Tyre cliff"} on lap ${s.cliff_lap} (${s.cliff_lost?.toFixed(1)} s lost)` : ""),
        })),
      ],
    });
  }, [stints, results, totalLaps, neutral]);
  return <Chart make={make} ariaLabel="Tyre strategy by driver" />;
}

// ---------------------------------------------------------------- chance bars

/** Horizontal bars of a chance per entity, in its colour, with the value at the end. */
export function ChanceBars<T>({ data, label, value, colorOf, title, max }: {
  data: T[]; label: (d: T) => string; value: (d: T) => number; colorOf: (d: T) => string | null | undefined;
  title: string; max?: number;
}) {
  const make = useCallback((width: number) => Plot.plot({
    ...plotDefaults(width),
    height: data.length * 24 + 36,
    marginLeft: 48,
    marginRight: 48,
    marginBottom: 30,
    x: { label: null, domain: [0, max ?? Math.max(...data.map(value), 0.05)], tickFormat: "%", ticks: 4, grid: true },
    y: { label: null, domain: data.map(label), padding: 0.25 },
    marks: [
      Plot.barX(data, { x: value, y: label, fill: (d) => colorOf(d) ?? color.neutral, rx: 3 }),
      Plot.text(data, { x: value, y: label, text: (d) => `${(value(d) * 100).toFixed(value(d) < 0.1 ? 1 : 0)}%`, dx: 5, textAnchor: "start", fill: color.ink2, fontSize: 11 }),
    ],
  }), [data, label, value, colorOf, max]);
  return <Chart make={make} ariaLabel={title} />;
}
