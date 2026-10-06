"""
modules/penalties.py — Stewards' penalties, power-unit usage and the likely-penalty model.

Two free sources:

* **f1penalties.com/data**: every stewards' decision since 2020 (time, grid and drive-through
  penalties, power-unit and parc fermé grid drops, reprimands, fines, penalty points), from the
  CSV export of its Dash app (`_dash-update-component`, the "Export CSV" button). It lags the
  FIA by a few days to a few weeks, so it's fetched again every PENALTY_FRESH_HOURS; a season
  is archived (archive/f1penalties/<year>.csv.gz) once it's over (from ARCHIVE_AFTER the next
  year), and read from there for good.
* **FIA decision documents** (fia.com/documents): at every event the Technical Delegate
  publishes "PU elements used per driver up to now" (each driver's count of every element
  before the event) and "New PU elements for this Competition" (what's fitted there, with the
  allocation: "the fifth (5th) of the four (4) new internal combustion engines allowed"). Both
  are PDFs, read with pypdf; drivers are matched by car number. An event is archived
  (archive/fia/<year>/rNN-pu.json.gz, the parsed tables and their source URLs) FIA_FINAL_DAYS
  after its race.

The model (`penalty_risk`, constants in config.py "Power-unit penalties", fitted by
scripts/calibrate_penalties.py on every round since 2022): the chance a driver takes an
over-allocation element at each Grand Prix left, from how far their projected use overshoots
the allocation, how much of the season is left and how often teams have chosen that circuit.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import math
import re
import time
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import config

F1PEN_URL = "https://www.f1penalties.com/_dash-update-component"
F1PEN_CACHE = config.PROJECT_ROOT / ".f1penalties"
F1PEN_ARCHIVE = config.PROJECT_ROOT / "archive" / "f1penalties"
FIA_ROOT = "https://www.fia.com"
FIA_F1 = FIA_ROOT + "/documents/championships/fia-formula-one-world-championship-14"
FIA_CACHE = config.PROJECT_ROOT / ".fia"
FIA_ARCHIVE = config.PROJECT_ROOT / "archive" / "fia"
FIA_FIRST_YEAR = 2022          # the PU documents in this form (and the f1penalties data) start here
FIA_FINAL_DAYS = 4             # an event's documents are final this long after its race
ARCHIVE_AFTER = (1, 15)        # a season's penalties are final from 15 January of the next year
HEADERS = {"User-Agent": "Mozilla/5.0 (f1-stratbox; +https://amparaj.github.io/f1-stratbox/)"}
MIN_INTERVAL_S = 1.0

# Power-unit elements: the FIA's abbreviations, as they appear in its tables, to one name each.
ELEMENTS = {"ICE": "ICE", "TC": "TC", "MGU-H": "MGU-H", "MGU-K": "MGU-K", "ES": "ES", "CE": "CE",
            "PU-CE": "CE", "EX": "EX", "EXH": "EX", "PU-ANC": "ANC"}
ELEMENT_NAMES = {"ICE": "Internal combustion engine", "TC": "Turbocharger", "MGU-H": "MGU-H",
                 "MGU-K": "MGU-K", "ES": "Energy store", "CE": "Control electronics", "EX": "Exhaust",
                 "ANC": "PU ancillaries"}

_last_request = 0.0


# ---------------------------------------------------------------------------
# HTTP with a disk cache
# ---------------------------------------------------------------------------
def _fetch(url: str, cache: Path, max_age: dt.timedelta | None, data: dict | None = None) -> bytes:
    """GET (or POST `data` as JSON) through a disk cache; max_age None = never expires. On a
    failure a stale copy is used if there is one."""
    global _last_request
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1((url + json.dumps(data, sort_keys=True)).encode()).hexdigest()[:20]
    path, stamp = cache / f"{key}.bin", cache / f"{key}.fetched"
    if path.exists() and stamp.exists():
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp.read_text())
        if max_age is None or age < max_age:
            return path.read_bytes()
    try:
        for attempt in range(4):
            wait = MIN_INTERVAL_S - (time.time() - _last_request)
            if wait > 0:
                time.sleep(wait)
            _last_request = time.time()
            r = (requests.post(url, json=data, headers=HEADERS, timeout=60) if data is not None
                 else requests.get(url, headers=HEADERS, timeout=60))
            if r.status_code in (429, 502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        else:
            r.raise_for_status()
    except requests.RequestException:
        if path.exists():
            return path.read_bytes()
        raise
    path.write_bytes(r.content)
    stamp.write_text(dt.datetime.now(dt.timezone.utc).isoformat())
    return r.content


def _write_gz(path: Path, body: bytes) -> None:
    """Deterministic gzip (no timestamp), so a rewrite is never a git change."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))


