// Car upgrades (the FIA's Car Presentation Submissions, modules/upgrades.py) and the F1 news the
// site scans (modules/news.py): a weekend's upgrade list on its session pages, and on Next Race
// the season's upgrades with how each team's pace moved after, plus the latest news.
import { useMemo, useState } from "react";
import { rows, type Columns, type News, type NewsItem, type UpgradeRow } from "../data";
import { shortEvent, signed } from "../format";
import { useData, useSite } from "../site";
import { DriverChip, TeamBadge, TeamName } from "./f1";
import { Segmented, Table } from "./ui";

const REASON: Record<string, { label: string; cls: string }> = {
  performance: { label: "Performance", cls: "tag good-tag" },
  circuit: { label: "Circuit specific", cls: "tag" },
  reliability: { label: "Reliability", cls: "tag" },
  cooling: { label: "Cooling", cls: "tag" },
  other: { label: "Other", cls: "tag" },
};

interface Effect { round: number; team: string; items: number; before: number; after: number; change: number }
interface TeamSummary { team: string; rounds: number; parts: number; quickerN: number; avg: number; last: Effect }

/** Each team's upgrade record this season: how often its pace beat its previous races after one. */
function summarise(effect: Effect[]): TeamSummary[] {
  const by = new Map<string, Effect[]>();
  for (const e of effect) by.set(e.team, [...(by.get(e.team) ?? []), e]);
  return [...by.entries()].map(([team, es]) => ({
    team, rounds: es.length, parts: es.reduce((a, e) => a + e.items, 0),
    quickerN: es.filter((e) => e.change < 0).length, avg: es.reduce((a, e) => a + e.change, 0) / es.length,
    last: es.reduce((a, e) => (e.round > a.round ? e : a)),
  }));
}

/** Team colours from the standings, for team names on their own. */
function useTeamColor() {
  const site = useSite();
  return useMemo(() => new Map(site.teams.map((t) => [t.team, t.color])), [site]);
}

