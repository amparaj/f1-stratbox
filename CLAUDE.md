# F1 Stratbox

Streamlit prototype for an F1 team's strategy workflow: post-race review (with a track replay),
qualifying analysis, lap telemetry head-to-head, a lap-by-lap race tracker, and a future-race
strategy sandbox. Built on FastF1 lap timing. Plus a public
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
  completed session (Q, SQ, Sprint, Grand Prix) of a season (default: current) into `.fastf1/`.
  Re-run after each race weekend.
- Sessions verified to work: 2024 British GP (rain), 2024 Bahrain GP (dry),
  2024 São Paulo GP (SC + VSC + red flag + 3 DNFs). Use these when testing.

## Layout

```
app.py                     page config, FastF1 init, explicit st.navigation + home page
config.py                  ALL tunable constants and model assumptions
modules/data_engine.py     FastF1 loading, cleaning, timeline; shared sidebar session picker
modules/analytics.py       deg fits, cliff detector, undercut maths, post-mortem text
modules/simulator.py       deterministic + Monte Carlo race simulator
pages/1_Race_Recap.py      track replay, tyre-strategy chart, post-mortem, deg plots, export
pages/2_Live_Race_Tracker.py  battle map, pit-rejoin forecast, undercut threats
pages/3_Future_Sandbox.py  scenario controls, strategy comparison, Monte Carlo (Grand Prix or Sprint)
pages/4_Qualifying.py      Q/SQ result, cut-off margins, sectors & ideal lap, evolution, run plan
pages/5_Telemetry.py       two laps head to head: traces, delta, corner zones, faster-where map
modules/telemetry.py       car telemetry: replay frames, lap traces, lap comparison
modules/replay_player.html the track replay (canvas + JS), payload swapped in by telemetry.replay_html
modules/weather.py         Open-Meteo forecast/ensemble/climate/nowcast, rain scenarios, track temp
modules/forecast.py        website forecasts: driver form, race/title Monte Carlo, strategy search
modules/site_export.py     website data files (web/public/data/*.json)
modules/site_telemetry.py  website lap telemetry (telemetry/rNN-<code>.json) from OpenF1 car data
modules/history.py         website History files (every season since 1950, from Jolpica's dump)
modules/penalties.py       stewards' decisions (f1penalties.com), PU usage (FIA PDFs), likely-penalty model
modules/practice.py        free practice: one-lap pace, long runs (fuel corrected, driver + compound effects)
modules/upgrades.py        car upgrades from the FIA's Car Presentation Submissions (PDF per event)
modules/news.py            F1 news RSS (The Race, Crash.net, Autosport, ...): topics, reported PU penalty plans
modules/teams.py           team badges (logo or colour + code) for dashboard tables, from web/src/teamLogos.json
modules/profiles.py        website driver/circuit pop-ups (profiles.json): line-up, season results, circuits, race analysis
pages/6_Practice.py        FP1-3: timesheet, one-lap vs long-run pace map, long runs, the weekend combined
scripts/export_site.py     runs site_export; scripts/needs_update.py: the Action's "anything new?"
scripts/calibrate_forecast.py  fits config's "Session forecasts" constants by replaying 2025-26
scripts/calibrate_penalties.py fits config's "Power-unit penalties" constants on every round since 2022
scripts/prefetch_practice.py   downloads a season's practice sessions through OpenF1 (archived like the rest)
scripts/fetch_team_logos.py    downloads the team logos in web/src/teamLogos.json from Wikimedia Commons
scripts/fetch_driver_photos.py downloads driver photos (web/src/driverPhotos.json) from Wikimedia Commons
scripts/fetch_flags.py         downloads the flags in web/src/countries.json (flag-icons SVGs)
web/                       the website (React + TypeScript + Vite, theme copied from xpfpl)
.github/workflows/site.yml scheduled export + build + push to gh-pages
```

- `app.py` uses `st.navigation`, so `pages/` is **not** auto-discovered. A new page must
  be registered in the `pages` list in `app.py` (and linked from `home()` if wanted).
  `st.set_page_config` is called only in `app.py`.
- Each page starts with a `sys.path.insert(...)` shim so it can `import config` when run
  directly (e.g. by `AppTest`). Keep it.
- The shared session picker (`render_session_selector`) lives in `data_engine.py` to keep
  to the original spec's file tree. It stores the chosen `(year, event_name, code)` under
  the non-widget key `st.session_state["active_session"]`, so the choice survives page
  switches (Streamlit deletes widget-keyed state on navigation). It lists only the event's
  finished sessions, in weekend order.