def name_key(name: str) -> str:
    """A driver's name for matching across sources: letters only, no accents, no spaces (the FIA's
    PDFs break names: "Geor ge Russell", "H?lkenber g")."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


# ---------------------------------------------------------------------------
# f1penalties.com
# ---------------------------------------------------------------------------
_EXPORT = {"output": "download-csv.data", "outputs": {"id": "download-csv", "property": "data"},
           "inputs": [{"id": "btn-export-csv", "property": "n_clicks", "value": 1}],
           "changedPropIds": ["btn-export-csv.n_clicks"], "state": []}


def _season_final(year: int, today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    return today >= dt.date(year + 1, *ARCHIVE_AFTER)


def _f1penalties_csv(max_age: dt.timedelta) -> str:
    body = _fetch(F1PEN_URL, F1PEN_CACHE, max_age, data=_EXPORT)
    return json.loads(body)["response"]["download-csv"]["data"]["content"]


def _grid_drop(v) -> int | str | None:
    """f1penalties' grid penalty: places (int), "pit" (pit-lane start) or "back" (back of the grid)."""
    if v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() == "":
        return None
    s = str(v).strip().lower()
    if "pit" in s:
        return "pit"
    if "back" in s:
        return "back"
    try:
        return int(float(s))
    except ValueError:
        return None


def stewards_decisions(years: list[int] | None = None, max_age_h: float | None = None) -> pd.DataFrame:
    """
    Every stewards' decision in `years` (default: all), from f1penalties.com: archived seasons from
    archive/f1penalties/, the rest from a fresh export (at most PENALTY_FRESH_HOURS old). Columns:
    year, round, race, driver (full name), name_key, team, session (FP1..R), allegation, detail,
    involving, outcome, time_s, fine, grid (places / "pit" / "back"), points, notes.
    Archives any season that's now final. Empty if the site is down and nothing is cached.
    """
    max_age = dt.timedelta(hours=max_age_h if max_age_h is not None else config.PENALTY_FRESH_HOURS)
    want = set(years) if years else None
    frames, live = [], None
    archived = {int(p.name[:4]) for p in F1PEN_ARCHIVE.glob("*.csv.gz")}
    for y in sorted(archived):
        if want is None or y in want:
            frames.append(pd.read_csv(io.BytesIO(gzip.decompress((F1PEN_ARCHIVE / f"{y}.csv.gz").read_bytes()))))
    if want is None or want - archived:
        try:
            text = _f1penalties_csv(max_age)
        except Exception as exc:  # noqa: BLE001 — site down, nothing cached: no penalties
            print(f"  f1penalties.com: {str(exc)[:120]}", flush=True)
            text = None
        if text:
            live = pd.read_csv(io.StringIO(text))
            for y, rows in live.groupby("Year"):
                if int(y) not in archived and _season_final(int(y)):
                    buf = io.StringIO()
                    rows.to_csv(buf, index=False, lineterminator="\n")
                    _write_gz(F1PEN_ARCHIVE / f"{int(y)}.csv.gz", buf.getvalue().encode())
                    print(f"  archived f1penalties {int(y)}", flush=True)
            live = live[~live["Year"].isin(archived)]
            if want is not None:
                live = live[live["Year"].isin(want)]
            frames.append(live)
    if not frames:
        return pd.DataFrame(columns=["year", "round", "race", "driver", "name_key", "team", "session",
                                     "allegation", "detail", "involving", "outcome", "time_s", "fine",
                                     "grid", "points", "notes"])
    df = pd.concat(frames, ignore_index=True)
    out = pd.DataFrame({
        "year": df["Year"].astype(int), "round": df["Round"].astype(int), "race": df["Race"],
        "driver": df["Driver"], "name_key": df["Driver"].map(name_key), "team": df["Team"],
        "session": df["Session"], "allegation": df["Allegation"],
        "detail": df["Allegation_Raw"], "involving": df["Incident involving"], "outcome": df["Outcome"],
        "time_s": pd.to_numeric(df["Time Penalty (in seconds)"], errors="coerce"),
        "fine": pd.to_numeric(df["Fine"], errors="coerce"),
        "grid": df["Grid Penalty"].map(_grid_drop),
        "points": pd.to_numeric(df["Penalty Points"], errors="coerce"),
        "notes": df["Notes"],
    })
    return out.sort_values(["year", "round"], kind="stable").reset_index(drop=True)


