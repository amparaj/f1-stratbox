// The driver and circuit pop-ups' content (loaded on first use; components/Profiles.tsx holds the host and links).
// A driver: photo, number, flag, team; this season race by race, against their teammate, the next race's odds;
// their career from the History files. A circuit: the track map and its facts, who wins there, the coming
// weekend's forecast, and how this season's race there played against the rest.

import * as Plot from "@observablehq/plot";
import { useCallback, useMemo } from "react";
import { color } from "../colors";
import {
  forecastFile, rows, SESSION_LABEL, sessionOdds, sessionStart, weekendAll,
  type CalendarEvent, type Forecast, type ForecastDriver, type QualiForecastDriver, type TitleOdds,
} from "../data";
import { dayYear, dec, lapTime, pct, shortEvent, when } from "../format";
import {
  H, link, type CareerRow, type CircuitRace, type CircuitRow, type CircuitsFile, type DriverSeason, type DriversFile,
} from "../history";
import { CareerChart } from "../pages/History";
import {
  age, country, countryByName, photoUrl,
  type ProfileCircuit, type ProfileResult, type Profiles, type ProfileTarget, type RaceAnalysis,
} from "../profiles";
import { useData, useSite } from "../site";
import { COMPOUND, compoundKey, DriverChip, Plan, TeamBadge, TeamName } from "./f1";
import { CircuitLink, DriverName, Flag } from "./Profiles";
import { Chart, Legend, Loading, plotDefaults, Stats, Table, Tiles } from "./ui";

const PROFILES = "profiles.json";

export default function ProfileBody({ target }: { target: ProfileTarget }) {
  const prof = useData<Profiles>(PROFILES);
  if (prof === undefined) return <Loading />;
  if (target.kind === "driver") return <DriverProfile code={target.code} prof={prof} />;
  if (target.kind === "history-driver") {
    // A driver still racing: the full profile, this season included.
    const now = prof?.drivers.find((d) => d.ref === target.ref);
    return now ? <DriverProfile code={now.driver} prof={prof} /> : <HistoryDriverProfile refId={target.ref} />;
  }
  if (target.kind === "circuit") return <CircuitProfile round={target.round} prof={prof} />;
  const now = prof?.circuits.find((c) => c.ref === target.ref);
  return now ? <CircuitProfile round={now.round} prof={prof} /> : <HistoryCircuitProfile refId={target.ref} />;
}

const ordinal = (n: number) => {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
};
const P = (n: number | null | undefined) => (n == null ? "–" : `P${n}`);

// ---------------------------------------------------------------- driver

interface WeekendRow {
  round: number; event: string; q: ProfileResult | undefined; r: ProfileResult | undefined;
  sq: ProfileResult | undefined; s: ProfileResult | undefined; points: number;
}

