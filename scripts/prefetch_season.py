"""
Warm the FastF1 disk cache (.fastf1/) for every completed session of a season (Grand Prix,
Sprint, Qualifying, Sprint Qualifying),
so the app opens those sessions instantly instead of hitting the F1 timing API.

    .venv\\Scripts\\python scripts\\prefetch_season.py            # current season
    .venv\\Scripts\\python scripts\\prefetch_season.py 2025 2026  # specific seasons

Loads exactly what the app's 'full' tier loads (laps + weather + messages, no telemetry).
Re-running is cheap: already-cached sessions are read from disk.
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fastf1
import pandas as pd

import config

SPRINT_FORMATS = {"sprint", "sprint_shootout", "sprint_qualifying"}


def prefetch(year: int) -> list[tuple[str, str, str]]:
    sched = fastf1.get_event_schedule(year, include_testing=False)
    past = sched[pd.to_datetime(sched["EventDate"]) < pd.Timestamp(dt.date.today())]
    results = []
    for _, ev in past.iterrows():
        names = {ev[f"Session{i}"] for i in range(1, 6)}
        codes = [c for c in config.WEEKEND_ORDER if names & set(config.session_names(c))
                 and (c in ("Q", "R") or ev["EventFormat"] in SPRINT_FORMATS)]
        for code in codes:
            label = f"{year} R{ev['RoundNumber']:>2} {ev['EventName']} [{code}]"
            try:
                name = next(n for n in config.session_names(code) if n in names)
                session = fastf1.get_session(year, ev["EventName"], name)
                session.load(laps=True, telemetry=False, weather=True, messages=True)
                n_laps = int(session.laps["LapNumber"].max())
                first = session.results.sort_values("Position").iloc[0]["Abbreviation"]
                status = f"ok  {n_laps} laps, {'pole' if code in config.QUALI_CODES else 'winner'} {first}"
            except Exception as exc:  # noqa: BLE001 — report and carry on
                status = f"FAILED  {exc}"
            print(f"{label:<50} {status}", flush=True)
            results.append((label, code, status))
    return results


def main() -> None:
    years = [int(a) for a in sys.argv[1:]] or [dt.date.today().year]
    config.FASTF1_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(config.FASTF1_CACHE_DIR))
    fastf1.set_log_level("ERROR")
    failed = [r for y in years for r in prefetch(y) if r[2].startswith("FAILED")]
    print(f"\nDone. {len(failed)} failed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