def stewards_fingerprint(year: int) -> str | None:
    """A hash of `year`'s rows in f1penalties.com's export (the cached copy), so
    scripts/needs_update.py can tell when the site has added or changed any (it keeps a copy)."""
    try:
        text = _f1penalties_csv(dt.timedelta(hours=config.PENALTY_FRESH_HOURS))
    except Exception:  # noqa: BLE001
        return None
    lines = sorted(line.strip() for line in text.splitlines() if line.startswith(f"{year},"))
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()[:16]


# The FIA's two power-unit documents of an event, by their file name (scripts/needs_update.py keeps a copy).
PU_DOC = re.compile(r"(pu[ _]elements[ _]used|new[ _]pu[ _]elements)", re.I)


PU_ALLEGATIONS = ("New power unit element(s)",)
PARC_FERME_ALLEGATIONS = ("Changes made under Parc Ferme", "Parc Ferme Infringement")


def kind_of(allegation: str, detail=None) -> str:
    """'pu' (power unit / gearbox: a new element over the allocation), 'parc_ferme' or 'other'."""
    a = str(allegation)
    if a in PU_ALLEGATIONS or "gearbox" in str(detail).lower():
        return "pu"
    if a in PARC_FERME_ALLEGATIONS:
        return "parc_ferme"
    return "other"


# ---------------------------------------------------------------------------
# FIA documents
# ---------------------------------------------------------------------------
def _html(url: str, max_age: dt.timedelta | None) -> str:
    return _fetch(url, FIA_CACHE, max_age).decode("utf-8", "replace")


def _season_pages(max_age: dt.timedelta) -> dict[int, str]:
    """{season: its documents page}, from the FIA's season menu (the current season is the root)."""
    html = _html(FIA_F1, max_age)
    out = {}
    for path, label in re.findall(r'<option value="(/documents/championships/fia-formula-one-world-championship-14[^"]*)">'
                                  r'SEASON (\d{4})</option>', html):
        out[int(label)] = FIA_ROOT + path
    return out


def _events(season_url: str, max_age: dt.timedelta | None) -> dict[str, str]:
    """{event name: its documents page} for one season."""
    html = _html(season_url, max_age)
    return {re.sub(r"\s+", " ", name).strip(): FIA_ROOT + path
            for path, name in re.findall(r'<option value="([^"]*/event/[^"]+)">([^<]+)</option>', html)}


def _documents(event_url: str, max_age: dt.timedelta | None) -> list[dict]:
    """Every document on an event page: {title, url, published (UTC)}."""
    html = _html(event_url, max_age)
    docs = []
    for block in html.split('<li class="document-row')[1:]:
        href = re.search(r'href="([^"]+\.pdf)"', block)
        title = re.search(r'<div class="title">\s*(.*?)\s*</div>', block, re.S)
        when = re.search(r'date-display-single">(\d\d)\.(\d\d)\.(\d\d) (\d\d):(\d\d)<', block)
        if not href:
            continue
        name = re.sub(r"<[^>]+>|\s+", " ", title.group(1)).strip() if title else Path(href.group(1)).stem
        published = None
        if when:
            d, m, y, hh, mm = map(int, when.groups())
            # Central European time: +1 h in winter, +2 h in summer; an hour off doesn't matter here.
            published = (dt.datetime(2000 + y, m, d, hh, mm) - dt.timedelta(hours=2)).replace(tzinfo=dt.timezone.utc)
        docs.append({"title": name, "url": FIA_ROOT + href.group(1) if href.group(1).startswith("/") else href.group(1),
                     "published": published})
    return docs


