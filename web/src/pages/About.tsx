import { Suspense, lazy } from "react";
import { Flow } from "../components/Flow";
import { Loading, Segmented } from "../components/ui";
import { dayYear } from "../format";
import { useHash, useSite } from "../site";
import { PHOTOS } from "../profiles";
import { logoUrl, TEAMS } from "../teams";

// The maths needs KaTeX, so the technical documentation loads as its own chunk.
const Docs = lazy(() => import("./Docs"));

/** About: the plain-language overview (#about) or the technical documentation (#about/docs). */
export default function About() {
  const view = useHash().split("/")[1] === "docs" ? "docs" : "overview";
  return (
    <div className="about">
      <h2>About F1 Stratbox</h2>
      <div className="about-switch">
        <Segmented label="About" value={view} onChange={(v) => { window.location.hash = v === "docs" ? "about/docs" : "about"; }}
                   options={[{ value: "overview", label: "Overview" }, { value: "docs", label: "Technical Documentation" }]} />
      </div>
      {view === "docs" ? <Suspense fallback={<Loading />}><Docs /></Suspense> : <Overview />}
    </div>
  );
}

function Overview() {
  const { meta } = useSite();
  return (
    <>
      <p className="lede">
        A personal F1 strategy notebook: every session of the {meta.season} season reviewed (Qualifying, Sprint
        Qualifying, Sprints and Grands Prix), and a forecast for each one still to come. It's the public, read-only side of a Streamlit app that runs on
        my computer (post-race review, a lap-by-lap race tracker and a strategy sandbox).
      </p>

      <Flow label="How F1 Stratbox works, from timing data to the pages" stages={[
        { label: "Data", join: "every lap of every car", nodes: [
          { title: "Lap timing", text: "lap times, tyres, pit stops", kind: "source", section: "data" },
          { title: "Race control", text: "Safety Cars, VSC, red flags, deleted laps", kind: "source", section: "data" },
          { title: "Results & grid", text: "classification, points, starting grid", kind: "source", section: "data" },
          { title: "Penalties & news", text: "stewards' decisions, FIA power-unit documents, F1 news", kind: "source", section: "penalties" },
        ] },
        { label: "Analyse", join: "per race", nodes: [
          { title: "Clean laps", text: "neutralised, pit, wet and outlier laps out", kind: "model", section: "cleaning" },
          { title: "Tyre wear", text: "degradation lines and cliffs", kind: "model", section: "degradation" },
          { title: "Race pace", text: "each driver against the field", kind: "model", section: "pace" },
          { title: "Qualifying", text: "cut-offs, sectors, track evolution", kind: "model", section: "qualifying" },
          { title: "Penalties", text: "grid drops announced, power-unit penalties likely", kind: "model", section: "penalties" },
        ] },
        { label: "Forecast", join: "", nodes: [
          { title: "Session odds", text: "10,000 runs of each Qualifying, Sprint and Grand Prix, grid penalties in", kind: "model", section: "race" },
          { title: "Title odds", text: "10,000 simulated seasons, penalties race by race", kind: "model", section: "title" },
          { title: "Tyre strategy", text: "every 1- and 2-stop plan", kind: "model", section: "strategy" },
        ] },
        { label: "Pages", nodes: [
          { title: "Current Season", kind: "out" },
          { title: "Race Results & Analysis", kind: "out" },
          { title: "Next Race Forecast", kind: "out" },
        ] },
      ]} />
      <p className="note">Each box links to its part of the <a href="#about/docs">Technical Documentation</a>.</p>

      <h3>How it stays up to date</h3>
      <p>
        Results usually appear within half an hour of the chequered flag. They're marked provisional until
        the official classification is out. Last update: {dayYear(meta.generated)}.
      </p>

      <h3>The race analysis</h3>
      <ul>
        <li><b>Clean laps.</b> Lap 1, pit in and out laps, laps under Safety Car, VSC or red flag, deleted laps,
          slick laps in the rain and outliers (more than 7% over a driver's median on that compound) are dropped
          before anything is modelled.</li>
        <li><b>Fuel correction.</b> Each lap gets 0.033 s per lap already run added back, a full-tank equivalent,
          so what's left of the slope against tyre age is tyre wear.</li>
        <li><b>Degradation.</b> A straight line through each driver's fuel-corrected laps against tyre age, per
          compound, using laps in clean air (more than 1.5 s behind the car ahead) when there are at least five.</li>
        <li><b>Tyre cliffs.</b> A two-piece line through each stint: a cliff is called only when the slope after the
          break is at least 0.15 s/lap steeper, the split explains at least 35% more of the variation, and at least
          1 s is lost against the earlier trend. A break within three laps of rain is put down to the weather.</li>
        <li><b>Race pace.</b> A driver's median clean lap against the field median on the same compound, in percent
          (negative is faster).</li>
        <li><b>Qualifying.</b> Q1, Q2 and Q3 split by the flag that ends each; how close every driver was to each
          cut-off; best sectors and the ideal lap; how much quicker the track got between and within segments; and
          a qualifying pace that compares drivers knocked out early with those in Q3.</li>
        <li><b>Sprints</b> are analysed like Grands Prix and labelled as such everywhere; they score 8 points down to 1.</li>
      </ul>

      <h3>The forecasts</h3>
      <ul>
        <li><b>Form.</b> Two weighted averages over the last six rounds, each earlier round counting 0.75 times the
          next: race form (Grands Prix and Sprints) and qualifying form (Qualifying and Sprint Qualifying).</li>
        <li><b>Session odds.</b> Every session of a weekend is played 10,000 times. Qualifying draws each driver's
          lap around their qualifying form: pole, front row, Q3 and knock-out chances. A race draws pace around a
          blend of race and qualifying form, lightly adjusted for how their team went at the circuit last season,
          with retirements at each driver's (shrunk) DNF rate. Once the race's qualifying is in, that session's pace
          and the grid take over: a grid place is worth ten times as much in a Sprint as in a Grand Prix. Rounds
          further ahead are a little less certain. All of it was fitted by replaying 2025 and 2026.</li>
        <li><b>Penalties.</b> A grid penalty already announced (a power-unit change past the allocation, or a drop
          carried over from an incident or handed out in qualifying) moves the driver back before the race is played; a
          place lost that way costs more than an ordinary grid slot, because the car is faster than where it starts. A
          power-unit penalty that isn't announced yet comes in as a chance at every Grand Prix left: from how many
          elements each driver has used against the allocation, the circuit (teams take them where overtaking is easy),
          what the team has said and what two or more news sites report. Time penalties in a finished race are already in
          its result and the points; ones still to come in a race ahead aren't forecast.</li>
        <li><b>Qualifying strategy.</b> How much the track came to the drivers at this circuit last season, how close
          the cut-offs were, the chance of rain, and who is on the edge of Q1 and Q3.</li>
        <li><b>Title odds.</b> Every remaining Sprint and Grand Prix simulated the same way, 10,000 times, on top of
          the points already scored.</li>
        <li><b>Strategy.</b> Each circuit gets one figure for how hard it is on tyres: last season's measured wear there,
          against a set of preset wear rates, averaged over the compounds enough drivers used, and scaled by how this
          season's tyres compare with last season's at the circuits both seasons have visited. (One race's fit for a
          single compound is too noisy to plan on: a tyre a few drivers ran late can fit with no wear at all.) The
          split between compounds, their pace on fresh tyres (Medium 0.45 s and Hard 0.9 s slower than Soft) and the
          age each falls off a cliff are fixed assumptions. Every 1- and 2-stop plan (for a Sprint, about 100 km, also
          running with no stop) is run lap by lap through the simulator; the best ones then go through a Monte Carlo with lap-time noise and a 45% chance of a Safety Car
          (3–5 laps, which makes a stop cheaper) and the weather: each simulated race draws one version of the
          rain from Open-Meteo's ensemble forecast (or, further ahead, the race's dates in the last ten years), and
          the expected track temperature moves the wear.</li>
        <li><b>Checked against results.</b> Every finished session shows the forecast made from the sessions before
          it next to the result, under Forecast vs Result. Those are re-made by the current model, not saved copies.</li>
      </ul>

      <h3>Data</h3>
      <p>
        Lap timing, tyre stints, pit stops, race control (Safety Cars, deleted laps), weather and results come
        from the <a href="https://openf1.org">OpenF1</a> API (qualifying too: segment times, sectors, speed traps); the calendar through the{" "}
        <a href="https://docs.fastf1.dev/">FastF1</a> library; starting grids from the Jolpica (Ergast) API.
        Weather forecasts and climate for the races ahead come from{" "}
        <a href="https://open-meteo.com/">Open-Meteo</a> (CC BY 4.0). While a session is on, Next Race
        shows a live rain radar from <a href="https://www.rainviewer.com/">RainViewer</a> on an{" "}
        <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> map; RainViewer keeps only the last
        two hours, so finished races have none.
        Stewards' decisions (penalties, reprimands, power-unit and parc fermé grid drops) come from{" "}
        <a href="https://www.f1penalties.com/data">f1penalties.com</a>, and each driver's power-unit elements from the
        FIA Technical Delegate's documents at every event; together they drive the{" "}
        <a href="#about/docs/penalties">penalty model</a>. F1 news headlines (The Race, Crash.net, Autosport,
        Motorsport.com, Formula1.com, BBC Sport, Sky Sports, GPFans) are read from their RSS feeds for reported
        power-unit penalties and upgrades; the site shows the headline and links to the article.
        The <a href="#history">History</a> pages (every season since 1950) come from{" "}
        <a href="https://github.com/jolpica/jolpica-f1">Jolpica</a>'s database, licensed{" "}
        <a href="https://creativecommons.org/licenses/by-nc-sa/4.0/">CC BY-NC-SA 4.0</a>: results, grids and
        standings for every season, lap times from 1996, fastest laps from 2004 and pit stops from 2011.
        Pit-lane losses are approximate public figures. The code is at{" "}
        <a href="https://github.com/amparaj/f1-stratbox">github.com/amparaj/f1-stratbox</a>.
      </p>

      <h3>Team logos</h3>
      <p>
        Team logos are freely licensed files from <a href="https://commons.wikimedia.org/">Wikimedia Commons</a> (public
        domain, CC0 or Creative Commons), apart from those marked "supplied by hand" below (Ferrari, Racing Bulls and
        some past teams and eras); all are shown only to identify the teams, and the logos are trademarks of their
        owners. A team with no logo for that era (most teams before the 1980s) gets a badge in its colour with a short
        code instead.
      </p>
      <details className="logo-credits">
        <summary>Logo credits ({LOGO_CREDITS.length} files)</summary>
        <ul>
          {LOGO_CREDITS.map(({ team, logo }) => (
            <li key={logo.file}>
              <img src={logoUrl(logo)} alt="" /> {team}: {logo.page ? <a href={logo.page}>{logo.source ?? logo.file}</a> : logo.file}, {logo.author}, {logo.licence}
            </li>
          ))}
        </ul>
      </details>

      <h3>Driver photos and flags</h3>
      <p>
        Click any driver's name or code, or a circuit's name, for their profile or circuit guide. Driver photos are
        the lead images of their Wikipedia articles, freely licensed files from{" "}
        <a href="https://commons.wikimedia.org/">Wikimedia Commons</a> (credited below); a driver without one shows
        their car number. Flags are from <a href="https://github.com/lipis/flag-icons">flag-icons</a> (MIT licence).
        Track maps come from MultiViewer's circuit data, as FastF1 reads it.
      </p>
      <details className="logo-credits">
        <summary>Photo credits ({PHOTO_CREDITS.length} files)</summary>
        <ul>
          {PHOTO_CREDITS.map(([ref, p]) => (
            <li key={ref}>
              <a href={p.page}>{p.source.replace(/_/g, " ")}</a>, {p.author}, {p.licence}
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}

const PHOTO_CREDITS = Object.entries(PHOTOS).sort(([a], [b]) => a.localeCompare(b));

const LOGO_CREDITS = TEAMS.flatMap((t) => t.logos.filter((l) => l.page || l.archive).map((logo) => ({ team: t.name, logo })))
  .filter((c, i, all) => all.findIndex((o) => o.logo.file === c.logo.file) === i); // Lotus F1 shares Team Lotus' file
