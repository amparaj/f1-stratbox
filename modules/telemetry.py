"""
modules/telemetry.py — car telemetry: the track replay and the lap head-to-head.

    load_telemetry_session()  FastF1 session with car + position data (cached resource, 2 kept)
    replay_payload()          a race's frames for the browser player (modules/replay_player.html)
    replay_html()             the player page with the payload embedded
    driver_lap_options()      a driver's timed laps, for the lap pickers
    lap_trace()               one lap's telemetry on distance: speed, throttle, brake, gear, x/y
    compare_laps()            two laps on a shared distance axis: delta, corner zones, lap shares

Telemetry always comes from FastF1 (F1's live-timing archive), whatever config.DATA_SOURCE says:
OpenF1 has the same feeds, but paging every car's 4 Hz data is far too slow at its rate limit.

Replay frames
-------------
Every car is resampled onto one clock at config.REPLAY_HZ. Its place in the race (`progress`, in
laps) is the laps it has completed (lap timing) plus how far round the current lap it is, found by
projecting its x/y onto the track outline (the session's fastest lap). Where that disagrees with
lap timing by more than a quarter lap (pit lane, off track, no position) progress falls back to
timing interpolated between line crossings. Running order ranks progress; a finished car is frozen
at its last lap (ties by who crossed the line first) and a retired car drops below every runner.
Gap to leader = how long ago the leader was where the car is now.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import json
from pathlib import Path

import fastf1
import numpy as np
import pandas as pd
import streamlit as st

import config
from modules import data_engine as de

MISSING = -32768                 # int16 "no value" in the replay arrays
PLAYER_FILE = Path(__file__).with_name("replay_player.html")
REPLAY_FIELDS = ("x", "y", "speed", "gear", "throttle", "brake", "drs", "pos", "gap")
DRS_OPEN = 10                    # FastF1 DRS codes 10/12/14 = flap open


class TelemetryUnavailableError(de.DataUnavailableError):
    """The session loaded but has no car or position data."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False, max_entries=2)
def load_telemetry_session(year: int, location: str, session_type: str):
    """
    A FastF1 session loaded with car telemetry and position data (a race is ~1.3 M rows, so only
    two are kept in memory). Goes through data_engine.get_session_info first, so a running or
    missing session raises the same errors as everywhere else. A recent session (still changing)
    skips FastF1's disk cache, as data_engine.load_session does.
    """
    de.initialize_fastf1()
    de.get_session_info(year, location, session_type)
    found = de._schedule_session(year, location, session_type)
    identifier, start = found if found else (session_type, None)
    recent = start is not None and pd.Timestamp.now(tz="UTC") - start < pd.Timedelta(days=config.RECENT_DAYS)
    session = fastf1.get_session(int(year), location, identifier)
    with fastf1.Cache.disabled() if recent else contextlib.nullcontext():
        session.load(laps=True, telemetry=True, weather=True, messages=True)
    try:
        ok = bool(session.car_data) and bool(session.pos_data)
    except Exception:  # noqa: BLE001 — DataNotLoadedError
        ok = False
    if not ok:
        raise TelemetryUnavailableError(f"No car telemetry for {year} {location} {session_type}.")
    return session


def _seconds(td) -> np.ndarray:
    return pd.to_timedelta(td).dt.total_seconds().to_numpy(dtype=float)


def _rotate(x: np.ndarray, y: np.ndarray, degrees: float) -> tuple[np.ndarray, np.ndarray]:
    """Turn FastF1 coordinates by the circuit's map rotation (track maps' usual orientation)."""
    a = np.deg2rad(degrees)
    return x * np.cos(a) - y * np.sin(a), x * np.sin(a) + y * np.cos(a)


def _circuit(session):
    """(rotation in degrees, corners DataFrame X/Y/Number/Letter) or (0, empty) if unknown."""
    try:
        ci = session.get_circuit_info()
        return float(ci.rotation), ci.corners.copy()
    except Exception:  # noqa: BLE001 — older seasons lack circuit info
        return 0.0, pd.DataFrame(columns=["X", "Y", "Number", "Letter"])