def _pdf_text(url: str) -> str:
    """A PDF's text (cached for good: a published document doesn't change under its URL)."""
    from pypdf import PdfReader     # only the export needs it
    key = hashlib.sha1(url.encode()).hexdigest()[:20]
    txt = FIA_CACHE / f"{key}.txt"
    if txt.exists():
        return txt.read_text(encoding="utf-8")
    body = _fetch(url, FIA_CACHE, None)
    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(body)).pages)
    txt.write_text(text, encoding="utf-8")
    return text


_ROW = re.compile(r"^\s*(\d{1,2})\s+(.+?)((?:\s+\d{1,2})+)\s*$")


def _header_elements(text: str) -> list[str]:
    """The element columns of a usage table, in order (its header is split across lines)."""
    # "Car Driver ICE TC ...": the cover page's title also says "per Driver". The PDFs' text can
    # break inside a word ("Ca r Drive r").
    m = re.search(r"C\s*a\s*r\s+D\s*r\s*i\s*v\s*e\s*r(.*?)\n\s*\d{1,2}\s+\S", text, re.S)
    if not m:
        return []
    head = re.sub(r"\s+", "", m.group(1)).upper()
    found, i = [], 0
    abbrs = sorted(ELEMENTS, key=len, reverse=True)
    while i < len(head):
        a = next((a for a in abbrs if head.startswith(a, i)), None)
        if a is None:
            i += 1
            continue
        found.append(ELEMENTS[a])
        i += len(a)
    return found


def parse_usage(text: str) -> dict[int, dict]:
    """"PU elements used per driver up to now": {car number: {"who": team + driver text, "used":
    {element: count}}}."""
    elements = _header_elements(text)
    out = {}
    if not elements:
        return out
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        counts = [int(x) for x in m.group(3).split()]
        if len(counts) < len(elements):
            continue
        counts = counts[-len(elements):]
        out[int(m.group(1))] = {"who": m.group(2).strip(), "used": dict(zip(elements, counts))}
    return out


_SECTION = re.compile(r"with an? new ([^()\n]+?)\s*\(([A-Z][A-Z\-\s]*?)\)\s*:", re.S)
_LIMIT = re.compile(r"of the \w+ \((\d+)\) new", re.I)


def parse_new(text: str) -> dict:
    """"New PU elements for this Competition": {"new": [[car, element, previously used]],
    "limits": {element: allocation}}."""
    new, limits = [], {}
    parts = _SECTION.split(text)
    # parts: [before, name, abbr, body, name, abbr, body, ...]
    for i in range(1, len(parts) - 2, 3):
        abbr = re.sub(r"\s+", "", parts[i + 1]).upper()
        el = ELEMENTS.get(abbr)
        if el is None:
            continue
        body = parts[i + 2]
        for line in body.splitlines():
            m = re.match(r"^\s*(\d{1,2})\s+\D.*?\s(\d{1,2})\s*$", line)
            if m:
                new.append([int(m.group(1)), el, int(m.group(2))])
        lim = _LIMIT.search(body)
        if lim:
            limits[el] = int(lim.group(1))
    return {"new": new, "limits": limits}


def _schedule(year: int) -> pd.DataFrame:
    import fastf1     # the calendar; FastF1's cache keeps it
    s = fastf1.get_event_schedule(year, include_testing=False)
    s = s[s["RoundNumber"] > 0]
    return pd.DataFrame({"round": s["RoundNumber"].astype(int), "event": s["EventName"],
                         "start": pd.to_datetime(s["Session1DateUtc"]).dt.tz_localize("UTC"),
                         "race": pd.to_datetime(s["Session5DateUtc"]).dt.tz_localize("UTC")})