/** A session page's "Car Upgrades": what each team brought this weekend, and (after the race) how its pace moved. */
export function WeekendUpgrades({ items, round }: { items?: Columns; round: number }) {
  const teamColor = useTeamColor();
  const news = useData<News>("news.json");
  const list = useMemo(() => rows<UpgradeRow>(items), [items]);
  const effect = useMemo(() => rows<Effect>(news?.upgrade_effect).filter((e) => e.round === round), [news, round]);
  if (!list.length) return null;
  const perf = list.filter((u) => u.reason === "performance");
  const byTeam = new Map<string, number>();
  for (const u of perf) byTeam.set(u.team, (byTeam.get(u.team) ?? 0) + 1);
  return (
    <section>
      <h3>Car Upgrades</h3>
      <p className="muted">
        What each team declared new this weekend (the FIA's Car Presentation Submissions, published on the Thursday), with the reason
        it gave: {perf.length} performance parts from {byTeam.size} team{byTeam.size === 1 ? "" : "s"}, the rest for this circuit or reliability.
        An upgrade doesn't always work: {effect.length ? "the last column is how the team's race pace moved against its previous three races (negative = quicker, the field's average moves taken out)." : "once the race has run, the team's pace against its previous races shows whether it did."}
      </p>
      <Table<UpgradeRow>
        data={list}
        rowKey={(u) => `${u.team}-${u.item}`}
        cardTitle={(u) => <><TeamName team={u.team} color={teamColor.get(u.team)} />: {u.component}</>}
        cardSub={["reason"]}
        cardStats={[]}
        columns={[
          { key: "team", label: "Team", value: (u) => u.team, render: (u) => <TeamName team={u.team} color={teamColor.get(u.team)} /> },
          { key: "component", label: "Component", value: (u) => u.component },
          { key: "reason", label: "Reason", value: (u) => u.reason, render: (u) => <span className={REASON[u.reason]?.cls ?? "tag"}>{REASON[u.reason]?.label ?? u.reason}</span> },
          { key: "text", label: "What it does", value: (u) => u.text, wrap: true, sortable: false, render: (u) => <span className="muted">{u.text.replace(/^.*?(?=[A-Z][a-z]+ [a-z])/, "").slice(0, 260)}</span> },
          ...(effect.length ? [{ key: "moved", label: "Pace moved", title: "Team race pace here against its previous three races (negative = quicker)", numeric: true,
            value: (u: UpgradeRow) => effect.find((e) => e.team === u.team)?.change ?? null,
            render: (u: UpgradeRow) => { const e = effect.find((x) => x.team === u.team); return e ? <span className={e.change < 0 ? "good" : "bad"}>{signed(e.change, 2)}%</span> : "–"; } }] : []),
        ]}
      />
    </section>
  );
}

/** Next Race: this round's upgrade list (once the FIA has it), and the season's upgrades against how each team's pace moved after. */
export function UpgradeTracker({ round }: { round: number }) {
  const site = useSite();
  const teamColor = useTeamColor();
  const news = useData<News>("news.json");
  const ups = useMemo(() => rows<UpgradeRow & { round: number }>(news?.upgrades), [news]);
  const effect = useMemo(() => rows<Effect>(news?.upgrade_effect), [news]);
  const [all, setAll] = useState(false);
  if (!news || (!ups.length && !effect.length)) return null;
  const here = ups.filter((u) => u.round === round);
  const hereTeams = [...new Set(here.filter((u) => u.reason === "performance").map((u) => u.team))];
  const worked = effect.filter((e) => e.change < 0).length;
  const evName = (r: number) => shortEvent(site.event.get(r)?.event ?? `Round ${r}`);
  return (
    <section>
      <h3>Car Upgrades</h3>
      <p className="muted">
        {here.length
          ? <>For this race the teams have declared {here.length} updated parts (the FIA's list, Thursday): performance parts from {hereTeams.join(", ") || "nobody"}. </>
          : <>The FIA publishes this race's upgrade list on the Thursday before it. </>}
        This season, after each round where a team brought performance parts, its race pace against its previous three races moved
        quicker {worked} time{worked === 1 ? "" : "s"} out of {effect.length}: upgrades don't always work, so the forecast counts them only as far as
        the replays of past seasons support.
      </p>
      {here.length > 0 && (
        <Table<UpgradeRow>
          data={here}
          rowKey={(u) => `${u.team}-${u.item}`}
          cardTitle={(u) => <><TeamName team={u.team} color={teamColor.get(u.team)} />: {u.component}</>}
          cardSub={["reason"]}
          cardStats={[]}
          columns={[
            { key: "team", label: "Team", value: (u) => u.team, render: (u) => <TeamName team={u.team} color={teamColor.get(u.team)} /> },
            { key: "component", label: "Component", value: (u) => u.component },
            { key: "reason", label: "Reason", value: (u) => u.reason, render: (u) => <span className={REASON[u.reason]?.cls ?? "tag"}>{REASON[u.reason]?.label ?? u.reason}</span> },
          ]}
        />
      )}
      {effect.length > 0 && (
        <>
          <h4 className="sub">Did They Work?</h4>
          <Table<TeamSummary>
            data={summarise(effect)}
            rowKey={(t) => t.team}
            sort="avg"
            desc={false}
            cardTitle={(t) => <TeamName team={t.team} color={teamColor.get(t.team)} />}
            cardSub={["last"]}
            cardStats={["quicker", "avg"]}
            columns={[
              { key: "team", label: "Team", value: (t) => t.team, render: (t) => <TeamName team={t.team} color={teamColor.get(t.team)} /> },
              { key: "rounds", label: "Rounds", title: "Rounds it brought performance parts to", value: (t) => t.rounds, numeric: true },
              { key: "parts", label: "Parts", value: (t) => t.parts, numeric: true },
              { key: "quicker", label: "Quicker after", title: "Rounds its race pace beat its previous three races", value: (t) => t.quickerN / t.rounds, render: (t) => `${t.quickerN} of ${t.rounds}`, numeric: true },
              { key: "avg", label: "Average move", title: "Mean change in race pace (negative = quicker)", value: (t) => t.avg, render: (t) => <span className={t.avg < 0 ? "good" : "bad"}>{signed(t.avg, 2)}%</span>, numeric: true },
              { key: "last", label: "Latest", value: (t) => t.last.round, render: (t) => <>{evName(t.last.round)}: <span className={t.last.change < 0 ? "good" : "bad"}>{signed(t.last.change, 2)}%</span></>, numeric: true },
            ]}
          />
          <button className="link-button" onClick={() => setAll(!all)}>{all ? "Hide" : "Show"} every round ({effect.length})</button>
          {all && <Table<Effect>
            data={effect}
            rowKey={(e) => `${e.round}-${e.team}`}
            sort="round"
            desc
            cardTitle={(e) => <><TeamName team={e.team} color={teamColor.get(e.team)} />, {evName(e.round)}</>}
            cardSub={[]}
            cardStats={["items", "change"]}
            columns={[
              { key: "round", label: "Round", value: (e) => e.round, render: (e) => evName(e.round), rank: true },
              { key: "team", label: "Team", value: (e) => e.team, render: (e) => <TeamName team={e.team} color={teamColor.get(e.team)} /> },
              { key: "items", label: "Parts", title: "Performance parts declared", value: (e) => e.items, numeric: true },
              { key: "before", label: "Before", title: "Race pace over the previous three races (% against the field)", value: (e) => e.before, render: (e) => `${signed(e.before, 2)}%`, numeric: true },
              { key: "after", label: "That race", value: (e) => e.after, render: (e) => `${signed(e.after, 2)}%`, numeric: true },
              { key: "change", label: "Moved", title: "Negative = quicker than before", value: (e) => e.change, render: (e) => <span className={e.change < 0 ? "good" : "bad"}>{signed(e.change, 2)}%</span>, numeric: true },
            ]}
          />}
        </>
      )}
    </section>
  );
}

const TOPIC_LABEL: Record<string, string> = { pu: "Power units", penalty: "Penalties", upgrade: "Upgrades" };

/** Next Race: the latest F1 news on power units, penalties and upgrades, from the sites the model reads. */
export function NewsFeed({ round }: { round: number }) {
  const site = useSite();
  const news = useData<News>("news.json");
  const teamColor = useTeamColor();
  const [topic, setTopic] = useState<"all" | "pu" | "penalty" | "upgrade">("all");
  const [more, setMore] = useState(false);
  const items = useMemo(() => rows<NewsItem>(news?.items), [news]);
  if (!news || !items.length) return null;
  const shown = items.filter((i) => topic === "all" || i.topics.includes(topic));
  const list = more ? shown : shown.slice(0, 12);
  const when = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const evName = (r: number) => shortEvent(site.event.get(r)?.event ?? `Round ${r}`);
  return (
    <section>
      <h3>In the News</h3>
      <p className="muted">
        Power units, penalties and upgrades from {news.feeds.join(", ")}: their headlines, read every update. A power-unit penalty that
        two or more of them report for a named race counts in the forecast (Power-Unit Penalties above); the rest is here to read.
      </p>
      <div className="toolbar">
        <Segmented label="Topic" value={topic} onChange={(v) => { setTopic(v); setMore(false); }}
                   options={[{ value: "all", label: "All" }, ...(["pu", "penalty", "upgrade"] as const).map((t) => ({ value: t, label: TOPIC_LABEL[t] }))]} />
      </div>
      <ul className="news-list">
        {list.map((i) => (
          <li key={i.id}>
            <a href={i.link} target="_blank" rel="noreferrer">{i.title}</a>
            <div className="news-meta">
              <span>{i.source} · {when(i.published)}</span>
              {i.drivers.map((d) => <DriverChip key={d} code={d} color={site.driver.get(d)?.color} />)}
              {i.teams.map((t) => <TeamBadge key={t} team={t} color={teamColor.get(t)} />)}
              {i.rounds.filter((r) => r >= round).map((r) => <span key={r} className="tag">{evName(r)}</span>)}
              {i.topics.map((t) => <span key={t} className="tag">{TOPIC_LABEL[t]}</span>)}
            </div>
            {i.summary && <p className="muted small">{i.summary}</p>}
          </li>
        ))}
      </ul>
      {shown.length > list.length && <button className="link-button" onClick={() => setMore(true)}>Show all {shown.length}</button>}
    </section>
  );
}
