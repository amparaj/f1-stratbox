import { DriverChip } from "../components/f1";
import { Tiles } from "../components/ui";
import { sessionStart, weekendAll, type CalendarEvent } from "../data";
import { shortEvent, when } from "../format";
import { useCurrentRound, useSite } from "../site";
import NextRace from "./NextRace";

/** When a round's weekend starts: its first session. */
const firstStart = (e: CalendarEvent) =>
  weekendAll(e).map((c) => sessionStart(e, c)).filter((t): t is string => !!t).sort()[0] ?? e.race_utc;

/** The weekend that's on (data.currentRound): its sessions' results as they finish and forecasts for the rest.
 *  Between weekends, where to look instead. */
export default function Current() {
  const site = useSite();
  const current = useCurrentRound(site);
  if (current) return <NextRace current={current} />;

  const last = site.meta.calendar.filter((e) => e.done_R).at(-1);
  const next = site.meta.calendar.find((e) => !e.done_R && Date.parse(firstStart(e) ?? "") > Date.now());
  const start = next ? firstStart(next) : null;
  return (
    <>
      <h2>Current Round</h2>
      <p className="lede">
        No race weekend on at the moment. A round shows here from its first session until its Grand Prix result is in:
        each session's result and analysis as it finishes, and the forecasts for the rest. Until then, the last round is
        in <a href="#races">Race Results & Analysis</a> and the next one's odds are in <a href="#next">Next Race Forecast</a>.
      </p>
      <Tiles tiles={[
        ...(last ? [{
          label: "Last round",
          value: <a href={`#races/${last.round}`}>{shortEvent(last.event)}</a>,
          note: <>Round {last.round}{last.winner && <> · won by <DriverChip code={last.winner} color={last.winner_color ?? undefined} /></>}</>,
        }] : []),
        ...(next ? [{
          label: "Next round",
          value: <a href={`#next/${next.round}`}>{shortEvent(next.event)}</a>,
          note: `Round ${next.round}${start ? ` · starts ${when(start)}` : ""}`,
        }] : []),
      ]} />
      {!next && <p>The season is over. <a href="#season">The final standings</a></p>}
    </>
  );
}
