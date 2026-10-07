// The technical documentation (#about/docs, or #about/docs/<section> to open at a section): how the race
// analysis and the forecasts work, each step in words, then the maths, then a worked example, with
// diagrams of the flow. Formulas are TeX turned into MathML by KaTeX, which the browser draws itself
// (no fonts or stylesheet to load). Loaded lazily by About.tsx so KaTeX stays out of the main bundle.
// The constants mirror config.py; keep them in step when the model changes.

import * as Plot from "@observablehq/plot";
import katex from "katex";
import { useCallback, useEffect, useMemo, type ReactNode } from "react";
import { color } from "../colors";
import { COMPOUND } from "../components/f1";
import { Checks, Flow } from "../components/Flow";
import { Chart, Legend, plotDefaults } from "../components/ui";
import { useHash } from "../site";

// ---------------------------------------------------------------- model constants (config.py)

const FUEL = 0.033;
const PRESETS = {
  S: { offset: 0.0, deg: 0.11, cliffAge: 18, cliffRate: 0.25 },
  M: { offset: 0.45, deg: 0.065, cliffAge: 30, cliffRate: 0.2 },
  H: { offset: 0.9, deg: 0.04, cliffAge: 42, cliffRate: 0.15 },
} as const;
const FORM_DECAY = 0.65;
const FORM_RACES = 12;
const FORM_CLIP = 0.25;
// Car + driver form (config.py SPLIT_FORM ... TEAMMATE_CLIP).
const SPLIT = { persist: 0.5, decay: 0.9, carry: 0.5, prior: 0.25, clip: 0.15 };
// Practice and upgrades (config.py, fitted by calibrate_forecast.py).
const PRACTICE = { blend: 0.2, clip: 1.0, runLaps: 6, tol: 3.5 };
const UPGRADE = { effect: -0.05, max: 4 };
// Session forecasts (config.py "Session forecasts", fitted by scripts/calibrate_forecast.py).
const SD = { R: 0.35, S: 0.6, Q: 0.35 };
const SD_GRID = { R: 0.2, S: 0.45 };
const BLEND = 0.75;
const BLEND_WEEKEND = 0.85;
const GRID = { R: 0.02, S: 0.2 };
const CIRCUIT = 0;
const DRIFT_ROUND = 0.1;
const RACE_SD = SD.R;
const DRIFT_SD = 0.35;
// Power-unit penalties (config.py "Power-unit penalties", fitted by scripts/calibrate_penalties.py).
const PEN_GRID = 0.05;
const PU = {
  intercept: -4.033, deficit: 1.066, over: -0.91, left: -1.076, circuit: 0.951, prior: 3, plan: 0.6, late: 0.1,
  drop: { back: 0.65, 15: 0.05, 10: 0.14, 5: 0.16 },
  loss: { model: 0.1635, circuit: 0.1726, constant: 0.1792 }, rows: 2272, changes: 97,
};
const WET = { mm: 0.3, heavy: 3, raceMin: 95, drying: 4, sunGain: 0.02, offset: 5.3, years: 10, days: 3 };
const RAIN = {
  light: { inter: 7, wet: 10, slick: 12 },
  heavy: { inter: 20, wet: 14, slick: 28 },
  onDry: { inter: 4.5, wet: 9, degFactor: 3 },
};
// Pirelli's Hard white is too faint for a line on the light page; config.py's grey stands in for it.
const LINE: Record<keyof typeof PRESETS, string> = { S: COMPOUND.S.color, M: "#e0a800", H: "#9c9c9c" };

const SECTIONS = [
  { id: "architecture", title: "Strategy Architecture" },
  { id: "data", title: "Data" },
  { id: "cleaning", title: "Clean laps" },
  { id: "fuel", title: "Fuel correction" },
  { id: "degradation", title: "Tyre degradation" },
  { id: "cliffs", title: "Tyre cliffs" },
  { id: "pace", title: "Race pace" },
  { id: "qualifying", title: "Qualifying analysis" },
  { id: "telemetry", title: "Lap telemetry" },
  { id: "form", title: "Form" },
  { id: "race", title: "Session forecasts" },
  { id: "title", title: "Title odds" },
  { id: "penalties", title: "Penalties" },
  { id: "strategy", title: "Tyre strategy" },
  { id: "weather", title: "Weather" },
  { id: "checking", title: "Checking the forecasts" },
  { id: "limits", title: "Assumptions and limits" },
] as const;

// ---------------------------------------------------------------- building blocks

/** A formula: inline by default, on its own line with `block`. */
function M({ t, block }: { t: string; block?: boolean }) {
  const html = useMemo(() => katex.renderToString(t, { output: "mathml", displayMode: !!block, throwOnError: false }), [t, block]);
  return block
    ? <div className="maths-block" dangerouslySetInnerHTML={{ __html: html }} />
    : <span className="maths-inline" dangerouslySetInnerHTML={{ __html: html }} />;
}

function Section({ id, children }: { id: (typeof SECTIONS)[number]["id"]; children: ReactNode }) {
  const n = SECTIONS.findIndex((s) => s.id === id) + 1;
  return (
    <section id={`docs-${id}`} className="report-section">
      <h3><span className="report-n">{n}</span>{SECTIONS[n - 1].title}</h3>
      {children}
    </section>
  );
}

function Example({ children }: { children: ReactNode }) {
  return <div className="maths-example"><span className="maths-example-label">Example</span>{children}</div>;
}

function Figure({ children, caption }: { children: ReactNode; caption: ReactNode }) {
  return <figure className="docs-figure">{children}<figcaption className="note">{caption}</figcaption></figure>;
}

/** Small, repeatable wobble for the illustrations (the same picture on every load). */
const wobble = (i: number, size: number) => size * (Math.sin(i * 12.9898) * 0.6 + Math.sin(i * 4.1414 + 1) * 0.4);

/** Least-squares line through (x, y): slope, intercept and the residual sum of squares. */
function fit(x: number[], y: number[]) {
  const n = x.length;
  const mx = x.reduce((a, b) => a + b, 0) / n, my = y.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0;
  x.forEach((xi, i) => { sxy += (xi - mx) * (y[i] - my); sxx += (xi - mx) ** 2; });
  const slope = sxy / sxx, icpt = my - slope * mx;
  const sse = x.reduce((s, xi, i) => s + (y[i] - (icpt + slope * xi)) ** 2, 0);
  return { slope, icpt, sse };
}

// ---------------------------------------------------------------- the page

