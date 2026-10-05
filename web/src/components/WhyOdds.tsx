// "Why these odds?": what makes each driver's expected performance in a session forecast, two
// drivers side by side, and the sessions behind their form (outliers capped, as the model does).
import { useEffect, useMemo, useState } from "react";
import { rows, SESSION_LABEL, type ForecastWhy, type FormInput, type SessionCode } from "../data";
import { pct, signed } from "../format";
import { useSite } from "../site";
import { DriverChip, SessionBadge } from "./f1";
import { usePhone } from "./ui";

/** config.FORM_CLIP: a session's pace counts at most this far (%) from the driver's median. */
export const FORM_CLIP = 0.25;
/** A cap this big (%) is named in the explanation; smaller ones are only marked in the table. */
const BIG_CAP = 0.1;

/** The breakdown columns a forecast row carries (race or qualifying). */
export interface WhyDriver {
  driver: string; color: string; pace?: number; form?: number; p_dnf?: number;
  race_form?: number | null; quali_form?: number | null; quali_share?: number;
  circuit?: number | null; circuit_term?: number; grid?: number | null; grid_term?: number;
  penalty?: number | string | null; penalty_places?: number; penalty_term?: number;
}

interface Term { key: string; label: string; raw?: string; value: number }

const GRID_SOURCE: Record<string, string> = {
  official: "the official starting grid",
  starting_grid: "the published starting grid (grid penalties applied)",
  qualifying: "the qualifying order, with any announced grid penalties applied (the starting grid isn't out yet)",
};

const penaltyText = (p: number | string | null | undefined) =>
  p == null ? "" : p === "back" ? "back of the grid" : p === "pit" ? "pit-lane start" : `${p} places`;

/** The terms that add up to a driver's expected performance (%, negative = faster). */
export function terms(d: WhyDriver, race: boolean, why: ForecastWhy | null): Term[] {
  const out: Term[] = [];
  const share = race ? d.quali_share ?? 0 : 1;
  const hasR = race && d.race_form != null, hasQ = d.quali_form != null;
  // A driver with only one of the two gets that one at full weight (forecast.expected_pace).
  const wR = hasR ? (hasQ ? 1 - share : 1) : 0, wQ = hasQ ? (hasR ? share : 1) : 0;
  if (hasR) out.push({ key: "race", label: "Race form", raw: `${signed(d.race_form, 2)}% × ${pct(wR)}`, value: d.race_form! * wR });
  if (hasQ) {
    const weekend = race && why?.quali_source === "weekend";
    out.push({ key: "quali", label: weekend ? "This weekend's qualifying" : "Qualifying form", raw: `${signed(d.quali_form, 2)}% × ${pct(wQ)}`, value: d.quali_form! * wQ });
  }
  if (d.circuit_term) out.push({ key: "circuit", label: "Team at this circuit last season", raw: d.circuit != null ? `${signed(d.circuit, 2)}%, part counted` : undefined, value: d.circuit_term });
  if (d.grid != null && d.grid_term != null) out.push({ key: "grid", label: "Grid slot", raw: `P${d.grid}`, value: d.grid_term });
  if (d.penalty_term) out.push({ key: "penalty", label: "Announced grid penalty", raw: penaltyText(d.penalty), value: d.penalty_term });
  return out;
}

const label = (t: Term) => t.label.charAt(0).toLowerCase() + t.label.slice(1);

