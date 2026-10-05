import * as Plot from "@observablehq/plot";
import { useCallback, useEffect, useMemo, useState } from "react";
import { color } from "../colors";
import { ChanceBars, DriverChip, Plan, SessionBadge } from "../components/f1";
import { liveSession, Radar, useNow } from "../components/Radar";
import { FORM_CLIP, WhyOdds } from "../components/WhyOdds";
import { Chart, Legend, Loading, Note, plotDefaults, Segmented, Table, Tiles } from "../components/ui";
import {
  forecastFile, isQuali, load, rows, SESSION_LABEL, sessionDone, sessionHash, sessionOdds, sessionStart, sessionWhy, weekendSessions,
  type CalendarEvent, type Forecast, type ForecastDriver, type QualiForecastDriver, type QualiStrategy, type SessionCode,
  type StrategyForecast, type StrategyPlan, type WeatherForecast, type WeatherHour,
} from "../data";
import { dec, delta, lapTime, pct, shortEvent, signed, when } from "../format";
import { useData, useHash, useSite } from "../site";

// Fixed slots by rank in the forecast file, as the dashboard's strategy colours.
const STRATEGY_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7", "#e87ba4", "#008300"];

export default function NextRace() {
  const site = useSite();
  const hash = useHash();
  const [, roundPart, codePart] = hash.split("/");
  const upcoming = site.meta.calendar.filter((e) => !e.done_R && site.meta.forecasts.includes(e.round));
  const asked = Number(roundPart);
  // While a session is on, open on its round (an export during the weekend moves next_round on).
  const now = useNow();
  const live = upcoming.find((e) => liveSession(e, now));
  const round = upcoming.some((e) => e.round === asked) ? asked : live?.round ?? site.meta.next_round ?? upcoming[0]?.round;
  const ev = round ? site.event.get(round) : undefined;
  const forecast = useData<Forecast>(round ? forecastFile(round) : null);

  if (!ev) return <p>The season is over: no races left to forecast. <a href="#season">The final standings</a></p>;
  if (forecast === undefined) return <Loading />;
  if (forecast === null) return <p>No forecast for {ev.event} yet.</p>;

  const codes = weekendSessions(ev).filter((c) => sessionOdds(forecast, c, "pre"));
  // The session asked for, else the weekend's next one still to run.
  const code: SessionCode = codes.find((c) => c === codePart) ?? codes.find((c) => !sessionDone(ev, c)) ?? "R";
  // ?radar in the address shows the radar outside a session (for checking the layout).
  const session = liveSession(ev, now) ?? (new URLSearchParams(window.location.search).has("radar") ? { code: "R" as const, start: now } : null);
  const go = (r: number, c: SessionCode) => { window.location.hash = `next/${r}/${c}`; };
  const fav = (c: SessionCode) => {
    const odds = sessionOdds(forecast, c);
    if (!odds) return null;
    const key = isQuali(c) ? "p_pole" : "p_win";
    return rows<Record<string, any>>(odds).sort((a, b) => b[key] - a[key])[0] as { driver: string; color: string; p_win?: number; p_pole?: number } | undefined;
  };
  const ahead = forecast.rounds_ahead ?? 1;

  return (
    <>
      <h2>Round {ev.round}: {ev.event}</h2>
      <p className="lede">
        {ev.location}, {ev.country} · {weekendSessions(ev).map((c, i) => (
          <span key={c}>{i > 0 && " · "}{SESSION_LABEL[c].toLowerCase()} {when(sessionStart(ev, c))}</span>
        ))}.
        {ev.sprint_utc ? " A sprint weekend: Sprint Qualifying sets the Sprint grid, Qualifying the Grand Prix's." : ""}
      </p>
      {session && ev.lat != null && ev.lon != null && (
        <section>
          <h3><span className="live-dot" aria-hidden="true" />Live Rain Radar</h3>
          <p className="muted">
            {ev.event} {SESSION_LABEL[session.code].toLowerCase()} {now < session.start ? "starts soon" : "is on"}: rain
            around {ev.location} over the last two hours. It's shown only while a session is on, as the radar isn't kept.
          </p>
          <Radar lat={ev.lat} lon={ev.lon} place={ev.location} />
        </section>
      )}
      <div className="toolbar">
        {upcoming.length > 1 && (
          <label>
            Round
            <select value={round} onChange={(e) => go(Number(e.target.value), "R")}>
              {upcoming.map((e) => <option key={e.round} value={e.round}>{e.round}. {shortEvent(e.event)}{e.round === site.meta.next_round ? " (next)" : ""}</option>)}
            </select>
          </label>
        )}
        {codes.length > 1 && (
          <Segmented label="Session" value={code} onChange={(c) => go(ev.round, c)}
                     options={codes.map((c) => ({ value: c, label: SESSION_LABEL[c] }))} />
        )}
      </div>

      <Tiles tiles={codes.map((c) => {
        const f = fav(c);
        return {
          label: `${SESSION_LABEL[c]} favourite`,
          value: f ? <><DriverChip code={f.driver} color={f.color} /> {pct(isQuali(c) ? f.p_pole : f.p_win)}</> : "–",
          note: sessionDone(ev, c) ? "finished" : isQuali(c) ? "pole chance" : "win chance",
        };
      })} />

      {sessionDone(ev, code) && (
        <Note>The {SESSION_LABEL[code].toLowerCase()} has finished: <a href={`#${sessionHash(ev.round, code)}`}>see the result and analysis</a>. Its forecast is below.</Note>
      )}
      {isQuali(code)
        ? <QualiOdds forecast={forecast} code={code as "Q" | "SQ"} ahead={ahead} />
        : <RaceOdds forecast={forecast} code={code as "R" | "S"} ahead={ahead} />}
      {isQuali(code) && forecast.quali_strategy?.[code as "Q" | "SQ"] && (
        <QualiPlan qs={forecast.quali_strategy[code as "Q" | "SQ"]!} code={code as "Q" | "SQ"} />
      )}
      {code === "R" && forecast.strategy?.weather && <Weather w={forecast.strategy.weather} start={ev.race_utc} minutes={95} />}
      {code === "R" && forecast.strategy && <Strategy s={forecast.strategy} />}
      {code === "S" && forecast.sprint_strategy?.weather && <Weather w={forecast.sprint_strategy.weather} start={ev.sprint_utc} minutes={32} />}
      {code === "S" && forecast.sprint_strategy && <Strategy s={forecast.sprint_strategy} />}
      <SeasonAhead upcoming={upcoming} />
    </>
  );
}

