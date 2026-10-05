"""
modules/data_engine.py — FastF1 ingestion, cleaning and shaping.

Pipeline
--------
    load_session()        tiered FastF1 load with graceful fallback (cached resource)
    get_all_laps()        every lap, timedeltas -> float seconds, gap-to-car-ahead
    get_cleaned_laps()    model-grade laps: no pit/SC/VSC/lap-1/outliers, CleanAir flag
    get_race_timeline()   per-lap running order + gap to leader (DNFs truncated)
    get_session_info()    event metadata, driver colours, neutralised laps
    get_quali_laps()      qualifying: every lap with its segment (Q1/Q2/Q3), sectors, speed trap
    get_quali_results()   qualifying: Q1/Q2/Q3 times, gap to pole, cut-off margins, pace
    quali_sectors()       qualifying: best sectors, ideal lap, top speed per driver

Session codes: "R" Grand Prix, "S" Sprint, "Q" Qualifying, "SQ" Sprint Qualifying
(config.SESSION_NAMES).

All public DataFrame-returning functions are wrapped in @st.cache_data keyed on
(year, event, session_type) so a debrief room flipping between pages never
re-hits the F1 API. FastF1's own disk cache (.fastf1/) sits underneath that.

Streamlit UI helpers that every page shares (the session picker) live at the
bottom of this module so the file tree stays as specified.
"""

from __future__ import annotations

import datetime as dt
import logging

import fastf1
import numpy as np
import pandas as pd
import streamlit as st

import config

log = logging.getLogger(__name__)


class DataUnavailableError(RuntimeError):
    """Raised when no usable lap-timing data could be loaded for a session."""


class SessionRunningError(DataUnavailableError):
    """The session hasn't finished yet: no chequered flag (config.session_finished)."""


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
def initialize_fastf1() -> None:
    """Enable FastF1's on-disk cache in the project's .fastf1/ directory."""
    config.FASTF1_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(config.FASTF1_CACHE_DIR))
    fastf1.set_log_level("WARNING")


def _schedule_session(year: int, location: str, session_type: str) -> tuple[str, pd.Timestamp] | None:
    """(FastF1 session name, scheduled start in tz-aware UTC) of a session code, or None."""
    try:
        event = fastf1.get_event(int(year), location)
    except Exception:  # noqa: BLE001
        return None
    for i in range(1, 6):
        name = event.get(f"Session{i}")
        if name in config.session_names(session_type) and pd.notna(event.get(f"Session{i}DateUtc")):
            return str(name), pd.Timestamp(event[f"Session{i}DateUtc"]).tz_localize("UTC")
    return None


def _session_start(year: int, location: str, session_type: str) -> pd.Timestamp | None:
    """Scheduled start of a session ('R', 'S', 'Q', 'SQ'), tz-aware UTC, or None."""
    found = _schedule_session(year, location, session_type)
    return found[1] if found else None


def _fastf1_chequered(session, session_type: str) -> pd.Timestamp | None:
    """When FastF1's race-control messages show the flag that ends the session, or None."""
    try:
        rcm = session.race_control_messages
    except Exception:  # noqa: BLE001 — messages not loaded
        return None
    if rcm is None or rcm.empty or "Flag" not in rcm:
        return None
    t = pd.to_datetime(rcm.loc[rcm["Flag"].astype(str).str.upper() == "CHEQUERED", "Time"])
    flag = config.last_chequered(list(t), session_type)
    return pd.Timestamp(flag).tz_localize("UTC") if flag is not None else None