/** One or two sentences on what separates two drivers. */
function explain(a: WhyDriver, b: WhyDriver, ta: Term[], tb: Term[], race: boolean, inputs: FormInput[], sessionName: (id: string) => string): string[] {
  const out: string[] = [];
  const diff = (a.pace ?? 0) - (b.pace ?? 0);
  const [fast, slow] = diff <= 0 ? [a, b] : [b, a];
  out.push(Math.abs(diff) < 0.005
    ? `${a.driver} and ${b.driver} are expected to be level on pace.`
    : `${fast.driver} is expected to be ${Math.abs(diff).toFixed(2)}% of a lap quicker than ${slow.driver}.`);
  const keys = [...new Set([...ta, ...tb].map((t) => t.key))];
  const gaps = keys.map((k) => {
    const x = ta.find((t) => t.key === k), y = tb.find((t) => t.key === k);
    return { t: (x ?? y)!, gap: (x?.value ?? 0) - (y?.value ?? 0) };
  }).filter((g) => Math.abs(g.gap) >= 0.01).sort((p, q) => Math.abs(q.gap) - Math.abs(p.gap));
  if (gaps.length) {
    const parts = gaps.slice(0, 2).map((g) => `${label(g.t)} (${Math.abs(g.gap).toFixed(2)}% to ${g.gap < 0 ? a.driver : b.driver})`);
    out.push(`The biggest difference${gaps.length > 1 ? "s" : ""}: ${parts.join("; ")}.`);
  }
  if (race && a.p_dnf != null && b.p_dnf != null && Math.abs(a.p_dnf - b.p_dnf) >= 0.03) {
    const [hi, lo] = a.p_dnf > b.p_dnf ? [a, b] : [b, a];
    out.push(`${hi.driver} also has a higher retirement chance (${pct(hi.p_dnf)} against ${pct(lo.p_dnf)}), from their DNFs so far this season.`);
  }
  // Only the caps that move form noticeably; small ones are marked in the session table.
  for (const d of [a, b]) {
    const capped = inputs.filter((f) => f.driver === d.driver && Math.abs(f.used - f.pace) >= BIG_CAP)
      .sort((x, y) => Math.abs(y.used - y.pace) * y.share - Math.abs(x.used - x.pace) * x.share).slice(0, 2);
    if (capped.length) {
      const list = capped.map((f) => `${sessionName(f.session)} (${signed(f.pace, 2)}%, counted as ${signed(f.used, 2)}%)`).join(" and ");
      out.push(`${d.driver}'s ${list} ${capped.length > 1 ? "were" : "was"} well off their usual pace, so ${capped.length > 1 ? "they're" : "it's"} capped as an outlier: one crash, failure or scrappy lap doesn't decide their form.`);
    }
  }
  return out;
}