function DriverProfile({ code, prof }: { code: string; prof: Profiles | null }) {
  const site = useSite();
  const hist = useData<DriversFile>(H.drivers);
  const nextRound = site.meta.next_round;
  const forecast = useData<Forecast>(nextRound ? forecastFile(nextRound) : null);
  const p = prof?.drivers.find((d) => d.driver === code);
  const st = site.driver.get(code);
  const all = useMemo(() => rows<ProfileResult>(prof?.results), [prof]);
  const mine = useMemo(() => all.filter((r) => r.driver === code), [all, code]);

  // Career to last season (History), with this season added.
  const career = useMemo(() => {
    if (!hist || !p?.ref) return null;
    const list = rows<CareerRow>(hist.drivers);
    const i = list.findIndex((d) => d.ref === p.ref);
    if (i < 0) return null;
    return { d: list[i], titles: hist.drivers.title_years[i], seasons: rows<DriverSeason>(hist.seasons).filter((s) => s.ref === p.ref) };
  }, [hist, p?.ref]);

  if (!p && !st) return <p>No profile for {code} this season.</p>;
  const name = p?.name ?? st?.name ?? code;
  const team = p?.team ?? st?.team ?? "";
  const teamColor = p?.color ?? st?.color ?? null;

  // This season, weekend by weekend.
  const weekends: WeekendRow[] = [...new Set(mine.map((r) => r.round))].sort((a, b) => a - b).map((round) => {
    const at = (c: ProfileResult["code"]) => mine.find((r) => r.round === round && r.code === c);
    const r = at("R"), s = at("S");
    return { round, event: site.event.get(round)?.event ?? `Round ${round}`, q: at("Q"), r, sq: at("SQ"), s, points: (r?.points ?? 0) + (s?.points ?? 0) };
  });
  const gp = mine.filter((r) => r.code === "R" && !r.dns);
  const finished = gp.filter((r) => !r.dnf && r.position != null);
  const quali = mine.filter((r) => r.code === "Q" && r.position != null);
  const mean = (v: number[]) => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : null);

  // Head to head with each teammate, session by session (a DNF finishes behind; both out: not counted).
  const mates = new Map<string, { q: [number, number]; r: [number, number] }>();
  for (const mineRow of mine) {
    if (mineRow.code !== "Q" && mineRow.code !== "R") continue;
    const mate = all.find((x) => x.round === mineRow.round && x.code === mineRow.code && x.team === mineRow.team && x.driver !== code);
    if (!mate) continue;
    const h = mates.get(mate.driver) ?? { q: [0, 0], r: [0, 0] };
    const key = mineRow.code === "Q" ? "q" : "r";
    const out = (x: ProfileResult) => x.code === "R" && (x.dnf || x.dns);
    if (out(mineRow) && out(mate)) continue;
    const ahead = out(mate) ? true : out(mineRow) ? false : (mineRow.position ?? 99) < (mate.position ?? 99);
    h[key][ahead ? 0 : 1]++;
    mates.set(mate.driver, h);
  }
  const teammates = [...mates.entries()].sort((a, b) => (b[1].q[0] + b[1].q[1]) - (a[1].q[0] + a[1].q[1]));

  const seasonRow: DriverSeason | null = st ? {
    ref: p?.ref ?? code, year: site.meta.season, team, color: teamColor ?? color.neutral, position: st.position, points: st.points,
    starts: st.starts, wins: st.wins, podiums: st.podiums, poles: st.poles ?? 0, best: finished.length ? Math.min(...finished.map((r) => r.position!)) : null,
  } : null;

  // The next race's odds and the title.
  const odds = rows<ForecastDriver>(sessionOdds(forecast, "R")).find((d) => d.driver === code);
  const qOdds = rows<QualiForecastDriver>(sessionOdds(forecast, "Q")).find((d) => d.driver === code);
  const title = rows<TitleOdds>(site.meta.title_odds?.drivers).find((d) => d.driver === code);
  const nextEv = nextRound ? site.event.get(nextRound) : undefined;
  const photo = photoUrl(p?.ref);
  const years = age(p?.born);

  return (
    <div className="profile">
      <div className="profile-hero" style={{ ["--team" as string]: teamColor ?? "var(--neutral)" }}>
        <div className="profile-photo">
          {photo ? <img src={photo} alt={name} /> : <span className="profile-photo-number">{p?.number ?? code}</span>}
        </div>
        <div className="profile-id">
          <div className="profile-number" aria-label={`Car number ${p?.number ?? "unknown"}`}>{p?.number ?? ""}</div>
          <h2 className="profile-name">{name}</h2>
          <div className="profile-meta">
            {p?.country && <span className="profile-meta-item"><Flag a3={p.country} /> {country(p.country)?.name ?? p.nationality}</span>}
            {team && <span className="profile-meta-item"><TeamName team={team} color={teamColor} year={site.meta.season} /></span>}
            <span className="profile-meta-item"><DriverChip code={code} color={teamColor} plain /></span>
          </div>
          <div className="profile-meta muted">
            {p?.born && <span>Born {dayYear(p.born)}{years !== null ? ` (age ${years})` : ""}</span>}
            {career && <span>F1 debut {career.d.first}</span>}
          </div>
          <div className="profile-links">
            {career && <a href={link.driver(career.d.ref)}>Full career in History</a>}
            {p?.wikipedia && <a href={p.wikipedia} target="_blank" rel="noreferrer">Wikipedia</a>}
          </div>
        </div>
      </div>

      <h3>{site.meta.season} Season</h3>
      {st ? (
        <Tiles tiles={[
          { label: "Championship", value: P(st.position), note: `${dec(st.points, 0)} pts${title ? ` · ${pct(title.p_title)} title chance` : ""}` },
          { label: "Wins", value: st.wins, note: st.sprint_wins ? `+ ${st.sprint_wins} sprint win${st.sprint_wins === 1 ? "" : "s"}` : `${st.starts} starts` },
          { label: "Podiums", value: st.podiums, note: `poles ${st.poles ?? 0}` },
          { label: "Average finish", value: finished.length ? dec(mean(finished.map((r) => r.position!)), 1) : "–", note: `${st.dnfs} DNF${st.dnfs === 1 ? "" : "s"}` },
          { label: "Average qualifying", value: quali.length ? dec(mean(quali.map((r) => r.position!)), 1) : "–", note: quali.length ? `best ${P(Math.min(...quali.map((r) => r.position!)))}` : undefined },
        ]} />
      ) : <p className="muted">No championship standing yet.</p>}

      {teammates.length > 0 && (
        <div className="h2h">
          {teammates.map(([mate, h]) => {
            const m = site.driver.get(mate);
            return (
              <div className="h2h-row" key={mate}>
                <span className="h2h-label">Against <DriverName code={mate} color={m?.color} name={m?.name} /></span>
                <H2H label="Qualifying" a={h.q[0]} b={h.q[1]} color={teamColor} />
                <H2H label="Grands Prix" a={h.r[0]} b={h.r[1]} color={teamColor} />
              </div>
            );
          })}
        </div>
      )}

      {weekends.length > 0 && (
        <>
          <h4 className="sub profile-sub">Race by race</h4>
          <SeasonChart weekends={weekends} teamColor={teamColor} />
          <Table<WeekendRow>
            data={weekends}
            rowKey={(w) => w.round}
            sort="round"
            onRow={(w) => { window.location.hash = `races/${w.round}`; }}
            cardTitle={(w) => <>{w.round}. {shortEvent(w.event)}</>}
            cardSub={[]}
            cardStats={["q", "r", "points"]}
            columns={[
              { key: "round", label: "Rd", value: (w) => w.round, numeric: true, rank: true },
              { key: "event", label: "Grand Prix", value: (w) => w.event, render: (w) => <CircuitLink round={w.round}>{shortEvent(w.event)}</CircuitLink> },
              { key: "q", label: "Quali", value: (w) => w.q?.position ?? null, render: (w) => P(w.q?.position), numeric: true, rank: true },
              ...(weekends.some((w) => w.s) ? [{ key: "s", label: "Sprint", value: (w: WeekendRow) => w.s?.position ?? null, render: (w: WeekendRow) => (w.s ? (w.s.dnf ? "DNF" : P(w.s.position)) : ""), numeric: true, rank: true }] : []),
              { key: "grid", label: "Grid", value: (w) => w.r?.grid ?? null, render: (w) => (w.r ? (w.r.grid ? P(w.r.grid) : "Pit lane") : "–"), numeric: true, rank: true },
              { key: "r", label: "Result", value: (w) => (w.r ? (w.r.dnf || w.r.dns ? 99 : w.r.position) : null), render: (w) => resultText(w.r), numeric: true, rank: true },
              { key: "points", label: "Pts", value: (w) => w.points, numeric: true },
            ]}
          />
        </>
      )}

      {nextEv && odds && (
        <>
          <h3>Next: {nextEv.event}</h3>
          <Tiles tiles={[
            { label: "Win", value: pct(odds.p_win), note: `podium ${pct(odds.p_podium)}` },
            { label: "Points", value: pct(odds.p_points), note: `expected ${dec(odds.exp_points, 1)} pts` },
            { label: "Expected finish", value: P(Math.round(odds.exp_pos)), note: `DNF ${pct(odds.p_dnf)}` },
            ...(qOdds ? [{ label: "Pole", value: pct(qOdds.p_pole), note: `Q3 ${pct(qOdds.p_q3)}` }] : []),
          ]} />
          <p className="note"><a href={`#next/${nextEv.round}`}>The full forecast</a> · <CircuitLink round={nextEv.round}>circuit guide</CircuitLink></p>
        </>
      )}

      <h3>Career</h3>
      {hist === undefined ? <Loading /> : career ? (
        <CareerPanel d={career.d} titles={career.titles} seasons={seasonRow ? [...career.seasons, seasonRow] : career.seasons} season={seasonRow} />
      ) : (
        <p className="muted">{site.meta.season} is {name.split(" ")[0]}'s first season in Formula 1{seasonRow ? "" : " (no starts yet)"}.</p>
      )}
    </div>
  );
}

