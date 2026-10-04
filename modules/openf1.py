"""
modules/openf1.py — Load a race or sprint from the OpenF1 API (https://openf1.org) into the
same shape FastF1 gives, so data_engine and everything after it work unchanged.

Why: F1's live-timing server, which FastF1 reads, doesn't answer GitHub's Actions runners
(FastF1 falls back to its mirror, which has no current season). OpenF1 does. The website's
export uses this source everywhere (locally too, so local and published data match); the
Streamlit dashboard keeps FastF1 unless config.DATA_SOURCE says otherwise.

    session = load_session(2026, "Bahrain Grand Prix", "R")
    session.laps, session.results, session.weather_data, session.event, session.total_laps

What maps to what
-----------------
laps          /laps (lap start time and duration), /stints (compound, tyre age), /pit
              (in-lap), race-control messages (Safety Car / VSC / red flag -> TrackStatus
              codes 4 / 6-7 / 5; "... DELETED ... LAP n" -> Deleted). Times are seconds from
              the first lap's start. Position is the order of lap-end times on each lap.
results       /session_result (position, points, DNF/DNS/DSQ, laps) and /drivers; the
              starting grid comes from Jolpica (Ergast), which posts it a few hours to a day
              later. Until then GridPosition is missing, and `complete` is False.
weather_data  /weather.
event         FastF1's event schedule (it doesn't need the live-timing server).

Archive: once a session is final (CACHE_FINAL_DAYS after it started, with its result and grid
in) everything fetched for it is written to archive/openf1/<year>/rNN-R.json.gz, which is
committed to the repo. From then on the session is read from there and never fetched again.
Before that, requests go through a cache in .openf1/ (kept between GitHub runs) and a recent
session is re-fetched after CACHE_FRESH_MINUTES, so late classifications and penalties come
through. Requests are spaced to stay inside OpenF1's free rate limit and retried on HTTP 429.
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
MIN_INTERVAL_S = 2.1            # ~28 requests a minute: under the free tier's 30/min
SESSION_MATCH = pd.Timedelta(hours=6)

_last_request = 0.0


class OpenF1Error(RuntimeError):
    pass


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


def _write_archive(path: Path, raw: dict) -> None:
    """Deterministic gzip (sorted keys, no timestamp), so a rewrite is never a git change."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))


def _max_age(start: pd.Timestamp) -> dt.timedelta | None:
    """Final sessions never expire; recent ones are re-fetched now and then."""
    if pd.Timestamp.now(tz="UTC") - start > pd.Timedelta(days=CACHE_FINAL_DAYS):
        return None
    return dt.timedelta(minutes=CACHE_FRESH_MINUTES)


# ---------------------------------------------------------------------------
# Finding the session
# ---------------------------------------------------------------------------
def find_session(year: int, start_utc: pd.Timestamp, code: str) -> dict:
    """OpenF1's session starting within SESSION_MATCH of `start_utc` (matching by time,
    not name: OpenF1 still lists cancelled rounds, and names change)."""
    name = "Sprint" if code == "S" else "Race"
    sessions = _api("sessions", dt.timedelta(hours=6), year=int(year), session_name=name)
    if sessions.empty:
        raise OpenF1Error(f"OpenF1 has no {name} sessions for {year}")
    starts = pd.to_datetime(sessions["date_start"], utc=True, format="ISO8601")
    gap = (starts - start_utc).abs()
    if gap.min() > SESSION_MATCH:
        raise OpenF1Error(f"No OpenF1 {name} near {start_utc:%Y-%m-%d %H:%M} UTC")
    return sessions.loc[gap.idxmin()].to_dict()


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
        col = "Sprint" if code == "S" else "Race"
        start = next((self.event[f"Session{i}DateUtc"] for i in range(1, 6)
                      if self.event[f"Session{i}"] == col), None)
        if start is None or pd.isna(start):
            raise OpenF1Error(f"{event_name} {year} has no {col}")
        self.start = pd.Timestamp(start).tz_localize("UTC")
        self.name = col
        rnd = int(self.event["RoundNumber"])
        path = archive_path(year, rnd, code)
        if path.exists():
            raw = json.loads(gzip.decompress(path.read_bytes()))
        else:
            raw = self._fetch(year, code, rnd)
            self._maybe_archive(path, raw)
        frames = {k: pd.DataFrame(v) for k, v in raw["endpoints"].items()}
        drivers, laps, stints, pits, rc, weather, result = (frames[k] for k in ENDPOINTS)
        if laps.empty or drivers.empty:
            raise OpenF1Error(f"OpenF1 has no laps for {year} {event_name} {col} yet")
        self._grid_raw = raw.get("grid")

        drivers = drivers.drop_duplicates("driver_number").set_index("driver_number")
        self.t0 = pd.to_datetime(laps["date_start"], utc=True, format="ISO8601").min()
        self.laps = self._laps(laps, stints, pits, rc, drivers)
        self.total_laps = int(self.laps["LapNumber"].max())
        self.results = self._results(result, drivers, year, code)
        self.weather_data = self._weather(weather)

    def _fetch(self, year: int, code: str, rnd: int) -> dict:
        """Everything this session needs, from OpenF1 and Jolpica (through the cache)."""
        info = find_session(year, self.start, code)
        sk, age = int(info["session_key"]), _max_age(self.start)
        endpoints = {}
        for name in ENDPOINTS:
            endpoints[name] = _get(API + name, {"session_key": sk}, age)
            if name in ("drivers", "laps") and not endpoints[name]:
                break                              # not out yet: don't spend requests on the rest
        endpoints = {k: endpoints.get(k, []) for k in ENDPOINTS}
        what = "sprint" if code == "S" else "results"
        try:
            grid = _get(f"{JOLPICA}{year}/{rnd}/{what}.json", {"limit": 30}, age)
        except Exception:  # noqa: BLE001 — the grid is a nice-to-have
            grid = None
        return {"session": info, "endpoints": endpoints, "grid": grid}

    def _maybe_archive(self, path: Path, raw: dict) -> None:
        """Archive a final session: old enough, with laps, a result and (usually) the grid."""
        age = pd.Timestamp.now(tz="UTC") - self.start
        if age <= pd.Timedelta(days=CACHE_FINAL_DAYS):
            return
        ep = raw["endpoints"]
        if not (ep["laps"] and ep["drivers"] and ep["session_result"]):
            return
        if not self._grid_rows(raw.get("grid")) and age <= pd.Timedelta(days=ARCHIVE_WITHOUT_GRID_DAYS):
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
            return pd.DataFrame(columns=["Time", "AirTemp", "TrackTemp", "Rainfall"])
        return pd.DataFrame({
            "Time": self._secs(w["date"]),
            "AirTemp": w["air_temperature"], "TrackTemp": w["track_temperature"],
            "Humidity": w.get("humidity"), "Rainfall": w["rainfall"].fillna(0).astype(float) > 0,
        }).sort_values("Time").reset_index(drop=True)


def load_session(year: int, event_name: str, code: str) -> Session:
    return Session(year, event_name, code)
