"""
modules/data_engine.py — FastF1 ingestion, cleaning and shaping.

Pipeline
--------
    load_session()        tiered FastF1 load with graceful fallback (cached resource)
    get_all_laps()        every lap, timedeltas -> float seconds, gap-to-car-ahead
    get_cleaned_laps()    model-grade laps: no pit/SC/VSC/lap-1/outliers, CleanAir flag
    get_race_timeline()   per-lap running order + gap to leader (DNFs truncated)
    get_session_info()    event metadata, driver colours, neutralised laps

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


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
def initialize_fastf1() -> None:
    """Enable FastF1's on-disk cache in the project's .fastf1/ directory."""
    config.FASTF1_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(config.FASTF1_CACHE_DIR))
    fastf1.set_log_level("WARNING")


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
    Raises DataUnavailableError if even lap timing cannot be obtained.
    """
    initialize_fastf1()
    if config.DATA_SOURCE == "openf1":
        from modules import openf1
        try:
            return openf1.load_session(year, location, session_type), {"tier": "openf1", "warnings": []}
        except Exception as exc:  # noqa: BLE001 — same contract as the FastF1 tiers
            raise DataUnavailableError(f"No OpenF1 data for {year} {location} {session_type}: {exc}") from exc
    tiers = [
        ("full", dict(laps=True, telemetry=with_telemetry, weather=True, messages=True)),
        ("timing-only", dict(laps=True, telemetry=False, weather=False, messages=False)),
    ]
    warnings: list[str] = []
    for tier, kwargs in tiers:
        try:
            session = fastf1.get_session(int(year), location, session_type)
            session.load(**kwargs)
            laps = session.laps  # raises DataNotLoadedError if timing is absent
            if laps is None or laps.empty:
                raise DataUnavailableError("lap table is empty")
            return session, {"tier": tier, "warnings": warnings}
        except Exception as exc:  # noqa: BLE001 — any feed failure drops a tier
            warnings.append(f"{tier} load failed: {exc}")
            log.warning("FastF1 %s load failed for %s %s %s: %s",
                        tier, year, location, session_type, exc)
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

    event = session.event
    return {
        "year": int(year),
        "event_name": str(event.get("EventName", location)),
        "location": str(event.get("Location", location)),
        "country": str(event.get("Country", "")),
        "session_name": str(getattr(session, "name", session_type)),
        "total_laps": int(total_laps),
        "drivers": drivers.reset_index(drop=True),
        "neutralised": _neutralised_laps(laps),
        "load_tier": load_info["tier"],
        "load_warnings": load_info["warnings"],
    }


@st.cache_data(show_spinner=False, ttl=6 * 3600)
def get_event_names(year: int) -> list[str]:
    """Names of events in a season whose race date has already passed."""
    initialize_fastf1()
    try:
        sched = fastf1.get_event_schedule(int(year), include_testing=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("Schedule fetch failed for %s: %s", year, exc)
        return []
    today = pd.Timestamp(dt.date.today())
    past = sched[pd.to_datetime(sched["EventDate"]) < today]
    return past["EventName"].tolist()


# ---------------------------------------------------------------------------
# Shared Streamlit helpers
# ---------------------------------------------------------------------------
SESSION_TYPES = {"Race": "R", "Sprint": "S"}
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
        events = get_event_names(year)
        if not events:
            st.warning("Could not fetch the schedule for this season.")
            return active
        default_event = active[1] if active and active[1] in events else (
            "British Grand Prix" if "British Grand Prix" in events else events[-1])
        event = st.selectbox("Grand Prix", events, index=events.index(default_event))
        stype_label = st.radio("Session", list(SESSION_TYPES), horizontal=True,
                               index=0 if not active or active[2] == "R" else 1)
        if st.button("Load session", type="primary", width="stretch"):
            st.session_state[ACTIVE_SESSION_KEY] = (year, event, SESSION_TYPES[stype_label])
            active = st.session_state[ACTIVE_SESSION_KEY]
        if active:
            st.caption(f"Loaded: **{active[0]} {active[1]}** ({active[2]})")
        st.divider()
    return active


def load_active_session(active: tuple[int, str, str]) -> dict | None:
    """Load info for the active session; show an error instead of crashing."""
    year, event, stype = active
    try:
        with st.spinner(f"Loading {year} {event} — first load downloads from F1 timing, "
                        "later loads come from the local cache…"):
            info = get_session_info(year, event, stype)
    except DataUnavailableError as exc:
        st.error(f"Timing data unavailable for this session.\n\n{exc}")
        return None
    except Exception as exc:  # noqa: BLE001 — keep the UI alive during a debrief
        st.error(f"Unexpected error loading {year} {event}: {exc}")
        return None
    if info["load_tier"] != "full":
        st.warning("Full feed unavailable — running on historical lap-timing data only "
                   "(weather and race-control messages missing).")
    return info
