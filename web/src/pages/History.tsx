import * as Plot from "@observablehq/plot";
import { useCallback, useMemo, useState } from "react";
import { color } from "../colors";
import { DriverChip, DriverPicker, PositionChart, TeamDot } from "../components/f1";
import { Chart, Loading, Note, plotDefaults, Segmented, Table, Tiles } from "../components/ui";
import { rows } from "../data";
import { dayYear, delta, lapTime, raceTime, signed } from "../format";
import {
  H, link, type CareerRow, type CircuitRace, type CircuitRow, type CircuitsFile, type DriverSeason, type DriversFile,
  type HistoryDriverStanding, type HistoryIndex, type HistoryLap, type HistoryPit, type HistoryResult, type HistoryRound,
  type HistorySeason, type HistoryTeam, type HistoryTeamStanding, type RaceLapsFile, type SeasonFile,
} from "../history";
import { useData, useHash } from "../site";

/** Points as the record books print them: 9, 13.5, 3.14 (shared drives and fastest laps in the 1950s). */
const pts = (v: number | null | undefined) =>
  v === null || v === undefined ? "–" : Number.isInteger(v) ? String(v) : String(Number(v.toFixed(2)));

const TABS = [
  { value: "seasons", label: "Seasons" },
  { value: "drivers", label: "Drivers" },
  { value: "constructors", label: "Constructors" },
  { value: "circuits", label: "Circuits" },
] as const;
type Tab = (typeof TABS)[number]["value"];

/**
 * #history                     every season (and the tabs: /drivers, /constructors, /circuits)
 * #history/1988                one season
 * #history/1988/16[/S]         one race (or sprint)
 * #history/driver/senna        one driver's career
 * #history/circuit/monaco      every race at one circuit
 */
export default function History() {
  const [, a, b, c] = useHash().split("/");
  const index = useData<HistoryIndex>(H.index);
  if (index === undefined) return <Loading />;
  if (index === null) {
    return <p>The history files aren't there yet. Run <code>scripts/export_site.py --history-only</code> to write them.</p>;
  }
  if (a === "driver" && b) return <DriverView refId={b} />;
  if (a === "circuit" && b) return <CircuitView refId={b} />;
  const year = Number(a);
  if (year && Number(b)) return <RaceView year={year} round={Number(b)} code={c === "S" ? "S" : "R"} index={index} />;
  if (year) return <SeasonView year={year} index={index} />;
  const tab = (TABS.find((t) => t.value === a)?.value ?? "seasons") as Tab;
  return <Overview index={index} tab={tab} />;
}

// ---------------------------------------------------------------- overview and tabs

function Overview({ index, tab }: { index: HistoryIndex; tab: Tab }) {
  const titles = useMemo(() => {
    const n = new Map<string, number>();
    for (const s of index.seasons) if (s.champion_name) n.set(s.champion_name, (n.get(s.champion_name) ?? 0) + 1);
    return [...n.entries()].sort((x, y) => y[1] - x[1]);
  }, [index]);
  const most = titles.filter(([, n]) => n === titles[0]?.[1]).map(([name]) => name);
  return (
    <>
      <h2>Formula 1 History, {index.first_year}–{index.last_year}</h2>
      <p className="lede">
        Every World Championship season since the first Grand Prix at Silverstone in 1950: champions,
        standings, every race result, career records and every circuit. Races from 1996 have the running
        order lap by lap. The {index.last_year + 1} season so far is on the <a href="#season">Season</a> page.
      </p>
      <Tiles tiles={[
        { label: "Seasons", value: index.seasons.length, note: `${index.first_year} to ${index.last_year}` },
        { label: "Grands Prix", value: index.races.toLocaleString(), note: "championship rounds (Indianapolis 500 included 1950–60)" },
        { label: "Drivers", value: index.drivers.toLocaleString(), note: "who started at least one" },
        { label: "Most titles", value: titles[0]?.[1] ?? "–", note: most.join(" and ") },
      ]} />
      <div className="toolbar">
        <Segmented label="History" value={tab} onChange={(t) => { window.location.hash = t === "seasons" ? "history" : `history/${t}`; }}
                   options={TABS.map((t) => ({ value: t.value, label: t.label }))} />
      </div>
      {tab === "seasons" && <Seasons index={index} />}
      {tab === "drivers" && <Drivers />}
      {tab === "constructors" && <Constructors index={index} />}
      {tab === "circuits" && <Circuits />}
      <Note>
        Data: {index.source.name}, {index.source.licence}. Points are as awarded at the time, so they aren't
        comparable across eras (a win was worth 8 in 1950 and 25 today); until 1990 only a driver's best
        results counted. Pole is the car that started first.
      </Note>
    </>
  );
}