- **Practice** codes `FP1`/`FP2`/`FP3` are in `config.PRACTICE_CODES` and `ALL_SESSIONS`, **not** `WEEKEND_ORDER`
  (the sessions with forecasts): loaded, analysed and shown, never forecast. A sprint weekend has FP1 only. The
  picker lists them in the order they ran; race/quali pages redirect a practice pick, the Practice page a
  competitive one (`page_session(..., "practice")`). The export keeps them in `practice_recs`, apart from `exported`.
- **Session codes** (config.SESSION_NAMES / SESSION_LABELS): `R` Grand Prix, `S` Sprint, `Q`
  Qualifying, `SQ` Sprint Qualifying ("Sprint Shootout" in 2023: `config.session_names`). A sprint
  weekend runs SQ, S, Q, R. Race pages call `de.page_session(active, "race")` (Q→R, SQ→S) and the
  Qualifying page `de.page_session(active, "quali")` (R→Q, S→SQ), so no pick is a dead end.
  Show which kind a session is with `de.session_badge(code)` (Streamlit) / `SessionBadge` (site).

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
- **Qualifying** (`get_quali_laps`, `get_quali_results`, `quali_sectors`, `quali_evolution`,
  `quali_track_gain`): laps are split into Q1/Q2/Q3 by the chequered flags that end each
  segment (OpenF1 `Session.segment_ends`) or FastF1's `split_qualifying_sessions`. Who went
  through is by classified position against `quali_cutoffs(n)` (20 cars 15/10, 22 cars 16/10),
  not by having a time (a driver can reach Q2 and set none). Quali pace = each segment's time
  against the Q3 runners' median in that segment, best of them, centred (comparable across
  segments on an evolving track). Track gain = slope of push laps on the clock, each against the
  driver's own mean in that segment.

## Telemetry (`modules/telemetry.py`)

- Always FastF1 car + position data (`load_telemetry_session`, `@st.cache_resource` keeps **2**:
  a race is ~1.3 M rows), whatever `config.DATA_SOURCE` says. It calls `get_session_info` first, so
  running/missing sessions raise the usual errors. A race downloads in ~20 s, then `.fastf1/`.
- **Track replay** (`replay_payload`, Race Recap's first tab behind a "Load track replay" button
  remembered in `st.session_state["replay_session"]`): every car on one clock at
  `config.REPLAY_HZ`, gzip'd int16 arrays (x, y, speed, gear, throttle, brake, drs, pos, gap) in
  base64 (~1.3 MB a race), unpacked in the browser with `DecompressionStream`; the player
  interpolates between frames. Embedded with `st.iframe` (`components.v1.html` is deprecated).
  - Running order = laps completed + fraction round the lap from projecting x/y onto the outline
    (the fastest lap). Lap timing (`by_time`, from each lap's own start to end) takes over in the
    pit lane or when the projection is > 0.25 lap off. A car never goes backwards.
  - Before the start: grid order (pit-lane starters last, and held at -0.5 until their pit exit).
    Ties (at the flag, parked under red) go to whoever crossed the line first.
  - Red flag: order and gaps frozen at the moment it came out; gaps use a clock that stops under
    red. The red-flag queue in the pit lane is not a stop. Known glitch: a few seconds of
    shuffling as cars leave the pit lane at the restart.
  - Gap = how long ago the leader was where the car is; at the flag it's exact (lap end − the
    first car's end of that lap). Checked: 2024 Silverstone and São Paulo final gaps match the
    official result to 0.1 s.
- **Head-to-head** (`lap_trace`, `compare_laps`): each lap pinned to the line at 0 s and at its lap
  time, B's distance scaled to A's lap length, so the delta ends on the lap-time gap and the zone
  gains sum to it. Corner zones: corners within `CORNER_GROUP_GAP_M` grouped, ± `CORNER_ZONE_PAD_M`,
  classed by minimum speed (`CORNER_SPEED_CLASSES`); straights between. DRS = codes ≥ 10 (open);
  no DRS from 2026 (row hidden).

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
- **Team badges** (`web/src/teamLogos.json`, one manifest for both sides; site `TeamBadge`/`TeamName` in
  `components/f1.tsx` + `teams.ts`, dashboard `modules/teams.py` → `with_badges` + `badge_column()` as an
  `ImageColumn`): a freely licensed logo (Wikimedia Commons: public domain, CC0, CC BY/BY-SA; files in
  `web/public/logos/`, credits on About) on a white tile edged in the team colour, else the colour with a short
  code. Matched by name, then the chassis before '-' ("Lotus-Climax"); entries' `years` split teams that shared a
  name (Lotus), logos' `since`/`until` pick the era (pass the season). No logo: most pre-1980s
  teams. Add one: put its Commons title and file in the manifest, run
  `scripts/fetch_team_logos.py` (refuses non-free licences, fills licence/author/page). Exception: logos supplied by the user (not free:
  Ferrari, Racing Bulls, Toro Rosso, AlphaTauri, Aston Martin 2021, McLaren 1971-96, Lotus, Brabham, Alfa Romeo to 2018)
  have no `source`; the site file's copy is in `archive/logos/` (manifest `archive`, copied back by the script if the
  site file is missing), `page` is where it came from, and a cropped/converted one's download is in `archive/logos/original/`. Drivers keep their colour
  chip/dot; badges are for teams. Plotly legends stay colour lines.
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
  `DATA_SOURCE`): its last chequered flag (`config.last_chequered`: qualifying shows three, one
  per segment) is 5 min old, or 6 h (race) / 3 h (qualifying) have passed since the start with no
  flag. Nothing looks before start + 75 min (race) / 25 (sprint) / 55 (Q) / 40 (SQ). Before the flag, loading
  raises `data_engine.SessionRunningError` (`openf1.SessionRunning`): OpenF1 fetches race control
  first on race day and nothing else until the flag; the dashboard shows "hasn't finished yet".
  Never go back to a fixed timer: 2026 R16 (KL) took 3 h 20 min. `needs_update.py` keeps a copy of
  the constants and asks OpenF1 for the flag itself. A FastF1 session < `RECENT_DAYS` (4) old is
  loaded past FastF1's disk cache (it would pickle a partial or pre-penalty load for good), full
  tier only; `get_session_info` returns `provisional`, and the dashboard shows a banner with
  "Check for updates" (clears the memory caches).
