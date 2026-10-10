"""
modules/live.py — F1's live-timing feed for the Live Race Tracker.

Source      F1's own live-timing stream (SignalR Core, wss://livetiming.formula1.com/signalrcore), the
            feed the official app's timing screen uses. Without an F1 TV login it still sends timing,
            tyres, race control, track status, weather, lap count and the clock; car positions
            (Position.z), car data (CarData.z) and since the 2025 Dutch GP some others need a login,
            so they aren't asked for. FastF1's own client can't connect without a login (it passes
            access_token_factory=None, which signalrcore rejects), so this connects with signalrcore.

Clock       Every event is (t, topic, data) with t in epoch seconds: the moment it arrived (live) or the
            moment F1 sent it (archive replay). A board is built from the events up to a cut-off time,
            so a delay to match the TV broadcast is just an earlier cut-off.

Feed        `LiveFeed` (one per Streamlit server, held by the page's st.cache_resource) runs the
            connection in a thread, reconnects when it drops and stops when no page has asked for it
            for LIVE_IDLE_STOP_S. Events also go to `.live/<session key>.jsonl`, so a restart of the
            app during a session picks the lap history back up.

Replay      `archive_events` reads the same feed for a finished session from F1's static archive
            (livetiming.formula1.com/static/...jsonStream, one file per topic), cached in
            `.live/archive/`. The page plays it on a virtual clock: that's how the page is tested
            between race weekends.

Board       `Board` merges the events into F1's state (snapshots replace a topic, updates are deep
            merged; a dict of "index": value updates a list) and keeps a lap history in FastF1's
            column names (Driver, LapNumber, LapTime, Compound, TyreLife, Stint, PitIn, PitOut,
            TrackStatus, CleanAir, ...), so analytics and practice functions run on it unchanged.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import requests

import config

LIVE_DIR = Path(__file__).resolve().parents[1] / ".live"
ARCHIVE_DIR = LIVE_DIR / "archive"

STREAM_URL = "wss://livetiming.formula1.com/signalrcore"
NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate"
STATIC_URL = "https://livetiming.formula1.com/static/"

# The topics that come without an F1 TV login.
TOPICS = ["Heartbeat", "DriverList", "ExtrapolatedClock", "RaceControlMessages", "SessionInfo",
          "SessionStatus", "SessionData", "TimingAppData", "TimingData", "TimingStats", "TrackStatus",
          "WeatherData", "LapCount", "TopThree"]

LIVE_IDLE_STOP_S = 600          # stop the connection when no page has polled it for this long
LIVE_RECONNECT_S = (2, 5, 10, 30, 60)   # waits between reconnect attempts
HISTORY_MAX_AGE_S = 6 * 3600    # a saved session file older than this isn't loaded back in

# The feed's session names per session code (2023's sprint qualifying was the Sprint Shootout).
ARCHIVE_NAMES = {"R": ("Race",), "S": ("Sprint",), "Q": ("Qualifying",),
                 "SQ": ("Sprint Qualifying", "Sprint Shootout"),
                 "FP1": ("Practice 1",), "FP2": ("Practice 2",), "FP3": ("Practice 3",)}

TRACK_STATUS = {"1": "Green flag", "2": "Yellow flag", "4": "Safety Car", "5": "Red flag",
                "6": "Virtual Safety Car", "7": "VSC ending"}

_DELETED = re.compile(r"CAR (\d+)\b.*?DELETED.*?\bLAP (\d+)")

log = logging.getLogger(__name__)


class LiveUnavailable(RuntimeError):
    """The live feed or the archive couldn't be reached."""


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def parse_laptime(value) -> float:
    """'1:33.617' / '27.506' / '+1.188' -> seconds; anything else NaN."""
    if not isinstance(value, str) or not value.strip():
        return float("nan")
    v = value.strip().lstrip("+")
    try:
        if ":" in v:
            m, s = v.split(":", 1)
            return int(m) * 60 + float(s)
        return float(v)
    except ValueError:
        return float("nan")


