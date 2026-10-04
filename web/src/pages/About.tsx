import { dayYear } from "../format";
import { useSite } from "../site";

export default function About() {
  const { meta } = useSite();
  return (
    <div className="about">
      <h2>About F1 Stratbox</h2>
      <p className="lede">
        A personal F1 strategy notebook: every race of the {meta.season} season reviewed lap by lap, and a
        forecast for each race still to come. It's the public, read-only side of a Streamlit app that runs on
        my computer (post-race review, a lap-by-lap race tracker and a strategy sandbox).
      </p>

      <h3>How it stays up to date</h3>
      <p>
        A scheduled GitHub Action checks every hour over race weekends (and once a day otherwise). About
        three hours after a race or sprint starts it downloads the timing data, rebuilds every page and
        publishes the site, usually within an hour or two of the chequered flag. If the classification
        isn't out yet the race is marked provisional, with points worked out from the timing order; the
        starting grid usually follows a few hours to a day later, and later runs fill both in (and pick up
        any penalties). Last update: {dayYear(meta.generated)}.
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
      </ul>

      <h3>The forecasts</h3>
      <ul>
        <li><b>Form.</b> A weighted average of each driver's race pace over their last six races, each earlier race
          counting 0.75 times the next.</li>
        <li><b>Race odds.</b> The race is played 10,000 times: every driver's pace is drawn around their form (a
          spread of 0.45% of lap time, roughly how much a driver's pace moves race to race this season) and they
          retire at their own DNF rate, shrunk towards the field's. The order of the finishers gives the win,
          podium and points chances and the expected points. It doesn't know the grid, the circuit or upgrades,
          so treat it as "who's been quickest lately".</li>
        <li><b>Title odds.</b> Every remaining race and sprint simulated the same way, 10,000 times, on top of the
          points already scored.</li>
        <li><b>Strategy.</b> Each circuit gets one figure for how hard it is on tyres: last season's measured wear there,
          against a set of preset wear rates, averaged over the compounds enough drivers used, and scaled by how this
          season's tyres compare with last season's at the circuits both seasons have visited. (One race's fit for a
          single compound is too noisy to plan on: a tyre a few drivers ran late can fit with no wear at all.) The
          split between compounds, their pace on fresh tyres (Medium 0.45 s and Hard 0.9 s slower than Soft) and the
          age each falls off a cliff are fixed assumptions. Every 1- and 2-stop plan is run lap by lap through the
          simulator; the best ones then go through a Monte Carlo with lap-time noise and a 45% chance of a Safety Car
          (3–5 laps, which makes a stop cheaper).</li>
        <li><b>Checked against results.</b> Every finished race shows the forecast made from the races before it next
          to the result, under Forecast vs Result. Those are re-made by the current model, not saved copies.</li>
      </ul>

      <h3>Data</h3>
      <p>
        Lap timing, tyre stints, pit stops, race control (Safety Cars, deleted laps), weather and results come
        from the <a href="https://openf1.org">OpenF1</a> API; the calendar through the{" "}
        <a href="https://docs.fastf1.dev/">FastF1</a> library; starting grids from the Jolpica (Ergast) API.
        Pit-lane losses are approximate public figures. The code is at{" "}
        <a href="https://github.com/amparaj/f1-stratbox">github.com/amparaj/f1-stratbox</a>.
      </p>
    </div>
  );
}
