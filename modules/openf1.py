"""
modules/openf1.py — Load a race, sprint or qualifying session from the OpenF1 API
(https://openf1.org) into the same shape FastF1 gives, so data_engine and everything after it
work unchanged.

Why: F1's live-timing server, which FastF1 reads, doesn't answer GitHub's Actions runners
(FastF1 falls back to its mirror, which has no current season). OpenF1 does. The website's
export uses this source everywhere (locally too, so local and published data match); the
Streamlit dashboard keeps FastF1 unless config.DATA_SOURCE says otherwise.

    session = load_session(2026, "Bahrain Grand Prix", "R")
    session.laps, session.results, session.weather_data, session.event, session.total_laps

What maps to what
-----------------
laps          /laps (lap start time and duration, sector times, speed traps), /stints
              (compound, tyre age), /pit (in-lap), race-control messages (Safety Car / VSC / red flag -> TrackStatus
              codes 4 / 6-7 / 5; "... DELETED ... LAP n" -> Deleted). Times are seconds from
              the first lap's start. Position is the order of lap-end times on each lap.
              Qualifying: the chequered flags that end Q1, Q2 and Q3 (`segment_ends`, session
              seconds) split the laps into segments.
results       /session_result (position, points, DNF/DNS/DSQ, laps) and /drivers; the
              starting grid comes from Jolpica (Ergast), which posts it a few hours to a day
              later. Until then GridPosition is missing, and `complete` is False.
              Qualifying: Q1, Q2, Q3 times (Timedelta, as FastF1) from session_result's
              `duration` list; no grid or points.
weather_data  /weather.
event         FastF1's event schedule (it doesn't need the live-timing server).

Archive: once a session is final (CACHE_FINAL_DAYS after it started, with its result and, for a
race, its grid in) everything fetched for it is written to archive/openf1/<year>/rNN-<code>.json.gz
(R, S, Q, SQ), which is
committed to the repo. From then on the session is read from there and never fetched again.
Before that, requests go through a cache in .openf1/ (kept between GitHub runs) and a recent
session is re-fetched after CACHE_FRESH_MINUTES (RACE_DAY_FRESH_MINUTES on the day), so late
classifications and penalties come through.

Gaps: when OpenF1 is missing an endpoint for a session (2025 Azerbaijan qualifying has no laps),
`backfill_from_fastf1` fills it from F1's live-timing archive through FastF1 (laps, pit stops,
stints, weather, drivers; the qualifying classification from Jolpica) and keeps everything OpenF1
does have. FastF1's session clock is tied to UTC by the moment the session started (its status
feed against OpenF1's "SESSION STARTED" message). The result goes to
archive/openf1/<year>/rNN-<code>.fastf1.json.gz, read like any archive, with `sources` naming what
came from where. It runs locally (scripts/backfill_fastf1.py): F1's live-timing server doesn't
answer GitHub's runners. Separately, a qualifying session OpenF1 has no classification for yet
takes Jolpica's (positions and Q1/Q2/Q3 times).

Unfinished sessions: on the day, race-control messages are fetched first, and until the last chequered
flag is in (config.session_finished; qualifying has three) loading raises SessionRunning without fetching anything else,
so a race is never published half-run. Requests are spaced to stay inside OpenF1's free rate limit and retried on HTTP 429.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import re
import time
from pathlib import Path

import fastf1
import numpy as np
import pandas as pd
import requests

import config

API = "https://api.openf1.org/v1/"
JOLPICA = "https://api.jolpi.ca/ergast/f1/"
CACHE_DIR = config.PROJECT_ROOT / ".openf1"
ARCHIVE_DIR = config.PROJECT_ROOT / "archive" / "openf1"
ENDPOINTS = ("drivers", "laps", "stints", "pit", "race_control", "weather", "session_result")
CACHE_FINAL_DAYS = 4
ARCHIVE_WITHOUT_GRID_DAYS = 14  # archive anyway if Jolpica still has no grid by then
CACHE_FRESH_MINUTES = 20
RACE_DAY_FRESH_MINUTES = 3      # until LATEST_FINISH_H: catch the chequered flag quickly
MIN_INTERVAL_S = 2.1            # ~28 requests a minute: under the free tier's 30/min
SESSION_MATCH = pd.Timedelta(hours=6)

_last_request = 0.0


class OpenF1Error(RuntimeError):
    pass


class SessionRunning(OpenF1Error):
    """The session hasn't finished yet (no chequered flag)."""