def parse_gap(value) -> tuple[float, int]:
    """Race gap/interval string -> (seconds, laps down). 'LAP 23' (the leader) is (0, 0), '+1.2' (1.2, 0),
    '1 L' (NaN, 1)."""
    if not isinstance(value, str) or not value.strip():
        return float("nan"), 0
    v = value.strip()
    if v.upper().startswith("LAP"):
        return 0.0, 0
    m = re.match(r"\+?(\d+)\s*L", v)
    if m:
        return float("nan"), int(m.group(1))
    return parse_laptime(v), 0


def fmt_laptime(s: float) -> str:
    if s is None or s != s:
        return ""
    m, sec = divmod(s, 60)
    return f"{int(m)}:{sec:06.3f}" if m else f"{sec:.3f}"


def _utc(s: str | None) -> float | None:
    if not s:
        return None
    try:
        ts = pd.Timestamp(s)
    except (ValueError, TypeError):
        return None
    return (ts.tz_localize("UTC") if ts.tzinfo is None else ts).timestamp()


def _merge(base, upd):
    """F1's update rules: dicts merge key by key; a dict of 'index': value updates a list in place."""
    if isinstance(upd, dict):
        if isinstance(base, list):
            for k, v in upd.items():
                if k.startswith("_"):
                    continue
                try:
                    i = int(k)
                except ValueError:
                    continue
                while len(base) <= i:
                    base.append({})
                base[i] = _merge(base[i], v)
            return base
        if not isinstance(base, dict):
            base = {}
        for k, v in upd.items():
            if k == "_kf" or k == "_deleted":
                continue
            base[k] = _merge(base.get(k), v)
        return base
    return upd


# ---------------------------------------------------------------------------
# Board: the state at a moment, plus the lap history
# ---------------------------------------------------------------------------
@dataclass
class _Running:
    """What a driver has done since their last lap ended."""
    pit_in: bool = False          # went into the pit lane on this lap: an in-lap
    pit_out: bool = False         # started this lap in the pit lane: an out-lap
    next_out: bool = False        # left the pits after this lap's in-lap (timing line past pit exit)
    status: set = field(default_factory=set)
    rain: bool = False