/** Before the weekend, or after the session's earlier ones (its qualifying and grid). */
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
  return { which: after.length ? which : ("pre" as const), toggle, after };
}

const aheadText = (ahead: number) =>
  ahead > 1 ? ` It's ${ahead} rounds away, so each driver's form is also allowed to drift by then: the odds are flatter than for the next round.` : "";

function RaceOdds({ forecast, code, ahead }: { forecast: Forecast; code: "R" | "S"; ahead: number }) {
  const { which, toggle, after } = useWhich(forecast, code);
  const drivers = useMemo(() => rows<ForecastDriver>(sessionOdds(forecast, code, which)).sort((a, b) => b.p_win - a.p_win), [forecast, code, which]);
  if (!drivers.length) return null;
  const grid = drivers.some((d) => d.grid != null);
  const circuit = drivers.some((d) => d.circuit != null);
  const penalty = drivers.some((d) => d.penalty != null);
  const name = code === "S" ? "sprint" : "race";
  return (
    <section>
      <h3>Who Wins the {SESSION_LABEL[code]}? <SessionBadge code={code} /></h3>
      <p className="muted">
        The {name} played 10,000 times. Each driver's pace is drawn around their expected pace: recent race
        pace (sprints count three-quarters) blended with qualifying pace, a session more than {FORM_CLIP}% off a
        driver's usual pace capped as an outlier{circuit ? ", plus how their team went at this circuit last season" : ""}
        {grid ? <>, and the grid from this weekend's {after.at(-1)}, grid penalties applied (track position is worth something)</> : ""}
        {!grid && penalty ? ", and announced grid penalties (the places they're expected to lose)" : ""}.
        Retirements come from their (shrunk) DNF rate{code === "S" ? ", lower over a sprint's shorter distance" : ""}.
        {code === "S" ? " Points go to the top eight (8 to 1)." : ""}{which === "pre" ? aheadText(ahead) : ""}
      </p>
      {toggle}
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
          { key: "pace", label: "Pace", title: "Expected performance against the field median (negative = faster): race and qualifying form, plus the circuit, grid and penalty terms", value: (d) => -(d.pace ?? d.form ?? 0), render: (d) => `${signed(d.pace ?? d.form, 2)}%`, numeric: true },
          ...(circuit ? [{ key: "circuit", label: "Circuit", title: "The team's pace here last season against its season average (negative = better here)", value: (d: ForecastDriver) => -(d.circuit ?? 0), render: (d: ForecastDriver) => (d.circuit == null ? "–" : `${signed(d.circuit, 2)}%`), numeric: true }] : []),
          ...(grid ? [{ key: "grid", label: "Grid", value: (d: ForecastDriver) => d.grid ?? 99, render: (d: ForecastDriver) => d.grid ?? "–", numeric: true, rank: true }] : []),
          ...(penalty ? [{ key: "penalty", label: "Penalty", title: "Announced grid penalty: places back, back of the grid or a pit-lane start", value: (d: ForecastDriver) => d.penalty_places ?? 0, render: (d: ForecastDriver) => (d.penalty == null ? "–" : d.penalty === "back" ? "Back" : d.penalty === "pit" ? "Pit lane" : `+${d.penalty}`), numeric: true }] : []),
          { key: "p_win", label: "Win", value: (d) => d.p_win, render: (d) => pct(d.p_win), numeric: true },
          { key: "p_podium", label: "Podium", value: (d) => d.p_podium, render: (d) => pct(d.p_podium), numeric: true },
          { key: "p_points", label: "Points", value: (d) => d.p_points, render: (d) => pct(d.p_points), numeric: true },
          { key: "p_dnf", label: "DNF", value: (d) => d.p_dnf, render: (d) => pct(d.p_dnf), numeric: true },
          { key: "exp_pos", label: "Exp. pos", value: (d) => d.exp_pos, render: (d) => dec(d.exp_pos, 1), numeric: true, rank: true },
          { key: "exp_points", label: "Exp. pts", value: (d) => d.exp_points, render: (d) => dec(d.exp_points, 1), numeric: true },
        ]}
      />
      <WhyOdds drivers={drivers} why={sessionWhy(forecast, code, which)} code={code} />
    </section>
  );
}