# ---------------------------------------------------------------------------
# HTTP with a disk cache
# ---------------------------------------------------------------------------
def _get(url: str, params: dict, max_age: dt.timedelta | None) -> list | dict:
    """GET JSON through the cache. max_age None = cached copies never expire."""
    global _last_request
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:20]
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(saved["fetched"])
        if max_age is None or age < max_age:
            return saved["data"]

    for attempt in range(6):
        wait = MIN_INTERVAL_S - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        r = requests.get(url, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 0) or 0) or 10 * (attempt + 1))
            continue
        if r.status_code == 404:          # OpenF1's "No results found."
            data = []
            break
        r.raise_for_status()
        data = r.json()
        break
    else:
        raise OpenF1Error(f"rate limited on {url}")
    path.write_text(json.dumps({"fetched": dt.datetime.now(dt.timezone.utc).isoformat(), "url": url,
                                "params": params, "data": data}), encoding="utf-8")
    return data


def _api(path: str, max_age: dt.timedelta | None, **params) -> pd.DataFrame:
    return pd.DataFrame(_get(API + path, params, max_age))


def archive_path(year: int, rnd: int, code: str) -> Path:
    return ARCHIVE_DIR / str(year) / f"r{rnd:02d}-{code}.json.gz"


def fallback_path(year: int, rnd: int, code: str) -> Path:
    """A session with OpenF1's gaps filled from F1 live timing (backfill_from_fastf1)."""
    return ARCHIVE_DIR / str(year) / f"r{rnd:02d}-{code}.fastf1.json.gz"


def _lap_seconds(text) -> float | None:
    """Jolpica's "1:41.331" -> 101.331."""
    if not text:
        return None
    m, _, s = str(text).rpartition(":")
    try:
        return (int(m) * 60 if m else 0) + float(s)
    except ValueError:
        return None


def _jolpica_quali(year: int, rnd: int, max_age: dt.timedelta | None) -> list[dict]:
    """Jolpica's qualifying classification as OpenF1 session_result rows ([] if it has none)."""
    try:
        data = _get(f"{JOLPICA}{year}/{rnd}/qualifying.json", {"limit": 30}, max_age)
        races = data["MRData"]["RaceTable"]["Races"]
        rows = races[0]["QualifyingResults"] if races else []
    except Exception:  # noqa: BLE001 — not out yet, or Jolpica down
        return []
    return [{"position": int(r["position"]), "driver_number": int(r["number"]),
             "duration": [_lap_seconds(r.get(q)) for q in ("Q1", "Q2", "Q3")],
             "dnf": False, "dns": False, "dsq": False} for r in rows]


