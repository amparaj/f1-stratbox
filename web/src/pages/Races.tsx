import { useMemo, useState } from "react";
import { COMPOUND, compoundKey, DriverChip, GapChart, Plan, PositionChart, StrategyChart, Tyre } from "../components/f1";
import { Legend, Loading, Note, Segmented, Table, Tiles } from "../components/ui";
import {
  forecastFile, raceFile, rows, type CalendarEvent, type DegRow, type Forecast, type ForecastDriver, type LapRow,
  type Race, type ResultRow, type StintRow,
} from "../data";
import { dec, gap, lapTime, pct, shortEvent, signed, dayYear } from "../format";
import { useData, useHash, useSite } from "../site";

export default function Races() {
  const hash = useHash();
  const [, roundPart, code] = hash.split("/");
  const round = Number(roundPart);
  if (round) return <RaceView round={round} code={code === "S" ? "S" : "R"} />;
  return <RaceList />;
}

// ---------------------------------------------------------------- the list

function RaceList() {
  const site = useSite();
  const done = site.meta.calendar.filter((e) => e.done_R).reverse();
  return (
    <>
      <h2>Race Results & Analysis</h2>
      <p className="lede">
        Every finished round, latest first. Each race has the result, the running order and gaps lap by lap,
        every driver's tyre strategy and degradation, tyre cliffs, Safety Cars, and the forecast made before
        the race next to what happened.
      </p>
      <Table<CalendarEvent>
        data={done}
        rowKey={(e) => e.round}
        onRow={(e) => { window.location.hash = `races/${e.round}`; }}
        cardTitle={(e) => `${e.round}. ${shortEvent(e.event)}`}
        cardSub={["date"]}
        cardStats={["winner"]}
        columns={[
          { key: "round", label: "Rd", value: (e) => e.round, numeric: true },
          { key: "event", label: "Grand Prix", value: (e) => e.event, render: (e) => <a href={`#races/${e.round}`}>{e.event}</a> },
          { key: "location", label: "Circuit", value: (e) => e.location },
          { key: "date", label: "Date", value: (e) => dayYear(e.race_utc) },
          { key: "sprint", label: "Sprint", value: (e) => (e.done_S ? "Sprint" : ""), render: (e) => (e.done_S ? <a className="tag" href={`#races/${e.round}/S`} onClick={(ev) => ev.stopPropagation()}>Sprint</a> : "") },
          { key: "winner", label: "Winner", value: (e) => e.winner ?? "", render: (e) => <DriverChip code={e.winner!} color={e.winner_color} /> },
        ]}
      />
    </>
  );
}

// ---------------------------------------------------------------- one race