class Board:
    """F1's live-timing state built from events, with each driver's completed laps."""

    def __init__(self) -> None:
        self.state: dict = {}
        self.t: float | None = None          # time of the last event applied
        self.laps: dict[tuple[str, int], dict] = {}
        self.deleted: set[tuple[str, int]] = set()
        self._run: dict[str, _Running] = {}
        self._track = "1"

    # -- applying events ----------------------------------------------------
    def apply(self, t: float, topic: str, data, snapshot: bool = False) -> None:
        self.t = t
        if topic == "TimingData" and not snapshot:
            self._timing_update(data)
            return
        if snapshot or topic not in self.state:
            self.state[topic] = _merge({} if isinstance(data, dict) else [], data) \
                if isinstance(data, (dict, list)) else data
        else:
            self.state[topic] = _merge(self.state[topic], data)
        if topic == "TrackStatus":
            self._track = str(self.state["TrackStatus"].get("Status", self._track))
            for r in self._run.values():
                r.status.add(self._track)
        elif topic == "WeatherData" and str(self.state["WeatherData"].get("Rainfall", "0")) not in ("0", ""):
            for r in self._run.values():
                r.rain = True
        elif topic == "RaceControlMessages":
            msgs = data.get("Messages") if isinstance(data, dict) else None
            for m in (msgs.values() if isinstance(msgs, dict) else msgs or []):
                text = str((m or {}).get("Message", ""))
                hit = _DELETED.search(text)
                if hit:
                    key = (hit.group(1), int(hit.group(2)))
                    (self.deleted.discard if "REINSTATED" in text else self.deleted.add)(key)

    def _timing_update(self, data: dict) -> None:
        td = self.state.setdefault("TimingData", {})
        lines = (data or {}).get("Lines") or {}
        for k, v in data.items():
            if k != "Lines":
                td[k] = _merge(td.get(k), v)
        all_lines = td.setdefault("Lines", {})
        for num, upd in lines.items():
            line = all_lines.setdefault(num, {})
            _merge(line, upd)
            run = self._run.setdefault(num, _Running(status={self._track}))
            if upd.get("InPit") is True:
                run.pit_in = True
            if upd.get("PitOut") is True:
                if run.pit_in:
                    run.next_out = True
                else:
                    run.pit_out = True
            last = upd.get("LastLapTime")
            if isinstance(last, dict) and last.get("Value"):
                self._lap_done(num, line, run)
            elif "NumberOfLaps" in upd:      # a lap with no time (lap 1 of a race): start afresh
                self._run[num] = _Running(status={self._track}, pit_out=bool(line.get("InPit")))

    def _lap_done(self, num: str, line: dict, run: _Running) -> None:
        lap_no = int(line.get("NumberOfLaps") or 0)
        if lap_no <= 0:
            return
        stint, compound, age, new = self._tyre(num)
        gap, down = parse_gap(line.get("GapToLeader"))
        interval, _ = parse_gap((line.get("IntervalToPositionAhead") or {}).get("Value"))
        pos = int(line["Position"]) if str(line.get("Position", "")).isdigit() else None
        self.laps[(num, lap_no)] = {
            "Number": num, "LapNumber": lap_no, "LapTime": parse_laptime(line["LastLapTime"]["Value"]),
            "Sector1Time": parse_laptime((_sector(line, 0) or {}).get("Value")),
            "Sector2Time": parse_laptime((_sector(line, 1) or {}).get("Value")),
            "Sector3Time": parse_laptime((_sector(line, 2) or {}).get("Value")),
            "Stint": stint, "Compound": compound, "TyreLife": age, "FreshTyre": new,
            "Position": pos, "GapToLeader": gap, "LapsDown": down,
            "GapToAhead": float("inf") if pos == 1 else interval,
            "PitIn": run.pit_in, "PitOut": run.pit_out,
            "TrackStatus": "".join(sorted(run.status | {self._track})), "Rainfall": run.rain,
            "Time": self.t, "SessionPart": self.state.get("TimingData", {}).get("SessionPart"),
        }
        rain = str(self.state.get("WeatherData", {}).get("Rainfall", "0")) not in ("0", "")
        self._run[num] = _Running(status={self._track}, rain=rain,
                                  pit_out=bool(line.get("InPit")) or run.next_out)

    def _tyre(self, num: str) -> tuple[int | None, str, float, bool | None]:
        stints = ((self.state.get("TimingAppData", {}).get("Lines") or {}).get(num) or {}).get("Stints") or []
        if isinstance(stints, dict):
            stints = [stints[k] for k in sorted(stints, key=int)]
        stints = [s for s in stints if isinstance(s, dict) and s]
        if not stints:
            return None, "UNKNOWN", float("nan"), None
        s = stints[-1]
        comp = str(s.get("Compound") or "UNKNOWN").upper()
        new = s.get("New")
        new = None if new is None else str(new).lower() == "true"
        age = s.get("TotalLaps")
        return len(stints), comp, float(age) if age is not None else float("nan"), new

    # -- reading the board ------------------------------------------------------
    def session(self) -> dict:
        info = self.state.get("SessionInfo") or {}
        meeting = info.get("Meeting") or {}
        kind = str(info.get("Type", ""))
        name = str(info.get("Name", ""))
        code = {"Race": "R", "Sprint": "S", "Qualifying": "Q", "Sprint Qualifying": "SQ",
                "Sprint Shootout": "SQ", "Practice 1": "FP1", "Practice 2": "FP2",
                "Practice 3": "FP3"}.get(name, "R" if kind == "Race" else "Q" if kind == "Qualifying" else "FP1")
        status = (self.state.get("SessionStatus") or {}).get("Status") or info.get("SessionStatus") or ""
        return {"key": info.get("Key"), "name": name, "type": kind, "code": code,
                "event": meeting.get("Name", ""), "location": meeting.get("Location", ""),
                "country": (meeting.get("Country") or {}).get("Name", ""),
                "status": status, "path": info.get("Path"),
                "start": info.get("StartDate"), "gmt_offset": info.get("GmtOffset")}

    def drivers(self) -> pd.DataFrame:
        rows = []
        for num, d in (self.state.get("DriverList") or {}).items():
            if not isinstance(d, dict) or not d.get("Tla"):
                continue
            colour = str(d.get("TeamColour") or "888888")
            rows.append({"Number": num, "Driver": d["Tla"], "FullName": d.get("FullName", ""),
                         "Team": d.get("TeamName", ""), "Color": f"#{colour.lstrip('#')}"})
        return pd.DataFrame(rows, columns=["Number", "Driver", "FullName", "Team", "Color"])

    def track_status(self) -> tuple[str, str]:
        ts = self.state.get("TrackStatus") or {}
        code = str(ts.get("Status", "1"))
        return code, TRACK_STATUS.get(code, ts.get("Message", ""))

    def lap_count(self) -> tuple[int | None, int | None]:
        lc = self.state.get("LapCount") or {}
        return lc.get("CurrentLap"), lc.get("TotalLaps")

    def remaining(self, now: float) -> float | None:
        """Seconds left on the session clock at `now` (it runs on between updates while extrapolating)."""
        clock = self.state.get("ExtrapolatedClock") or {}
        rem = clock.get("Remaining")
        if not rem:
            return None
        h, m, s = (float(x) for x in rem.split(":"))
        left = h * 3600 + m * 60 + s
        if clock.get("Extrapolating"):
            at = _utc(clock.get("Utc"))
            if at is not None:
                left -= max(0.0, now - at)
        return max(0.0, left)

    def weather(self) -> dict:
        w = self.state.get("WeatherData") or {}

        def num(k):
            try:
                return float(w.get(k))
            except (TypeError, ValueError):
                return float("nan")
        return {"AirTemp": num("AirTemp"), "TrackTemp": num("TrackTemp"), "Humidity": num("Humidity"),
                "WindSpeed": num("WindSpeed"), "Rainfall": str(w.get("Rainfall", "0")) not in ("0", "")}

    def race_control(self, n: int = 12) -> pd.DataFrame:
        msgs = (self.state.get("RaceControlMessages") or {}).get("Messages") or []
        if isinstance(msgs, dict):
            msgs = [msgs[k] for k in sorted(msgs, key=int)]
        rows = [{"Utc": pd.Timestamp(m["Utc"]).tz_localize("UTC") if m.get("Utc") else pd.NaT,
                 "Lap": m.get("Lap"), "Category": m.get("Category", ""), "Flag": m.get("Flag", ""),
                 "Message": m.get("Message", "")} for m in msgs if isinstance(m, dict) and m.get("Message")]
        return pd.DataFrame(rows[::-1][:n], columns=["Utc", "Lap", "Category", "Flag", "Message"])

    def lap_frame(self) -> pd.DataFrame:
        """Completed laps in FastF1's column names (LapTime etc. in seconds)."""
        cols = ["Driver", "Team", "Number", "LapNumber", "LapTime", "Sector1Time", "Sector2Time", "Sector3Time",
                "Stint", "Compound", "TyreLife", "FreshTyre", "Position", "GapToLeader", "LapsDown",
                "GapToAhead", "CleanAir", "PitIn", "PitOut", "TrackStatus", "Rainfall", "Deleted", "Time",
                "SessionPart"]
        if not self.laps:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(list(self.laps.values()))
        drv = self.drivers().set_index("Number")
        df["Driver"] = df["Number"].map(drv["Driver"]).fillna(df["Number"])
        df["Team"] = df["Number"].map(drv["Team"]).fillna("")
        df["CleanAir"] = df["GapToAhead"] > config.CLEAN_AIR_THRESHOLD_S
        df["Deleted"] = [(n, l) in self.deleted for n, l in zip(df["Number"], df["LapNumber"])]
        return df[cols].sort_values(["Driver", "LapNumber"]).reset_index(drop=True)

    def tower(self) -> pd.DataFrame:
        """One row per car in timing order: everything the timing screen shows."""
        td = self.state.get("TimingData") or {}
        part = int(td.get("SessionPart") or 0)
        drv = self.drivers().set_index("Number")
        stats = (self.state.get("TimingStats") or {}).get("Lines") or {}
        app = (self.state.get("TimingAppData") or {}).get("Lines") or {}
        rows = []
        for num, line in (td.get("Lines") or {}).items():
            if not isinstance(line, dict) or num not in drv.index:
                continue
            stint, comp, age, new = self._tyre(num)
            gap, down = parse_gap(line.get("GapToLeader"))
            interval, int_down = parse_gap((line.get("IntervalToPositionAhead") or {}).get("Value"))
            best = line.get("BestLapTime") or {}
            seg_best, seg_gap, seg_int = float("nan"), float("nan"), float("nan")
            if part:      # qualifying: best time, gap and interval in the current segment
                blt = line.get("BestLapTimes") or []
                st_ = line.get("Stats") or []
                if isinstance(blt, list) and len(blt) >= part:
                    seg_best = parse_laptime((blt[part - 1] or {}).get("Value"))
                if line.get("KnockedOut") and isinstance(blt, list):     # the time they went out with
                    seg_best = next((parse_laptime(x.get("Value")) for x in blt[::-1]
                                     if isinstance(x, dict) and x.get("Value")), seg_best)
                if isinstance(st_, list) and len(st_) >= part:
                    seg_gap = parse_laptime((st_[part - 1] or {}).get("TimeDiffToFastest"))
                    seg_int = parse_laptime((st_[part - 1] or {}).get("TimeDifftoPositionAhead"))
            last = line.get("LastLapTime") or {}
            pb = ((stats.get(num) or {}).get("PersonalBestLapTime") or {}).get("Value")
            sectors = [_sector(line, i) or {} for i in range(3)]
            pos = str(line.get("Position", ""))
            rows.append({
                "Number": num, "Pos": int(pos) if pos.isdigit() else 99, "Driver": drv.at[num, "Driver"],
                "Team": drv.at[num, "Team"], "Color": drv.at[num, "Color"],
                "Gap": gap, "LapsDown": down, "Interval": interval, "IntervalLaps": int_down,
                "Best": parse_laptime(best.get("Value")) if best.get("Value") else parse_laptime(pb),
                "SegBest": seg_best, "SegGap": seg_gap, "SegInterval": seg_int,
                "Last": parse_laptime(last.get("Value")), "LastFlag": _flag(last),
                **{f"S{i + 1}": parse_laptime(s.get("Value")) for i, s in enumerate(sectors)},
                **{f"S{i + 1}Flag": _flag(s) for i, s in enumerate(sectors)},
                "Laps": int(line.get("NumberOfLaps") or 0), "Pits": int(line.get("NumberOfPitStops") or 0),
                "Stint": stint, "Compound": comp, "TyreLife": age, "FreshTyre": new,
                "Grid": int(g) if str(g := (app.get(num) or {}).get("GridPos", "")).isdigit() else None,
                "InPit": bool(line.get("InPit")), "PitOut": bool(line.get("PitOut")),
                "Retired": bool(line.get("Retired")), "Stopped": bool(line.get("Stopped")),
                "KnockedOut": bool(line.get("KnockedOut")),
            })
        cols = ["Number", "Pos", "Driver", "Team", "Color", "Gap", "LapsDown", "Interval", "IntervalLaps", "Best",
                "SegBest", "SegGap", "SegInterval", "Last", "LastFlag", "S1", "S2", "S3", "S1Flag", "S2Flag",
                "S3Flag", "Laps", "Pits", "Stint", "Compound", "TyreLife", "FreshTyre", "Grid", "InPit", "PitOut",
                "Retired", "Stopped", "KnockedOut"]
        return pd.DataFrame(rows, columns=cols).sort_values("Pos").reset_index(drop=True)

    def quali_part(self) -> tuple[int, list[int]]:
        """(segment 1-3 or 0 outside qualifying, cars in each segment, e.g. [22, 16, 10])."""
        td = self.state.get("TimingData") or {}
        return int(td.get("SessionPart") or 0), list(td.get("NoEntries") or [])

    def stints(self) -> pd.DataFrame:
        """Every set of tyres each car has run: driver, stint, compound, new, laps on it."""
        rows = []
        drv = self.drivers().set_index("Number")
        for num, line in ((self.state.get("TimingAppData") or {}).get("Lines") or {}).items():
            st_ = (line or {}).get("Stints") or []
            if isinstance(st_, dict):
                st_ = [st_[k] for k in sorted(st_, key=int)]
            for i, s in enumerate(x for x in st_ if isinstance(x, dict) and x):
                if num in drv.index:
                    rows.append({"Driver": drv.at[num, "Driver"], "Stint": i + 1,
                                 "Compound": str(s.get("Compound", "UNKNOWN")).upper(),
                                 "New": str(s.get("New", "")).lower() == "true",
                                 "Laps": int(s.get("TotalLaps") or 0) - int(s.get("StartLaps") or 0),
                                 "StartAge": int(s.get("StartLaps") or 0)})
        return pd.DataFrame(rows, columns=["Driver", "Stint", "Compound", "New", "Laps", "StartAge"])


