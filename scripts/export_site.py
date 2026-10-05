"""
Write the website's data files (web/public/data/) for a season.

    .venv\\Scripts\\python scripts\\export_site.py              # current season
    .venv\\Scripts\\python scripts\\export_site.py --year 2026 --out web/public/data
    .venv\\Scripts\\python scripts\\export_site.py --history-only    # just history/: 1950 to last season

Then `npm run dev` in web/ to look at the site locally. The GitHub Action
(.github/workflows/site.yml) runs this, builds the site and publishes it.
"""
import argparse
import datetime as dt
import logging
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# FastF1 warns about every lap-accuracy and stint fix, and Streamlit's caches (which work
# without a running app) warn on every call: hundreds of lines. Sessions that fail to
# load are listed in the summary line instead.
logging.disable(logging.WARNING)

import fastf1  # noqa: E402

import config  # noqa: E402
from modules import history, site_export  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    ap.add_argument("--out", type=Path, default=ROOT / "web" / "public" / "data")
    ap.add_argument("--source", choices=["openf1", "fastf1"], default="openf1",
                    help="session data source (default openf1: works on GitHub's runners)")
    ap.add_argument("--no-history", action="store_true", help="skip the History pages' files")
    ap.add_argument("--no-telemetry-download", action="store_true",
                    help="write lap telemetry only for sessions already archived")
    ap.add_argument("--telemetry-budget", type=int, default=None,
                    help=f"sessions whose telemetry may be downloaded (default {config.SITE_TEL_MAX_FETCH})")
    ap.add_argument("--history-only", action="store_true", help="only write history/ (keeps the rest)")
    args = ap.parse_args()
    config.DATA_SOURCE = args.source

    if args.history_only:
        export_history(args.out, args.year)
        return

    config.FASTF1_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(config.FASTF1_CACHE_DIR))
    fastf1.set_log_level("ERROR")

    shutil.rmtree(args.out, ignore_errors=True)        # no stale race files from a calendar change
    started = time.time()
    print(f"Exporting {args.year} to {args.out}", flush=True)
    if args.telemetry_budget is not None:
        config.SITE_TEL_MAX_FETCH = args.telemetry_budget
    meta = site_export.export_season(args.year, args.out, telemetry=not args.no_telemetry_download)
    print(f"Done in {time.time() - started:.0f} s: {len(meta['sessions'])} sessions, "
          f"{len(meta['forecasts'])} forecasts, pending {meta['pending'] or 'none'}.")
    if not args.no_history:
        export_history(args.out, args.year)


def export_history(out: Path, year: int) -> None:
    """Every season before `year`, from Jolpica's dump. A failure leaves the History page empty
    rather than failing the season's export."""
    started = time.time()
    shutil.rmtree(out / "history", ignore_errors=True)
    try:
        done = history.export_history(out, year - 1)
    except Exception as e:
        print(f"History export failed: {type(e).__name__}: {e}")
        return
    print(f"History in {time.time() - started:.0f} s: {done['seasons']} seasons, {done['races']} races, "
          f"{done['lap_files']} with lap charts.")


if __name__ == "__main__":
    main()
