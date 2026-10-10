import { Component, useEffect, useRef, useState, type ReactNode } from "react";
import { ProfileHost } from "./components/Profiles";
import PageJump from "./components/PageJump";
import { Loading, usePhone } from "./components/ui";
import { SESSION_LABEL, sessionDone, sessionStart, weekendAll, weekendSessions } from "./data";
import { dayYear, shortEvent, when } from "./format";
import About from "./pages/About";
import Current from "./pages/Current";
import History from "./pages/History";
import NextRace from "./pages/NextRace";
import Races from "./pages/Races";
import Season from "./pages/Season";
import { SiteContext, useCurrentRound, useHash, useSiteData, type Site } from "./site";

// What the site is and how it works first (where the site opens), then the season, the weekend that's on, every
// race, the race ahead, and every season before this one.
const PAGES = [
  { id: "about", label: "About", short: "About", component: About },
  { id: "season", label: "Current Season", short: "Current Season", component: Season },
  { id: "current", label: "Current Round", short: "Current Round", component: Current },
  { id: "races", label: "Race Results & Analysis", short: "Past Races", component: Races },
  { id: "next", label: "Next Race Forecast", short: "Next Race", component: NextRace },
  { id: "history", label: "History", short: "History", component: History },
] as const;
const HOME = PAGES[0];

export default function App() {
  const site = useSiteData();
  const hash = useHash();
  // The page is the part of the hash before any "/": #races/16/S -> races.
  const page = PAGES.find((p) => p.id === hash.split("/")[0]) ?? HOME;
  const Page = page.component;

  // The weekend that's on lives on Current Round: links to it on the other pages go there.
  const current = useCurrentRound(site);
  useEffect(() => {
    const [p, r, asked] = hash.split("/");
    if (!current || (p !== "races" && p !== "next") || Number(r) !== current.round) return;
    // #races/16 alone is the weekend's latest finished session; #next/16 alone its next one (Current Round's default).
    const c = asked ?? (p === "races" ? weekendAll(current).filter((x) => sessionDone(current, x)).at(-1) : undefined);
    window.location.replace(`#current${c ? `/${c}` : ""}`);
  }, [hash, current]);

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
    if (!h || !n) return;
    if (!phone) {
      // The whole header stays pinned on a wide screen.
      setOffset(0);
      const pin = () => document.documentElement.style.setProperty("--pinned", `${h.offsetHeight}px`);
      const observer = new ResizeObserver(pin);
      observer.observe(h);
      pin();
      return () => observer.disconnect();
    }
    const measure = () => {
      setOffset(n.offsetTop);
      // What stays pinned (the tabs): a section jumped to lands just below it.
      document.documentElement.style.setProperty("--pinned", `${h.offsetHeight - n.offsetTop}px`);
    };
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
              <a href="#about" className="brand-logo" aria-label="F1 Stratbox: About">
                <img src={`${import.meta.env.BASE_URL}logo-mark.png`} alt="" width={40} height={40} />
              </a>
              <span>F1 Stratbox<span className="brand-sub">: Race Results, Analysis & Forecasts</span></span>
            </h1>
            <span className="byline">Created by Ash Parajuli</span>
          </div>
          {site && <Status site={site} />}
          <nav aria-label="Pages" ref={nav}>
            {PAGES.map((p) => (
              <a key={p.id} href={`#${p.id}`} className={p === page ? "on" : undefined}
                 aria-current={p === page ? "page" : undefined}>
                {p.id === "current" && current && <span className="live-dot" aria-label="on now" />}
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
            {/* History's driver codes repeat across eras: there a chip opens a pop-up only with the driver's History ref. */}
            <ProfileHost scope={page.id === "history" ? "history" : "season"}>
              <Boundary key={page.id}>
                <Page />
              </Boundary>
              <PageJump page={hash} />
            </ProfileHost>
          </SiteContext.Provider>
        )}
      </main>
      <footer>
        Lap timing, tyres, race control and results from OpenF1; the calendar via FastF1; starting grids from Jolpica (Ergast).
        Weather data by <a href="https://open-meteo.com/">Open-Meteo.com</a>.
        Stewards' decisions from <a href="https://www.f1penalties.com/data">f1penalties.com</a>; power-unit elements from the FIA's documents.
        History since 1950 from <a href="https://github.com/jolpica/jolpica-f1">Jolpica F1</a>'s database
        (<a href="https://creativecommons.org/licenses/by-nc-sa/4.0/">CC BY-NC-SA 4.0</a>).
        Team logos and driver photos from <a href="https://commons.wikimedia.org/">Wikimedia Commons</a> contributors
        (CC BY, CC BY-SA, CC0 and public domain), logos trademarks of their owners; flags from{" "}
        <a href="https://github.com/lipis/flag-icons">flag-icons</a> (MIT).
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
  // The weekend's next session to start: qualifying, the sprint, or the Grand Prix.
  const nextCode = next && weekendSessions(next).find((c) => { const t = sessionStart(next, c); return t && Date.parse(t) > Date.now(); });
  const nextStart = next && nextCode ? sessionStart(next, nextCode) : null;
  const nextLabel = nextCode ? SESSION_LABEL[nextCode] : "Race";
  return (
    <div className="status-row">
      <div className="status">
        <span><strong>{meta.season} Season</strong> · Round {done.length} of {meta.calendar.length}</span>
        <span>
          {last && <>Results up to {shortEvent(last.event)} (Round {last.round}) · </>}Data Last Updated {dayYear(meta.generated)}
        </span>
        {next && nextStart && (
          <span>Next: <strong>{shortEvent(next.event)}</strong> {nextLabel === "Grand Prix" ? "race" : nextLabel.toLowerCase()}, {when(nextStart)}</span>
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