function Seasons({ index }: { index: HistoryIndex }) {
  return (
    <section>
      <h3>World Champions</h3>
      <p className="muted">The share of the season's races the champion won. Hover a dot for the season, click it to open it.</p>
      <ChampionsChart seasons={index.seasons} />
      <Table<HistorySeason>
        data={index.seasons}
        rowKey={(s) => s.year}
        sort="year"
        desc
        limit={25}
        onRow={(s) => { window.location.hash = link.season(s.year); }}
        cardTitle={(s) => <>{s.year} · {s.champion_name}</>}
        cardSub={["team"]}
        cardStats={["wins", "margin"]}
        columns={[
          { key: "year", label: "Season", value: (s) => s.year, render: (s) => <a href={link.season(s.year)}>{s.year}</a>, numeric: true },
          { key: "rounds", label: "Races", value: (s) => s.rounds, numeric: true },
          { key: "champion", label: "Drivers' champion", value: (s) => s.champion_name, render: (s) => <><TeamDot color={s.champion_color} /> <a href={link.driver(s.champion_ref!)} onClick={(e) => e.stopPropagation()}>{s.champion_name}</a></> },
          { key: "team", label: "Team", value: (s) => s.champion_team },
          { key: "points", label: "Points", value: (s) => s.champion_points, render: (s) => pts(s.champion_points), numeric: true },
          { key: "wins", label: "Wins", value: (s) => s.champion_wins, numeric: true },
          { key: "margin", label: "Margin", title: "Points ahead of the runner-up", value: (s) => s.margin, render: (s) => pts(s.margin), numeric: true },
          { key: "runner_up", label: "Runner-up", value: (s) => s.runner_up },
          { key: "constructor", label: "Constructors' champion", value: (s) => s.constructor ?? "", render: (s) => (s.constructor ? <><TeamDot color={s.constructor_color} /> {s.constructor}</> : <span className="muted">from 1958</span>) },
        ]}
      />
    </section>
  );
}

function ChampionsChart({ seasons }: { seasons: HistorySeason[] }) {
  const make = useCallback((width: number) => {
    const data = seasons.filter((s) => s.champion_wins !== null).map((s) => ({ ...s, share: s.champion_wins! / s.rounds }));
    const chart = Plot.plot({
      ...plotDefaults(width),
      height: 280,
      x: { label: null, tickFormat: "d", ticks: width < 500 ? 5 : 10 },
      y: { label: "Races won by the champion", tickFormat: "%", domain: [0, 1], grid: true },
      marks: [
        Plot.ruleY([0]),
        Plot.dot(data, { x: "year", y: "share", r: 4.5, fill: (s) => s.champion_color ?? color.neutral, stroke: color.surface }),
        Plot.tip(data, Plot.pointer({ x: "year", y: "share", title: (s) => `${s.year}: ${s.champion_name} (${s.champion_team})\n${s.champion_wins} wins from ${s.rounds} races` })),
      ],
    });
    // A click opens the season under the pointer.
    chart.addEventListener("click", () => {
      const s = (chart as unknown as { value?: HistorySeason }).value;
      if (s) window.location.hash = link.season(s.year);
    });
    return chart;
  }, [seasons]);
  return <Chart make={make} ariaLabel="Share of races won by each season's champion" />;
}

function Drivers() {
  const file = useData<DriversFile>(H.drivers);
  const [query, setQuery] = useState("");
  const [champions, setChampions] = useState<"all" | "champions">("all");
  const all = useMemo(() => {
    if (!file) return [];
    const years = file.drivers.title_years;
    return rows<CareerRow>(file.drivers).map((d, i) => ({ ...d, title_years: years[i] }));
  }, [file]);
  if (file === undefined) return <Loading />;
  if (file === null) return <p>No driver file.</p>;
  const q = query.trim().toLowerCase();
  const shown = all.filter((d) => (champions === "all" || d.titles > 0) && (!q || d.name.toLowerCase().includes(q)));
  return (
    <section>
      <h3>Every Driver</h3>
      <div className="toolbar">
        <input type="search" placeholder="Find a driver" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Find a driver" />
        <Segmented label="Which drivers" value={champions} onChange={setChampions}
                   options={[{ value: "all", label: "Everyone" }, { value: "champions", label: "Champions" }]} />
      </div>
      <Table<CareerRow & { title_years: number[] }>
        data={shown}
        rowKey={(d) => d.ref}
        sort="wins"
        limit={30}
        onRow={(d) => { window.location.hash = link.driver(d.ref); }}
        cardTitle={(d) => <><TeamDot color={d.color} /> {d.name}</>}
        cardSub={["years"]}
        cardStats={["wins", "titles", "starts"]}
        columns={[
          { key: "name", label: "Driver", value: (d) => d.name, render: (d) => <><TeamDot color={d.color} /> <a href={link.driver(d.ref)}>{d.name}</a></> },
          { key: "country", label: "Nat.", value: (d) => d.country ?? "" },
          { key: "years", label: "Years", value: (d) => (d.first === d.last ? `${d.first}` : `${d.first}–${d.last}`) },
          { key: "titles", label: "Titles", value: (d) => d.titles, numeric: true },
          { key: "starts", label: "Starts", value: (d) => d.starts, numeric: true },
          { key: "wins", label: "Wins", value: (d) => d.wins, numeric: true },
          { key: "win_rate", label: "Win %", value: (d) => (d.starts ? d.wins / d.starts : null), render: (d) => (d.starts ? `${((100 * d.wins) / d.starts).toFixed(1)}%` : "–"), numeric: true },
          { key: "podiums", label: "Podiums", value: (d) => d.podiums, numeric: true },
          { key: "poles", label: "Poles", value: (d) => d.poles, numeric: true },
          { key: "fastest", label: "Fastest laps", title: "Recorded from 2004", value: (d) => d.fastest, numeric: true },
          { key: "points", label: "Points", value: (d) => d.points, render: (d) => pts(d.points), numeric: true },
        ]}
      />
    </section>
  );
}

