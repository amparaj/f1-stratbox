"""
modules/site_telemetry.py — the website's lap telemetry (web/public/data/telemetry/rNN-<code>.json).

Every driver's fastest valid lap of a session, from OpenF1's car_data (speed, RPM, gear, throttle,
brake, DRS; ~3.7 Hz) and location (x/y), two requests a driver, limited to the lap's time window.
The site compares any two of them in the browser (web/src/components/Telemetry.tsx), so one file
serves every pairing.

    raw = fetch(year, event, code)          # OpenF1 (+ MultiViewer corners), or the archive
    data = build(raw)                       # what the site reads
    export(year, ev, code, out_dir, budget) # both, and write the file

Shared distance axis: each lap's distance is speed integrated over time, pinned to the line at 0 s
and at the lap time; laps are scaled to the reference (the session's fastest) lap's length, so
two laps' times at the same index are at the same point and their difference is the gap there.
Samples every config.SITE_TEL_STEP_M metres: t (ms), speed, throttle, brake, gear, rpm, drs, and
`gaps` ([start, end] m) where the car data has a hole: the feed sometimes repeats one sample for
seconds (frozen runs are cut) or drops some. Distance across a hole comes from where the position feed
puts the car on the track outline at each end of it.
The track outline is the reference lap's x/y on the same axis; corners (number, x/y, rotation) come
from MultiViewer's circuit API (as FastF1's get_circuit_info), placed at the nearest outline point.

Archive: once the session itself is archived (final), the raw samples (compacted: columns, times
in ms from the lap's start) go to archive/openf1/<year>/rNN-<code>.tel.json.gz and are read from
there for good. Before that, requests go through openf1's cache; their URLs carry the lap's time
window, so they never need refreshing.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

import config
from modules import openf1

CAR_COLS = ("speed", "throttle", "brake", "n_gear", "rpm", "drs")
DRS_OPEN = 10


def archive_path(year: int, rnd: int, code: str) -> Path:
    return openf1.ARCHIVE_DIR / str(year) / f"r{rnd:02d}-{code}.tel.json.gz"


def _stamp(ts: pd.Timestamp) -> str:
    """OpenF1's date filter: UTC without the offset (a '+' in a URL reads as a space)."""
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _window(endpoint: str, sk: int, number: int, start: pd.Timestamp, seconds: float) -> list[dict]:
    a, b = start - pd.Timedelta(seconds=0.6), start + pd.Timedelta(seconds=seconds + 0.6)
    url = (f"{openf1.API}{endpoint}?session_key={sk}&driver_number={number}"
           f"&date>={_stamp(a)}&date<={_stamp(b)}")
    return openf1._get(url, {}, None)


def _columns(rows: list[dict], start: pd.Timestamp, cols: tuple[str, ...]) -> dict[str, list]:
    """OpenF1 rows -> {"t": ms from the lap's start, col: [...]} (the archive's compact form)."""
    if not rows:
        return {"t": [], **{c: [] for c in cols}}
    df = pd.DataFrame(rows).sort_values("date")
    t = (pd.to_datetime(df["date"], utc=True, format="ISO8601") - start).dt.total_seconds() * 1000
    out = {"t": t.round().astype(int).tolist()}
    for c in cols:
        v = df[c] if c in df else pd.Series([None] * len(df))
        out[c] = [None if pd.isna(x) else int(x) for x in v]
    return out


def _corners(year: int, circuit_key: int | None) -> dict | None:
    """MultiViewer's corner positions and map rotation (None if it doesn't answer)."""
    if circuit_key is None:
        return None
    try:
        from fastf1.mvapi import get_circuit_info
        ci = get_circuit_info(year=int(year), circuit_key=int(circuit_key))
    except Exception:  # noqa: BLE001 — corners are a nice-to-have
        return None
    if ci is None:
        return None
    return {"rotation": float(ci.rotation),
            "corners": [{"number": int(r.Number), "letter": str(r.Letter or ""), "x": float(r.X), "y": float(r.Y)}
                        for r in ci.corners.itertuples()]}


def fastest_laps(session) -> pd.DataFrame:
    """Each driver's fastest timed, not deleted lap (quickest first)."""
    laps = session.laps
    ok = laps["LapTime"].notna() & ~laps["Deleted"].fillna(False).astype(bool) & laps["LapStartTime"].notna()
    laps = laps[ok]
    return laps.loc[laps.groupby("Driver")["LapTime"].idxmin()].sort_values("LapTime").reset_index(drop=True)


