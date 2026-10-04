import { Component, useEffect, useRef, useState, type ReactNode } from "react";
import { Loading, usePhone } from "./components/ui";
import { dayYear, shortEvent, when } from "./format";
import About from "./pages/About";
import NextRace from "./pages/NextRace";
import Races from "./pages/Races";
import Season from "./pages/Season";
import { SiteContext, useHash, useSiteData, type Site } from "./site";

// The season first (where the site opens), then every race, then the race ahead, then how it works.
const PAGES = [
  { id: "season", label: "Season", short: "Season", component: Season },
  { id: "races", label: "Race Results & Analysis", short: "Races", component: Races },
  { id: "next", label: "Next Race Forecast", short: "Next Race", component: NextRace },
  { id: "about", label: "About", short: "About", component: About },
] as const;

export default function App() {
  const site = useSiteData();
  const hash = useHash();
  // The page is the part of the hash before any "/": #races/16/S -> races.
  const page = PAGES.find((p) => p.id === hash.split("/")[0]) ?? PAGES[0];
  const Page = page.component;

  useEffect(() => {
    document.title = `${page.label} · F1 Stratbox`;
  }, [page]);

  // On a phone the header scrolls away but the page tabs stay pinned (as on xP-FPL).
  const phone = usePhone();
  const header = useRef<HTMLElement>(null);
  const nav = useRef<HTMLElement>(null);
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const h = header.current, n = nav.current;
    if (!h || !n || !phone) { setOffset(0); return; }
    const measure = () => setOffset(n.offsetTop);
    const observer = new ResizeObserver(measure);
    observer.observe(h);
    measure();
    return () => observer.disconnect();
  }, [phone, site]);

  return (
    <>
      <header className="top" ref={header} style={offset ? { top: -offset } : undefined}>
        <div className="top-inner">
          <div className="brand">
            <h1>
              <a href="#season" className="brand-logo" aria-label="F1 Stratbox: Season">
                <img src={`${import.meta.env.BASE_URL}logo-mark.png`} alt="" width={40} height={40} />
              </a>
              <span>F1 Stratbox<span className="brand-sub">: Race Results, Analysis & Forecasts</span></span>
            </h1>
            <span className="byline">Created by Ayush Parajuli</span>
          </div>
          {site && <Status site={site} />}
          <nav aria-label="Pages" ref={nav}>
            {PAGES.map((p) => (
              <a key={p.id} href={`#${p.id}`} className={p === page ? "on" : undefined}
                 aria-current={p === page ? "page" : undefined}>
                {phone ? p.short : p.label}
              </a>
            ))}
          </nav>
        </div>
      </header>
      <main>
        {site === undefined && <Loading />}
        {site === null && (
          <p>
            No data yet. Run <code>scripts/export_site.py</code> to write <code>web/public/data/</code>.
          </p>
        )}
        {site && site.meta.sessions.length === 0 && (
          <p>
            No results downloaded yet ({site.meta.pending.length} sessions waiting). The site fills in
            over the next few hourly updates.
          </p>
        )}
        {site && site.meta.sessions.length > 0 && (
          <SiteContext.Provider value={site}>
            <Boundary key={page.id}>
              <Page />
            </Boundary>
          </SiteContext.Provider>
        )}
      </main>
      <footer>
        Lap timing, tyres, race control and results from OpenF1; the calendar via FastF1; starting grids from Jolpica (Ergast).
        Forecasts are a personal prototype, not betting advice. Not affiliated with Formula 1 or the FIA.
      </footer>
    </>
  );
}

/** Keeps one page's failure (a bad chart, an odd data file) from blanking the whole site. */
class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    return this.state.error ? (
      <p>Something went wrong showing this page ({this.state.error.message}). Try another page, or reload.</p>
    ) : this.props.children;
  }
}

/** The header's status lines: the season, the last race and when the data was updated, the next race. */
function Status({ site }: { site: Site }) {
  const { meta } = site;
  const done = meta.calendar.filter((e) => e.done_R);
  const last = done.at(-1);
  const next = meta.next_round ? site.event.get(meta.next_round) : undefined;
  // The next session to start: the sprint on a sprint weekend, if it's still to come.
  const nextStart = next && [next.sprint_utc, next.race_utc].find((t) => t && Date.parse(t) > Date.now());
  const nextLabel = next && nextStart === next.sprint_utc ? "Sprint" : "Race";
  return (
    <div className="status-row">
      <div className="status">
        <span><strong>{meta.season} Season</strong> · Round {done.length} of {meta.calendar.length}</span>
        <span>
          {last && <>Results up to {shortEvent(last.event)} (Round {last.round}) · </>}Data Last Updated {dayYear(meta.generated)}
        </span>
        {next && nextStart && (
          <span>Next: <strong>{shortEvent(next.event)}</strong> {nextLabel.toLowerCase()}, {when(nextStart)}</span>
        )}
      </div>
      {next && nextStart && <Countdown to={nextStart} label={`${nextLabel} in`} />}
    </div>
  );
}

/** Days, hours, minutes and seconds to `to`, ticking every second. */
function Countdown({ to, label }: { to: string; label: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const left = Math.max(0, Math.floor((Date.parse(to) - now) / 1000));
  if (left === 0) return <div className="countdown"><span className="countdown-label">Lights out</span></div>;
  const parts: [number, string][] = [
    [Math.floor(left / 86400), "days"], [Math.floor(left / 3600) % 24, "hrs"],
    [Math.floor(left / 60) % 60, "min"], [left % 60, "sec"],
  ];
  return (
    <div className="countdown" role="timer" aria-label={`Time to the next ${label}`}>
      <span className="countdown-label">{label}</span>
      <div className="countdown-units">
        {parts.map(([n, unit]) => (
          <span key={unit} className="countdown-unit">
            <b>{String(n).padStart(2, "0")}</b>
            <small>{unit}</small>
          </span>
        ))}
      </div>
    </div>
  );
}
