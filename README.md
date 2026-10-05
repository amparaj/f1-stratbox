# F1 Stratbox

Formula 1 race strategy, built on lap timing from [OpenF1](https://openf1.org) and [FastF1](https://docs.fastf1.dev/). Three parts:

- **Website** (https://amparaj.github.io/f1-stratbox/, phone-friendly): every race of the season
  reviewed lap by lap, the standings and title odds, and a forecast for each race to come. It
  updates itself after every race and sprint. Plus every season since 1950: champions,
  standings, every race result, career records and circuits.
- **Dashboard** (Streamlit, runs locally): post-race review, a lap-by-lap race tracker and a
  strategy sandbox.
- **Analysis code** shared by both: `modules/`.

## Website

| Page | What's on it |
| --- | --- |
| Season | Drivers' and constructors' standings, title odds from 10,000 simulated seasons and how they moved, points through the season, the calendar with every winner |
| Race Results & Analysis | For each race and sprint: the story in bullet points, the result, the forecast made before the race against what happened, running order by lap, every driver's tyre strategy (with tyre cliffs), gap to the leader, degradation per compound, and a stint-by-stint post-mortem |
| Next Race Forecast | Win, podium and points chances for every driver, and the fastest 1- or 2-stop tyre strategy (searched, then Monte Carlo'd with Safety Cars), for the next race or any later one |
| History | Every season since 1950: champions, standings and points through the season, every race result (running order lap by lap and pit stops from 1996), every driver's career, every constructor and circuit |
| About | How the analysis and forecasts work |

### How it updates

`.github/workflows/site.yml` runs hourly Saturday to Monday (UTC) and daily otherwise. Each run
first asks `scripts/needs_update.py`, which reads the published `meta.json`, whether a race or
sprint finished more than 3 hours ago but isn't on the site yet, or a provisional result may now
have its official classification. Only then does it export the data (`scripts/export_site.py`),
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
result, and Jolpica's grid) is kept in `archive/openf1/<year>/rNN-R.json.gz` (`-S` for a sprint),
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
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\streamlit run app.py
```

`scripts\prefetch_season.py` downloads every finished race and sprint of a season into the local
cache (`.fastf1/`) so the dashboard opens them instantly.

## Data

Website: lap timing, tyres, pit stops, race control, weather and results from OpenF1; the calendar
via FastF1; starting grids from Jolpica (Ergast); history since 1950 from Jolpica's database dump
(CC BY-NC-SA 4.0). Dashboard: Formula 1's live-timing feed via
FastF1 (set `config.DATA_SOURCE = "openf1"` to use OpenF1 instead). Pit-lane losses (`config.TRACK_PIT_LOSS`) and the
tyre presets are approximate. Not affiliated with Formula 1 or the FIA.
