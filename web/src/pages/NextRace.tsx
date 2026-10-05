import * as Plot from "@observablehq/plot";
import { useCallback, useMemo } from "react";
import { color } from "../colors";
import { ChanceBars, DriverChip, Plan } from "../components/f1";
import { Chart, Loading, Note, plotDefaults, Table, Tiles } from "../components/ui";
import { forecastFile, rows, type Forecast, type ForecastDriver, type StrategyForecast, type StrategyPlan } from "../data";
import { dec, delta, pct, shortEvent, signed, when } from "../format";
import { useData, useHash, useSite } from "../site";

// Fixed slots by rank in the forecast file, as the dashboard's strategy colours.
const STRATEGY_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7", "#e87ba4", "#008300"];

export default function NextRace() {
  const site = useSite();
  const hash = useHash();
  const upcoming = site.meta.calendar.filter((e) => !e.done_R && site.meta.forecasts.includes(e.round));
  const asked = Number(hash.split("/")[1]);
  const round = upcoming.some((e) => e.round === asked) ? asked : site.meta.next_round ?? upcoming[0]?.round;
  const ev = round ? site.event.get(round) : undefined;
  const forecast = useData<Forecast>(round ? forecastFile(round) : null);
  const drivers = useMemo(() => rows<ForecastDriver>(forecast?.drivers).sort((a, b) => b.p_win - a.p_win), [forecast]);

  if (!ev) return <p>The season is over: no races left to forecast. <a href="#season">The final standings</a></p>;
  if (forecast === undefined) return <Loading />;
  if (forecast === null) return <p>No forecast for {ev.event} yet.</p>;
  const fav = drivers[0];

  return (
    <>
      <h2>Round {ev.round}: {ev.event}</h2>
      <p className="lede">
        {ev.location}, {ev.country} · race {when(ev.race_utc)}{ev.sprint_utc && <> · sprint {when(ev.sprint_utc)}</>}.
        The forecast uses the {forecast.based_on.length} races so far this season.
      </p>
      {upcoming.length > 1 && (
        <div className="toolbar">
          <label>
            Race
            <select value={round} onChange={(e) => { window.location.hash = `next/${e.target.value}`; }}>
              {upcoming.map((e) => <option key={e.round} value={e.round}>{e.round}. {shortEvent(e.event)}{e.round === site.meta.next_round ? " (next)" : ""}</option>)}
            </select>
          </label>
        </div>
      )}

      <Tiles tiles={[
        { label: "Favourite", value: <><DriverChip code={fav.driver} color={fav.color} /> {pct(fav.p_win)}</>, note: `${fav.team} · win chance` },
        { label: "Most likely podium", value: [...drivers].sort((a, b) => b.p_podium - a.p_podium).slice(0, 3).map((d) => d.driver).join(" · ") },
        ...(forecast.strategy ? [{ label: "Fastest strategy", value: <Plan stints={forecast.strategy.strategies[0].stints} />, note: `${forecast.strategy.strategies[0].stops} stop${forecast.strategy.strategies[0].stops === 1 ? "" : "s"} · pit lap${forecast.strategy.strategies[0].pit_laps.length === 1 ? "" : "s"} ${forecast.strategy.strategies[0].pit_laps.join(", ")}` }] : []),
      ]} />

      <section>
        <h3>Who Wins?</h3>
        <p className="muted">
          The race played 10,000 times. Each driver's pace is drawn around their recent race-pace form, with
          retirements at their (shrunk) DNF rate. It doesn't know the grid, upgrades or the circuit.
        </p>
        <div className="grid-2">
          <div>
            <h4 className="sub">Win chance</h4>
            <ChanceBars data={drivers.filter((d) => d.p_win >= 0.002).slice(0, 10)} label={(d) => d.driver} value={(d) => d.p_win} colorOf={(d) => d.color} title="Win chances" />
          </div>
          <div>
            <h4 className="sub">Podium chance</h4>
            <ChanceBars data={[...drivers].sort((a, b) => b.p_podium - a.p_podium).slice(0, 10)} label={(d) => d.driver} value={(d) => d.p_podium} colorOf={(d) => d.color} title="Podium chances" max={1} />
          </div>
        </div>
        <Table<ForecastDriver>
          data={drivers}
          rowKey={(d) => d.driver}
          sort="p_win"
          cardTitle={(d) => <><DriverChip code={d.driver} color={d.color} /> {d.team}</>}
          cardSub={[]}
          cardStats={["p_win", "p_podium", "exp_points"]}
          columns={[
            { key: "driver", label: "Driver", value: (d) => d.driver, render: (d) => <DriverChip code={d.driver} color={d.color} /> },
            { key: "team", label: "Team", value: (d) => d.team },
            { key: "form", label: "Form", title: "Recent race pace against the field median (negative = faster)", value: (d) => -d.form, render: (d) => `${signed(d.form, 2)}%`, numeric: true },
            { key: "p_win", label: "Win", value: (d) => d.p_win, render: (d) => pct(d.p_win), numeric: true },
            { key: "p_podium", label: "Podium", value: (d) => d.p_podium, render: (d) => pct(d.p_podium), numeric: true },
            { key: "p_points", label: "Points", value: (d) => d.p_points, render: (d) => pct(d.p_points), numeric: true },
            { key: "p_dnf", label: "DNF", value: (d) => d.p_dnf, render: (d) => pct(d.p_dnf), numeric: true },
            { key: "exp_pos", label: "Exp. pos", value: (d) => d.exp_pos, render: (d) => dec(d.exp_pos, 1), numeric: true, rank: true },
            { key: "exp_points", label: "Exp. pts", value: (d) => d.exp_points, render: (d) => dec(d.exp_points, 1), numeric: true },
          ]}
        />
      </section>

      {forecast.strategy && <Strategy s={forecast.strategy} />}
    </>
  );
}