def _sector(line: dict, i: int) -> dict | None:
    s = line.get("Sectors")
    if isinstance(s, list):
        return s[i] if len(s) > i and isinstance(s[i], dict) else None
    if isinstance(s, dict):
        return s.get(str(i))
    return None


def _flag(d: dict) -> str:
    """'purple' fastest overall, 'green' personal best, '' otherwise."""
    if not isinstance(d, dict) or not d.get("Value"):
        return ""
    return "purple" if d.get("OverallFastest") else "green" if d.get("PersonalFastest") else ""


# ---------------------------------------------------------------------------
# Events -> board, incrementally
# ---------------------------------------------------------------------------
class BoardCursor:
    """A board kept up to date with a growing event list, up to a cut-off time. Moving the cut-off back
    (a longer delay, a replay rewound) or a new generation of the list (a new session, history loaded
    back in) rebuilds it from the start."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.board = Board()
        self.pos = 0
        self.cutoff = float("-inf")
        self.generation = 0

    def advance(self, events: list, cutoff: float, generation: int = 0) -> Board:
        if cutoff < self.cutoff or generation != self.generation:
            self.board, self.pos, self.generation = Board(), 0, generation
        n = len(events)
        while self.pos < n:
            t, topic, data, snap = events[self.pos]
            if t > cutoff:
                break
            self.board.apply(t, topic, data, snap)
            self.pos += 1
        self.cutoff = cutoff
        return self.board


# ---------------------------------------------------------------------------
# Live connection
# ---------------------------------------------------------------------------
class LiveFeed:
    """The live-timing connection, in a thread. `events` only ever grows (append under the lock)."""

    def __init__(self) -> None:
        self.events: list = []
        self.lock = threading.Lock()
        self.status = "idle"          # idle / connecting / connected / reconnecting / stopped / error
        self.error: str | None = None
        self.last_message: float | None = None
        self.last_poll = time.time()
        self.session_key = None
        self.generation = 0           # bumped whenever `events` is replaced rather than appended to
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._file = None
        self._pending: list = []      # events before SessionInfo names the file

    # -- page side --------------------------------------------------------------
    def view(self) -> tuple[int, list]:
        """(generation, events): the list is only appended to until the generation changes."""
        with self.lock:
            return self.generation, self.events

    def ensure_running(self) -> None:
        self.last_poll = time.time()
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="f1-live-timing", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- thread -------------------------------------------------------------------
    def _loop(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            if time.time() - self.last_poll > LIVE_IDLE_STOP_S:
                break
            self.status = "connecting" if attempt == 0 else "reconnecting"
            try:
                self._connect_once()
                attempt = 0
            except Exception as exc:  # noqa: BLE001 — keep trying while a page is watching
                self.error = str(exc) or type(exc).__name__
                log.warning("live timing: %s", self.error)
            wait = LIVE_RECONNECT_S[min(attempt, len(LIVE_RECONNECT_S) - 1)]
            attempt += 1
            self._stop.wait(wait)
        self.status = "stopped"
        if self._file:
            self._file.close()
            self._file = None

    def _connect_once(self) -> None:
        from signalrcore.hub_connection_builder import HubConnectionBuilder
        from signalrcore.messages.completion_message import CompletionMessage

        r = requests.options(NEGOTIATE_URL, timeout=20)
        cookie = r.cookies.get("AWSALBCORS")
        headers = {"Cookie": f"AWSALBCORS={cookie}"} if cookie else {}
        conn = HubConnectionBuilder().with_url(STREAM_URL, options={"verify_ssl": True, "headers": headers}) \
            .configure_logging(logging.ERROR).build()
        opened, closed = threading.Event(), threading.Event()

        def on_message(msg) -> None:
            now = time.time()
            self.last_message = now
            if isinstance(msg, CompletionMessage):     # the snapshot: SessionInfo first, it names the file
                for topic, data in sorted((msg.result or {}).items(), key=lambda kv: kv[0] != "SessionInfo"):
                    self._add((now, topic, data, True))
            elif isinstance(msg, list) and len(msg) >= 2:
                self._add((now, msg[0], msg[1], False))

        conn.on_open(opened.set)
        conn.on_close(closed.set)
        conn.on_error(lambda e: setattr(self, "error", str(getattr(e, "error", e))))
        conn.on("feed", on_message)
        conn.start()
        if not opened.wait(20):
            conn.stop()
            raise LiveUnavailable("couldn't connect to F1 live timing")
        self.status, self.error = "connected", None
        conn.send("Subscribe", [TOPICS], on_invocation=on_message)
        while not self._stop.is_set() and not closed.is_set():
            if time.time() - self.last_poll > LIVE_IDLE_STOP_S:
                break
            self._stop.wait(1)
        try:
            conn.stop()
        except Exception:  # noqa: BLE001
            pass
        if closed.is_set() and not self._stop.is_set():
            raise LiveUnavailable("the connection dropped")

    def _add(self, ev: tuple) -> None:
        t, topic, data, snap = ev
        if topic == "SessionInfo" and isinstance(data, dict) and data.get("Key") \
                and data.get("Key") != self.session_key:
            self._open_session(data["Key"], t)
        with self.lock:
            self.events.append(ev)
        line = json.dumps([t, topic, data, snap], separators=(",", ":"), default=str) + "\n"
        if self._file:
            self._file.write(line)
            self._file.flush()
        else:
            self._pending.append(line)

    def _open_session(self, key, t: float) -> None:
        """A new session: start a fresh event list with what was saved of it earlier (its lap history,
        after an app restart or a dropped connection), then keep appending to its file."""
        self.session_key = key
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        path = LIVE_DIR / f"{key}.jsonl"
        earlier = []
        if path.exists() and time.time() - path.stat().st_mtime < HISTORY_MAX_AGE_S:
            earlier = [e for e in read_events(path) if e[0] < t]
        with self.lock:
            self.events = earlier
            self.generation += 1
        if self._file:
            self._file.close()
        self._file = open(path, "a", encoding="utf-8")
        for line in self._pending:
            self._file.write(line)
        self._pending.clear()
        self._file.flush()


def read_events(path: Path) -> list:
    out = []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                t, topic, data, snap = json.loads(line)
            except (ValueError, TypeError):
                continue
            out.append((float(t), topic, data, bool(snap)))
    out.sort(key=lambda e: e[0])
    return out


# ---------------------------------------------------------------------------
# F1's static archive (replays)
# ---------------------------------------------------------------------------
def _get(url: str, timeout: int = 60) -> str:
    try:
        r = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise LiveUnavailable(f"F1's archive didn't answer: {exc}") from exc
    if r.status_code != 200:
        raise LiveUnavailable(f"F1's archive: HTTP {r.status_code} for {url.rsplit('/', 2)[-2]}")
    r.encoding = "utf-8-sig"
    return r.text


def archive_path(year: int, event: str, code: str) -> str:
    """The session's folder in F1's archive, e.g. '2024/2024-07-07_British_Grand_Prix/2024-07-07_Race/'."""
    index = json.loads(_get(f"{STATIC_URL}{year}/Index.json"))
    names = ARCHIVE_NAMES.get(code, (code,))
    want = event.lower().replace("grand prix", "").strip()
    for meeting in index.get("Meetings", []):
        m_name = str(meeting.get("Name", "")).lower()
        if m_name == event.lower() or (want and want in m_name):
            for s in meeting.get("Sessions", []):
                if s.get("Name") in names and s.get("Path"):
                    return s["Path"]
    raise LiveUnavailable(f"{year} {event} {code} isn't in F1's live-timing archive")


def archive_events(year: int, event: str, code: str) -> list:
    """Every event of a finished session from F1's archive, on the epoch clock (cached on disk)."""
    path = archive_path(year, event, code)
    cache = ARCHIVE_DIR / (path.strip("/").replace("/", "__") + ".jsonl.gz")
    if cache.exists():
        return read_events(cache)
    base = STATIC_URL + path
    events, beats = [], []
    for topic in TOPICS:
        try:
            text = _get(base + f"{topic}.jsonStream", timeout=120)
        except LiveUnavailable:
            continue
        first = True
        for line in text.splitlines():
            line = line.lstrip("﻿")
            if len(line) < 13 or line[12] not in "{[":
                continue
            h, m, s = line[:12].split(":")
            off = int(h) * 3600 + int(m) * 60 + float(s)
            try:
                data = json.loads(line[12:])
            except ValueError:
                continue
            if topic == "Heartbeat" and isinstance(data, dict) and data.get("Utc"):
                beats.append((off, data["Utc"]))
            events.append((off, topic, data, first))
            first = False
    if not events:
        raise LiveUnavailable("F1's archive has no timing for this session yet")
    # Stream offsets -> epoch: from the heartbeats (each carries the UTC time it was sent).
    if beats:
        zero = sorted(_utc(u) - off for off, u in beats if _utc(u) is not None)
        epoch0 = zero[len(zero) // 2]
    else:
        epoch0 = 0.0
    events = sorted(((epoch0 + off, topic, data, snap) for off, topic, data, snap in events), key=lambda e: e[0])
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(list(e), separators=(",", ":")) + "\n")
    tmp.replace(cache)
    return events