function RaceView({ round, code }: { round: number; code: "R" | "S" }) {
  const site = useSite();
  const ev = site.event.get(round);
  const race = useData<Race>(raceFile(round, code));
  const forecast = useData<Forecast>(code === "R" ? forecastFile(round) : null);
  const results = useMemo(() => rows<ResultRow>(race?.results), [race]);
  const laps = useMemo(() => rows<LapRow>(race?.laps), [race]);
  const stints = useMemo(() => rows<StintRow>(race?.stints), [race]);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [gapScope, setGapScope] = useState<"top" | "all">("top");

  const doneRounds = site.meta.calendar.filter((e) => e.done_R).map((e) => e.round);
  const i = doneRounds.indexOf(round);
  const prev = i > 0 ? doneRounds[i - 1] : null;
  const next = i >= 0 && i < doneRounds.length - 1 ? doneRounds[i + 1] : null;

  if (race === undefined) return <Loading />;
  if (race === null || !ev) return <p>No data for this session yet. <a href="#races">All races</a></p>;

  const winner = results[0];
  const stopsBy = new Map<string, number>();
  for (const s of stints) stopsBy.set(s.driver, Math.max(stopsBy.get(s.driver) ?? 0, s.stint - 1));
  const planBy = new Map<string, { compound: string; laps: number }[]>();
  for (const s of stints) planBy.set(s.driver, [...(planBy.get(s.driver) ?? []), { compound: compoundKey(s.compound), laps: s.laps }]);
  const neutralCount = race.neutralised.SC.length + race.neutralised.VSC.length + race.neutralised.RED.length;
  const topDrivers = results.filter((r) => r.classified).slice(0, 10).map((r) => r.driver);

  return (
    <>
      <p className="crumb">
        <a href="#races">← All races</a>
        {prev && <> · <a href={`#races/${prev}`}>Round {prev}</a></>}
        {next && <> · <a href={`#races/${next}`}>Round {next}</a></>}
      </p>
      <h2>Round {round}: {race.event}{code === "S" ? " Sprint" : ""}</h2>
      <p className="lede">{race.location}, {race.country} · {dayYear(race.start_utc)} · {race.total_laps} laps</p>
      {ev.sprint_utc && ev.done_S && (
        <div className="toolbar">
          <Segmented label="Session" value={code} onChange={(c) => { window.location.hash = `races/${round}${c === "S" ? "/S" : ""}`; }}
                     options={[{ value: "R", label: "Grand Prix" }, { value: "S", label: "Sprint" }]} />
        </div>
      )}
      {!race.complete && (
        <Note>
          Provisional: the official classification (grid, penalties, status) isn't out yet, so points and
          retirements are worked out from the timing data. The site updates itself when it arrives.
        </Note>
      )}

      <Tiles tiles={[
        { label: "Winner", value: <><DriverChip code={winner.driver} color={winner.color} /> {winner.name ?? winner.driver}</>, note: winner.grid ? `${winner.team} · from P${winner.grid}` : winner.team },
        { label: "Fastest lap", value: race.fastest ? lapTime(race.fastest.time) : "–", note: race.fastest ? `${race.fastest.driver}, lap ${race.fastest.lap}` : undefined },
        { label: "Neutralised", value: neutralCount ? `${neutralCount} laps` : "None", note: [race.neutralised.SC.length && "Safety Car", race.neutralised.VSC.length && "VSC", race.neutralised.RED.length && "Red flag"].filter(Boolean).join(" · ") || "Green all race" },
        { label: "Weather", value: race.weather.rain ? "Rain" : "Dry", note: race.weather.track ? `Track ${race.weather.track[0].toFixed(0)}–${race.weather.track[1].toFixed(0)} °C · Air ${race.weather.air![0].toFixed(0)}–${race.weather.air![1].toFixed(0)} °C` : undefined },
      ]} />

      <section>
        <h3>The Story</h3>
        <ul className="insights">
          {race.insights.map((t, k) => <li key={k}>{t}</li>)}
        </ul>
      </section>

      <section>
        <h3>Result</h3>
        <Table<ResultRow>
          data={results}
          rowKey={(r) => r.driver}
          cardTitle={(r) => <><span className="muted">{r.classified ? `P${r.position}` : "DNF"}</span> <DriverChip code={r.driver} color={r.color} /> {r.name}</>}
          cardSub={["team"]}
          cardStats={["gained", "gap", "points"]}
          columns={[
            { key: "position", label: "Pos", value: (r) => r.position, render: (r) => (r.classified ? r.position : r.dns ? "DNS" : "DNF"), numeric: true },
            { key: "driver", label: "Driver", value: (r) => r.name ?? r.driver, render: (r) => <><DriverChip code={r.driver} color={r.color} /> {r.name}</> },
            { key: "team", label: "Team", value: (r) => r.team },
            { key: "grid", label: "Grid", value: (r) => r.grid, render: (r) => (r.pit_lane_start ? "Pit lane" : r.grid ?? "–"), numeric: true },
            { key: "gained", label: "+/−", title: "Places gained from the grid", value: (r) => (r.grid && r.classified && r.position ? r.grid - r.position : null), render: (r) => (r.grid && r.classified && r.position ? signed(r.grid - r.position, 0) : "–"), numeric: true },
            { key: "gap", label: "Gap", value: (r) => r.gap ?? (r.classified ? 1e4 - r.laps : null), render: (r) => (r.position === 1 ? "Winner" : r.gap !== null ? gap(r.gap) : r.status || "–") },
            { key: "plan", label: "Tyres", value: (r) => stopsBy.get(r.driver) ?? null, render: (r) => <Plan stints={planBy.get(r.driver) ?? []} /> },
            { key: "best_lap", label: "Best lap", value: (r) => r.best_lap, render: (r) => lapTime(r.best_lap), numeric: true },
            { key: "pace", label: "Race pace", title: "Median clean-lap pace against the field median (fuel corrected, per compound). Negative = faster.", value: (r) => r.pace, render: (r) => (r.pace === null ? "–" : `${signed(r.pace, 2)}%`), numeric: true },
            { key: "points", label: "Pts", value: (r) => r.points, numeric: true },
          ]}
        />
      </section>

      {forecast && <ForecastCheck forecast={forecast} results={results} />}

      <section>
        <h3>Running Order</h3>
        <p className="muted">Position at the end of each lap. Circles are pit stops; shaded laps were under the Safety Car (darker) or VSC. Tap a driver to pick them out.</p>
        <DriverPicker results={results} value={highlight} onChange={setHighlight} />
        <PositionChart laps={laps} results={results} neutral={race.neutralised} highlight={highlight} />
      </section>

      <section>
        <h3>Tyre Strategy</h3>
        <Legend items={["S", "M", "H", "I", "W"].filter((k) => stints.some((s) => compoundKey(s.compound) === k)).map((k) => ({ label: COMPOUND[k].name, color: COMPOUND[k].color }))} />
        <StrategyChart stints={stints} results={results} totalLaps={race.total_laps} neutral={race.neutralised} />
        <p className="muted">▲ marks a tyre cliff: the lap the stint's pace fell away from its trend (blue when it came with rain, so not the tyres).</p>
      </section>

      <section>
        <h3>Gap to the Leader</h3>
        <div className="toolbar">
          <Segmented label="Drivers" value={gapScope} onChange={setGapScope}
                     options={[{ value: "top", label: "Top 10" }, { value: "all", label: "Everyone" }]} />
        </div>
        <GapChart laps={laps} results={results} neutral={race.neutralised} highlight={highlight}
                  drivers={gapScope === "top" ? topDrivers : results.map((r) => r.driver)} />
      </section>

      <Degradation race={race} />
      <PostMortem results={results} stints={stints} />
    </>
  );
}