export default function Docs() {
  const hash = useHash();
  // #about/docs/<section> opens at that section.
  useEffect(() => {
    const id = hash.split("/")[2];
    if (id) document.getElementById(`docs-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [hash]);

  return (
    <div className="report">
      <p className="lede">
        How every number on the site is made, in the order the work is done: from raw timing to clean laps, from
        clean laps to tyre wear and race pace, and from race pace to the race, title and strategy forecasts.
      </p>
      <nav className="report-toc" aria-label="Sections">
        {SECTIONS.map((s, i) => <a key={s.id} href={`#about/docs/${s.id}`}>{i + 1}. {s.title}</a>)}
      </nav>
      <p>
        Notation: <M t="t" /> is a lap time in seconds, <M t="a" /> a tyre's age in laps, <M t="n" /> the lap
        number, <M t="d" /> a driver and <M t="c" /> a tyre compound. A hat (<M t={String.raw`\hat\beta`} />) marks
        a fitted value, and <M t={String.raw`\mathcal{N}(0, \sigma^2)`} /> a
        normal random draw with spread <M t={String.raw`\sigma`} />.
      </p>

      <Architecture />
      <Data />
      <Cleaning />
      <Fuel />
      <Degradation />
      <Cliffs />
      <Pace />
      <Qualifying />
      <LapTelemetry />
      <Form />
      <RaceForecast />
      <TitleOdds />
      <Penalties />
      <Strategy />
      <Weather />
      <Checking />
      <Limits />
    </div>
  );
}

// ---------------------------------------------------------------- 1. architecture

function Architecture() {
  return (
    <Section id="architecture">
      <p>
        Two halves. The <b>race analysis</b> looks back: it turns one race's timing into clean laps, then tyre
        wear, tyre cliffs and each driver's pace. The <b>forecasts</b> look ahead: they turn the pace of recent
        races into a form figure per driver, play the next race and the rest of the season thousands of times (with
        the grid penalties announced and the power-unit penalties likely to come), and search for the fastest tyre
        strategy at the next circuit. Click a box to jump to its section.
      </p>
      <Flow label="The whole system, from timing data to the pages" stages={[
        { label: "Data", join: "one session at a time: Qualifying, Sprint Qualifying, Sprint, Grand Prix", nodes: [
          { title: "Laps & stints", text: "lap times, sectors, speed traps, tyre compound and age, pit in and out", kind: "source", section: "data" },
          { title: "Race control", text: "SC, VSC and red flag periods, deleted laps", kind: "source", section: "data" },
          { title: "Weather", text: "track sensors through the race; Open-Meteo forecast and climate ahead", kind: "source", section: "weather" },
          { title: "Results & grid", text: "classification, points, starting grid", kind: "source", section: "data" },
          { title: "Penalties & news", text: "stewards' decisions (f1penalties.com), FIA power-unit documents, F1 news feeds", kind: "source", section: "penalties" },
        ] },
        { label: "Clean", join: "laps that show true pace", nodes: [
          { title: "Lap filters", text: "lap 1, pit laps, neutralised, deleted, slicks in rain, outliers", section: "cleaning" },
          { title: "Fuel correction", text: "+0.033 s per lap run: a full-tank equivalent", section: "fuel" },
          { title: "Traffic", text: "gap to car ahead > 1.5 s = clean air", section: "degradation" },
        ] },
        { label: "Analyse", join: "per race, saved with the race", nodes: [
          { title: "Degradation", text: "a line per driver and compound: s/lap of tyre age", kind: "model", section: "degradation" },
          { title: "Cliffs", text: "a two-piece line per stint", kind: "model", section: "cliffs" },
          { title: "Race pace", text: "% against the field median, per compound", kind: "model", section: "pace" },
          { title: "Qualifying", text: "Q1/Q2/Q3, cut-offs, sectors, track evolution, one-lap pace", kind: "model", section: "qualifying" },
          { title: "Penalties", text: "grid drops announced; each driver's chance of a power-unit penalty at every race left", kind: "model", section: "penalties" },
        ] },
        { label: "Form", join: "fed into three simulations", nodes: [
          { title: "Driver form", text: `race form (Grands Prix and sprints) and qualifying form, this season's last ${FORM_RACES} rounds, the latest counting most, plus each driver's DNF rate`, kind: "key", section: "form" },
        ] },
        { label: "Forecast", join: "", nodes: [
          { title: "Session odds", text: "10,000 runs of each Qualifying, Sprint and Grand Prix; grid penalties applied, a likely one drawn in or out", kind: "model", section: "race" },
          { title: "Title odds", text: "10,000 seasons on top of the points so far; power-unit penalties race by race", kind: "model", section: "title" },
          { title: "Tyre strategy", text: "circuit tyre severity → every 1- and 2-stop plan (a sprint: no-stop too) → Monte Carlo", kind: "model", section: "strategy" },
        ] },
        { label: "Out", nodes: [
          { title: "Current Season", text: "standings, title odds, calendar", kind: "out" },
          { title: "Race Results & Analysis", text: "result, charts, strategy, deg, forecast vs result", kind: "out" },
          { title: "Next Race Forecast", text: "odds per session, qualifying and tyre strategy", kind: "out" },
        ], note: <>↺ Every finished session is re-forecast from only the sessions before it, and shown against the result.</> },
      ]} />
    </Section>
  );
}

// ---------------------------------------------------------------- 2. data

function Data() {
  return (
    <Section id="data">
      <p>
        Lap timing (with sector times and speed traps), tyre stints, pit stops, race control messages, weather and the
        official classification come from the <a href="https://openf1.org">OpenF1</a> API, for every session of a
        weekend: Qualifying and the Grand Prix, and on a sprint weekend Sprint Qualifying and the Sprint. The season's calendar comes from the{" "}
        <a href="https://docs.fastf1.dev/">FastF1</a> library and starting grids from the Jolpica (Ergast) API.
        The History pages use Jolpica's database dump (CC BY-NC-SA 4.0); none of the models below run on it.
        A few things have to be rebuilt from what those sources give:
      </p>
      <ul>
        <li><b>Track status per lap.</b> Race control messages give when each Safety Car, VSC and red flag starts
          and ends; a lap is marked neutralised if any part of it falls inside one of those periods.</li>
        <li><b>Deleted laps.</b> Race control's "... LAP <i>n</i> DELETED" messages mark the lap.</li>
        <li><b>Rain.</b> A lap is wet if the weather feed reported rain at its start or its end. The feed's air and
          track temperature, humidity and wind are shown lap by lap under each race's Weather.</li>
        <li><b>Running order and gaps.</b> From the time each car crossed the line at the end of every lap (below).</li>
      </ul>
      <p>
        <b>Provisional results.</b> Until the official classification and the grid are both published, the order
        comes from timing: a driver is classified if they completed at least 90% of the winner's laps, and points
        follow that order. The race is shown as provisional until the official figures replace it.
      </p>
      <p>
        <b>Final results are frozen.</b> Four days after a session (once its classification and, for a race, its grid
        are in) its raw data is archived and never downloaded again, so a finished session always shows the same numbers.
      </p>
      <p>
        <b>Gaps.</b> When OpenF1 is missing part of a session (2025's Azerbaijan qualifying has no lap timing there),
        the missing part is filled from F1's own live-timing archive, read through FastF1, and the classification of a
        qualifying session OpenF1 hasn't got from Jolpica. Everything OpenF1 does have is kept. Live timing counts
        time from its own session clock, which is tied to UTC by the moment the session started (its status feed
        against race control's "SESSION STARTED"); as a check, its segment ends then land within a second of race
        control's chequered flags. A session filled this way says so on its page.
      </p>
      <p>The gap from driver <M t="d" /> to the car ahead at the end of lap <M t="n" /> is the difference in the session clock <M t="T" /> as they crossed the line:</p>
      <M block t={String.raw`g_{d,n} = T_{d,n} - T_{\text{ahead},n}`} />
      <p>where the car ahead is the one that crossed just before <M t="d" /> among cars on the same lap (the leader's gap is infinite). This uses timing only, no car telemetry.</p>
    </Section>
  );
}

// ---------------------------------------------------------------- 3. cleaning

function Cleaning() {
  return (
    <Section id="cleaning">
      <p>
        A lap time only says something about a car and its tyres if nothing else got in the way. Each lap goes
        through these checks in turn; the laps that pass every one are the <b>clean laps</b> used by everything
        that follows.
      </p>
      <Checks label="The checks a lap goes through to count as clean"
        start={<><span className="flow-title">Every lap of every car</span><span className="flow-text">from the timing data</span></>}
        checks={[
          { test: "Not lap 1?", fail: "Standing start: out" },
          { test: "Not a pit-in or pit-out lap?", fail: "Pit lane time: out" },
          { test: "Green flag throughout (no SC, VSC or red flag)?", fail: "Neutralised: out" },
          { test: "Not deleted, and has a time and a tyre age?", fail: "Deleted or missing: out" },
          { test: "Not on slicks while it was raining?", fail: "Wrong tyre for the track: out" },
          { test: <>Within 7% of the driver's median on that compound?</>, fail: "Outlier (traffic, damage, yellow): out" },
        ]}
        pass={<><span className="flow-title">Clean lap</span><span className="flow-text">used for degradation, cliffs and pace</span></>}
      />
      <p>The outlier check keeps a lap only if it's within 7% of <M t="m_{d,c}" />, the median of driver <M t="d" />'s laps on compound <M t="c" />:</p>
      <M block t={String.raw`t_\ell \le 1.07 \times m_{d,c}`} />
      <Example>
        A driver's median on Mediums is 94.0 s. The cut-off is <M t={String.raw`1.07 \times 94.0 = 100.6`} /> s, so a 101.2 s lap stuck behind a
        backmarker is dropped, and a 95.5 s lap with a small mistake is kept.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- 4. fuel

function Fuel() {
  const make = useCallback((width: number) => {
    const first = 20;
    const data = Array.from({ length: 26 }, (_, i) => {
      const age = i + 1, lap = first + i;
      const raw = 93 + 0.06 * age - FUEL * (lap - 1) + wobble(i + 3, 0.12);
      return { age, lap, raw, fc: raw + FUEL * (lap - 1) };
    });
    const f = fit(data.map((d) => d.age), data.map((d) => d.fc));
    const r = fit(data.map((d) => d.age), data.map((d) => d.raw));
    const long = data.flatMap((d) => [
      { age: d.age, lap: d.lap, t: d.raw, kind: "As timed" },
      { age: d.age, lap: d.lap, t: d.fc, kind: "Fuel corrected" },
    ]);
    const ink = (k: string) => (k === "As timed" ? color.neutral : color.s1);
    return Plot.plot({
      ...plotDefaults(width),
      height: 280,
      marginLeft: 52,
      x: { label: "Tyre age (laps)" },
      y: { label: "Lap time (s)", grid: true },
      marks: [
        Plot.line([1, 26].map((a) => ({ a, t: r.icpt + r.slope * a })), { x: "a", y: "t", stroke: color.neutral, strokeWidth: 2, strokeDasharray: "4,3" }),
        Plot.line([1, 26].map((a) => ({ a, t: f.icpt + f.slope * a })), { x: "a", y: "t", stroke: color.s1, strokeWidth: 2, strokeDasharray: "4,3" }),
        Plot.dot(long, { x: "age", y: "t", r: 3.5, fill: (d) => ink(d.kind), stroke: color.surface, strokeWidth: 1 }),
        Plot.text([{ a: 26, t: r.icpt + r.slope * 26, s: `${r.slope.toFixed(3)} s/lap` }, { a: 26, t: f.icpt + f.slope * 26, s: `${f.slope.toFixed(3)} s/lap` }],
          { x: "a", y: "t", text: "s", dx: 6, textAnchor: "start", fill: color.ink2, fontSize: 11 }),
        Plot.tip(long, Plot.pointer({ x: "age", y: "t", title: (d) => `${d.kind}\nLap ${d.lap}, tyre ${d.age} laps old\n${d.t.toFixed(2)} s` })),
      ],
      marginRight: 80,
    });
  }, []);
  return (
    <Section id="fuel">
      <p>
        A car burns fuel all race, and a lighter car is quicker: about <b>{FUEL} s per lap</b>. Over a stint that
        hides tyre wear, because the car gains from fuel burn while it loses from wear. Adding the fuel gain back
        gives every lap the time it would have done with the starting fuel load:
      </p>
      <M block t={String.raw`t^{\text{fc}}_\ell = t_\ell + 0.033\,(n_\ell - 1)`} />
      <p>What's left of the slope against tyre age is then the tyres alone.</p>
      <Figure caption="An illustration (made-up stint, lap 20 to 45): the timed laps barely get slower, but once the fuel burnt is added back, the tyres are losing about twice as much per lap.">
        <Legend items={[{ label: "As timed", color: color.neutral, kind: "dot" }, { label: "Fuel corrected", color: color.s1, kind: "dot" }]} />
        <Chart make={make} height={280} ariaLabel="Lap times as timed and fuel corrected against tyre age" />
      </Figure>
      <Example>
        Lap 40 timed at 93.10 s becomes <M t={String.raw`93.10 + 0.033 \times 39 = 94.39`} /> s, the time it would have
        been with lap 1's fuel.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- 5. degradation

function Degradation() {
  return (
    <Section id="degradation">
      <p>
        For each driver and compound, a straight line through their fuel-corrected clean laps against tyre age.
        The intercept is the pace on a brand-new tyre; the slope is the <b>degradation rate</b>, the seconds lost
        for every lap the tyre has run:
      </p>
      <M block t={String.raw`t^{\text{fc}}_\ell = \beta_0 + \beta_1\, a_\ell + \varepsilon_\ell`} />
      <p>Ordinary least squares picks the line that minimises the squared misses, which has a closed form:</p>
      <M block t={String.raw`\hat\beta_1 = \frac{\sum_\ell (a_\ell - \bar a)(t^{\text{fc}}_\ell - \bar t)}{\sum_\ell (a_\ell - \bar a)^2}, \qquad \hat\beta_0 = \bar t - \hat\beta_1 \bar a`} />
      <p>
        Following another car costs time (dirty air) and changes how the tyres are used, so laps in <b>clean air</b> are
        preferred: more than 1.5 s behind the car ahead (<M t="g_{d,n} > 1.5" />). Which laps a fit uses:
      </p>
      <Checks label="Which laps a degradation fit uses"
        start={<><span className="flow-title">A driver's clean laps on one compound</span></>}
        checks={[
          { test: "At least 5 in clean air, over 3 or more tyre ages?", fail: "Use all their clean laps instead, if those pass the same test; otherwise no fit" },
        ]}
        pass={<><span className="flow-title">Fit on clean-air laps</span><span className="flow-text">the sample is recorded with the fit</span></>}
      />
      <p>
        The <b>field figure</b> for a compound is the median driver's slope, floored at zero (a negative slope means the
        track was getting faster quicker than the tyres wore, not that tyres improve with age). The spread shown with it
        is half the interquartile range:
      </p>
      <M block t={String.raw`\delta_c = \max\!\Big(0,\ \operatorname{median}\{\, \hat\beta_{1,d,c} : \text{every driver } d \,\}\Big), \qquad \text{spread}_c = \tfrac12\big(Q_3 - Q_1\big)`} />
      <Example>
        Laps of 94.20, 94.31, 94.35, 94.52 and 94.60 s at tyre ages 3 to 7 fit <M t={String.raw`\hat\beta_1 = 0.098`} /> s/lap: after 20
        laps that tyre is about 2 s slower than when new.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- 6. cliffs

function Cliffs() {
  const make = useCallback((width: number) => {
    const ages = Array.from({ length: 24 }, (_, i) => i + 1);
    const t = ages.map((a, i) => 92 + 0.05 * a + Math.max(0, a - 16) * 0.35 + wobble(i + 11, 0.1));
    const one = fit(ages, t);
    let best = { k: 0, sse: Infinity };
    for (let k = 4; k <= ages.length - 3; k++) {
      const sse = fit(ages.slice(0, k), t.slice(0, k)).sse + fit(ages.slice(k), t.slice(k)).sse;
      if (sse < best.sse) best = { k, sse };
    }
    const A = fit(ages.slice(0, best.k), t.slice(0, best.k)), B = fit(ages.slice(best.k), t.slice(best.k));
    const breakAge = ages[best.k];
    const pts = ages.map((a, i) => ({ a, t: t[i] }));
    const seg = (f: ReturnType<typeof fit>, a0: number, a1: number) => [a0, a1].map((a) => ({ a, t: f.icpt + f.slope * a }));
    return Plot.plot({
      ...plotDefaults(width),
      height: 280,
      marginLeft: 52,
      x: { label: "Tyre age (laps)" },
      y: { label: "Fuel-corrected lap time (s)", grid: true },
      marks: [
        Plot.ruleX([breakAge - 0.5], { stroke: color.muted, strokeDasharray: "2,3" }),
        Plot.text([{ a: breakAge - 0.5 }], { x: "a", frameAnchor: "top", text: () => `break: age ${breakAge}`, dx: -4, textAnchor: "end", fill: color.ink2, fontSize: 11 }),
        Plot.line(seg(one, 1, 24), { x: "a", y: "t", stroke: color.neutral, strokeWidth: 2, strokeDasharray: "4,3" }),
        Plot.line(seg(A, 1, breakAge - 1), { x: "a", y: "t", stroke: color.s1, strokeWidth: 2 }),
        Plot.line(seg(B, breakAge, 24), { x: "a", y: "t", stroke: color.s1, strokeWidth: 2 }),
        Plot.dot(pts, { x: "a", y: "t", r: 3.5, fill: color.ink2, stroke: color.surface }),
        Plot.text([{ a: 4, t: A.icpt + A.slope * 4, s: `${A.slope.toFixed(2)} s/lap` }, { a: 22, t: B.icpt + B.slope * 22, s: `${B.slope.toFixed(2)} s/lap` }],
          { x: "a", y: "t", text: "s", dy: -12, fill: color.ink2, fontSize: 11 }),
        Plot.tip(pts, Plot.pointer({ x: "a", y: "t", title: (d) => `Tyre age ${d.a}\n${d.t.toFixed(2)} s` })),
      ],
    });
  }, []);
  return (
    <Section id="cliffs">
      <p>
        A tyre "falls off a cliff" when it stops wearing gently and suddenly loses much more time per lap. To find
        one, every stint with at least 8 clean laps is fitted twice: once with one line, and once with two lines
        that meet at a break. Every break with at least 4 laps before and 3 after is tried; the one with the smallest
        total squared error wins:
      </p>
      <M block t={String.raw`k^\star = \arg\min_{k}\ \Big[\operatorname{SSE}\big(\text{laps}_{<k}\big) + \operatorname{SSE}\big(\text{laps}_{\ge k}\big)\Big]`} />
      <M block t={String.raw`G = 1 - \frac{\operatorname{SSE}_{<k^\star} + \operatorname{SSE}_{\ge k^\star}}{\operatorname{SSE}_{\text{one line}}}`} />
      <p>
        <M t="G" /> is how much of the scatter the break explains. Then the time lost after the break, against the
        trend before it (a falling trend is treated as flat: the tyre is never assumed to keep getting faster):
      </p>
      <M block t={String.raw`L = \sum_{i \ge k^\star} \Big(t^{\text{fc}}_i - \big(\hat s_A\, a_i + \hat b_A\big)\Big), \qquad \hat s_A \leftarrow \max(\hat s_A, 0)`} />
      <Figure caption="An illustration (made-up stint): one line (dashed) misses badly at both ends; two lines (blue) fit, with the slope rising from about 0.05 to 0.4 s/lap at the break.">
        <Legend items={[{ label: "One line", color: color.neutral, kind: "line" }, { label: "Two-piece fit", color: color.s1, kind: "line" }, { label: "Clean lap", color: color.ink2, kind: "dot" }]} />
        <Chart make={make} height={280} ariaLabel="A stint with a tyre cliff, fitted with one line and with two" />
      </Figure>
      <p>A break is only called a cliff if it passes every check. These guards were added after false alarms on real races (a drying track's negative slope flattening out looks like a "break" too):</p>
      <Checks label="The checks a break goes through to count as a tyre cliff"
        start={<><span className="flow-title">Best break in a stint</span><span className="flow-text">slopes <M t={String.raw`\hat s_A`} /> before, <M t={String.raw`\hat s_B`} /> after</span></>}
        checks={[
          { test: <>Slope after at least 0.15 s/lap: <M t={String.raw`\hat s_B \ge 0.15`} />?</>, fail: "Not wearing fast enough after: no cliff" },
          { test: <>Slope rises by at least 0.15 s/lap: <M t={String.raw`\hat s_B - \hat s_A \ge 0.15`} />?</>, fail: "No real change in wear: no cliff" },
          { test: <>Break explains 35% of the scatter: <M t={String.raw`G \ge 0.35`} />?</>, fail: "One line does nearly as well: no cliff" },
          { test: <>At least 1 s lost: <M t={String.raw`L \ge 1.0`} />?</>, fail: "Too small to matter: no cliff" },
          { test: "No rain within 3 laps of the break?", fail: "Shown as a rain-related drop (blue marker)" },
        ]}
        pass={<><span className="flow-title">Tyre cliff</span><span className="flow-text">marked ▲ on the strategy chart, with the time lost</span></>}
      />
    </Section>
  );
}

// ---------------------------------------------------------------- 7. pace

function Pace() {
  return (
    <Section id="pace">
      <p>
        A driver's pace in one race, comparable across compounds. Each fuel-corrected clean lap is divided by the
        median of every driver's clean laps on that compound, so a lap on Hards is only compared with other laps on
        Hards:
      </p>
      <M block t={String.raw`r_\ell = 100\left(\frac{t^{\text{fc}}_\ell}{m_c} - 1\right)\%`} />
      <p>where <M t="m_c" /> is the median fuel-corrected clean lap of the whole field on compound <M t="c" />.</p>
      <p>
        The driver's race pace is the median of their ratios (they need at least 8 clean laps), shifted so the
        median driver is exactly 0. Negative is faster:
      </p>
      <M block t={String.raw`\tilde p_d = \operatorname{median}\{\, r_\ell : \ell \in d \,\}`} />
      <M block t={String.raw`p_d = \tilde p_d - \operatorname{median}_j\, \tilde p_j`} />
      <p>Medians rather than means, so one slow lap or one odd driver doesn't move the figure.</p>
      <Example>
        On a 95 s lap, 1% is 0.95 s. A pace of −0.40% is about 0.38 s a lap quicker than the median driver, some 20 s over a
        55-lap race.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- qualifying

function Qualifying() {
  return (
    <Section id="qualifying">
      <p>
        Qualifying (and Sprint Qualifying, the same format, shorter) runs in three segments; the slowest cars go out
        after Q1 and Q2 (20 cars: 15 then 10 go through; 22 cars: 16 then 10). The laps are split into segments by
        the chequered flag that ends each one: a lap started before the flag counts in that segment.
      </p>
      <ul>
        <li><b>Through or out</b> goes by the classification, not by having a time: a driver can reach Q2 and set none.</li>
        <li><b>Cut-off margin.</b> For a driver who went through, their time against the fastest driver knocked out;
          for one knocked out, against the slowest who went through.</li>
        <li><b>Ideal lap.</b> The driver's best three sectors from any of their valid laps, added up. The time left on
          the table is their best lap minus that.</li>
        <li><b>Track evolution.</b> Between segments: the median change of the same drivers' times from Q1 to Q2 and
          Q2 to Q3. Within a segment: the slope of push laps (within 5% of the driver's best) against the clock, each
          lap taken against the same driver's mean push lap in that segment, so the car's pace drops out:</li>
      </ul>
      <M block t={String.raw`g_q = \frac{\sum (m_i - \bar m_{d})(t_i - \bar t_{d})}{\sum (m_i - \bar m_{d})^2} \quad \text{s per minute}`} />
      <p>
        <b>Qualifying pace</b> puts every driver on one scale although the track gets faster: each segment's times are
        taken against the median time of the drivers who reached Q3 in that same segment, a driver's pace is the best
        of those ratios, and the field median is subtracted (negative = faster). It feeds qualifying form.
      </p>
      <M block t={String.raw`p^{Q}_d = \min_q \left(\frac{t_{d,q}}{\operatorname{med}_{d' \in Q3}\, t_{d',q}} - 1\right) \times 100 \;-\; \text{field median}`} />
      <Example>
        Q1 1:37.041 against a Q3-runners' Q1 median of 1:36.95 is +0.09%; Q2 1:35.959 against 1:36.03 is −0.07%;
        Q3 1:35.631 against 1:35.67 is −0.04%. The best, −0.07%, is the driver's pace before the field median comes off.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- 9. lap telemetry

function LapTelemetry() {
  return (
    <Section id="telemetry">
      <p>
        Every session page compares any two drivers' fastest valid laps from F1's car data, through OpenF1: speed,
        RPM, gear, throttle, brake and DRS about four times a second, and the car's position on the track. Only the
        fastest lap of each driver is fetched (two requests a driver, limited to that lap's time window).
      </p>
      <ul>
        <li><b>One distance axis.</b> A lap's distance is its speed added up over time, <M t={String.raw`s(t) = \int_0^t v\,dt`} />,
          pinned to the line at 0 s and at the lap time. Different lines cover slightly different distances, so every
          lap is scaled to the length of the session's fastest lap and sampled every 10 m. At the same sample both cars
          are at the same point, and the gap there is simply <M t={String.raw`\Delta(s) = t_B(s) - t_A(s)`} />, which ends at the
          lap-time difference.</li>
        <li><b>Track dominance.</b> The lap is cut into 25 equal mini-sectors, or into corner zones and the straights
          between. Each piece is drawn in the colour of the driver who lost less time through it:
          <M t={String.raw`\Delta(s_{	ext{end}}) - \Delta(s_{	ext{start}})`} />.</li>
        <li><b>Corner zones.</b> Corners (positions from the circuit map F1's timing apps use) less than 150 m apart make one
          zone, which runs 100 m either side. A zone is low speed below 120 km/h at its slowest point (the two drivers'
          average), medium below 200, high above.</li>
        <li><b>Lap shares.</b> Full throttle is the share of the lap's time at 98% throttle or more; heavy braking the
          share with the brake on; cornering the rest.</li>
      </ul>
      <p>
        <b>Bad readings.</b> F1's car feed sometimes holds one reading for seconds (a car "doing 308 km/h" well into
        a braking zone): those samples are marked by a throttle and brake of 104, by every channel standing still for a
        second, or by a change of speed no car can make (losing over 220 km/h in a second, about 6 g, or gaining over 80).
        They are dropped. A hole longer than 1.5 s is shaded "no data": the traces are bridged across it, the distance
        covered comes from where the car's position puts it on the track at each end, and no time is claimed there. A
        lap with more than a third missing is left out.
      </p>
      <p>
        Positions come in about four times a second, so a gap of a few hundredths inside one corner is within the
        noise; over a sector or a lap it adds up correctly.
      </p>
    </Section>
  );
}

// ---------------------------------------------------------------- 8. form

function Form() {
  const weights = Array.from({ length: FORM_RACES }, (_, k) => FORM_DECAY ** k);
  const sum = weights.reduce((a, b) => a + b, 0);
  const make = useCallback((width: number) => {
    const data = weights.map((w, k) => ({ race: k === 0 ? "Last race" : `${k} before`, share: w / sum }));
    return Plot.plot({
      ...plotDefaults(width),
      height: 220,
      marginLeft: 44,
      x: { label: null, domain: data.map((d) => d.race), padding: Math.min(0.8, Math.max(0.35, 1 - 40 / ((width - 64) / data.length))) },
      y: { label: "Share of the form figure", tickFormat: "%", grid: true },
      marks: [
        Plot.barY(data, { x: "race", y: "share", fill: color.s1, rx: 3 }),
        Plot.text(data, { x: "race", y: "share", text: (d) => `${(d.share * 100).toFixed(0)}%`, dy: -8, fill: color.ink2, fontSize: 11 }),
        Plot.ruleY([0], { stroke: color.muted }),
        Plot.tip(data, Plot.pointerX({ x: "race", y: "share", title: (d) => `${d.race}: ${(d.share * 100).toFixed(1)}% of the weight` })),
      ],
    });
  }, [weights, sum]);
  return (
    <Section id="form">
      <p>
        Form is a weighted average of a driver's paces over this season's last {FORM_RACES} rounds (never last season's:
        another car, often other rules). The most recent round counts fully and each earlier one {FORM_DECAY} times the
        one after it, so the last three rounds carry about three-quarters of the weight: form follows a car that's
        improving without being thrown by one bad afternoon. There are two: <b>race form</b> from Grand Prix and Sprint race pace (a
        sprint counts 0.75 of a Grand Prix: the replays could barely tell weights from 0 to 1 apart), and
        <b>qualifying form</b> from Qualifying and Sprint Qualifying pace. Sessions a driver has no pace figure for
        (a DNF, too few clean laps) are left out and the weights re-normalised. Before averaging, each pace is held to
        within {FORM_CLIP}% of the driver's median <M t="m_d" /> over those rounds, so one crash, failure or scrappy
        lap can't swing their form:
      </p>
      <M block t={String.raw`f_d = \frac{\sum_{k=0}^{${FORM_RACES - 1}} w_k\, \tilde p_{d,R-k}}{\sum_{k=0}^{${FORM_RACES - 1}} w_k}, \qquad w_k = ${FORM_DECAY}^{\,k}, \qquad \tilde p = \operatorname{clip}\!\left(p,\ m_d - ${FORM_CLIP},\ m_d + ${FORM_CLIP}\right)`} />
      <p>
        Without the cap a single session moved form a long way. At Baku in 2026 Antonelli set only a Q1 banker lap
        (+0.38%, against a median of −0.58%), and as the second most recent round it took his qualifying form from
        −0.53% to −0.39%: enough to turn the championship leader from a favourite into an outsider for every race left.
        Capped, it counts as −0.33%. The cap scored better for every kind of session when 2025 and 2026 were replayed
        (see Checking the forecasts). Each forecast's "Why these odds?" panel lists the sessions behind two drivers'
        form and marks the capped ones.
      </p>
      <p>
        <b>How much recency?</b> Recent races should count most: teams bring parts every few weeks, so March says little
        about September. But a short window is worse, not better: one session's pace is noisy, and replaying 2025 and
        2026 scored forecasts from only the last 2 rounds at −3.07, the last 3 at −2.93, 4 at −2.84, 6 at −2.77 and 12
        (with the latest counting most) at −2.75 (higher is better; see Checking the forecasts). How fast older rounds
        fade barely mattered (a weight of 0.4 to 1.0 per round back all within 0.012). What separates a good forecast
        from a poor one is qualifying more than recency: one-lap pace gets three-quarters of a race's expected pace, and
        once the grid is set the score jumps from −2.53 to −1.72, by far the biggest step in the model.
      </p>
      <Figure caption={`How much each of the last ${FORM_RACES} rounds counts towards form.`}>
        <Chart make={make} height={220} ariaLabel={`The weight of each of the last ${FORM_RACES} rounds in the form figure`} />
      </Figure>
      <p>
        <b>Car and driver.</b> A driver's pace mixes two things that change at different speeds: the car (upgrades
        every few weeks) and the driver (much steadier). For Qualifying, Sprint Qualifying and the Sprint, form is
        split in three. The <b>car</b>'s form is the mean of the team's two cars, each less their driver's rating,
        weighted and capped as above, this season only. The <b>driver</b>'s rating is their pace against their
        teammates: every session's gap between two teammates (held to within {SPLIT.clip}% of that pair's usual gap,
        so one car's failure can't swing it) is the difference of their ratings, fitted together by ridge regression
        since the start of last season. Older rounds fade {SPLIT.decay} a round, last season's count {SPLIT.carry} on
        top, and every rating is pulled towards 0 as if by {SPLIT.prior} of a session. Gaps chain across pairings, so a
        driver who changes team keeps their rating. Last, a <b>streak</b>: how far the driver's own form (above) is
        from car + rating, of which {SPLIT.persist * 100}% is left one round on, {SPLIT.persist ** 2 * 100}% two rounds on:
      </p>
      <M block t={String.raw`f_d = c_{t(d)} + r_d + ${SPLIT.persist}^{\,h}\big(f^{\text{own}}_d - c_{t(d)} - r_d\big), \qquad \min_r \sum_s w_s \big(g_{ab,s} - (r_a - r_b)\big)^2 + ${SPLIT.prior} \sum_d r_d^2`} />
      <p>
        where <M t="h" /> is the rounds ahead and <M t="g_{ab,s}" /> the gap between teammates <M t="a" /> and{" "}
        <M t="b" /> in session <M t="s" />. Replayed on 2025 and 2026, it made qualifying forecasts three rounds ahead
        much better (−2.99 → −2.85), Sprint Qualifying (−3.25 → −3.03) and the Sprint (−3.09 → −3.01) too (11 weekends
        each), and left the next round's qualifying level (−2.718 → −2.725). Without the streak that next round got worse
        (−2.77): a driver's run of form is real and carries into the next session, then fades. The Grand Prix keeps the
        plain form: no setting of the split beat it (three rounds ahead −2.69 → −2.75 at best).
      </p>
      <p>
        <b>This weekend's practice.</b> Once a weekend's practice is in, each driver's best clean lap (against the
        field) moves their qualifying form {PRACTICE.blend * 100}% of the way towards it, but by no more than{" "}
        {PRACTICE.clip}% of pace: a crash or an aborted programme leaves a driver 5–10% off, and unclipped even a tenth
        of that made the forecasts worse. Replayed, qualifying forecasts made after practice scored −2.71 against −2.80
        without it, and races and sprints −2.59 against −2.65 (through qualifying form). Long runs (six or more laps in
        a row, fuel corrected, tyres allowed for) added nothing to the race forecasts at any weight, so they're shown on
        the practice pages but not used: fuel loads and programmes differ too much between teams.
      </p>
      <p>
        <b>Upgrades.</b> On the Thursday the FIA publishes every team's updated parts with the reason (performance,
        circuit specific, reliability). Each performance part makes the team {Math.abs(UPGRADE.effect)}% quicker at
        that round, up to {UPGRADE.max} parts. That's an average: replayed, upgrades helped a little (−2.684 against
        −2.706 with none), but a single team's pace after one moves either way. Next Race shows each team's record.
      </p>
      <Example>
        Paces of −0.5%, −0.2% and −0.4% in the last three races (most recent first) give
        <M t={String.raw`\;f = \frac{-0.5 - 0.65 \times 0.2 - 0.4225 \times 0.4}{1 + 0.65 + 0.4225} = -0.39\%`} />.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- 9. race forecast

function RaceForecast() {
  const drivers = [{ name: "Driver A", form: -0.5 }, { name: "Driver B", form: -0.2 }, { name: "Driver C", form: 0.3 }];
  const series = [color.s1, color.s2, color.s3];
  const make = useCallback((width: number) => {
    const pdf = (x: number, mu: number) => Math.exp(-0.5 * ((x - mu) / RACE_SD) ** 2) / (RACE_SD * Math.sqrt(2 * Math.PI));
    const xs = Array.from({ length: 161 }, (_, i) => -2 + i * 0.025);
    const data = drivers.flatMap((d, j) => xs.map((x) => ({ x, y: pdf(x, d.form), name: d.name, j })));
    return Plot.plot({
      ...plotDefaults(width),
      height: 240,
      x: { label: "Pace drawn for one simulated race (% against the field, faster ←)", domain: [-2, 2] },
      y: { label: null, ticks: [], grid: false },
      marks: [
        ...drivers.map((_, j) => Plot.areaY(data.filter((p) => p.j === j), { x: "x", y: "y", fill: series[j], fillOpacity: 0.12 })),
        ...drivers.map((_, j) => Plot.line(data.filter((p) => p.j === j), { x: "x", y: "y", stroke: series[j], strokeWidth: 2 })),
        Plot.ruleY([0], { stroke: color.muted }),
      ],
    });
  }, []);
  return (
    <Section id="race">
      <p>
        Every session of a weekend gets its own forecast, played 10,000 times: Sprint Qualifying, the Sprint,
        Qualifying and the Grand Prix. Each comes in two versions: before the weekend (from the rounds before), and,
        once earlier sessions of the weekend have run, the latest one (a race then knows its grid). Each simulated session:
      </p>
      <Flow label="One simulated race" stages={[
        { label: "In", join: "for each driver", nodes: [
          { title: "Form", text: "race and qualifying form, this season, the latest rounds counting most", kind: "source", section: "form" },
          { title: "Weekend", text: "this race's qualifying pace and grid, once they're in", kind: "source", section: "qualifying" },
          { title: "DNF rate", text: "their retirements, shrunk to the field's", kind: "source" },
        ] },
        { label: "Draw", join: "", nodes: [
          { title: "Pace on the day", text: <>expected pace + a random draw (Grand Prix spread {SD.R}%, Sprint {SD.S}%, Qualifying {SD.Q}%)</>, kind: "model" },
          { title: "Retirement?", text: "yes with the driver's DNF rate", kind: "model" },
        ] },
        { label: "Order", join: "× 10,000 races", nodes: [
          { title: "Finishers by pace", text: "quickest wins; retirements score nothing", kind: "key" },
        ] },
        { label: "Out", nodes: [
          { title: "Win, podium, points %", text: "share of races in each place", kind: "out" },
          { title: "Expected finish", text: "a DNF counts as last", kind: "out" },
          { title: "Expected points", text: "25-18-…-1, a sprint 8-7-…-1", kind: "out" },
          { title: "Qualifying", text: "pole, front row, Q3, out in Q1, expected grid", kind: "out" },
        ] },
      ]} />
      <p>The expected pace depends on the session. Qualifying uses qualifying form <M t="f^Q_d" />. A race blends race form with qualifying form, and once its own qualifying is done, with that session's pace <M t="p^{Q*}_d" /> plus a step per grid place <M t="g_d" />:</p>
      <M block t={String.raw`\mu_d = (1-${BLEND})\,f^R_d + ${BLEND}\,f^Q_d + \gamma\,n_d \qquad \text{after qualifying: } \mu_d = (1-${BLEND_WEEKEND})\,f^R_d + ${BLEND_WEEKEND}\,p^{Q*}_d + \gamma\,(g_d - \bar g)`} />
      <p>
        with <M t="\gamma" /> = {GRID.R}% per place in a Grand Prix and {GRID.S}% in a Sprint (overtaking is harder over
        a third of the distance, so the grid decides more). One-lap pace turned out to say more about the next race than
        recent race pace does (the replays put {BLEND * 100}% of the weight on it).
      </p>
      <p>
        <b>Grid penalties.</b> The grid <M t="g_d" /> is the official one once the race has run. Before that it's
        OpenF1's published starting grid, which has the grid penalties in (power-unit and gearbox changes, carried-over
        penalties; against every 2025–26 race it matched the official grid but for late pit-lane starts). Until that's
        out it's the qualifying order with any announced penalties applied. Before qualifying, an announced penalty costs
        the places it is expected to lose, <M t="n_d" />: its size, but no further than the back from where the driver's
        pace puts them (all the way for a back-of-the-grid or pit-lane start). Power-unit penalties are announced by the
        FIA's "New PU elements" document on the Friday, read automatically; others are kept by hand (config.py{" "}
        <code>GRID_PENALTIES</code>). A place lost to a penalty counts {PEN_GRID}% rather than <M t="\gamma" />, before
        the grid and after it (a slot behind where the driver qualified): <M t="\gamma" /> is fitted on ordinary grids,
        where the slot and the pace go together, and undervalues a penalised car, which is faster than its slot. In 94
        power-unit penalty starts (2020–25) drivers finished 0.237 places worse per grid place dropped; {PEN_GRID}% gives
        that in the simulation, <M t="\gamma" /> about 0.1. A penalty that isn't announced yet comes in as a chance (see{" "}
        <a href="#about/docs/penalties">Penalties</a>). A sprint's grid has no such penalties.
        Then, for simulation <M t="s" />:
      </p>
      <M block t={String.raw`x_{d,s} = \mu_d + w\,c_{\text{team}(d)} + \delta_{d,s} + \varepsilon_{d,s}, \quad \delta_{d,s} \sim \mathcal{N}\!\left(0,\ (${DRIFT_ROUND}\sqrt{h-1})^2\right), \quad \varepsilon_{d,s} \sim \mathcal{N}\!\left(0,\ \sigma_{\text{session}}^2\right)`} />
      <p>
        <M t="c" /> is the circuit term: how much faster or slower the team was at this circuit last season than its
        own season median, weighted <M t={`w = ${CIRCUIT}`} /> (switched off: the replays found nothing in it, and
        last season's car isn't this one). <M t="h" /> is how many
        rounds ahead the session is: a round further away has its form drift too, so its odds are flatter. Once a race's
        grid is in, the spread on the day drops to {SD_GRID.R}% (Grand Prix) and {SD_GRID.S}% (Sprint).
      </p>
      <M block t={String.raw`\text{DNF}_{d,s} \sim \operatorname{Bernoulli}(q_d)`} />
      <M block t={String.raw`P(\text{win}_d) \approx \frac{1}{S}\sum_{s=1}^{S} \mathbf{1}\big[\text{pos}_{d,s} = 1\big], \qquad S = 10{,}000`} />
      <p>
        The spreads are how much a driver's result really moves around their expected pace: they gave the highest
        likelihood to what happened when 2025 and 2026 were replayed (see Checking the forecasts). A sprint retires a
        driver at 0.4 times their Grand Prix rate, and qualifying has a 1% chance of no time. A driver's DNF rate is
        their own record pulled towards the field's, as if they'd had 10 extra starts at the field rate, so one early
        retirement doesn't brand a driver unreliable:
      </p>
      <M block t={String.raw`q_d = \frac{\text{DNF}_d + 10\,\bar q}{\text{starts}_d + 10}, \qquad \bar q = \frac{\sum_d \text{DNF}_d}{\sum_d \text{starts}_d}`} />
      <Figure caption="Why the favourite doesn't always win: three drivers' pace on the day, from forms of −0.5%, −0.2% and +0.3%. The curves overlap, so the slower driver is sometimes quicker.">
        <Legend items={drivers.map((d, j) => ({ label: `${d.name} (form ${d.form > 0 ? "+" : ""}${d.form}%)`, color: series[j], kind: "line" as const }))} />
        <Chart make={make} height={240} ariaLabel="Distributions of simulated race pace for three drivers" />
      </Figure>
      <p>For two drivers alone, the chance one beats the other has a closed form (<M t={String.raw`\Phi`} /> is the standard normal's cumulative chance):</p>
      <M block t={String.raw`P(x_A < x_B) = \Phi\!\left(\frac{\mu_B - \mu_A}{\sqrt{2}\times ${SD.R}}\right)`} />
      <Example>
        Expected pace −0.5% against −0.2% before the weekend: <M t={String.raw`\Phi(0.3 / 0.495) = \Phi(0.61) = 73\%`} />. A driver with 2 DNFs in 8 starts, in a
        field retiring 10% of the time, gets <M t={String.raw`q = (2 + 1)/(8 + 10) = 17\%`} />, not 25%.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- 10. title odds

function TitleOdds() {
  return (
    <Section id="title">
      <p>
        The rest of the season is played 10,000 times with the same simulation, every Sprint and Grand Prix left (each
        with its own circuit term), on top of the points already scored. One extra step: form isn't fixed for the rest
        of the year (cars get upgrades, teams find or lose their way), so in each simulated season every driver's form
        is shifted once, by a random amount:
      </p>
      <Flow label="One simulated season" stages={[
        { label: "Start", join: "once per season", nodes: [
          { title: "Points so far", text: "every driver's total today", kind: "source" },
          { title: "Form and DNF rate", kind: "source", section: "form" },
        ] },
        { label: "Season", join: "for each race and sprint left", nodes: [
          { title: "Form drift", text: "form + a random shift, spread 0.35%, kept all season", kind: "model" },
        ] },
        { label: "Races", join: "× 10,000 seasons", nodes: [
          { title: "Simulated race", text: "as in the race forecast", kind: "model", section: "race" },
          { title: "Points", text: "race 25…1, sprint 8…1, added to the totals", kind: "model" },
        ] },
        { label: "Out", nodes: [
          { title: "Title chance", text: "share of seasons each driver ends on top", kind: "out" },
          { title: "Projected points", text: "average final total", kind: "out" },
          { title: "Constructors", text: "the same, adding up both drivers", kind: "out" },
        ] },
      ]} />
      <M block t={String.raw`\tilde f_{d,s} = f_d + \eta_{d,s}, \quad \eta_{d,s} \sim \mathcal{N}\!\left(0,\ ${DRIFT_SD}^2\right)`} />
      <M block t={String.raw`\text{Points}_{d,s} = \text{Points}^{\text{now}}_d + \sum_{\text{races left}} \text{pts}\big(\text{pos}_{d,s,r}\big), \qquad P(\text{title}_d) \approx \frac{1}{S}\sum_s \mathbf{1}\Big[d = \arg\max_{d'} \text{Points}_{d',s}\Big]`} />
      <p>
        Power-unit penalties are drawn the same way, race by race: in each simulated season a driver takes one at a Grand
        Prix with its chance there (<a href="#about/docs/penalties">Penalties</a>), lower at the rounds after once
        they have (a fresh pool), and loses the places it costs from where their pace puts them.
      </p>
      <p>
        The drift's 0.35% comes from this season: the spread of (a driver's average pace over the next seven races
        minus their form now) is 0.41%, part of which is ordinary race-to-race noise. Without it the title odds were
        overconfident: a leader's chance went too high too early. The title chance chart on the Season page re-runs
        this from the standings after every round.
      </p>
    </Section>
  );
}

// ---------------------------------------------------------------- 11. penalties

function Penalties() {
  return (
    <Section id="penalties">
      <p>Every kind of penalty reaches the site, each in its own way:</p>
      <ul>
        <li><b>Grid penalties for a race ahead</b>: a power-unit or gearbox change past the allocation, a drop carried
          over from causing a collision in the race before, one handed out in qualifying (impeding, ignoring a red
          flag), or a pit-lane start for changing the car under parc fermé. They move the driver back on the grid
          before the race is played, at {PEN_GRID}% of pace a place (<a href="#about/docs/race">Session forecasts</a>,
          Grid penalties). Before qualifying they come from the FIA's "New PU elements" document (power units, read
          automatically) or are kept by hand (config.py <code>GRID_PENALTIES</code>: no free feed has the stewards'
          decisions that fast); after qualifying, from OpenF1's published starting grid, which has them all applied;
          after the race, the official grid.</li>
        <li><b>Power-unit penalties nobody has announced yet</b>: a chance at every Grand Prix left, from each driver's
          elements, the circuit, the team's word and the news (below).</li>
        <li><b>Penalties in a race</b> (time penalties, drive-throughs, stop-and-gos, disqualification): a finished
          race's result is the official classification, with them applied, so the points, the standings and the title
          odds' starting point have them. Form doesn't: race pace is measured from lap times, which a time penalty
          doesn't change, so a driver's speed isn't marked down for a penalty. In a race still to come they aren't
          forecast one by one; they're part of the spread every simulated race has.</li>
        <li><b>Deleted laps</b> (track limits): left out of the clean laps, and in qualifying a deleted lap doesn't count
          for the segment times or qualifying pace, as it doesn't for the classification.</li>
        <li><b>Sprints</b>: power-unit penalties are served in the Grand Prix; a Sprint's grid has only the drops the
          stewards hand out for the Sprint itself, once OpenF1's starting grid has them.</li>
        <li><b>Reprimands, penalty points and fines</b> are listed under Stewards &amp; Power Units on each session page
          but don't enter the forecasts (a race ban at 12 points would be added by hand, like a grid penalty).</li>
      </ul>
      <h4 className="sub">Power-unit penalties</h4>
      <p>
        Each season a driver may use a set number of each power-unit element (2026: four combustion engines,
        turbochargers and exhausts, three MGU-Ks, energy stores and control electronics, six ancillary sets). Every
        element past that costs grid places in the Grand Prix where it's fitted: 10 for the first of a kind, 5 for each
        one after, and the back of the grid past 15. Teams choose when: usually a circuit where overtaking is easy, often
        a whole new unit at once, sometimes with an upgrade. Two free sources say who is where:
      </p>
      <ul>
        <li><b>The FIA's documents.</b> At every event the Technical Delegate publishes each driver's count of every
          element so far, and which new ones are fitted there, with the allocation ("the fifth of the four ... allowed").
          The site reads both PDFs (matched by car number), every round since 2022, and archives each event four days
          after its race. An over-allocation element in the Friday document is an announced penalty for that race.</li>
        <li><b><a href="https://www.f1penalties.com/data" target="_blank" rel="noreferrer">f1penalties.com</a></b>: every
          stewards' decision since 2020 (time, grid and drive-through penalties, power-unit and parc fermé grid drops,
          reprimands, points, fines), which the session pages list and the model's circuits and drop sizes come from. It
          runs days to weeks behind the FIA, so every update fetches it again; a season is archived once it's over.</li>
      </ul>
      <p>
        <b>The chance of one at each race left.</b> Logistic, fitted on {PU.rows.toLocaleString()} driver-rounds of 2022–26
        with {PU.changes} over-allocation changes, from the state before the round:
      </p>
      <M block t={String.raw`\operatorname{logit}\, h_{d,r} = ${PU.intercept} + ${PU.deficit}\,D_{d,r} ${PU.over}\,O_{d,r} ${PU.left}\,L_r + ${PU.circuit}\,\ln c_r`} />
      <M block t={String.raw`D_{d,r} = \max_e \Big( \tfrac{R-r+1}{R}\,A_e - (A_e - u_{d,e}) \Big)`} />
      <p>
        <M t="D" /> is how many elements short the driver is: the allocation <M t="A_e" /> they'd use on the rounds left at
        the allocation's own rate, minus what's left of it after the <M t="u_{d,e}" /> used (the worst element). <M t="O" />{" "}
        is 1 once they've already gone past it this season: the next round's chance drops, as a fresh pool needs nothing
        for a while. <M t="L" /> is the share of the season left. <M t="c_r" /> is the circuit's factor: how many more
        power-unit penalties were taken there (2020 to last season) than at the average race, mixed with {PU.prior} races of
        "average" (Spa ×2.4, Austin ×2.1, Monza ×2.0, Mexico City ×1.7; Singapore ×0.6, Monaco ×0.4). Left one season out,
        the model scores a log loss of {PU.loss.model} against {PU.loss.circuit} for the circuit alone and {PU.loss.constant}{" "}
        for a constant.
      </p>
      <p>
        Round by round, the chance of a penalty at <M t="r" /> is <M t="h" /> if they haven't taken one in the rounds before
        it, and the after-one chance (one element fewer short, <M t="O = 1" />) if they have:
      </p>
      <M block t={String.raw`p_{d,r} = \prod_{k<r}(1-h_{d,k})\cdot h_{d,r} + \Big(1-\prod_{k<r}(1-h_{d,k})\Big)\cdot h'_{d,r}`} />
      <p>
        <b>Reported plans.</b> When a team says when it will take one (config.py <code>PU_PLANS</code>, with the source),
        each round named gets at least {Math.round(PU.plan * 100)}% until it's taken: one penalty, at the first of those rounds it doesn't
        slip past. Once a round's "New PU elements" document is out, a driver it lists past the allocation is certain to
        take the penalty there, and anyone else keeps {Math.round(PU.late * 100)}% of their chance (a change after qualifying).
      </p>
      <p>
        <b>Reported in the news.</b> The headlines and summaries of eight F1 news feeds (The Race, Crash.net, Autosport,
        Motorsport.com, Formula1.com, BBC Sport, Sky Sports, GPFans) are read on every update. A sentence that names a
        driver, a race still to run and a power-unit penalty (and no "won't", "avoid" or "no penalty") is a claim; when
        two different sites make the same claim within ten days, it counts like a team's plan: at least{" "}
        {Math.round(PU.plan * 100)}% at that race until the penalty is taken, independent of any other plan. The site
        checks the feeds at least hourly (more often over a race weekend) and updates the forecasts as soon
        as a new penalty headline appears. A news plan is used only for forecasts made now, not when past rounds are
        re-forecast.
      </p>
      <p>
        <b>What it costs.</b> The places come from how big such penalties turn out to be (f1penalties.com, 2022 on): the back
        of the grid {Math.round(PU.drop.back * 100)}% of the time (pit-lane starts included), 15 places {Math.round(PU.drop[15] * 100)}%, 10{" "}
        {Math.round(PU.drop[10] * 100)}%, 5 {Math.round(PU.drop[5] * 100)}%, each no further than the back from where the driver's pace puts them,
        at {PEN_GRID}% of pace a place. The race forecast draws it (all or nothing) in each simulated race; the title odds
        draw it race by race with the after-one chance once taken.
      </p>
      <Example>
        After Malaysia (round 16) George Russell had used four combustion engines, turbochargers and exhausts and three of
        everything else: the whole allocation, with seven rounds left, so <M t="D" /> = 7/23 × 4 = 1.2 elements short.
        The model alone gives 3% at Singapore, 7% at Austin and 24% for the rest of the season; Mercedes said they'd take it with their upgraded
        engine at Austin or Mexico City, so with the plan it's 58% at Austin, 24% at Mexico City and 86% for the season.
        Then Sky Sports and Crash.net both reported that his Malaysia failure means a fresh engine and a penalty at
        Singapore: a reported plan there, so 61% at Singapore, and the forecast plays that race with him at the back
        in 61% of the simulated runs.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- 12. strategy

function Strategy() {
  const make = useCallback((width: number) => {
    const ages = Array.from({ length: 51 }, (_, a) => a);
    const keys = ["S", "M", "H"] as const;
    const data = keys.flatMap((k) => ages.map((a) => {
      const p = PRESETS[k];
      return { k, a, t: p.offset + p.deg * a + Math.max(0, a - p.cliffAge) * p.cliffRate };
    }));
    const ends = keys.map((k) => data.filter((d) => d.k === k).at(-1)!);
    return Plot.plot({
      ...plotDefaults(width),
      height: 300,
      marginRight: 64,
      x: { label: "Tyre age (laps)" },
      y: { label: "Seconds slower than a new Soft", grid: true, domain: [0, 8] },
      marks: [
        ...keys.map((k) => Plot.line(data.filter((d) => d.k === k), { x: "a", y: "t", stroke: LINE[k], strokeWidth: 2, clip: true })),
        ...keys.map((k) => Plot.dot([{ a: PRESETS[k].cliffAge, t: PRESETS[k].offset + PRESETS[k].deg * PRESETS[k].cliffAge }], { x: "a", y: "t", r: 4, fill: LINE[k], stroke: color.surface, strokeWidth: 2 })),
        Plot.text(ends.filter((d) => d.t <= 8), { x: "a", y: "t", text: (d) => COMPOUND[d.k].name, dx: 6, textAnchor: "start", fill: color.ink2, fontSize: 11 }),
        Plot.tip(data, Plot.pointer({ x: "a", y: "t", title: (d) => `${COMPOUND[d.k].name}, ${d.a} laps old\n+${d.t.toFixed(2)} s against a new Soft` })),
      ],
    });
  }, []);
  return (
    <Section id="strategy">
      <p>
        The strategy forecast simulates the race lap by lap for every sensible plan and keeps the quickest. The hard
        part is knowing how much the tyres will wear at a circuit before anyone has raced there this year.
      </p>

      <h4 className="sub">How hard the circuit is on tyres</h4>
      <p>
        One race's fit for a single compound is too noisy to plan on: a compound only a few drivers ran, late in the
        race, can fit with no wear at all, and the track getting faster skews every intercept. So each circuit gets a
        single number, its <b>tyre severity</b>: last season's measured wear there against the preset wear rates,
        averaged over the dry compounds that at least 4 drivers have a fit on, weighted by how many:
      </p>
      <M block t={String.raw`S_{\text{race}} = \frac{\sum_c n_c\, \delta_c / \delta^{\text{preset}}_c}{\sum_c n_c}`} />
      <p>
        <M t="S = 1" /> means exactly the presets; a severity under 0.25 means the wear was swamped by track
        evolution, and that race isn't used. Tyres and cars change from one season to the next, so last season's figure is
        scaled by how this season compares at the circuits both seasons have raced (median ratio, kept between ×0.5 and ×2):
      </p>
      <M block t={String.raw`F = \operatorname{clip}\!\left(\operatorname{median}_k \frac{S^{\text{this}}_k}{S^{\text{last}}_k},\ 0.5,\ 2\right), \qquad S = S^{\text{last}}_{\text{circuit}} \times F`} />
      <p>A circuit with no usable race last season gets this season's median severity. Each compound's wear is then its preset times <M t="S" />:</p>
      <M block t={String.raw`\delta_c = S \times \delta^{\text{preset}}_c`} />

      <h4 className="sub">The lap-time model</h4>
      <p>A lap on compound <M t="c" /> at tyre age <M t="a" /> on lap <M t="n" />:</p>
      <M block t={String.raw`t(n) = \underbrace{b + o_c}_{\text{new tyre}} + \underbrace{\delta_c\, a}_{\text{wear}} + \underbrace{\gamma_c \max(0,\ a - A_c)}_{\text{cliff}} - \underbrace{0.033\,(n-1)}_{\text{fuel}} + \underbrace{L\ \text{on an in-lap}}_{\text{pit stop}}`} />
      <div className="table-wrap docs-table">
        <table>
          <thead><tr><th>Compound</th><th className="num">Pace on new tyre <M t="o_c" /></th><th className="num">Preset wear <M t={String.raw`\delta^{\text{preset}}_c`} /></th><th className="num">Cliff age <M t="A_c" /></th><th className="num">Extra wear past it <M t={String.raw`\gamma_c`} /></th></tr></thead>
          <tbody>
            {(["S", "M", "H"] as const).map((k) => (
              <tr key={k}>
                <td>{COMPOUND[k].name}</td>
                <td className="num">+{PRESETS[k].offset.toFixed(2)} s</td>
                <td className="num">{PRESETS[k].deg.toFixed(3)} s/lap</td>
                <td className="num">{PRESETS[k].cliffAge} laps</td>
                <td className="num">+{PRESETS[k].cliffRate.toFixed(2)} s/lap</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Figure caption="The presets at severity 1: time lost against a new Soft as each compound ages. Dots mark the cliff age, after which wear speeds up.">
        <Legend items={(["S", "M", "H"] as const).map((k) => ({ label: COMPOUND[k].name, color: LINE[k], kind: "line" as const }))} />
        <Chart make={make} height={300} ariaLabel="Lap time lost against tyre age for Soft, Medium and Hard" />
      </Figure>
      <p>
        The base <M t="b" /> is the same for every plan, so it cancels when plans are compared. <M t="L" /> is the
        circuit's pit-lane loss.
      </p>

      <h4 className="sub">The search</h4>
      <Flow label="The strategy search, from every plan to the best few" stages={[
        { label: "Plans", join: "each run lap by lap, no Safety Car", nodes: [
          { title: "1-stop", text: "two different compounds, every stop lap (stints ≥ 8 laps)" },
          { title: "2-stop", text: "three stints using at least two compounds, stops every 2 laps" },
          { title: "Sprint", text: "no stop on any compound, and 1-stops (a sprint has no two-compound rule)" },
        ], note: <>A 57-lap race has 3,924 plans.</> },
        { label: "Dedupe", join: "best 5 (always the best 1-stop and 2-stop; a sprint's best no-stop and 1-stop)", nodes: [
          { title: "Best plan per compound set", text: "without a Safety Car, S→H→H takes as long as H→H→S: 10 sets", kind: "model" },
        ] },
        { label: "Monte Carlo", join: "", nodes: [
          { title: "500 races", text: "lap noise 0.30 s; Safety Car in 45%, 3–5 laps; one weather scenario each", kind: "model", section: "weather" },
          { title: "Same luck for every plan", text: "each race's noise, Safety Car and weather are shared by all five", kind: "model" },
        ] },
        { label: "Out", nodes: [
          { title: "Best strategies", text: "race time, gap to best, P10–P90 range, chance of being fastest", kind: "out" },
        ] },
      ]} />
      <p>
        A <b>Sprint</b> gets its own search: its distance is the fewest laps past 100 km (a Grand Prix runs past 305 km,
        so about a third of the laps), with the same circuit tyre wear, pit loss and weather (for the sprint's own start
        time). With no compulsory stop and no rule to use two compounds, running the whole sprint on one set usually
        wins: a stop costs the full pit loss over too few laps to win it back.
      </p>
      <p>In each Monte Carlo race, with lap noise <M t={String.raw`e_n \sim \mathcal{N}(0, 0.30^2)`} /> shared by every plan:</p>
      <M block t={String.raw`\begin{aligned}
t_{\text{SC}}(n) &= \max\big(t(n),\ 1.4\,b_{\min}\big) && \text{laps behind the Safety Car} \\
L_{\text{SC}} &= 0.55\,L && \text{a stop under the Safety Car is cheaper} \\
\text{stop due within 8 laps} &\Rightarrow \text{taken under the Safety Car}
\end{aligned}`} />
      <p>
        Sharing the random draws between plans (common random numbers) means the differences between plans come from
        the plans, not from one getting luckier draws, so 500 races are enough to rank them. Each race also draws one
        weather scenario, and the expected track temperature moves the wear (see <a href="#about/docs/weather">Weather</a>).
      </p>
      <Example>
        Last season's race here measured severity 1.10; this season's tyres have worn 0.95 times as much at shared
        circuits. So <M t={String.raw`S = 1.10 \times 0.95 = 1.045`} />, and the Medium wears <M t={String.raw`1.045 \times 0.065 = 0.068`} /> s/lap.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- weather

function Weather() {
  return (
    <Section id="weather">
      <p>
        Weather enters the strategy forecast in two ways: the chance and timing of rain, and the track temperature.
        Both come from <a href="https://open-meteo.com/">Open-Meteo</a> (free, no key), at the circuit's coordinates.
      </p>
      <Flow label="From a weather forecast to the strategy Monte Carlo" stages={[
        { label: "Source", join: "hourly rain at the circuit", nodes: [
          { title: "Ensemble forecast", text: "up to 16 days ahead: 31–40 versions of the weather (ICON, then GFS)", kind: "source" },
          { title: "Climate", text: `further ahead: the race's hours on ±${WET.days} days of its date, last ${WET.years} years (ERA5)`, kind: "source" },
        ] },
        { label: "Scenarios", join: "one per version or day", nodes: [
          { title: "Rain spell", text: `wet laps: ≥ ${WET.mm} mm/h (heavy from ${WET.heavy}); dry ${WET.drying} laps after it stops`, kind: "model" },
        ] },
        { label: "Simulator", join: "", nodes: [
          { title: "Each simulated race", text: "draws a scenario; every plan sees the same one", kind: "model", section: "strategy" },
        ] },
      ]} />

      <h4 className="sub">Hours to laps</h4>
      <p>
        A forecast gives rain per hour; the race is taken to last {WET.raceMin} minutes, so lap <M t="n" /> of <M t="N" /> is
        run at <M t={String.raw`t_n = t_0 + (n - \tfrac12)\,${WET.raceMin}/N`} /> minutes. The rain rate at <M t="t_n" /> is
        interpolated between the middles of the hours, so rain doesn't only start on the hour. The first wet lap starts
        the spell; the track is dry again {WET.drying} laps after the last wet one. Later spells are ignored.
      </p>

      <h4 className="sub">Rain in the lap-time model</h4>
      <p>On top of the dry lap time, a lap costs (seconds):</p>
      <div className="table-wrap docs-table">
        <table>
          <thead><tr><th>Track</th><th className="num">Slicks</th><th className="num">Intermediates</th><th className="num">Wets</th></tr></thead>
          <tbody>
            <tr><td>Light rain</td><td className="num">+{RAIN.light.slick}</td><td className="num">+{RAIN.light.inter} on <M t={String.raw`b_{\min}`} /></td><td className="num">+{RAIN.light.wet} on <M t={String.raw`b_{\min}`} /></td></tr>
            <tr><td>Heavy rain</td><td className="num">+{RAIN.heavy.slick}</td><td className="num">+{RAIN.heavy.inter} on <M t={String.raw`b_{\min}`} /></td><td className="num">+{RAIN.heavy.wet} on <M t={String.raw`b_{\min}`} /></td></tr>
            <tr><td>Dry</td><td className="num">0</td><td className="num">+{RAIN.onDry.inter}, wear ×{RAIN.onDry.degFactor}</td><td className="num">+{RAIN.onDry.wet}, wear ×{RAIN.onDry.degFactor}</td></tr>
          </tbody>
        </table>
      </div>
      <p>
        <M t={String.raw`b_{\min}`} /> is the fastest dry base pace. At the end of a wet lap, a car on the wrong tyre
        pits for the right one (intermediates in light rain, wets in heavy) unless staying out loses less than the
        stops it would need. With <M t="W" /> wet laps left, a penalty <M t={String.raw`\Delta`} /> per lap against the
        right tyre and <M t="k" /> stops still planned, it pits when
      </p>
      <M block t={String.raw`W\,\Delta > L\,\big(1 + [\text{rain stops before the flag}] - k\big)`} />
      <p>
        (a car that changes for the rain drops its remaining dry stops). Once the track is dry, a car on wet tyres
        pits for the softest slick that lasts to the flag before its cliff, unless staying out costs less than one stop.
      </p>

      <h4 className="sub">Track temperature</h4>
      <p>From the forecast's air temperature <M t="T_a" /> and sunshine <M t="G" /> (W/m²), fitted on the track sensors of 34 dry 2025–26 races (error about 3.5 °C):</p>
      <M block t={String.raw`T_{\text{track}} \approx T_a + ${WET.offset} + ${WET.sunGain}\,G`} />
      <p>
        Tyre wear was calibrated on last season's race at the circuit, so what matters is the change from it. The same
        estimate, from the same source, is made for that race (its archived forecast, or its climate), and the
        difference <M t={String.raw`\Delta T`} /> scales each compound's wear and brings its cliff forward:
      </p>
      <M block t={String.raw`\delta_c' = \delta_c\,(1 + s_c\,\Delta T), \qquad A_c' = A_c / (1 + s_c\,\Delta T)`} />
      <p>with <M t="s_c" /> = 0.025, 0.018 and 0.012 per °C for Soft, Medium and Hard.</p>
      <Example>
        Last season the race here ran on a 33.8 °C track (sensors). The forecast's estimate for this year is
        32.3 °C against 32.6 °C for last year's race, so <M t={String.raw`\Delta T = -0.3`} /> °C and the expected
        track is about 33.5 °C. The Soft's wear changes by <M t={String.raw`1 + 0.025 \times (-0.3) = 0.99`} />.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- 12. checking

function Checking() {
  return (
    <Section id="checking">
      <p>
        Every finished session is re-forecast using only the sessions before it, with the current model, and shown next
        to the result under Forecast vs Result: the favourite's chance, the winner's (or pole sitter's) chance and rank,
        and how many of the predicted podium (or Q3) made it. Because they're re-made rather than saved, a change to the model shows up in
        every past round, and its effect on past races can be seen straight away.
      </p>
      <p>
        The constants were fitted the same way (scripts/calibrate_forecast.py): every session of 2025 and 2026 from round
        2 on was forecast from the sessions before it, before its weekend, a few rounds ahead, and (races) after its
        qualifying, and each constant was moved over a grid, one at a time, to maximise
      </p>
      <M block t={String.raw`\sum_{\text{sessions}} \Big[\ln P(\text{win}_{\text{winner}}) + \tfrac13 \sum_{d \in \text{top 3}} \ln P(\text{top 3}_d)\Big]`} />
      <p>
        What came out: the weekend's own qualifying and grid are worth the most (the Grand Prix score goes from −2.57
        before the weekend to −1.74 after qualifying, the Sprint's from −3.18 to −1.56); qualifying form is worth more
        than race form for the next race; the circuit term doesn't help (0 and 0.25 tie, so it's off), and the horizon
        drift is small. The latest re-fit (with practice and upgrades) also tested recency: a longer form window with the
        latest rounds counting most beat the last two or three rounds alone (Form), this weekend's practice one-lap pace
        improved qualifying forecasts (−2.80 → −2.71) and through them races (−2.65 → −2.59), long runs didn't, and the
        FIA's upgrade lists helped a little (−2.706 → −2.684). Capping outlier sessions in form ({FORM_CLIP}%) helped every kind: on the
        same sessions, the Grand Prix before the weekend −2.574 → −2.558, after qualifying −1.807 → −1.799, the Sprint
        −3.159 → −3.141, Qualifying −2.818 → −2.754 and Sprint Qualifying −3.403 → −3.318. The title-odds drift (0.35%) comes from how far form really moved over
        the following seven races.
      </p>
      <p>
        <b>Against naive forecasts.</b> The same sessions were scored with simple orderings turned into odds by the same
        simulation (each at its best spread): everyone equal, the last session's order, the championship standings, and
        for races after qualifying, the grid. With the plain form the model beat the best of them for the Grand Prix
        (−2.53 against the standings' −2.75 before the weekend, −1.72 against the grid's −2.10 after qualifying) and
        Qualifying (−2.72 against −2.91), but not Sprint Qualifying (−3.25 against −3.03) or Qualifying three rounds
        ahead (−2.99 against −2.95). Long-memory standings beating a short-memory form further out is what led to the
        car and driver split (Form): with it, Qualifying three rounds ahead beats them (−2.85 against −2.95) and Sprint
        Qualifying and the Sprint draw level (−3.03 against −3.03, −3.01 against −3.01). Sprint Qualifying three rounds
        ahead is still behind (−2.92 against −2.57, from 9 weekends).
      </p>
    </Section>
  );
}

// ---------------------------------------------------------------- 13. limits

function Limits() {
  return (
    <Section id="limits">
      <ul>
        <li><b>The forecasts are pace and grid.</b> They know this season's race and qualifying pace, this weekend's
          practice one-lap pace, the grid once it's set, the teams' declared upgrades (on average), grid penalties once
          announced and the chance of a power-unit penalty from each driver's elements and from the news; not last season,
          other penalties nobody has announced yet (an incident in a race ahead, a time penalty) or weather (which only
          enters the strategy forecast). Grid drops other than power units are kept by hand until OpenF1's starting
          grid has them, so one the stewards hand out between races can be missing for a day or two. The power-unit
          model can't see an engine's mileage or damage (a failure that forces a change comes as a surprise until it's
          reported). News is read from headlines and the feeds' summaries: a penalty counts only when two sites name the
          driver and the race. Even with all of it, much of a race (Safety Cars, retirements, a slow stop, the weather on
          the day) stays down to chance: the odds are wide for a reason.</li>
        <li><b>Weather.</b> The rain timeline comes from hourly forecasts at the circuit, so a passing shower can be
          missed, and only the first spell counts. Wet-tyre pace and the drying time are fixed assumptions, and
          teams are assumed to know when the rain stops.</li>
        <li><b>Drivers are independent.</b> Teammates' cars don't share a good or bad day, and one driver's retirement
          doesn't make another's likelier.</li>
        <li><b>Strategy presets are assumptions.</b> The pace gaps between compounds, cliff ages and the Safety Car
          chance are fixed figures, not measured at each circuit; only the overall wear level is.</li>
        <li><b>Pit-lane losses</b> are approximate public figures per circuit.</li>
        <li><b>Traffic.</b> The gap to the car ahead is measured among cars on the same lap, so being held up by a lapped
          car isn't seen as dirty air.</li>
        <li><b>Linear wear.</b> Degradation is a straight line in tyre age (plus a cliff); real wear can curve, and the
          track getting faster through the race is folded into the slope.</li>
      </ul>
    </Section>
  );
}