def session_start(events: list) -> float | None:
    """When the session went green (the first 'Started' status), else the first event."""
    for t, topic, data, _ in events:
        if topic == "SessionStatus" and isinstance(data, dict) and data.get("Status") == "Started":
            return t
    return events[0][0] if events else None


def session_end(events: list) -> float | None:
    """The last chequered flag (qualifying shows one per segment), else the last event."""
    ends = [t for t, topic, data, _ in events
            if topic == "SessionStatus" and isinstance(data, dict) and data.get("Status") in ("Finished", "Finalised")]
    return ends[-1] if ends else (events[-1][0] if events else None)


def clean_laps(laps: pd.DataFrame) -> pd.DataFrame:
    """The laps the degradation fits use, by data_engine.get_cleaned_laps's rules: no lap 1, in/out laps,
    SC/VSC/red laps, slick laps in rain, deleted laps or laps past 107 % of the driver's median on that
    compound."""
    if laps.empty:
        return laps
    neutral = laps["TrackStatus"].astype(str).apply(lambda s: any(c in s for c in config.NEUTRALISED_TRACK_STATUS))
    keep = ((laps["LapNumber"] > 1) & ~laps["PitIn"].astype(bool) & ~laps["PitOut"].astype(bool) & ~neutral
            & laps["LapTime"].notna() & laps["TyreLife"].notna() & (laps["Compound"] != "UNKNOWN")
            & ~(laps["Rainfall"].astype(bool) & laps["Compound"].isin(config.DRY_COMPOUNDS))
            & ~laps["Deleted"].astype(bool))
    clean = laps.loc[keep].copy()
    median = clean.groupby(["Driver", "Compound"])["LapTime"].transform("median")
    return clean[clean["LapTime"] <= median * config.OUTLIER_LAP_FACTOR].reset_index(drop=True)