function resultText(r: ProfileResult | undefined) {
  if (!r) return "–";
  if (r.dns) return "DNS";
  if (r.dnf) return <span title={r.status}>DNF</span>;
  return P(r.position);
}

function H2H({ label, a, b, color: c }: { label: string; a: number; b: number; color: string | null }) {
  const n = a + b;
  return (
    <div className="h2h-bar" title={`${label}: ahead ${a} of ${n}`}>
      <span className="h2h-name">{label}</span>
      <span className="h2h-track" aria-hidden>
        <span style={{ width: `${n ? (100 * a) / n : 50}%`, background: c ?? "var(--s1)" }} />
      </span>
      <b>{a}–{b}</b>
    </div>
  );
}

/** Each weekend's qualifying and Grand Prix result (DNFs marked), positions down the axis. */
function SeasonChart({ weekends, teamColor }: { weekends: WeekendRow[]; teamColor: string | null }) {
  const site = useSite();
  const make = useCallback((width: number) => {
    const c = teamColor ?? color.s1;
    const q = weekends.filter((w) => w.q?.position != null).map((w) => ({ round: w.round, pos: w.q!.position!, ev: w.event }));
    const r = weekends.filter((w) => w.r && w.r.position != null && !w.r.dns).map((w) => ({ round: w.round, pos: w.r!.position!, dnf: w.r!.dnf, ev: w.event, status: w.r!.status }));
    const worst = Math.max(10, ...q.map((d) => d.pos), ...r.map((d) => d.pos));
    const rounds = site.meta.calendar.length;
    return Plot.plot({
      ...plotDefaults(width),
      height: 220,
      marginBottom: 34,
      x: { label: "Round", domain: [0.5, Math.max(rounds, ...weekends.map((w) => w.round)) + 0.5], ticks: Math.min(rounds, width < 500 ? 8 : 24), tickFormat: "d" },
      y: { label: "Position", reverse: true, domain: [1, worst], grid: true, ticks: [1, 5, 10, 15, 20].filter((t) => t <= worst) },
      marks: [
        Plot.ruleY([10.5], { stroke: color.muted, strokeDasharray: "3,3", strokeOpacity: 0.5 }),
        Plot.line(r.filter((d) => !d.dnf), { x: "round", y: "pos", stroke: c, strokeOpacity: 0.35 }),
        Plot.dot(q, { x: "round", y: "pos", r: 4, stroke: c, strokeWidth: 1.5, fill: color.surface }),
        Plot.dot(r.filter((d) => !d.dnf), { x: "round", y: "pos", r: 4.5, fill: c, stroke: color.surface }),
        Plot.dot(r.filter((d) => d.dnf), { x: "round", y: "pos", r: 5, symbol: "times", stroke: color.bad, strokeWidth: 2 }),
        Plot.tip([...r.map((d) => ({ ...d, kind: "R" })), ...q.map((d) => ({ ...d, kind: "Q", dnf: false, status: "" }))],
          Plot.pointer({ x: "round", y: "pos", title: (d: { round: number; pos: number; ev: string; kind: string; dnf: boolean; status: string }) =>
            `${d.round}. ${d.ev}\n${d.kind === "Q" ? `Qualified P${d.pos}` : d.dnf ? `DNF (${d.status || "retired"})` : `Finished P${d.pos}`}` })),
      ],
    });
  }, [weekends, teamColor, site]);
  return (
    <>
      <Legend items={[
        { label: "Grand Prix finish", color: teamColor ?? color.s1, kind: "dot" },
        { label: "Qualifying (hollow)", color: color.muted, kind: "dot" },
        { label: "DNF (×)", color: color.bad, kind: "dot" },
      ]} />
      <Chart make={make} ariaLabel="Qualifying and Grand Prix positions by round" />
    </>
  );
}

