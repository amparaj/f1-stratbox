"""
Does the published site need a rebuild? Standard library only, so the scheduled
GitHub Action can ask cheaply before installing anything.

    python scripts/needs_update.py https://amparaj.github.io/f1-stratbox/data/meta.json

Prints "yes" or "no" (and writes `update=true|false` to $GITHUB_OUTPUT when set). Yes when:
  * the site has no meta.json yet;
  * a race or sprint that should have finished by now (start + 3 h) isn't published, or
    is published but still provisional (no official classification yet), and started
    less than RETRY_DAYS ago (so a cancelled session doesn't retry for ever);
  * the last export couldn't load some sessions ("pending": FastF1's request limit on a cold
    start, or data not out yet);
  * the site's data is more than MAX_AGE_DAYS old (calendar changes, code fixes).
"""
import datetime as dt
import json
import os
import sys
import urllib.request

FINISHED_AFTER = dt.timedelta(hours=3)
RETRY_DAYS = 4
MAX_AGE_DAYS = 7


def reasons(meta: dict | None, now: dt.datetime) -> list[str]:
    if meta is None:
        return ["nothing published yet"]
    out = []
    published = {s["id"]: s for s in meta["sessions"]}
    for ev in meta["calendar"]:
        for code, key in (("S", "sprint_utc"), ("R", "race_utc")):
            if not ev.get(key):
                continue
            start = dt.datetime.fromisoformat(ev[key])
            sid = f"r{ev['round']:02d}-{code}"
            if not start + FINISHED_AFTER < now < start + dt.timedelta(days=RETRY_DAYS):
                continue
            if sid not in published:
                out.append(f"{sid} {ev['event']} finished but isn't published")
            elif not published[sid]["complete"]:
                out.append(f"{sid} {ev['event']} is still provisional")
    if meta.get("pending"):
        out.append(f"{len(meta['pending'])} sessions still to download: {', '.join(meta['pending'][:5])}")
    age = now - dt.datetime.fromisoformat(meta["generated"])
    if age > dt.timedelta(days=MAX_AGE_DAYS):
        out.append(f"data is {age.days} days old")
    return out


def main() -> None:
    url = sys.argv[1]
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            meta = json.load(r)
    except Exception as exc:  # noqa: BLE001 — 404 on the first run, or the site is down
        print(f"Couldn't read {url}: {exc}")
        meta = None
    why = reasons(meta, dt.datetime.now(dt.timezone.utc))
    for line in why:
        print(f"- {line}")
    print("yes" if why else "no")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"update={'true' if why else 'false'}\n")


if __name__ == "__main__":
    main()