function Constructors({ index }: { index: HistoryIndex }) {
  const teams = useMemo(() => rows<HistoryTeam>(index.teams), [index]);
  return (
    <section>
      <h3>Every Constructor</h3>
      <p className="muted">
        A race counts once per constructor, however many cars it entered. Until the 1980s many teams raced
        under the chassis and engine name together (Lotus-Climax, Cooper-Maserati), and they're listed that way.
      </p>
      <Table<HistoryTeam>
        data={teams}
        rowKey={(t) => t.team}
        sort="wins"
        limit={30}
        cardTitle={(t) => <><TeamDot color={t.color} /> {t.team}</>}
        cardSub={["years"]}
        cardStats={["wins", "titles", "races"]}
        columns={[
          { key: "team", label: "Constructor", value: (t) => t.team, render: (t) => <><TeamDot color={t.color} /> {t.team}</> },
          { key: "years", label: "Years", value: (t) => (t.first === t.last ? `${t.first}` : `${t.first}–${t.last}`) },
          { key: "races", label: "Races", value: (t) => t.races, numeric: true },
          { key: "wins", label: "Wins", value: (t) => t.wins, numeric: true },
          { key: "podium_races", label: "Podium races", title: "Races with at least one car on the podium", value: (t) => t.podium_races, numeric: true },
          { key: "poles", label: "Poles", value: (t) => t.poles, numeric: true },
          { key: "titles", label: "Constructors' titles", value: (t) => t.titles, numeric: true },
          { key: "driver_titles", label: "Drivers' titles", value: (t) => t.driver_titles, numeric: true },
        ]}
      />
    </section>
  );
}

function Circuits() {
  const file = useData<CircuitsFile>(H.circuits);
  const circuits = useMemo(() => rows<CircuitRow>(file?.circuits), [file]);
  if (file === undefined) return <Loading />;
  return (
    <section>
      <h3>Every Circuit</h3>
      <Table<CircuitRow>
        data={circuits}
        rowKey={(c) => c.circuit}
        sort="races"
        limit={30}
        onRow={(c) => { window.location.hash = link.circuit(c.circuit); }}
        cardTitle={(c) => c.name}
        cardSub={["place"]}
        cardStats={["races", "top"]}
        columns={[
          { key: "name", label: "Circuit", value: (c) => c.name, render: (c) => <a href={link.circuit(c.circuit)}>{c.name}</a> },
          { key: "place", label: "Location", value: (c) => `${c.locality}, ${c.country}` },
          { key: "races", label: "Grands Prix", value: (c) => c.races, numeric: true },
          { key: "years", label: "Years", value: (c) => (c.first === c.last ? `${c.first}` : `${c.first}–${c.last}`) },
          { key: "top", label: "Most wins", value: (c) => c.top_wins, render: (c) => `${c.top_winner} (${c.top_wins})`, numeric: true },
        ]}
      />
    </section>
  );
}

// ---------------------------------------------------------------- one season

