// Lap telemetry head to head: any two drivers' fastest laps of a session (telemetry/rNN-<code>.json,
// written by modules/site_telemetry.py). Track dominance map, lap cards (sectors, throttle / braking /
// cornering shares), and speed, delta, throttle, brake, gear, RPM and DRS traces on one distance
// axis, with corner zones by speed class and the time won in each. Hovering a trace moves a
// crosshair on every trace and a marker on the map.

import * as Plot from "@observablehq/plot";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { color } from "../colors";
import { telemetryFile, type SessionCode, type TelLap, type Telemetry } from "../data";
import { lapTime } from "../format";
import { useData } from "../site";
import { DriverChip, Tyre } from "./f1";
import { plotDefaults, Segmented, Table, usePhone } from "./ui";

export interface TelDriver { driver: string; color?: string | null; name?: string | null; team?: string | null }

// Same rules as config.py's FULL_THROTTLE_PCT / CORNER_* (the dashboard's head-to-head).
const FULL_THROTTLE = 98;
const CORNER_GROUP_GAP_M = 150;
const CORNER_ZONE_PAD_M = 100;
const MINI_SECTORS = 25;
const speedClass = (v: number) => (v < 120 ? "Low" : v < 200 ? "Medium" : "High");
const CLASS_SHADE: Record<string, number> = { Low: 0.16, Medium: 0.1, High: 0.05 };
const MARGIN = { marginLeft: 48, marginRight: 12 };

interface Zone {
  i0: number; i1: number; label: string; kind: "corner" | "straight" | "mini"; cls?: string;
  /** Time A gained over the zone (s): + = A faster. */
  delta: number; avgA: number; avgB: number; minA: number; minB: number;
  /** A hole in either car's data runs through it: no verdict. */
  holed: boolean;
}

/** Both cars' holes, overlapping ones merged into one band. */
function holesOf(A: TelLap, B: TelLap): number[][] {
  const all = [...(A.gaps ?? []), ...(B.gaps ?? [])].sort((p, q) => p[0] - q[0]);
  const out: number[][] = [];
  for (const [s, e] of all) {
    const last = out.at(-1);
    if (last && s <= last[1]) last[1] = Math.max(last[1], e); else out.push([s, e]);
  }
  return out;
}

function mean(a: number[], i0: number, i1: number) { let s = 0; for (let i = i0; i <= i1; i++) s += a[i]; return s / (i1 - i0 + 1); }
function min(a: number[], i0: number, i1: number) { let m = Infinity; for (let i = i0; i <= i1; i++) m = Math.min(m, a[i]); return m; }

function zonesOf(tel: Telemetry, A: TelLap, B: TelLap, mode: "mini" | "corners"): Zone[] {
  const n = A.t.length, step = tel.step;
  const gapAt = (i: number) => (B.t[i] - A.t[i]) / 1000;
  const holes = holesOf(A, B);
  const zone = (i0: number, i1: number, label: string, kind: Zone["kind"]): Zone => {
    const minA = min(A.speed, i0, i1), minB = min(B.speed, i0, i1);
    return { i0, i1, label, kind, delta: gapAt(i1) - gapAt(i0), avgA: mean(A.speed, i0, i1), avgB: mean(B.speed, i0, i1),
             minA, minB, cls: kind === "corner" ? speedClass((minA + minB) / 2) : undefined,
             holed: holes.some(([s, e]) => s < i1 * step && e > i0 * step) };
  };
  if (mode === "mini" || !tel.corners.length) {
    return Array.from({ length: MINI_SECTORS }, (_, k) => {
      const i0 = Math.round((k * (n - 1)) / MINI_SECTORS), i1 = Math.round(((k + 1) * (n - 1)) / MINI_SECTORS);
      return zone(i0, i1, `Mini-sector ${k + 1}`, "mini");
    });
  }
  // Corners close together make one zone (Monaco's swimming pool), padded either side; straights between.
  const groups: { label: string; d: number }[][] = [];
  for (const c of tel.corners) {
    const last = groups.at(-1);
    if (last && c.d - last.at(-1)!.d < CORNER_GROUP_GAP_M) last.push(c); else groups.push([c]);
  }
  const bounds = groups.map((g) => ({
    s: Math.max(0, g[0].d - CORNER_ZONE_PAD_M), e: Math.min(tel.length, g.at(-1)!.d + CORNER_ZONE_PAD_M),
    label: g.length === 1 ? `Turn ${g[0].label}` : `Turns ${g[0].label}–${g.at(-1)!.label}`,
  }));
  for (let k = 0; k + 1 < bounds.length; k++) {
    if (bounds[k].e > bounds[k + 1].s) bounds[k].e = bounds[k + 1].s = (bounds[k].e + bounds[k + 1].s) / 2;
  }
  const idx = (d: number) => Math.min(n - 1, Math.max(0, Math.round(d / step)));
  const out: Zone[] = [];
  let cursor = 0;
  for (const b of bounds) {
    if (idx(b.s) > cursor) out.push(zone(cursor, idx(b.s), "Straight", "straight"));
    out.push(zone(idx(b.s), idx(b.e), b.label, "corner"));
    cursor = idx(b.e);
  }
  if (cursor < n - 1) out.push(zone(cursor, n - 1, "Straight", "straight"));
  return out;
}