function DriverPicker({ results, value, onChange }: { results: ResultRow[]; value: string | null; onChange: (d: string | null) => void }) {
  return (
    <div className="driver-picker" role="group" aria-label="Pick out a driver">
      {results.map((r) => (
        <button key={r.driver} className={value === r.driver ? "on" : undefined} aria-pressed={value === r.driver}
                onClick={() => onChange(value === r.driver ? null : r.driver)}>
          <DriverChip code={r.driver} color={r.color} />
        </button>
      ))}
    </div>
  );
}

/** The forecast made before the race (from earlier races only) against the result. */
function ForecastCheck({ forecast, results }: { forecast: Forecast; results: ResultRow[] }) {
  const fc = rows<ForecastDriver>(forecast.drivers).sort((a, b) => b.p_win - a.p_win);
  const pos = new Map(results.map((r) => [r.driver, r]));
  const winner = results[0];
  const wf = fc.find((f) => f.driver === winner.driver);
  const favourite = fc[0];
  const podium = new Set(results.filter((r) => r.classified).slice(0, 3).map((r) => r.driver));
  const predictedPodium = [...fc].sort((a, b) => b.p_podium - a.p_podium).slice(0, 3);
  const hits = predictedPodium.filter((f) => podium.has(f.driver)).length;
  return (
    <section>
      <h3>Forecast vs Result</h3>
      <p className="muted">
        The forecast for this race, made only from the {forecast.based_on.length} race{forecast.based_on.length === 1 ? "" : "s"} before it.
      </p>
      <Tiles tiles={[
        { label: "Favourite", value: <><DriverChip code={favourite.driver} color={favourite.color} /> {pct(favourite.p_win)}</>, note: `finished ${pos.get(favourite.driver)?.classified ? `P${pos.get(favourite.driver)!.position}` : "DNF"}` },
        { label: "Winner's chance", value: wf ? pct(wf.p_win) : "–", note: wf ? `${winner.driver} was ranked ${fc.indexOf(wf) + 1} of ${fc.length}` : "no form figure" },
        { label: "Podium picks", value: `${hits} of 3`, note: predictedPodium.map((f) => f.driver).join(", ") },
      ]} />
      <Table<ForecastDriver>
        data={fc}
        rowKey={(f) => f.driver}
        limit={10}
        sort="p_win"
        cardTitle={(f) => <><DriverChip code={f.driver} color={f.color} /> {f.team}</>}
        cardStats={["p_win", "exp_pos", "actual"]}
        columns={[
          { key: "driver", label: "Driver", value: (f) => f.driver, render: (f) => <DriverChip code={f.driver} color={f.color} /> },
          { key: "p_win", label: "Win", value: (f) => f.p_win, render: (f) => pct(f.p_win), numeric: true, group: "Forecast" },
          { key: "p_podium", label: "Podium", value: (f) => f.p_podium, render: (f) => pct(f.p_podium), numeric: true, group: "Forecast" },
          { key: "exp_pos", label: "Exp. pos", value: (f) => f.exp_pos, render: (f) => dec(f.exp_pos, 1), numeric: true, group: "Forecast" },
          { key: "actual", label: "Finished", value: (f) => (pos.get(f.driver)?.classified ? pos.get(f.driver)!.position : 99), render: (f) => (pos.get(f.driver) ? (pos.get(f.driver)!.classified ? `P${pos.get(f.driver)!.position}` : "DNF") : "–"), numeric: true, group: "Result" },
        ]}
      />
    </section>
  );
}