function CareerPanel({ d, titles, seasons, season }: { d: CareerRow; titles: number[]; seasons: DriverSeason[]; season: DriverSeason | null }) {
  const plus = (base: number, extra: number | undefined) => base + (extra ?? 0);
  const now = season?.year;
  const note = (base: number, extra: number | undefined) => (season && extra ? `${base} to ${now! - 1}, ${extra} in ${now}` : undefined);
  // Best championship finish in a completed season (this one's standing is still moving).
  const best = seasons.filter((s) => s.year !== now && s.position != null).sort((a, b) => a.position! - b.position! || a.year - b.year)[0];
  return (
    <>
      <Tiles tiles={[
        { label: "World titles", value: d.titles, note: titles.length ? titles.join(", ") : best ? `best finish ${P(best.position)} (${best.year})` : undefined },
        { label: "Starts", value: plus(d.starts, season?.starts), note: `${d.first}–${now ?? d.last}` },
        { label: "Wins", value: plus(d.wins, season?.wins), note: note(d.wins, season?.wins) },
        { label: "Podiums", value: plus(d.podiums, season?.podiums), note: note(d.podiums, season?.podiums) },
        { label: "Poles", value: plus(d.poles, season?.poles), note: note(d.poles, season?.poles) },
      ]} />
      {seasons.length > 1 && <CareerChart seasons={seasons} />}
      <Table<DriverSeason>
        data={seasons}
        rowKey={(s) => s.year}
        sort="year"
        desc
        limit={8}
        onRow={(s) => { window.location.hash = s.year === now ? "season" : link.season(s.year); }}
        cardTitle={(s) => <>{s.year} · {s.team}</>}
        cardStats={["position", "points", "wins"]}
        columns={[
          { key: "year", label: "Season", value: (s) => s.year, render: (s) => <>{s.year}{s.year === now ? <span className="muted"> (so far)</span> : null}</>, numeric: true },
          { key: "team", label: "Team", value: (s) => s.team, render: (s) => <TeamName team={s.team} color={s.color} year={s.year} /> },
          { key: "position", label: "Pos", value: (s) => s.position, render: (s) => P(s.position), numeric: true, rank: true },
          { key: "points", label: "Points", value: (s) => s.points, render: (s) => dec(s.points, Number.isInteger(s.points ?? 0) ? 0 : 1), numeric: true },
          { key: "wins", label: "Wins", value: (s) => s.wins, numeric: true },
          { key: "podiums", label: "Podiums", value: (s) => s.podiums, numeric: true },
          { key: "poles", label: "Poles", value: (s) => s.poles, numeric: true },
        ]}
      />
    </>
  );
}

function HistoryDriverProfile({ refId }: { refId: string }) {
  const hist = useData<DriversFile>(H.drivers);
  const data = useMemo(() => {
    if (!hist) return null;
    const list = rows<CareerRow>(hist.drivers);
    const i = list.findIndex((d) => d.ref === refId);
    if (i < 0) return null;
    return { d: list[i], titles: hist.drivers.title_years[i], teams: hist.drivers.teams[i], seasons: rows<DriverSeason>(hist.seasons).filter((s) => s.ref === refId) };
  }, [hist, refId]);
  if (hist === undefined) return <Loading />;
  if (!data) return <p>No such driver in the History files.</p>;
  const { d, titles, teams, seasons } = data;
  const photo = photoUrl(d.ref);
  const last = seasons.at(-1);
  return (
    <div className="profile">
      <div className="profile-hero" style={{ ["--team" as string]: d.color ?? "var(--neutral)" }}>
        <div className="profile-photo">
          {photo ? <img src={photo} alt={d.name} /> : <span className="profile-photo-number">{d.driver}</span>}
        </div>
        <div className="profile-id">
          <h2 className="profile-name">{d.name}</h2>
          <div className="profile-meta">
            {d.country && <span className="profile-meta-item"><Flag a3={d.country} /> {country(d.country)?.name ?? d.country}</span>}
            {last && <span className="profile-meta-item"><TeamBadge team={last.team} color={last.color} year={last.year} /> {teams.join(", ")}</span>}
          </div>
          <div className="profile-meta muted">
            {d.born && <span>Born {dayYear(d.born)}</span>}
            <span>{d.seasons} season{d.seasons === 1 ? "" : "s"}, {d.first === d.last ? d.first : `${d.first}–${d.last}`}</span>
          </div>
          <div className="profile-links">
            <a href={link.driver(d.ref)}>Full career in History</a>
            {d.wikipedia && <a href={d.wikipedia} target="_blank" rel="noreferrer">Wikipedia</a>}
          </div>
        </div>
      </div>
      <h3>Career</h3>
      <CareerPanel d={d} titles={titles} seasons={seasons} season={null} />
    </div>
  );
}

