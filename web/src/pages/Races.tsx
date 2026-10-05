import * as Plot from "@observablehq/plot";
import { useCallback, useMemo, useState } from "react";
import { color } from "../colors";
import { COMPOUND, compoundKey, DriverChip, DriverPicker, GapChart, Plan, PositionChart, SessionBadge, StrategyChart, Tyre } from "../components/f1";
import { Chart, Legend, Loading, Note, plotDefaults, Segmented, Table, Tiles } from "../components/ui";
import {
  forecastFile, isQuali, raceFile, rows, SESSION_LABEL, sessionDone, sessionHash, sessionOdds, weekendSessions,
  type CalendarEvent, type DegRow, type Forecast, type ForecastDriver, type LapRow, type Quali, type QualiForecastDriver,
  type QualiLap, type QualiRow, type Race, type ResultRow, type SectorRow, type SessionCode, type StintRow, type WeatherLap,
} from "../data";
import { dec, gap, lapTime, pct, shortEvent, signed, dayYear } from "../format";
import { useData, useHash, useSite } from "../site";

export default function Races() {
  const site = useSite();
  const hash = useHash();
  const [, roundPart, codePart] = hash.split("/");
  const round = Number(roundPart);
  if (!round) return <RaceList />;
  const ev = site.event.get(round);
  let code: SessionCode = (["S", "Q", "SQ"] as const).find((c) => c === codePart) ?? "R";
  // #races/16 before the Grand Prix is out: the weekend's latest finished session.
  if (ev && !codePart && !ev.done_R) code = weekendSessions(ev).filter((c) => sessionDone(ev, c)).at(-1) ?? "R";
  return isQuali(code) ? <QualiView round={round} code={code as "Q" | "SQ"} /> : <RaceView round={round} code={code as "R" | "S"} />;
}

// ---------------------------------------------------------------- the list

const anyDone = (e: CalendarEvent) => weekendSessions(e).some((c) => sessionDone(e, c));

function RaceList() {
  const site = useSite();
  const done = site.meta.calendar.filter(anyDone).reverse();
  const sprints = done.some((e) => e.sprint_utc);
  return (
    <>
      <h2>Race Results & Analysis</h2>
      <p className="lede">
        Every finished round, latest first, with each of its sessions: Qualifying and the Grand Prix, and on
        a sprint weekend Sprint Qualifying and the Sprint too. A race has the result, the running order and
        gaps lap by lap, every driver's tyre strategy and degradation, tyre cliffs and Safety Cars; a
        qualifying session has Q1, Q2 and Q3, the cut-offs, sectors and ideal laps, and how much quicker the
        track got. Each comes with the forecast made before it next to what happened.
      </p>
      <Table<CalendarEvent>
        data={done}
        rowKey={(e) => e.round}
        sort="round"
        desc
        onRow={(e) => { window.location.hash = `races/${e.round}`; }}
        cardTitle={(e) => `${e.round}. ${shortEvent(e.event)}`}
        cardSub={["date", "sessions"]}
        cardStats={["pole", ...(sprints ? ["sprint"] : []), "winner"]}
        columns={[
          { key: "round", label: "Round", value: (e) => e.round, numeric: true, rank: true },
          { key: "event", label: "Grand Prix", value: (e) => e.event, render: (e) => <a href={`#races/${e.round}`}>{e.event}</a> },
          { key: "date", label: "Date", value: (e) => dayYear(e.race_utc) },
          { key: "sessions", label: "Sessions", sortable: false, value: (e) => weekendSessions(e).length, wrap: true,
            render: (e) => (
              <span className="session-links">
                {weekendSessions(e).filter((c) => sessionDone(e, c)).map((c) => (
                  <a key={c} href={`#${sessionHash(e.round, c)}`} onClick={(ev) => ev.stopPropagation()}><SessionBadge code={c} short /></a>
                ))}
              </span>
            ) },
          { key: "pole", label: "Pole", title: "Pole position in Qualifying (the Grand Prix grid)", value: (e) => e.pole ?? "",
            render: (e) => (e.pole ? <DriverChip code={e.pole} color={e.pole_color} /> : "–") },
          ...(sprints ? [{ key: "sprint", label: "Sprint", title: "Sprint winner", value: (e: CalendarEvent) => e.sprint_winner ?? "",
            render: (e: CalendarEvent) => (e.sprint_winner ? <DriverChip code={e.sprint_winner} color={e.sprint_winner_color} /> : e.sprint_utc ? "–" : "") }] : []),
          { key: "winner", label: "Winner", title: "Grand Prix winner", value: (e) => e.winner ?? "",
            render: (e) => (e.winner ? <DriverChip code={e.winner} color={e.winner_color} /> : "–") },
        ]}
      />
    </>
  );
}

