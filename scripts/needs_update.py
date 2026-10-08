"""
Does the published site need a rebuild? Standard library only, so the scheduled
GitHub Action can ask cheaply before installing anything.

    python scripts/needs_update.py https://amparaj.github.io/f1-stratbox/data/meta.json

Prints "yes" or "no" (and writes `update=true|false` to $GITHUB_OUTPUT when set). Yes when:
  * the site has no meta.json yet;
  * a session (Grand Prix, Sprint, Qualifying, Sprint Qualifying: a qualifying result moves the
    race forecasts onto the grid; or a practice session, which moves them too) isn't published but has finished: OpenF1 shows its last
    chequered flag (qualifying shows three; config.session_finished: FINISH_SETTLE_MIN ago),
    or LATEST_FINISH_H have passed since the
    start without one. It's checked from EARLIEST_FINISH_MIN after the start, until RETRY_DAYS
    (so a cancelled session doesn't retry for ever);
  * a session is published but still provisional (no official classification or grid yet), or
    the last export couldn't load some sessions ("pending": a rate limit on a cold start, or
    data not out yet), and the last export is REFRESH_MINUTES old;
  * the next race is within WEATHER_DAYS and its weather forecast (Open-Meteo, in the
    strategy forecast) is WEATHER_REFRESH_HOURS old;
  * the FIA's documents of its latest event have a new power-unit document (a "New PU elements"
    document is an announced grid penalty) or stewards' decision (a grid drop or pit-lane start
    moves the forecasts at once: a penalty between practice and qualifying is in the qualifying-
    based race odds), checked once the export is FIA_CHECK_MINUTES old; modules/penalties.py;
  * f1penalties.com's rows for the season changed (it catches up on the FIA days to weeks late),
    checked once the export is PENALTY_CHECK_MINUTES old;
  * a news feed has a power-unit penalty headline from the last NEWS_PLAN_DAYS that the export
    didn't have (meta.json "news"; a reported penalty moves the forecasts), checked once the export
    is NEWS_CHECK_MINUTES old; and in any case once it's NEWS_REFRESH_HOURS old (the rest of the news);
  * the site's data is more than MAX_AGE_DAYS old (calendar changes, code fixes).
"""
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Copies of config.py's finish rules (this script runs before anything is installed).
EARLIEST_FINISH_MIN = {"R": 75, "S": 25, "Q": 55, "SQ": 40, "FP1": 55, "FP2": 55, "FP3": 55}
CHEQUERED_FLAGS = {"R": 1, "S": 1, "Q": 3, "SQ": 3, "FP1": 1, "FP2": 1, "FP3": 1}
FINISH_SETTLE_MIN = 5
LATEST_FINISH_H = {"R": 6, "S": 6, "Q": 3, "SQ": 3, "FP1": 3, "FP2": 3, "FP3": 3}
SESSION_NAMES = {"R": ("Race",), "S": ("Sprint",), "Q": ("Qualifying",), "SQ": ("Sprint Qualifying", "Sprint Shootout"),
                 "FP1": ("Practice 1",), "FP2": ("Practice 2",), "FP3": ("Practice 3",)}
UTC_KEY = {"SQ": "sprint_quali_utc", "S": "sprint_utc", "Q": "quali_utc", "R": "race_utc",
           "FP1": "fp1_utc", "FP2": "fp2_utc", "FP3": "fp3_utc"}
# The news (modules/news.py: power-unit penalties, upgrades) moves between sessions too.
NEWS_REFRESH_HOURS = 12
NEWS_CHECK_MINUTES = 20
# Copies of config.NEWS_FEEDS / NEWS_PLAN_DAYS and modules/news.py's power-unit penalty test (keep them in step).
NEWS_FEEDS = {
    "The Race": "https://www.the-race.com/feed/",
    "Crash.net": "https://www.crash.net/rss/f1",
    "Autosport": "https://www.autosport.com/rss/f1/news/",
    "Motorsport.com": "https://www.motorsport.com/rss/f1/news/",
    "Formula1.com": "https://www.formula1.com/en/latest/all.xml",
    "BBC Sport": "https://feeds.bbci.co.uk/sport/formula1/rss.xml",
    "Sky Sports": "https://www.skysports.com/rss/12433",
    "GPFans": "https://www.gpfans.com/en/rss.xml",
}
NEWS_PLAN_DAYS = 10
NEWS_PU = re.compile(r"\b(engine|power[ -]?units?|PU|ICE|turbo(charger)?|MGU-?[KH]|energy store|gearbox|ADUO)\b", re.I)
NEWS_PU_PENALTY = re.compile(r"grid (penalty|drop)|engine penalty|back of the grid|pit[ -]lane start|penalt", re.I)
NEWS_NEGATION = re.compile(r"\b(won't|will not|not (?:take|get|face|receive|need)|avoid\w*|escape\w*|no (?:grid )?penalty|"
                           r"rule[sd]? out|dodge\w*)\b", re.I)
RETRY_DAYS = 4
REFRESH_MINUTES = 50
MAX_AGE_DAYS = 7
WEATHER_DAYS = 4
WEATHER_REFRESH_HOURS = 6
OPENF1 = "https://api.openf1.org/v1/"
# Copies of modules/penalties.py's (keep them in step): f1penalties.com's CSV export, the FIA's
# documents page (it lists the latest event's) and the power-unit documents' file names.
PENALTY_CHECK_MINUTES = 60
FIA_CHECK_MINUTES = 15
F1PEN_URL = "https://www.f1penalties.com/_dash-update-component"
F1PEN_EXPORT = {"output": "download-csv.data", "outputs": {"id": "download-csv", "property": "data"},
                "inputs": [{"id": "btn-export-csv", "property": "n_clicks", "value": 1}],
                "changedPropIds": ["btn-export-csv.n_clicks"], "state": []}