// ---------------------------------------------------------------- circuit

function CircuitProfile({ round, prof }: { round: number; prof: Profiles | null }) {
  const site = useSite();
  const ev = site.event.get(round);
  const c = prof?.circuits.find((x) => x.round === round);
  const file = useData<CircuitsFile>(H.circuits);
  const forecast = useData<Forecast>(ev && !ev.done_R && site.meta.forecasts.includes(round) ? forecastFile(round) : null);
  const hc = useMemo(() => rows<CircuitRow>(file?.circuits).find((x) => x.circuit === c?.ref), [file, c?.ref]);
  const past = useMemo(() => rows<CircuitRace>(file?.races).filter((x) => x.circuit === c?.ref), [file, c?.ref]);
  if (!ev) return <p>No round {round} this season.</p>;
  const a = c?.analysis ?? null;
  const laps = a?.total_laps ?? past.at(-1)?.laps ?? null;
  const thisWin = ev.done_R && ev.winner ? site.driver.get(ev.winner) : undefined;

  return (
    <div className="profile">
      <CircuitHero c={c} ev={ev} hc={hc} />
      <Stats items={[
        { label: "Lap length", value: c?.length_m ? `${(c.length_m / 1000).toFixed(3)} km` : "–", title: "From the track outline (MultiViewer or this season's telemetry)" },
        { label: "Race laps", value: laps ?? "–" },
        { label: "Race distance", value: c?.length_m && laps ? `${((c.length_m * laps) / 1000).toFixed(1)} km` : "–" },
        { label: "Corners", value: c?.corners?.length ? c.corners.length : "–" },
        { label: "Pit-lane loss", value: c ? `${dec(c.pit_loss, 1)} s` : "–", title: c?.pit_loss_known ? "Time lost by a stop against staying out (the strategy model's figure)" : "No figure for this circuit: the model's default" },
        { label: "First Grand Prix", value: hc ? hc.first : site.meta.season, title: hc ? undefined : "New to the calendar" },
        { label: "Grands Prix held", value: (hc?.races ?? 0) + (ev.done_R ? 1 : 0), title: ev.done_R ? `including ${site.meta.season}` : undefined },
        { label: "Fastest race lap", value: hc?.record ? lapTime(hc.record) : "–", title: hc?.record ? `${hc.record_by}, ${hc.record_year} (current layout, laps timed since 1996)` : undefined },
      ]} />
      {hc?.record && <p className="note">Fastest race lap on this layout: {lapTime(hc.record)} by {hc.record_by} in {hc.record_year}{a?.fastest && a.fastest.time < hc.record ? <>; beaten in {site.meta.season} by {a.fastest.driver} ({lapTime(a.fastest.time)})</> : null}.</p>}

      {!ev.done_R && <Weekend ev={ev} forecast={forecast} />}

      {ev.done_R && a && (
        <>
          <h3>How the {site.meta.season} race played</h3>
          {thisWin && <p className="profile-line">Won by <DriverName code={thisWin.driver} color={thisWin.color} name={thisWin.name} /> from {P(a.winner_grid)}.{ev.pole ? <> Pole: <DriverName code={ev.pole} color={ev.pole_color} name={site.driver.get(ev.pole)?.name} />.</> : null}</p>}
          <CircuitAnalysis round={round} prof={prof} />
          <p className="note"><a href={`#races/${round}`}>Full results and analysis</a></p>
        </>
      )}

      <WinnersHere past={past} ev={ev} hc={hc} />
    </div>
  );
}

function CircuitHero({ c, ev, hc }: { c: ProfileCircuit | undefined; ev?: CalendarEvent; hc?: CircuitRow }) {
  const name = c?.name ?? hc?.name ?? ev?.location ?? "";
  const place = c ? `${c.locality}, ${country(c.country)?.name ?? c.country_name ?? ""}` : hc ? `${hc.locality}, ${hc.country}` : "";
  const a3 = c?.country ?? countryByName(hc?.country);
  return (
    <div className="circuit-hero">
      <div className="circuit-id">
        <h2 className="profile-name"><Flag a3={a3} /> {name}</h2>
        <div className="profile-meta">{ev && <span>Round {ev.round}: {ev.event} ·</span>}<span>{place}</span></div>
        <div className="profile-links">
          {(c?.ref ?? hc?.circuit) && hc && <a href={link.circuit(hc.circuit)}>Every race here in History</a>}
          {c?.wikipedia && <a href={c.wikipedia} target="_blank" rel="noreferrer">Wikipedia</a>}
        </div>
      </div>
      {c?.x && c.y && <TrackMap c={c} />}
    </div>
  );
}

