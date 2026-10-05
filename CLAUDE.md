# F1 Stratbox

Streamlit prototype for an F1 team's strategy workflow: post-race review, a lap-by-lap
race tracker, and a future-race strategy sandbox. Built on FastF1 lap timing. Plus a public
website (https://amparaj.github.io/f1-stratbox/) that a GitHub Action rebuilds after every
race: see "Website" below and README.md.

## Run

```
.venv\Scripts\streamlit run app.py
```

- Windows, Python 3.14, venv in `.venv/` (deps in `requirements.txt`). If the folder is
  moved, rebuild the venv: its launcher `.exe`s hard-code the old path.
- First load of a session downloads from the F1 timing API (30–90 s). After that it
  comes from `.fastf1/` (FastF1 disk cache, git-ignored) plus Streamlit's memory cache.
- `.venv\Scripts\python scripts\prefetch_season.py [years...]` pre-downloads every
  completed race + sprint of a season (default: current) into `.fastf1/`. Re-run after
  each race weekend.
- Sessions verified to work: 2024 British GP (rain), 2024 Bahrain GP (dry),
  2024 São Paulo GP (SC + VSC + red flag + 3 DNFs). Use these when testing.

## Layout

```
app.py                     page config, FastF1 init, explicit st.navigation + home page
config.py                  ALL tunable constants and model assumptions
modules/data_engine.py     FastF1 loading, cleaning, timeline; shared sidebar session picker
modules/analytics.py       deg fits, cliff detector, undercut maths, post-mortem text
modules/simulator.py       deterministic + Monte Carlo race simulator
pages/1_Race_Recap.py      replay, tyre-strategy chart, post-mortem, deg plots, export
pages/2_Live_Race_Tracker.py  battle map, pit-rejoin forecast, undercut threats
pages/3_Future_Sandbox.py  scenario controls, strategy comparison, Monte Carlo
modules/weather.py         Open-Meteo forecast/ensemble/climate/nowcast, rain scenarios, track temp
modules/forecast.py        website forecasts: driver form, race/title Monte Carlo, strategy search
modules/site_export.py     website data files (web/public/data/*.json)
modules/history.py         website History files (every season since 1950, from Jolpica's dump)
scripts/export_site.py     runs site_export; scripts/needs_update.py: the Action's "anything new?"
web/                       the website (React + TypeScript + Vite, theme copied from xpfpl)
.github/workflows/site.yml scheduled export + build + push to gh-pages
```

- `app.py` uses `st.navigation`, so `pages/` is **not** auto-discovered. A new page must
  be registered in the `pages` list in `app.py` (and linked from `home()` if wanted).
  `st.set_page_config` is called only in `app.py`.
- Each page starts with a `sys.path.insert(...)` shim so it can `import config` when run
  directly (e.g. by `AppTest`). Keep it.
- The shared session picker (`render_session_selector`) lives in `data_engine.py` to keep
  to the original spec's file tree. It stores the chosen `(year, event_name, "R"|"S")` under
  the non-widget key `st.session_state["active_session"]`, so the choice survives page
  switches (Streamlit deletes widget-keyed state on navigation).

## Data pipeline (`modules/data_engine.py`)

- `load_session` (`@st.cache_resource`) loads in tiers: **full** (laps + weather +
  messages, optionally telemetry), then **timing-only** (`session.laps`) if that fails.
  If both fail it raises `DataUnavailableError`. Pages call `load_active_session`, which
  shows `st.error` / `st.warning` and never crashes.
- `get_all_laps`: every `timedelta64` column becomes float seconds (same column names,
  so `LapTime` is seconds). It adds `PitIn`, `PitOut`, `GapToAhead`, `CleanAir` and
  `Rainfall`.
  - `GapToAhead` is computed from lap timing (difference in lap-end `Time` among cars
    completing the same lap; the lap leader gets `inf`), **not** telemetry. This is cheaper
    and needs no car data.
  - `CleanAir` = `GapToAhead > 1.5 s`.
  - `Rainfall` = the weather feed reported rain at the lap's start or end. It is all
    `False` on the timing-only tier.
- `get_cleaned_laps` removes:
  - lap 1, pit-in and pit-out laps
  - laps whose `TrackStatus` contains 4/5/6/7 (SC/red/VSC)
  - **slick laps run in rain**
  - deleted laps and laps missing a time or tyre age
  - outliers above 1.07 × the median for that driver and compound

  These are the laps used for degradation modelling.
- `get_lap_weather`: the track sensors (air/track temp, humidity, wind, rain) at the moment the
  leader finished each lap, plus the lap's `UTC` time (OpenF1 `t0`; FastF1 has no `t0_date`
  without car data, so lap 1's start is pinned to the scheduled start).
- `get_race_timeline` gives per-lap `GapToLeader` and `RunningPosition`. **DNF handling:**
  a driver's rows end at their last valid lap. Nothing is forward-filled or padded.
- All DataFrame functions are `@st.cache_data` keyed on `(year, event, session_type)`.

## Modelling conventions (`modules/analytics.py`)

- Fuel correction: `FuelCorrectedLapTime = LapTime + 0.033 * (LapNumber - 1)`, i.e. a
  full-tank equivalent. The slope against `TyreLife` is then pure tyre degradation.
- Degradation model per driver and compound: `np.polyfit` line, giving
  `base_pace` (intercept at tyre age 0) + `deg_rate` (s/lap). It uses clean-air laps if
  there are at least 5 of them, otherwise all cleaned laps (recorded in `sample`).
  `field_compound_model` is the median across drivers, with negative deg floored at 0. It
  is the fallback when a driver has no fit, and it calibrates the Sandbox.
- **Cliff detector** (`detect_tyre_cliff`): a two-segment piecewise regression over each
  stint. These guards were added after false positives on real data. Don't loosen them
  without re-checking Silverstone 2024:
  - the slope after the break must itself be at least 0.15 s/lap (a drying track's
    negative slope flattening out is not a cliff)
  - the slope must rise by at least 0.15 s/lap, and the split must cut the residual
    sum of squares by at least 35 %
  - at least 1.0 s must be lost against a non-negative pre-cliff trend
  - in `build_postmortem`, a break within ±3 laps of rainfall is labelled
    `cause="weather"`, not a tyre cliff
- Undercut (`assess_undercut`): car B (behind) pits onto a fresh compound. A (ahead) is
  "Vulnerable to Undercut" if the gap A→B is smaller than B's lap-time gain, summed over
  the laps before A can respond. Fuel and pit losses cancel. On the Live Race Tracker the
  models are fitted **only on laps up to the selected lap** (no future leakage).
- "Best tyre management" headlines only rank fits with at least 8 laps and deg ≥ 0.

## Simulator (`modules/simulator.py`)

- Both engines share one lap loop, `_simulate_core`, so they can't drift apart. Lap time:
  `base + deg_eff*age + post-cliff extra - fuel*(lap-1)`, plus a slick-in-rain penalty
  and pit loss on the in-lap.
- `deg_eff = deg × (1 - upgrade%/100) × temp_factor`. A +2 % upgrade means × 0.98, as in
  the spec. Hotter track: more deg, and the cliff comes earlier (`cliff_age / temp_factor`).
- Rain: a weather modifier is `{"rain_lap", "dry_lap", "intensity"}` (`dry_lap` None = wet to the
  flag). Wet tyres' base is the fastest dry base; the lap adds `RAIN_PROFILES[intensity]`
  (`slick_penalty` on slicks, `pace_offset[tyre]` on wets) or, dry, `WET_TYRE_ON_DRY` with deg
  × `WET_TYRE_DRY_DEG_FACTOR`. On a wet lap a car on the wrong tyre pits for the profile's tyre
  (remaining planned stops lapse) unless the wet laps left × the penalty < the extra stops; once
  dry, a car on wets pits for `slick_for(laps left)` unless staying out costs less than a stop.
  If it stays out in the rain, its planned stops still happen. Teams know when rain stops.
- `_simulate_core(start_age=, race_start=False)` runs the rest of a race from the current lap
  (Live Race Tracker rain call): no free wet-tyre change on lap 1.
- `run_sandbox_simulation(base_pace, degradation_rate, total_laps, upgrade_modifier,
  weather_modifier, ...)` keeps the spec's signature. `base_pace` and `degradation_rate`
  accept either a float (expanded across compounds using the presets) or a dict per
  compound.
- `run_monte_carlo` adds lap noise and random Safety Cars:
  - by default a 45 % chance per race, lasting 3–5 laps
  - pit loss under SC is × 0.55
  - a stop due within 8 laps is brought forward under SC

  It uses **common random numbers**: every strategy sees the same noise and SC draw in
  each simulated race. About 0.13 s for 500 races × 4 strategies. `weather_scenarios` (list of
  modifiers or None = dry) draws one per race, shared by all strategies.
- Strategy strings: `parse_strategy("S-15, H")`, letters S/M/H/I/W. The last stint may
  omit its length. `normalise_strategy` makes stint lengths sum to the race distance.

## UI conventions

- Plotly charts use `theme="streamlit"` and `width="stretch"`. **Don't use
  `use_container_width`**: it is deprecated in Streamlit 1.65.
- Colour follows the entity, never its rank: team colours come from `session.results`;
  sandbox strategies have fixed slots from `config.STRATEGY_COLORS`. Teammates share a
  colour, so the second driver gets a dotted line.
- Status colours come from `config.STATUS_COLORS` and always appear with an icon and a
  label. Compound colours come from `config.COMPOUND_COLORS`.
- Avoid uncommon emoji in chart or metric text. Some don't render on Windows (🛞 showed
  as a box), and 🟢 inside `st.metric` renders huge.