function SeasonView({ year, index }: { year: number; index: HistoryIndex }) {
  const file = useData<SeasonFile>(H.season(year));
  const summary = index.seasons.find((s) => s.year === year);
  const crumb = (
    <p className="crumb">
      <a href="#history">← All seasons</a>
      {year > index.first_year && <> · <a href={link.season(year - 1)}>{year - 1}</a></>}
      {year < index.last_year && <> · <a href={link.season(year + 1)}>{year + 1}</a></>}
    </p>
  );
  if (file === undefined) return <>{crumb}<Loading /></>;
  if (file === null || !summary) return <>{crumb}<p>No data for {year}.</p></>;
  const [champ] = file.drivers;
  const dropped = file.drivers.some((d) => Math.abs(d.points - d.points_scored) > 0.01);
  const mostWins = [...file.drivers].sort((a, b) => b.wins - a.wins)[0];
  return (
    <>
      {crumb}
      <h2>The {year} Season</h2>
      <p className="lede">
        {file.rounds.length} races, {dayYear(file.rounds[0]?.date)} to {dayYear(file.rounds.at(-1)?.date)}.
        {" "}{champ && <>{champ.name} won the title by {pts(summary.margin)} points from {summary.runner_up}.</>}
      </p>
      <Tiles tiles={[
        { label: "Drivers' champion", value: champ ? <><DriverChip code={champ.driver} color={champ.color} /> {champ.name}</> : "–", note: champ ? `${champ.team} · ${pts(champ.points)} pts` : undefined },
        { label: "Margin", value: `${pts(summary.margin)} pts`, note: `over ${summary.runner_up}` },
        { label: "Constructors' champion", value: file.teams[0]?.team ?? "Not awarded", note: file.teams[0] ? `${pts(file.teams[0].points)} pts` : "the Constructors' Championship began in 1958" },
        { label: "Most wins", value: mostWins ? `${mostWins.wins}` : "–", note: mostWins?.name },
      ]} />

      <section>
        <h3>Drivers' Championship</h3>
        {dropped && (
          <p className="muted">
            Only each driver's best results counted this season, so the points that count can be fewer than
            the points scored (the "All results" column).
          </p>
        )}
        <Table<HistoryDriverStanding>
          data={file.drivers}
          rowKey={(d) => d.ref}
          sort="position"
          limit={20}
          onRow={(d) => { window.location.hash = link.driver(d.ref); }}
          cardTitle={(d) => <><span className="muted">P{d.position}</span> <DriverChip code={d.driver} color={d.color} /> {d.name}</>}
          cardSub={["team"]}
          cardStats={["points", "wins", "podiums"]}
          columns={[
            { key: "position", label: "Pos", value: (d) => d.position, numeric: true, rank: true },
            { key: "driver", label: "Driver", value: (d) => d.name, render: (d) => <><DriverChip code={d.driver} color={d.color} /> <a href={link.driver(d.ref)}>{d.name}</a></> },
            { key: "team", label: "Team", value: (d) => d.team, wrap: true },
            { key: "points", label: "Points", value: (d) => d.points, render: (d) => pts(d.points), numeric: true },
            ...(dropped ? [{ key: "scored", label: "All results", title: "Points scored in every race, before dropped results", value: (d: HistoryDriverStanding) => d.points_scored, render: (d: HistoryDriverStanding) => pts(d.points_scored), numeric: true }] : []),
            { key: "wins", label: "Wins", value: (d) => d.wins, numeric: true },
            { key: "podiums", label: "Podiums", value: (d) => d.podiums, numeric: true },
            { key: "poles", label: "Poles", value: (d) => d.poles, numeric: true },
            { key: "starts", label: "Starts", value: (d) => d.starts, numeric: true },
            { key: "best", label: "Best", value: (d) => d.best, render: (d) => (d.best ? `P${d.best}` : "–"), numeric: true, rank: true },
          ]}
        />
      </section>

      <section>
        <h3>Points Through the Season</h3>
        <Progression file={file} />
      </section>

      {file.teams.length > 0 && (
        <section>
          <h3>Constructors' Championship</h3>
          <Table<HistoryTeamStanding>
            data={file.teams}
            rowKey={(t) => t.team}
            sort="position"
            cards={false}
            columns={[
              { key: "position", label: "Pos", value: (t) => t.position, numeric: true, rank: true },
              { key: "team", label: "Constructor", value: (t) => t.team, render: (t) => <><TeamDot color={t.color} /> {t.team}</> },
              { key: "points", label: "Points", value: (t) => t.points, render: (t) => pts(t.points), numeric: true },
              { key: "wins", label: "Wins", value: (t) => t.wins, numeric: true },
            ]}
          />
        </section>
      )}

      <section>
        <h3>Races</h3>
        <Table<HistoryRound>
          data={file.rounds}
          rowKey={(r) => r.round}
          sort="round"
          onRow={(r) => { window.location.hash = link.race(year, r.round); }}
          cardTitle={(r) => `${r.round}. ${r.event}`}
          cardSub={["date", "circuit"]}
          cardStats={["winner"]}
          columns={[
            { key: "round", label: "Round", value: (r) => r.round, numeric: true, rank: true },
            { key: "event", label: "Grand Prix", value: (r) => r.event, render: (r) => <a href={link.race(year, r.round)}>{r.event}</a> },
            { key: "circuit", label: "Circuit", value: (r) => r.circuit_name, render: (r) => <a href={link.circuit(r.circuit)} onClick={(e) => e.stopPropagation()}>{r.circuit_name}</a>, wrap: true },
            { key: "date", label: "Date", value: (r) => dayYear(r.date) },
            { key: "winner", label: "Winner", value: (r) => r.winner_name ?? "", render: (r) => (r.winner ? <><DriverChip code={r.winner} color={r.winner_color} /> {r.winner_name}</> : "–") },
            { key: "team", label: "Team", value: (r) => r.winner_team ?? "" },
            { key: "pole", label: "Pole", value: (r) => r.pole ?? "" },
            ...(file.rounds.some((r) => r.sprint) ? [{ key: "sprint", label: "Sprint", value: (r: HistoryRound) => (r.sprint ? "Sprint" : ""), render: (r: HistoryRound) => (r.sprint ? <a className="tag" href={link.race(year, r.round, "S")} onClick={(e) => e.stopPropagation()}>Sprint</a> : "") }] : []),
          ]}
        />
      </section>
    </>
  );
}