/** The outline with corner numbers, as F1 draws it (north isn't up). */
function TrackMap({ c }: { c: ProfileCircuit }) {
  const x = c.x!, y = c.y!;
  const minX = Math.min(...x), maxX = Math.max(...x), minY = Math.min(...y), maxY = Math.max(...y);
  const span = Math.max(maxX - minX, maxY - minY);
  const pad = span * 0.08;
  const vb = `${minX - pad} ${-maxY - pad} ${maxX - minX + 2 * pad} ${maxY - minY + 2 * pad}`;
  const pts = x.map((v, i) => `${v},${-y[i]}`).join(" ");
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  return (
    <svg className="track-map" viewBox={vb} role="img" aria-label={`Track map of ${c.name}${c.corners?.length ? `, ${c.corners.length} corners` : ""}`}>
      <polygon points={pts} fill="none" stroke="var(--baseline)" strokeWidth={9} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <polygon points={pts} fill="none" stroke="var(--ink)" strokeWidth={3} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      {(c.corners ?? []).map((k) => {
        // Labels a little outside the track, away from the centre.
        const dx = k.x - cx, dy = k.y - cy, d = Math.hypot(dx, dy) || 1;
        const lx = k.x + (dx / d) * span * 0.05, ly = k.y + (dy / d) * span * 0.05;
        return <text key={k.n} x={lx} y={-ly} fontSize={span * 0.035} textAnchor="middle" dominantBaseline="central" fill="var(--muted)">{k.n}</text>;
      })}
    </svg>
  );
}

/** The coming weekend: session times, the favourites, the weather and the best strategy. */
function Weekend({ ev, forecast }: { ev: CalendarEvent; forecast: Forecast | null | undefined }) {
  const site = useSite();
  const top = <T extends { driver: string; color: string }>(cols: ReturnType<typeof sessionOdds>, key: keyof T) =>
    rows<T>(cols).sort((a, b) => (b[key] as number) - (a[key] as number)).slice(0, 3);
  const gp = top<ForecastDriver>(sessionOdds(forecast, "R"), "p_win");
  const q = top<QualiForecastDriver>(sessionOdds(forecast, "Q"), "p_pole");
  const s = forecast?.strategy;
  const best = s?.strategies[0];
  const w = s?.weather;
  return (
    <>
      <h3>{ev.round === site.meta.next_round ? "This weekend" : "The weekend"}</h3>
      <ul className="profile-sessions">
        {weekendAll(ev).map((code) => (
          <li key={code}><span>{SESSION_LABEL[code]}</span> <b>{when(sessionStart(ev, code))}</b></li>
        ))}
      </ul>
      {forecast === undefined && <Loading />}
      {forecast && (
        <div className="grid-2 profile-forecast">
          <div>
            <h4 className="sub">Favourites to win</h4>
            {gp.map((d) => <div key={d.driver} className="profile-odds"><DriverName code={d.driver} color={d.color} name={site.driver.get(d.driver)?.name} /> <b>{pct(d.p_win)}</b></div>)}
            {q.length > 0 && <>
              <h4 className="sub" style={{ marginTop: 10 }}>Favourites for pole</h4>
              {q.map((d) => <div key={d.driver} className="profile-odds"><DriverName code={d.driver} color={d.color} name={site.driver.get(d.driver)?.name} /> <b>{pct(d.p_pole)}</b></div>)}
            </>}
          </div>
          <div>
            {w && <>
              <h4 className="sub">Weather</h4>
              <p className="profile-line">{pct(w.rain_chance)} chance of rain in the race{w.heavy_chance > 0 ? ` (${pct(w.heavy_chance)} heavy)` : ""}
                {w.track_expected != null ? `, track about ${Math.round(w.track_expected)} °C` : w.air != null ? `, air about ${Math.round(w.air)} °C` : ""}.</p>
            </>}
            {best && <>
              <h4 className="sub">Quickest strategy</h4>
              <p className="profile-line"><Plan stints={best.stints} /> <span className="muted">{best.stops} stop{best.stops === 1 ? "" : "s"}</span></p>
              {s!.strategies[1] && <p className="note">Next: {s!.strategies[1].plan}, {dec(s!.strategies[1].delta, 1)} s slower over the race.</p>}
            </>}
          </div>
        </div>
      )}
      {forecast === null && <p className="muted">No forecast for this round yet.</p>}
      {forecast && <p className="note"><a href={`#next/${ev.round}`}>The full forecast</a></p>}
    </>
  );
}