## Testing

No test suite is checked in. Use Streamlit's `AppTest` headlessly; it catches runtime
errors on every page:

```python
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("pages/1_Race_Recap.py", default_timeout=600)
at.session_state["active_session"] = (2024, "British Grand Prix", "R")
at.run()
assert not at.exception and not at.error
```

For visual checks, run the app and screenshot it with Playwright and the installed
Chrome (`C:\Program Files\Google\Chrome\Application\chrome.exe`). AppTest can't see
chart layout problems.

Windows notes: set `PYTHONIOENCODING=utf-8` when printing report text (it contains →
and emoji). This is a Windows PowerShell 5.1 / Git Bash environment.

## Website

- **When a session counts as finished** (`config.session_finished`, constants beside
  `DATA_SOURCE`): its chequered flag is 5 min old, or 6 h have passed since the start with no
  flag. Nothing looks before start + 75 min (race) / 25 min (sprint). Before the flag, loading
  raises `data_engine.SessionRunningError` (`openf1.SessionRunning`): OpenF1 fetches race control
  first on race day and nothing else until the flag; the dashboard shows "hasn't finished yet".
  Never go back to a fixed timer: 2026 R16 (KL) took 3 h 20 min. `needs_update.py` keeps a copy of
  the constants and asks OpenF1 for the flag itself. A FastF1 session < `RECENT_DAYS` (4) old is
  loaded past FastF1's disk cache (it would pickle a partial or pre-penalty load for good), full
  tier only; `get_session_info` returns `provisional`, and the dashboard shows a banner with
  "Check for updates" (clears the memory caches).