/** Championship points (as counted) after each round, for the season's top ten. */
function Progression({ file }: { file: SeasonFile }) {
  const make = useCallback((width: number) => {
    const prog = rows<{ round: number; driver: string; points: number; position: number }>(file.progression);
    const top = file.drivers.slice(0, 10);
    const colorOf = new Map(top.map((d) => [d.driver, d.color ?? color.neutral]));
    const seen = new Set<string>();
    const dashed = new Set(top.filter((d) => { const c = d.color ?? ""; const s = seen.has(c); seen.add(c); return s; }).map((d) => d.driver));
    const latest = Math.max(...prog.map((p) => p.round));
    return Plot.plot({
      ...plotDefaults(width),
      height: 380,
      marginRight: 44,
      x: { label: "Round", tickFormat: "d", ticks: Math.min(latest, 12) },
      y: { label: "Points", grid: true },
      marks: [
        ...top.map((d) => Plot.line(prog.filter((p) => p.driver === d.driver), {
          x: "round", y: "points", stroke: colorOf.get(d.driver), strokeWidth: 2, strokeDasharray: dashed.has(d.driver) ? "4,3" : undefined,
        })),
        Plot.text(prog.filter((p) => p.round === latest), { x: "round", y: "points", text: "driver", dx: 6, textAnchor: "start", fill: color.ink2, fontSize: 11 }),
        Plot.tip(prog, Plot.pointer({ x: "round", y: "points", title: (p) => `${p.driver} after round ${p.round}: ${pts(p.points)} pts (P${p.position})` })),
      ],
    });
  }, [file]);
  return <Chart make={make} ariaLabel="Championship points through the season, top ten drivers" />;
}

// ---------------------------------------------------------------- one race

/** The running-order tooltip: position and lap time. Module-level, so the chart isn't redrawn on every render. */
const lapTip = (l: HistoryLap) => `${l.driver} · lap ${l.lap}\nP${l.pos}${l.lap_time ? ` · ${lapTime(l.lap_time)}` : ""}${l.pit ? "\nPitted" : ""}`;