function WinnersHere({ past, ev, hc }: { past: CircuitRace[]; ev?: CalendarEvent; hc?: CircuitRow }) {
  const site = useSite();
  if (!past.length && !ev?.done_R) return <p className="note">No Grand Prix here before {site.meta.season}.</p>;
  const teams = new Map<string, { team: string; color: string; wins: number; year: number }>();
  for (const r of past) {
    const t = teams.get(r.team) ?? { team: r.team, color: r.color, wins: 0, year: r.year };
    t.wins++; t.year = r.year;
    teams.set(r.team, t);
  }
  const topTeam = [...teams.values()].sort((a, b) => b.wins - a.wins)[0];
  const byDriver = new Map<string, number>();
  for (const r of past) byDriver.set(r.winner_name, (byDriver.get(r.winner_name) ?? 0) + 1);
  const topDrivers = [...byDriver.entries()].filter(([, n]) => n === hc?.top_wins).map(([name]) => name);
  const withGrid = past.filter((r) => r.winner_grid != null && r.winner_grid > 0);
  const fromPole = withGrid.filter((r) => r.winner_grid === 1).length;
  const thisYear = ev?.done_R && ev.winner ? site.driver.get(ev.winner) : undefined;
  const recent: { year: number; code: string; name: string; team: string; color: string; ref?: string; pole: string | null; round?: number }[] = [
    ...(thisYear && ev ? [{ year: site.meta.season, code: thisYear.driver, name: thisYear.name, team: thisYear.team, color: thisYear.color, pole: ev.pole ?? null }] : []),
    ...[...past].sort((a, b) => b.year - a.year).map((r) => ({ year: r.year, code: r.winner, name: r.winner_name, team: r.team, color: r.color, ref: r.winner_ref, pole: r.pole, round: r.round })),
  ].slice(0, 6);
  return (
    <>
      <h3>Who wins here</h3>
      {hc && (
        <Tiles tiles={[
          { label: "Most wins", value: hc.top_wins, note: topDrivers.join(", ") || hc.top_winner },
          ...(topTeam ? [{ label: "Most successful team", value: topTeam.wins, note: topTeam.team }] : []),
          ...(withGrid.length ? [{ label: "Won from pole", value: pct(fromPole / withGrid.length), note: `${fromPole} of ${withGrid.length} races` }] : []),
        ]} />
      )}
      <Table
        data={recent}
        rowKey={(r) => r.year}
        sort="year"
        desc
        cards={false}
        onRow={(r) => { window.location.hash = r.year === site.meta.season ? `races/${ev!.round}` : link.race(r.year, r.round!).slice(1); }}
        columns={[
          { key: "year", label: "Season", value: (r) => r.year, numeric: true },
          { key: "winner", label: "Winner", value: (r) => r.name, render: (r) => r.year === site.meta.season ? <DriverName code={r.code} color={r.color} name={r.name} /> : <DriverName code={r.code} color={r.color} name={r.name} historyRef={r.ref} /> },
          { key: "team", label: "Team", value: (r) => r.team, render: (r) => <TeamName team={r.team} color={r.color} year={r.year} /> },
          { key: "pole", label: "Pole", value: (r) => r.pole ?? "" },
        ]}
      />
    </>
  );
}

function HistoryCircuitProfile({ refId }: { refId: string }) {
  const file = useData<CircuitsFile>(H.circuits);
  const hc = useMemo(() => rows<CircuitRow>(file?.circuits).find((x) => x.circuit === refId), [file, refId]);
  const past = useMemo(() => rows<CircuitRace>(file?.races).filter((x) => x.circuit === refId), [file, refId]);
  if (file === undefined) return <Loading />;
  if (!hc) return <p>No such circuit in the History files.</p>;
  return (
    <div className="profile">
      <CircuitHero c={undefined} hc={hc} />
      <Stats items={[
        { label: "Grands Prix", value: hc.races },
        { label: "Years", value: hc.first === hc.last ? hc.first : `${hc.first}–${hc.last}` },
        { label: "Race laps", value: past.at(-1)?.laps ?? "–", title: "At the latest race here" },
        { label: "Fastest race lap", value: hc.record ? lapTime(hc.record) : "–", title: hc.record ? `${hc.record_by}, ${hc.record_year}` : undefined },
      ]} />
      <WinnersHere past={past} hc={hc} />
    </div>
  );
}

// ---------------------------------------------------------------- how a race played (also on the Races page)

type Ranked = { value: number | null; rank: number | null; of: number };

/** `value`'s place among every finished round's (1 = highest, or lowest with `low`). */
function rankAmong(all: (number | null)[], value: number | null, low = false): Ranked {
  const vals = all.filter((v): v is number => v != null);
  if (value == null) return { value, rank: null, of: vals.length };
  const rank = 1 + vals.filter((v) => (low ? v < value : v > value)).length;
  return { value, rank, of: vals.length };
}
const rankText = (r: Ranked, most: string, least: string) => {
  if (r.rank == null || r.of < 3) return "";
  const fromEnd = r.of - r.rank + 1;
  if (r.rank === 1) return `${most} of ${r.of} races`;
  if (fromEnd === 1) return `${least} of ${r.of} races`;
  return r.rank <= fromEnd ? `${ordinal(r.rank)} ${most} of ${r.of}` : `${ordinal(fromEnd)} ${least} of ${r.of}`;
};

