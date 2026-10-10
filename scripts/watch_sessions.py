"""
Keeps the site up to date every few minutes over a race weekend. Standard library only, like needs_update.py.

GitHub starts scheduled runs late or not at all when it's busy (2026 Singapore: asked for every
10 minutes, it ran four times on the Friday, so FP1 went up two hours after the flag). So over a
race weekend the workflow keeps one "watch" run going, which it starts itself (workflow_dispatch:
the only trigger the workflow's own token may fire):

    python scripts/watch_sessions.py URL [--since ISO]    wait until the site needs a rebuild
    python scripts/watch_sessions.py URL --chain          is it a race weekend? (start the next watch)

Waiting: every POLL_SECONDS it asks needs_update.py's question (a finished session not on the site,
a provisional result, a new stewards' decision or FIA power-unit document, f1penalties.com's rows,
a power-unit penalty in the news, ...), each source no more often than needs_update's own limits
(the FIA's documents every 15 minutes, f1penalties.com every hour). Writes update=true to
$GITHUB_OUTPUT as soon as the answer is yes, update=false after MAX_MINUTES (a job may run 6 h);
either way the workflow then starts the next watch. Nothing is rebuilt unless something is new.

A session that had certainly finished (LATEST_FINISH_H) before the export that started this watch
began (`--since`) isn't a reason here: that export couldn't load it (a cancelled session), and the
hourly scheduled runs keep trying, without a rebuild every few minutes.

The weekend: from LEAD_HOURS before a round's first session until its last could have finished.
The rest of the week the hourly schedule does the same check.
"""
import argparse
import contextlib
import datetime as dt
import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import needs_update as nu  # noqa: E402

LEAD_HOURS = 12        # a watch starts this long before a weekend's first session
POLL_SECONDS = 300     # check this often
MAX_MINUTES = 330      # then hand over to the next watch (a job may run 6 h)
SINCE_WAIT_MINUTES = 15  # how long to wait for GitHub Pages to serve the export that started this watch


def read_meta(url: str) -> dict:
    # A query string gets past GitHub Pages' 10-minute cache.
    with urllib.request.urlopen(f"{url}?t={int(time.time())}", timeout=30) as r:
        return json.load(r)


def weekend_end(meta: dict, now: dt.datetime) -> dt.datetime | None:
    """When the race weekend that's on (or starts within LEAD_HOURS) is over, or None."""
    by_round: dict[str, list] = {}
    for sid, _, code, start in nu.sessions(meta):
        by_round.setdefault(sid[:3], []).append((start, start + dt.timedelta(hours=nu.LATEST_FINISH_H[code])))
    for spans in by_round.values():
        first, last = min(s for s, _ in spans), max(e for _, e in spans)
        if first - dt.timedelta(hours=LEAD_HOURS) <= now <= last:
            return last
    return None


def reasons(meta: dict, now: dt.datetime, since: dt.datetime | None) -> list[str]:
    """needs_update's reasons to rebuild, less sessions certainly over before the last export began."""
    with contextlib.redirect_stdout(io.StringIO()):     # its "hasn't finished yet" lines, every 5 minutes
        why = nu.reasons(meta, now)
    if since:
        stale = {sid for sid, _, code, start in nu.sessions(meta)
                 if start + dt.timedelta(hours=nu.LATEST_FINISH_H[code]) < since}
        why = [w for w in why if w.split()[0] not in stale]
    return why


def output(**values: str) -> None:
    for k, v in values.items():
        print(f"{k}={v}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.writelines(f"{k}={v}\n" for k, v in values.items())


def wait(url: str, since: dt.datetime | None) -> None:
    stop = time.monotonic() + MAX_MINUTES * 60
    meta = read_meta(url)
    # The export that started this watch may not be served yet: its sessions would look unpublished.
    give_up = time.monotonic() + SINCE_WAIT_MINUTES * 60
    while since and dt.datetime.fromisoformat(meta["generated"]) < since and time.monotonic() < give_up:
        time.sleep(30)
        meta = read_meta(url)
    while True:
        now = dt.datetime.now(dt.timezone.utc)
        if weekend_end(meta, now) is None:
            print("No race weekend on.")
            return output(update="false")
        why = reasons(meta, now, since)
        if why:
            for line in why:
                print(f"- {line}")
            return output(update="true")
        left = stop - time.monotonic()
        if left <= 0:
            print(f"Nothing new in {MAX_MINUTES} min.")
            return output(update="false")
        print(f"{now:%a %H:%M} UTC: nothing new", flush=True)
        time.sleep(min(POLL_SECONDS, left))
        try:
            meta = read_meta(url)   # a scheduled run may have published meanwhile
        except Exception as exc:  # noqa: BLE001 — the site blipped: keep the copy we have
            print(f"  (couldn't read meta.json: {exc})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--since", default="", help="when the export that started this watch began (ISO, UTC)")
    ap.add_argument("--chain", action="store_true", help="only say whether a race weekend is on (watch=true|false)")
    args = ap.parse_args()
    if args.chain:
        try:
            end = weekend_end(read_meta(args.url), dt.datetime.now(dt.timezone.utc))
        except Exception as exc:  # noqa: BLE001 — no site yet: the scheduled runs take over
            print(f"Couldn't read {args.url}: {exc}")
            end = None
        print(f"Race weekend on until {end:%a %d %b %H:%M} UTC." if end else "No race weekend on.")
        return output(watch="true" if end else "false")
    wait(args.url, dt.datetime.fromisoformat(args.since) if args.since else None)


if __name__ == "__main__":
    main()