# ---------------------------------------------------------------------------
# Type conversion
# ---------------------------------------------------------------------------
def timedeltas_to_seconds(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert every timedelta64[ns] column to float seconds (NaT -> NaN).

    NumPy/SciPy cannot regress on timedeltas, so this runs before any maths.
    Column names are kept, so 'LapTime' becomes raw seconds as a float.
    """
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_timedelta64_dtype(out[col]):
            out[col] = out[col].dt.total_seconds().astype(float)
    return out


# ---------------------------------------------------------------------------
# Session loading (tiered, with graceful fallback)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False, max_entries=6)
def load_session(year: int, location: str, session_type: str, with_telemetry: bool = False):
    """
    Load a FastF1 session, degrading gracefully if feeds are missing.

    Tier 1 'full'        laps + weather + race-control messages (+ car telemetry if asked)
    Tier 2 'timing-only' session.laps only — historical lap-timing profile

    Returns (session, info) where info = {"tier": str, "warnings": [str]}.
    Raises SessionRunningError before the chequered flag, DataUnavailableError if even lap
    timing cannot be obtained.

    A session less than config.RECENT_DAYS old is loaded past FastF1's disk cache, which
    would otherwise keep a half-finished download (and the classification before penalties)
    for good; it must also have a chequered flag, so only the full tier is tried.
    """
    initialize_fastf1()
    if config.DATA_SOURCE == "openf1":
        from modules import openf1
        try:
            return openf1.load_session(year, location, session_type), {"tier": "openf1", "warnings": []}
        except openf1.SessionRunning as exc:
            raise SessionRunningError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — same contract as the FastF1 tiers
            raise DataUnavailableError(f"No OpenF1 data for {year} {location} {session_type}: {exc}") from exc
    tiers = [
        ("full", dict(laps=True, telemetry=with_telemetry, weather=True, messages=True)),
        ("timing-only", dict(laps=True, telemetry=False, weather=False, messages=False)),
    ]
    now = pd.Timestamp.now(tz="UTC")
    found = _schedule_session(year, location, session_type)
    # FastF1 takes the schedule's name ("Sprint Shootout" in 2023), the code as a fallback.
    identifier, start = found if found else (session_type, None)
    recent = start is not None and now - start < pd.Timedelta(days=config.RECENT_DAYS)
    if recent:
        tiers = tiers[:1]
    warnings: list[str] = []
    for tier, kwargs in tiers:
        try:
            session = fastf1.get_session(int(year), location, identifier)
            if recent:
                with fastf1.Cache.disabled():
                    session.load(**kwargs)
                if not config.session_finished(start, _fastf1_chequered(session, session_type), now, session_type):
                    raise SessionRunningError(f"{year} {location} {session_type} hasn't finished yet")
            else:
                session.load(**kwargs)
            laps = session.laps  # raises DataNotLoadedError if timing is absent
            if laps is None or laps.empty:
                raise DataUnavailableError("lap table is empty")
            return session, {"tier": tier, "warnings": warnings}
        except SessionRunningError:
            raise
        except Exception as exc:  # noqa: BLE001 — any feed failure drops a tier
            warnings.append(f"{tier} load failed: {exc}")
            log.warning("FastF1 %s load failed for %s %s %s: %s",
                        tier, year, location, session_type, exc)
    if recent and start + pd.Timedelta(hours=config.LATEST_FINISH_H[session_type]) > now:
        raise SessionRunningError(f"{year} {location} {session_type}: no timing yet. " + " | ".join(warnings))
    raise DataUnavailableError(
        f"No lap timing available for {year} {location} {session_type}. "
        + " | ".join(warnings)
    )


# ---------------------------------------------------------------------------
# Lap-level data
# ---------------------------------------------------------------------------
def _add_gap_to_car_ahead(laps: pd.DataFrame) -> pd.DataFrame:
    """
    Gap to the car ahead at the timing line, per lap.

    For each lap number, cars are ordered by the session time at which they
    completed that lap; the interval is the difference to the previous car.
    The lap leader has an infinite gap (always clean air).
    """
    laps = laps.sort_values(["LapNumber", "Time"]).copy()
    laps["GapToAhead"] = laps.groupby("LapNumber")["Time"].diff()
    first_in_lap = laps.groupby("LapNumber").cumcount() == 0
    laps.loc[first_in_lap & laps["Time"].notna(), "GapToAhead"] = np.inf
    return laps


def _add_rainfall_flag(laps: pd.DataFrame, session) -> pd.DataFrame:
    """
    Flag laps where the weather station reported rainfall at the lap's start or end.
    Silently marks everything dry when the weather feed is unavailable
    (e.g. 'timing-only' fallback tier).
    """
    laps["Rainfall"] = False
    try:
        weather = timedeltas_to_seconds(pd.DataFrame(session.weather_data))
        weather = weather[["Time", "Rainfall"]].dropna().sort_values("Time")
    except Exception:  # noqa: BLE001 — weather not loaded
        return laps
    if weather.empty:
        return laps
    w_time = weather["Time"].to_numpy()
    w_rain = weather["Rainfall"].astype(bool).to_numpy()

    def rain_at(t: pd.Series) -> np.ndarray:
        idx = np.clip(np.searchsorted(w_time, t.fillna(-1).to_numpy(), side="right") - 1, 0, None)
        return w_rain[idx] & t.notna().to_numpy()

    laps["Rainfall"] = rain_at(laps["LapStartTime"]) | rain_at(laps["Time"])
    return laps


@st.cache_data(show_spinner=False, max_entries=12)
def get_all_laps(year: int, location: str, session_type: str) -> pd.DataFrame:
    """Every lap of the session with float-second timings and GapToAhead."""
    session, _ = load_session(year, location, session_type)
    laps = timedeltas_to_seconds(pd.DataFrame(session.laps))
    laps["LapNumber"] = laps["LapNumber"].astype(int)
    laps["Compound"] = laps["Compound"].fillna("UNKNOWN").replace("", "UNKNOWN")
    laps["TrackStatus"] = laps["TrackStatus"].fillna("").astype(str)
    laps["PitIn"] = laps["PitInTime"].notna()
    laps["PitOut"] = laps["PitOutTime"].notna()
    laps = _add_gap_to_car_ahead(laps)
    laps = _add_rainfall_flag(laps, session)
    # Clean-air rule from the spec: strictly more than 1.5 s to the car ahead.
    laps["CleanAir"] = laps["GapToAhead"] > config.CLEAN_AIR_THRESHOLD_S
    return laps.reset_index(drop=True)


@st.cache_data(show_spinner=False, max_entries=12)
def get_cleaned_laps(year: int, location: str, session_type: str) -> pd.DataFrame:
    """
    Model-grade laps for degradation fitting.

    Removes: lap 1 (standing start), pit-in and pit-out laps, any lap run
    under SC / VSC / red flag, slick laps run while it was raining (a rain
    slowdown is not tyre degradation), laps without a time or tyre age,
    deleted laps, and per-driver/compound outliers slower than
    OUTLIER_LAP_FACTOR x their median.
    Keeps the CleanAir boolean so analytics can choose clean-air-only fits.
    LapTime is float seconds.
    """
    laps = get_all_laps(year, location, session_type)

    neutralised = laps["TrackStatus"].apply(
        lambda s: any(code in s for code in config.NEUTRALISED_TRACK_STATUS)
    )
    keep = (
        (laps["LapNumber"] > 1)
        & ~laps["PitIn"]
        & ~laps["PitOut"]
        & ~neutralised
        & laps["LapTime"].notna()
        & laps["TyreLife"].notna()
        & (laps["Compound"] != "UNKNOWN")
        & ~(laps["Rainfall"] & laps["Compound"].isin(config.DRY_COMPOUNDS))
        & ~laps.get("Deleted", pd.Series(False, index=laps.index)).fillna(False).astype(bool)
    )
    clean = laps.loc[keep].copy()

    # Robust outlier strip (traffic, damage, local yellows). Per driver AND
    # compound so slow-but-legitimate wet-tyre laps aren't judged against slicks.
    median = clean.groupby(["Driver", "Compound"])["LapTime"].transform("median")
    clean = clean[clean["LapTime"] <= median * config.OUTLIER_LAP_FACTOR]

    cols = ["Driver", "Team", "LapNumber", "Stint", "Compound", "TyreLife",
            "LapTime", "Position", "GapToAhead", "CleanAir", "TrackStatus"]
    return clean[cols].sort_values(["Driver", "LapNumber"]).reset_index(drop=True)


@st.cache_data(show_spinner=False, max_entries=12)
def get_race_timeline(year: int, location: str, session_type: str) -> pd.DataFrame:
    """
    Per-driver, per-lap running order and cumulative gap to the leader.

    DNF handling: a driver's rows end at their last lap with a valid timing
    line crossing. Nothing is forward-filled, so traces simply stop and no
    driver ever gets padded to the leader's lap count (no length mismatches).
    """
    laps = get_all_laps(year, location, session_type)
    tl = laps[laps["Time"].notna()].copy()

    # Truncate each driver at their final valid lap (handles trailing NaN rows).
    last_valid = tl.groupby("Driver")["LapNumber"].transform("max")
    tl = tl[tl["LapNumber"] <= last_valid]

    tl["LeaderTime"] = tl.groupby("LapNumber")["Time"].transform("min")
    tl["GapToLeader"] = tl["Time"] - tl["LeaderTime"]
    tl["RunningPosition"] = tl.groupby("LapNumber")["Time"].rank(method="first").astype(int)

    cols = ["Driver", "Team", "LapNumber", "RunningPosition", "Time", "GapToLeader",
            "GapToAhead", "LapTime", "Compound", "TyreLife", "Stint", "PitIn",
            "PitOut", "TrackStatus", "Rainfall"]
    return tl[cols].sort_values(["LapNumber", "RunningPosition"]).reset_index(drop=True)


def _session_t0_utc(session) -> pd.Timestamp | None:
    """The UTC moment session times (lap `Time`, weather `Time`) count from, if known."""
    t0 = getattr(session, "t0", None)                    # modules/openf1.Session
    if t0 is None:
        try:
            t0 = session.t0_date                         # FastF1: naive UTC
        except Exception:  # noqa: BLE001 — not loaded
            return None
    if t0 is None or pd.isna(t0):
        return None
    t0 = pd.Timestamp(t0)
    return t0.tz_localize("UTC") if t0.tzinfo is None else t0.tz_convert("UTC")


WEATHER_COLUMNS = ["AirTemp", "TrackTemp", "Humidity", "WindSpeed", "Rainfall"]


@st.cache_data(show_spinner=False, max_entries=12)
def get_lap_weather(year: int, location: str, session_type: str) -> pd.DataFrame:
    """
    The track's weather sensors lap by lap: the latest reading when the leader finished each
    lap. Columns LapNumber, Time (session s), UTC, AirTemp, TrackTemp (°C), Humidity (%),
    WindSpeed (m/s), Rainfall. Empty when the weather feed isn't loaded (timing-only tier).
    """
    session, _ = load_session(year, location, session_type)
    laps = get_all_laps(year, location, session_type)
    out = laps.dropna(subset=["Time"]).groupby("LapNumber", as_index=False)["Time"].min()
    try:
        w = timedeltas_to_seconds(pd.DataFrame(session.weather_data))
    except Exception:  # noqa: BLE001 — weather not loaded
        w = pd.DataFrame()
    if w.empty or "Time" not in w:
        return pd.DataFrame(columns=["LapNumber", "Time", "UTC", *WEATHER_COLUMNS])
    w = w[["Time", *[c for c in WEATHER_COLUMNS if c in w]]].dropna(subset=["Time"]).sort_values("Time")
    # The feed has the odd zero temperature reading: treat it as missing.
    for c in ("AirTemp", "TrackTemp"):
        if c in w:
            w[c] = w[c].where(w[c] > 0)
    out = pd.merge_asof(out.sort_values("Time"), w, on="Time", direction="backward")
    out = out.reindex(columns=["LapNumber", "Time", *WEATHER_COLUMNS])
    out["Rainfall"] = out["Rainfall"].fillna(False).astype(bool)
    t0 = _session_t0_utc(session)
    if t0 is None:
        # FastF1 needs car data for t0_date: lap 1 starts at the scheduled start instead.
        start = _session_start(year, location, session_type)
        lap1 = laps.loc[laps["LapNumber"] == 1, "LapStartTime"].min()
        if start is not None and pd.notna(lap1):
            t0 = start - pd.Timedelta(seconds=float(lap1))
    out.insert(2, "UTC", t0 + pd.to_timedelta(out["Time"], unit="s") if t0 is not None else pd.NaT)
    return out.reset_index(drop=True)


def _neutralised_laps(laps: pd.DataFrame) -> dict[str, list[int]]:
    """Lap numbers run (at least partly) under SC, VSC or red flag."""
    by_lap = laps.groupby("LapNumber")["TrackStatus"].apply(lambda s: "".join(s))
    out = {"SC": [], "VSC": [], "RED": []}
    for lap, codes in by_lap.items():
        if any(c in codes for c in config.RED_FLAG_STATUS_CODES):
            out["RED"].append(int(lap))
        elif any(c in codes for c in config.SC_STATUS_CODES):
            out["SC"].append(int(lap))
        elif any(c in codes for c in config.VSC_STATUS_CODES):
            out["VSC"].append(int(lap))
    return out


@st.cache_data(show_spinner=False, max_entries=12)
def get_session_info(year: int, location: str, session_type: str) -> dict:
    """Event metadata, driver table (team, colour, result) and neutralised laps."""
    session, load_info = load_session(year, location, session_type)
    laps = get_all_laps(year, location, session_type)

    results = pd.DataFrame(session.results) if session.results is not None else pd.DataFrame()
    drivers = laps.groupby("Driver").agg(Team=("Team", "first"),
                                         LastLap=("LapNumber", "max")).reset_index()
    if not results.empty and "Abbreviation" in results:
        res = results.rename(columns={"Abbreviation": "Driver"})
        wanted = ["Driver", "TeamColor", "Status", "ClassifiedPosition", "Position", "FullName"]
        drivers = drivers.merge(res[[c for c in wanted if c in res.columns]],
                                on="Driver", how="left")

    # Team colour with fallback palette; teammates share a hue (domain convention),
    # so pages add a dash/symbol as the secondary encoding.
    fallback = iter(config.FALLBACK_DRIVER_COLORS * 3)
    colors = []
    for raw in drivers.get("TeamColor", pd.Series([None] * len(drivers))):
        raw = str(raw) if raw is not None and str(raw) not in ("", "nan", "None") else None
        colors.append(f"#{raw.lstrip('#')}" if raw else next(fallback))
    drivers["Color"] = colors
    drivers["IsSecondDriver"] = drivers.groupby("Team").cumcount() > 0

    if "Position" in drivers:
        drivers = drivers.sort_values("Position", na_position="last")
    status = drivers.get("Status", pd.Series([""] * len(drivers))).fillna("").astype(str)
    drivers["DNF"] = ~status.str.contains("Finished|Lapped|^\\+", regex=True) & (status != "")

    total_laps = getattr(session, "total_laps", None)
    if not total_laps or pd.isna(total_laps):
        total_laps = int(laps["LapNumber"].max())

    # Provisional: a recent session whose official classification or starting grid isn't in
    # yet (the website's "complete" rule in site_export._results).
    start = _session_start(year, location, session_type)
    recent = start is not None and pd.Timestamp.now(tz="UTC") - start < pd.Timedelta(days=config.RECENT_DAYS)
    has = lambda c: c in results and results[c].replace("", np.nan).notna().any()  # noqa: E731
    if session_type in config.QUALI_CODES:
        provisional = recent and not (has("Position") and has("Q1"))
    else:
        provisional = recent and not (has("Points") and has("Status") and has("GridPosition"))

    event = session.event
    return {
        "year": int(year),
        "event_name": str(event.get("EventName", location)),
        "location": str(event.get("Location", location)),
        "country": str(event.get("Country", "")),
        "session_name": str(getattr(session, "name", session_type)),
        "session_code": session_type,
        "session_label": config.SESSION_LABELS[session_type],
        "total_laps": int(total_laps),
        "drivers": drivers.reset_index(drop=True),
        "neutralised": _neutralised_laps(laps),
        "load_tier": load_info["tier"],
        "load_warnings": load_info["warnings"],
        "provisional": bool(provisional),
        "start_utc": start,
        # Data that didn't come from the session's own source (openf1.Session.sources), e.g.
        # {"laps": "F1 live timing (FastF1)"} for a gap filled by scripts/backfill_fastf1.py.
        "sources": dict(getattr(session, "sources", {}) or {}),
    }


def get_event_sessions(year: int) -> dict[str, list[str]]:
    """Event name -> the codes of its sessions that could have finished by now, in weekend
    order (SQ, S, Q, R), for every event with at least one."""
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    out = {}
    for name, starts in _session_starts(year):
        done = [c for c in config.WEEKEND_ORDER if c in starts
                and starts[c] + pd.Timedelta(minutes=config.EARLIEST_FINISH_MIN[c]) <= now]
        if done:
            out[name] = done
    return out


def get_event_names(year: int) -> list[str]:
    """Names of events in a season with a session that could have finished by now."""
    return list(get_event_sessions(year))


@st.cache_data(show_spinner=False, ttl=6 * 3600)
def _session_starts(year: int) -> list[tuple[str, dict[str, pd.Timestamp]]]:
    """(event name, {session code: start in naive UTC}) for every event of a season."""
    initialize_fastf1()
    try:
        sched = fastf1.get_event_schedule(int(year), include_testing=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("Schedule fetch failed for %s: %s", year, exc)
        return []
    code_of = {n: c for c in config.SESSION_NAMES for n in config.session_names(c)}
    out = []
    for _, ev in sched.iterrows():
        starts = {code_of[ev[f"Session{i}"]]: pd.Timestamp(ev[f"Session{i}DateUtc"]) for i in range(1, 6)
                  if ev[f"Session{i}"] in code_of and pd.notna(ev[f"Session{i}DateUtc"])}
        if starts:
            out.append((str(ev["EventName"]), starts))
    return out


# ---------------------------------------------------------------------------
# Qualifying
# ---------------------------------------------------------------------------
SEGMENTS = ("Q1", "Q2", "Q3")


def _quali_segment(session, raw: pd.DataFrame) -> pd.Series:
    """The segment (1, 2, 3) each lap of `raw` (session.laps, float seconds) started in."""
    ends = getattr(session, "segment_ends", None)            # modules/openf1.Session
    if ends is not None:
        if not ends:
            return pd.Series(np.nan, index=raw.index)
        seg = 1 + np.searchsorted(np.asarray(ends[:2], float), raw["LapStartTime"].to_numpy(float), side="right")
        return pd.Series(np.where(raw["LapStartTime"].notna(), seg, np.nan), index=raw.index)
    seg = pd.Series(np.nan, index=raw.index)
    try:                                                     # FastF1: from the session status feed
        for i, part in enumerate(session.laps.split_qualifying_sessions()):
            if part is not None:
                seg.loc[seg.index.intersection(part.index)] = i + 1
    except Exception as exc:  # noqa: BLE001 — no status feed (timing-only tier)
        log.warning("Couldn't split qualifying into segments: %s", exc)
    return seg


@st.cache_data(show_spinner=False, max_entries=12)
def get_quali_laps(year: int, location: str, session_type: str) -> pd.DataFrame:
    """
    Every qualifying lap: Driver, Team, LapNumber, Segment (1-3, NaN if unknown), LapTime,
    Sector1-3Time, SpeedST, Compound, TyreLife, LapStartTime/Time (session s), Deleted,
    Valid (a full timed lap, not deleted, not an in- or out-lap) and Push (valid and within
    QUALI_PUSH_FACTOR of the driver's best: not a cool-down or aborted lap).
    """
    session, _ = load_session(year, location, session_type)
    raw = pd.DataFrame(session.laps)
    seg = _quali_segment(session, timedeltas_to_seconds(raw))
    laps = timedeltas_to_seconds(raw).assign(Segment=seg)
    for c in ("Sector1Time", "Sector2Time", "Sector3Time", "SpeedST", "Deleted"):
        if c not in laps:
            laps[c] = np.nan
    laps["Compound"] = laps["Compound"].fillna("UNKNOWN").replace("", "UNKNOWN")
    laps["Deleted"] = laps["Deleted"].fillna(False).astype(bool)
    laps["Valid"] = (laps["LapTime"].notna() & ~laps["Deleted"]
                     & laps["PitInTime"].isna() & laps["PitOutTime"].isna())
    best = laps[laps["Valid"]].groupby("Driver")["LapTime"].min()
    laps["Push"] = laps["Valid"] & (laps["LapTime"] <= laps["Driver"].map(best) * config.QUALI_PUSH_FACTOR)
    cols = ["Driver", "Team", "LapNumber", "Segment", "LapTime", "Sector1Time", "Sector2Time",
            "Sector3Time", "SpeedST", "Compound", "TyreLife", "LapStartTime", "Time", "Deleted",
            "Valid", "Push"]
    return laps[cols].sort_values(["Driver", "LapNumber"]).reset_index(drop=True)


def quali_cutoffs(n_drivers: int) -> tuple[int, int]:
    """How many go through to Q2 and to Q3: Q1 and Q2 each knock out half the cars beyond
    ten (20 cars: 15 and 10; 22 cars: 16 and 10)."""
    return 10 + -(-(n_drivers - 10) // 2), 10


@st.cache_data(show_spinner=False, max_entries=12)
def get_quali_results(year: int, location: str, session_type: str) -> pd.DataFrame:
    """
    Qualifying classification, one row per driver, in order: Position, Driver, FullName, Team,
    Color, IsSecondDriver, Q1/Q2/Q3 (s), Best, Reached (1-3: the last segment with a time),
    GapToPole (best lap against pole, s), Q1Margin/Q2Margin (time against the cut-off in that
    segment, s: negative = through with that much to spare), TeammateGap (against the teammate
    in the last segment both set a time, s) and Pace (% against the field median, see below).

    Q times are the official ones where the source has them, otherwise each segment's best
    valid lap. Pace puts every driver on one scale although later segments run on a faster
    track: each segment's times are taken against the median time of the Q3 runners in that
    segment, a driver's pace is the best of those ratios, centred on the field median.
    """
    session, _ = load_session(year, location, session_type)
    info = get_session_info(year, location, session_type)
    res = timedeltas_to_seconds(pd.DataFrame(session.results)).rename(
        columns={"Abbreviation": "Driver", "TeamName": "Team"})
    laps = get_quali_laps(year, location, session_type)
    for q in SEGMENTS:
        if q not in res:
            res[q] = np.nan
    if res[list(SEGMENTS)].isna().all().all() and laps["Segment"].notna().any():
        best = laps[laps["Valid"]].groupby(["Driver", "Segment"])["LapTime"].min().unstack()
        for i, q in enumerate(SEGMENTS, start=1):
            res[q] = res["Driver"].map(best[i]) if i in best else np.nan
    res = res[res["Driver"].notna()].copy()
    times = res[list(SEGMENTS)].apply(pd.to_numeric, errors="coerce")
    res[list(SEGMENTS)] = times
    if "Position" not in res or res["Position"].isna().all():
        timed = np.select([times["Q3"].notna(), times["Q2"].notna(), times["Q1"].notna()], [3, 2, 1], 0)
        last = np.select([timed == 3, timed == 2], [times["Q3"], times["Q2"]], times["Q1"])
        order = np.lexsort((np.nan_to_num(last, nan=1e9), -timed))
        res["Position"] = np.empty(len(res))
        res.iloc[order, res.columns.get_loc("Position")] = np.arange(1, len(res) + 1)
    res = res.sort_values("Position", na_position="last").reset_index(drop=True)
    # Who went through goes by the classification (a driver can reach Q2 and set no time there).
    to_q2, to_q3 = quali_cutoffs(len(res))
    res["Reached"] = np.select([res["Position"] <= to_q3, res["Position"] <= to_q2], [3, 2], 1)
    res["Best"] = res[list(SEGMENTS)].min(axis=1)
    pole = res["Best"].iloc[0] if len(res) and pd.notna(res["Best"].iloc[0]) else res["Best"].min()
    res["GapToPole"] = res["Best"] - pole

    # Margin against the cut-off line in Q1 and Q2: for a driver who went through, against the
    # fastest driver knocked out; for one knocked out, against the slowest who went through.
    for q, level in (("Q1", 2), ("Q2", 3)):
        through = res[res["Reached"] >= level]
        out = res[(res["Reached"] == level - 1) & res[q].notna()]
        first_out = out[q].min() if not out.empty else np.nan
        last_in = through[q].max() if not through.empty else np.nan
        res[f"{q}Margin"] = np.where(res["Reached"] >= level, res[q] - first_out,
                                     np.where(res["Reached"] == level - 1, res[q] - last_in, np.nan))

    drv = info["drivers"].set_index("Driver")
    res["Color"] = res["Driver"].map(drv["Color"])
    res["IsSecondDriver"] = res["Driver"].map(drv["IsSecondDriver"]).astype("boolean").fillna(False).astype(bool)
    if "Team" not in res or res["Team"].isna().all():
        res["Team"] = res["Driver"].map(drv["Team"])
    if "FullName" not in res:
        res["FullName"] = res["Driver"]

    def teammate_gap(r):
        mate = res[(res["Team"] == r["Team"]) & (res["Driver"] != r["Driver"])]
        if mate.empty:
            return np.nan
        m = mate.iloc[0]
        for q in reversed(SEGMENTS):
            if pd.notna(r[q]) and pd.notna(m[q]):
                return r[q] - m[q]
        return np.nan
    res["TeammateGap"] = res.apply(teammate_gap, axis=1)
    res["Pace"] = res["Driver"].map(quali_pace(res))
    cols = ["Position", "Driver", "FullName", "Team", "Color", "IsSecondDriver", "Q1", "Q2", "Q3",
            "Best", "Reached", "GapToPole", "Q1Margin", "Q2Margin", "TeammateGap", "Pace"]
    return res[cols]


def quali_pace(res: pd.DataFrame) -> pd.Series:
    """Each driver's qualifying pace, % against the field median (negative = faster): every
    segment's times against the median of that segment's times by the drivers who reached Q3
    (so a Q1 lap isn't compared with a quicker track in Q3), the best of them."""
    top = res[res["Q3"].notna()] if res["Q3"].notna().sum() >= 3 else res[res["Q2"].notna()]
    rel = []
    for q in SEGMENTS:
        ref = top[q].median()
        if pd.notna(ref) and ref > 0:
            rel.append((res[q] / ref - 1.0) * 100.0)
    if not rel:
        return pd.Series(dtype=float)
    pace = pd.concat(rel, axis=1).min(axis=1)
    pace.index = res["Driver"].to_numpy()
    pace = pace.dropna()
    # A lap far off (a crash, a red flag, a mistake on the only run) says nothing about pace.
    pace = pace[pace <= pace.median() + config.QUALI_PACE_OUTLIER]
    return pace - pace.median()


def quali_sectors(laps: pd.DataFrame) -> pd.DataFrame:
    """Per driver: best lap, its segment and compound, best sector times, the ideal lap (the
    sum of the best sectors), time lost against it, and top speed at the speed trap."""
    valid = laps[laps["Valid"]]
    if valid.empty:
        return pd.DataFrame(columns=["Driver", "BestLap", "BestSegment", "BestCompound", "S1", "S2", "S3",
                                     "Ideal", "LostToIdeal", "TopSpeed", "PushLaps"])
    best = valid.loc[valid.groupby("Driver")["LapTime"].idxmin()].set_index("Driver")
    sec = valid.groupby("Driver")[["Sector1Time", "Sector2Time", "Sector3Time"]].min()
    out = pd.DataFrame({
        "BestLap": best["LapTime"], "BestSegment": best["Segment"], "BestCompound": best["Compound"],
        "S1": sec["Sector1Time"], "S2": sec["Sector2Time"], "S3": sec["Sector3Time"],
    })
    out["Ideal"] = out[["S1", "S2", "S3"]].sum(axis=1, min_count=3)
    out["LostToIdeal"] = (out["BestLap"] - out["Ideal"]).clip(lower=0)
    out["TopSpeed"] = laps.groupby("Driver")["SpeedST"].max()
    out["PushLaps"] = laps[laps["Push"]].groupby("Driver").size()
    out["PushLaps"] = out["PushLaps"].fillna(0).astype(int)
    return out.reset_index(names="Driver").sort_values("BestLap").reset_index(drop=True)


def quali_track_gain(laps: pd.DataFrame) -> dict[str, float | None]:
    """
    How fast the track came to the drivers within each segment, s per minute (negative =
    quicker later): the slope of push-lap times against the clock, each lap taken against the
    same driver's mean push lap in that segment (so car pace drops out). Drivers need two push
    laps in the segment; the segment needs QUALI_GAIN_MIN_DRIVERS of them.
    """
    out: dict[str, float | None] = {}
    push = laps[laps["Push"] & laps["Segment"].notna() & laps["Time"].notna()]
    for i, q in enumerate(SEGMENTS, start=1):
        seg = push[push["Segment"] == i]
        n = seg.groupby("Driver")["LapTime"].transform("size")
        seg = seg[n >= 2]
        if seg["Driver"].nunique() < config.QUALI_GAIN_MIN_DRIVERS:
            out[q] = None
            continue
        x = seg["Time"] / 60.0 - seg.groupby("Driver")["Time"].transform("mean") / 60.0
        y = seg["LapTime"] - seg.groupby("Driver")["LapTime"].transform("mean")
        out[q] = float((x * y).sum() / (x * x).sum()) if (x * x).sum() > 0 else None
    return out


def quali_evolution(res: pd.DataFrame) -> dict[str, float | None]:
    """How much quicker the same drivers went from one segment to the next (median, s and %):
    the track rubbering in plus fresher tyres and engine modes. Negative = quicker."""
    out: dict[str, float | None] = {}
    for a, b in (("Q1", "Q2"), ("Q2", "Q3")):
        both = res[res[a].notna() & res[b].notna()]
        d = (both[b] - both[a]).median() if len(both) >= 3 else np.nan
        out[f"{a}_{b}"] = None if pd.isna(d) else float(d)
        out[f"{a}_{b}_pct"] = None if pd.isna(d) else float(d / both[a].median() * 100.0)
    return out


# ---------------------------------------------------------------------------
# Shared Streamlit helpers
# ---------------------------------------------------------------------------
ACTIVE_SESSION_KEY = "active_session"   # survives page switches (not a widget key)


def render_session_selector() -> tuple[int, str, str] | None:
    """
    Sidebar picker shared by every page. The chosen session is stored under a
    non-widget session_state key so it persists when the user switches pages.
    Returns (year, event_name, session_code) or None until the user loads one.
    """
    active = st.session_state.get(ACTIVE_SESSION_KEY)
    this_year = dt.date.today().year
    years = list(range(this_year, 2017, -1))

    with st.sidebar:
        st.markdown("### 📡 Session")
        default_year = active[0] if active else this_year - 1
        year = st.selectbox("Season", years, index=years.index(default_year)
                            if default_year in years else 1)
        sessions = get_event_sessions(year)
        events = list(sessions)
        if not events:
            st.warning("Could not fetch the schedule for this season.")
            return active
        default_event = active[1] if active and active[1] in events else (
            "British Grand Prix" if "British Grand Prix" in events else events[-1])
        event = st.selectbox("Grand Prix", events, index=events.index(default_event))
        # The weekend's finished sessions, in running order: a sprint weekend has four.
        codes = sessions[event]
        default_code = active[2] if active and active[2] in codes else ("R" if "R" in codes else codes[-1])
        code = st.radio("Session", codes, index=codes.index(default_code), horizontal=True,
                        format_func=lambda c: config.SESSION_LABELS[c])
        if st.button("Load session", type="primary", width="stretch"):
            st.session_state[ACTIVE_SESSION_KEY] = (year, event, code)
            active = st.session_state[ACTIVE_SESSION_KEY]
        if active:
            st.caption(f"Loaded: **{active[0]} {active[1]}**, {session_label(active[2])}")
        st.divider()
    return active


def session_label(code: str) -> str:
    """'Grand Prix', 'Sprint', 'Qualifying' or 'Sprint Qualifying'."""
    return config.SESSION_LABELS.get(code, code)


def page_session(active: tuple[int, str, str], kind: str) -> tuple[int, str, str]:
    """
    The session a page shows for the active choice: race pages ("race") show the race a
    qualifying session set the grid for, the Qualifying page ("quali") the qualifying for a
    race, so switching pages never lands on a dead end. Says so when it swaps.
    """
    year, event, code = active
    if kind == "race" and code in config.QUALI_CODES:
        swapped = config.RACE_OF_QUALI[code]
    elif kind == "quali" and code in config.RACE_CODES:
        swapped = config.QUALI_OF_RACE[code]
    else:
        return active
    st.caption(f"You picked {session_label(code)}: this page shows the weekend's {session_label(swapped)}.")
    return year, event, swapped


def session_badge(code: str) -> str:
    """A coloured Markdown badge naming the session type (Sprint and Grand Prix look different)."""
    colour = {"R": "red", "S": "orange", "Q": "violet", "SQ": "blue"}.get(code, "gray")
    return f":{colour}-badge[{session_label(code)}]"


def load_active_session(active: tuple[int, str, str]) -> dict | None:
    """Load info for the active session; show an error instead of crashing."""
    year, event, stype = active
    try:
        with st.spinner(f"Loading {year} {event} — first load downloads from F1 timing, "
                        "later loads come from the local cache…"):
            info = get_session_info(year, event, stype)
    except SessionRunningError:
        st.info("This session hasn't finished yet. Results appear here a few minutes after "
                "the chequered flag.")
        if st.button("Check again"):
            st.rerun()
        return None
    except DataUnavailableError as exc:
        st.error(f"Timing data unavailable for this session.\n\n{exc}")
        return None
    except Exception as exc:  # noqa: BLE001 — keep the UI alive during a debrief
        st.error(f"Unexpected error loading {year} {event}: {exc}")
        return None
    if info.get("provisional"):
        cols = st.columns([5, 1])
        cols[0].warning("Provisional result: the official classification or starting grid isn't "
                        "out yet, so positions and retirements come from timing and may still "
                        "change (penalties).")
        if cols[1].button("Check for updates", width="stretch"):
            st.cache_data.clear()
            load_session.clear()
            st.rerun()
    if info.get("sources"):
        st.caption("Gap-filled: " + ", ".join(f"{k.replace('_', ' ')} from {v}" for k, v in info["sources"].items())
                   + " (the primary source had none).")
    if info["load_tier"] not in ("full", "openf1"):
        st.warning("Full feed unavailable — running on historical lap-timing data only "
                   "(weather and race-control messages missing).")
    return info
