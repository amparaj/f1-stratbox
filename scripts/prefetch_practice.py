"""
Download every finished practice session (FP1, FP2, FP3) of a season through OpenF1, so the
export and the forecast calibration have them (archived under archive/openf1/<year>/rNN-FPn.json.gz
once final).

    .venv\\Scripts\\python scripts\\prefetch_practice.py 2025 2026
"""
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

import fastf1  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from modules import openf1  # noqa: E402


def main() -> None:
    fastf1.Cache.enable_cache(str(config.FASTF1_CACHE_DIR))
    fastf1.set_log_level("ERROR")
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    for year in [int(a) for a in sys.argv[1:]] or [now.year]:
        sched = fastf1.get_event_schedule(year, include_testing=False)
        for _, ev in sched[sched["RoundNumber"] > 0].iterrows():
            for i in range(1, 6):
                name, start = ev[f"Session{i}"], ev[f"Session{i}DateUtc"]
                code = next((c for c in config.PRACTICE_CODES if name in config.session_names(c)), None)
                if code is None or pd.isna(start) or start + pd.Timedelta(hours=config.LATEST_FINISH_H[code]) > now:
                    continue
                t = time.time()
                try:
                    s = openf1.load_session(year, ev["EventName"], code)
                    print(f"{year} r{int(ev['RoundNumber']):02d} {code}: {len(s.laps)} laps ({time.time() - t:.0f} s)", flush=True)
                except Exception as exc:  # noqa: BLE001 — report and carry on
                    print(f"{year} r{int(ev['RoundNumber']):02d} {code}: {str(exc)[:120]}", flush=True)


if __name__ == "__main__":
    main()