// ---------------------------------------------------------------- one weekend's header

/** Which data came from elsewhere because the primary source (OpenF1) had a gap. */
function SourcesNote({ sources }: { sources?: Record<string, string> }) {
  const entries = Object.entries(sources ?? {});
  if (!entries.length) return null;
  return (
    <Note>
      OpenF1 has a gap for this session, so {entries.map(([k, v], i) => (
        <span key={k}>{i > 0 && (i === entries.length - 1 ? " and " : ", ")}its {k.replace("_", " ")} come{k.endsWith("s") ? "" : "s"} from {v}</span>
      ))}.
    </Note>
  );
}

/** Breadcrumb, the round's title with the session's badge, and a switch between its finished sessions. */
function WeekendHeader({ round, code, ev, subtitle }: { round: number; code: SessionCode; ev: CalendarEvent; subtitle: string }) {
  const site = useSite();
  const rounds = site.meta.calendar.filter(anyDone).map((e) => e.round);
  const i = rounds.indexOf(round);
  const prev = i > 0 ? rounds[i - 1] : null;
  const next = i >= 0 && i < rounds.length - 1 ? rounds[i + 1] : null;
  const done = weekendSessions(ev).filter((c) => sessionDone(ev, c));
  return (
    <>
      <p className="crumb">
        <a href="#races">← All races</a>
        {prev && <> · <a href={`#races/${prev}`}>Round {prev}</a></>}
        {next && <> · <a href={`#races/${next}`}>Round {next}</a></>}
      </p>
      <h2 className="session-title">Round {round}: {ev.event} <SessionBadge code={code} /></h2>
      <p className="lede">{subtitle}</p>
      {done.length > 1 && (
        <div className="toolbar">
          <Segmented label="Session" value={code} onChange={(c) => { window.location.hash = sessionHash(round, c); }}
                     options={done.map((c) => ({ value: c, label: SESSION_LABEL[c] }))} />
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- one race

function RaceView({ round, code }: { round: number; code: "R" | "S" }) {
  const site = useSite();
  const ev = site.event.get(round);
  const race = useData<Race>(raceFile(round, code));
  const forecast = useData<Forecast>(forecastFile(round));
  const results = useMemo(() => rows<ResultRow>(race?.results), [race]);
  const laps = useMemo(() => rows<LapRow>(race?.laps), [race]);
  const stints = useMemo(() => rows<StintRow>(race?.stints), [race]);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [gapScope, setGapScope] = useState<"top" | "all">("top");

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
      <WeekendHeader round={round} code={code} ev={ev}
                     subtitle={`${race.location}, ${race.country} · ${dayYear(race.start_utc)} · ${race.total_laps} laps${code === "S" ? " · a sprint: about a third of the distance, no compulsory stop, sprint points (8 to 1)" : ""}`} />
      <SourcesNote sources={race.sources} />
      {!race.complete && (
        <Note>
          Provisional: the classification or starting grid isn't out yet, so points and
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
          sort="position"
          cardTitle={(r) => <><span className="muted">{r.classified ? `P${r.position}` : "DNF"}</span> <DriverChip code={r.driver} color={r.color} /> {r.name}</>}
          cardSub={["team"]}
          cardStats={["gained", "gap", "points"]}
          columns={[
            { key: "position", label: "Pos", value: (r) => r.position, render: (r) => (r.classified ? r.position : r.dns ? "DNS" : "DNF"), numeric: true, rank: true },
            { key: "driver", label: "Driver", value: (r) => r.name ?? r.driver, render: (r) => <><DriverChip code={r.driver} color={r.color} /> {r.name}</> },
            { key: "team", label: "Team", value: (r) => r.team },
            { key: "grid", label: "Grid", value: (r) => r.grid, render: (r) => (r.pit_lane_start ? "Pit lane" : r.grid ?? "–"), numeric: true, rank: true },
            { key: "gained", label: "+/−", title: "Places gained from the grid", value: (r) => (r.grid && r.classified && r.position ? r.grid - r.position : null), render: (r) => (r.grid && r.classified && r.position ? signed(r.grid - r.position, 0) : "–"), numeric: true },
            { key: "gap", label: "Gap", value: (r) => r.gap ?? (r.classified ? 1e4 - r.laps : null), render: (r) => (r.position === 1 ? "Winner" : r.gap !== null ? gap(r.gap) : r.status || "–") },
            { key: "plan", label: "Tyres", value: (r) => stopsBy.get(r.driver) ?? null, render: (r) => <Plan stints={planBy.get(r.driver) ?? []} /> },
            { key: "best_lap", label: "Best lap", value: (r) => r.best_lap, render: (r) => lapTime(r.best_lap), numeric: true },
            { key: "pace", label: "Race pace", title: "Median clean-lap pace against the field median (fuel corrected, per compound). Negative = faster.", value: (r) => r.pace, render: (r) => (r.pace === null ? "–" : `${signed(r.pace, 2)}%`), numeric: true },
            { key: "points", label: "Pts", value: (r) => r.points, numeric: true },
          ]}
        />
      </section>

      {forecast && sessionOdds(forecast, code, "pre") && <ForecastCheck forecast={forecast} code={code} results={results} />}

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

      {race.weather_laps && <Weather race={race} />}
      <Degradation race={race} />
      <PostMortem results={results} stints={stints} />
    </>
  );
}

/** The circuit's weather sensors lap by lap: track and air temperature, and the laps with rain. */
function Weather({ race }: { race: Race }) {
  const laps = useMemo(() => rows<WeatherLap>(race.weather_laps), [race]);
  const make = useCallback((width: number) => {
    const wet = laps.filter((l) => l.rain);
    const temps = laps.flatMap((l) => [
      { lap: l.lap, v: l.track, k: "Track" }, { lap: l.lap, v: l.air, k: "Air" },
    ]).filter((d) => d.v !== null);
    return Plot.plot({
      ...plotDefaults(width),
      height: 260,
      x: { label: "Lap", domain: [0.5, race.total_laps + 0.5] },
      y: { label: "°C", grid: true },
      marks: [
        Plot.rectX(wet, { x1: (l) => l.lap - 0.5, x2: (l) => l.lap + 0.5, fill: "#2a78d6", fillOpacity: 0.15 }),
        Plot.line(temps.filter((d) => d.k === "Track"), { x: "lap", y: "v", stroke: color.s2, strokeWidth: 2 }),
        Plot.line(temps.filter((d) => d.k === "Air"), { x: "lap", y: "v", stroke: color.s1, strokeWidth: 2 }),
        Plot.tip(laps, Plot.pointerX({ x: "lap", y: (l) => l.track ?? l.air,
          title: (l) => `Lap ${l.lap}${l.rain ? " · rain" : ""}
Track ${l.track?.toFixed(1) ?? "–"} °C, air ${l.air?.toFixed(1) ?? "–"} °C` +
            (l.humidity !== null ? `
Humidity ${l.humidity.toFixed(0)}%` : "") + (l.wind !== null ? `, wind ${l.wind.toFixed(1)} m/s` : "") })),
      ],
    });
  }, [laps, race.total_laps]);
  if (!laps.some((l) => l.track !== null || l.air !== null)) return null;
  return (
    <section>
      <h3>Weather</h3>
      <p className="muted">The circuit's own sensors when the leader finished each lap. Shaded laps had rain.</p>
      <Legend items={[
        { label: "Track", color: color.s2, kind: "line" }, { label: "Air", color: color.s1, kind: "line" },
        ...(laps.some((l) => l.rain) ? [{ label: "Rain", color: "rgba(42,120,214,0.3)" }] : []),
      ]} />
      <Chart make={make} height={260} ariaLabel="Track and air temperature by lap, with the laps that had rain" />
    </section>
  );
}

/** Choose between the forecast from before the weekend and the one after the session's qualifying. */
function useWhich(forecast: Forecast, code: SessionCode) {
  const entry = forecast.sessions?.[code];
  const [which, setWhich] = useState<"latest" | "pre">("latest");
  const after = entry?.latest ? entry.latest_after.map((id) => SESSION_LABEL[id.split("-")[1] as SessionCode]) : [];
  const toggle = after.length ? (
    <div className="toolbar">
      <Segmented label="Forecast" value={which} onChange={setWhich}
                 options={[{ value: "latest", label: `After ${after.at(-1)}` }, { value: "pre", label: "Before the weekend" }]} />
    </div>
  ) : null;
  return { which: after.length ? which : "pre" as const, toggle, after };
}

/** The forecast made before the race (from earlier sessions only) against the result. */
function ForecastCheck({ forecast, code, results }: { forecast: Forecast; code: "R" | "S"; results: ResultRow[] }) {
  const { which, toggle, after } = useWhich(forecast, code);
  const fc = rows<ForecastDriver>(sessionOdds(forecast, code, which)).sort((a, b) => b.p_win - a.p_win);
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
        The forecast for this {code === "S" ? "sprint" : "race"}, made only from the sessions before it:{" "}
        {which === "latest"
          ? <>the earlier rounds plus this weekend's {after.join(", ")}, so it knew the grid.</>
          : <>the {forecast.based_on.length} round{forecast.based_on.length === 1 ? "" : "s"} before, without this weekend's qualifying or grid.</>}
      </p>
      {toggle}
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
          { key: "exp_pos", label: "Exp. pos", value: (f) => f.exp_pos, render: (f) => dec(f.exp_pos, 1), numeric: true, rank: true, group: "Forecast" },
          { key: "actual", label: "Finished", value: (f) => (pos.get(f.driver)?.classified ? pos.get(f.driver)!.position : 99), render: (f) => (pos.get(f.driver) ? (pos.get(f.driver)!.classified ? `P${pos.get(f.driver)!.position}` : "DNF") : "–"), numeric: true, rank: true, group: "Result" },
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

// ---------------------------------------------------------------- one qualifying session

function QualiView({ round, code }: { round: number; code: "Q" | "SQ" }) {
  const site = useSite();
  const ev = site.event.get(round);
  const q = useData<Quali>(raceFile(round, code));
  const forecast = useData<Forecast>(forecastFile(round));
  const results = useMemo(() => rows<QualiRow>(q?.results), [q]);
  const sectors = useMemo(() => rows<SectorRow>(q?.sectors), [q]);
  if (q === undefined) return <Loading />;
  if (q === null || !ev) return <p>No data for this session yet. <a href="#races">All races</a></p>;

  const pole = results[0];
  const second = results[1];
  const evo = q.evolution;
  const race = code === "Q" ? "Grand Prix" : "Sprint";
  const fastest = (k: "s1" | "s2" | "s3") => Math.min(...sectors.map((s) => s[k] ?? Infinity));
  const best = { s1: fastest("s1"), s2: fastest("s2"), s3: fastest("s3") };
  const segCell = (r: QualiRow, k: "q1" | "q2" | "q3", level: number) =>
    r[k] !== null ? lapTime(r[k]) : r.reached >= level ? "No time" : "";
  const colorOf = (d: string) => results.find((r) => r.driver === d)?.color;

  return (
    <>
      <WeekendHeader round={round} code={code} ev={ev}
                     subtitle={`${q.location}, ${q.country} · ${dayYear(q.start_utc)} · sets the ${race} grid · ${results.length} drivers: ${q.to_q2} through to Q2, 10 to Q3`} />
      <SourcesNote sources={q.sources} />
      {!q.complete && <Note>Provisional: the official classification isn't out yet, so times come from the timing data.</Note>}

      <Tiles tiles={[
        { label: "Pole", value: pole ? <><DriverChip code={pole.driver} color={pole.color} /> {lapTime(pole.best)}</> : "–", note: pole ? `${pole.name ?? pole.driver} · ${pole.team}` : undefined },
        { label: "Margin", value: second?.best && pole?.best ? `${(second.best - pole.best).toFixed(3)} s` : "–", note: second ? `to ${second.driver} in P2` : undefined },
        { label: "Track evolution", value: evo.Q1_Q2 !== null ? `${signed(evo.Q1_Q2 + (evo.Q2_Q3 ?? 0), 3)} s` : "–", note: evo.Q1_Q2 !== null ? `Q1→Q2 ${signed(evo.Q1_Q2, 3)}${evo.Q2_Q3 !== null ? ` · Q2→Q3 ${signed(evo.Q2_Q3, 3)}` : ""} (same drivers, median)` : "not enough drivers in both" },
        { label: "Within Q1", value: q.gain?.Q1 != null ? `${signed(q.gain.Q1, 3)} s/min` : "–", note: "track gain: each driver's laps against their own, by the minute" },
        { label: "Weather", value: q.weather.rain ? "Rain" : "Dry", note: q.weather.track ? `Track ${q.weather.track[0].toFixed(0)}–${q.weather.track[1].toFixed(0)} °C` : undefined },
      ]} />

      <section>
        <h3>The Story</h3>
        <ul className="insights">{q.insights.map((t, k) => <li key={k}>{t}</li>)}</ul>
      </section>

      <section>
        <h3>Result</h3>
        <p className="muted">
          Each segment's time; "No time" means the driver went through but didn't set one. Pace puts every
          driver on one scale although the track gets faster through the session (each segment against the
          Q3 runners' times in it).
        </p>
        <Table<QualiRow>
          data={results}
          rowKey={(r) => r.driver}
          sort="position"
          cardTitle={(r) => <><span className="muted">P{r.position}</span> <DriverChip code={r.driver} color={r.color} /> {r.name}</>}
          cardSub={["team"]}
          cardStats={["best", "gap_to_pole", "out"]}
          columns={[
            { key: "position", label: "Pos", value: (r) => r.position, numeric: true, rank: true },
            { key: "driver", label: "Driver", value: (r) => r.name ?? r.driver, render: (r) => <><DriverChip code={r.driver} color={r.color} /> {r.name}</> },
            { key: "team", label: "Team", value: (r) => r.team },
            { key: "q1", label: "Q1", value: (r) => r.q1, render: (r) => segCell(r, "q1", 1), numeric: true, group: "Times" },
            { key: "q2", label: "Q2", value: (r) => r.q2, render: (r) => segCell(r, "q2", 2), numeric: true, group: "Times" },
            { key: "q3", label: "Q3", value: (r) => r.q3, render: (r) => segCell(r, "q3", 3), numeric: true, group: "Times" },
            { key: "best", label: "Best", value: (r) => r.best, render: (r) => lapTime(r.best), numeric: true },
            { key: "gap_to_pole", label: "To pole", title: "Best lap against the pole lap", value: (r) => r.gap_to_pole, render: (r) => (r.position === 1 ? "Pole" : gap(r.gap_to_pole)), numeric: true },
            { key: "out", label: "Out in", value: (r) => r.reached, render: (r) => (r.reached === 3 ? "Q3" : <span className="tag">{r.reached === 2 ? "Q2" : "Q1"}</span>), numeric: true },
            { key: "teammate_gap", label: "Teammate", title: "Against the teammate, in the last segment both set a time (negative = faster)", value: (r) => r.teammate_gap, render: (r) => (r.teammate_gap === null ? "–" : `${signed(r.teammate_gap, 3)} s`), numeric: true },
            { key: "pace", label: "Pace", title: "Qualifying pace against the field median (negative = faster)", value: (r) => r.pace, render: (r) => (r.pace === null ? "–" : `${signed(r.pace, 2)}%`), numeric: true },
          ]}
        />
      </section>

      <Margins q={q} results={results} />
      <EvolutionChart q={q} results={results} />

      <section>
        <h3>Sectors and the Ideal Lap</h3>
        <p className="muted">
          Each driver's best three sectors from any of their laps, added up: the lap they had in them. "Lost" is
          how far their best lap was from it. The fastest time in each sector is in bold.
        </p>
        <Table<SectorRow>
          data={sectors}
          rowKey={(s) => s.driver}
          sort="ideal"
          desc={false}
          cardTitle={(s) => <DriverChip code={s.driver} color={colorOf(s.driver)} />}
          cardStats={["ideal", "lost", "top_speed"]}
          columns={[
            { key: "driver", label: "Driver", value: (s) => s.driver, render: (s) => <DriverChip code={s.driver} color={colorOf(s.driver)} /> },
            { key: "best_lap", label: "Best lap", value: (s) => s.best_lap, render: (s) => <>{lapTime(s.best_lap)} {s.compound !== "?" && <Tyre compound={s.compound} />}</>, numeric: true },
            { key: "ideal", label: "Ideal", value: (s) => s.ideal, render: (s) => lapTime(s.ideal), numeric: true },
            { key: "lost", label: "Lost", value: (s) => s.lost, render: (s) => (s.lost === null ? "–" : `${s.lost.toFixed(3)} s`), numeric: true },
            ...(["s1", "s2", "s3"] as const).map((k) => ({
              key: k, label: k.toUpperCase(), group: "Best sectors", numeric: true, value: (s: SectorRow) => s[k],
              render: (s: SectorRow) => (s[k] === null ? "–" : s[k] === best[k] ? <b>{s[k]!.toFixed(3)}</b> : s[k]!.toFixed(3)),
            })),
            { key: "top_speed", label: "Top speed", title: "Fastest at the speed trap (km/h)", value: (s) => s.top_speed, render: (s) => dec(s.top_speed, 0), numeric: true },
            { key: "push_laps", label: "Push laps", title: "Laps within 5% of the driver's best: their flying laps", value: (s) => s.push_laps, numeric: true },
          ]}
        />
      </section>

      {forecast && sessionOdds(forecast, code, "pre") && <QualiForecastCheck forecast={forecast} code={code} results={results} />}
    </>
  );
}

/** How close each driver was to the Q1 and Q2 cut-offs: negative went through. */
function Margins({ q, results }: { q: Quali; results: QualiRow[] }) {
  const [seg, setSeg] = useState<"q1" | "q2">("q1");
  const make = useCallback((width: number) => {
    const key = seg === "q1" ? "q1_margin" : "q2_margin";
    const data = results.filter((r) => r[key] !== null && Math.abs(r[key]!) <= 1.0).sort((a, b) => a[key]! - b[key]!);
    return Plot.plot({
      ...plotDefaults(width),
      height: data.length * 22 + 50,
      marginLeft: 48,
      marginRight: 56,
      x: { label: "Seconds against the cut-off (negative = through)", grid: true },
      y: { label: null, domain: data.map((r) => r.driver), padding: 0.2 },
      marks: [
        Plot.barX(data, { x: key, y: "driver", fill: (r) => r.color, fillOpacity: (r) => (r[key]! < 0 ? 1 : 0.45), rx: 2 }),
        Plot.ruleX([0], { stroke: color.ink2 }),
        Plot.text(data, { x: key, y: "driver", text: (r) => signed(r[key], 3), dx: 4, textAnchor: "start", fill: color.ink2, fontSize: 11 }),
      ],
    });
  }, [results, seg]);
  if (!results.some((r) => r.q1_margin !== null)) return null;
  return (
    <section>
      <h3>Knockout Margins</h3>
      <p className="muted">
        How far each driver near the line was from the cut-off ({seg === "q1" ? `top ${q.to_q2} into Q2` : "top 10 into Q3"}):
        a driver who went through against the fastest one knocked out, one knocked out against the slowest who
        went through. Faded bars missed out. Drivers more than a second away are left off.
      </p>
      <div className="toolbar">
        <Segmented label="Segment" value={seg} onChange={setSeg} options={[{ value: "q1", label: "Q1 cut-off" }, { value: "q2", label: "Q2 cut-off" }]} />
      </div>
      <Chart make={make} ariaLabel="Margins to the qualifying cut-off" />
    </section>
  );
}

/** Every flying lap against the session clock: the track getting faster, segment by segment. */
function EvolutionChart({ q, results }: { q: Quali; results: QualiRow[] }) {
  const make = useCallback((width: number) => {
    const laps = rows<QualiLap>(q.laps);
    const colorOf = new Map(results.map((r) => [r.driver, r.color]));
    const lo = Math.min(...laps.map((l) => l.lap_time));
    const shown = laps.filter((l) => l.lap_time <= lo * 1.04);
    return Plot.plot({
      ...plotDefaults(width),
      height: 320,
      x: { label: "Minutes into the session" },
      y: { label: "Lap time (s)", grid: true },
      marks: [
        ...(q.cutoff.q1 ? [Plot.ruleY([q.cutoff.q1], { stroke: color.muted, strokeDasharray: "4,3" })] : []),
        ...(q.cutoff.q2 ? [Plot.ruleY([q.cutoff.q2], { stroke: color.muted, strokeDasharray: "2,3" })] : []),
        Plot.dot(shown, { x: "minute", y: "lap_time", fill: (l) => colorOf.get(l.driver) ?? color.neutral, r: 3.5, stroke: color.surface, strokeWidth: 0.5 }),
        Plot.tip(shown, Plot.pointer({ x: "minute", y: "lap_time", title: (l) => `${l.driver} · Q${l.segment ?? "?"}\n${lapTime(l.lap_time)} at ${l.minute.toFixed(0)} min` })),
      ],
    });
  }, [q, results]);
  if (!rows<QualiLap>(q.laps).length) return null;
  return (
    <section>
      <h3>Track Evolution</h3>
      <p className="muted">
        Every flying lap within 4% of the fastest, by when it ended, in team colours. The lines are the slowest
        times that got through Q1 (dashed) and Q2 (dotted). Laps get quicker as the track rubbers in, which is
        why drivers queue to run last.
      </p>
      <Chart make={make} height={320} ariaLabel="Flying lap times through the session" />
    </section>
  );
}

/** The qualifying forecast (from earlier sessions only) against the result. */
function QualiForecastCheck({ forecast, code, results }: { forecast: Forecast; code: "Q" | "SQ"; results: QualiRow[] }) {
  const { which, toggle, after } = useWhich(forecast, code);
  const fc = rows<QualiForecastDriver>(sessionOdds(forecast, code, which)).sort((a, b) => b.p_pole - a.p_pole);
  if (!fc.length || !results.length) return null;
  const pos = new Map(results.map((r) => [r.driver, r]));
  const poleSitter = results[0];
  const pf = fc.find((f) => f.driver === poleSitter.driver);
  const favourite = fc[0];
  const q3 = new Set(results.filter((r) => r.reached === 3).map((r) => r.driver));
  const picked = [...fc].sort((a, b) => b.p_q3 - a.p_q3).slice(0, 10);
  const hits = picked.filter((f) => q3.has(f.driver)).length;
  return (
    <section>
      <h3>Forecast vs Result</h3>
      <p className="muted">
        The forecast for this session from qualifying form{which === "latest" ? <> and this weekend's {after.join(", ")}</> : null}, made before it.
      </p>
      {toggle}
      <Tiles tiles={[
        { label: "Favourite", value: <><DriverChip code={favourite.driver} color={favourite.color} /> {pct(favourite.p_pole)}</>, note: `qualified P${pos.get(favourite.driver)?.position ?? "–"}` },
        { label: "Pole sitter's chance", value: pf ? pct(pf.p_pole) : "–", note: pf ? `${poleSitter.driver} was ranked ${fc.indexOf(pf) + 1} of ${fc.length}` : "no form figure" },
        { label: "Q3 picks", value: `${hits} of 10`, note: "the ten most likely to reach Q3" },
      ]} />
      <Table<QualiForecastDriver>
        data={fc}
        rowKey={(f) => f.driver}
        limit={10}
        sort="p_pole"
        cardTitle={(f) => <><DriverChip code={f.driver} color={f.color} /> {f.team}</>}
        cardStats={["p_pole", "exp_pos", "actual"]}
        columns={[
          { key: "driver", label: "Driver", value: (f) => f.driver, render: (f) => <DriverChip code={f.driver} color={f.color} /> },
          { key: "p_pole", label: "Pole", value: (f) => f.p_pole, render: (f) => pct(f.p_pole), numeric: true, group: "Forecast" },
          { key: "p_q3", label: "Q3", value: (f) => f.p_q3, render: (f) => pct(f.p_q3), numeric: true, group: "Forecast" },
          { key: "exp_pos", label: "Exp. pos", value: (f) => f.exp_pos, render: (f) => dec(f.exp_pos, 1), numeric: true, rank: true, group: "Forecast" },
          { key: "actual", label: "Qualified", value: (f) => pos.get(f.driver)?.position ?? 99, render: (f) => (pos.get(f.driver) ? `P${pos.get(f.driver)!.position}` : "–"), numeric: true, rank: true, group: "Result" },
        ]}
      />
    </section>
  );
}