def _round_of(docs: list[dict], sched: pd.DataFrame) -> int | None:
    """The round an event's documents belong to: the event whose weekend they were published in."""
    times = sorted(d["published"] for d in docs if d["published"] is not None)
    if not times:
        return None
    t = times[len(times) // 2]
    hit = sched[(sched["start"] - pd.Timedelta(days=4) <= t) & (t <= sched["race"] + pd.Timedelta(days=3))]
    return int(hit["round"].iloc[0]) if len(hit) else None


def fia_archive_path(year: int, rnd: int) -> Path:
    return FIA_ARCHIVE / str(year) / f"r{rnd:02d}-pu.json.gz"


def _event_pu(docs: list[dict]) -> dict:
    """Parse an event's PU documents: usage before it, new elements (every version), allocations."""
    used = next((d for d in docs if re.search(r"PU elements used per driver", d["title"], re.I)), None)
    news = [d for d in docs if re.search(r"New PU elements for this (Competition|Event)", d["title"], re.I)]
    out = {"usage": {}, "new": [], "limits": {}, "sources": []}
    if used:
        out["usage"] = {str(k): v for k, v in parse_usage(_pdf_text(used["url"])).items()}
        out["sources"].append(used["url"])
    seen = set()
    for d in sorted(news, key=lambda d: (d["published"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc))):
        p = parse_new(_pdf_text(d["url"]))
        out["sources"].append(d["url"])
        out["limits"].update(p["limits"])
        for row in p["new"]:
            if tuple(row) not in seen:
                seen.add(tuple(row))
                out["new"].append(row)
    return out


def pu_season(year: int, now: dt.datetime | None = None) -> dict[int, dict]:
    """
    Every round's power-unit documents for `year` ({round: {"usage", "new", "limits", "sources"}}):
    archived rounds from archive/fia/, the rest from fia.com (event pages FIA_PAGE_FRESH_HOURS
    old at most), archiving those now final. Rounds with no documents yet are left out.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {}
    for p in sorted((FIA_ARCHIVE / str(year)).glob("r*-pu.json.gz")):
        out[int(p.name[1:3])] = json.loads(gzip.decompress(p.read_bytes()))
    sched = _schedule(year)
    due = sched[(sched["start"] - pd.Timedelta(days=2) <= now) & ~sched["round"].isin(out)]
    if due.empty:
        return out
    fresh = dt.timedelta(hours=config.FIA_PAGE_FRESH_HOURS)
    try:
        season_url = _season_pages(fresh).get(year)
        if season_url is None:
            return out
        final_season = now > sched["race"].max() + pd.Timedelta(days=FIA_FINAL_DAYS)
        events = _events(season_url, None if final_season else fresh)
    except Exception as exc:  # noqa: BLE001 — FIA site down: what's archived
        print(f"  fia.com: {str(exc)[:120]}", flush=True)
        return out
    for name, url in events.items():
        try:
            # A cached page is enough to tell which round it is, unless it's a page that had no
            # documents yet or its round isn't final: then it's fetched again when stale.
            docs = _documents(url, None)
            rnd = _round_of(docs, sched)
            if rnd is None and not final_season:
                docs = _documents(url, fresh)
                rnd = _round_of(docs, sched)
            if rnd is None or rnd in out or rnd not in set(due["round"]):
                continue
            race = sched.loc[sched["round"] == rnd, "race"].iloc[0]
            final = now > race + pd.Timedelta(days=FIA_FINAL_DAYS)
            if not final:
                docs = _documents(url, fresh)
            rec = {"year": year, "round": rnd, "event": name, **_event_pu(docs)}
            if not rec["sources"]:
                continue
            out[rnd] = rec
            # A usage table that didn't parse (or isn't out) is never archived: tried again next run.
            if final and (rec["usage"] or rnd == 1):
                _write_gz(fia_archive_path(year, rnd), json.dumps(rec, sort_keys=True, separators=(",", ":")).encode())
        except Exception as exc:  # noqa: BLE001 — one bad document: skip the event, keep the rest
            print(f"  fia.com {year} {name}: {str(exc)[:120]}", flush=True)
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# Power-unit usage, round by round
# ---------------------------------------------------------------------------
def circuit_key(location: str) -> str:
    """site_export.circuit_key (one name per circuit across seasons), without importing it."""
    return config.get_pit_loss(location)[1] or config._normalise(location)


def season_limits(year: int, records: dict[int, dict]) -> dict[str, int]:
    """Each element's allocation for the season: what the FIA's documents state (the most common
    figure), else config.PU_LIMITS."""
    seen: dict[str, list[int]] = {}
    for rec in records.values():
        for el, n in rec.get("limits", {}).items():
            seen.setdefault(el, []).append(int(n))
    out = dict(config.PU_LIMITS.get(year, config.PU_LIMITS[max(config.PU_LIMITS)]))
    out.update({el: max(set(v), key=v.count) for el, v in seen.items()})
    return out


def grid_drop(before: dict[str, int], fitted: dict[str, int], limits: dict[str, int]) -> int | str | None:
    """
    The grid penalty for fitting `fitted` new elements onto `before` (FIA Sporting Regulations):
    the first element of a kind beyond the allocation costs PU_FIRST_DROP places, each one after
    that PU_NEXT_DROP, and more than PU_BACK_OVER in all is the back of the grid. None when it's all
    within the allocation.
    """
    places = 0
    for el, n in fitted.items():
        lim = limits.get(el)
        if not n or lim is None:
            continue
        for k in range(before.get(el, 0) + 1, before.get(el, 0) + n + 1):
            if k == lim + 1:
                places += config.PU_FIRST_DROP
            elif k > lim + 1:
                places += config.PU_NEXT_DROP
    if not places:
        return None
    return "back" if places > config.PU_BACK_OVER else places


def penalty_features(before: dict[str, int], limits: dict[str, int], rnd: int, total_rounds: int,
                     over: bool) -> dict[str, float]:
    """
    The state a round's power-unit call is made in. `deficit`: elements needed beyond the
    allocation to finish the season at the allocation's rate (rounds left × allocation / rounds,
    minus what's left of it; the worst element). `over`: already took an over-allocation element
    this season. `left`: share of the season still to run, this round included.
    """
    left_rounds = total_rounds - rnd + 1
    deficit = max((left_rounds * lim / total_rounds - (lim - before.get(el, 0))
                   for el, lim in limits.items()), default=0.0)
    return {"deficit": float(deficit), "over": float(over), "left": left_rounds / total_rounds}


def usage_table(year: int, records: dict[int, dict], total_rounds: int) -> pd.DataFrame:
    """
    One row per round and car: the elements used before the round (`before`, {element: n}), fitted
    at it (`fitted`), and what that means: `drop` (the grid penalty it earned, grid_drop), `changed`
    (any over-allocation element fitted), and the state the round was decided in (`deficit`,
    `over`, `left`: penalty_features). Rounds after the last document get no row.
    """
    limits = season_limits(year, records)
    rounds = sorted(records)
    usage = {r: {int(c): v["used"] for c, v in records[r].get("usage", {}).items()} for r in rounds}
    who: dict[int, str] = {}
    for r in rounds:
        who.update({int(c): v["who"] for c, v in records[r].get("usage", {}).items()})
    news: dict[int, dict[int, dict[str, int]]] = {}
    for r in rounds:
        for car, el, _prev in records[r].get("new", []):
            per = news.setdefault(r, {}).setdefault(int(car), {})
            per[el] = per.get(el, 0) + 1
    cars = sorted(set(who) | {c for r in news for c in news[r]})
    rows, prev_after, penalised = [], {}, set()
    for r in rounds:
        before_all = usage.get(r) or prev_after
        nxt = usage.get(r + 1)
        for car in cars:
            if r > rounds[0] and not any((before_all.get(car) or {}).values()) \
                    and not any((prev_after.get(car) or {}).values()):
                # New mid-season (a driver taking over a seat): the tables carry the car's counts
                # over, so the jump to them is no new element. Their state starts at the next table.
                if nxt and car in nxt:
                    prev_after[car] = dict(nxt[car])
                continue
            before = before_all.get(car) or prev_after.get(car) or {el: 0 for el in limits}
            if nxt and car in nxt:
                fitted = {el: max(0, nxt[car].get(el, 0) - before.get(el, 0)) for el in limits}
            else:
                fitted = {el: news.get(r, {}).get(car, {}).get(el, 0) for el in limits}
            after = {el: before.get(el, 0) + fitted.get(el, 0) for el in limits}
            drop = grid_drop(before, fitted, limits)
            feats = penalty_features(before, limits, r, total_rounds, car in penalised)
            rows.append({"year": year, "round": r, "car": car, "who": who.get(car, ""), "before": before,
                         "fitted": fitted, "after": after, "drop": drop, "changed": drop is not None, **feats})
            prev_after[car] = after
            if drop is not None:
                penalised.add(car)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# The likely-penalty model
# ---------------------------------------------------------------------------
def pu_penalty_events(decisions: pd.DataFrame) -> pd.DataFrame:
    """One row per driver and race with a power-unit grid penalty (f1penalties has a row per
    element or decision): year, round, race, driver, name_key, grid (summed; "back" past 15)."""
    pu = decisions[decisions["allegation"].isin(PU_ALLEGATIONS)]
    if pu.empty:
        return pd.DataFrame(columns=["year", "round", "race", "driver", "name_key", "grid"])

    def total(s):
        v = list(s)
        if "pit" in v:
            return "pit"
        if "back" in v:
            return "back"
        n = sum(x for x in v if isinstance(x, int))
        return "back" if n > config.PU_BACK_OVER else (n or None)
    return pu.groupby(["year", "round", "race", "driver", "name_key"], as_index=False).agg(grid=("grid", total))


def circuit_factors(events: pd.DataFrame, hosted: pd.DataFrame) -> dict[str, float]:
    """
    How much likelier a power-unit penalty is at each circuit than at the average race, from where
    teams have taken them (`events`: a `circuit` column, one row per penalty; `hosted`: every race
    held, a `circuit` column). Shrunk towards 1 by PU_CIRCUIT_PRIOR races' worth of evidence.
    """
    per_race = len(events) / max(len(hosted), 1)
    n = events.groupby("circuit").size()
    races = hosted.groupby("circuit").size()
    k = config.PU_CIRCUIT_PRIOR
    return {c: float((n.get(c, 0) + k * per_race) / ((races[c] + k) * per_race)) for c in races.index}


def hazard(deficit, over, left, circuit_factor, coef: dict | None = None) -> np.ndarray:
    """Chance of an over-allocation element at one Grand Prix, from penalty_features and the
    circuit's factor (logistic; coefficients config.PU_HAZARD)."""
    c = coef or config.PU_HAZARD
    z = (c["intercept"] + c["deficit"] * np.asarray(deficit, float) + c["over"] * np.asarray(over, float)
         + c["left"] * np.asarray(left, float) + c["circuit"] * np.log(np.asarray(circuit_factor, float)))
    return 1.0 / (1.0 + np.exp(-z))


def plan_groups(plans: dict | None) -> dict[str, list[dict]]:
    """Reported plans as {driver: [group, ...]}: config.PU_PLANS ({driver: group} or {driver:
    [groups]}) and news.news_plans alike. A group is one planned penalty: {"rounds": [...], ...}."""
    out: dict[str, list[dict]] = {}
    for d, g in (plans or {}).items():
        out.setdefault(d, []).extend(g if isinstance(g, list) else [g])
    return out


def penalty_risk(state: dict[str, dict], limits: dict[str, int], schedule: list[dict], total_rounds: int,
                 factors: dict[str, float], penalised: set[str], plans: dict | None = None,
                 announced: dict[str, object] | None = None) -> pd.DataFrame:
    """
    Every driver's chance of a power-unit grid penalty at each Grand Prix left. `state`: driver ->
    elements used so far; `schedule`: the Grand Prix left in order ({"round", "circuit"});
    `penalised`: drivers already over the allocation this season; `plans`: reported plans
    (plan_groups); `announced`: driver -> grid drop for the next round once the FIA's "New PU
    elements" document is out (None before): certain there, and anyone else only changes late.

    Two independent sources of a penalty, combined: the usage model (h_before / h_after: the chance
    at a round if the driver hasn't / has already taken one by then; taking one makes them `over`,
    with one fresh element more) and each reported plan, one penalty at the first of its rounds it
    doesn't slip past (PU_PLAN_HAZARD at each; plan_p: the plans' chance at that round). A Singapore
    change after a failure and an upgrade's change two rounds later are two plans: both can happen.

    One row per driver and round: h_before, h_after, plan_p, p (the chance of a penalty there), p_by
    (of at least one by then), plan, announced. `attrs["groups"]`: [(driver, {round: chance})] per
    plan, for the title odds (which draw each plan's round once per season).
    """
    plans = plan_groups(plans)
    known = announced is not None          # the next round's "New PU elements" are out
    announced = announced or {}
    rows, groups = [], []
    rounds_left = [ev["round"] for ev in schedule]
    first = rounds_left[0] if rounds_left else None
    for d in state:
        for g in plans.get(d, []):
            left = [r for r in rounds_left if r in g.get("rounds", ())]
            q, none = {}, 1.0
            for r in left:
                h = config.PU_PLAN_HAZARD
                if r == first and known:
                    h = 1.0 if d in announced else h * config.PU_LATE_SHARE
                q[r] = none * h
                none *= 1 - h
            if q:
                groups.append((d, q))
    for d, before in state.items():
        model_none = 1.0                  # no penalty from the usage model yet
        mine = [q for dd, q in groups if dd == d]
        for k, ev in enumerate(schedule):
            rnd = ev["round"]
            f = factors.get(ev["circuit"], 1.0)
            feat = penalty_features(before, limits, rnd, total_rounds, d in penalised)
            hb = float(hazard(feat["deficit"], feat["over"], feat["left"], f))
            ha = float(hazard(feat["deficit"] - 1, 1.0, feat["left"], f))
            ann = k == 0 and d in announced
            if ann:
                hb = ha = 1.0
            elif k == 0 and known:
                # The round's new elements are out and this driver isn't over the allocation in
                # them: only a late change (after qualifying, parc fermé) is left.
                hb, ha = hb * config.PU_LATE_SHARE, ha * config.PU_LATE_SHARE
            p_model = model_none * hb + (1 - model_none) * ha
            model_none *= 1 - hb
            plan_p = 1 - float(np.prod([1 - q.get(rnd, 0.0) for q in mine])) if mine else 0.0
            p = 1 - (1 - p_model) * (1 - plan_p)
            plans_none = float(np.prod([1 - sum(v for r, v in q.items() if r <= rnd) for q in mine])) if mine else 1.0
            rows.append({"driver": d, "round": rnd, "h_before": hb, "h_after": ha, "plan_p": plan_p, "p": p,
                         "p_by": 1 - model_none * plans_none, "plan": any(rnd in q for q in mine),
                         "announced": announced.get(d) if ann else None})
    out = pd.DataFrame(rows, columns=["driver", "round", "h_before", "h_after", "plan_p", "p", "p_by", "plan", "announced"])
    out = out.astype({"round": int, "h_before": float, "h_after": float, "plan_p": float, "p": float, "p_by": float,
                      "plan": bool})
    out.attrs["groups"] = groups
    return out


def expected_drop(rank: float, n: int) -> float:
    """Grid places a driver expected to start `rank`-th (of n) loses to a power-unit penalty, over
    the sizes they come in (config.PU_DROP_SHARE). A drop can't go past the back."""
    room = max(n - rank, 0.0)
    return float(sum(share * (room if size == "back" else min(float(size), room))
                     for size, share in config.PU_DROP_SHARE.items()))