function RaceView({ year, round, code, index }: { year: number; round: number; code: "R" | "S"; index: HistoryIndex }) {
  const file = useData<SeasonFile>(H.season(year));
  const ev = file?.rounds.find((r) => r.round === round);
  const lapsFile = useData<RaceLapsFile>(ev?.has_laps || code === "S" ? H.race(year, round, code) : null);
  const results = useMemo(() => rows<HistoryResult>(file?.results).filter((r) => r.round === round && r.session === code), [file, round, code]);
  const laps = useMemo(() => rows<HistoryLap>(lapsFile?.laps).filter((l): l is HistoryLap & { pos: number } => l.pos !== null), [lapsFile]);
  const pits = useMemo(() => rows<HistoryPit>(lapsFile?.pits), [lapsFile]);
  const [highlight, setHighlight] = useState<string | null>(null);

  if (file === undefined) return <Loading />;
  if (!file || !ev) return <p>No such race. <a href={link.season(year)}>The {year} season</a></p>;
  const prev = file.rounds.find((r) => r.round === round - 1);
  const next = file.rounds.find((r) => r.round === round + 1);
  const winner = results[0];
  const fastest = results.find((r) => r.fl_rank === 1);
  const pole = results.find((r) => r.grid === 1);
  const lapped = new Set(laps.map((l) => l.driver));
  const charted = results.filter((r) => lapped.has(r.driver));
  const stops = new Map<string, HistoryPit[]>();
  for (const p of pits) stops.set(p.driver, [...(stops.get(p.driver) ?? []), p]);
  const started = results.filter((r) => r.started);

  return (
    <>
      <p className="crumb">
        <a href={link.season(year)}>← The {year} season</a>
        {prev && <> · <a href={link.race(year, prev.round)}>Round {prev.round}</a></>}
        {next && <> · <a href={link.race(year, next.round)}>Round {next.round}</a></>}
      </p>
      <h2>{year} {ev.event}{code === "S" ? " Sprint" : ""}</h2>
      <p className="lede">
        Round {round} of {file.rounds.length} · <a href={link.circuit(ev.circuit)}>{ev.circuit_name}</a>, {ev.locality}, {ev.country} · {dayYear(ev.date)}
        {code === "R" && ev.laps ? <> · {ev.laps} laps</> : null}
        {ev.wikipedia && <> · <a href={ev.wikipedia} target="_blank" rel="noreferrer">Wikipedia</a></>}
      </p>
      {ev.sprint && (
        <div className="toolbar">
          <Segmented label="Session" value={code} onChange={(c) => { window.location.hash = link.race(year, round, c).slice(1); }}
                     options={[{ value: "R", label: "Grand Prix" }, { value: "S", label: "Sprint" }]} />
        </div>
      )}
      <Tiles tiles={[
        { label: "Winner", value: winner ? <><DriverChip code={winner.driver} color={winner.color} /> {winner.name}</> : "–", note: winner ? `${winner.team}${winner.grid ? ` · from P${winner.grid}` : ""}` : undefined },
        { label: "Race time", value: raceTime(winner?.time), note: winner?.time && winner.laps ? `${winner.laps} laps` : undefined },
        { label: code === "S" ? "Sprint pole" : "Pole", value: pole ? <><DriverChip code={pole.driver} color={pole.color} /> {pole.name}</> : "–", note: pole?.team },
        { label: "Finishers", value: `${results.filter((r) => r.pos !== null).length} of ${started.length}`, note: `${results.length - started.length > 0 ? `${results.length - started.length} entered but didn't start` : "every entry started"}` },
        ...(fastest ? [{ label: "Fastest lap", value: <><DriverChip code={fastest.driver} color={fastest.color} /> {fastest.name}</>, note: fastest.team }] : []),
      ]} />

      <section>
        <h3>Result</h3>
        <Table<HistoryResult>
          data={results}
          rowKey={(r) => `${r.ref}-${r.number}`}
          sort="pos"
          onRow={(r) => { window.location.hash = link.driver(r.ref); }}
          cardTitle={(r) => <><span className="muted">{r.pos ? `P${r.pos}` : r.started ? "DNF" : "DNS"}</span> <DriverChip code={r.driver} color={r.color} /> {r.name}</>}
          cardSub={["team"]}
          cardStats={["gained", "gap", "points"]}
          columns={[
            { key: "pos", label: "Pos", value: (r) => r.pos, render: (r) => r.pos ?? (r.started ? "–" : "DNS"), numeric: true, rank: true },
            { key: "driver", label: "Driver", value: (r) => r.name, render: (r) => <><DriverChip code={r.driver} color={r.color} /> <a href={link.driver(r.ref)}>{r.name}</a></> },
            { key: "team", label: "Team", value: (r) => r.team },
            { key: "number", label: "No.", value: (r) => r.number, numeric: true },
            { key: "grid", label: "Grid", value: (r) => r.grid, render: (r) => r.grid ?? (r.started ? "Pit lane" : "–"), numeric: true, rank: true },
            { key: "gained", label: "+/−", title: "Places gained from the grid", value: (r) => (r.grid && r.pos ? r.grid - r.pos : null), render: (r) => (r.grid && r.pos ? signed(r.grid - r.pos, 0) : "–"), numeric: true },
            { key: "laps", label: "Laps", value: (r) => r.laps, numeric: true },
            { key: "gap", label: "Time / status", value: (r) => r.gap ?? (r.pos ? 1e5 - (r.laps ?? 0) : null), render: (r) => (r.pos === 1 ? raceTime(r.time) : r.gap !== null ? delta(r.gap) : r.status || "–") },
            { key: "points", label: "Pts", value: (r) => r.points, render: (r) => pts(r.points), numeric: true },
          ]}
        />
      </section>

      {charted.length > 0 ? (
        <section>
          <h3>Running Order</h3>
          <p className="muted">Position at the end of each lap.{pits.length ? " Circles are pit stops." : ""} Tap a driver to pick them out.</p>
          <DriverPicker results={charted} value={highlight} onChange={setHighlight} />
          <PositionChart laps={laps} results={charted} highlight={highlight} tip={lapTip} />
        </section>
      ) : lapsFile === undefined && ev.has_laps ? <Loading /> : (
        <Note>Lap-by-lap timing starts in 1996, so there's no running-order chart for this race.</Note>
      )}

      {pits.length > 0 && (
        <section>
          <h3>Pit Stops</h3>
          <p className="muted">Pit lane time, entry to exit, for each stop.</p>
          <Table<HistoryResult>
            data={charted.filter((r) => stops.has(r.driver))}
            rowKey={(r) => r.driver}
            sort="stops"
            cards={false}
            columns={[
              { key: "driver", label: "Driver", value: (r) => r.name, render: (r) => <DriverChip code={r.driver} color={r.color} /> },
              { key: "stops", label: "Stops", value: (r) => stops.get(r.driver)!.length, numeric: true },
              { key: "laps", label: "On laps", value: (r) => stops.get(r.driver)!.map((p) => p.lap).join(", ") },
              { key: "best", label: "Quickest", value: (r) => Math.min(...stops.get(r.driver)!.map((p) => p.duration ?? Infinity)), render: (r) => { const v = Math.min(...stops.get(r.driver)!.map((p) => p.duration ?? Infinity)); return Number.isFinite(v) ? `${v.toFixed(1)} s` : "–"; }, numeric: true },
            ]}
          />
        </section>
      )}
      {index.last_year === year && round === file.rounds.length && (
        <p className="note">The season after this one is on the <a href="#season">Season</a> page.</p>
      )}
    </>
  );
}

// ---------------------------------------------------------------- one driver

function DriverView({ refId }: { refId: string }) {
  const file = useData<DriversFile>(H.drivers);
  const data = useMemo(() => {
    if (!file) return null;
    const all = rows<CareerRow>(file.drivers);
    const i = all.findIndex((d) => d.ref === refId);
    if (i < 0) return null;
    return {
      d: all[i], titles: file.drivers.title_years[i], teams: file.drivers.teams[i],
      seasons: rows<DriverSeason>(file.seasons).filter((s) => s.ref === refId),
    };
  }, [file, refId]);
  if (file === undefined) return <Loading />;
  if (!data) return <p>No such driver. <a href="#history/drivers">All drivers</a></p>;
  const { d, titles, teams, seasons } = data;
  return (
    <>
      <p className="crumb"><a href="#history/drivers">← All drivers</a></p>
      <h2>{d.name}</h2>
      <p className="lede">
        {d.country && <>{d.country} · </>}{d.born && <>born {dayYear(d.born)} · </>}
        {d.seasons} season{d.seasons === 1 ? "" : "s"}, {d.first === d.last ? d.first : `${d.first}–${d.last}`} · {teams.join(", ")}
        {d.wikipedia && <> · <a href={d.wikipedia} target="_blank" rel="noreferrer">Wikipedia</a></>}
      </p>
      <Tiles tiles={[
        { label: "World titles", value: d.titles, note: titles.length ? titles.join(", ") : "none" },
        { label: "Wins", value: d.wins, note: d.starts ? `${((100 * d.wins) / d.starts).toFixed(1)}% of ${d.starts} starts` : undefined },
        { label: "Podiums", value: d.podiums },
        { label: "Poles", value: d.poles },
        { label: "Points", value: pts(d.points), note: "as counted at the time" },
      ]} />
      {seasons.length > 1 && (
        <section>
          <h3>Championship Position by Season</h3>
          <CareerChart seasons={seasons} />
        </section>
      )}
      <section>
        <h3>Season by Season</h3>
        <Table<DriverSeason>
          data={seasons}
          rowKey={(s) => s.year}
          sort="year"
          onRow={(s) => { window.location.hash = link.season(s.year); }}
          cardTitle={(s) => <>{s.year} · {s.team}</>}
          cardStats={["position", "points", "wins"]}
          columns={[
            { key: "year", label: "Season", value: (s) => s.year, render: (s) => <a href={link.season(s.year)}>{s.year}</a>, numeric: true },
            { key: "team", label: "Team", value: (s) => s.team, render: (s) => <><TeamDot color={s.color} /> {s.team}</> },
            { key: "position", label: "Pos", value: (s) => s.position, render: (s) => (s.position ? `P${s.position}` : "–"), numeric: true, rank: true },
            { key: "points", label: "Points", value: (s) => s.points, render: (s) => pts(s.points), numeric: true },
            { key: "starts", label: "Starts", value: (s) => s.starts, numeric: true },
            { key: "wins", label: "Wins", value: (s) => s.wins, numeric: true },
            { key: "podiums", label: "Podiums", value: (s) => s.podiums, numeric: true },
            { key: "poles", label: "Poles", value: (s) => s.poles, numeric: true },
            { key: "best", label: "Best", value: (s) => s.best, render: (s) => (s.best ? `P${s.best}` : "–"), numeric: true, rank: true },
          ]}
        />
      </section>
    </>
  );
}

function CareerChart({ seasons }: { seasons: DriverSeason[] }) {
  const make = useCallback((width: number) => {
    const data = seasons.filter((s) => s.position !== null);
    const worst = Math.max(10, ...data.map((s) => s.position!));
    return Plot.plot({
      ...plotDefaults(width),
      height: 260,
      x: { label: null, tickFormat: "d", ticks: Math.min(seasons.length, width < 500 ? 5 : 10) },
      y: { label: "Championship position", reverse: true, domain: [1, worst], grid: true },
      marks: [
        Plot.line(data, { x: "year", y: "position", stroke: color.muted, strokeWidth: 1.25 }),
        Plot.dot(data, { x: "year", y: "position", r: 5, fill: (s) => s.color, stroke: color.surface }),
        Plot.tip(data, Plot.pointer({ x: "year", y: "position", title: (s) => `${s.year} · ${s.team}\nP${s.position}, ${pts(s.points)} pts, ${s.wins} win${s.wins === 1 ? "" : "s"}` })),
      ],
    });
  }, [seasons]);
  return <Chart make={make} ariaLabel="Championship position in each season, coloured by team" />;
}

// ---------------------------------------------------------------- one circuit

function CircuitView({ refId }: { refId: string }) {
  const file = useData<CircuitsFile>(H.circuits);
  const c = useMemo(() => rows<CircuitRow>(file?.circuits).find((x) => x.circuit === refId), [file, refId]);
  const races = useMemo(() => rows<CircuitRace>(file?.races).filter((r) => r.circuit === refId), [file, refId]);
  const winners = useMemo(() => {
    const n = new Map<string, { name: string; ref: string; wins: number; color: string }>();
    for (const r of races) {
      const w = n.get(r.winner_ref) ?? { name: r.winner_name, ref: r.winner_ref, wins: 0, color: r.color };
      w.wins++;
      n.set(r.winner_ref, w);
    }
    return [...n.values()].sort((a, b) => b.wins - a.wins);
  }, [races]);
  if (file === undefined) return <Loading />;
  if (!c) return <p>No such circuit. <a href="#history/circuits">All circuits</a></p>;
  return (
    <>
      <p className="crumb"><a href="#history/circuits">← All circuits</a></p>
      <h2>{c.name}</h2>
      <p className="lede">{c.locality}, {c.country} · {c.races} Grand{c.races === 1 ? "" : "s"} Prix, {c.first === c.last ? c.first : `${c.first}–${c.last}`}</p>
      <Tiles tiles={[
        { label: "Grands Prix", value: c.races },
        { label: "First", value: c.first },
        { label: "Latest", value: c.last },
        { label: "Most wins", value: winners[0]?.wins ?? "–", note: winners.filter((w) => w.wins === winners[0]?.wins).map((w) => w.name).join(", ") },
      ]} />
      <section>
        <h3>Every Winner</h3>
        <Table<CircuitRace>
          data={races}
          rowKey={(r) => `${r.year}-${r.round}`}
          sort="year"
          desc
          limit={25}
          onRow={(r) => { window.location.hash = link.race(r.year, r.round); }}
          cardTitle={(r) => <>{r.year} · {r.winner_name}</>}
          cardSub={["team"]}
          columns={[
            { key: "year", label: "Season", value: (r) => r.year, render: (r) => <a href={link.race(r.year, r.round)}>{r.year}</a>, numeric: true },
            { key: "event", label: "Race", value: (r) => r.event },
            { key: "winner", label: "Winner", value: (r) => r.winner_name, render: (r) => <><DriverChip code={r.winner} color={r.color} /> {r.winner_name}</> },
            { key: "team", label: "Team", value: (r) => r.team },
            { key: "pole", label: "Pole", value: (r) => r.pole ?? "" },
          ]}
        />
      </section>
      {winners.length > 1 && (
        <section>
          <h3>Most Wins Here</h3>
          <Table<{ name: string; ref: string; wins: number; color: string }>
            data={winners.slice(0, 10)}
            rowKey={(w) => w.ref}
            sort="wins"
            cards={false}
            columns={[
              { key: "name", label: "Driver", value: (w) => w.name, render: (w) => <><TeamDot color={w.color} /> <a href={link.driver(w.ref)}>{w.name}</a></> },
              { key: "wins", label: "Wins", value: (w) => w.wins, numeric: true },
            ]}
          />
        </section>
      )}
    </>
  );
}
