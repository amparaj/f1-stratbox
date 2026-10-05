"""
modules/weather.py — Weather at the circuit: forecasts, climate and rain scenarios.

Two free sources:
  * the track's own sensors, in the session data (FastF1 weather_data, or OpenF1's
    /weather): air and track temperature, humidity, wind and a rain flag about once a
    minute. Finished sessions only: OpenF1's live feed during a session is a paid tier.
    data_engine.get_lap_weather lines them up with the laps.
  * Open-Meteo (no key, non-commercial use; credit CREDIT, CC BY 4.0):
      forecast             hourly air temperature, sunshine, rain amount and probability
      ensemble             every member's own hourly rain (ICON 7 days, GFS 16 days ahead)
      historical forecast  what the forecast said for a past time (Live Race Tracker replays)
      archive (ERA5)       the climate: the race window on the same dates in past years
    Requests are cached in config.OPEN_METEO_CACHE_DIR: anything about the past for good,
    forecasts for FORECAST_FRESH_HOURS.

Rain scenarios
--------------
The simulator's Monte Carlo takes a list of weather scenarios, each a weather modifier
({"rain_lap", "dry_lap", "intensity"}) or None (dry), and draws one per simulated race:
one per ensemble member, or one per past day from the climate (CLIMATE_YEARS years x
2 CLIMATE_DAYS + 1 days). Hours become laps assuming the race takes RACE_MINUTES: a lap is
wet when the hour it falls in had RAIN_WET_MM_H or more, heavy from RAIN_HEAVY_MM_H. Only
the first spell of rain counts, and the track is dry again TRACK_DRYING_LAPS after it.

Track temperature = air + TRACK_TEMP_BASE_OFFSET + TRACK_TEMP_SUN_GAIN x sunshine (W/m²),
fitted on past races (config.py). A forecast's temperature change against a reference race
(the one tyre wear was calibrated on) is estimated the same way from the same source for
both, so a source's bias at a site (ERA5 runs cold at Mexico City's altitude) cancels.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time

import numpy as np
import pandas as pd
import requests

import config

FORECAST = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_FORECAST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
CREDIT = "Weather data by Open-Meteo.com"

HOURLY = {"temperature_2m": "air", "shortwave_radiation": "sun", "precipitation": "rain_mm",
          "precipitation_probability": "rain_prob", "cloud_cover": "cloud",
          "relative_humidity_2m": "humidity", "wind_speed_10m": "wind"}
FORECAST_PAST_DAYS = 90          # the forecast API keeps this much of the past; older: historical forecast


class WeatherUnavailable(RuntimeError):
    """Open-Meteo didn't answer, or has nothing for that place and time."""


# ---------------------------------------------------------------------------
# HTTP with a disk cache
# ---------------------------------------------------------------------------
def _get(url: str, params: dict, max_age: dt.timedelta | None) -> dict:
    """GET JSON through the cache. max_age None = cached copies never expire."""
    cache = config.OPEN_METEO_CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:20]
    path = cache / f"{key}.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(saved["fetched"])
        if max_age is None or age < max_age:
            return saved["data"]
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            err = str(exc)
        else:
            if r.status_code == 200:
                data = r.json()
                path.write_text(json.dumps({"fetched": dt.datetime.now(dt.timezone.utc).isoformat(),
                                            "url": url, "params": params, "data": data}), encoding="utf-8")
                return data
            err = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code == 400:              # bad request (e.g. dates out of range): no retry
                break
        time.sleep(3 * (attempt + 1))
    raise WeatherUnavailable(f"Open-Meteo: {err}")


def _utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _now(now=None) -> pd.Timestamp:
    return _utc(now) if now is not None else pd.Timestamp.now(tz="UTC")


def _epoch_s(idx) -> np.ndarray:
    """Seconds since 1970 of tz-aware timestamps, whatever pandas' time resolution."""
    return ((pd.DatetimeIndex(idx) - pd.Timestamp(0, tz="UTC")) / pd.Timedelta(seconds=1)).to_numpy(float)


def _frame(block: dict, rename: dict | None = None) -> pd.DataFrame:
    """Open-Meteo's {"time": [...], var: [...]} as a frame indexed by UTC slot time."""
    df = pd.DataFrame(block)
    df.index = pd.to_datetime(df.pop("time")).dt.tz_localize("UTC")
    return df.rename(columns=rename or {}).astype(float)


