"""
Fill OpenF1's gaps from F1 live timing (FastF1), locally, into the archive.

    .venv\\Scripts\\python scripts\\backfill_fastf1.py 2025                       # scan: every gap of 2025
    .venv\\Scripts\\python scripts\\backfill_fastf1.py 2025 --round 17 --code Q   # one session

A scan loads every session of the season that finished more than CACHE_FINAL_DAYS ago through
modules/openf1 and backfills the ones OpenF1 can't give laps for (modules/openf1.py,
backfill_from_fastf1). Each is written to archive/openf1/<year>/rNN-<code>.fastf1.json.gz: commit
it, and the website reads it like any archived session. Runs locally only: F1's live-timing server
doesn't answer GitHub's runners.
"""
import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

import fastf1  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from modules import openf1  # noqa: E402


def backfill(year: int, event: str, code: str, label: str) -> None:
    try:
        out = openf1.backfill_from_fastf1(year, event, code)
    except Exception as exc:  # noqa: BLE001 — report and carry on
        print(f"{label}: backfill FAILED ({str(exc)[:200]})", flush=True)
        return
    check = f"{out['check_s']:.1f} s" if out["check_s"] is not None else "nothing to compare"
    print(f"{label}: filled {', '.join(f'{k} from {v}' for k, v in out['filled'].items()) or 'nothing'}; "
          f"clock tied to UTC by the {out['anchor']} (segment ends vs OpenF1's flags: {check}) "
          f"-> {out['path'].relative_to(ROOT)}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("year", type=int)
    ap.add_argument("--round", type=int, help="one round (with --code)")
    ap.add_argument("--code", choices=list(config.SESSION_NAMES), help="R, S, Q or SQ")
    args = ap.parse_args()
    config.FASTF1_CACHE_DIR.mkdir(exist_ok=True)
    fastf1.Cache.enable_cache(str(config.FASTF1_CACHE_DIR))
    fastf1.set_log_level("ERROR")

    sched = fastf1.get_event_schedule(args.year, include_testing=False)
    final = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=openf1.CACHE_FINAL_DAYS)
    for _, ev in sched.iterrows():
        rnd = int(ev["RoundNumber"])
        if args.round and rnd != args.round:
            continue
        for i in range(1, 6):
            code = next((c for c in config.SESSION_NAMES if ev[f"Session{i}"] in config.session_names(c)), None)
            start = ev[f"Session{i}DateUtc"]
            if code is None or pd.isna(start) or (args.code and code != args.code):
                continue
            label = f"{args.year} r{rnd:02d} {code:2s} {ev['EventName']}"
            if args.round and args.code:
                backfill(args.year, ev["EventName"], code, label)
                continue
            if pd.Timestamp(start).tz_localize("UTC") > final:
                continue
            try:
                openf1.load_session(args.year, ev["EventName"], code)
            except openf1.OpenF1Error as exc:
                if "no laps" in str(exc):
                    backfill(args.year, ev["EventName"], code, label)
                else:
                    print(f"{label}: OpenF1 failed, not a lap gap ({str(exc)[:150]})", flush=True)


if __name__ == "__main__":
    main()