def _corner_label(row) -> str:
    return f"{int(row['Number'])}{row.get('Letter') or ''}"


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def _outline(session, rotation: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The fastest lap's path resampled every REPLAY_OUTLINE_STEP_M: x, y (m, rotated), distance."""
    lap = session.laps.pick_fastest()
    if lap is None:
        lap = session.laps.pick_quicklaps().pick_fastest()
    pos = lap.get_pos_data()
    x, y = _rotate(pos["X"].to_numpy(float) / 10, pos["Y"].to_numpy(float) / 10, rotation)  # dm -> m
    d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    grid = np.arange(0, d[-1], config.REPLAY_OUTLINE_STEP_M)
    x, y = np.interp(grid, d, x), np.interp(grid, d, y)
    return x, y, grid


def _lap_fraction(x: np.ndarray, y: np.ndarray, ox: np.ndarray, oy: np.ndarray,
                  od: np.ndarray) -> np.ndarray:
    """How far round the lap (0–1) the nearest outline point to each (x, y) is (NaN in, NaN out)."""
    out = np.full(len(x), np.nan)
    ok = np.flatnonzero(~np.isnan(x))
    length = od[-1]
    for i in range(0, len(ok), 1500):
        idx = ok[i:i + 1500]
        d2 = (x[idx, None] - ox[None, :]) ** 2 + (y[idx, None] - oy[None, :]) ** 2
        out[idx] = od[np.argmin(d2, axis=1)] / length
    return out


def _resample(t_src: np.ndarray, v: np.ndarray, grid: np.ndarray, step: bool = False) -> np.ndarray:
    """Values on the grid: linear (or last value, for gears/flags), NaN outside the data."""
    keep = ~np.isnan(t_src) & ~np.isnan(v)
    t_src, v = t_src[keep], v[keep]
    if len(t_src) < 2:
        return np.full(len(grid), np.nan)
    if step:
        i = np.clip(np.searchsorted(t_src, grid, side="right") - 1, 0, len(v) - 1)
        out = v[i].astype(float)
    else:
        out = np.interp(grid, t_src, v)
    out[(grid < t_src[0]) | (grid > t_src[-1])] = np.nan
    return out


def _pit_windows(dl: pd.DataFrame) -> list[tuple[float, float, int, str]]:
    """(pit-in s, pit-out s, in-lap, compound fitted) of every stop (a pit-lane start isn't one)."""
    ins = dl.dropna(subset=["PitInTime"])
    outs = dl[(dl["LapNumber"] > 1) & dl["PitOutTime"].notna()]
    windows = []
    for r in ins.itertuples():
        nxt = outs[outs["PitOutTime"] > r.PitInTime]
        if not nxt.empty and nxt["PitOutTime"].iloc[0] - r.PitInTime < 3600:
            windows.append((round(float(r.PitInTime), 1), round(float(nxt["PitOutTime"].iloc[0]), 1),
                            int(r.LapNumber), str(nxt["Compound"].iloc[0])))
    return windows


def _messages(session, t0: pd.Timestamp | None) -> list[list]:
    """Race-control messages worth a strategist's eye: [session s, lap, text, kind]."""
    try:
        rc = pd.DataFrame(session.race_control_messages)
    except Exception:  # noqa: BLE001 — messages not loaded
        return []
    if rc.empty or t0 is None:
        return []
    out = []
    for _, m in rc.iterrows():
        text, cat = str(m.get("Message", "")), str(m.get("Category", ""))
        flag, scope = str(m.get("Flag") or ""), str(m.get("Scope") or "")
        if cat == "Flag" and (flag in ("BLUE", "CLEAR") or (flag in ("YELLOW", "DOUBLE YELLOW") and scope == "Sector")):
            continue
        if "TRACK LIMITS" in text or ("DELETED" in text and "PENALTY" not in text):
            continue
        kind = ("flag" if cat == "Flag" else "sc" if cat == "SafetyCar" else "drs" if cat == "Drs"
                else "penalty" if ("PENALTY" in text or "INVESTIGATION" in text or "NOTED" in text) else "info")
        t = (pd.Timestamp(m["Time"]) - t0).total_seconds()
        lap = m.get("Lap")
        out.append([round(t, 1), int(lap) if pd.notna(lap) else None, text, kind])
    return out


@st.cache_data(show_spinner=False, max_entries=3)
def replay_payload(year: int, location: str, session_type: str) -> dict:
    """
    Everything the replay player needs for one race or sprint. `blob` is a gzip of int16 arrays,
    field-major then driver-major, each `frames` long (MISSING = no value): x, y (m), speed
    (km/h), gear, throttle (%), brake (0/1), drs (1 = open), pos (running position), gap
    (tenths of a second behind the leader; -n = n laps down). The rest is JSON: drivers with
    their laps, tyres and pit windows, the track outline and corners, track status, race-control
    messages, weather and derived events.
    """
    session = load_telemetry_session(year, location, session_type)
    info = de.get_session_info(year, location, session_type)
    laps = de.get_all_laps(year, location, session_type)
    rotation, corners = _circuit(session)
    ox, oy, od = _outline(session, rotation)

    lap1 = laps.loc[laps["LapNumber"] == 1, "LapStartTime"].dropna()
    ends = laps["Time"].dropna()
    t_start = float(lap1.min()) - 5 if not lap1.empty else float(ends.min()) - 120
    t_end = float(ends.max()) + 20
    grid = np.arange(t_start, t_end, 1 / config.REPLAY_HZ)
    n = len(grid)

    # A race clock that stops under red flags, for gaps: a stoppage between the leader passing a
    # point and a car behind passing it isn't gap.
    try:
        ts = pd.DataFrame(session.track_status)
        status = [[round(float(t), 1), str(s)] for t, s in zip(_seconds(ts["Time"]), ts["Status"])]
    except Exception:  # noqa: BLE001
        status = []
    red = np.zeros(n, bool)
    for (t_a, s_a), nxt in zip(status, status[1:] + [[t_end, ""]]):
        if s_a in config.RED_FLAG_STATUS_CODES:
            red |= (grid >= t_a) & (grid < nxt[0])
    clock = grid - np.cumsum(red) / config.REPLAY_HZ

    results = pd.DataFrame(session.results)
    number_of = dict(zip(results["Abbreviation"], results["DriverNumber"].astype(str))) if not results.empty else {}
    drivers = info["drivers"]
    total = info["total_laps"]
    grid_pos = pd.to_numeric(results.get("GridPosition"), errors="coerce") if not results.empty else pd.Series(dtype=float)
    grid_slot = {a: (g if g > 0 else 99) for a, g in zip(results.get("Abbreviation", []), grid_pos) if pd.notna(g)}

    fields = {f: np.full((len(drivers), n), np.nan) for f in REPLAY_FIELDS}
    progress = np.full((len(drivers), n), np.nan)
    rank_key = np.full((len(drivers), n), -1e6)
    finish_at = np.full(len(drivers), np.nan)
    meta_drivers, events = [], []
    fastest = np.inf

    for i, drv in enumerate(drivers.itertuples()):
        dl = laps[laps["Driver"] == drv.Driver].sort_values("LapNumber")
        num = number_of.get(drv.Driver)
        if dl.empty or num not in session.pos_data:
            meta_drivers.append(None)
            continue
        pos = session.pos_data[num]
        car = session.car_data[num]
        tp = _seconds(pos["SessionTime"])
        on_track = (pos["Status"] == "OnTrack").to_numpy() if "Status" in pos else np.ones(len(pos), bool)
        x, y = _rotate(pos["X"].to_numpy(float) / 10, pos["Y"].to_numpy(float) / 10, rotation)
        x[~on_track], y[~on_track] = np.nan, np.nan
        fields["x"][i] = _resample(tp, x, grid)
        fields["y"][i] = _resample(tp, y, grid)
        tc = _seconds(car["SessionTime"])
        fields["speed"][i] = _resample(tc, car["Speed"].to_numpy(float), grid)
        fields["gear"][i] = _resample(tc, car["nGear"].to_numpy(float), grid, step=True)
        fields["throttle"][i] = _resample(tc, car["Throttle"].to_numpy(float), grid)
        fields["brake"][i] = _resample(tc, car["Brake"].to_numpy(float), grid, step=True)
        fields["drs"][i] = _resample(tc, (car["DRS"].to_numpy(float) >= DRS_OPEN).astype(float), grid, step=True)

        # Progress: completed laps from timing + the fraction round the lap from position. Lap
        # timing alone (`by_time`) runs from each lap's start to its end, so a car parked between
        # laps (red flag) holds still.
        timed = dl.dropna(subset=["Time"])
        lap_ends = timed["Time"].to_numpy(float)
        lap_nums = timed["LapNumber"].to_numpy(float)
        knots = sorted({(float(r.LapStartTime), r.LapNumber - 1.0) for r in timed.itertuples() if pd.notna(r.LapStartTime)}
                       | set(zip(lap_ends, lap_nums)))
        kt, kp = np.array([k[0] for k in knots]), np.maximum.accumulate(np.array([k[1] for k in knots]))
        by_time = np.interp(grid, kt, kp)
        done = np.r_[0, lap_nums][np.searchsorted(lap_ends, grid, side="right")]
        frac = _lap_fraction(fields["x"][i], fields["y"][i], ox, oy, od)
        cands = done[None, :] + frac[None, :] + np.array([-1, 0, 1])[:, None]
        err = np.abs(cands - by_time[None, :])
        best = cands[np.argmin(np.where(np.isnan(err), np.inf, err), axis=0), np.arange(n)]
        # In the pit lane (a stop, a red flag, a pit-lane start) the nearest bit of track can be anywhere.
        in_pits = np.zeros(n, bool)
        for a, b, _, _ in _pit_windows(dl):
            in_pits |= (grid >= a) & (grid <= b)
        prog = np.where(np.isnan(best) | in_pits | (np.abs(best - by_time) > 0.25), by_time, best)
        first = dl.iloc[0]
        if pd.notna(first["PitOutTime"]) and int(first["LapNumber"]) == 1:
            prog[grid <= float(first["PitOutTime"])] = -0.5     # pit-lane start: behind the whole field
        prog = np.maximum.accumulate(np.nan_to_num(prog, nan=-1.0))     # a car never goes backwards
        # Cars level on laps with no position between (the flag, parked under red) rank by who
        # crossed the line first: a tie-break far below any real difference in progress.
        crossed = np.r_[0.0, lap_ends][np.searchsorted(lap_ends, grid, side="right")]
        last_end = float(lap_ends[-1]) if len(lap_ends) else float(kt[0])
        last_lap = int(lap_nums[-1]) if len(lap_nums) else 0
        if drv.DNF:
            # Out once the car stops after its last timed lap (or at that lap if it pitted in).
            after = (tc > last_end) & (car["Speed"].to_numpy(float) > 10)
            out_t = (tc[after].max() if after.any() else last_end) + 5
            prog = np.where(grid > out_t, prog[max(np.searchsorted(grid, out_t) - 1, 0)], prog)
            gone = grid > out_t
            for f in ("x", "y"):
                fields[f][i][gone] = np.nan
            rank_key[i] = np.where(gone, -1e3 + prog, prog - 1e-7 * crossed)
            events.append([round(out_t, 1), last_lap, f"{drv.Driver} retires", "out", drv.Driver])
        else:
            prog = np.where(grid >= last_end, last_lap, prog)
            finish_at[i] = last_end
            rank_key[i] = prog - 1e-7 * crossed
        progress[i] = prog

        stints = []
        for lap in dl.itertuples():
            stints.append([config.COMPOUND_SHORT.get(lap.Compound, "?"),
                           int(lap.TyreLife) if pd.notna(lap.TyreLife) else None])
            if pd.notna(lap.LapTime) and lap.LapNumber > 1 and lap.LapTime < fastest:
                fastest = lap.LapTime
                events.append([round(lap.Time, 1), int(lap.LapNumber),
                               f"Fastest lap {drv.Driver} {_fmt_lap(lap.LapTime)}", "fastest", drv.Driver])
        # A red flag's queue in the pit lane isn't a stop.
        pits = [w for w in _pit_windows(dl) if not red[(grid >= w[0]) & (grid <= w[1])].any()]
        for t_in, t_out, lap_in, comp in pits:
            events.append([t_in, lap_in, f"{drv.Driver} pits → {comp.title()} ({t_out - t_in:.1f} s in the lane)",
                           "pit", drv.Driver])
        meta_drivers.append({
            "code": drv.Driver, "number": num, "team": drv.Team, "color": drv.Color,
            "second": bool(drv.IsSecondDriver), "name": str(getattr(drv, "FullName", drv.Driver)),
            "dnf": bool(drv.DNF),
            "lapEnds": [round(float(v), 1) for v in lap_ends],
            "lapNums": [int(v) for v in lap_nums],
            "lapTimes": [round(float(v), 3) if pd.notna(v) else None for v in timed["LapTime"]],
            "tyres": stints, "tyreLaps": [int(v) for v in dl["LapNumber"]],
            "pits": [[t_in, t_out] for t_in, t_out, _, _ in pits],
        })

    # Running order and gap to the leader. Before the start it's the grid (pit-lane starters last);
    # under a red flag the order and gaps stand as they were when it came out (cars queue in the lane).
    for i, drv in enumerate(drivers.itertuples()):
        rank_key[i, grid < t_start + 5] = -grid_slot.get(drv.Driver, 99)
    red_runs = []
    for k0 in np.flatnonzero(red & ~np.r_[False, red[:-1]]):
        k1 = k0 + int(np.argmin(red[k0:])) if not red[k0:].all() else n
        red_runs.append((max(k0 - 1, 0), k0, k1))
    for before, k0, k1 in red_runs:
        rank_key[:, k0:k1] = rank_key[:, [before]]
        progress[:, k0:k1] = progress[:, [before]]
    order = np.argsort(-rank_key, axis=0, kind="stable")
    pos = np.empty_like(order)
    pos[order, np.arange(n)[None, :]] = np.arange(1, len(drivers) + 1)[:, None]
    fields["pos"] = pos.astype(float)
    lead = np.maximum.accumulate(np.nanmax(np.where(np.isnan(progress), -1, progress), axis=0))
    lead_curve = lead + np.arange(n) * 1e-9                       # strictly increasing for interp
    gap = clock[None, :] - np.vstack([np.interp(np.interp(p, lead_curve, grid), grid, clock) for p in progress])
    first_cross = laps.groupby("LapNumber")["Time"].min()       # the leader on each lap
    for i, t_fin in enumerate(finish_at):
        if not np.isnan(t_fin):                                   # hold the exact gap at the flag
            k = min(np.searchsorted(grid, t_fin), n - 1)
            gap[i, k:] = t_fin - first_cross.get(int(progress[i, -1]), t_fin)
    for before, k0, k1 in red_runs:
        gap[:, k0:k1] = gap[:, [before]]
    down = np.floor(lead[None, :] - progress + 1e-6)
    fields["gap"] = np.where(down >= 1, -down, np.clip(gap * 10, 0, 32000))

    blob = np.stack([np.where(np.isnan(fields[f]), MISSING, np.round(fields[f])).astype(np.int16)
                     for f in REPLAY_FIELDS])
    cx, cy = _rotate(corners["X"].to_numpy(float) / 10, corners["Y"].to_numpy(float) / 10, rotation)

    try:
        w = pd.DataFrame(session.weather_data)
        weather = [[round(float(t), 0), _num(r.AirTemp), _num(r.TrackTemp), bool(r.Rainfall),
                    _num(r.Humidity), _num(r.WindSpeed)]
                   for t, r in zip(_seconds(w["Time"]), w.itertuples())]
    except Exception:  # noqa: BLE001
        weather = []
    try:
        t0 = pd.Timestamp(session.t0_date)
    except Exception:  # noqa: BLE001
        t0 = None

    keep = [i for i, m in enumerate(meta_drivers) if m is not None]
    return {
        "title": f"{info['year']} {info['event_name']}",
        "session": info["session_label"], "location": info["location"],
        "totalLaps": total, "t0": round(float(grid[0]), 2), "raceStart": round(t_start + 5, 2), "hz": config.REPLAY_HZ, "frames": n,
        "drivers": [meta_drivers[i] for i in keep],
        "fields": list(REPLAY_FIELDS),
        "blob": base64.b64encode(gzip.compress(blob[:, keep, :].tobytes(), compresslevel=6, mtime=0)).decode(),
        "track": {"x": np.round(ox, 1).tolist(), "y": np.round(oy, 1).tolist()},
        "corners": [{"x": round(float(a), 1), "y": round(float(b), 1), "label": _corner_label(r)}
                    for a, b, (_, r) in zip(cx, cy, corners.iterrows())],
        "status": status,
        "messages": _messages(session, t0),
        "events": sorted(events, key=lambda e: e[0]),
        "weather": weather,
        "compoundColors": {config.COMPOUND_SHORT[k]: v for k, v in config.COMPOUND_COLORS.items()
                           if k in config.COMPOUND_SHORT},
    }


def _num(v) -> float | None:
    return round(float(v), 1) if pd.notna(v) else None


def _fmt_lap(seconds: float) -> str:
    m, s = divmod(float(seconds), 60)
    return f"{int(m)}:{s:06.3f}"


def replay_html(payload: dict) -> str:
    """The player page (modules/replay_player.html) with one race's payload embedded."""
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return PLAYER_FILE.read_text(encoding="utf-8").replace("/*__PAYLOAD__*/null", data)


# ---------------------------------------------------------------------------
# Lap head-to-head
# ---------------------------------------------------------------------------
def driver_lap_options(laps: pd.DataFrame, driver: str) -> list[tuple[int, float]]:
    """(lap number, lap time) of a driver's timed laps, fastest first."""
    dl = laps[(laps["Driver"] == driver) & laps["LapTime"].notna()]
    if "Deleted" in dl:
        dl = dl[dl["Deleted"] != True]  # noqa: E712 — NaN means not deleted
    dl = dl.sort_values("LapTime")
    return [(int(r.LapNumber), float(r.LapTime)) for r in dl.itertuples()]


@st.cache_data(show_spinner=False, max_entries=24)
def lap_trace(year: int, location: str, session_type: str, driver: str, lap_number: int) -> pd.DataFrame:
    """
    One lap's car telemetry on distance from the line. Columns Time (s into the lap), Distance
    (m), Speed (km/h), RPM, Gear, Throttle (%), Brake (0/1), DRS (open, bool), X, Y (m, rotated
    like the replay). attrs: lap_time, compound, tyre_life, lap_number.
    """
    session = load_telemetry_session(year, location, session_type)
    rotation, _ = _circuit(session)
    lap = session.laps.pick_drivers(driver)
    lap = lap[lap["LapNumber"] == lap_number].iloc[0]
    car = lap.get_car_data().add_distance()
    pos = lap.get_pos_data()
    t = _seconds(car["SessionTime"])
    tp = _seconds(pos["SessionTime"])
    x, y = _rotate(pos["X"].to_numpy(float) / 10, pos["Y"].to_numpy(float) / 10, rotation)
    out = pd.DataFrame({
        "Time": _seconds(car["Time"]),
        "Distance": car["Distance"].to_numpy(float),
        "Speed": car["Speed"].to_numpy(float),
        "RPM": car["RPM"].to_numpy(float),
        "Gear": car["nGear"].to_numpy(float),
        "Throttle": car["Throttle"].to_numpy(float).clip(0, 100),
        "Brake": car["Brake"].astype(float).to_numpy(),
        "DRS": car["DRS"].to_numpy(float) >= DRS_OPEN,
        "X": np.interp(t, tp, x), "Y": np.interp(t, tp, y),
    })
    lap_s = lap["LapTime"].total_seconds() if pd.notna(lap["LapTime"]) else float(out["Time"].iloc[-1])
    # Car data starts and stops a fraction of a second inside the lap: pin the lap to the line at
    # both ends (0 s, 0 m and the lap time, carried on at the last speed) so deltas end on the gap.
    first, last = out.iloc[[0]].copy(), out.iloc[[-1]].copy()
    first[["Time", "Distance"]] = 0.0
    last["Distance"] += last["Speed"] / 3.6 * max(lap_s - float(last["Time"].iloc[0]), 0.0)
    last["Time"] = lap_s
    out = pd.concat([first, out[(out["Time"] > 0) & (out["Time"] < lap_s)], last], ignore_index=True)
    out.attrs = {
        "lap_time": lap_s,
        "compound": str(lap["Compound"]) if pd.notna(lap["Compound"]) else "UNKNOWN",
        "tyre_life": int(lap["TyreLife"]) if pd.notna(lap["TyreLife"]) else None,
        "lap_number": int(lap_number),
    }
    return out


@st.cache_data(show_spinner=False, max_entries=6)
def session_corners(year: int, location: str, session_type: str) -> pd.DataFrame:
    """The circuit's corners: X, Y (m, rotated like lap_trace) and Label ("8", "9A")."""
    session = load_telemetry_session(year, location, session_type)
    rotation, corners = _circuit(session)
    if corners.empty:
        return pd.DataFrame(columns=["X", "Y", "Label"])
    x, y = _rotate(corners["X"].to_numpy(float) / 10, corners["Y"].to_numpy(float) / 10, rotation)
    return pd.DataFrame({"X": x, "Y": y, "Label": [_corner_label(r) for _, r in corners.iterrows()]})


def _lap_shares(tr: pd.DataFrame) -> dict[str, float]:
    """% of lap time flat out, braking, and the rest (cornering / partial throttle)."""
    dt = np.diff(tr["Time"].to_numpy(), append=tr["Time"].iloc[-1])
    total = dt.sum() or 1
    flat = dt[tr["Throttle"].to_numpy() >= config.FULL_THROTTLE_PCT].sum() / total * 100
    brake = dt[tr["Brake"].to_numpy() > 0].sum() / total * 100
    return {"full_throttle": flat, "braking": brake, "cornering": max(0.0, 100 - flat - brake),
            "top_speed": float(tr["Speed"].max())}


def compare_laps(a: pd.DataFrame, b: pd.DataFrame, corners: pd.DataFrame) -> dict:
    """
    Two laps on A's distance axis. B's distance is scaled to A's lap length (different lines
    cover slightly different distances), so the delta ends at exactly B's lap time - A's.

    Returns
      trace   DataFrame on A's samples: Distance, DeltaB (s, + = B behind), B's channels
              interpolated (SpeedB, RPMB, GearB, ThrottleB, BrakeB, DRSB)
      zones   DataFrame: Start, End (m), Kind ("corner"/"straight"), Label ("T8–12"),
              Class ("Low speed"...), MinA, MinB (km/h), Delta (s, + = A gained there)
      corner_dist  {label: distance on A's lap}
      shares  {"A": {...}, "B": {...}} from _lap_shares
    """
    da = a["Distance"].to_numpy()
    db = b["Distance"].to_numpy() * (da[-1] / b["Distance"].iloc[-1])
    tb = np.interp(da, db, b["Time"].to_numpy())
    trace = pd.DataFrame({"Distance": da, "DeltaB": tb - a["Time"].to_numpy()})
    for c in ("Speed", "RPM", "Gear", "Throttle", "Brake", "DRS"):
        trace[c + "B"] = np.interp(da, db, b[c].to_numpy(float))
    trace["DRSB"] = trace["DRSB"].round()

    # Corner positions on A's lap: the nearest point of A's path to each corner marker.
    corner_dist = {}
    ax, ay = a["X"].to_numpy(), a["Y"].to_numpy()
    for c in corners.itertuples():
        corner_dist[c.Label] = float(da[np.argmin((ax - c.X) ** 2 + (ay - c.Y) ** 2)])
    marks = sorted(corner_dist.items(), key=lambda kv: kv[1])

    groups: list[list[tuple[str, float]]] = []
    for label, d in marks:
        if groups and d - groups[-1][-1][1] < config.CORNER_GROUP_GAP_M:
            groups[-1].append((label, d))
        else:
            groups.append([(label, d)])
    bounds = []                                     # (start, end, label) for each corner zone
    for g in groups:
        s, e = g[0][1] - config.CORNER_ZONE_PAD_M, g[-1][1] + config.CORNER_ZONE_PAD_M
        name = f"T{g[0][0]}" if len(g) == 1 else f"T{g[0][0]}–{g[-1][0]}"
        bounds.append([max(s, 0.0), min(e, float(da[-1])), name])
    for prev, nxt in zip(bounds, bounds[1:]):       # overlapping pads meet halfway
        if prev[1] > nxt[0]:
            prev[1] = nxt[0] = (prev[1] + nxt[0]) / 2

    def delta_at(d: float) -> float:
        return float(np.interp(d, da, trace["DeltaB"].to_numpy()))

    zones, cursor = [], 0.0
    speed_a, speed_b = a["Speed"].to_numpy(), trace["SpeedB"].to_numpy()

    def add(start: float, end: float, kind: str, label: str) -> None:
        if end - start < 1:
            return
        m = (da >= start) & (da <= end)
        min_a = float(speed_a[m].min()) if m.any() else np.nan
        min_b = float(speed_b[m].min()) if m.any() else np.nan
        cls = next(name for lim, name in config.CORNER_SPEED_CLASSES if np.nanmean([min_a, min_b]) < lim) \
            if kind == "corner" else "Straight"
        zones.append({"Start": start, "End": end, "Kind": kind, "Label": label, "Class": cls,
                      "MinA": min_a, "MinB": min_b, "Delta": delta_at(end) - delta_at(start)})

    for s, e, name in bounds:
        add(cursor, s, "straight", "Straight")
        add(s, e, "corner", name)
        cursor = e
    add(cursor, float(da[-1]), "straight", "Straight")
    return {"trace": trace, "zones": pd.DataFrame(zones), "corner_dist": corner_dist,
            "shares": {"A": _lap_shares(a), "B": _lap_shares(b)}}
