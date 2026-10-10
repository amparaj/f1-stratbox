# F1 Stratbox

Formula 1 race strategy, built on lap timing from [OpenF1](https://openf1.org) and [FastF1](https://docs.fastf1.dev/). Three parts:

- **Website** (https://amparaj.github.io/f1-stratbox/, phone-friendly): every race of the season
  reviewed lap by lap (Qualifying, Sprint Qualifying, Sprints and Grands Prix), the standings
  and title odds, and a forecast for every session to come. It updates itself after every session. Plus every season since 1950: champions,
  standings, every race result, career records and circuits.
- **Dashboard** (Streamlit, runs locally): post-race review, qualifying analysis, a lap-by-lap
  race tracker and a strategy sandbox (Grand Prix or Sprint).
- **Analysis code** shared by both: `modules/`.

## Website

| Page | What's on it |
| --- | --- |
| Season | Drivers' and constructors' standings (Grand Prix and Sprint points apart, wins, sprint wins, poles), title odds from 10,000 simulated seasons and how they moved, points through the season, the calendar with every pole, sprint winner and winner |
| Race Results & Analysis | Every session of every weekend, labelled by kind. Races (Grand Prix, Sprint): the story in bullet points, the result, the forecast against what happened, running order by lap, every driver's tyre strategy (with tyre cliffs), gap to the leader, degradation per compound, a stint-by-stint post-mortem. Qualifying (and Sprint Qualifying): Q1/Q2/Q3, knockout margins, sectors and the ideal lap, track evolution, the forecast against the result |
| Next Race Forecast | For each session of the weekend: pole / Q3 / knock-out chances, or win, podium and points chances (before the weekend, and after qualifying with the grid); the qualifying strategy; the fastest tyre strategy for the Grand Prix and the Sprint; and the favourite for every round left |
| History | Every season since 1950: champions, standings and points through the season, every race result (running order lap by lap and pit stops from 1996), every driver's career, every constructor and circuit |
| About | How the analysis and forecasts work |

### How it updates

`.github/workflows/site.yml` runs every 10 minutes Friday to Sunday (UTC) and hourly
otherwise. Each run first asks `scripts/needs_update.py`, which reads the published
`meta.json`, whether a session (qualifying, sprint, race) whose last chequered flag is out (OpenF1) isn't on the site yet,
or a provisional result may now have its official classification (checked hourly at most), or a news
feed has a new power-unit penalty headline (a reported penalty moves the forecasts). A race
is usually on the site 15–40 minutes after the flag. Only then does it export the data (`scripts/export_site.py`),
build the site (`web/`, React + Vite) and push it to the `gh-pages` branch. A push to `main`
that touches the code, or **Actions → Update site → Run workflow**, always rebuilds.

The site's data comes from the OpenF1 API (`modules/openf1.py`): F1's live-timing server, which
FastF1 reads, doesn't answer GitHub's runners. The download caches are kept between runs, so a
run downloads only the new session; the first downloads this season and last (about 25 minutes,
paced to OpenF1's free rate limit).

Until the classification and the starting grid (from Jolpica, a few hours to a day after the
race) are both in, the race is marked provisional: missing points and retirements are worked out
from the timing data.

### History

`modules/history.py` builds the History pages from [Jolpica](https://github.com/jolpica/jolpica-f1)'s
database dump (the successor to Ergast): every season up to last year, as CSV tables in one ~14 MB
zip. It's free for non-commercial use under CC BY-NC-SA 4.0; the free dump runs 14 days behind,
which doesn't matter for finished seasons. Every finished season is archived in the repo (see
below), so the dump is only downloaded when a season isn't archived yet: once a year, for the
season just finished. The site's files are rebuilt from the archive on every run (about 30 s).
If a season is missing and Jolpica can't be reached, the site publishes without the History
files rather than failing.

### Archive

Every finished session's raw data (OpenF1's laps, stints, pit stops, race control, weather and
result, and Jolpica's grid) is kept in `archive/openf1/<year>/rNN-R.json.gz` (`-S` sprint, `-Q` qualifying,
`-SQ` sprint qualifying),
committed to the repo: about 50 kB a race. The History pages' data is archived the same way:
`archive/jolpica/<year>.json.gz` holds that season's rows from every table of Jolpica's dump
(about 14 MB for 1950–2025, most of it lap times). A season goes in once a dump taken after
15 January of the next year has it, and from then on it's read from the archive and never
downloaded again. A session goes in once it's final, 4 days after it
ran with its result and grid in, and from then on it's read from the archive and never fetched
again. The Action commits new archive files to `main` itself. Newer sessions are re-fetched now
and then, so penalties and late grids come through.

### Branches

| Branch | What it is |
| --- | --- |
| `main` | The code and the archives (`archive/openf1/`, `archive/jolpica/`; the Action commits new files) |
| `gh-pages` | The built website that GitHub Pages serves. The Action replaces it with one fresh commit each time: never edit or merge it |

### Locally

```
.venv\Scripts\python scripts\export_site.py      # writes web/public/data/ (and history/)
.venv\Scripts\python scripts\export_site.py --history-only   # just the History files
cd web
npm install                                      # once
npm run dev                                      # http://localhost:5173
```

## Dashboard

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -e .                  # dependencies from pyproject.toml
.venv\Scripts\streamlit run app.py
```

`scripts\prefetch_season.py` downloads every finished session (qualifying, sprint, race) of a
season into the local cache (`.fastf1/`) so the dashboard opens them instantly.

### Filling OpenF1's gaps

When OpenF1 is missing part of a finished session (2025 Azerbaijan qualifying has no laps there),
`scriptsackfill_fastf1.py 2025` (or `2025 --round 17 --code Q`) fills it from F1's live-timing
archive through FastF1, keeping everything OpenF1 has, and writes
`archive/openf1/<year>/rNN-<code>.fastf1.json.gz`. Run it locally (F1's live-timing server doesn't
answer GitHub's runners) and commit the file. A qualifying classification OpenF1 hasn't got comes
from Jolpica automatically.

### Forecast calibration

`scripts\calibrate_forecast.py 2025 2026` replays every session's forecast from the sessions
before it and fits the "Session forecasts" constants in `config.py` (results noted there).

## Data

Website: lap timing, tyres, pit stops, race control, weather and results from OpenF1; the calendar
via FastF1; starting grids from Jolpica (Ergast); history since 1950 from Jolpica's database dump
(CC BY-NC-SA 4.0); weather forecasts and climate from [Open-Meteo](https://open-meteo.com/)
(free, no key, non-commercial, CC BY 4.0). Dashboard: Formula 1's live-timing feed via
FastF1 (set `config.DATA_SOURCE = "openf1"` to use OpenF1 instead). Pit-lane losses (`config.TRACK_PIT_LOSS`) and the
tyre presets are approximate. Not affiliated with Formula 1 or the FIA.