# ---------------------------------------------------------------------------
# Where the circuit is
# ---------------------------------------------------------------------------
def circuit_coords(*names: str | None) -> tuple[float, float] | None:
    """(lat, lon) of a circuit from any of its names (FastF1 location, event name, ...)."""
    _, key = config.get_pit_loss(*names)
    if key in config.CIRCUIT_COORDS:
        return config.CIRCUIT_COORDS[key]
    for name in names:
        if not name:
            continue
        try:
            found = _get(GEOCODE, {"name": str(name), "count": 1}, None).get("results") or []
        except WeatherUnavailable:
            continue
        if found:
            return float(found[0]["latitude"]), float(found[0]["longitude"])
    return None


def track_temp(air, sun):
    """Estimated track temperature (°C) from air temperature and sunshine (W/m²)."""
    return air + config.TRACK_TEMP_BASE_OFFSET + config.TRACK_TEMP_SUN_GAIN * sun


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------
def hourly(lat: float, lon: float, start_utc, hours: float = 3, now=None) -> pd.DataFrame | None:
    """
    Hourly forecast from an hour before `start_utc` to `hours` after it: air, sun, rain_mm,
    rain_prob, cloud, humidity, wind and track (estimated). Each row is the hour that ENDS
    at its time (Open-Meteo's convention for rain and sunshine). For the past it's the
    archived forecast. None when the start is beyond the forecast's 16 days.
    """
    start, now = _utc(start_utc), _now(now)
    if start - now > pd.Timedelta(days=15):
        return None
    lo, hi = start - pd.Timedelta(hours=1), start + pd.Timedelta(hours=hours + 2)
    past = hi < now - pd.Timedelta(days=1)
    url = HISTORICAL_FORECAST if lo < now - pd.Timedelta(days=FORECAST_PAST_DAYS) else FORECAST
    data = _get(url, {"latitude": lat, "longitude": lon, "hourly": ",".join(HOURLY), "timezone": "UTC",
                      "start_date": lo.date().isoformat(), "end_date": hi.date().isoformat()},
                None if past else dt.timedelta(hours=config.FORECAST_FRESH_HOURS))
    df = _frame(data["hourly"], HOURLY)
    df = df[(df.index >= lo) & (df.index <= hi)]
    if df.empty:
        return None
    df["track"] = track_temp(df["air"], df["sun"].fillna(0))
    return df


def nowcast(lat: float, lon: float, at_utc, minutes: int = 120, now=None) -> pd.DataFrame | None:
    """
    Rain (mm in each 15 minutes) from `at_utc` for `minutes`, from Open-Meteo's 15-minute
    forecast: what radar-driven short-range models expect next. For a past time it's the
    archived forecast for that time. Rows are the 15 minutes that end at their time.
    """
    at, now = _utc(at_utc), _now(now)
    if at - now > pd.Timedelta(days=15):
        return None
    hi = at + pd.Timedelta(minutes=minutes + 15)
    url = HISTORICAL_FORECAST if at < now - pd.Timedelta(days=FORECAST_PAST_DAYS) else FORECAST
    past = hi < now - pd.Timedelta(days=1)
    data = _get(url, {"latitude": lat, "longitude": lon, "minutely_15": "precipitation", "timezone": "UTC",
                      "start_date": at.date().isoformat(), "end_date": hi.date().isoformat()},
                None if past else dt.timedelta(minutes=15))
    df = _frame(data["minutely_15"], {"precipitation": "rain_mm"})
    df = df[(df.index > at) & (df.index <= hi)]
    return None if df.empty or df["rain_mm"].isna().all() else df


def ensemble(lat: float, lon: float, start_utc, hours: float, now=None) -> tuple[pd.DataFrame, str] | None:
    """
    Every ensemble member's hourly rain (mm) around the race: a frame indexed by slot time,
    one column per member, and the model's name. None when the race is in the past or
    beyond every model's range.
    """
    start, now = _utc(start_utc), _now(now)
    ahead = (start - now) / pd.Timedelta(days=1)
    if start + pd.Timedelta(hours=hours) < now:
        return None
    model = next((m for m, days in config.ENSEMBLE_MODELS if ahead <= days - 1), None)
    if model is None:
        return None
    hi = start + pd.Timedelta(hours=hours + 2)
    data = _get(ENSEMBLE, {"latitude": lat, "longitude": lon, "hourly": "precipitation", "models": model,
                           "timezone": "UTC", "start_date": start.date().isoformat(),
                           "end_date": hi.date().isoformat()},
                dt.timedelta(hours=config.FORECAST_FRESH_HOURS))
    df = _frame(data["hourly"])
    df = df[[c for c in df.columns if c.startswith("precipitation")]].dropna(axis=1, how="all")
    return (df, model) if not df.empty else None


