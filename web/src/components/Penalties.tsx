// Stewards' decisions and power units: a weekend's decisions and new elements (every session
// page), and each driver's elements against the allocation with the chance of a penalty at every
// Grand Prix left (the Next Race page). Data from modules/penalties.py via site_export.
import { useMemo, useState } from "react";
import { rows, SESSION_LABEL, type AnySession, type PenaltyRow, type PowerUnitDriver, type PowerUnits, type WeekendPenalties } from "../data";
import { pct, shortEvent } from "../format";
import { useData, useSite } from "../site";
import { DriverChip, TeamName } from "./f1";
import { Note, Segmented, Table, usePhone } from "./ui";

export const ELEMENT_ORDER = ["ICE", "TC", "MGU-H", "MGU-K", "ES", "CE", "EX", "ANC"];

/** A grid drop as text: "Back of the grid", "Pit lane", "10 places". */
export const dropText = (g: number | string | null | undefined) =>
  g == null ? "" : g === "back" ? "Back of the grid" : g === "pit" ? "Pit-lane start" : `${g} places`;

const SESSION_ORDER = ["FP1", "FP2", "FP3", "SQ", "S", "Q", "R"];
const SESSION_NAME: Record<string, string> = { FP1: "Practice 1", FP2: "Practice 2", FP3: "Practice 3", SQ: "Sprint Quali", S: "Sprint", Q: "Qualifying", R: "Grand Prix" };
/** Outcomes that aren't a penalty. */
const NOT_PENALTY = /^(No Further Action|Warning|Not Investigated|Noted)/i;

function penaltyText(r: PenaltyRow): string {
  const parts: string[] = [];
  if (r.time_s) parts.push(`${r.time_s} s`);
  if (r.grid != null) parts.push(dropText(r.grid));
  if (r.points) parts.push(`${r.points} pt${r.points === 1 ? "" : "s"}`);
  if (r.fine) parts.push(`€${r.fine.toLocaleString()}`);
  return parts.join(" · ");
}

