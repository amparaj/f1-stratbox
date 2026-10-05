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
const FORM_DECAY = 0.75;
const FORM_RACES = 6;
const RACE_SD = 0.45;
const DRIFT_SD = 0.35;
// Pirelli's Hard white is too faint for a line on the light page; config.py's grey stands in for it.
const LINE: Record<keyof typeof PRESETS, string> = { S: COMPOUND.S.color, M: "#e0a800", H: "#9c9c9c" };

const SECTIONS = [
  { id: "architecture", title: "Architecture" },
  { id: "data", title: "Data" },
  { id: "cleaning", title: "Clean laps" },
  { id: "fuel", title: "Fuel correction" },
  { id: "degradation", title: "Tyre degradation" },
  { id: "cliffs", title: "Tyre cliffs" },
  { id: "pace", title: "Race pace" },
  { id: "form", title: "Form" },
  { id: "race", title: "Race forecast" },
  { id: "title", title: "Title odds" },
  { id: "strategy", title: "Tyre strategy" },
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
        clean laps to tyre wear and race pace, and from race pace to the race, title and strategy forecasts. Each
        step is described in words, written out as maths, and worked through with an example.
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
      <Form />
      <RaceForecast />
      <TitleOdds />
      <Strategy />
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
        races into a form figure per driver, play the next race and the rest of the season thousands of times, and
        search for the fastest tyre strategy at the next circuit. Click a box to jump to its section.
      </p>
      <Flow label="The whole system, from timing data to the pages" stages={[
        { label: "Data", join: "one session at a time", nodes: [
          { title: "Laps & stints", text: "lap times, tyre compound and age, pit in and out", kind: "source", section: "data" },
          { title: "Race control", text: "SC, VSC and red flag periods, deleted laps", kind: "source", section: "data" },
          { title: "Weather", text: "rain flag through the race", kind: "source", section: "data" },
          { title: "Results & grid", text: "classification, points, starting grid", kind: "source", section: "data" },
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
        ] },
        { label: "Form", join: "fed into three simulations", nodes: [
          { title: "Driver form", text: "decayed average of the last six race paces, plus each driver's DNF rate", kind: "key", section: "form" },
        ] },
        { label: "Forecast", join: "", nodes: [
          { title: "Race odds", text: "10,000 races: win, podium, points, expected finish", kind: "model", section: "race" },
          { title: "Title odds", text: "10,000 seasons on top of the points so far", kind: "model", section: "title" },
          { title: "Tyre strategy", text: "circuit tyre severity → every 1- and 2-stop plan → Monte Carlo", kind: "model", section: "strategy" },
        ] },
        { label: "Out", nodes: [
          { title: "Season", text: "standings, title odds, calendar", kind: "out" },
          { title: "Race Results & Analysis", text: "result, charts, strategy, deg, forecast vs result", kind: "out" },
          { title: "Next Race Forecast", text: "odds and the best strategies", kind: "out" },
        ], note: <>↺ Every finished race is re-forecast from only the races before it, and shown against the result.</> },
      ]} />
    </Section>
  );
}

// ---------------------------------------------------------------- 2. data

