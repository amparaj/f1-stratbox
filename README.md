# F1 Stratbox

Formula 1 race strategy, built on [FastF1](https://docs.fastf1.dev/) lap timing. Three parts:

- **Website** (https://amparaj.github.io/f1-stratbox/, phone-friendly): every race of the season
  reviewed lap by lap, the standings and title odds, and a forecast for each race to come. It
  updates itself after every race and sprint.
- **Dashboard** (Streamlit, runs locally): post-race review, a lap-by-lap race tracker and a
  strategy sandbox.
- **Analysis code** shared by both: `modules/`.

## Website

| Page | What's on it |
| --- | --- |
| Season | Drivers' and constructors' standings, title odds from 10,000 simulated seasons and how they moved, points through the season, the calendar with every winner |
| Race Results & Analysis | For each race and sprint: the story in bullet points, the result, the forecast made before the race against what happened, running order by lap, every driver's tyre strategy (with tyre cliffs), gap to the leader, degradation per compound, and a stint-by-stint post-mortem |
| Next Race Forecast | Win, podium and points chances for every driver, and the fastest 1- or 2-stop tyre strategy (searched, then Monte Carlo'd with Safety Cars), for the next race or any later one |
| About | How the analysis and forecasts work |

### How it updates

`.github/workflows/site.yml` runs hourly Saturday to Monday (UTC) and daily otherwise. Each run
first asks `scripts/needs_update.py`, which reads the published `meta.json`, whether a race or
sprint finished more than 3 hours ago but isn't on the site yet, or a provisional result may now
have its official classification. Only then does it export the data (`scripts/export_site.py`),
build the site (`web/`, React + Vite) and push it to the `gh-pages` branch. A push to `main`
that touches the code, or **Actions → Update site → Run workflow**, always rebuilds.

The FastF1 download cache is kept between runs, so a run downloads only the new session. The
first run downloads this season and last season (about 20 minutes).

Until Jolpica (Ergast) has the official classification, usually a few hours to a day after a
race, the race is marked provisional: points and retirements are worked out from the timing data.

### Branches

| Branch | What it is |
| --- | --- |
| `main` | The code |
| `gh-pages` | The built website that GitHub Pages serves. The Action replaces it with one fresh commit each time: never edit or merge it |

### Locally

```
.venv\Scripts\python scripts\export_site.py      # writes web/public/data/
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

Lap timing, tyres, track status and weather from Formula 1's live-timing feed via FastF1;
classifications from the Jolpica (Ergast) API. Pit-lane losses (`config.TRACK_PIT_LOSS`) and the
tyre presets are approximate. Not affiliated with Formula 1 or the FIA.