/** How one finished Grand Prix played, each figure ranked against this season's other races. */
export function CircuitAnalysis({ round, prof }: { round: number; prof?: Profiles | null }) {
  const loaded = useData<Profiles>(prof ? null : PROFILES);
  const data = prof ?? loaded;
  const site = useSite();
  if (data === undefined) return <Loading />;
  const done = (data?.circuits ?? []).filter((c) => c.analysis);
  const me = done.find((c) => c.round === round)?.analysis;
  if (!me) return null;
  const col = (f: (a: RaceAnalysis) => number | null) => done.map((c) => f(c.analysis!));
  const passes = rankAmong(col((a) => a.passes_per_lap), me.passes_per_lap);
  const moved = rankAmong(col((a) => a.places_moved), me.places_moved);
  const stops = rankAmong(col((a) => a.stops), me.stops);
  const temp = rankAmong(col((a) => a.track_temp), me.track_temp);
  const leads = rankAmong(col((a) => a.lead_changes), me.lead_changes);
  const neutral = me.sc_laps + me.vsc_laps;
  const climber = me.climber ? site.driver.get(me.climber) : undefined;
  // Field median deg per compound across the season's races, to say whether this track ate its tyres.
  const degLine = Object.entries(me.deg).map(([comp, v]) => {
    const others = done.map((c) => c.analysis!.deg[comp]).filter((x): x is number => x != null).sort((a, b) => a - b);
    const med = others.length ? others[Math.floor(others.length / 2)] : null;
    const k = compoundKey(comp);
    return { k, name: COMPOUND[k].name, v, med };
  });

  // The headline: what stood out most against the rest of the season.
  const lines: string[] = [];
  if (passes.rank != null && passes.of >= 3) {
    if (passes.rank <= 3) lines.push(`one of the season's easiest races to pass in (${rankText(passes, "most passes a lap", "fewest passes a lap")})`);
    else if (passes.of - passes.rank < 3) lines.push(`one of the season's hardest to pass in (${rankText(passes, "most passes a lap", "fewest passes a lap")})`);
  }
  if (stops.rank != null && stops.of >= 3 && (stops.rank <= 2 || stops.of - stops.rank < 2)) lines.push(`${stops.rank <= 2 ? "heavy" : "light"} on pit stops (${rankText(stops, "most", "fewest")})`);
  if (temp.rank === 1 && temp.of >= 3) lines.push("the hottest track of the season");
  if (neutral >= 10) lines.push(`${neutral} laps behind the Safety Car or VSC`);
  if (me.rain_laps > 0) lines.push(`rain on ${me.rain_laps} lap${me.rain_laps === 1 ? "" : "s"}`);

  return (
    <div className="circuit-analysis">
      {lines.length > 0 && <p className="profile-line">{site.event.get(round)?.event ?? "This race"} was {lines.join("; ")}.</p>}
      <Stats items={[
        { label: "Passes on track (estimate)", value: `${me.passes} (${dec(me.passes_per_lap, 1)} a lap)`, title: `${rankText(passes, "most", "fewest")}. Places gained over laps run clear of the pits, lap 1 and Safety Cars, so it's a comparison, not a count` },
        { label: "Places moved per finisher", value: dec(me.places_moved, 1), title: `Average |grid − finish|: ${rankText(moved, "most", "fewest")}` },
        { label: "Lead changes", value: me.lead_changes, title: rankText(leads, "most", "fewest") },
        { label: "Winner started", value: P(me.winner_grid) },
        { label: "Biggest climber", value: climber ? <>{me.climber} {P(me.climber_from)}→{P(me.climber_to)}</> : "–" },
        { label: "Stops per finisher", value: dec(me.stops, 1), title: rankText(stops, "most", "fewest") },
        { label: "Most common strategy", value: me.top_plan ? `${me.top_plan} (${me.top_plan_n} cars)` : "–" },
        { label: "Safety Car / VSC laps", value: `${me.sc_laps} / ${me.vsc_laps}${me.red_laps ? ` · red ${me.red_laps}` : ""}` },
        { label: "Track temperature", value: me.track_temp != null ? `${Math.round(me.track_temp)} °C` : "–", title: rankText(temp, "hottest", "coolest") },
        { label: "Finishers", value: `${me.finishers} of ${me.starters}` },
      ]} />
      <p className="note ranks">
        {[rankText(passes, "most passes a lap", "fewest passes a lap"), rankText(stops, "most stops", "fewest stops"), rankText(temp, "hottest", "coolest")]
          .filter(Boolean).map((t, i) => <span key={i}>{t[0].toUpperCase() + t.slice(1)}</span>)}
      </p>
      {degLine.length > 0 && (
        <p className="note">
          Tyre wear (field median, s/lap): {degLine.map((d, i) => (
            <span key={d.k}>{i > 0 && " · "}{d.name} {d.v >= 0 ? "+" : ""}{d.v.toFixed(3)}{d.med != null ? ` (season median ${d.med >= 0 ? "+" : ""}${d.med.toFixed(3)})` : ""}</span>
          ))}
        </p>
      )}
    </div>
  );
}