function Data() {
  return (
    <Section id="data">
      <p>
        Lap timing, tyre stints, pit stops, race control messages, weather and the official classification come
        from the <a href="https://openf1.org">OpenF1</a> API. The season's calendar comes from the{" "}
        <a href="https://docs.fastf1.dev/">FastF1</a> library and starting grids from the Jolpica (Ergast) API.
        The History pages use Jolpica's database dump (CC BY-NC-SA 4.0); none of the models below run on it.
        A few things have to be rebuilt from what those sources give:
      </p>
      <ul>
        <li><b>Track status per lap.</b> Race control messages give when each Safety Car, VSC and red flag starts
          and ends; a lap is marked neutralised if any part of it falls inside one of those periods.</li>
        <li><b>Deleted laps.</b> Race control's "... LAP <i>n</i> DELETED" messages mark the lap.</li>
        <li><b>Rain.</b> A lap is wet if the weather feed reported rain at its start or its end.</li>
        <li><b>Running order and gaps.</b> From the time each car crossed the line at the end of every lap (below).</li>
      </ul>
      <p>
        <b>Provisional results.</b> Until the official classification and the grid are both published, the order
        comes from timing: a driver is classified if they completed at least 90% of the winner's laps, and points
        follow that order. The race is shown as provisional until the official figures replace it.
      </p>
      <p>
        <b>Final results are frozen.</b> Four days after a session (once its classification and grid are in) its
        raw data is archived and never downloaded again, so a finished race always shows the same numbers.
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
        Form is a weighted average of a driver's race paces over their last six races. The most recent race counts
        fully and each earlier one 0.75 times the one after it, so form follows a car that's improving without
        being thrown by one bad afternoon. Races a driver has no pace figure for (a DNF, too few clean laps) are
        left out and the weights re-normalised:
      </p>
      <M block t={String.raw`f_d = \frac{\sum_{k=0}^{5} w_k\, p_{d,R-k}}{\sum_{k=0}^{5} w_k}, \qquad w_k = 0.75^{\,k}`} />
      <Figure caption="How much each of the last six races counts towards form.">
        <Chart make={make} height={220} ariaLabel="The weight of each of the last six races in the form figure" />
      </Figure>
      <Example>
        Paces of −0.5%, −0.2% and −0.4% in the last three races (most recent first) give
        <M t={String.raw`\;f = \frac{-0.5 - 0.75 \times 0.2 - 0.5625 \times 0.4}{1 + 0.75 + 0.5625} = -0.38\%`} />.
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
      <p>The next race is played 10,000 times. Each simulated race:</p>
      <Flow label="One simulated race" stages={[
        { label: "In", join: "for each driver", nodes: [
          { title: "Form", text: "from the last six races", kind: "source", section: "form" },
          { title: "DNF rate", text: "their retirements, shrunk to the field's", kind: "source" },
        ] },
        { label: "Draw", join: "", nodes: [
          { title: "Pace on the day", text: <>form + a random draw, spread 0.45%</>, kind: "model" },
          { title: "Retirement?", text: "yes with the driver's DNF rate", kind: "model" },
        ] },
        { label: "Order", join: "× 10,000 races", nodes: [
          { title: "Finishers by pace", text: "quickest wins; retirements score nothing", kind: "key" },
        ] },
        { label: "Out", nodes: [
          { title: "Win, podium, points %", text: "share of races in each place", kind: "out" },
          { title: "Expected finish", text: "a DNF counts as last", kind: "out" },
          { title: "Expected points", text: "25-18-15-12-10-8-6-4-2-1", kind: "out" },
        ] },
      ]} />
      <p>In symbols, for simulation <M t="s" />:</p>
      <M block t={String.raw`x_{d,s} = f_d + \varepsilon_{d,s}, \quad \varepsilon_{d,s} \sim \mathcal{N}\!\left(0,\ 0.45^2\right)`} />
      <M block t={String.raw`\text{DNF}_{d,s} \sim \operatorname{Bernoulli}(q_d)`} />
      <M block t={String.raw`P(\text{win}_d) \approx \frac{1}{S}\sum_{s=1}^{S} \mathbf{1}\big[\text{pos}_{d,s} = 1\big], \qquad S = 10{,}000`} />
      <p>
        The spread of 0.45% is how much a driver's pace really moves from one race to the next: it gave the highest
        likelihood to the actual winners when the season's earlier rounds were replayed. A driver's DNF rate is
        their own record pulled towards the field's, as if they'd had 10 extra starts at the field rate, so one early
        retirement doesn't brand a driver unreliable:
      </p>
      <M block t={String.raw`q_d = \frac{\text{DNF}_d + 10\,\bar q}{\text{starts}_d + 10}, \qquad \bar q = \frac{\sum_d \text{DNF}_d}{\sum_d \text{starts}_d}`} />
      <Figure caption="Why the favourite doesn't always win: three drivers' pace on the day, from forms of −0.5%, −0.2% and +0.3%. The curves overlap, so the slower driver is sometimes quicker.">
        <Legend items={drivers.map((d, j) => ({ label: `${d.name} (form ${d.form > 0 ? "+" : ""}${d.form}%)`, color: series[j], kind: "line" as const }))} />
        <Chart make={make} height={240} ariaLabel="Distributions of simulated race pace for three drivers" />
      </Figure>
      <p>For two drivers alone, the chance one beats the other has a closed form (<M t={String.raw`\Phi`} /> is the standard normal's cumulative chance):</p>
      <M block t={String.raw`P(x_A < x_B) = \Phi\!\left(\frac{f_B - f_A}{\sqrt{2}\times 0.45}\right)`} />
      <Example>
        Form −0.5% against −0.2%: <M t={String.raw`\Phi(0.3 / 0.636) = \Phi(0.47) = 68\%`} />. A driver with 2 DNFs in 8 starts, in a
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
        The rest of the season is played 10,000 times with the same race simulation, on top of the points already
        scored. One extra step: form isn't fixed for the rest of the year (cars get upgrades, teams find or lose their
        way), so in each simulated season every driver's form is shifted once, by a random amount:
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
        The drift's 0.35% comes from this season: the spread of (a driver's average pace over the next seven races
        minus their form now) is 0.41%, part of which is ordinary race-to-race noise. Without it the title odds were
        overconfident: a leader's chance went too high too early. The title chance chart on the Season page re-runs
        this from the standings after every round.
      </p>
    </Section>
  );
}