- `site_export.export_season` loads every session that could have finished, skips running ones, writes
  `meta.json`, `races/rNN-R|S.json` and `forecasts/rNN.json`. Tables are column-wise
  (`{"col": [...]}`); `web/src/data.ts` `rows()` turns them back into rows.
- The data-engine functions carry `@st.cache_data`; they work outside Streamlit (memory
  cache). `export_site.py` calls `logging.disable(WARNING)` because FastF1 and Streamlit log
  hundreds of lines; failed sessions are printed instead.
- **Data source:** the export uses OpenF1 (`modules/openf1.py`, `--source openf1` default),
  locally and on GitHub. F1's live-timing server (FastF1) doesn't answer GitHub's runners:
  FastF1 falls back to its mirror, which has no current season. `openf1.Session` mimics the
  FastF1 session (laps with FastF1 column names, results, weather_data, event, total_laps), so
  data_engine is unchanged; `config.DATA_SOURCE` picks the loader (dashboard default
  "fastf1"). TrackStatus comes from race-control messages, deleted laps from "... DELETED ...
  LAP n", grid from Jolpica. Sessions are matched to FastF1's calendar by start time (OpenF1
  still lists cancelled rounds). Requests are cached in `.openf1/` and paced to ~28/min.
  **Archive:** a final session (start + 4 days, result and grid in; grid optional after 14
  days) is written to `archive/openf1/<year>/rNN-R|S.json.gz` (raw endpoint JSON + Jolpica
  grid, deterministic gzip) and read from there for good; the Action commits new files to
  main. Don't hand-edit archive files; delete one to force a re-fetch.
  OpenF1's result is the official classification; FastF1's timing order can differ (2026
  KL: LEC P4 officially, P17 in FastF1's timing order).
- **Provisional results:** until the classification and grid are both in, `_results` derives
  points from timing order and classification from laps ≥ 90 % of the winner's, and the
  session has `complete: false`; `needs_update.py` rebuilds while a session < 4 days old is
  provisional or anything is pending.
- **Forecasts** (`modules/forecast.py`, constants in `config.py` "Season forecasts"):
  - race pace = median fuel-corrected clean lap / field median on that compound, in %
  - form = decayed mean of last 6 race paces; race MC adds `FORECAST_RACE_SD` per race;
    title odds also add one `FORM_DRIFT_SD` shift per simulated season. Both were checked
    against replays of 2026 rounds 2–16 (see the config comments)
  - forecasts for finished rounds are recomputed from earlier races on every export, not
    saved, so they change if the model changes
  - strategy: per-compound deg fits and intercepts from one race are too noisy (late-run
    Hards fit faster than Softs; a compound few drivers used fits 0 deg). So a circuit gets
    one *tyre severity* (field deg / preset deg, weighted by drivers) from last season's race
    there, times this season's vs last season's severity at shared circuits. Compound split,
    pace gaps and cliffs come from `COMPOUND_PRESETS`. Order of identical stints doesn't
    change a no-SC race time, so plans are deduped by compound set.
- **History** (`modules/history.py`, `web/src/pages/History.tsx`, `#history[/1988[/16[/S]]]`,
  `#history/driver/<ref>`, `#history/circuit/<ref>`): every season up to last year from Jolpica's free
  CSV dump (delayed 14 days, CC BY-NC-SA 4.0, non-commercial: keep the credit in the footer and About).
  **Archive:** each season is `archive/jolpica/<year>.json.gz` (that season's rows of every dump table
  as CSV text, every column, deterministic gzip), written once a dump from after 15 Jan of the next
  year has it, then read from there for good; the Action commits new files with the OpenF1 ones. The
  dump (cached in `.jolpica/`) is fetched only when a season up to last year isn't archived. Driver,
  team and circuit rows repeat in every season's file; loading checks their ids agree across files.
  Don't hand-edit archive files; delete one to re-fetch it. Written to `web/public/data/history/`
  by `export_site.py` (`--history-only`, `--no-history`); a failure skips it.
  Coverage: results/grids/standings from 1950, lap times 1996+, fastest laps 2004+, pit stops 2011+.
  - Standings come from the dump's championship tables, which already apply dropped-score rules.
  - Pole = started from grid 1 (matches the record books: 104 HAM, 68 MSC, 65 SEN); qualifying P1
    only where no grid. From 2022 the dump's Q1/Q2/Q3 positions are per session, not overall.
  - Driver codes: official abbreviation, else surname letters, de-duplicated within a season only, so
    all-time tables show names. Old teams' colours: `config.HISTORY_TEAM_COLORS`.
- Circuits are matched across seasons by `circuit_key(location)` (via the pit-loss table),
  never by event name: the 2026 "Bahrain Grand Prix" was in Kuala Lumpur.
- Pages must render at phone width (`usePhone`, cards via `Table`). Screenshot both widths.
- About has an Overview and a Technical Documentation (`web/src/pages/Docs.tsx`, `#about/docs[/section]`,
  KaTeX lazy-loaded). Its constants and formulas are copied from `config.py` and the modules: update
  them when the model changes. Table columns that are an order (Pos, Round, Grid) set `rank: true`.
- Publishing needs GitHub Pages set to "Deploy from a branch: gh-pages".

## Weather (`modules/weather.py`)

- Free only: track sensors from the session data; Open-Meteo (no key, non-commercial, credit
  "Weather data by Open-Meteo.com" on every page that shows it). OpenF1's *live* feed is paid,
  so nothing reads weather during a running session; the Live Tracker's rain call takes the
  forecast or a hand-set outlook. Requests cached in `.openmeteo/` (past: for good; forecasts
  `FORECAST_FRESH_HOURS`); the Action caches the folder.
- `outlook()` → rain scenarios from the ensemble (ICON ≤7 days, GFS ≤16) or, further out, the
  climate (ERA5, ±3 days × 10 years). Hourly rain is interpolated between hour midpoints onto laps
  (race = `RACE_MINUTES`), wet ≥ `RAIN_WET_MM_H`, heavy ≥ `RAIN_HEAVY_MM_H`, first spell only,
  dry `TRACK_DRYING_LAPS` after it.
- Track temp = air + 5.3 + 0.020 × sunshine (fitted on 34 dry 2025–26 races, RMSE 3.5 °C). The
  temp delta for the simulator is **like-for-like**: the same estimate from the same source for the
  reference race (archived forecast, or its climate); ERA5 alone is ~13 °C cold at Mexico City.
  Displayed absolute = reference race's sensors + delta.
- Live Tracker: archived 15-minute forecast at the lap's UTC (a model, not radar: it missed the
  2024 Silverstone rain). If the sensor reads rain, the next laps are wet regardless. The rain
  call compares stay out / pit for wets / pit for slicks, each with its best continuation (≤1
  more stop onto a slick), so tyre age doesn't masquerade as a rain call.
