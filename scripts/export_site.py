"""
Write the website's data files (web/public/data/) for a season.

    .venv\\Scripts\\python scripts\\export_site.py              # current season
    .venv\\Scripts\\python scripts\\export_site.py --year 2026 --out web/public/data

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
from modules import site_export  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    ap.add_argument("--out", type=Path, default=ROOT / "web" / "public" / "data")
    ap.add_argument("--source", choices=["openf1", "fastf1"], default="openf1",
                    help="session data source (default openf1: works on GitHub's runners)")
    args = ap.parse_args()
    config.DATA_SOURCE = args.source

    config.FASTF1_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(config.FASTF1_CACHE_DIR))
    fastf1.set_log_level("ERROR")

    shutil.rmtree(args.out, ignore_errors=True)        # no stale race files from a calendar change
    started = time.time()
    print(f"Exporting {args.year} to {args.out}", flush=True)
    meta = site_export.export_season(args.year, args.out)
    print(f"Done in {time.time() - started:.0f} s: {len(meta['sessions'])} sessions, "
          f"{len(meta['forecasts'])} forecasts, pending {meta['pending'] or 'none'}.")


if __name__ == "__main__":
    main()
