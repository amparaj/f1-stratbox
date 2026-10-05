import * as Plot from "@observablehq/plot";
import { useCallback, useMemo, useState } from "react";
import { color } from "../colors";
import { ChanceBars, DriverChip, TeamDot } from "../components/f1";
import { Chart, Note, plotDefaults, Segmented, Table, Tiles, type Column } from "../components/ui";
import { rows, type CalendarEvent, type DriverStanding, type TeamStanding, type TitleOdds } from "../data";
import { day, int, pct, shortEvent } from "../format";
import { useSite } from "../site";

export default function Season() {
  const site = useSite();
  const { meta, drivers, teams } = site;
  const done = meta.calendar.filter((e) => e.done_R);
  const next = meta.next_round ? site.event.get(meta.next_round) : undefined;
  const oddsD = useMemo(() => new Map(rows<TitleOdds>(meta.title_odds?.drivers).map((o) => [o.driver!, o])), [meta]);
  const oddsT = useMemo(() => new Map(rows<TitleOdds>(meta.title_odds?.teams).map((o) => [o.team, o])), [meta]);
  const [lead, second] = drivers;
  const left = meta.calendar.length - done.length;

  return (
    <>
      <h2>The {meta.season} Season</h2>
      <p className="lede">
        Standings after {done.length} of {meta.calendar.length} rounds, the title odds from simulating the
        rest of the season, and every round's winner. Pick a race for its full analysis.
      </p>
      <Tiles tiles={[
        { label: "Leader", value: lead ? <><DriverChip code={lead.driver} color={lead.color} /> {int(lead.points)} pts</> : "–", note: lead?.team },
        { label: "Lead", value: lead && second ? `${int(lead.points - second.points)} pts` : "–", note: second ? `over ${second.name ?? second.driver}` : undefined },
        { label: "Rounds left", value: left, note: `${meta.calendar.filter((e) => !e.done_R && e.sprint_utc).length} with a sprint · ${int(maxLeft(meta.calendar))} pts still available` },
        ...(lead && oddsD.get(lead.driver) ? [{ label: "Leader's title chance", value: pct(oddsD.get(lead.driver)!.p_title), note: "from 10,000 simulated seasons" }] : []),
        ...(next ? [{ label: "Next race", value: shortEvent(next.event), note: `Round ${next.round} · ${day(next.race_utc)}` }] : []),
      ]} />

      {meta.title_odds && <TitleOddsSection />}

      <section>
        <h3>Drivers' Championship</h3>
        <Table<DriverStanding>
          data={drivers}
          rowKey={(d) => d.driver}
          sort="points"
          cardTitle={(d) => <><DriverChip code={d.driver} color={d.color} /> {d.name}</>}
          cardSub={["team"]}
          cardStats={["points", "wins", "p_title"]}
          columns={[
            { key: "position", label: "Pos", value: (d) => d.position, numeric: true, rank: true },
            { key: "driver", label: "Driver", value: (d) => d.name, render: (d) => <><DriverChip code={d.driver} color={d.color} /> {d.name}</> },
            { key: "team", label: "Team", value: (d) => d.team },
            { key: "points", label: "Points", value: (d) => d.points, numeric: true },
            { key: "wins", label: "Wins", value: (d) => d.wins, numeric: true },
            { key: "podiums", label: "Podiums", value: (d) => d.podiums, numeric: true },
            { key: "dnfs", label: "DNFs", value: (d) => d.dnfs, numeric: true },
            ...(meta.title_odds ? [
              { key: "exp", label: "Projected", title: "Expected points at the end of the season", value: (d: DriverStanding) => oddsD.get(d.driver)?.exp_points, render: (d: DriverStanding) => int(oddsD.get(d.driver)?.exp_points), numeric: true, group: "Forecast" },
              { key: "p_title", label: "Title", title: "Chance of winning the championship", value: (d: DriverStanding) => oddsD.get(d.driver)?.p_title, render: (d: DriverStanding) => pct(oddsD.get(d.driver)?.p_title), numeric: true, group: "Forecast" },
            ] as Column<DriverStanding>[] : []),
          ]}
        />
      </section>

      <section>
        <h3>Points Through the Season</h3>
        <Progression />
      </section>

      <section>
        <h3>Constructors' Championship</h3>
        <Table<TeamStanding>
          data={teams}
          rowKey={(t) => t.team}
          sort="points"
          cards={false}
          columns={[
            { key: "position", label: "Pos", value: (t) => t.position, numeric: true, rank: true },
            { key: "team", label: "Team", value: (t) => t.team, render: (t) => <><TeamDot color={t.color} /> {t.team}</> },
            { key: "points", label: "Points", value: (t) => t.points, numeric: true },
            { key: "wins", label: "Wins", value: (t) => t.wins, numeric: true },
            ...(meta.title_odds ? [
              { key: "p_title", label: "Title", value: (t: TeamStanding) => oddsT.get(t.team)?.p_title, render: (t: TeamStanding) => pct(oddsT.get(t.team)?.p_title), numeric: true },
            ] as Column<TeamStanding>[] : []),
          ]}
        />
      </section>

      <section>
        <h3>Calendar</h3>
        <Table<CalendarEvent>
          data={meta.calendar}
          rowKey={(e) => e.round}
          sort="round"
          onRow={(e) => { window.location.hash = e.done_R ? `races/${e.round}` : `next/${e.round}`; }}
          cardTitle={(e) => `${e.round}. ${shortEvent(e.event)}`}
          cardSub={["date", "location"]}
          cardStats={["winner"]}
          columns={[
            { key: "round", label: "Round", value: (e) => e.round, numeric: true, rank: true },
            { key: "event", label: "Grand Prix", value: (e) => e.event, render: (e) => <a href={e.done_R ? `#races/${e.round}` : `#next/${e.round}`}>{e.event}</a> },
            { key: "location", label: "Circuit", value: (e) => `${e.location}, ${e.country}` },
            { key: "date", label: "Date", value: (e) => day(e.race_utc) },
            { key: "format", label: "Sprint", value: (e) => (e.sprint_utc ? "Sprint" : ""), render: (e) => (e.sprint_utc ? <span className="tag">Sprint</span> : "") },
            { key: "winner", label: "Winner", value: (e) => e.winner ?? "", render: (e) => (e.winner ? <DriverChip code={e.winner} color={e.winner_color} /> : e.round === meta.next_round ? <span className="tag warn">Next</span> : "") },
          ]}
        />
        {meta.pending.length > 0 && (
          <Note>Waiting for timing data: {meta.pending.join(", ")}. The site updates itself once it's out.</Note>
        )}
      </section>
    </>
  );
}