/** Shares of lap time flat out, braking, and the rest; top and average speed. */
function shares(lap: TelLap, length: number) {
  let flat = 0, brake = 0, total = 0;
  for (let i = 0; i + 1 < lap.t.length; i++) {
    const dt = lap.t[i + 1] - lap.t[i];
    total += dt;
    if (lap.throttle[i] >= FULL_THROTTLE) flat += dt;
    if (lap.brake[i] > 0) brake += dt;
  }
  const f = (flat / total) * 100, b = (brake / total) * 100;
  return { flat: f, brake: b, corner: Math.max(0, 100 - f - b), top: Math.max(...lap.speed), avg: (length / lap.time) * 3.6 };
}

// ---------------------------------------------------------------- the section

export default function LapTelemetry({ round, code, drivers }: { round: number; code: SessionCode; drivers: TelDriver[] }) {
  const tel = useData<Telemetry>(telemetryFile(round, code));
  const order = useMemo(() => (tel ? drivers.filter((d) => tel.drivers[d.driver]) : []), [tel, drivers]);
  const [pick, setPick] = useState<[string | null, string | null]>([null, null]);
  const [mode, setMode] = useState<"mini" | "corners">("mini");
  const [hover, setHover] = useState<number | null>(null);
  if (!tel || order.length < 2) return null;            // no telemetry for this session: no section

  const a = pick[0] && tel.drivers[pick[0]] ? pick[0] : order[0].driver;
  let b = pick[1] && tel.drivers[pick[1]] ? pick[1] : order[1].driver;
  if (b === a) b = order.find((d) => d.driver !== a)!.driver;
  return <HeadToHead tel={tel} order={order} a={a} b={b} mode={mode} setMode={setMode} hover={hover} setHover={setHover}
                     setPick={(x, y) => setPick([x, y])} />;
}