def fetch(year: int, event: str, code: str, rnd: int, allow_download: bool = True) -> dict | None:
    """The session's raw lap telemetry: from the archive, else OpenF1 (None if not allowed to
    download, or OpenF1 has no car data for it). Archived once the session is."""
    path = archive_path(year, rnd, code)
    if path.exists():
        return json.loads(gzip.decompress(path.read_bytes()))
    if not allow_download:
        return None
    session = openf1.load_session(year, event, code)
    sk = session.info.get("session_key")
    if sk is None:
        return None
    laps = {}
    for lap in fastest_laps(session).itertuples():
        start = session.t0 + pd.Timedelta(seconds=float(lap.LapStartTime))
        number = int(lap.DriverNumber)
        car = _window("car_data", int(sk), number, start, float(lap.LapTime))
        if len(car) < 20:                         # OpenF1 has no car data for this lap
            continue
        pos = _window("location", int(sk), number, start, float(lap.LapTime))
        laps[lap.Driver] = {
            "number": number, "lap": int(lap.LapNumber), "start": start.isoformat(),
            "duration": float(lap.LapTime),
            "sectors": [None if pd.isna(v) else float(v) for v in (lap.Sector1Time, lap.Sector2Time, lap.Sector3Time)],
            "compound": None if lap.Compound is None or pd.isna(lap.Compound) else str(lap.Compound),
            "car": _columns(car, start, CAR_COLS),
            "pos": _columns(pos, start, ("x", "y")),
        }
    if not laps:
        return None
    raw = {"session_key": int(sk), "circuit": _corners(year, session.info.get("circuit_key")), "laps": laps}
    if session.archived:
        openf1._write_archive(path, raw)
    return raw


# ---------------------------------------------------------------------------
# The site's file
# ---------------------------------------------------------------------------
def _rotate(x: np.ndarray, y: np.ndarray, degrees: float) -> tuple[np.ndarray, np.ndarray]:
    a = np.deg2rad(degrees)
    return x * np.cos(a) - y * np.sin(a), x * np.sin(a) + y * np.cos(a)


FROZEN_S = 1.0          # every channel unchanged this long: the feed repeated a stale sample
GAP_S = 1.5             # car samples further apart than this leave a hole in the trace
MAX_MISSING = 1 / 3     # a lap with more of its time in holes than this is left out
MAX_DECEL = 220         # km/h lost per second: beyond about 6 g, so the earlier sample is stale
MAX_ACCEL = 80          # km/h gained per second: beyond what a car can do, so the earlier sample is stale


def _clean_car(lap: dict) -> pd.DataFrame:
    """The lap's car samples inside the lap without stale ones: F1's feed sometimes repeats one
    sample for seconds (speed stuck at 308 km/h into a braking zone), flagged by throttle/brake 104
    or by every channel unchanged; frozen runs are cut to their first sample."""
    car = pd.DataFrame(lap["car"]).dropna(subset=["speed"])
    car["t"] = car["t"] / 1000
    car = car[(car["t"] > 0) & (car["t"] < lap["duration"])].reset_index(drop=True)
    key = car[["speed", "rpm", "throttle", "brake", "n_gear"]].astype(str).agg("|".join, axis=1)
    run = (key != key.shift()).cumsum()
    span = car.groupby(run)["t"].transform(lambda x: x.max() - x.min())
    stale = (span >= FROZEN_S) & (run == run.shift())
    # Throttle and brake are 0-100; 104 means the feed has no reading for them. Such a sample that
    # also repeats the last speed is a stale copy; otherwise its speed, RPM and gear are kept.
    bad = (car["throttle"].fillna(0) > 100) | (car["brake"].fillna(0) > 100)
    stale |= bad & (car["speed"] == car["speed"].shift())
    car.loc[bad, ["throttle", "brake"]] = np.nan
    car = car[~stale].reset_index(drop=True)
    # A car can't lose more than MAX_DECEL or gain more than MAX_ACCEL km/h a second: a sample that
    # needs it to is stale (a held speed into a braking zone). Drop such samples until none is left.
    for _ in range(50):
        v, t = car["speed"].to_numpy(float), car["t"].to_numpy(float)
        rate = np.diff(v) / np.maximum(np.diff(t), 0.05)
        wrong = np.flatnonzero((rate < -MAX_DECEL) | (rate > MAX_ACCEL))
        if not len(wrong):
            break
        car = car.drop(index=car.index[wrong]).reset_index(drop=True)
    car[["throttle", "brake"]] = car[["throttle", "brake"]].interpolate(limit_direction="both")
    return car