export function WhyOdds({ drivers, why, code }: { drivers: WhyDriver[]; why: ForecastWhy | null; code: SessionCode }) {
  const site = useSite();
  const phone = usePhone();
  const race = code === "R" || code === "S";
  const order = drivers.map((d) => d.driver);
  const [a, setA] = useState(order[0]);
  const [b, setB] = useState(order[1]);
  // A new table (session or forecast switched): start again from its top two.
  useEffect(() => { setA(order[0]); setB(order[1]); }, [order.join()]);  // eslint-disable-line react-hooks/exhaustive-deps
  const inputs = useMemo(() => (why ? rows<FormInput>(why.form) : []), [why]);
  const da = drivers.find((d) => d.driver === a), db = drivers.find((d) => d.driver === b);
  if (!why || !da || !db || da.pace == null || db.pace == null) return null;

  const place = (id: string) => site.event.get(Number(id.split("-")[0].slice(1)))?.location ?? id.split("-")[0];
  const sessionName = (id: string) => `${place(id)} ${SESSION_LABEL[id.split("-")[1] as SessionCode]}`;
  const ta = terms(da, race, why), tb = terms(db, race, why);
  const keys = [...new Set([...ta, ...tb].map((t) => t.key))];
  const termOf = (ts: Term[], k: string) => ts.find((t) => t.key === k);
  const cell = (t: Term | undefined) => t
    ? <>{signed(t.value, 2)}%{t.raw && <div className="muted why-raw">{t.raw}</div>}</>
    : "–";
  const better = (x: number, y: number) => Math.abs(x - y) < 0.005 ? "level" : x < y ? `${a} by ${Math.abs(x - y).toFixed(2)}%` : `${b} by ${Math.abs(x - y).toFixed(2)}%`;

  // Sessions behind either driver's form, most recent first.
  const sessions = [...new Set(inputs.filter((f) => f.driver === a || f.driver === b).map((f) => f.session))]
    .sort((x, y) => (x < y ? 1 : -1));
  const inputOf = (d: string, s: string) => inputs.find((f) => f.driver === d && f.session === s);
  const formCell = (f: FormInput | undefined) => {
    if (!f) return <span className="muted">–</span>;
    const capped = Math.abs(f.used - f.pace) >= 0.005;
    return (
      <span title={capped ? `More than ${FORM_CLIP}% off their median: counted as ${signed(f.used, 2)}%` : undefined}>
        {signed(f.pace, 2)}%{capped && <b> → {signed(f.used, 2)}%</b>}
        {!phone && <span className="muted"> · {pct(f.share)}</span>}
      </span>
    );
  };
  const pick = (value: string, set: (d: string) => void, title: string) => (
    <label>
      {title}
      <select value={value} onChange={(e) => set(e.target.value)}>
        {order.map((d) => <option key={d} value={d}>{d}</option>)}
      </select>
    </label>
  );

  return (
    <details className="why-details" open>
      <summary>Why these odds? <span className="muted">what puts one driver ahead of another</span></summary>
      <div className="toolbar">
        {pick(a, setA, "Driver")}
        {pick(b, setB, "against")}
      </div>
      <ul className="insights">
        {explain(da, db, ta, tb, race, inputs, sessionName).map((s, i) => <li key={i}>{s}</li>)}
      </ul>
      <div className="table-wrap">
        <table className="compact why-table">
          <thead>
            <tr>
              <th>{phone ? "Pace (%, − = faster)" : "Expected pace (% of a lap, negative = faster)"}</th>
              <th className="num"><DriverChip code={da.driver} color={da.color} /></th>
              <th className="num"><DriverChip code={db.driver} color={db.color} /></th>
              {!phone && <th>Favours</th>}
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const x = termOf(ta, k), y = termOf(tb, k);
              return (
                <tr key={k}>
                  <td>{(x ?? y)!.label}</td>
                  <td className="num">{cell(x)}</td>
                  <td className="num">{cell(y)}</td>
                  {!phone && <td>{better(x?.value ?? 0, y?.value ?? 0)}</td>}
                </tr>
              );
            })}
            <tr className="why-total">
              <td>Expected performance</td>
              <td className="num">{signed(da.pace, 2)}%</td>
              <td className="num">{signed(db.pace, 2)}%</td>
              {!phone && <td>{better(da.pace, db.pace)}</td>}
            </tr>
            {race && da.p_dnf != null && db.p_dnf != null && (
              <tr>
                <td>Retirement chance</td>
                <td className="num">{pct(da.p_dnf)}</td>
                <td className="num">{pct(db.p_dnf)}</td>
                {!phone && <td>{Math.abs(da.p_dnf - db.p_dnf) < 0.005 ? "level" : da.p_dnf < db.p_dnf ? a : b}</td>}
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {race && why.grid_source && <p className="muted">Grid from {GRID_SOURCE[why.grid_source]}.</p>}
      {sessions.length > 0 && (
        <>
          <h4 className="sub">The sessions behind their form</h4>
          <div className="table-wrap">
            <table className="compact why-table">
              <thead>
                <tr>
                  <th>Session</th>
                  <th className="num"><DriverChip code={da.driver} color={da.color} /></th>
                  <th className="num"><DriverChip code={db.driver} color={db.color} /></th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s}>
                    <td><SessionBadge code={s.split("-")[1] as SessionCode} short /> {place(s)}</td>
                    <td className="num">{formCell(inputOf(a, s))}</td>
                    <td className="num">{formCell(inputOf(b, s))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted">
            Each session's pace against the field median{phone ? "" : ", and its share of that driver's form"} (recent rounds count most).
            An arrow marks an outlier: a session more than {FORM_CLIP}% off the driver's median over these rounds counts
            as if it were {FORM_CLIP}% off, so one crash, failure or scrappy lap can't swing the forecast.
            {race ? " Race form is Grands Prix and sprints, qualifying form Qualifying and Sprint Qualifying." : ""}
          </p>
        </>
      )}
    </details>
  );
}