function Degradation({ race }: { race: Race }) {
  const deg = rows<DegRow>(race.deg);
  const comps = race.compounds.filter((c) => c.n_drivers > 0 && compoundKey(c.compound) !== "?").sort((a, b) => "SMHIW".indexOf(compoundKey(a.compound)) - "SMHIW".indexOf(compoundKey(b.compound)));
  const [compound, setCompound] = useState(() => comps.reduce((a, b) => (b.n_drivers > a.n_drivers ? b : a), comps[0])?.compound);
  if (!comps.length) return null;
  const shown = deg.filter((d) => d.compound === compound && d.n_laps >= 5).sort((a, b) => a.deg_rate - b.deg_rate);
  return (
    <section>
      <h3>Tyre Degradation</h3>
      <p className="muted">
        Seconds lost per lap of tyre age, from clean laps only (no lap 1, pit laps, Safety Cars or traffic
        within 1.5 s), corrected for fuel burn. The field figure is the median driver.
      </p>
      <Tiles tiles={comps.map((c) => ({
        label: COMPOUND[compoundKey(c.compound)].name,
        value: `${signed(c.deg_rate, 3)} s/lap`,
        note: `${c.n_drivers} driver${c.n_drivers === 1 ? "" : "s"} · spread ±${dec(c.deg_iqr / 2, 3)}`,
      }))} />
      <div className="toolbar">
        <Segmented label="Compound" value={compound} onChange={setCompound}
                   options={comps.map((c) => ({ value: c.compound, label: COMPOUND[compoundKey(c.compound)].name }))} />
      </div>
      <Table<DegRow>
        data={shown}
        rowKey={(d) => d.driver}
        sort="deg_rate"
        desc={false}
        cards={false}
        limit={10}
        columns={[
          { key: "driver", label: "Driver", value: (d) => d.driver, render: (d) => <DriverChip code={d.driver} color={rows<ResultRow>(race.results).find((r) => r.driver === d.driver)?.color} /> },
          { key: "deg_rate", label: "Deg (s/lap)", value: (d) => d.deg_rate, render: (d) => signed(d.deg_rate, 3), numeric: true },
          { key: "n_laps", label: "Laps", value: (d) => d.n_laps, numeric: true },
          { key: "sample", label: "Sample", value: (d) => d.sample },
        ]}
      />
    </section>
  );
}

/** Each driver's stints in words: compound, laps, deg and any cliff. */
function PostMortem({ results, stints }: { results: ResultRow[]; stints: StintRow[] }) {
  const [open, setOpen] = useState(false);
  const shown = open ? results : results.slice(0, 5);
  return (
    <section>
      <h3>Strategy Post-Mortem</h3>
      <div className="postmortem">
        {shown.map((r) => {
          const own = stints.filter((s) => s.driver === r.driver);
          return (
            <div key={r.driver} className="pm-driver">
              <div className="pm-head">
                <span className="muted">{r.classified ? `P${r.position}` : "DNF"}</span> <DriverChip code={r.driver} color={r.color} /> {r.name}
                <span className="muted"> · {r.team}</span>
              </div>
              <ul>
                {own.map((s) => (
                  <li key={s.stint}>
                    <Tyre compound={s.compound} /> Laps {s.first}–{s.last} ({s.laps}):{" "}
                    {s.deg !== null ? <>deg <b>{signed(s.deg, 3)} s/lap</b> over {s.clean_laps} clean laps.</> : "too few clean laps to model."}
                    {s.cliff_lap !== null && s.cliff_cause === "tyre" && <> <span className="bad">Cliff on lap {s.cliff_lap}</span>, about {dec(s.cliff_lost, 1)} s lost.</>}
                    {s.cliff_lap !== null && s.cliff_cause === "weather" && <> Pace dropped from lap {s.cliff_lap} with the rain, not the tyres.</>}
                  </li>
                ))}
                {r.dnf && <li>Retired after lap {r.laps} ({r.status}).</li>}
              </ul>
            </div>
          );
        })}
      </div>
      <button className="link" onClick={() => setOpen(!open)}>{open ? "Show the top five" : `Show all ${results.length} drivers`}</button>
    </section>
  );
}