- `site_export.export_season` loads every session that could have finished (all four kinds), skips
  running ones, writes `meta.json`, `races/rNN-R|S|Q|SQ.json` (qualifying: `export_quali`) and
  `forecasts/rNN.json`. Tables are column-wise
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
  **Archive:** a final session (start + 4 days, result and, for a race, grid in; grid optional
  after 14 days) is written to `archive/openf1/<year>/rNN-R|S|Q|SQ.json.gz` (raw endpoint JSON + Jolpica
  grid, deterministic gzip) and read from there for good; the Action commits new files to
  main. Don't hand-edit archive files; delete one to force a re-fetch.
  **Gaps:** if OpenF1 lacks a session's laps (2025 Baku Q), `scripts/backfill_fastf1.py <year>` (local only)
  fills the missing endpoints from FastF1, keeping OpenF1's, into `rNN-<code>.fastf1.json.gz` (`sources`
  names what came from where; the site and dashboard show it). FastF1's session clock is tied to UTC by
  its first "Started" status vs OpenF1's SESSION STARTED message (checked: segment ends vs chequered
  flags, 0.2 s on Baku). A Q classification OpenF1 lacks comes from Jolpica (`_jolpica_quali`).
  OpenF1's result is the official classification; FastF1's timing order can differ (2026
  KL: LEC P4 officially, P17 in FastF1's timing order).
- **Provisional results:** until the classification and grid are both in, `_results` derives
  points from timing order and classification from laps ≥ 90 % of the winner's, and the
  session has `complete: false`; `needs_update.py` rebuilds while a session < 4 days old is
  provisional or anything is pending.
- **Forecasts** (`modules/forecast.py`, constants in `config.py` "Season forecasts"; fitted by
  `scripts/calibrate_forecast.py 2025 2026`, which replays every session from the ones before it, then scores
  the result against naive baselines (equal odds, last session's order, standings, grid) on the same targets;
  `--report-only` skips the search):
  - race pace = median fuel-corrected clean lap / field median on that compound, in %; quali pace
    as above. Race form (R + S, a sprint `SPRINT_FORM_WEIGHT`) and quali form (Q + SQ) = decayed
    mean over the last 6 rounds, each session's pace first held to ±`FORM_CLIP` (0.25 %) of the
    driver's median over the window (`forecast.form_inputs`): without it one bad session (2026 Baku Q:
    ANT +0.38 %) flipped every race left. Improved every session kind in the replays
  - **car + driver form** (`forecast.split_form`, sessions in `config.SPLIT_FORM`: Q, SQ, S): team form
    (this season, `FORM_DECAY`) + the driver's rating against teammates (ridge on teammate gaps since the
    start of last season, `TEAMMATE_*`; `last_season()["form"]` feeds it) + streak (own form less those)
    × `STREAK_PERSIST`^rounds ahead. Q 3 ahead −2.99 → −2.85, SQ/S better, next-round Q level; the Grand
    Prix got no better at any setting, so it keeps plain form. The "why" rows are `form_car/driver/streak/practice`
  - grid: official once the race is in, else OpenF1 `starting_grid` (keyed on the qualifying session;
    has grid penalties, misses only late pit-lane starts: checked on 2025-26), else the qualifying order
    with `config.GRID_PENALTIES` (hand-kept, Grand Prix only). Before qualifying an announced penalty
    adds `GRID_WEIGHT` × places expected to be lost (`forecast.session_terms`), in title odds too
  - forecast files carry the breakdown (race_form, quali_form, quali_share, circuit/grid/penalty terms,
    summing to `pace`) and `sessions[code].why` (the sessions behind each driver's form, used vs raw
    pace, share; quali and grid sources): the site's "Why these odds?" panel (`components/WhyOdds.tsx`)
  - every session of a weekend gets odds (`forecast_session`): expected pace (race: race form
    blended with quali form `RACE_QUALI_BLEND`, or with that race's own qualifying
    `RACE_QUALI_BLEND_WEEKEND` + `GRID_WEIGHT` per grid place once it's in) + last season's team
    circuit offset × `CIRCUIT_WEIGHT` + horizon drift `DRIFT_PER_ROUND`·√(rounds ahead − 1) +
    `SESSION_SD[code]` (`SESSION_SD_GRID` once the grid is known). The circuit term was weak on
    2026 (new rules): keep the weights the calibration gives, don't inflate them
  - each forecast file has `sessions[code] = {pre, latest, latest_after}`: before the weekend, and
    after the weekend's earlier sessions. Title odds play every S and R left with its circuit term
  - forecasts for finished rounds are recomputed from earlier sessions on every export, so they
    change if the model changes. **As published** (`site_export.archive_forecast`): the next round's
    forecast file + title odds are cached in `.snapshots/<year>.json` each export; when the stage (the
    round's last finished session, "pre" before any) moves on, the last version goes to
    `archive/forecasts/<year>/rNN-pre|after-FP1|...|after-Q.json.gz`, written once (gh-pages keeps no history)
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
- **Lap telemetry** (`modules/site_telemetry.py`, `web/src/components/Telemetry.tsx`, a "Lap Telemetry"
  section on every session page): each driver's fastest valid lap from OpenF1 `car_data` + `location`,
  two requests a driver limited to the lap's time window (cached for good: the URL is the window).
  Corners and map rotation from MultiViewer (`fastf1.mvapi`, FastF1's circuit-info source; same
  coordinates as OpenF1's location). Laps are put on the fastest lap's distance axis (speed integrated,
  pinned to the line at 0 s and the lap time, scaled to the reference length), every
  `SITE_TEL_STEP_M`; the browser compares any two (delta = time difference at the same index), so
  one file serves every pairing (~170 KB). **Stale samples** (checked on all 862 2026 laps): F1's car
  feed sometimes holds one reading for seconds (Monza Q GAS: 308 km/h held into Turn 4), flagged by
  throttle/brake 104 or every channel unchanged >= 1 s; and a sample that would need > 220 km/h/s
  braking or > 80 km/h/s acceleration is stale. Those are dropped; a hole > 1.5 s is bridged (distance
  from projecting the car's position at each end of it onto the outline) and listed in the lap's
  `gaps`, which the site shades "no data" and never claims time in. ~16% of laps have a hole; a lap
  with over a third missing is left out (~3%). Don't loosen these without re-checking those laps. Archive `archive/openf1/<year>/rNN-<code>.tel.json.gz`
  (raw samples, columns, ms from lap start) once the session is archived. One export run downloads at
  most `SITE_TEL_MAX_FETCH` sessions (`--telemetry-budget`, `--no-telemetry-download`): the first
  backfill of a season (~1.5 min a session) is run locally. A telemetry failure never fails a session.
  Site: lap cards (sectors, full throttle / heavy braking / cornering shares), track dominance map
  (25 mini-sectors or corner zones, tooltip per zone), speed trace with corner zones by speed class and
  the time won in each, delta, throttle, brake, gear, RPM, DRS (pre-2026), synced hover (crosshair on
  every trace, marker on the map), corner table. Same zone rules as the dashboard's Telemetry page.
- **Penalties and power units** (`modules/penalties.py`, config "Power-unit penalties", `components/Penalties.tsx`):
  - Sources: **f1penalties.com** (every stewards' decision since 2020, the CSV export of its Dash app via
    `_dash-update-component`; lags the FIA by days to weeks, so fetched every `PENALTY_FRESH_HOURS`; a season is archived
    to `archive/f1penalties/<year>.csv.gz` from 15 Jan next year) and the **FIA's documents** ("PU elements used per
    driver up to now" + "New PU elements for this Competition" PDFs, read with pypdf, matched by car number; an event is
    archived to `archive/fia/<year>/rNN-pu.json.gz` 4 days after its race, never with an unparsed usage table). The PDFs'
    text breaks words ("Ca r Drive r", "Geor ge Russell"): parse loosely, match names with `name_key`. FIA pages are slow
    (6-25 s each); a cold backfill of five seasons took about 10 min. 2023's "PU elements used" title is right but the
    page also lists RNC (gearbox) tables: match the title, not "used per driver".
  - `usage_table`: per round and car, elements before/fitted/after (fitted = next round's table minus this one, else the
    "New PU elements" list), `grid_drop` (10 first of a kind past the allocation, 5 after, back past 15). A driver new
    mid-season inherits the car's counts: no change for that jump. The FIA tables catch more PU penalties than
    f1penalties (93% of its are in them; it misses some, e.g. 2025 Colapinto Silverstone).
  - Model (`penalty_risk`): logistic hazard per Grand Prix on deficit (elements short at the allocation's rate),
    already over this season, season left and log circuit factor (`circuit_factors`: PU penalties per race at a circuit,
    2020 to last season, shrunk by `PU_CIRCUIT_PRIOR`). Leave one season out log loss 0.1635 vs 0.1726 circuit-only,
    0.1792 constant. `PU_PLANS` (hand-kept, from team statements, with the source) puts at least `PU_PLAN_HAZARD` on each
    named round until the penalty is taken. Once a round's "New PU elements" is out, an over-allocation element there is
    an announced penalty (`PowerUnits.announced`, merged into `penalties_for`) and everyone else keeps `PU_LATE_SHARE`.
  - A place lost to a penalty costs `PENALTY_GRID_WEIGHT` (0.05%), not `GRID_WEIGHT` (0.02%, fitted on ordinary grids):
    94 PU penalty starts 2020-25 finished 0.237 places worse per grid place dropped. Applied to announced penalties and
    the risk before the grid, and to a slot behind the qualifying position after it (`session_terms(quali_order=)`).
  - Session forecasts draw the PU penalty all-or-nothing per simulated race (`forecast_session(terms=)`); title odds
    draw it race by race with the after-one hazard once taken. Past rounds' forecasts use the state before that round.
  - Export: `site_export.PowerUnits` (module global `_PU` during an export) writes `power-units.json` and each session
    file's `penalties` (the weekend's decisions + PU elements fitted); race results get `quali`. meta.json `penalties`
    holds a hash of the season's f1penalties rows and the last FIA PU documents read: `needs_update.py` (its own copies of
    the constants) rebuilds when either changes, checked once the export is an hour old.
  - Dashboard: Race Recap's Penalties tab. Site: "Stewards & Power Units" on every session page, "Power-Unit Penalties"
    and a "PU risk" column on Next Race, terms in "Why these odds?", Docs section "Power-unit penalties".
- **News** (`modules/news.py`, config "NEWS_*"): RSS from eight sites (PlanetF1 404s, RaceFans 403s), read at most
  hourly, kept in `.news/items.json` 45 days (headline, link, date, the feed's summary). A teaser cut
  mid-sentence (Motorsport.com, Autosport: "... Keep reading") gets that one sentence finished from the article page
  (robots.txt permitting, `NEWS_COMPLETE_MAX` a run, each article once, the page never stored), else is cut back to
  its last full sentence; the feed's text stays in `teaser`. **Archive:** each UTC day's
  items go to `archive/news/<year>/<date>.json.gz` once the day is `NEWS_ARCHIVE_AFTER_DAYS` (3) old, written once. Items are
  tagged with topics (pu / penalty / upgrade), drivers (surname), teams (`upgrades.TEAM_KEYS`) and rounds (event,
  city, country, nicknames). A sentence with a driver, a round still to run and a PU penalty (no negation) is a
  claim; `NEWS_PLAN_MIN_SOURCES` (2) different sites within 10 days make a reported plan, handled like `PU_PLANS`.
  Every export reads the feeds; meta.json `news.pu_items` lists the PU-penalty headlines it read, and
  `needs_update.py` (hourly midweek, its own copy of the sentence test) rebuilds when a feed has a new one.
  Each plan is an independent one-off penalty (`penalty_risk`'s `plan_p`; title odds draw each plan's round once
  per season), so a Singapore change after a failure and a later upgrade change can both happen. Plans retire
  once the FIA tables show the change. News plans count only for forecasts made now (not replays).
- **Upgrades** (`modules/upgrades.py`): the FIA's "Car Presentation Submissions" PDF each Thursday (2024 partial,
  2025-26 full; 2023's weren't found), one row per part with the team's stated reason (performance / circuit /
  reliability / cooling). `site_export.upgrade_effect`: a team's race pace at an upgraded round against its
  previous `UPGRADE_BEFORE` races (2026: quicker only ~half the time). The forecast's `UPGRADE_EFFECT` term is
  fitted by calibrate_forecast.py. `news.json` has the items, upgrades and effects; Next Race shows them.
- **Driver and circuit pop-ups** (`modules/profiles.py` → `profiles.json`; site `components/Profiles.tsx` (host, links, flags;
  every page) + `ProfileViews.tsx` (the pop-ups, lazy-loaded)): any `DriverChip`/`DriverName` opens the driver, any
  `CircuitLink` the circuit, in the wide `Sheet`; opening pushes a history entry so Back closes it, a hash change closes it.
  On History codes repeat across eras, so a chip opens only with `historyRef` (`ProfileHost scope`). `profiles.json` is
  built from the session files already in `races/` plus Jolpica's season `drivers`/`races` lists (driverId = the History
  `ref`, circuitId = History circuit, nationality → ISO3 through `web/src/countries.json`, shared with Python), so
  `export_site.py --profiles-only` rewrites it in seconds; the full export writes it last. Circuit outline: MultiViewer
  (this season's, else last season's) else this season's telemetry file. `race_analysis` per finished Grand Prix (passes =
  places gained over laps clear of pits, lap 1 and SC/VSC/red: an estimate for comparing races; stops = stints − 1, red-flag
  changes count) is ranked client-side against the season. History `circuits.json` carries each race's fastest lap and the
  winner's grid; a circuit's `record` = fastest race lap on today's layout (same scheduled laps as the latest race, ≥ 90 %
  of its fastest lap: Silverstone 1:27.097 VER 2020, not 2005's old layout). Photos: Commons lead image of the
  driver's Wikipedia article, free licences only (OGL counts; F1's headshots don't), credited on About; none = car number.
  Flags are images (Windows has no flag emoji). A new driver: run `scripts/fetch_driver_photos.py` after an export.