def climate(lat: float, lon: float, start_utc, hours: float) -> tuple[list[tuple[pd.Timestamp, pd.Series]], dict]:
    """
    The race window on every day within CLIMATE_DAYS of the race's date in each of the last
    CLIMATE_YEARS years, from ERA5. Returns ([(that day's start, hourly rain mm)], means of
    air, sun and track over those windows).
    """
    start = _utc(start_utc)
    samples, temps = [], []
    for year in range(start.year - config.CLIMATE_YEARS, start.year):
        try:
            mid = start.replace(year=year)
        except ValueError:                                     # 29 February
            mid = start.replace(year=year, day=28)
        lo, hi = mid - pd.Timedelta(days=config.CLIMATE_DAYS), mid + pd.Timedelta(days=config.CLIMATE_DAYS, hours=hours + 2)
        try:
            data = _get(ARCHIVE, {"latitude": lat, "longitude": lon, "timezone": "UTC",
                                  "hourly": "precipitation,temperature_2m,shortwave_radiation",
                                  "start_date": lo.date().isoformat(), "end_date": hi.date().isoformat()}, None)
        except WeatherUnavailable:
            continue
        df = _frame(data["hourly"])
        for d in range(-config.CLIMATE_DAYS, config.CLIMATE_DAYS + 1):
            s = mid + pd.Timedelta(days=d)
            win = df[(df.index >= s) & (df.index <= s + pd.Timedelta(hours=hours + 2))]
            if win["precipitation"].notna().any():
                samples.append((s, win["precipitation"]))
                temps.append(win[["temperature_2m", "shortwave_radiation"]].mean())
    if not samples:
        raise WeatherUnavailable("no climate data")
    t = pd.DataFrame(temps).mean()
    air, sun = float(t["temperature_2m"]), float(t["shortwave_radiation"])
    return samples, {"air": air, "sun": sun, "track": float(track_temp(air, sun))}


# ---------------------------------------------------------------------------
# Hours -> laps -> scenarios
# ---------------------------------------------------------------------------
def lap_rain(rain: pd.Series, start_utc, total_laps: int, code: str = "R") -> np.ndarray:
    """
    Rain rate (mm/h) during each lap, from an hourly series (rows = the hour ending then).
    The rate is interpolated between hour midpoints, so rain doesn't only start on the hour.
    """
    start = _utc(start_utc)
    lap_min = config.RACE_MINUTES[code] / total_laps
    mids = start + pd.to_timedelta((np.arange(total_laps) + 0.5) * lap_min, unit="min")
    rain = rain.dropna()
    if rain.empty:
        return np.zeros(total_laps)
    t = _epoch_s(rain.index - pd.Timedelta(minutes=30))
    return np.interp(_epoch_s(mids), t, rain.to_numpy())


def scenario(mm_per_lap: np.ndarray) -> dict | None:
    """The first spell of rain as a weather modifier, or None for a dry race."""
    wet = mm_per_lap >= config.RAIN_WET_MM_H
    if not wet.any():
        return None
    first = int(np.argmax(wet))
    after = np.flatnonzero(~wet[first:])
    end = first + int(after[0]) if after.size else len(wet)
    heavy = float(mm_per_lap[first:end].max()) >= config.RAIN_HEAVY_MM_H
    dry = end + 1 + config.TRACK_DRYING_LAPS               # the track takes a few laps to dry
    return {"rain_lap": first + 1, "dry_lap": dry if dry <= len(wet) else None,
            "intensity": "Heavy rain" if heavy else "Light rain"}


def summarise(scenarios: list[dict | None]) -> dict:
    """Rain chance, heavy-rain chance and the typical start lap of a scenario list."""
    wet = [s for s in scenarios if s]
    n = max(len(scenarios), 1)
    return {"samples": len(scenarios), "rain_chance": len(wet) / n,
            "heavy_chance": sum(s["intensity"] == "Heavy rain" for s in wet) / n,
            "rain_lap_median": float(np.median([s["rain_lap"] for s in wet])) if wet else None}