FIA_ROOT = "https://www.fia.com"
FIA_F1 = FIA_ROOT + "/documents/championships/fia-formula-one-world-championship-14"
PU_DOC = re.compile(r"(pu[ _]elements[ _]used|new[ _]pu[ _]elements)", re.I)
STEWARDS_DOC = re.compile(r"(infringement|decision|offence)[^/]*?car[ _-]*\d", re.I)
HEADERS = {"User-Agent": "Mozilla/5.0 (f1-stratbox; +https://amparaj.github.io/f1-stratbox/)"}


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


def stewards_fingerprint(year: int) -> str | None:
    """penalties.stewards_fingerprint, from a fresh export of f1penalties.com."""
    req = urllib.request.Request(F1PEN_URL, data=json.dumps(F1PEN_EXPORT).encode(),
                                 headers={**HEADERS, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = json.load(r)["response"]["download-csv"]["data"]["content"]
    except Exception as exc:  # noqa: BLE001 — site down: try again next run
        print(f"  (f1penalties.com: {exc})")
        return None
    lines = sorted(line.strip() for line in text.splitlines() if line.startswith(f"{year},"))
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()[:16]


def fia_pu_docs() -> set[str] | None:
    """The power-unit documents and stewards' decisions on the FIA's page (its latest event's), as full URLs."""
    try:
        with urllib.request.urlopen(urllib.request.Request(FIA_F1, headers=HEADERS), timeout=60) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"  (fia.com: {exc})")
        return None
    hrefs = re.findall(r'href="([^"]+\.pdf)"', html)
    return {FIA_ROOT + h if h.startswith("/") else h for h in hrefs
            if PU_DOC.search(h) or STEWARDS_DOC.search(h.rsplit("/", 1)[-1])}


def penalty_reasons(meta: dict, now: dt.datetime, f1penalties: bool = True) -> list[str]:
    """New FIA documents (power-unit or stewards' decisions) and, if `f1penalties`, changed
    f1penalties.com rows."""
    seen = meta.get("penalties")
    if not seen:
        return []
    out = []
    if f1penalties:
        fp = stewards_fingerprint(meta["season"])
        if fp and fp != seen.get("stewards"):
            out.append("f1penalties.com has new or changed decisions this season")
    docs = fia_pu_docs()
    new = sorted(docs - set(seen.get("fia_docs", []))) if docs else []
    if new:
        out.append(f"{len(new)} new FIA document(s) (power units, stewards): {new[0].rsplit('/', 1)[-1]}")
    return out


def _plain(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def news_reasons(meta: dict, now: dt.datetime) -> list[str]:
    """Power-unit penalty headlines in the feeds that the export didn't have (modules/news.py's
    pu_penalty_ids: same id, same sentence test)."""
    seen = meta.get("news")
    if not seen:
        return []
    known = set(seen.get("pu_items", []))
    cutoff = now - dt.timedelta(days=NEWS_PLAN_DAYS)
    new = []
    for source, url in NEWS_FEEDS.items():
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as r:
                root = ET.fromstring(r.read())
        except Exception as exc:  # noqa: BLE001 — one feed down: the others still count
            print(f"  (news {source}: {str(exc)[:80]})")
            continue
        for it in root.iter():
            if it.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
                continue
            get = {c.tag.rsplit("}", 1)[-1]: c for c in it}
            link = get.get("link")
            href = ((link.get("href") or link.text) if link is not None else None) or ""
            title = _plain(get["title"].text if "title" in get else "")
            body = next((get[k].text for k in ("description", "summary") if get.get(k) is not None), "")
            when = next((get[k].text for k in ("pubDate", "published") if get.get(k) is not None), None)
            try:
                d = email.utils.parsedate_to_datetime(when) if when else now
            except (TypeError, ValueError):
                try:
                    d = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
                except ValueError:
                    d = now
            d = d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
            if not (title and href) or d < cutoff:
                continue
            text = title + ". " + _plain(body)[:400]
            sentences = [x for x in re.split(r"(?<=[.!?])\s+|\s+\|\s+", text) if x.strip()]
            if any(NEWS_PU.search(x) and NEWS_PU_PENALTY.search(x) and not NEWS_NEGATION.search(x) for x in sentences) \
                    and hashlib.sha1(href.strip().encode()).hexdigest()[:12] not in known:
                new.append(f"{source}: {title}")
    return [f"{len(new)} new power-unit penalty headline(s): {new[0]}"] if new else []


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
    elif age > dt.timedelta(hours=NEWS_REFRESH_HOURS):
        out.append(f"the news is {age.total_seconds() / 3600:.0f} h old")
    if not out and age > dt.timedelta(minutes=NEWS_CHECK_MINUTES):
        out += news_reasons(meta, now)
    if not out and age > dt.timedelta(minutes=FIA_CHECK_MINUTES):
        out += penalty_reasons(meta, now, f1penalties=age > dt.timedelta(minutes=PENALTY_CHECK_MINUTES))
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