function QualiOdds({ forecast, code, ahead }: { forecast: Forecast; code: "Q" | "SQ"; ahead: number }) {
  const { which, toggle, after } = useWhich(forecast, code);
  const drivers = useMemo(() => rows<QualiForecastDriver>(sessionOdds(forecast, code, which)).sort((a, b) => b.p_pole - a.p_pole), [forecast, code, which]);
  if (!drivers.length) return null;
  const circuit = drivers.some((d) => d.circuit != null);
  return (
    <section>
      <h3>Who Takes Pole? <SessionBadge code={code} /></h3>
      <p className="muted">
        The session played 10,000 times from each driver's qualifying pace (Qualifying and Sprint
        Qualifying, recent sessions weighted most, a session more than {FORM_CLIP}% off a driver's usual pace capped
        as an outlier{which === "latest" ? <>, this weekend's {after.join(", ")} included</> : null})
        {circuit ? ", plus how their team qualified at this circuit last season" : ""}, with a small chance of setting
        no time. It gives each driver's chance of pole, of making Q3 and of going out in Q1.{which === "pre" ? aheadText(ahead) : ""}
      </p>
      {toggle}
      <div className="grid-2">
        <div>
          <h4 className="sub">Pole chance</h4>
          <ChanceBars data={drivers.filter((d) => d.p_pole >= 0.002).slice(0, 10)} label={(d) => d.driver} value={(d) => d.p_pole} colorOf={(d) => d.color} title="Pole chances" />
        </div>
        <div>
          <h4 className="sub">Chance of reaching Q3</h4>
          <ChanceBars data={[...drivers].sort((a, b) => b.p_q3 - a.p_q3).slice(0, 12)} label={(d) => d.driver} value={(d) => d.p_q3} colorOf={(d) => d.color} title="Q3 chances" max={1} />
        </div>
      </div>
      <Table<QualiForecastDriver>
        data={drivers}
        rowKey={(d) => d.driver}
        sort="p_pole"
        cardTitle={(d) => <><DriverChip code={d.driver} color={d.color} /> {d.team}</>}
        cardSub={[]}
        cardStats={["p_pole", "p_q3", "p_q1_out"]}
        columns={[
          { key: "driver", label: "Driver", value: (d) => d.driver, render: (d) => <DriverChip code={d.driver} color={d.color} /> },
          { key: "team", label: "Team", value: (d) => d.team },
          { key: "pace", label: "Pace", title: "Qualifying form against the field median (negative = faster)", value: (d) => -d.pace, render: (d) => `${signed(d.pace, 2)}%`, numeric: true },
          ...(circuit ? [{ key: "circuit", label: "Circuit", title: "The team's qualifying pace here last season against its season average (negative = better here)", value: (d: QualiForecastDriver) => -(d.circuit ?? 0), render: (d: QualiForecastDriver) => (d.circuit == null ? "–" : `${signed(d.circuit, 2)}%`), numeric: true }] : []),
          { key: "p_pole", label: "Pole", value: (d) => d.p_pole, render: (d) => pct(d.p_pole), numeric: true },
          { key: "p_front_row", label: "Front row", value: (d) => d.p_front_row, render: (d) => pct(d.p_front_row), numeric: true },
          { key: "p_q3", label: "Q3", value: (d) => d.p_q3, render: (d) => pct(d.p_q3), numeric: true },
          { key: "p_q1_out", label: "Out in Q1", value: (d) => d.p_q1_out, render: (d) => pct(d.p_q1_out), numeric: true },
          { key: "exp_pos", label: "Exp. grid", value: (d) => d.exp_pos, render: (d) => dec(d.exp_pos, 1), numeric: true, rank: true },
        ]}
      />
      <WhyOdds drivers={drivers} why={sessionWhy(forecast, code, which)} code={code} />
    </section>
  );
}

/** What last season's qualifying here and the forecast say about running the session. */
function QualiPlan({ qs, code }: { qs: QualiStrategy; code: "Q" | "SQ" }) {
  const ref = qs.reference;
  const evo = ref && ref.Q1_Q2 !== null ? ref.Q1_Q2 + (ref.Q2_Q3 ?? 0) : null;
  const rain = qs.rain?.chance ?? null;
  const tips: string[] = [];
  const q1Gain = ref?.gain?.Q1 ?? null;
  if (q1Gain !== null && q1Gain < -0.01) {
    tips.push(`Within Q1 the track came ${Math.abs(q1Gain).toFixed(3)} s a minute here last season (each driver's laps against their own): a lap in the last minutes is worth about ${Math.abs(q1Gain * 15).toFixed(1)} s over one at the start. Run late, but leave room for a lap ruined by traffic or a yellow flag.`);
  }
  if (evo !== null && evo < -0.3) {
    tips.push(`The track came ${Math.abs(evo).toFixed(2)} s to the drivers from Q1 to Q3 here last season: a lap set late in a segment is worth more than an early one, so the last run is the one that counts, and a clear gap in the queue matters.`);
  } else if (evo !== null) {
    tips.push(`The track barely improved here last season (${signed(evo, 2)} s from Q1 to Q3): an early run is nearly as good as a late one, and a clean lap matters more than timing.`);
  }
  if (rain !== null && rain >= 0.2) {
    tips.push(`${pct(rain)} chance of rain in the session: bank a lap on the first run of each segment, as the track may only get slower.`);
  }
  if (qs.q1_edge.length) {
    tips.push(`On the edge of the Q1 cut-off: ${qs.q1_edge.join(", ")}. A single run may not be enough for them; a second set of new Softs in Q1 means one fewer later.`);
  }
  if (qs.q3_edge.length) {
    tips.push(`Fighting for Q3: ${qs.q3_edge.join(", ")}. For them Q2 is the session to spend tyres on.`);
  }
  return (
    <section>
      <h3>Qualifying Strategy</h3>
      <Tiles tiles={[
        { label: "Track evolution", value: evo === null ? "–" : `${signed(evo, 2)} s`, note: ref ? `Q1 to Q3, ${ref.event}` : "no qualifying here last season" },
        { label: "Q1 cut-off", value: ref?.q1_cut_pct != null ? `+${ref.q1_cut_pct.toFixed(2)}%` : "–", note: ref?.q1_cut_pct != null ? `≈ ${(ref.pole * ref.q1_cut_pct / 100).toFixed(2)} s off pole last season` : undefined },
        { label: "Q2 cut-off", value: ref?.q2_cut_pct != null ? `+${ref.q2_cut_pct.toFixed(2)}%` : "–", note: ref?.q2_cut_pct != null ? `≈ ${(ref.pole * ref.q2_cut_pct / 100).toFixed(2)} s off pole · pole ${lapTime(ref.pole)}` : undefined },
        { label: "Chance of rain", value: rain === null ? "–" : pct(rain), note: qs.rain ? (qs.rain.source === "ensemble" ? "weather forecast" : "the climate") : undefined },
      ]} />
      {tips.length > 0 && <ul className="insights">{tips.map((t, i) => <li key={i}>{t}</li>)}</ul>}
      <p className="muted">
        Track evolution is how much quicker the same drivers went from one segment to the next in last
        season's {code === "SQ" ? "Grand Prix qualifying (sprint qualifying runs the same format, shorter)" : "qualifying"} here;
        the cut-offs are the slowest lap that went through, against pole. "On the edge" are the drivers the
        forecast gives a 25–75% chance of going out (Q1) or making it (Q3).
      </p>
    </section>
  );
}

/** Every round left: who the forecast favours, and how sure it is, so they can be compared. */
function SeasonAhead({ upcoming }: { upcoming: CalendarEvent[] }) {
  const [files, setFiles] = useState<(Forecast | null)[] | null>(null);
  useEffect(() => {
    let live = true;
    Promise.all(upcoming.map((e) => load<Forecast>(forecastFile(e.round)))).then((f) => live && setFiles(f));
    return () => { live = false; };
  }, [upcoming]);
  if (!files || upcoming.length < 2) return null;
  const data = upcoming.map((e, i) => {
    const f = files[i];
    const race = rows<ForecastDriver>(sessionOdds(f, "R", "pre")).sort((a, b) => b.p_win - a.p_win);
    const quali = rows<QualiForecastDriver>(sessionOdds(f, "Q", "pre")).sort((a, b) => b.p_pole - a.p_pole);
    const sprint = rows<ForecastDriver>(sessionOdds(f, "S", "pre")).sort((a, b) => b.p_win - a.p_win);
    return { e, ahead: f?.rounds_ahead ?? null, race, quali, sprint };
  });
  type Row = (typeof data)[number];
  const chip = (d: { driver: string; color: string } | undefined, p: number | undefined) =>
    d ? <><DriverChip code={d.driver} color={d.color} /> {pct(p)}</> : "–";
  return (
    <section>
      <h3>Rest of the Season</h3>
      <p className="muted">
        Each round's forecast as it stands now, before its weekend. They differ by circuit (how each team went
        there last season) and by distance: the further away a round, the more each driver's form can change by
        then, so the favourite's chance shrinks. Sprint weekends have their own sprint odds.
      </p>
      <Table<Row>
        data={data}
        rowKey={(r) => r.e.round}
        sort="round"
        onRow={(r) => { window.location.hash = `next/${r.e.round}/R`; }}
        cardTitle={(r) => `${r.e.round}. ${shortEvent(r.e.event)}`}
        cardStats={["pole", "win", "second"]}
        columns={[
          { key: "round", label: "Round", value: (r) => r.e.round, numeric: true, rank: true },
          { key: "event", label: "Grand Prix", value: (r) => r.e.event, render: (r) => <><a href={`#next/${r.e.round}/R`}>{shortEvent(r.e.event)}</a>{r.e.sprint_utc ? <> <SessionBadge code="S" short /></> : null}</> },
          { key: "ahead", label: "Ahead", title: "Rounds from now", value: (r) => r.ahead, numeric: true },
          { key: "pole", label: "Pole", title: "Favourite for pole in Qualifying", value: (r) => r.quali[0]?.p_pole ?? 0, render: (r) => chip(r.quali[0], r.quali[0]?.p_pole), numeric: true },
          { key: "sprint", label: "Sprint", title: "Favourite to win the Sprint", value: (r) => r.sprint[0]?.p_win ?? 0, render: (r) => (r.sprint.length ? chip(r.sprint[0], r.sprint[0]?.p_win) : ""), numeric: true },
          { key: "win", label: "Win", title: "Favourite to win the Grand Prix", value: (r) => r.race[0]?.p_win ?? 0, render: (r) => chip(r.race[0], r.race[0]?.p_win), numeric: true },
          { key: "second", label: "Next best", title: "Second favourite for the Grand Prix", value: (r) => r.race[1]?.p_win ?? 0, render: (r) => chip(r.race[1], r.race[1]?.p_win), numeric: true },
        ]}
      />
    </section>
  );
}