def manual_scenarios(chance: float, window: tuple[int, int], duration: tuple[int, int] | None,
                     heavy_share: float, total_laps: int, n: int = 200, seed: int = 11) -> list[dict | None]:
    """Scenarios for a rain risk set by hand: `chance` of rain starting uniformly within
    `window` (laps), lasting `duration` laps (None = to the flag), heavy with `heavy_share`."""
    rng = np.random.default_rng(seed)
    out: list[dict | None] = []
    for _ in range(n):
        if rng.random() >= chance:
            out.append(None)
            continue
        start = int(rng.integers(window[0], window[1] + 1))
        dry = None if duration is None else start + int(rng.integers(duration[0], duration[1] + 1))
        out.append({"rain_lap": start, "dry_lap": dry if dry and dry <= total_laps else None,
                    "intensity": "Heavy rain" if rng.random() < heavy_share else "Light rain"})
    return out


def outlook(names: tuple[str | None, ...], start_utc, total_laps: int, code: str = "R",
            now=None, reference_start=None) -> dict:
    """
    Everything the simulator needs about a race's weather: rain scenarios (ensemble if the
    race is in range, else climate), their summary, and the expected air, sun and track
    temperature. Keys: source ("ensemble" | "climate"), model, coords, scenarios, samples,
    rain_chance, heavy_chance, rain_lap_median, air, sun, track, hourly (DataFrame or None).
    With `reference_start` (an earlier race there) also track_ref, the same estimate for that
    race from the same source (forecast archive, or climate), and temp_delta = track - track_ref.
    Raises WeatherUnavailable.
    """
    coords = circuit_coords(*names)
    if coords is None:
        raise WeatherUnavailable(f"no coordinates for {names}")
    lat, lon = coords
    start = _utc(start_utc)
    hours = config.RACE_MINUTES[code] / 60
    out: dict = {"coords": coords, "hourly": None}

    table = hourly(lat, lon, start, hours, now)
    if table is not None:
        out["hourly"] = table
        race = table[(table.index > start) & (table.index <= start + pd.Timedelta(hours=hours + 1))]   # hours of the race
        out.update(air=float(race["air"].mean()), sun=float(race["sun"].mean()), track=float(race["track"].mean()))

    ens = ensemble(lat, lon, start, hours, now)
    if ens is not None:
        members, model = ens
        scen = [scenario(lap_rain(members[c], start, total_laps, code)) for c in members.columns]
        out.update(source="ensemble", model=model)
    else:
        samples, temps = climate(lat, lon, start, hours)
        scen = [scenario(lap_rain(rain, s, total_laps, code)) for s, rain in samples]
        out.update(source="climate", model="ERA5")
        if table is None:
            out.update(temps)
    out["scenarios"] = scen
    out.update(summarise(scen))

    if reference_start is not None and out.get("track") is not None:
        ref = _utc(reference_start)
        try:
            if table is not None:
                past = hourly(lat, lon, ref, hours, now)
                race = past[(past.index > ref) & (past.index <= ref + pd.Timedelta(hours=hours + 1))]                     if past is not None else None
                track_ref = float(race["track"].mean()) if race is not None and not race.empty else None
            else:
                track_ref = climate(lat, lon, ref, hours)[1]["track"]
        except WeatherUnavailable:
            track_ref = None
        out["track_ref"] = track_ref
        out["temp_delta"] = None if track_ref is None else out["track"] - track_ref
    return out


def nowcast_rain_laps(rain: pd.DataFrame | None, at_utc, lap_seconds: float, laps_left: int,
                      raining_now: bool = False) -> dict | None:
    """
    When a 15-minute nowcast says the rain starts and stops, in laps from now, and how
    hard: a weather modifier counted from the next lap, or None if it stays dry. If the
    track sensor reports rain now, the track is wet from the next lap whatever the forecast
    says (a model can miss a shower) and dries TRACK_DRYING_LAPS after the forecast rain ends.
    """
    if laps_left < 1:
        return None
    if rain is None:
        rain = pd.DataFrame({"rain_mm": []}, index=pd.DatetimeIndex([], tz="UTC"))
    spell = _nowcast_spell(rain, at_utc, lap_seconds, laps_left)
    if not raining_now:
        return spell
    if spell is None or spell["rain_lap"] > 1 + config.TRACK_DRYING_LAPS:
        dry = 1 + config.TRACK_DRYING_LAPS                 # rain stops now: a few laps to dry
        return {"rain_lap": 1, "dry_lap": dry if dry <= laps_left else None, "intensity": "Light rain"}
    return {**spell, "rain_lap": 1}