- `needs_update.py` rebuilds when the next race is < 4 days away and the export is 6 h old.
- **Live rain radar** (`web/src/components/Radar.tsx`, Next Race page): RainViewer's free API
  keeps only the last 2 h (10-min frames, no nowcast, max zoom 7), so it's **live only**: shown
  from 30 min before a race/sprint start to 3 h (race) / 1.5 h (sprint) after, fetched in the
  browser, never archived. Basemap: OpenStreetMap tiles (CARTO's now need a key), darkened by a
  CSS filter in dark mode. Circuit `lat`/`lon` are in meta.json's calendar. During a session the
  page opens on that round even if an export moved `next_round` on. `?radar` in the URL forces it
  on for layout checks.

## Known limitations / next steps

- **Live Race Tracker replays a finished session** lap by lap (optionally auto-advancing
  via `st.fragment(run_every=...)`). FastF1's live-timing client is not wired in yet.
- **Pit losses** in `config.TRACK_PIT_LOSS`: Silverstone, Monaco, Spa and Monza come from
  the original spec. The rest are approximate public figures. Replace them with team
  data. Lookup goes through `config.get_pit_loss` (FastF1 location or event name, with
  accent-insensitive aliases).
- **Compound presets, rain profiles and Monte Carlo defaults** in `config.py` are
  prototype assumptions, not measured values.
- Gap to car ahead is the classification interval on the same lap. Being held up by a
  lapped car on track isn't captured.
- The post-mortem is rule-based. The Export tab produces an LLM-ready prompt but calls
  no LLM.
- The Sandbox's "Calibrate from loaded session" uses fitted per-compound intercepts and deg,
  which suffer the track-evolution skew described under Website; the website's strategy
  forecast avoids it with the tyre-severity approach.