const RAIN = "#2a78d6";

function Weather({ w, start, minutes }: { w: WeatherForecast; start: string | null; minutes: number }) {
  const source = w.source === "ensemble"
    ? <>Open-Meteo's {w.model.startsWith("icon") ? "ICON" : "GFS"} ensemble forecast: {w.samples} versions of the weather, each with its own rain timeline.</>
    : <>Too far ahead for a forecast, so it's the climate: the race's hours on the {w.samples} days within three days of its date over the last ten years (ERA5).</>;
  const track = w.track_expected ?? w.track;
  return (
    <section>
      <h3>Weather</h3>
      <Tiles tiles={[
        { label: "Chance of rain", value: pct(w.rain_chance), note: w.heavy_chance > 0 ? `${pct(w.heavy_chance)} heavy` : "none of it heavy" },
        { label: "Track temperature", value: track === null ? "–" : `${track.toFixed(0)} °C`, note: w.reference ? `${signed(w.temp_delta, 1)} °C on ${w.reference}` : "expected" },
        { label: "Air temperature", value: w.air === null ? "–" : `${w.air.toFixed(0)} °C` },
      ]} />
      <p className="muted">
        {source} A version counts as rain when an hour of the race gets {"≥"}0.3 mm (heavy from 3 mm); laps
        follow from a typical {minutes}-minute {minutes < 60 ? "sprint" : "race"}, and the track is dry again four laps after the rain stops. Each
        simulated race in the strategy Monte Carlo below draws one of them, so a wet version sends every plan
        onto intermediates or wets when the rain makes it worth a stop. Track temperature is the air plus the
        effect of sunshine (fitted on past races){w.reference ? <>, as a change on last season's race here, which sets the tyre wear: hotter wears the tyres faster</> : null}.
        Weather data by <a href="https://open-meteo.com/">Open-Meteo.com</a>.
      </p>
      {w.hourly && <HourlyChart w={w} start={start} minutes={minutes} />}
    </section>
  );
}

/** The hourly forecast around the race: chance of rain (bars) and the race window. */
function HourlyChart({ w, start, minutes }: { w: WeatherForecast; start: string | null; minutes: number }) {
  const make = useCallback((width: number) => {
    const hours = rows<WeatherHour>(w.hourly).map((h) => ({ ...h, t: new Date(h.time) }));
    const t0 = start ? new Date(start) : null;
    const t1 = t0 ? new Date(t0.getTime() + minutes * 60_000) : null;
    return Plot.plot({
      ...plotDefaults(width),
      height: 220,
      x: { type: "time", label: null },
      y: { label: "Chance of rain (%)", domain: [0, 100], grid: true },
      marks: [
        ...(t0 && t1 ? [Plot.rectX([{ a: t0, b: t1 }], { x1: "a", x2: "b", y1: 0, y2: 100, fill: color.grid, fillOpacity: 0.5 })] : []),
        // Each value is the hour that ends at its time.
        Plot.rectY(hours, { x1: (h) => new Date(h.t.getTime() - 3_600_000), x2: "t", y: (h) => h.rain_prob ?? 0, fill: RAIN, fillOpacity: 0.55, inset: 1 }),
        Plot.tip(hours, Plot.pointerX({ x: (h) => new Date(h.t.getTime() - 1_800_000), y: (h) => h.rain_prob ?? 0,
          title: (h) => `Hour to ${h.t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
${(h.rain_prob ?? 0).toFixed(0)}% chance of rain, ${(h.rain_mm ?? 0).toFixed(1)} mm
Air ${h.air?.toFixed(0) ?? "–"} °C, track ~${h.track?.toFixed(0) ?? "–"} °C` })),
      ],
    });
  }, [w, start, minutes]);
  return (
    <>
      <Legend items={[{ label: "Chance of rain in the hour", color: RAIN }, { label: "The race", color: color.grid }]} />
      <Chart make={make} height={220} ariaLabel="Hourly chance of rain around the race" />
      <p className="muted">
        The bars are the forecast's chance of any rain at all in each hour, a passing drop included; the chance of
        rain above counts only versions wet enough to need intermediates, so it's usually lower.
      </p>
    </>
  );
}

function Strategy({ s }: { s: StrategyForecast }) {
  const best = s.strategies[0];
  const bestMean = Math.min(...s.strategies.map((p) => p.mean));
  const colorOf = (name: string) => STRATEGY_COLORS[s.strategies.findIndex((p) => p.name === name) % STRATEGY_COLORS.length];
  return (
    <section>
      <h3>{s.code === "S" ? "Sprint " : ""}Tyre Strategy <SessionBadge code={s.code ?? "R"} /></h3>
      <p className="muted">
        {s.code === "S"
          ? <>Every no-stop and 1-stop plan (a sprint has no compulsory stop), run lap by lap through the simulator over{" "}</>
          : <>Every 1- and 2-stop plan over Soft, Medium and Hard, run lap by lap through the simulator over{" "}</>}
        {s.total_laps} laps with a {s.pit_loss.toFixed(1)} s pit loss{s.pit_loss_known ? "" : " (a default: no figure for this circuit yet)"}.
        {s.calibrated_on
          ? <> How hard the circuit is on tyres comes from the {s.calibrated_on} ({s.severity.toFixed(2)}× the preset wear, including this season's {s.season_factor.toFixed(2)}× change); the split between compounds, their pace gaps and cliffs are fixed assumptions.</>
          : <> No usable wear figure from this circuit last season (no race, or its wear was swamped by the track getting faster), so tyre wear is this season's typical level ({s.severity.toFixed(2)}× the presets).</>}
        {" "}The best plans then go through the Monte Carlo: lap-time noise and random Safety Cars, which
        make a stop cheaper{s.weather ? <>, and the weather above: {pct(s.weather.rain_chance)} of the simulated races see rain</> : null}.
        {s.weather && s.weather.temp_delta !== 0 && <> The track is expected {Math.abs(s.weather.temp_delta).toFixed(1)} °C {s.weather.temp_delta > 0 ? "hotter" : "cooler"} than last season's race here, which moves the wear and cliffs.</>}
      </p>
      <Table<StrategyPlan>
        data={s.strategies}
        rowKey={(p) => p.name}
        cards={false}
        columns={[
          { key: "plan", label: "Plan", value: (p) => p.plan, render: (p) => <span className="plan-cell"><span className="team-dot" style={{ background: colorOf(p.name) }} /><Plan stints={p.stints} /></span> },
          { key: "pit", label: "Pit laps", value: (p) => p.pit_laps.join(", ") || "No stop" },
          { key: "delta", label: "No SC", title: "Time behind the fastest plan in a race with no Safety Car", value: (p) => p.delta, render: (p) => (p.delta === 0 ? "Fastest" : delta(p.delta)), numeric: true },
          { key: "mean", label: "Average", title: "Average time behind the best plan over the simulated races", value: (p) => p.mean - bestMean, render: (p) => (p.mean === bestMean ? "Best" : delta(p.mean - bestMean)), numeric: true },
          { key: "win", label: "Fastest in", title: "Share of simulated races where this plan was the quickest", value: (p) => p.win_prob, render: (p) => pct(p.win_prob), numeric: true },
        ]}
      />
      <p className="muted">Each plan's coloured dot is its line in the chart below.</p>
      <Legend items={s.strategies.map((p) => ({ label: p.plan, color: colorOf(p.name), kind: "line" as const }))} />
      <StrategyTrace s={s} colorOf={colorOf} />
      <Note>
        Best plan with no Safety Car: {best.plan} ({best.stops === 0 ? "no stop" : <>{best.stops} stop{best.stops === 1 ? "" : "s"}, pit on lap {best.pit_laps.join(" and ")}</>}).
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