def _write_archive(path: Path, raw: dict) -> None:
    """Deterministic gzip (sorted keys, no timestamp), so a rewrite is never a git change."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))


def _max_age(start: pd.Timestamp, code: str) -> dt.timedelta | None:
    """Final sessions never expire; recent ones are re-fetched now and then."""
    age = pd.Timestamp.now(tz="UTC") - start
    if age > pd.Timedelta(days=CACHE_FINAL_DAYS):
        return None
    if age < pd.Timedelta(hours=config.LATEST_FINISH_H[code]):
        return dt.timedelta(minutes=RACE_DAY_FRESH_MINUTES)
    return dt.timedelta(minutes=CACHE_FRESH_MINUTES)


# ---------------------------------------------------------------------------
# Finding the session
# ---------------------------------------------------------------------------
def find_session(year: int, start_utc: pd.Timestamp, code: str) -> dict:
    """OpenF1's session starting within SESSION_MATCH of `start_utc` (matching by time,
    not name: OpenF1 still lists cancelled rounds, and names change)."""
    names = config.session_names(code)
    for max_age in (dt.timedelta(hours=6), dt.timedelta(0)):   # a cached list may predate it
        sessions = pd.concat([_api("sessions", max_age, year=int(year), session_name=n) for n in names])
        if sessions.empty:
            continue
        sessions = sessions.reset_index(drop=True)
        starts = pd.to_datetime(sessions["date_start"], utc=True, format="ISO8601")
        gap = (starts - start_utc).abs()
        if gap.min() <= SESSION_MATCH:
            return sessions.loc[gap.idxmin()].to_dict()
    raise OpenF1Error(f"No OpenF1 {names[0]} near {start_utc:%Y-%m-%d %H:%M} UTC")


def chequered_times(rc: list[dict] | pd.DataFrame) -> list[pd.Timestamp]:
    """Every time the chequered flag was shown, in order, from the race-control messages."""
    rc = pd.DataFrame(rc)
    if rc.empty or "flag" not in rc:
        return []
    t = pd.to_datetime(rc.loc[rc["flag"].astype(str).str.upper() == "CHEQUERED", "date"],
                       utc=True, format="ISO8601")
    # One flag can be logged twice a few seconds apart: keep flags a minute or more apart.
    out: list[pd.Timestamp] = []
    for x in sorted(t):
        if not out or x - out[-1] > pd.Timedelta(minutes=1):
            out.append(x)
    return out


def chequered_at(rc: list[dict] | pd.DataFrame, code: str = "R") -> pd.Timestamp | None:
    """When the flag that ends the session was shown (qualifying: the third), or None."""
    return config.last_chequered(chequered_times(rc), code)


# ---------------------------------------------------------------------------
# Track status from race-control messages
# ---------------------------------------------------------------------------
def _status_periods(rc: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    """
    (start, end, code) for every Safety Car ('4'), VSC ('6', '7' while ending) and red
    flag ('5') period, from the race-control messages in time order.
    """
    if rc.empty:
        return []
    rc = rc.assign(t=pd.to_datetime(rc["date"], utc=True, format="ISO8601")).sort_values("t")
    periods, state, since, sc_ending = [], None, None, False

    def close(t):
        nonlocal state, since, sc_ending
        if state:
            periods.append((since, t, state))
        state, since, sc_ending = None, None, False

    for m in rc.itertuples():
        msg, flag = str(m.message).upper(), str(m.flag or "").upper()
        if "VIRTUAL SAFETY CAR DEPLOYED" in msg or "VSC DEPLOYED" in msg:
            close(m.t); state, since = "6", m.t
        elif "SAFETY CAR DEPLOYED" in msg:
            close(m.t); state, since = "4", m.t
        elif ("VSC ENDING" in msg or "VIRTUAL SAFETY CAR ENDING" in msg) and state == "6":
            close(m.t); state, since = "7", m.t
        elif "SAFETY CAR IN THIS LAP" in msg and state == "4":
            sc_ending = True
        elif flag == "RED":
            close(m.t); state, since = "5", m.t
        elif flag == "CHEQUERED":
            close(m.t)
        elif (flag == "GREEN" or msg == "TRACK CLEAR") and (state in ("5", "7") or sc_ending):
            close(m.t)
    close(rc["t"].max() + pd.Timedelta(minutes=5))
    return periods


# ---------------------------------------------------------------------------
# The session object
# ---------------------------------------------------------------------------
class Session:
    """The parts of fastf1.core.Session the app uses, filled from OpenF1."""

    def __init__(self, year: int, event_name: str, code: str):
        self.event = fastf1.get_event(int(year), event_name)
        self.code = code
        col = config.SESSION_NAMES[code]
        start = next((self.event[f"Session{i}DateUtc"] for i in range(1, 6)
                      if self.event[f"Session{i}"] in config.session_names(code)), None)
        if start is None or pd.isna(start):
            raise OpenF1Error(f"{event_name} {year} has no {col}")
        self.start = pd.Timestamp(start).tz_localize("UTC")
        self.name = col
        rnd = int(self.event["RoundNumber"])
        path = archive_path(year, rnd, code)
        if path.exists():
            raw = json.loads(gzip.decompress(path.read_bytes()))
        elif fallback_path(year, rnd, code).exists():
            raw = json.loads(gzip.decompress(fallback_path(year, rnd, code).read_bytes()))
        else:
            raw = self._fetch(year, code, rnd)
            self._maybe_archive(path, raw)
        frames = {k: pd.DataFrame(v) for k, v in raw["endpoints"].items()}
        drivers, laps, stints, pits, rc, weather, result = (frames[k] for k in ENDPOINTS)
        if laps.empty or drivers.empty:
            raise OpenF1Error(f"OpenF1 has no laps for {year} {event_name} {col} yet (if they never come: "
                              f"scripts/backfill_fastf1.py {year} --round {rnd} --code {code})")
        self._grid_raw = raw.get("grid")
        self.info = raw.get("session", {})          # OpenF1's session row: session_key, circuit_key...
        self.archived = path.exists() or fallback_path(year, rnd, code).exists()
        # What didn't come from OpenF1, endpoint -> source (empty: all of it did).
        self.sources: dict[str, str] = raw.get("sources", {})

        drivers = drivers.drop_duplicates("driver_number").set_index("driver_number")
        self.t0 = pd.to_datetime(laps["date_start"], utc=True, format="ISO8601").min()
        # Qualifying: when Q1, Q2 and Q3 ended (session seconds), to split the laps by segment.
        self.segment_ends = [(t - self.t0).total_seconds() for t in chequered_times(rc)]
        self.laps = self._laps(laps, stints, pits, rc, drivers)
        self.total_laps = int(self.laps["LapNumber"].max())
        self.results = self._results(result, drivers, year, code)
        self.weather_data = self._weather(weather)

    def _fetch(self, year: int, code: str, rnd: int) -> dict:
        """Everything this session needs, from OpenF1 and Jolpica (through the cache)."""
        info = find_session(year, self.start, code)
        sk, age = int(info["session_key"]), _max_age(self.start, code)
        endpoints = {}
        now = pd.Timestamp.now(tz="UTC")
        if now - self.start < pd.Timedelta(hours=config.LATEST_FINISH_H[code]):
            # On the day: nothing else until the flag is out, and everything else fetched after it.
            endpoints["race_control"] = _get(API + "race_control", {"session_key": sk}, age)
            if not config.session_finished(self.start, chequered_at(endpoints["race_control"], code), now, code):
                raise SessionRunning(f"{self.name} {year} round {rnd} hasn't finished yet")
        for name in ENDPOINTS:
            if name in endpoints:
                continue
            endpoints[name] = _get(API + name, {"session_key": sk}, age)
            if name in ("drivers", "laps") and not endpoints[name]:
                break                              # not out yet: don't spend requests on the rest
        endpoints = {k: endpoints.get(k, []) for k in ENDPOINTS}
        sources = {}
        if code == "Q" and endpoints["laps"] and not endpoints["session_result"]:
            # OpenF1's classification can lag (or be missing): Jolpica has positions and Q times.
            endpoints["session_result"] = _jolpica_quali(year, rnd, age)
            if endpoints["session_result"]:
                sources["session_result"] = "Jolpica"
        grid = None
        if code in config.RACE_CODES:              # qualifying sets a grid, it doesn't start from one
            what = "sprint" if code == "S" else "results"
            try:
                grid = _get(f"{JOLPICA}{year}/{rnd}/{what}.json", {"limit": 30}, age)
            except Exception:  # noqa: BLE001 — the grid is a nice-to-have
                grid = None
        return {"session": info, "endpoints": endpoints, "grid": grid, "sources": sources}

    def _maybe_archive(self, path: Path, raw: dict) -> None:
        """Archive a final session: old enough, with laps, a result and (usually) the grid."""
        age = pd.Timestamp.now(tz="UTC") - self.start
        if age <= pd.Timedelta(days=CACHE_FINAL_DAYS):
            return
        ep = raw["endpoints"]
        if not (ep["laps"] and ep["drivers"] and ep["session_result"]):
            return
        if raw.get("sources", {}).get("session_result") == "Jolpica":
            return                                 # keep asking OpenF1 for its own classification
        if (self.code in config.RACE_CODES and not self._grid_rows(raw.get("grid"))
                and age <= pd.Timedelta(days=ARCHIVE_WITHOUT_GRID_DAYS)):
            return
        _write_archive(path, raw)

    def _secs(self, ts: pd.Series) -> pd.Series:
        return (pd.to_datetime(ts, utc=True, format="ISO8601") - self.t0).dt.total_seconds()

    def _laps(self, laps, stints, pits, rc, drivers) -> pd.DataFrame:
        laps = laps.sort_values(["driver_number", "lap_number"]).copy()
        laps["LapStartTime"] = self._secs(laps["date_start"])
        # Lap end: the next lap's start, else this lap's start plus its duration.
        nxt = laps.groupby("driver_number")["LapStartTime"].shift(-1)
        laps["Time"] = nxt.where(nxt.notna(), laps["LapStartTime"] + laps["lap_duration"])
        laps["LapTime"] = laps["lap_duration"]
        out = pd.DataFrame({
            "Driver": laps["driver_number"].map(drivers["name_acronym"]),
            "DriverNumber": laps["driver_number"].astype(str),
            "Team": laps["driver_number"].map(drivers["team_name"]),
            "LapNumber": laps["lap_number"].astype(int),
            "LapTime": laps["LapTime"],
            "LapStartTime": laps["LapStartTime"],
            "Time": laps["Time"],
            "PitOutTime": np.where(laps["is_pit_out_lap"].fillna(False), laps["LapStartTime"], np.nan),
            "Sector1Time": laps.get("duration_sector_1"),
            "Sector2Time": laps.get("duration_sector_2"),
            "Sector3Time": laps.get("duration_sector_3"),
            "SpeedI1": laps.get("i1_speed"), "SpeedI2": laps.get("i2_speed"), "SpeedST": laps.get("st_speed"),
        })

        # Tyres: the stint a lap falls in gives compound, stint number and tyre age.
        out["Stint"], out["Compound"], out["TyreLife"] = np.nan, None, np.nan
        for s in stints.itertuples():
            m = (laps["driver_number"] == s.driver_number) & laps["lap_number"].between(s.lap_start, s.lap_end)
            out.loc[m, "Stint"] = s.stint_number
            out.loc[m, "Compound"] = s.compound
            out.loc[m, "TyreLife"] = (s.tyre_age_at_start or 0) + laps.loc[m, "lap_number"] - s.lap_start + 1

        # Pit stops: the in-lap gets PitInTime (its end).
        pit_keys = set(zip(pits.get("driver_number", []), pits.get("lap_number", [])))
        is_in = [(d, n) in pit_keys for d, n in zip(laps["driver_number"], laps["lap_number"])]
        out["PitInTime"] = np.where(is_in, laps["Time"], np.nan)

        # Track status over each lap, as FastF1's string of codes.
        periods = _status_periods(rc)
        p_start = [(a - self.t0).total_seconds() for a, _, _ in periods]
        p_end = [(b - self.t0).total_seconds() for _, b, _ in periods]
        codes = [c for _, _, c in periods]

        def status(a, b):
            if pd.isna(a) or pd.isna(b):
                return "1"
            hit = "".join(sorted({c for s, e, c in zip(p_start, p_end, codes) if s < b and e > a}))
            return hit or "1"
        out["TrackStatus"] = [status(a, b) for a, b in zip(out["LapStartTime"], out["Time"])]

        # Deleted laps: "CAR 1 (NOR) TIME 2:06.772 DELETED - TRACK LIMITS AT TURN 12 LAP 4 ..."
        deleted = set()
        for msg in rc.get("message", pd.Series(dtype=str)).astype(str):
            m = re.search(r"CAR (\d+)\b.*?DELETED.*?\bLAP (\d+)", msg)
            if m and "REINSTATED" not in msg:
                deleted.add((int(m.group(1)), int(m.group(2))))
        out["Deleted"] = [(d, n) in deleted for d, n in zip(laps["driver_number"], laps["lap_number"])]

        # Position at the end of each lap: order of lap-end times among cars on that lap.
        out["Position"] = out.groupby("LapNumber")["Time"].rank(method="first")
        return out.reset_index(drop=True)

    def _results(self, result, drivers, year, code) -> pd.DataFrame:
        laps_done = self.laps.groupby("DriverNumber")["LapNumber"].max()
        res = pd.DataFrame({
            "DriverNumber": drivers.index.astype(str),
            "Abbreviation": drivers["name_acronym"].to_numpy(),
            "FullName": drivers["full_name"].str.title().to_numpy(),
            "TeamName": drivers["team_name"].to_numpy(),
            "TeamColor": drivers["team_colour"].fillna("").to_numpy(),
        })
        if self.code in config.QUALI_CODES:
            return self._quali_results(res, result)
        if not result.empty:
            r = result.drop_duplicates("driver_number").set_index(result["driver_number"].astype(str))
            res["Position"] = res["DriverNumber"].map(r["position"])
            res["Points"] = res["DriverNumber"].map(r["points"])
            laps_n = res["DriverNumber"].map(r["number_of_laps"])
            leader = laps_n.max()
            flag = lambda c: res["DriverNumber"].map(r[c]).fillna(False).astype(bool) if c in r else False  # noqa: E731
            dnf, dns, dsq = flag("dnf"), flag("dns"), flag("dsq")
            behind = (leader - laps_n).fillna(0).astype(int)
            res["Status"] = np.select(
                [dsq, dns, dnf, behind > 0],
                ["Disqualified", "Did not start", "Retired",
                 "+" + behind.astype(str) + np.where(behind > 1, " Laps", " Lap")],
                "Finished")
            res["ClassifiedPosition"] = np.where(dsq, "D", np.where(dns, "W", np.where(
                dnf | res["Position"].isna(), "R", res["Position"].fillna(0).astype(int).astype(str))))
        else:
            # No classification yet: timing order, no points (site_export derives them).
            last = self.laps.sort_values("LapNumber").groupby("DriverNumber").tail(1)
            order = last.sort_values(["LapNumber", "Time"], ascending=[False, True])["DriverNumber"].tolist()
            res["Position"] = res["DriverNumber"].map({d: i + 1 for i, d in enumerate(order)})
            res["Points"], res["Status"], res["ClassifiedPosition"] = np.nan, "", ""
        res["GridPosition"] = res["Abbreviation"].map(
            {r["Driver"].get("code"): float(r["grid"]) for r in self._grid_rows(self._grid_raw)})
        res["Laps"] = res["DriverNumber"].map(laps_done)
        return res.sort_values("Position", na_position="last").reset_index(drop=True)

    def _quali_results(self, res: pd.DataFrame, result: pd.DataFrame) -> pd.DataFrame:
        """Qualifying classification: position and the Q1, Q2, Q3 times (Timedelta, NaT = no time)."""
        for q in ("Q1", "Q2", "Q3"):
            res[q] = pd.NaT
        res["Position"], res["Points"], res["Status"], res["ClassifiedPosition"] = np.nan, np.nan, "", ""
        if not result.empty:
            r = result.drop_duplicates("driver_number").set_index(result["driver_number"].astype(str))
            res["Position"] = res["DriverNumber"].map(r["position"])

            def seg(d, i):
                if isinstance(d, list):
                    return pd.Timedelta(seconds=float(d[i])) if len(d) > i and d[i] is not None else pd.NaT
                return pd.Timedelta(seconds=float(d)) if i == 0 and isinstance(d, (int, float)) and pd.notna(d) else pd.NaT
            if "duration" in r:
                dur = res["DriverNumber"].map(r["duration"])
                for i, q in enumerate(("Q1", "Q2", "Q3")):
                    res[q] = dur.map(lambda d, i=i: seg(d, i))
            flag = lambda c: res["DriverNumber"].map(r[c]).fillna(False).astype(bool) if c in r else False  # noqa: E731
            res["Status"] = np.where(flag("dsq"), "Disqualified", np.where(flag("dns"), "Did not start", ""))
            res["ClassifiedPosition"] = res["Position"].map(lambda p: str(int(p)) if pd.notna(p) else "")
        res["GridPosition"] = np.nan
        res["Laps"] = res["DriverNumber"].map(self.laps.groupby("DriverNumber")["LapNumber"].max())
        return res.sort_values("Position", na_position="last").reset_index(drop=True)

    @staticmethod
    def _grid_rows(data) -> list[dict]:
        """Jolpica's result rows (each with a grid slot), or [] until it's posted."""
        try:
            races = data["MRData"]["RaceTable"]["Races"]
            r = races[0] if races else {}
            return r.get("SprintResults") or r.get("Results") or []
        except (TypeError, KeyError):
            return []

    def _weather(self, w: pd.DataFrame) -> pd.DataFrame:
        if w.empty:
            return pd.DataFrame(columns=["Time", "AirTemp", "TrackTemp", "Humidity", "WindSpeed",
                                         "WindDirection", "Rainfall"])
        return pd.DataFrame({
            "Time": self._secs(w["date"]),
            "AirTemp": w["air_temperature"], "TrackTemp": w["track_temperature"],
            "Humidity": w.get("humidity"), "WindSpeed": w.get("wind_speed"),
            "WindDirection": w.get("wind_direction"),
            "Rainfall": w["rainfall"].fillna(0).astype(float) > 0,
        }).sort_values("Time").reset_index(drop=True)


def load_session(year: int, event_name: str, code: str) -> Session:
    return Session(year, event_name, code)


# ---------------------------------------------------------------------------
# Filling OpenF1's gaps from F1 live timing (FastF1)
# ---------------------------------------------------------------------------
LIVE_TIMING = "F1 live timing (FastF1)"


def _iso(ts) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).isoformat()


def _num(v):
    """JSON-safe number: NaN -> None, numpy -> python."""
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, pd.Timedelta):
        return v.total_seconds()
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    return v


def _session_t0(ff1, rc: pd.DataFrame, scheduled: pd.Timestamp) -> tuple[pd.Timestamp, str]:
    """The UTC moment FastF1's session clock counts from, and how it was found: the first
    "Started" in its status feed against OpenF1's first SESSION STARTED message, else against
    the scheduled start."""
    started = None
    try:
        st = ff1.session_status
        started = st.loc[st["Status"] == "Started", "Time"].iloc[0]
    except Exception:  # noqa: BLE001 — no status feed
        pass
    if started is None:
        raise OpenF1Error("FastF1 has no session status to line its clock up with")
    msg = rc[rc.get("message", pd.Series(dtype=str)).astype(str).str.upper() == "SESSION STARTED"] if not rc.empty else rc
    if not msg.empty:
        at = pd.to_datetime(msg["date"], utc=True, format="ISO8601").min()
        return at - started, "SESSION STARTED message"
    return scheduled - started, "scheduled start"


def backfill_from_fastf1(year: int, event_name: str, code: str) -> dict:
    """
    Fill a session's missing OpenF1 endpoints from F1 live timing and write the fallback
    archive (fallback_path). Returns {"path", "filled", "anchor", "check_s"}: which endpoints were
    filled, how the clock was tied to UTC, and how far apart (s) FastF1's segment ends and
    OpenF1's chequered flags then are (a check on the anchor; None if there's nothing to compare).
    """
    event = fastf1.get_event(int(year), event_name)
    rnd = int(event["RoundNumber"])
    name, scheduled = next((event[f"Session{i}"], event[f"Session{i}DateUtc"]) for i in range(1, 6)
                           if event[f"Session{i}"] in config.session_names(code))
    scheduled = pd.Timestamp(scheduled).tz_localize("UTC")
    info = find_session(year, scheduled, code)
    sk, age = int(info["session_key"]), _max_age(scheduled, code)
    endpoints = {n: _get(API + n, {"session_key": sk}, age) for n in ENDPOINTS}

    ff1 = fastf1.get_session(int(year), event_name, name)
    ff1.load(laps=True, telemetry=False, weather=True, messages=True)
    rc = pd.DataFrame(endpoints["race_control"])
    t0, anchor = _session_t0(ff1, rc, scheduled)
    laps = pd.DataFrame(ff1.laps)
    filled: dict[str, str] = {}

    if not endpoints["laps"]:
        endpoints["laps"] = [{
            "driver_number": int(r.DriverNumber), "lap_number": int(r.LapNumber),
            "date_start": _iso(t0 + r.LapStartTime) if pd.notna(r.LapStartTime) else None,
            "lap_duration": _num(r.LapTime),
            "duration_sector_1": _num(r.Sector1Time), "duration_sector_2": _num(r.Sector2Time),
            "duration_sector_3": _num(r.Sector3Time),
            "i1_speed": _num(r.SpeedI1), "i2_speed": _num(r.SpeedI2), "st_speed": _num(r.SpeedST),
            "is_pit_out_lap": bool(pd.notna(r.PitOutTime)),
        } for r in laps.itertuples() if pd.notna(r.LapNumber)]
        filled["laps"] = LIVE_TIMING
    if not endpoints["pit"]:
        endpoints["pit"] = [{"driver_number": int(r.DriverNumber), "lap_number": int(r.LapNumber),
                             "date": _iso(t0 + r.PitInTime)}
                            for r in laps.itertuples() if pd.notna(r.PitInTime) and pd.notna(r.LapNumber)]
        filled["pit"] = LIVE_TIMING
    if not endpoints["stints"]:
        rows = []
        for (num, stint), g in laps.dropna(subset=["Stint", "LapNumber"]).groupby(["DriverNumber", "Stint"]):
            first = g.sort_values("LapNumber").iloc[0]
            rows.append({"driver_number": int(num), "stint_number": int(stint),
                         "lap_start": int(g["LapNumber"].min()), "lap_end": int(g["LapNumber"].max()),
                         "compound": first["Compound"], "tyre_age_at_start": _num(first["TyreLife"] - 1)
                         if pd.notna(first["TyreLife"]) else 0})
        endpoints["stints"] = rows
        filled["stints"] = LIVE_TIMING
    if not endpoints["weather"]:
        w = pd.DataFrame(ff1.weather_data)
        endpoints["weather"] = [{"date": _iso(t0 + r.Time), "air_temperature": _num(r.AirTemp),
                                 "track_temperature": _num(r.TrackTemp), "humidity": _num(r.Humidity),
                                 "wind_speed": _num(r.WindSpeed), "wind_direction": _num(r.WindDirection),
                                 "rainfall": _num(r.Rainfall)} for r in w.itertuples()]
        filled["weather"] = LIVE_TIMING
    if not endpoints["drivers"]:
        res = pd.DataFrame(ff1.results)
        endpoints["drivers"] = [{"driver_number": int(r.DriverNumber), "name_acronym": r.Abbreviation,
                                 "full_name": r.FullName, "team_name": r.TeamName, "team_colour": r.TeamColor}
                                for r in res.itertuples()]
        filled["drivers"] = LIVE_TIMING
    if not endpoints["session_result"] and code == "Q":
        endpoints["session_result"] = _jolpica_quali(year, rnd, None)
        if endpoints["session_result"]:
            filled["session_result"] = "Jolpica"

    # Check the anchor: FastF1's "Finished" marks on the UTC clock against OpenF1's flags.
    check = None
    try:
        st = ff1.session_status
        ends = [t0 + t for t in st.loc[st["Status"] == "Finished", "Time"]]
        flags = chequered_times(rc)
        if ends and flags:
            check = max(min(abs((e - f).total_seconds()) for f in flags) for e in ends)
    except Exception:  # noqa: BLE001
        pass

    grid = None
    if code in config.RACE_CODES:
        try:
            grid = _get(f"{JOLPICA}{year}/{rnd}/{'sprint' if code == 'S' else 'results'}.json", {"limit": 30}, None)
        except Exception:  # noqa: BLE001
            grid = None
    path = fallback_path(year, rnd, code)
    _write_archive(path, {"session": info, "endpoints": endpoints, "grid": grid, "sources": filled,
                          "anchor": {"t0": t0.isoformat(), "by": anchor, "check_s": check}})
    return {"path": path, "filled": filled, "anchor": anchor, "check_s": check}