/** A session page's "Stewards & Power Units" section: the weekend's decisions and new PU elements. */
export function WeekendPenaltiesSection({ code, data, round }: { code: AnySession; data?: WeekendPenalties | null; round: number }) {
  const site = useSite();
  const [scope, setScope] = useState<"penalties" | "all">("penalties");
  const all = useMemo(() => rows<PenaltyRow>(data?.decisions)
    .sort((a, b) => SESSION_ORDER.indexOf(a.session) - SESSION_ORDER.indexOf(b.session)), [data]);
  if (!data) return null;
  const shown = scope === "all" ? all : all.filter((r) => !NOT_PENALTY.test(r.outcome ?? ""));
  const fitted = data.pu_fitted ?? [];
  const over = fitted.filter((f) => f.drop != null);
  const colorOf = (d: string | null) => (d ? site.driver.get(d)?.color : undefined);
  const lagging = all.length === 0;
  return (
    <section>
      <h3>Stewards &amp; Power Units</h3>
      <p className="muted">
        Every stewards' decision of the weekend (all sessions; this page is the {SESSION_LABEL[code].toLowerCase()}), and the
        power-unit elements each car fitted. A new element past the season's allocation costs grid places in the Grand Prix:
        10 for the first of a kind, 5 for each one after, the back of the grid past 15.
      </p>
      {over.length > 0 && (
        <Note>
          Power-unit grid penalties this weekend: {over.map((f, i) => (
            <span key={f.driver}>{i > 0 && ", "}<DriverChip code={f.driver} color={colorOf(f.driver)} /> {dropText(f.drop).toLowerCase()} ({f.elements.join(", ")})</span>
          ))}.
        </Note>
      )}
      {lagging ? (
        <p className="muted">f1penalties.com hasn't added this round's decisions yet (it runs a few days to a few weeks behind the FIA). The site checks for them on every update.</p>
      ) : (
        <>
          <div className="toolbar">
            <Segmented label="Show" value={scope} onChange={setScope}
                       options={[{ value: "penalties", label: `Penalties (${all.filter((r) => !NOT_PENALTY.test(r.outcome ?? "")).length})` }, { value: "all", label: `Every decision (${all.length})` }]} />
          </div>
          {shown.length ? (
            <Table<PenaltyRow>
              data={shown}
              rowKey={(r) => `${r.session}-${r.driver}-${r.allegation}-${r.outcome}-${r.notes ?? ""}`}
              cardTitle={(r) => <><DriverChip code={r.code ?? r.driver.split(" ").at(-1)!.slice(0, 3).toUpperCase()} color={colorOf(r.code)} /> {r.driver}</>}
              cardSub={["session", "allegation"]}
              cardStats={["outcome"]}
              columns={[
                { key: "session", label: "Session", value: (r) => SESSION_NAME[r.session] ?? r.session, render: (r) => <span className={r.session === code ? "tag good-tag" : "tag"}>{SESSION_NAME[r.session] ?? r.session}</span> },
                { key: "driver", label: "Driver", value: (r) => r.driver, render: (r) => <>{r.code && <DriverChip code={r.code} color={colorOf(r.code)} />} {r.driver}</> },
                { key: "allegation", label: "Offence", value: (r) => r.allegation, wrap: true, render: (r) => <>{r.allegation}{r.involving ? <span className="muted"> (with {r.involving})</span> : null}</> },
                { key: "outcome", label: "Outcome", value: (r) => r.outcome, wrap: true, render: (r) => <span className={NOT_PENALTY.test(r.outcome ?? "") ? "muted" : r.kind === "pu" ? "tag warn" : undefined}>{r.outcome}</span> },
                { key: "penalty", label: "Penalty", value: (r) => penaltyText(r), sortable: false },
                { key: "notes", label: "Notes", value: (r) => r.notes ?? "", wrap: true, render: (r) => <span className="muted">{r.notes ?? ""}</span> },
              ]}
            />
          ) : <p className="muted">No penalties this weekend{scope === "penalties" && all.length ? ": every decision was no further action or a warning" : ""}.</p>}
        </>
      )}
      {fitted.length > 0 && (
        <>
          <h4 className="sub">New Power-Unit Elements</h4>
          <Table<WeekendPenalties["pu_fitted"][number]>
            data={fitted}
            rowKey={(f) => f.driver}
            cardTitle={(f) => <><DriverChip code={f.driver} color={colorOf(f.driver)} /> {site.driver.get(f.driver)?.name ?? f.driver}</>}
            cardSub={[]}
            cardStats={["elements", "drop"]}
            columns={[
              { key: "driver", label: "Driver", value: (f) => f.driver, render: (f) => <><DriverChip code={f.driver} color={colorOf(f.driver)} /> {site.driver.get(f.driver)?.name ?? ""}</> },
              { key: "elements", label: "Fitted", value: (f) => f.elements.length, render: (f) => f.elements.map((el, i) => {
                  const n = f.after[el] - f.elements.slice(i + 1).filter((x) => x === el).length;
                  const lim = data.limits[el];
                  return <span key={i} className={lim != null && n > lim ? "tag warn" : "tag"} title={`${el} number ${n} of ${lim ?? "?"} allowed`} style={{ marginRight: 4 }}>{el} {n}/{lim ?? "?"}</span>;
                }), numeric: true },
              { key: "drop", label: "Grid penalty", value: (f) => (f.drop === "back" ? 99 : f.drop ?? 0), render: (f) => (f.drop == null ? <span className="muted">None (within the allocation)</span> : <span className="tag warn">{dropText(f.drop)}</span>), numeric: true },
            ]}
          />
        </>
      )}
      <p className="muted small">
        Decisions: <a href="https://www.f1penalties.com/data" target="_blank" rel="noreferrer">f1penalties.com</a>
        {round > (data.stewards_round ?? round) ? ` (up to round ${data.stewards_round})` : ""}. Power-unit elements: the FIA Technical Delegate's
        documents ("PU elements used per driver", "New PU elements for this Competition").
      </p>
    </section>
  );
}