/** Points still to be won: 25 a race (+8 a sprint) for each round left. */
function maxLeft(calendar: CalendarEvent[]): number {
  return calendar.filter((e) => !e.done_R).reduce((s, e) => s + 25 + (e.sprint_utc && !e.done_S ? 8 : 0), 0);
}

function TitleOddsSection() {
  const { meta } = useSite();
  const [which, setWhich] = useState<"drivers" | "teams">("drivers");
  const data = useMemo(() => rows<TitleOdds>(meta.title_odds![which]).filter((o) => o.p_title >= 0.001).slice(0, 8), [meta, which]);
  return (
    <section>
      <h3>Title Odds</h3>
      <p className="muted">
        The rest of the season played 10,000 times from each driver's recent race pace and retirement rate.
        How it works is on the <a href="#about">About</a> page.
      </p>
      <div className="toolbar">
        <Segmented label="Championship" value={which} onChange={setWhich}
                   options={[{ value: "drivers", label: "Drivers" }, { value: "teams", label: "Constructors" }]} />
      </div>
      <div className="grid-2">
        <ChanceBars data={data} label={(o) => o.driver ?? o.team} value={(o) => o.p_title} colorOf={(o) => o.color} title="Title chances" />
        {which === "drivers" && <TitleHistory />}
      </div>
    </section>
  );
}

/** How each contender's title chance moved, race by race (forecast before each round). */
function TitleHistory() {
  const site = useSite();
  const make = useCallback((width: number) => {
    const hist = rows<{ after: number; driver: string; p_title: number }>(site.meta.title_history);
    const latest = Math.max(...hist.map((h) => h.after));
    const top = hist.filter((h) => h.after === latest).sort((a, b) => b.p_title - a.p_title).slice(0, 5).map((h) => h.driver);
    const data = hist.filter((h) => top.includes(h.driver));
    const colorOf = (d: string) => site.driver.get(d)?.color ?? color.neutral;
    const second = new Set(site.drivers.filter((d, i, all) => all.findIndex((x) => x.team === d.team) < i).map((d) => d.driver));
    return Plot.plot({
      ...plotDefaults(width),
      height: 300,
      marginRight: 44,
      x: { label: "After round", tickFormat: "d", ticks: Math.min(latest, 8) },
      y: { label: "Title chance", tickFormat: "%", domain: [0, 1], grid: true },
      marks: [
        ...top.map((d) => Plot.line(data.filter((h) => h.driver === d), { x: "after", y: "p_title", stroke: colorOf(d), strokeWidth: 2, strokeDasharray: second.has(d) ? "4,3" : undefined })),
        Plot.text(data.filter((h) => h.after === latest), { x: "after", y: "p_title", text: "driver", dx: 6, textAnchor: "start", fill: color.ink2, fontSize: 11 }),
        Plot.tip(data, Plot.pointer({ x: "after", y: "p_title", title: (h) => `${h.driver} after round ${h.after}: ${(h.p_title * 100).toFixed(1)}%` })),
      ],
    });
  }, [site]);
  return <Chart make={make} ariaLabel="Title chances through the season" />;
}

/** Cumulative points by round for the top ten, labelled at the end. */
function Progression() {
  const site = useSite();
  const make = useCallback((width: number) => {
    const prog = rows<{ round: number; driver: string; points: number }>(site.meta.progression);
    const top = site.drivers.slice(0, 10).map((d) => d.driver);
    const data = prog.filter((p) => top.includes(p.driver));
    const latest = Math.max(...prog.map((p) => p.round));
    const second = new Set(site.drivers.filter((d, i, all) => all.findIndex((x) => x.team === d.team) < i).map((d) => d.driver));
    return Plot.plot({
      ...plotDefaults(width),
      height: 380,
      marginRight: 44,
      x: { label: "Round", tickFormat: "d" },
      y: { label: "Points", grid: true },
      marks: [
        ...top.map((d) => Plot.line(data.filter((p) => p.driver === d), {
          x: "round", y: "points", stroke: site.driver.get(d)?.color ?? color.neutral, strokeWidth: 2, strokeDasharray: second.has(d) ? "4,3" : undefined,
        })),
        Plot.text(data.filter((p) => p.round === latest), { x: "round", y: "points", text: "driver", dx: 6, textAnchor: "start", fill: color.ink2, fontSize: 11 }),
        Plot.tip(data, Plot.pointer({ x: "round", y: "points", title: (p) => `${p.driver} after round ${p.round}: ${p.points} pts` })),
      ],
    });
  }, [site]);
  return <Chart make={make} ariaLabel="Points through the season, top ten drivers" />;
}