Track = tuple[np.ndarray, np.ndarray, np.ndarray, float]   # outline x, y (m, rotated), distance, rotation


def _lap_distance(lap: dict, track: Track | None = None) -> tuple[np.ndarray, np.ndarray, pd.DataFrame, list[tuple[float, float]]]:
    """(time s, distance m) along the lap, pinned to the line at both ends; the car samples; and the
    holes in the car data (time spans). Across a hole the distance is where the car's position puts
    it on the track outline at each end of the hole (when `track` is given and the position feed
    has a sample near both ends that makes sense), else speed bridged in a straight line."""
    car = _clean_car(lap)
    first, last = car.iloc[[0]].assign(t=0.0), car.iloc[[-1]].assign(t=lap["duration"])
    car = pd.concat([first, car, last], ignore_index=True)
    t = car["t"].to_numpy(float)
    v = car["speed"].to_numpy(float) / 3.6
    step = (v[1:] + v[:-1]) / 2 * np.diff(t)
    holes = []
    pos = pd.DataFrame(lap["pos"]).dropna(subset=["x", "y"])
    pt = pos["t"].to_numpy(float) / 1000
    if track is not None and len(pos):
        ox, oy, od, rotation = track
        px, py = _rotate(pos["x"].to_numpy(float) / 10, pos["y"].to_numpy(float) / 10, rotation)
    for k in np.flatnonzero(np.diff(t) > GAP_S):
        a, b = t[k], t[k + 1]
        holes.append((float(a), float(b)))
        if track is None or not len(pos):
            continue
        ia, ib = int(np.argmin(np.abs(pt - a))), int(np.argmin(np.abs(pt - b)))
        if abs(pt[ia] - a) > 0.6 or abs(pt[ib] - b) > 0.6:
            continue
        on = lambda i: od[int(np.argmin((ox - px[i]) ** 2 + (oy - py[i]) ** 2))]  # noqa: E731
        da = on(ia) + v[k] * (a - pt[ia])
        db = on(ib) + v[k + 1] * (b - pt[ib])
        gained = (db - da) % od[-1]
        if 40 / 3.6 < gained / (b - a) < 370 / 3.6:          # a believable average speed
            step[k] = gained
    d = np.concatenate([[0.0], np.cumsum(step)])
    return t, d, car, holes


def _path(lap: dict, rotation: float) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """A lap's position samples against how far round the lap they are (0-1): fraction, x, y
    (m, rotated). None without position data (OpenF1 has none for some sessions)."""
    pos = pd.DataFrame(lap["pos"]).dropna(subset=["x", "y"])
    if len(pos) < 20:
        return None
    t, d, _, _ = _lap_distance(lap)
    frac = np.interp(pos["t"].to_numpy(float) / 1000, t, d) / d[-1]
    keep = np.r_[True, np.diff(frac) > 0]
    x, y = _rotate(pos["x"].to_numpy(float)[keep] / 10, pos["y"].to_numpy(float)[keep] / 10, rotation)
    return frac[keep], x, y


