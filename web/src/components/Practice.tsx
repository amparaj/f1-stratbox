// A finished practice session (FP1-3): the timesheet, one-lap against long-run pace, and every long
// run lap by lap (site_export.export_practice, modules/practice.py).
import * as Plot from "@observablehq/plot";
import { useCallback, useMemo, useState } from "react";
import { color } from "../colors";
import { rows, type Practice, type PracticeRow, type RunRow } from "../data";
import { dec, lapTime, signed } from "../format";
import { DriverChip, DriverPicker, TeamName, Tyre } from "./f1";
import { Chart, Loading, Note, plotDefaults, Table, Tiles } from "./ui";

interface TraceRow { driver: string; run: string; run_lap: number; lap: number; lap_time: number; fuel_corrected: number; compound: string; tyre_life: number }

export function PracticeBody({ p }: { p: Practice }) {
  const results = useMemo(() => rows<PracticeRow>(p.results), [p]);
  const runs = useMemo(() => rows<RunRow>(p.runs), [p]);
  const trace = useMemo(() => rows<TraceRow>(p.trace), [p]);
  const colorOf = useMemo(() => new Map(results.map((r) => [r.driver, r.color])), [results]);
  const second = useMemo(() => new Set(results.filter((r) => r.second_driver).map((r) => r.driver)), [results]);
  const [pick, setPick] = useState<string | null>(null);
  if (!results.length) return <Loading />;
  const top = results[0];
  return (
    <>
      <Tiles tiles={[
        { label: "Fastest", value: <><DriverChip code={top.driver} color={top.color} /> {lapTime(top.best)}</>, note: top.compound ? `on ${top.compound === "S" ? "Softs" : top.compound === "M" ? "Mediums" : top.compound === "H" ? "Hards" : "?"}` : undefined },
        { label: "Laps run", value: results.reduce((a, r) => a + r.laps, 0), note: `${results.length} drivers` },
        { label: "Long runs", value: runs.length, note: runs.length ? `${new Set(runs.map((r) => r.driver)).size} drivers` : "short runs only" },
      ]} />
      <Note>
        Practice hides fuel loads and engine modes, so these are a guide, not a ranking. One-lap pace is each driver's best clean lap;
        a long run is six or more laps in a row near the run's best, fuel corrected and compared on the same tyres. This weekend's
        practice feeds the forecasts once it's in, at the weight the replays of 2025-26 support.
      </Note>
      <section>
        <h3>The Story</h3>
        <ul className="insights">{p.insights.map((t, k) => <li key={k}>{t}</li>)}</ul>
      </section>
      <section>
        <h3>Timesheet</h3>
        <Table<PracticeRow>
          data={results}
          rowKey={(r) => r.driver}
          sort="position"
          cardTitle={(r) => <><span className="muted">P{r.position}</span> <DriverChip code={r.driver} color={r.color} /> {r.name}</>}
          cardSub={["team"]}
          cardStats={["best", "one_lap", "long_run"]}
          columns={[
            { key: "position", label: "Pos", value: (r) => r.position, numeric: true, rank: true },
            { key: "driver", label: "Driver", value: (r) => r.name ?? r.driver, render: (r) => <><DriverChip code={r.driver} color={r.color} /> {r.name}</> },
            { key: "team", label: "Team", value: (r) => r.team, render: (r) => <TeamName team={r.team} color={r.color} /> },
            { key: "best", label: "Best", value: (r) => r.best, render: (r) => <>{lapTime(r.best)} {r.compound && r.compound !== "?" && <Tyre compound={r.compound} />}</>, numeric: true },
            { key: "gap", label: "Gap", value: (r) => r.gap, render: (r) => (r.gap ? `+${r.gap.toFixed(3)}` : r.best ? "–" : "No time"), numeric: true },
            { key: "laps", label: "Laps", value: (r) => r.laps, numeric: true },
            { key: "one_lap", label: "One-lap", title: "Best clean lap against the field median (negative = faster)", value: (r) => r.one_lap, render: (r) => (r.one_lap == null ? "–" : `${signed(r.one_lap, 2)}%`), numeric: true },
            { key: "long_run", label: "Long run", title: "Long-run pace against the field, fuel corrected and tyres allowed for (negative = faster)", value: (r) => r.long_run, render: (r) => (r.long_run == null ? "–" : `${signed(r.long_run, 2)}%`), numeric: true },
            { key: "long_laps", label: "Run laps", title: "Laps in long runs", value: (r) => r.long_laps, numeric: true },
          ]}
        />
      </section>
      <PaceMap results={results} />
      {trace.length > 0 && (
        <section>
          <h3>Long Runs</h3>
          <p className="muted">Every long run, lap by lap: fuel-corrected lap time against laps into the run, in team colours (the second driver dashed). Pick a driver to bring theirs forward.</p>
          <DriverPicker results={results.filter((r) => trace.some((t) => t.driver === r.driver))} value={pick} onChange={setPick} />
          <RunChart trace={trace} colorOf={colorOf} second={second} pick={pick} />
          <Table<RunRow>
            data={runs}
            rowKey={(r) => `${r.driver}-${r.first}`}
            sort="pace"
            cardTitle={(r) => <><DriverChip code={r.driver} color={colorOf.get(r.driver)} /> <Tyre compound={r.compound} /> laps {r.first}–{r.last}</>}
            cardSub={[]}
            cardStats={["pace", "median", "deg"]}
            columns={[
              { key: "driver", label: "Driver", value: (r) => r.driver, render: (r) => <DriverChip code={r.driver} color={colorOf.get(r.driver)} /> },
              { key: "compound", label: "Tyre", value: (r) => r.compound, render: (r) => <Tyre compound={r.compound} /> },
              { key: "first", label: "Laps", value: (r) => r.first, render: (r) => `${r.first}–${r.last}`, numeric: true },
              { key: "laps", label: "Length", value: (r) => r.laps, numeric: true },
              { key: "median", label: "Median", title: "Median fuel-corrected lap", value: (r) => r.median, render: (r) => lapTime(r.median), numeric: true },
              { key: "deg", label: "Deg", title: "Seconds a lap lost to tyre wear (fuel corrected)", value: (r) => r.deg, render: (r) => (r.deg == null ? "–" : `${signed(r.deg, 3)} s/lap`), numeric: true },
              { key: "pace", label: "Pace", title: "Against the field on the same tyres (negative = faster)", value: (r) => r.pace, render: (r) => `${signed(r.pace, 2)}%`, numeric: true },
            ]}
          />
        </section>
      )}
    </>
  );
}