// ---------------------------------------------------------------- 11. strategy

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
        ], note: <>A 57-lap race has 3,924 plans.</> },
        { label: "Dedupe", join: "best 5 (always the best 1-stop and 2-stop)", nodes: [
          { title: "Best plan per compound set", text: "without a Safety Car, S→H→H takes as long as H→H→S: 10 sets", kind: "model" },
        ] },
        { label: "Monte Carlo", join: "", nodes: [
          { title: "500 races", text: "lap noise 0.30 s; Safety Car in 45%, 3–5 laps", kind: "model" },
          { title: "Same luck for every plan", text: "each race's noise and Safety Car are shared by all five", kind: "model" },
        ] },
        { label: "Out", nodes: [
          { title: "Best strategies", text: "race time, gap to best, P10–P90 range, chance of being fastest", kind: "out" },
        ] },
      ]} />
      <p>In each Monte Carlo race, with lap noise <M t={String.raw`e_n \sim \mathcal{N}(0, 0.30^2)`} /> shared by every plan:</p>
      <M block t={String.raw`\begin{aligned}
t_{\text{SC}}(n) &= \max\big(t(n),\ 1.4\,b_{\min}\big) && \text{laps behind the Safety Car} \\
L_{\text{SC}} &= 0.55\,L && \text{a stop under the Safety Car is cheaper} \\
\text{stop due within 8 laps} &\Rightarrow \text{taken under the Safety Car}
\end{aligned}`} />
      <p>
        Sharing the random draws between plans (common random numbers) means the differences between plans come from
        the plans, not from one getting luckier draws, so 500 races are enough to rank them.
      </p>
      <Example>
        Last season's race here measured severity 1.10; this season's tyres have worn 0.95 times as much at shared
        circuits. So <M t={String.raw`S = 1.10 \times 0.95 = 1.045`} />, and the Medium wears <M t={String.raw`1.045 \times 0.065 = 0.068`} /> s/lap.
      </Example>
    </Section>
  );
}

// ---------------------------------------------------------------- 12. checking

function Checking() {
  return (
    <Section id="checking">
      <p>
        Every finished race is re-forecast using only the races before it, with the current model, and shown next
        to the result under Forecast vs Result: the favourite's chance, the winner's chance and rank, and how many
        of the predicted podium made it. Because they're re-made rather than saved, a change to the model shows up in
        every past round, and its effect on past races can be seen straight away.
      </p>
      <p>
        The two spreads were chosen this way. The race spread (0.45%) maximises the log-likelihood of the
        actual winners across the season's replayed rounds,
      </p>
      <M block t={String.raw`\hat\sigma = \arg\max_\sigma \sum_{r} \ln P_\sigma\big(\text{win}_{\text{winner}(r)}\big)`} />
      <p>
        (the podium Brier score prefers a slightly wider 0.7%), and the form drift (0.35%) from how far form
        really moved over the following seven races.
      </p>
    </Section>
  );
}

// ---------------------------------------------------------------- 13. limits

function Limits() {
  return (
    <Section id="limits">
      <ul>
        <li><b>The race forecast is pace only.</b> It doesn't know the grid, the circuit, upgrades or weather; it's
          "who's been quickest lately", with luck.</li>
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