def _nowcast_spell(rain: pd.DataFrame, at_utc, lap_seconds: float, laps_left: int) -> dict | None:
    if rain.empty:
        return None
    at = _utc(at_utc)
    per_hour = (rain["rain_mm"] * 4).dropna()                    # mm in 15 min -> mm/h
    mids = at + pd.to_timedelta((np.arange(laps_left) + 0.5) * lap_seconds, unit="s")
    t = _epoch_s(per_hour.index - pd.Timedelta(minutes=7.5))
    return scenario(np.interp(_epoch_s(mids), t, per_hour.to_numpy(), left=0.0, right=0.0))


def forecast_by_lap(table: pd.DataFrame | None, lap_utc: pd.Series) -> pd.DataFrame | None:
    """An hourly forecast (`hourly`) lined up with laps by their UTC time: rain_prob, rain_mm."""
    if table is None or lap_utc.isna().all():
        return None
    slots = pd.DatetimeIndex(lap_utc).ceil("h")
    return table.reindex(slots)[["rain_prob", "rain_mm", "air", "track"]].set_index(lap_utc.index)


# ---------------------------------------------------------------------------
# Dashboard chart
# ---------------------------------------------------------------------------
RAIN_SHADE = "rgba(42,120,214,0.13)"


def build_weather_figure(lap_weather: pd.DataFrame, forecast: pd.DataFrame | None = None,
                         upto_lap: int | None = None, height: int = 340):
    """Track and air temperature by lap, humidity and the forecast's rain chance on a % axis,
    and blue bands where the track sensors reported rain."""
    import plotly.graph_objects as go

    lw = lap_weather if upto_lap is None else lap_weather[lap_weather["LapNumber"] <= upto_lap]
    fig = go.Figure()
    if forecast is not None and forecast["rain_prob"].notna().any():
        fc = forecast.loc[lw.index]
        fig.add_trace(go.Bar(x=lw["LapNumber"], y=fc["rain_prob"], name="Forecast rain chance (%)",
                             yaxis="y2", marker_color="rgba(42,120,214,0.35)",
                             hovertemplate="Lap %{x}: forecast %{y:.0f}% chance of rain<extra></extra>"))
    for col, name, colour, dash in (("TrackTemp", "Track °C", "#eb6834", "solid"),
                                    ("AirTemp", "Air °C", "#2a78d6", "solid"),
                                    ("Humidity", "Humidity %", "#7f7f7f", "dot")):
        if col in lw and lw[col].notna().any():
            fig.add_trace(go.Scatter(x=lw["LapNumber"], y=lw[col], name=name, mode="lines",
                                     line=dict(color=colour, width=2, dash=dash),
                                     yaxis="y2" if col == "Humidity" else "y",
                                     hovertemplate=f"Lap %{{x}}: %{{y:.1f}} {name.split()[-1]}<extra></extra>"))
    wet = lw.loc[lw["Rainfall"], "LapNumber"].tolist()
    runs: list[list[int]] = []
    for lap in wet:
        if runs and lap == runs[-1][1] + 1:
            runs[-1][1] = lap
        else:
            runs.append([lap, lap])
    for a, b in runs:
        fig.add_vrect(x0=a - 0.5, x1=b + 0.5, fillcolor=RAIN_SHADE, line_width=0, layer="below",
                      annotation_text="Rain", annotation_position="top left", annotation_font_size=10)
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified", bargap=0.1,
        xaxis=dict(title="Lap", gridcolor="rgba(128,128,128,0.12)"),
        yaxis=dict(title="Temperature (°C)", gridcolor="rgba(128,128,128,0.12)"),
        yaxis2=dict(title="%", overlaying="y", side="right", range=[0, 100], showgrid=False),
        legend=dict(orientation="h", y=1.12, x=0),
    )
    return fig