function PaceMap({ results }: { results: PracticeRow[] }) {
  const both = results.filter((r) => r.one_lap != null && r.long_run != null);
  const make = useCallback((width: number) => Plot.plot({
    ...plotDefaults(width),
    height: 360,
    x: { label: "One-lap pace (%, negative = faster) →", reverse: true, grid: true },
    y: { label: "↑ Long-run pace (%)", reverse: true, grid: true },
    marks: [
      Plot.ruleX([0], { stroke: color.muted, strokeDasharray: "3,3" }),
      Plot.ruleY([0], { stroke: color.muted, strokeDasharray: "3,3" }),
      Plot.dot(both, { x: "one_lap", y: "long_run", fill: "color", r: 6, stroke: color.surface, symbol: (r: PracticeRow) => (r.second_driver ? "diamond" : "circle") }),
      Plot.text(both, { x: "one_lap", y: "long_run", text: "driver", dy: -11, fontSize: 11, fill: color.ink2 }),
      Plot.tip(both, Plot.pointer({ x: "one_lap", y: "long_run", title: (r: PracticeRow) => `${r.driver}\none lap ${signed(r.one_lap, 2)}%\nlong run ${signed(r.long_run, 2)}% over ${r.long_laps} laps` })),
    ],
  }), [both]);
  if (both.length < 3) return null;
  return (
    <section>
      <h3>One Lap or Long Run?</h3>
      <p className="muted">Each driver's one-lap pace against their long-run pace. Top right is quick at both; top left is quicker over a stint than over a lap. Teammates share a colour, the second driver a diamond.</p>
      <Chart make={make} height={360} ariaLabel="One-lap pace against long-run pace" />
    </section>
  );
}

function RunChart({ trace, colorOf, second, pick }: { trace: TraceRow[]; colorOf: Map<string, string>; second: Set<string>; pick: string | null }) {
  const make = useCallback((width: number) => {
    const lo = Math.min(...trace.map((t) => t.fuel_corrected));
    const shown = trace.filter((t) => t.fuel_corrected <= lo * 1.05);
    const faded = (t: TraceRow) => (pick && t.driver !== pick ? 0.15 : 0.9);
    return Plot.plot({
      ...plotDefaults(width),
      height: 340,
      x: { label: "Lap of the run", tickFormat: (d: number) => String(d + 1) },
      y: { label: "Fuel-corrected lap (s)", grid: true },
      marks: [
        // Plot can't vary the dash within one mark: the second drivers get their own, dashed.
        ...[false, true].map((dashed) => Plot.line(shown.filter((t) => second.has(t.driver) === dashed), {
          x: "run_lap", y: "fuel_corrected", z: "run", stroke: (t: TraceRow) => colorOf.get(t.driver) ?? color.neutral,
          strokeDasharray: dashed ? "4,3" : undefined, strokeOpacity: faded, strokeWidth: 1.6 })),
        Plot.tip(shown, Plot.pointer({ x: "run_lap", y: "fuel_corrected", title: (t: TraceRow) => `${t.driver}, lap ${t.lap} (${t.compound}, ${t.tyre_life} laps old)\n${lapTime(t.lap_time)} · fuel corrected ${dec(t.fuel_corrected, 3)} s` })),
      ],
    });
  }, [trace, colorOf, second, pick]);
  return <Chart make={make} height={340} ariaLabel="Long runs lap by lap" />;
}