function Strategy({ s }: { s: StrategyForecast }) {
  const best = s.strategies[0];
  const bestMean = Math.min(...s.strategies.map((p) => p.mean));
  const colorOf = (name: string) => STRATEGY_COLORS[s.strategies.findIndex((p) => p.name === name) % STRATEGY_COLORS.length];
  return (
    <section>
      <h3>Tyre Strategy</h3>
      <p className="muted">
        Every 1- and 2-stop plan over Soft, Medium and Hard, run lap by lap through the simulator over{" "}
        {s.total_laps} laps with a {s.pit_loss.toFixed(1)} s pit loss{s.pit_loss_known ? "" : " (a default: no figure for this circuit yet)"}.
        {s.calibrated_on
          ? <> How hard the circuit is on tyres comes from the {s.calibrated_on} ({s.severity.toFixed(2)}× the preset wear, including this season's {s.season_factor.toFixed(2)}× change); the split between compounds, their pace gaps and cliffs are fixed assumptions.</>
          : <> No usable wear figure from this circuit last season (no race, or its wear was swamped by the track getting faster), so tyre wear is this season's typical level ({s.severity.toFixed(2)}× the presets).</>}
        {" "}The best plans then go through the Monte Carlo: lap-time noise and random Safety Cars, which
        make a stop cheaper.
      </p>
      <Table<StrategyPlan>
        data={s.strategies}
        rowKey={(p) => p.name}
        cards={false}
        columns={[
          { key: "plan", label: "Plan", value: (p) => p.plan, render: (p) => <span className="plan-cell"><span className="team-dot" style={{ background: colorOf(p.name) }} /><Plan stints={p.stints} /></span> },
          { key: "pit", label: "Pit laps", value: (p) => p.pit_laps.join(", ") },
          { key: "delta", label: "No SC", title: "Time behind the fastest plan in a race with no Safety Car", value: (p) => p.delta, render: (p) => (p.delta === 0 ? "Fastest" : delta(p.delta)), numeric: true },
          { key: "mean", label: "Average", title: "Average time behind the best plan over the simulated races", value: (p) => p.mean - bestMean, render: (p) => (p.mean === bestMean ? "Best" : delta(p.mean - bestMean)), numeric: true },
          { key: "win", label: "Fastest in", title: "Share of simulated races where this plan was the quickest", value: (p) => p.win_prob, render: (p) => pct(p.win_prob), numeric: true },
        ]}
      />
      <StrategyTrace s={s} colorOf={colorOf} />
      <Note>
        Best plan with no Safety Car: {best.plan} ({best.stops} stop{best.stops === 1 ? "" : "s"}, pit on lap {best.pit_laps.join(" and ")}).
        Deg used (s/lap): {Object.entries(s.deg).map(([c, v]) => `${c.toLowerCase()} ${v.toFixed(3)}`).join(", ")}.
      </Note>
    </section>
  );
}

/** Time behind the fastest plan through the race: where each plan loses time in the pits and gains it back. */
function StrategyTrace({ s, colorOf }: { s: StrategyForecast; colorOf: (n: string) => string }) {
  const make = useCallback((width: number) => {
    const trace = rows<{ strategy: string; lap: number; gap_to_best: number }>(s.trace);
    const ends = s.strategies.map((p) => trace.filter((t) => t.strategy === p.name).at(-1)!).filter(Boolean);
    return Plot.plot({
      ...plotDefaults(width),
      height: 320,
      x: { label: "Lap" },
      y: { label: "Seconds behind the fastest plan", grid: true },
      marks: [
        Plot.ruleY([0], { stroke: color.grid }),
        ...s.strategies.map((p) => Plot.line(trace.filter((t) => t.strategy === p.name), { x: "lap", y: "gap_to_best", stroke: colorOf(p.name), strokeWidth: 2 })),
        Plot.dot(ends, { x: "lap", y: "gap_to_best", fill: (t) => colorOf(t.strategy), r: 3 }),
        Plot.tip(trace, Plot.pointer({ x: "lap", y: "gap_to_best", title: (t) => `${t.strategy}\nLap ${t.lap}: ${t.gap_to_best >= 0 ? "+" : ""}${t.gap_to_best.toFixed(1)} s` })),
      ],
    });
  }, [s, colorOf]);
  return <Chart make={make} ariaLabel="Time behind the fastest strategy by lap" />;
}