const status = (d: PowerUnitDriver, limits: Record<string, number>, elements: string[]) => {
  const over = elements.filter((el) => (d[el] as number) > limits[el]);
  const list = (els: string[]) => (els.length > 3 ? `${els.length} elements` : els.join(", "));
  if (over.length) return { text: `Over on ${list(over)}`, cls: "tag warn", full: `Past the allocation: ${over.join(", ")}` };
  if (d.at_limit.length) return { text: d.at_limit.length === elements.length ? "At the limit on all" : `At the limit on ${list(d.at_limit)}`, cls: "tag", full: `At the allocation: ${d.at_limit.join(", ")}` };
  return { text: "Within the allocation", cls: "tag good-tag", full: "Every element within the allocation" };
};

/** The Next Race page's power-unit outlook: elements used, and the chance of a penalty at each Grand Prix left. */
export function PowerUnitOutlook({ round }: { round: number }) {
  const site = useSite();
  const pu = useData<PowerUnits>("power-units.json");
  const [scope, setScope] = useState<"risk" | "all">("risk");
  const phone = usePhone();
  const drivers = useMemo(() => rows<PowerUnitDriver>(pu?.drivers), [pu]);
  const risk = useMemo(() => rows<{ driver: string; round: number; p: number; p_by: number; plan: boolean }>(pu?.risk), [pu]);
  if (!pu || !drivers.length) return null;
  const rounds = [...new Set(risk.map((r) => r.round))].sort((a, b) => a - b);
  const pAt = new Map(risk.map((r) => [`${r.driver}-${r.round}`, r]));
  const shown = scope === "all" ? drivers : drivers.filter((d) => (d.p_season ?? 0) >= 0.25 || d.announced != null || d.plans?.length);
  const at = (d: PowerUnitDriver) => pAt.get(`${d.driver}-${round}`);
  const plans = drivers.filter((d) => d.plans?.length);
  const evName = (r: number) => shortEvent(site.event.get(r)?.event ?? `Round ${r}`);
  // The grid's headings: the race, or on a phone (seven races across 360 px) a three-letter code
  // that the circuit-factor line under it spells out.
  const gridName = (r: number) => {
    const n = evName(r).replace(/ GP$/, "");
    if (!phone) return n;
    const w = n.split(/\s+/);
    return (w.length > 1 ? w.map((x) => x[0]).join("") : n.slice(0, 3)).toUpperCase();
  };
  return (
    <section>
      <h3>Power-Unit Penalties</h3>
      <p className="muted">
        Each driver's power-unit elements so far against the season's allocation (FIA Technical Delegate, after round {pu.fia_round}), and the
        chance they take a new element past it, so a grid penalty, at each Grand Prix left. The chance comes from a model fitted on every round
        since 2022: how far the driver's use runs ahead of the allocation for the races left, whether they've already gone past it this
        season (a fresh pool makes the next one less likely), how much of the season is left, and how often teams have chosen that circuit
        (overtaking-friendly tracks like Spa, Austin and Monza draw most). Reported plans count on top, each as one more penalty: a team's
        statement, or a penalty at a named race that at least two of the F1 news sites the site reads report (more likely when
        an article quotes the team or the driver saying so). The race forecasts and title
        odds draw these penalties race by race, and an announced one counts in full.
      </p>
      {plans.flatMap((d) => d.plans.map((g, k) => (
        <Note key={`${d.driver}-${k}`}>
          <DriverChip code={d.driver} color={d.color} /> {g.kind === "news" ? (g.quoted ? "Team or driver quoted in the news" : "Reported in the news") : "Team plan"}
          {" "}({g.rounds.map(evName).join(" or ")}): {g.note}.{" "}
          {g.links.map((l, i) => <span key={l.link}>{i > 0 && " · "}<a href={l.link} target="_blank" rel="noreferrer">{l.source}{l.title && g.kind === "news" ? `: ${l.title}` : ""}</a></span>)}.
          {" "}Chance there: {g.rounds.map((r, i) => <span key={r}>{i > 0 && ", "}{evName(r)} {pct(pAt.get(`${d.driver}-${r}`)?.p)}</span>)}; at least one
          before the season ends: {pct(d.p_season)}.
        </Note>
      )))}
      <div className="toolbar">
        <Segmented label="Drivers" value={scope} onChange={setScope}
                   options={[{ value: "risk", label: "Likely to take one" }, { value: "all", label: "Everyone" }]} />
      </div>
      <Table<PowerUnitDriver>
        data={shown}
        rowKey={(d) => d.driver}
        sort="p_season"
        desc
        cardTitle={(d) => <><DriverChip code={d.driver} color={d.color} /> {d.team}</>}
        cardSub={["status", "likely"]}
        cardStats={["p_here", "p_season"]}
        columns={[
          { key: "driver", label: "Driver", value: (d) => d.driver, render: (d) => <DriverChip code={d.driver} color={d.color} /> },
          { key: "team", label: "Team", value: (d) => d.team ?? "", render: (d) => <TeamName team={d.team} color={d.color} /> },
          ...pu.elements.map((el) => ({
            key: el, label: el, group: "Elements used", title: `${pu.names[el]}: ${pu.limits[el]} allowed`, numeric: true,
            value: (d: PowerUnitDriver) => d[el] as number,
            render: (d: PowerUnitDriver) => {
              const n = d[el] as number, lim = pu.limits[el];
              return <span className={n > lim ? "bad" : n === lim ? "at-limit" : "muted"}>{n}<span className="muted">/{lim}</span></span>;
            },
          })),
          { key: "status", label: "Status", value: (d) => status(d, pu.limits, pu.elements).text, render: (d) => { const s = status(d, pu.limits, pu.elements); return <span className={s.cls} title={s.full}>{s.text}</span>; } },
          { key: "penalties", label: "Taken", title: "Power-unit grid penalties so far this season", value: (d) => d.penalties, numeric: true },
          { key: "p_here", label: "This race", title: `Chance of a power-unit grid penalty at the ${site.event.get(round)?.event ?? "next Grand Prix"}`, value: (d) => (d.announced != null ? 1 : at(d)?.p ?? 0), render: (d) => (d.announced != null ? <span className="tag warn">{dropText(d.announced)}</span> : pct(at(d)?.p)), numeric: true },
          { key: "likely", label: "Likeliest race", value: (d) => d.likely_p ?? 0, render: (d) => (d.likely_round ? <>{evName(d.likely_round)} <span className="muted">{pct(d.likely_p)}</span></> : "–"), numeric: true },
          { key: "p_season", label: "Rest of season", title: "Chance of at least one power-unit grid penalty before the season ends", value: (d) => d.p_season ?? 0, render: (d) => pct(d.p_season), numeric: true },
        ]}
      />
      {rounds.length > 1 && (
        <>
          <h4 className="sub">Chance of a Penalty at Each Grand Prix</h4>
          <div className="table-wrap">
            <table className="pu-grid">
              <thead><tr><th>Driver</th>{rounds.map((r) => <th key={r} title={site.event.get(r)?.event}>{gridName(r)}</th>)}</tr></thead>
              <tbody>
                {shown.map((d) => (
                  <tr key={d.driver}>
                    <td><DriverChip code={d.driver} color={d.color} /></td>
                    {rounds.map((r) => {
                      const c = pAt.get(`${d.driver}-${r}`);
                      const p = c?.p ?? 0;
                      return <td key={r} className={c?.plan ? "pu-plan" : undefined} title={`${d.driver}, ${site.event.get(r)?.event}: ${pct(p, 1)}${c?.plan ? " (reported plan)" : ""}`}
                                 style={{ background: `color-mix(in srgb, var(--bad) ${Math.round(Math.min(p, 1) * 70)}%, transparent)` }}>{p >= 0.005 ? pct(p) : ""}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">
            Outlined: a reported plan's round. Circuit factors (how much likelier a penalty is there than at the average race, from
            2020 on): {rounds.map((r, i) => <span key={r}>{i > 0 && ", "}{phone && <b>{gridName(r)}</b>} {evName(r).replace(/ GP$/, "")} ×{(pu.circuits[String(r)] ?? 1).toFixed(1)}</span>)}.
          </p>
        </>
      )}
      <p className="muted small">
        Elements: {pu.elements.map((el) => (pu.names[el] === el ? el : `${el} ${pu.names[el]}`)).join(", ")}. Sources: the FIA's decision documents and{" "}
        <a href="https://www.f1penalties.com/data" target="_blank" rel="noreferrer">f1penalties.com</a>, checked on every update.
      </p>
    </section>
  );
}