def build(raw: dict, others: tuple[dict, ...] = ()) -> dict | None:
    """The site's telemetry file: every lap on the reference lap's distance axis. `others`: the
    weekend's other sessions, for the track outline when this one has no position data."""
    laps = {}
    for k, v in raw["laps"].items():
        if len(v["car"]["t"]) < 20:
            continue
        holes = _lap_distance(v)[3]
        if sum(b - a for a, b in holes) <= MAX_MISSING * v["duration"]:
            laps[k] = v
    if not laps:
        return None
    # The reference (distance axis and track outline) is the fastest lap with position samples and,
    # if there is one, no holes in its car data.
    mapped = [k for k in laps if sum(v is not None for v in laps[k]["pos"]["x"]) >= 20] or list(laps)
    whole = [k for k in mapped if not _lap_distance(laps[k])[3]]
    mapped = whole or mapped
    ref_code = min(mapped, key=lambda k: laps[k]["duration"])
    ref = laps[ref_code]
    t_ref, d_ref, _, _ = _lap_distance(ref)
    length = float(d_ref[-1])
    n = int(round(length / config.SITE_TEL_STEP_M)) + 1
    grid = np.linspace(0, length, n)

    # Track outline: the reference lap's positions placed on its distance axis, else (no position
    # data for this session) the fastest mapped lap of another session that weekend.
    circuit = raw.get("circuit") or next((o.get("circuit") for o in others if o.get("circuit")), None) or {}
    rotation = circuit.get("rotation", 0.0)
    path = _path(ref, rotation)
    for other in others:
        if path is not None:
            break
        for lap in sorted(other["laps"].values(), key=lambda v: v["duration"]):
            if (path := _path(lap, rotation)) is not None:
                break
    if path is None:
        return None
    frac, xr, yr = path
    x, y = np.interp(grid / length, frac, xr), np.interp(grid / length, frac, yr)
    track = (x, y, grid, rotation)

    drivers, any_drs = {}, False
    for code, lap in laps.items():
        t, d, car, holes = _lap_distance(lap, track)
        d = np.maximum.accumulate(d * (length / d[-1])) + np.arange(len(d)) * 1e-6
        hole_d = [[round(float(np.interp(a, t, d)), 1), round(float(np.interp(b, t, d)), 1)] for a, b in holes]
        on = lambda col: np.interp(grid, d, car[col].to_numpy(float))  # noqa: E731
        drs = car["drs"].to_numpy(float) if car["drs"].notna().any() else None
        open_ = None
        if drs is not None:
            open_ = np.round(np.interp(grid, d, (drs >= DRS_OPEN).astype(float))).astype(int)
            any_drs |= bool(open_.any())
        drivers[code] = {
            "lap": lap["lap"], "time": round(lap["duration"], 3), "compound": lap["compound"],
            "sectors": [None if s is None else round(s, 3) for s in lap["sectors"]],
            "t": np.round(np.interp(grid, d, t) * 1000).astype(int).tolist(),
            "speed": np.round(on("speed")).astype(int).tolist(),
            "throttle": np.clip(np.round(on("throttle")), 0, 100).astype(int).tolist(),
            "brake": (on("brake") >= 50).astype(int).tolist() if car["brake"].max() > 1
            else np.round(on("brake")).astype(int).tolist(),
            "gear": np.round(on("n_gear")).astype(int).tolist(),
            "rpm": np.round(on("rpm"), -1).astype(int).tolist(),
            **({"drs": open_.tolist()} if open_ is not None else {}),
            **({"gaps": hole_d} if hole_d else {}),
        }
    if not any_drs:
        for v in drivers.values():
            v.pop("drs", None)

    corners = []
    for c in circuit.get("corners", []):
        cx, cy = _rotate(np.array([c["x"] / 10]), np.array([c["y"] / 10]), rotation)
        i = int(np.argmin((x - cx[0]) ** 2 + (y - cy[0]) ** 2))
        corners.append({"label": f"{c['number']}{c['letter']}", "d": round(float(grid[i]), 1),
                        "x": round(float(cx[0]), 1), "y": round(float(cy[0]), 1)})
    corners.sort(key=lambda c: c["d"])

    s = ref["sectors"]
    sector_d = None
    if s[0] is not None and s[1] is not None:
        sector_d = [round(float(np.interp(v, t_ref, d_ref)), 1) for v in (s[0], s[0] + s[1])]

    return {"step": round(length / (n - 1), 3), "length": round(length, 1), "reference": ref_code,
            "x": np.round(x, 1).tolist(), "y": np.round(y, 1).tolist(), "corners": corners,
            "sector_d": sector_d, "drs": any_drs, "drivers": drivers}


def export(year: int, ev: dict, code: str, out_dir: Path, budget: list[int]) -> str:
    """Write telemetry/rNN-<code>.json for one session. `budget` = [downloads left this run],
    spent when a session has to be fetched. Returns what happened, for the log."""
    rnd = int(ev["round"])
    archived = archive_path(year, rnd, code).exists()
    raw = fetch(year, ev["event"], code, rnd, allow_download=archived or budget[0] > 0)
    if not archived and raw is not None:
        budget[0] -= 1
    if raw is None:
        return "no telemetry" if archived or budget[0] > 0 else "telemetry deferred (download budget)"
    others = tuple(json.loads(gzip.decompress(q.read_bytes())) for c in config.WEEKEND_ORDER
                   if c != code and (q := archive_path(year, rnd, c)).exists())
    data = build(raw, others)
    if data is None:
        return "no telemetry"
    path = out_dir / "telemetry" / f"r{rnd:02d}-{code}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return f"telemetry: {len(data['drivers'])} laps"