- Local preview: Vite's dev server refuses every file here (the path contains `.git`, which its default `fs.deny`
  matches), so `npm run build` then `npx vite preview`.
- Circuits are matched across seasons by `circuit_key(location)` (via the pit-loss table),
  never by event name: the 2026 "Bahrain Grand Prix" was in Kuala Lumpur.
- Pages must render at phone width (`usePhone`, cards via `Table`). Screenshot both widths.
- Site routes: `#races/16` Grand Prix (or the weekend's latest finished session before it),
  `#races/16/S|Q|SQ`; `#next/17/<code>` a session's forecast. `web/src/data.ts` has the session
  helpers (`weekendSessions`, `sessionDone`, `sessionHash`, `sessionOdds`).
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
- **Bookmarked: live strategy screen** (like RaceOS F1: live track map, timing, strategy sim during a
  session). OpenF1's real-time tier is paid. F1's own live-timing stream (FastF1 `livetiming`,
  unofficial) is still free for timing (positions, gaps, laps, sectors), tyres, race control, track
  status and weather; since the 2025 Dutch GP the driver tracker (car positions), DRS, pit-stop times
  and championship tables need an F1 TV login (FastF1's client supports one). So a free version gets
  the leaderboard and strategy calls but no live track map. Needs a recorder process during the
  session (Streamlit can't hold the connection); local only (GitHub's runners can't reach F1's
  server). The replay player could take a live payload unchanged. Not started.
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
