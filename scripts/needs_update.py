"""
Does the published site need a rebuild? Standard library only, so the scheduled
GitHub Action can ask cheaply before installing anything.

    python scripts/needs_update.py https://amparaj.github.io/f1-stratbox/data/meta.json

Prints "yes" or "no" (and writes `update=true|false` to $GITHUB_OUTPUT when set). Yes when:
  * the site has no meta.json yet;
  * a session (Grand Prix, Sprint, Qualifying, Sprint Qualifying: a qualifying result moves the
    race forecasts onto the grid) isn't published but has finished: OpenF1 shows its last
    chequered flag (qualifying shows three; config.session_finished: FINISH_SETTLE_MIN ago),
    or LATEST_FINISH_H have passed since the
    start without one. It's checked from EARLIEST_FINISH_MIN after the start, until RETRY_DAYS
    (so a cancelled session doesn't retry for ever);
  * a session is published but still provisional (no official classification or grid yet), or
    the last export couldn't load some sessions ("pending": a rate limit on a cold start, or
    data not out yet), and the last export is REFRESH_MINUTES old;
  * the next race is within WEATHER_DAYS and its weather forecast (Open-Meteo, in the
    strategy forecast) is WEATHER_REFRESH_HOURS old;
  * the site's data is more than MAX_AGE_DAYS old (calendar changes, code fixes).
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Copies of config.py's finish rules (this script runs before anything is installed).
EARLIEST_FINISH_MIN = {"R": 75, "S": 25, "Q": 55, "SQ": 40}
CHEQUERED_FLAGS = {"R": 1, "S": 1, "Q": 3, "SQ": 3}
FINISH_SETTLE_MIN = 5
LATEST_FINISH_H = {"R": 6, "S": 6, "Q": 3, "SQ": 3}
SESSION_NAMES = {"R": ("Race",), "S": ("Sprint",), "Q": ("Qualifying",), "SQ": ("Sprint Qualifying", "Sprint Shootout")}
UTC_KEY = {"SQ": "sprint_quali_utc", "S": "sprint_utc", "Q": "quali_utc", "R": "race_utc"}
RETRY_DAYS = 4
REFRESH_MINUTES = 50
MAX_AGE_DAYS = 7
WEATHER_DAYS = 4
WEATHER_REFRESH_HOURS = 6
OPENF1 = "https://api.openf1.org/v1/"


def _get_json(path: str, **params):
    url = OPENF1 + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "f1-stratbox"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:      # OpenF1's "No results found."
            return []
        raise


def chequered(code: str, start: dt.datetime) -> dt.datetime | None:
    """When OpenF1 shows the flag that ends the session (qualifying: the third), or None."""
    try:
        sessions = [s for name in SESSION_NAMES[code] for s in _get_json("sessions", year=start.year, session_name=name)]
        near = [s for s in sessions
                if abs(dt.datetime.fromisoformat(s["date_start"]) - start) < dt.timedelta(hours=6)]
        if not near:
            return None
        msgs = _get_json("race_control", session_key=near[0]["session_key"], flag="CHEQUERED")
    except Exception as exc:  # noqa: BLE001 — OpenF1 down or live-only: try again next run
        print(f"  (OpenF1: {exc})")
        return None
    times = sorted(dt.datetime.fromisoformat(m["date"]) for m in msgs if m.get("date"))
    flags = [t for i, t in enumerate(times) if i == 0 or t - times[i - 1] > dt.timedelta(minutes=1)]
    n = CHEQUERED_FLAGS[code]
    return flags[n - 1] if len(flags) >= n else None


def finished(code: str, start: dt.datetime, now: dt.datetime) -> bool:
    if now >= start + dt.timedelta(hours=LATEST_FINISH_H[code]):
        return True
    flag = chequered(code, start)
    return flag is not None and now >= flag + dt.timedelta(minutes=FINISH_SETTLE_MIN)


def reasons(meta: dict | None, now: dt.datetime) -> list[str]:
    if meta is None:
        return ["nothing published yet"]
    out = []
    published = {s["id"]: s for s in meta["sessions"]}
    age = now - dt.datetime.fromisoformat(meta["generated"])
    refresh = age > dt.timedelta(minutes=REFRESH_MINUTES)
    for ev in meta["calendar"]:
        for code, key in UTC_KEY.items():
            if not ev.get(key):
                continue
            start = dt.datetime.fromisoformat(ev[key])
            sid = f"r{ev['round']:02d}-{code}"
            if not start + dt.timedelta(minutes=EARLIEST_FINISH_MIN[code]) < now < start + dt.timedelta(days=RETRY_DAYS):
                continue
            if sid not in published:
                if finished(code, start, now):
                    out.append(f"{sid} {ev['event']} finished but isn't published")
                else:
                    print(f"  {sid} {ev['event']} hasn't finished yet")
            elif not published[sid]["complete"] and refresh:
                out.append(f"{sid} {ev['event']} is still provisional")
    if meta.get("pending") and refresh:
        out.append(f"{len(meta['pending'])} sessions still to download: {', '.join(meta['pending'][:5])}")
    upcoming = [dt.datetime.fromisoformat(e["race_utc"]) for e in meta["calendar"]
                if e.get("race_utc") and not e.get("done_R")]
    soon = [t for t in upcoming if now < t < now + dt.timedelta(days=WEATHER_DAYS)]
    if soon and age > dt.timedelta(hours=WEATHER_REFRESH_HOURS):
        out.append(f"the weather forecast for the race on {min(soon):%a %d %b} is {age.total_seconds() / 3600:.0f} h old")
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