function HeadToHead({ tel, order, a, b, mode, setMode, hover, setHover, setPick }: {
  tel: Telemetry; order: TelDriver[]; a: string; b: string; mode: "mini" | "corners"; setMode: (m: "mini" | "corners") => void;
  hover: number | null; setHover: (i: number | null) => void; setPick: (a: string, b: string) => void;
}) {
  const phone = usePhone();
  const A = tel.drivers[a], B = tel.drivers[b];
  const info = (d: string) => order.find((o) => o.driver === d)!;
  const ca = info(a).color || color.s1;
  const cbRaw = info(b).color || color.s2;
  const same = ca.toLowerCase() === cbRaw.toLowerCase();       // teammates share a colour: B is dashed
  const style = { a: { color: ca, dash: undefined as string | undefined }, b: { color: cbRaw, dash: same ? "5,4" : undefined } };
  const zones = useMemo(() => zonesOf(tel, A, B, mode), [tel, A, B, mode]);
  const cornerZones = useMemo(() => zonesOf(tel, A, B, "corners"), [tel, A, B]);
  const gap = B.time - A.time;
  const known = zones.filter((z) => !z.holed);
  const span = known.reduce((s, z) => s + z.i1 - z.i0, 0) || 1;
  const won = known.reduce((s, z) => s + (z.delta >= 0 ? z.i1 - z.i0 : 0), 0) / span;

  const option = (d: TelDriver) => (
    <option key={d.driver} value={d.driver}>{d.driver} · {d.name ?? d.driver} · {lapTime(tel.drivers[d.driver].time)}</option>
  );
  return (
    <section>
      <h3>Lap Telemetry</h3>
      <p className="muted">
        Any two drivers' fastest laps, on one distance axis from F1's car data (via OpenF1, about four samples a
        second). Hover or drag along a trace to follow both cars round the lap.
      </p>
      <div className="toolbar">
        <label>Driver A <select value={a} onChange={(e) => setPick(e.target.value, b)}>{order.map(option)}</select></label>
        <button className="link" onClick={() => setPick(b, a)} aria-label="Swap drivers">⇄ swap</button>
        <label>Driver B <select value={b} onChange={(e) => setPick(a, e.target.value)}>{order.map(option)}</select></label>
      </div>

      <div className="tel-cards">
        <LapCard d={info(a)} lap={A} other={B} tel={tel} c={style.a} gap={-gap} />
        <LapCard d={info(b)} lap={B} other={A} tel={tel} c={style.b} gap={gap} />
      </div>

      <div className="card tel-map-card">
        <div className="tel-map-head">
          <b>Track dominance</b>
          <span className="muted">
            {gap === 0 ? "Dead level" : <>{gap > 0 ? a : b} faster by {Math.abs(gap).toFixed(3)} s</>} · {a} quicker over {(won * 100).toFixed(0)}% of the lap, {b} {(100 - won * 100).toFixed(0)}%
          </span>
          <Segmented label="Split the lap into" value={mode} onChange={setMode}
                     options={[{ value: "mini", label: `${MINI_SECTORS} mini-sectors` }, ...(tel.corners.length ? [{ value: "corners" as const, label: "Corners" }] : [])]} />
        </div>
        <TrackMap tel={tel} zones={zones} a={a} b={b} style={style} hover={hover} />
      </div>

      <Traces tel={tel} A={A} B={B} a={a} b={b} style={style} zones={cornerZones} hover={hover} setHover={setHover} phone={phone} />

      {tel.corners.length > 0 && (
        <>
          <h4 className="tel-h4">Corner by corner</h4>
          <Table<Zone>
            data={cornerZones.filter((z) => z.kind === "corner")}
            rowKey={(z) => z.label}
            cardTitle={(z) => <>{z.label} <span className="muted">· {z.cls} speed</span></>}
            cardStats={["minA", "minB", "won"]}
            columns={[
              { key: "label", label: "Corner", value: (z) => z.label },
              { key: "cls", label: "Type", value: (z) => z.cls, render: (z) => `${z.cls} speed` },
              { key: "minA", label: `Min ${a}`, short: `${a} km/h`, value: (z) => Math.round(z.minA), numeric: true, group: "Minimum speed (km/h)" },
              { key: "minB", label: `Min ${b}`, short: `${b} km/h`, value: (z) => Math.round(z.minB), numeric: true, group: "Minimum speed (km/h)" },
              { key: "won", label: "Faster", value: (z) => (z.holed ? null : Math.abs(z.delta)), render: (z) => (z.holed ? <span className="muted">data gap</span> : <><DriverChip code={z.delta >= 0 ? a : b} color={z.delta >= 0 ? ca : cbRaw} /> {Math.abs(z.delta).toFixed(3)} s</>), numeric: true },
            ]}
          />
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- lap cards

function LapCard({ d, lap, other, tel, c, gap }: { d: TelDriver; lap: TelLap; other: TelLap; tel: Telemetry; c: { color: string; dash?: string }; gap: number }) {
  const s = shares(lap, tel.length);
  const bar = (label: string, v: number) => (
    <div className="tel-bar">
      <span>{label}</span>
      <div className="tel-bar-track"><i style={{ width: `${v}%`, background: c.color, backgroundImage: c.dash ? "repeating-linear-gradient(90deg, transparent 0 6px, var(--surface) 6px 9px)" : undefined }} /></div>
      <b>{v.toFixed(0)}%</b>
    </div>
  );
  return (
    <div className="card tel-card" style={{ borderTop: `4px ${c.dash ? "dashed" : "solid"} ${c.color}` }}>
      <div className="tel-card-head">
        <DriverChip code={d.driver} color={d.color} /> <b>{d.name ?? d.driver}</b> <span className="muted">{d.team}</span>
      </div>
      <div className="tel-time">
        <span>{lapTime(lap.time)}</span>
        {gap !== 0 && <span className={gap < 0 ? "good" : "bad"}>{gap < 0 ? "−" : "+"}{Math.abs(gap).toFixed(3)} s</span>}
      </div>
      <div className="muted tel-sub">Lap {lap.lap}{lap.compound && <> · <Tyre compound={lap.compound} /></>} · top {s.top} km/h · average {s.avg.toFixed(0)} km/h</div>
      <div className="tel-sectors">
        {lap.sectors.map((v, k) => {
          const o = other.sectors[k];
          const best = v !== null && (o === null || v <= o);
          return <div key={k}><small>S{k + 1}</small><b className={best ? "good" : undefined}>{v === null ? "–" : v.toFixed(3)}</b></div>;
        })}
      </div>
      {bar("Full throttle", s.flat)}
      {bar("Heavy braking", s.brake)}
      {bar("Cornering", s.corner)}
    </div>
  );
}

// ---------------------------------------------------------------- track map

function TrackMap({ tel, zones, a, b, style, hover }: {
  tel: Telemetry; zones: Zone[]; a: string; b: string; style: { a: { color: string; dash?: string }; b: { color: string; dash?: string } };
  hover: number | null;
}) {
  const [tip, setTip] = useState<{ z: Zone; x: number; y: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const { x, y } = tel;
  const minX = Math.min(...x), maxX = Math.max(...x), minY = Math.min(...y), maxY = Math.max(...y);
  const span = Math.max(maxX - minX, maxY - minY), pad = span * 0.08;
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  const pts = (i0: number, i1: number) => {
    let s = "";
    for (let i = i0; i <= i1; i++) s += `${x[i]},${-y[i]} `;
    return s;
  };
  const vb = `${minX - pad} ${-maxY - pad} ${maxX - minX + 2 * pad} ${maxY - minY + 2 * pad}`;
  const font = span * 0.028;
  const move = (z: Zone) => (e: React.PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    setTip({ z, x: e.clientX - r.left, y: e.clientY - r.top });
  };
  return (
    <div className="tel-map" ref={box} onPointerLeave={() => setTip(null)}>
      <svg viewBox={vb} role="img" aria-label={`Track map: where ${a} and ${b} were each faster`}>
        <polyline points={pts(0, x.length - 1)} fill="none" stroke="var(--grid)" strokeWidth={13} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        {zones.map((z, k) => {
          const s = z.holed ? { color: "var(--neutral)", dash: "2,3" } : z.delta >= 0 ? style.a : style.b;
          return (
            <polyline key={k} points={pts(z.i0, z.i1)} fill="none" stroke={s.color} strokeWidth={tip?.z === z ? 11 : 8}
                      strokeDasharray={s.dash ? "4,3" : undefined} strokeLinejoin="round" strokeLinecap={s.dash ? "butt" : "round"}
                      vectorEffect="non-scaling-stroke" onPointerMove={move(z)} style={{ cursor: "pointer" }} />
          );
        })}
        <circle cx={x[0]} cy={-y[0]} r={span * 0.012} fill="var(--ink)" />
        {tel.corners.map((c) => {
          const dx = c.x - cx, dy = c.y - cy, l = Math.hypot(dx, dy) || 1;
          return <text key={c.label} x={c.x + (dx / l) * span * 0.045} y={-(c.y + (dy / l) * span * 0.045)} fontSize={font}
                       textAnchor="middle" dominantBaseline="middle" fill="var(--muted)">{c.label}</text>;
        })}
        {hover !== null && hover < x.length && (
          <circle cx={x[hover]} cy={-y[hover]} r={span * 0.016} fill="var(--surface)" stroke="var(--ink)" strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
        )}
      </svg>
      {tip && (
        <div className="tel-tip" style={{ left: tip.x, top: tip.y }}>
          <div className="muted">{tip.z.label}{tip.z.cls ? ` · ${tip.z.cls} speed` : ""}</div>
          <div><DriverChip code={a} color={style.a.color} /> avg {tip.z.avgA.toFixed(0)} km/h{tip.z.kind === "corner" ? ` · min ${tip.z.minA.toFixed(0)}` : ""}</div>
          <div><DriverChip code={b} color={style.b.color} /> avg {tip.z.avgB.toFixed(0)} km/h{tip.z.kind === "corner" ? ` · min ${tip.z.minB.toFixed(0)}` : ""}</div>
          {tip.z.holed ? <div className="muted">A hole in the car data runs through here: no verdict.</div>
            : <div><b>{tip.z.delta >= 0 ? a : b}</b> faster by {Math.abs(tip.z.delta).toFixed(3)} s</div>}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- traces

/** An Observable Plot chart drawn once per data and width; the hover line is an overlay, so moving
 * the pointer never redraws the plot. */
function Trace({ make, height, hover, setHover, step, n, children }: {
  make: (width: number) => SVGSVGElement | HTMLElement; height: number; hover: number | null;
  setHover: (i: number | null) => void; step: number; n: number; children?: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const scale = useRef<{ apply: (v: number) => number; invert: (v: number) => number } | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const el = plotRef.current;
    if (!el || !width) return;
    const chart = make(width) as SVGSVGElement & { scale: (k: string) => { apply: (v: number) => number; invert: (v: number) => number } };
    el.replaceChildren(chart);
    scale.current = chart.scale("x");
    return () => chart.remove();
  }, [make, width]);
  const pick = (e: React.PointerEvent) => {
    if (!scale.current || !ref.current) return;
    const d = scale.current.invert(e.clientX - ref.current.getBoundingClientRect().left);
    setHover(Math.max(0, Math.min(n - 1, Math.round(d / step))));
  };
  const left = hover !== null && scale.current ? scale.current.apply(hover * step) : null;
  return (
    <div className="tel-trace" ref={ref} style={{ height }} onPointerMove={pick} onPointerDown={pick} onPointerLeave={() => setHover(null)}>
      <div ref={plotRef} className="chart" />
      {left !== null && <div className="tel-cursor" style={{ left }} />}
      {children}
    </div>
  );
}

function Traces({ tel, A, B, a, b, style, zones, hover, setHover, phone }: {
  tel: Telemetry; A: TelLap; B: TelLap; a: string; b: string;
  style: { a: { color: string; dash?: string }; b: { color: string; dash?: string } };
  zones: Zone[]; hover: number | null; setHover: (i: number | null) => void; phone: boolean;
}) {
  const n = A.t.length, step = tel.step;
  const idx = useMemo(() => Array.from({ length: n }, (_, i) => i), [n]);
  const xOf = (i: number) => i * step;
  const delta = useMemo(() => idx.map((i) => (B.t[i] - A.t[i]) / 1000), [idx, A, B]);

  const base = (width: number, height: number, last = false): Plot.PlotOptions => ({
    ...plotDefaults(width), ...MARGIN, height, marginTop: last ? 4 : 20, marginBottom: last ? 34 : 6,
    x: { domain: [0, tel.length], axis: last ? "bottom" : null, label: last ? "Distance from the line (m)" : null },
  });
  const pair = (key: keyof TelLap, curve: Plot.CurveName = "linear") => [
    Plot.line(idx, { x: xOf, y: (i: number) => (A[key] as number[])[i], stroke: style.a.color, strokeWidth: 1.6, curve }),
    Plot.line(idx, { x: xOf, y: (i: number) => (B[key] as number[])[i], stroke: style.b.color, strokeWidth: 1.6, strokeDasharray: style.b.dash, curve }),
  ];
  const corners = zones.filter((z) => z.kind === "corner");
  // Where either car's data has a hole: a red tint and "no data", so nothing there is read as real.
  const holes = holesOf(A, B);
  const holeBands = (y2: number, y1 = 0) => holes.length ? [
    Plot.rect(holes, { x1: (h: number[]) => h[0], x2: (h: number[]) => h[1], y1, y2, fill: color.bad, fillOpacity: 0.1 }),
    Plot.text(holes, { x: (h: number[]) => (h[0] + h[1]) / 2, y: y2, text: () => "no data", fill: color.bad, fontSize: 9, lineAnchor: "top", dy: 2 }),
  ] : [];
  const sectorRules = tel.sector_d ? [Plot.ruleX(tel.sector_d, { stroke: color.muted, strokeDasharray: "3,3" })] : [];

  const speed = useMemo(() => (width: number) => {
    const top = Math.max(...A.speed, ...B.speed);
    const yTop = top * 1.16;
    // Only numbers with room for them (about 34 px), and never through a hole in the data.
    const pxPerM = (width - MARGIN.marginLeft - MARGIN.marginRight) / tel.length;
    const shown = zones.filter((z) => !z.holed && Math.abs(z.delta) >= 0.005 && (z.i1 - z.i0) * step * pxPerM >= 34);
    const sectors = tel.sector_d ? [0, ...tel.sector_d, tel.length] : [];
    return Plot.plot({
      ...base(width, phone ? 240 : 290),
      y: { domain: [0, yTop], label: "Speed (km/h)", grid: true, ticks: 4 },
      marks: [
        Plot.rect(corners, { x1: (z: Zone) => xOf(z.i0), x2: (z: Zone) => xOf(z.i1), y1: 0, y2: top * 1.04, fill: color.ink, fillOpacity: (z: Zone) => CLASS_SHADE[z.cls!] }),
        ...(phone ? [] : [Plot.text(corners, { x: (z: Zone) => (xOf(z.i0) + xOf(z.i1)) / 2, y: top * 1.025, text: (z: Zone) => z.cls!.toUpperCase(), fontSize: 9, fill: color.muted, lineAnchor: "bottom" })]),
        ...sectorRules,
        ...sectors.slice(0, -1).map((s, k) => Plot.text([0], { x: () => (s + sectors[k + 1]) / 2, y: 6, text: () => `S${k + 1}`, fill: color.muted, fontSize: 10, lineAnchor: "bottom" })),
        ...holeBands(top * 1.04),
        ...pair("speed"),
        Plot.text(shown, {
          x: (z: Zone) => (xOf(z.i0) + xOf(z.i1)) / 2, y: top * 1.11, text: (z: Zone) => Math.abs(z.delta).toFixed(3),
          fill: (z: Zone) => (z.delta >= 0 ? style.a.color : style.b.color), fontSize: 10, fontWeight: 600,
        }),
      ],
    });
  }, [tel, A, B, zones, style, phone]);  // eslint-disable-line react-hooks/exhaustive-deps

  const deltaChart = useMemo(() => (width: number) => {
    const lim = Math.max(0.05, ...delta.map(Math.abs)) * 1.15;
    return Plot.plot({
      ...base(width, 140),
      y: { domain: [-lim, lim], label: `Gap (s): + = ${a} ahead`, grid: true, ticks: 4, tickFormat: (v: number) => (v > 0 ? `+${v}` : `${v}`) },
      marks: [
        Plot.areaY(idx, { x: xOf, y: (i: number) => Math.max(0, delta[i]), fill: style.a.color, fillOpacity: 0.25 }),
        Plot.areaY(idx, { x: xOf, y: (i: number) => Math.min(0, delta[i]), fill: style.b.color, fillOpacity: 0.25 }),
        ...holeBands(lim, -lim),
        Plot.ruleY([0], { stroke: color.muted }),
        Plot.line(idx, { x: xOf, y: (i: number) => delta[i], stroke: color.ink, strokeWidth: 1.4 }),
        ...sectorRules,
      ],
    });
  }, [tel, delta, style, a]);  // eslint-disable-line react-hooks/exhaustive-deps

  const simple = (key: keyof TelLap, label: string, height: number, curve: Plot.CurveName = "linear", y: Plot.ScaleOptions = {}) =>
    (width: number) => Plot.plot({ ...base(width, height), y: { label, grid: true, ticks: 3, ...y }, marks: [...sectorRules, ...pair(key, curve)] });
  const throttle = useMemo(() => simple("throttle", "Throttle (%)", 120, "linear", { domain: [0, 100] }), [tel, A, B, style]);  // eslint-disable-line react-hooks/exhaustive-deps
  const brake = useMemo(() => simple("brake", "Brake", 80, "step", { domain: [0, 1], ticks: [0, 1], tickFormat: (v: number) => (v ? "on" : "off") }), [tel, A, B, style]);  // eslint-disable-line react-hooks/exhaustive-deps
  const gear = useMemo(() => simple("gear", "Gear", 120, "step", { domain: [1, 8], ticks: [2, 4, 6, 8], tickFormat: "d" }), [tel, A, B, style]);  // eslint-disable-line react-hooks/exhaustive-deps
  const rpm = useMemo(() => simple("rpm", "RPM", 120, "linear", { tickFormat: (v: number) => `${v / 1000}k` }), [tel, A, B, style]);  // eslint-disable-line react-hooks/exhaustive-deps
  const drs = useMemo(() => simple("drs", "DRS", 70, "step", { domain: [0, 1], ticks: [0, 1], tickFormat: (v: number) => (v ? "open" : "shut") }), [tel, A, B, style]);  // eslint-disable-line react-hooks/exhaustive-deps
  const turns = useMemo(() => (width: number) => Plot.plot({
    ...base(width, 52, true), y: { axis: null, domain: [0, 1] },
    marks: [Plot.ruleX(tel.corners, { x: "d", y1: 0.55, y2: 1, stroke: color.muted }),
            Plot.text(tel.corners, { x: "d", y: 0.3, text: "label", fontSize: 10, fill: color.ink2 })],
  }), [tel]);  // eslint-disable-line react-hooks/exhaustive-deps

  const at = hover ?? null;
  const val = (lap: TelLap, k: keyof TelLap) => (at === null ? null : (lap[k] as number[] | undefined)?.[at]);
  const props = { hover, setHover, step, n };
  return (
    <div className="card tel-traces">
      <div className="tel-readout">
        {at === null ? <span className="muted">Hover a trace to read both cars at that point.</span> : (
          <>
            <span className="muted">{Math.round(at * step).toLocaleString()} m</span>
            {[{ c: a, l: A, s: style.a }, { c: b, l: B, s: style.b }].map(({ c, l, s }) => (
              <span key={c}><DriverChip code={c} color={s.color} /> {val(l, "speed")} km/h · gear {val(l, "gear")} · {val(l, "throttle")}%{val(l, "brake") ? " · braking" : ""}</span>
            ))}
            <span><b>{delta[at] === 0 ? "level" : `${delta[at] > 0 ? a : b} +${Math.abs(delta[at]).toFixed(3)} s`}</b></span>
          </>
        )}
      </div>
      <Trace make={speed} height={phone ? 240 : 290} {...props} />
      <Trace make={deltaChart} height={140} {...props} />
      <Trace make={throttle} height={120} {...props} />
      <Trace make={brake} height={80} {...props} />
      <Trace make={gear} height={120} {...props} />
      <Trace make={rpm} height={120} {...props} />
      {tel.drs && A.drs && B.drs && <Trace make={drs} height={70} {...props} />}
      <Trace make={turns} height={52} {...props} />
      <p className="muted tel-note">
        Shaded: corner zones (darker = slower corner), labelled low / medium / high speed; numbers above them are the
        time won through each zone and each straight, in the faster driver's colour. Dashed lines split the lap into
        sectors. The gap is how far {b} is behind {a} at each point; it ends at the lap-time difference. Red
        "no data" bands are holes in F1's car data feed (it sometimes repeats one reading for seconds); the
        traces are bridged across them and no time is claimed there.
      </p>
    </div>
  );
}
