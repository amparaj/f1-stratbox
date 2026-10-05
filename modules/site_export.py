"""
modules/site_export.py — The website's data files (web/public/data/).

    meta.json                 season, calendar, standings, title odds, the next race
    telemetry/r16-Q.json      every driver's fastest lap of a session, on one distance axis
                              (modules/site_telemetry.py)
    races/r16-R.json          one finished session: R Grand Prix, S Sprint (results, laps,
                              stints, deg, insights), Q Qualifying, SQ Sprint Qualifying
                              (Q1/Q2/Q3, sectors, ideal laps, track evolution, cut-offs)
    forecasts/r17.json        a round's forecasts: odds for each session of the weekend, as
                              they stood before it and after its earlier sessions (+ best tyre
                              strategies for the Sprint and Grand Prix and the qualifying
                              picture, if still to run)

Every round from 2 gets forecasts made only from the sessions before them, so finished
sessions can show the forecast next to what happened. Tables are written column-wise
({"driver": [...], "points": [...]}) to keep the files small; the site turns them back
into rows.

The classification (status, points) comes with the session data (OpenF1's session_result,
or FastF1's Jolpica results), the starting grid from Jolpica, which lags by a few hours
to a day. Until both are in, missing points and retirements are worked out from the timing
order and laps completed, and the session is marked "complete": false so the next
scheduled run picks up the official figures.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
from pathlib import Path

import fastf1
import numpy as np
import pandas as pd

import config
from modules import analytics as an
from modules import data_engine as de
from modules import forecast as fc
from modules import simulator as sim
from modules import site_telemetry
from modules import weather as wx

log = logging.getLogger(__name__)

SPRINT_FORMATS = {"sprint", "sprint_shootout", "sprint_qualifying"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _clean(v):
    """JSON-safe scalar: NaN/inf -> None, numpy -> python, floats to 3 dp."""
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if not math.isfinite(v) else round(float(v), 3)
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def columns(df: pd.DataFrame) -> dict[str, list]:
    """A DataFrame as {column: [values]} with JSON-safe values."""
    return {c: [_clean(v) for v in df[c].tolist()] for c in df.columns}


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(data), separators=(",", ":"), allow_nan=False), encoding="utf-8")


def session_id(rnd: int, code: str) -> str:
    return f"r{rnd:02d}-{code}"


def circuit_key(location: str) -> str:
    """One name per circuit across seasons ('Yas Island' and 'Yas Marina' are the same)."""
    matched = config.get_pit_loss(location)[1]
    return matched or config._normalise(location)


def _utc(ts) -> str | None:
    return None if pd.isna(ts) else pd.Timestamp(ts).tz_localize("UTC").isoformat()


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
# The calendar's start-time field for each session code.
UTC_KEY = {"R": "race_utc", "S": "sprint_utc", "Q": "quali_utc", "SQ": "sprint_quali_utc"}


def calendar(year: int) -> list[dict]:
    """Every event of the season with each session's start in UTC (race, sprint, qualifying,
    sprint qualifying; None when the weekend hasn't one) and the circuit's position (the site's
    live rain radar)."""
    sched = fastf1.get_event_schedule(year, include_testing=False)
    events = []
    for _, ev in sched.iterrows():
        sessions = {ev[f"Session{i}"]: ev[f"Session{i}DateUtc"] for i in range(1, 6)}
        sprint = ev["EventFormat"] in SPRINT_FORMATS

        def start(code: str):
            return next((_utc(sessions[n]) for n in config.session_names(code) if n in sessions), None)
        coords = wx.circuit_coords(str(ev["Location"]), str(ev["EventName"]))
        events.append({
            "round": int(ev["RoundNumber"]), "event": str(ev["EventName"]),
            "location": str(ev["Location"]), "country": str(ev["Country"]),
            "format": str(ev["EventFormat"]),
            "race_utc": start("R"),
            "sprint_utc": start("S") if sprint else None,
            "quali_utc": start("Q"),
            "sprint_quali_utc": start("SQ") if sprint else None,
            "lat": coords[0] if coords else None, "lon": coords[1] if coords else None,
        })
    return events


def due_sessions(events: list[dict], now: dt.datetime) -> list[tuple[dict, str]]:
    """(event, code) for every session that could have finished by `now`, in running order
    (loading one that hasn't raises SessionRunningError)."""
    out = []
    for ev in events:
        for code in config.WEEKEND_ORDER:
            start = ev.get(UTC_KEY[code])
            if start and pd.Timestamp(start) + dt.timedelta(minutes=config.EARLIEST_FINISH_MIN[code]) < now:
                out.append((ev, code))
    return out


# ---------------------------------------------------------------------------
# One session
# ---------------------------------------------------------------------------
def _results(year: int, event: str, code: str, info: dict, timeline: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """
    Classification for one session, official where the source has it, otherwise derived.
    Returns (table, complete).
    """
    session, _ = de.load_session(year, event, code)
    res = pd.DataFrame(session.results).rename(columns={"Abbreviation": "driver"})
    laps_done = timeline.groupby("Driver")["LapNumber"].max()
    winner_laps = int(laps_done.max()) if not laps_done.empty else 0
    status = res["Status"].where(res["Status"].astype(str).str.strip() != "") if "Status" in res         else pd.Series(np.nan, index=res.index)
    # Complete = the classification (points, status) and the starting grid are both in.
    complete = bool(res["Points"].notna().any() and status.notna().any() and res["GridPosition"].notna().any())

    out = pd.DataFrame({
        "driver": res["driver"],
        "name": res.get("FullName"),
        "number": res.get("DriverNumber"),
        "grid": res["GridPosition"].where(res["GridPosition"] > 0),
        "pit_lane_start": res["GridPosition"] == 0,
        "position": res["Position"],
        "laps": res["driver"].map(laps_done).fillna(0).astype(int),
    })
    if res["Points"].notna().any() and status.notna().any():
        out["status"] = status.fillna("")
        out["classified"] = res["ClassifiedPosition"].astype(str).str.isdigit()
        out["points"] = res["Points"].fillna(0.0)
    else:
        need = math.ceil(config.CLASSIFIED_FRACTION * winner_laps)
        out["classified"] = out["laps"] >= need
        behind = winner_laps - out["laps"]
        out["status"] = np.where(behind == 0, "Finished",
                                 np.where(out["classified"], "+" + behind.astype(str) + " Lap"
                                          + np.where(behind > 1, "s", ""), "Retired"))
        table = config.SPRINT_POINTS if code == "S" else config.RACE_POINTS
        out["points"] = [float(table[int(p) - 1]) if c and pd.notna(p) and int(p) <= len(table) else 0.0
                         for p, c in zip(out["position"], out["classified"])]
    out["dns"] = out["status"].str.contains("Did not start|Withdrew", case=False) | (out["laps"] == 0)
    out["dnf"] = ~out["classified"] & ~out["dns"]

    drv = info["drivers"].set_index("Driver")
    out["team"] = out["driver"].map(drv["Team"])
    out["color"] = out["driver"].map(drv["Color"])
    out["second_driver"] = out["driver"].map(drv["IsSecondDriver"]).astype("boolean").fillna(False).astype(bool)

    # Gap to the winner at the flag, from lap-end session times on the same lap.
    last = timeline.sort_values("LapNumber").groupby("Driver").tail(1).set_index("Driver")
    win_time = last.loc[last["LapNumber"] == winner_laps, "Time"].min()
    out["gap"] = out["driver"].map(lambda d: last.loc[d, "Time"] - win_time
                                   if d in last.index and last.loc[d, "LapNumber"] == winner_laps else np.nan)
    return out.sort_values("position", na_position="last").reset_index(drop=True), complete


def _insights(res: pd.DataFrame, stints: pd.DataFrame, deg: pd.DataFrame, info: dict,
              fastest: dict | None, rain_laps: list[int]) -> list[str]:
    """Plain-language headlines, every one read straight off the data."""
    lines = []
    win = res.iloc[0]
    plan = stints[stints["driver"] == win["driver"]]
    plan_txt = "→".join(config.COMPOUND_SHORT.get(c, "?") for c in plan["compound"])
    stops = max(len(plan) - 1, 0)
    grid = f" from P{int(win['grid'])}" if pd.notna(win["grid"]) else ""
    lines.append(f"{win['name'] or win['driver']} won{grid} on {plan_txt} "
                 f"({stops} stop{'s' if stops != 1 else ''}).")

    if res["grid"].notna().any():
        fin = res[res["classified"] & res["grid"].notna()].copy()
        fin["gained"] = fin["grid"] - fin["position"]
        if not fin.empty and fin["gained"].max() >= 3:
            g = fin.loc[fin["gained"].idxmax()]
            lines.append(f"Biggest climber: {g['driver']}, P{int(g['grid'])} to P{int(g['position'])}.")

    good = deg[(deg["n_laps"] >= 8) & (deg["deg_rate"] >= 0)]
    if not good.empty:
        comp = good["compound"].value_counts().idxmax()
        best = good[good["compound"] == comp].sort_values("deg_rate").iloc[0]
        lines.append(f"Best tyre management on {comp.title()}s: {best['driver']}, "
                     f"{best['deg_rate']:+.3f} s/lap over {int(best['n_laps'])} laps.")

    cliffs = stints[stints["cliff_lap"].notna() & (stints["cliff_cause"] == "tyre")]
    if not cliffs.empty:
        txt = ", ".join(f"{r.driver} lap {int(r.cliff_lap)} ({r.compound.title()}, {r.cliff_lost:.1f} s)"
                        for r in cliffs.sort_values("cliff_lost", ascending=False).head(3).itertuples())
        lines.append(f"Tyre cliffs: {txt}.")

    def spans(laps: list[int]) -> str:
        if not laps:
            return ""
        groups, start, prev = [], laps[0], laps[0]
        for lap in laps[1:] + [None]:
            if lap != (prev or 0) + 1:
                groups.append(f"{start}" if start == prev else f"{start}–{prev}")
                start = lap
            prev = lap
        return ", ".join(groups)

    neutral = [f"{name} laps {spans(info['neutralised'][key])}"
               for key, name in (("SC", "Safety Car"), ("VSC", "VSC"), ("RED", "Red flag"))
               if info["neutralised"][key]]
    if neutral:
        lines.append("; ".join(neutral) + ".")
    if rain_laps:
        lines.append(f"Rain reported on laps {spans(rain_laps)}.")
    if fastest:
        m, s = divmod(fastest["time"], 60)
        lines.append(f"Fastest lap: {fastest['driver']}, {int(m)}:{s:06.3f} (lap {fastest['lap']}).")
    dnf = res[res["dnf"]]
    if not dnf.empty:
        lines.append("Retired: " + ", ".join(f"{r.driver} (lap {r.laps})" for r in dnf.itertuples()) + ".")
    return lines


def export_session(year: int, ev: dict, code: str) -> dict:
    """Everything the race page needs for one finished session."""
    event = ev["event"]
    info = de.get_session_info(year, event, code)
    timeline = de.get_race_timeline(year, event, code)
    clean = de.get_cleaned_laps(year, event, code)
    all_laps = de.get_all_laps(year, event, code)
    res, complete = _results(year, event, code, info, timeline)

    # Stints with fitted deg and the cliff detector, via the post-mortem.
    pm_drivers = res.rename(columns={"driver": "Driver", "team": "Team", "status": "Status"}).assign(
        ClassifiedPosition=[str(int(p)) if c and pd.notna(p) else "R" for p, c in zip(res["position"], res["classified"])],
        DNF=res["dnf"])
    reports = an.build_postmortem(clean, timeline, pm_drivers)
    stints = pd.DataFrame([{
        "driver": r["driver"], "stint": s["stint"], "compound": s["compound"], "first": s["first"],
        "last": s["last"], "laps": s["laps"], "deg": s["deg_rate"], "clean_laps": s["clean_laps"],
        "cliff_lap": s["cliff"]["lap"] if s["cliff"] else None,
        "cliff_cause": s["cliff"]["cause"] if s["cliff"] else None,
        "cliff_lost": s["cliff"]["time_lost"] if s["cliff"] else None,
    } for r in reports for s in r["stints"]],
        columns=["driver", "stint", "compound", "first", "last", "laps", "deg", "clean_laps",
                 "cliff_lap", "cliff_cause", "cliff_lost"])

    deg_table = an.calculate_tyre_degradation(clean)
    deg = an.degradation_to_frame(deg_table).rename(columns=str.lower)
    field = an.field_compound_model(deg_table)
    pace = fc.race_pace(clean)
    res["pace"] = res["driver"].map(pace)

    valid = all_laps[all_laps["LapTime"].notna() & ~all_laps.get("Deleted", pd.Series(False, index=all_laps.index)).fillna(False).astype(bool)]
    fastest = None
    if not valid.empty:
        f = valid.loc[valid["LapTime"].idxmin()]
        fastest = {"driver": f["Driver"], "time": float(f["LapTime"]), "lap": int(f["LapNumber"])}
    best_lap = valid.groupby("Driver")["LapTime"].min()
    res["best_lap"] = res["driver"].map(best_lap)
    rain_laps = sorted(int(x) for x in timeline.loc[timeline["Rainfall"].astype(bool), "LapNumber"].unique())

    laps = timeline.assign(
        compound=timeline["Compound"].map(lambda c: config.COMPOUND_SHORT.get(c, "?")),
    )[["Driver", "LapNumber", "RunningPosition", "GapToLeader", "LapTime", "compound", "TyreLife", "PitIn"]]
    laps.columns = ["driver", "lap", "pos", "gap", "lap_time", "compound", "tyre_life", "pit"]

    weather, weather_laps = {}, None
    try:
        session, _ = de.load_session(year, event, code)
        w = pd.DataFrame(session.weather_data)
        # The feed has the odd zero reading: ignore anything at or below 0 °C.
        air, track = w["AirTemp"][w["AirTemp"] > 0], w["TrackTemp"][w["TrackTemp"] > 0]
        weather = {"air": [float(air.min()), float(air.max())],
                   "track": [float(track.min()), float(track.max())],
                   "track_mean": float(track.mean()),
                   "rain": bool(w["Rainfall"].any())}
        lw = de.get_lap_weather(year, event, code)
        if not lw.empty:
            weather_laps = columns(lw[["LapNumber", "AirTemp", "TrackTemp", "Humidity", "WindSpeed", "Rainfall"]]
                                   .set_axis(["lap", "air", "track", "humidity", "wind", "rain"], axis=1))
    except Exception:  # noqa: BLE001 — weather is optional
        pass

    return {
        "id": session_id(ev["round"], code), "round": ev["round"], "code": code,
        "label": config.SESSION_LABELS[code], "event": event, "location": ev["location"], "country": ev["country"],
        "start_utc": ev["sprint_utc"] if code == "S" else ev["race_utc"],
        "session_name": info["session_name"], "total_laps": info["total_laps"],
        "complete": complete, "neutralised": info["neutralised"], "rain_laps": rain_laps,
        "sources": info["sources"],
        "weather": weather, "weather_laps": weather_laps, "fastest": fastest,
        "results": columns(res[["driver", "name", "number", "team", "color", "second_driver", "grid",
                                "pit_lane_start", "position", "classified", "status", "points", "laps",
                                "gap", "best_lap", "pace", "dnf", "dns"]]),
        "laps": columns(laps),
        "stints": columns(stints),
        "deg": columns(deg[["driver", "compound", "base_pace", "deg_rate", "r_squared", "n_laps", "sample"]]),
        "compounds": [{"compound": c, **m} for c, m in field.items()],
        "insights": _insights(res, stints, deg, info, fastest, rain_laps),
        # Kept for the season roll-up, not written to the race file.
        "_results": res, "_pace": pace,
        "_field": {c: m for c, m in field.items() if c in config.DRY_COMPOUNDS},
    }


# ---------------------------------------------------------------------------
# One qualifying session
# ---------------------------------------------------------------------------
def _quali_insights(res: pd.DataFrame, sectors: pd.DataFrame, evo: dict, n_q2: int) -> list[str]:
    """Plain-language headlines for a qualifying session, every one read off the data."""
    lines = []
    if res.empty or pd.isna(res["best"].iloc[0]):
        return lines
    pole, second = res.iloc[0], res.iloc[1] if len(res) > 1 else None
    m, s = divmod(pole["best"], 60)
    lines.append(f"{pole['name'] or pole['driver']} took pole with {int(m)}:{s:06.3f}"
                 + (f", {second['best'] - pole['best']:.3f} s clear of {second['driver']}." if second is not None
                    and pd.notna(second["best"]) else "."))
    for q, level, n in (("q1", 2, n_q2), ("q2", 3, 10)):
        through = res[(res["reached"] >= level) & res[f"{q}_margin"].notna()]
        out = res[(res["reached"] == level - 1) & res[f"{q}_margin"].notna()]
        if not through.empty and not out.empty:
            last_in = through.loc[through[f"{q}_margin"].idxmax()]
            first_out = out.loc[out[f"{q}_margin"].idxmin()]
            lines.append(f"{q.upper()} cut-off (top {n}): {last_in['driver']} went through by "
                         f"{-last_in[f'{q}_margin']:.3f} s; {first_out['driver']} missed out by {first_out[f'{q}_margin']:.3f} s.")
    if evo.get("Q1_Q2") is not None:
        txt = f"Track evolution: the same drivers found {-evo['Q1_Q2']:.3f} s from Q1 to Q2"
        if evo.get("Q2_Q3") is not None:
            txt += f" and {-evo['Q2_Q3']:.3f} s from Q2 to Q3"
        lines.append(txt + " (median).")
    if not sectors.empty and sectors["ideal"].notna().any():
        best = {k: sectors.loc[sectors[k].idxmin()] for k in ("s1", "s2", "s3") if sectors[k].notna().any()}
        if len(best) == 3:
            ideal = sum(b[k] for k, b in best.items())
            m, s = divmod(ideal, 60)
            lines.append("Fastest sectors: " + ", ".join(f"{k.upper()} {b['driver']}" for k, b in best.items())
                         + f"; together a {int(m)}:{s:06.3f} lap.")
        lost = sectors[sectors["lost"].notna()].sort_values("lost", ascending=False).head(1)
        if not lost.empty and lost.iloc[0]["lost"] >= 0.1:
            r = lost.iloc[0]
            lines.append(f"Most time left on the table: {r['driver']}, {r['lost']:.3f} s slower than their own best sectors added up.")
        if sectors["top_speed"].notna().any():
            r = sectors.loc[sectors["top_speed"].idxmax()]
            lines.append(f"Top speed: {r['driver']}, {r['top_speed']:.0f} km/h.")
    mates = res[res["teammate_gap"].notna()]
    if not mates.empty:
        r = mates.loc[mates["teammate_gap"].idxmax()]
        lines.append(f"Biggest teammate gap: {r['driver']} {r['teammate_gap']:.3f} s behind their teammate ({r['team']}).")
    return lines


def export_quali(year: int, ev: dict, code: str) -> dict:
    """Everything the qualifying page needs for one finished Qualifying or Sprint Qualifying."""
    event = ev["event"]
    info = de.get_session_info(year, event, code)
    res = de.get_quali_results(year, event, code)
    laps = de.get_quali_laps(year, event, code)
    sec = de.quali_sectors(laps)
    evo = de.quali_evolution(res)
    n_q2, _ = de.quali_cutoffs(len(res))
    complete = bool(res["Q1"].notna().any() and not info["provisional"])

    out = pd.DataFrame({
        "position": res["Position"], "driver": res["Driver"], "name": res["FullName"], "team": res["Team"],
        "color": res["Color"], "second_driver": res["IsSecondDriver"],
        "q1": res["Q1"], "q2": res["Q2"], "q3": res["Q3"], "best": res["Best"], "reached": res["Reached"],
        "gap_to_pole": res["GapToPole"], "q1_margin": res["Q1Margin"], "q2_margin": res["Q2Margin"],
        "teammate_gap": res["TeammateGap"], "pace": res["Pace"],
    })
    sectors = pd.DataFrame({
        "driver": sec["Driver"], "best_lap": sec["BestLap"], "segment": sec["BestSegment"],
        "compound": sec["BestCompound"].map(lambda c: config.COMPOUND_SHORT.get(c, "?")),
        "s1": sec["S1"], "s2": sec["S2"], "s3": sec["S3"], "ideal": sec["Ideal"], "lost": sec["LostToIdeal"],
        "top_speed": sec["TopSpeed"], "push_laps": sec["PushLaps"],
    })
    # Every push lap against the session clock: the track getting faster, and the cut-offs.
    push = laps[laps["Push"] & laps["LapStartTime"].notna()].copy()
    t0 = laps["LapStartTime"].min()
    push_laps = pd.DataFrame({
        "driver": push["Driver"], "segment": push["Segment"],
        "minute": (push["Time"] - t0) / 60.0, "lap_time": push["LapTime"],
        "compound": push["Compound"].map(lambda c: config.COMPOUND_SHORT.get(c, "?")),
    })
    cut = {}
    for q, level in (("Q1", 2), ("Q2", 3)):
        through = res[res["Reached"] >= level]
        cut[q.lower()] = float(through[q].max()) if through[q].notna().any() else None

    weather = {}
    try:
        w = pd.DataFrame(de.load_session(year, event, code)[0].weather_data)
        air, track = w["AirTemp"][w["AirTemp"] > 0], w["TrackTemp"][w["TrackTemp"] > 0]
        weather = {"air": [float(air.min()), float(air.max())], "track": [float(track.min()), float(track.max())],
                   "rain": bool(w["Rainfall"].any())}
    except Exception:  # noqa: BLE001 — weather is optional
        pass

    return {
        "id": session_id(ev["round"], code), "round": ev["round"], "code": code,
        "label": config.SESSION_LABELS[code], "event": event, "location": ev["location"], "country": ev["country"],
        "start_utc": ev[UTC_KEY[code]], "session_name": info["session_name"], "complete": complete,
        "sources": info["sources"],
        "results": columns(out), "sectors": columns(sectors), "laps": columns(push_laps),
        "evolution": evo, "gain": de.quali_track_gain(laps), "cutoff": cut, "to_q2": n_q2, "weather": weather,
        "insights": _quali_insights(out, sectors, evo, n_q2),
        # Kept for the season roll-up, not written to the session file.
        "_results": out, "_pace": res.set_index("Driver")["Pace"].dropna(),
    }


# ---------------------------------------------------------------------------
# Last season: strategy calibration and each team's form circuit by circuit
# ---------------------------------------------------------------------------
def _mean_track_temp(year: int, event: str) -> float | None:
    lw = de.get_lap_weather(year, event, "R")
    return float(lw["TrackTemp"].mean()) if lw["TrackTemp"].notna().any() else None


def last_season(year: int) -> dict:
    """
    What last season (`year`) tells this one, by circuit (circuit_key):
      models   {"event", "total_laps", "start_utc", "field", "track_temp"} per Grand Prix (strategy)
      race     team -> circuit offset in race pace (forecast.circuit_offset)
      quali    team -> circuit offset in qualifying pace
      qref     that qualifying's segment evolution and cut-offs (the qualifying strategy)
    """
    models, race_pace, quali_pace, qref, teams = {}, {}, {}, {}, {}
    now = pd.Timestamp.now(tz="UTC")
    for ev in calendar(year):
        key = circuit_key(ev["location"])
        if ev["race_utc"] and pd.Timestamp(ev["race_utc"]) <= now:
            try:
                info = de.get_session_info(year, ev["event"], "R")
                clean = de.get_cleaned_laps(year, ev["event"], "R")
                field = an.field_compound_model(an.calculate_tyre_degradation(clean))
                models[key] = {
                    "event": f"{year} {ev['event']}", "total_laps": info["total_laps"], "start_utc": ev["race_utc"],
                    "field": {c: m for c, m in field.items() if c in config.DRY_COMPOUNDS},
                    "track_temp": _mean_track_temp(year, ev["event"]),
                }
                race_pace[key] = fc.race_pace(clean)
                teams.update(info["drivers"].set_index("Driver")["Team"].to_dict())
            except Exception as exc:  # noqa: BLE001 — a missing race just isn't used
                log.warning("Skipping %s %s for calibration: %s", year, ev["event"], exc)
        if ev["quali_utc"] and pd.Timestamp(ev["quali_utc"]) <= now:
            try:
                res = de.get_quali_results(year, ev["event"], "Q")
                quali_pace[key] = res.set_index("Driver")["Pace"].dropna()
                pole = res["Best"].iloc[0]
                to_q2, _ = de.quali_cutoffs(len(res))
                cut = {q: res.loc[res["Reached"] >= lvl, q].max() for q, lvl in (("Q1", 2), ("Q2", 3))}
                qref[key] = {"event": f"{year} {ev['event']}", "pole": float(pole), "to_q2": to_q2,
                             **de.quali_evolution(res),
                             "gain": de.quali_track_gain(de.get_quali_laps(year, ev["event"], "Q")),
                             "q1_cut_pct": float((cut["Q1"] / pole - 1) * 100) if pd.notna(cut["Q1"]) else None,
                             "q2_cut_pct": float((cut["Q2"] / pole - 1) * 100) if pd.notna(cut["Q2"]) else None}
                teams.update(res.set_index("Driver")["Team"].to_dict())
            except Exception as exc:  # noqa: BLE001
                log.warning("Skipping %s %s qualifying: %s", year, ev["event"], exc)
    teams = pd.Series(teams, dtype=object)
    return {
        "models": models,
        "race": {k: fc.circuit_offset(p, list(race_pace.values()), teams) for k, p in race_pace.items()},
        "quali": {k: fc.circuit_offset(p, list(quali_pace.values()), teams) for k, p in quali_pace.items()},
        "qref": qref,
    }


def weather_forecast(ev: dict, total_laps: int, reference: dict | None, now: dt.datetime,
                     code: str = "R") -> dict | None:
    """
    A session's weather outlook (modules/weather.py) for the forecast file, or None. The
    temperature change is against `reference`, the race tyre wear was calibrated on (none:
    no change); `reference_track` is what that race's own sensors read.
    """
    try:
        o = wx.outlook((ev["location"], ev["event"]), ev[UTC_KEY[code]], total_laps, code, now,
                       reference_start=reference["start_utc"] if reference else None)
    except Exception as exc:  # noqa: BLE001 — Open-Meteo down: forecast without weather
        print(f"  r{ev['round']:02d} {code} weather: {str(exc)[:150]}", flush=True)
        return None
    out = {k: o.get(k) for k in ("source", "model", "samples", "rain_chance", "heavy_chance",
                                 "rain_lap_median", "air", "track", "track_ref")}
    delta = o.get("temp_delta")
    ref_track = reference.get("track_temp") if reference and delta is not None else None
    out.update(temp_delta=float(np.clip(delta, -15, 15)) if delta is not None else 0.0,
               reference=reference["event"] if ref_track is not None or (reference and delta is not None) else None,
               reference_track=ref_track,
               # Best absolute guess: last season's sensors plus the change; else the estimate.
               track_expected=ref_track + delta if ref_track is not None else o.get("track"),
               scenarios=o["scenarios"])
    if o["hourly"] is not None:
        h = o["hourly"].reset_index(names="time")
        h["time"] = h["time"].map(lambda t: t.isoformat())
        out["hourly"] = columns(h[["time", "air", "track", "rain_mm", "rain_prob"]])
    return out


def strategy_forecast(ev: dict, last: dict[str, dict], factor: float,
                      season_severity: list[float], now: dt.datetime | None = None, code: str = "R") -> dict:
    """Best strategies for an upcoming Grand Prix ("R") or Sprint ("S"), calibrated as
    modules/forecast.py describes. A sprint runs sprint_laps() of the Grand Prix distance."""
    key = circuit_key(ev["location"])
    pit_loss, pit_src = config.get_pit_loss(ev["location"], ev["event"])
    prev = last.get(key)
    last_sev = fc.tyre_severity(prev["field"]) if prev else None
    if last_sev is not None:
        severity, total_laps, source = last_sev * factor, prev["total_laps"], prev["event"]
    else:
        # No usable race here last season: this season's median severity.
        severity = float(np.median(season_severity)) if season_severity else 1.0
        total_laps, source = (prev["total_laps"] if prev else 57), None
    if code == "S":
        total_laps = sim.sprint_laps(total_laps)
    # A float base pace and deg rate make the simulator use the presets' compound pace gaps
    # and deg split; the deg is the Medium's, which the others scale from.
    medium_deg = config.COMPOUND_PRESETS["MEDIUM"]["deg_rate"] * severity
    # Track temperature against the race the wear came from (none: no change).
    weather = weather_forecast(ev, total_laps, prev if last_sev is not None else None,
                               now or dt.datetime.now(dt.timezone.utc), code)
    result = fc.best_strategies(90.0, medium_deg, total_laps, pit_loss,
                                track_temp_delta=weather["temp_delta"] if weather else 0.0,
                                weather_scenarios=weather.pop("scenarios") if weather else None,
                                sprint=code == "S")
    return {
        "code": code, "weather": weather,
        "calibrated_on": source, "severity": severity, "season_factor": factor,
        "total_laps": total_laps, "pit_loss": pit_loss, "pit_loss_known": pit_src is not None,
        "deg": {c: config.COMPOUND_PRESETS[c]["deg_rate"] * severity for c in config.DRY_COMPOUNDS},
        "strategies": result["strategies"], "trace": columns(result["trace"]),
    }


def quali_strategy(ev: dict, code: str, ref: dict | None, odds: pd.DataFrame, now: dt.datetime) -> dict:
    """
    The qualifying picture for an upcoming Qualifying or Sprint Qualifying: how much the track
    came to the drivers here last season (segment to segment), how close the cut-offs were,
    the chance of rain, and who the forecast puts on the edge of each cut-off.
    """
    rain = None
    try:
        o = wx.outlook((ev["location"], ev["event"]), ev[UTC_KEY[code]], 20, code, now)
        rain = {"chance": o["rain_chance"], "source": o["source"], "model": o["model"]}
    except Exception as exc:  # noqa: BLE001 — no weather: the rest stands
        print(f"  r{ev['round']:02d} {code} weather: {str(exc)[:150]}", flush=True)
    edge = lambda col: odds[(odds[col] >= config.QUALI_EDGE[0]) & (odds[col] <= config.QUALI_EDGE[1])]  # noqa: E731
    return {
        "reference": ref,
        "rain": rain,
        "q1_edge": edge("p_q1_out").sort_values("p_q1_out", ascending=False)["driver"].tolist(),
        "q3_edge": edge("p_q3").sort_values("p_q3", ascending=False)["driver"].tolist(),
    }


# ---------------------------------------------------------------------------
# Forecasts for every session of a weekend
# ---------------------------------------------------------------------------
def _order(rec: dict) -> tuple[int, int]:
    return rec["round"], config.WEEKEND_ORDER.index(rec["code"])


def _form(recs: list[dict], codes: tuple[str, ...]) -> pd.Series:
    picked = [r for r in recs if r["code"] in codes and not r["_pace"].empty]
    return fc.driver_form([r["_pace"] for r in picked], [r["round"] for r in picked],
                          [config.SPRINT_FORM_WEIGHT if r["code"] == "S" else 1.0 for r in picked])


def _dnf(recs: list[dict]) -> pd.Series:
    """Retirement rates from the Grands Prix in `recs`."""
    races = [r["_results"] for r in recs if r["code"] == "R"]
    if not races:
        return pd.Series(dtype=float)
    a = pd.concat([pd.DataFrame({"driver": r["driver"], "start": ~r["dns"], "dnf": r["dnf"]}) for r in races]) \
        .groupby("driver").sum()
    return fc.dnf_rates(a["start"], a["dnf"])


def _grid(code: str, rnd: int, recs: list[dict], exported: dict[tuple[int, str], dict]) -> pd.Series | None:
    """The grid for a race: its official grid once the race is out, else the qualifying order."""
    race = exported.get((rnd, code))
    if race is not None and race["_results"]["grid"].notna().any():
        return race["_results"].set_index("driver")["grid"]
    q = next((r for r in recs if r["round"] == rnd and r["code"] == config.QUALI_OF_RACE[code]), None)
    return q["_results"].set_index("driver")["position"] if q is not None else None


def session_forecast(ev: dict, code: str, before: list[dict], last: dict, rounds_ahead: int,
                     exported: dict[tuple[int, str], dict]) -> pd.DataFrame | None:
    """The forecast for one session from the sessions in `before` (oldest first): expected
    pace, last season's circuit term, this weekend's qualifying and grid when they're in."""
    if not any(r["code"] == "R" for r in before):
        return None
    race_form, quali_form = _form(before, config.RACE_CODES), _form(before, config.QUALI_CODES)
    weekend = grid = None
    if code in config.RACE_CODES:
        q = next((r for r in before if r["round"] == ev["round"] and r["code"] == config.QUALI_OF_RACE[code]), None)
        if q is not None:
            weekend, grid = q["_pace"], _grid(code, ev["round"], before, exported)
    latest = before[-1]["_results"]
    line_up = latest["driver"]
    teams = latest.set_index("driver")["team"]
    pace = fc.expected_pace(code, race_form, quali_form, weekend)
    pace = pace[pace.index.isin(line_up)]
    if pace.empty:
        return None
    key = circuit_key(ev["location"])
    circuit = (last["race"] if code in config.RACE_CODES else last["quali"]).get(key)
    if not config.CIRCUIT_WEIGHT[code]:
        circuit = None                    # not used: don't show it either
    mean = fc.session_mean(code, pace, circuit, teams, grid)
    out = fc.forecast_session(code, mean, _dnf(before), rounds_ahead=rounds_ahead,
                              seed=ev["round"] * 10 + config.WEEKEND_ORDER.index(code), grid_known=grid is not None)
    out["team"] = out["driver"].map(teams)
    out["circuit"] = out["team"].map(lambda t: circuit.get(fc.team_now(t)) if circuit is not None and isinstance(t, str) else None)
    out["grid"] = out["driver"].map(grid) if grid is not None else None
    return out


def weekend_codes(ev: dict) -> list[str]:
    """The weekend's sessions in running order."""
    return [c for c in config.WEEKEND_ORDER if ev.get(UTC_KEY[c])]


# ---------------------------------------------------------------------------
# The whole season
# ---------------------------------------------------------------------------
def export_season(year: int, out_dir: Path, now: dt.datetime | None = None, telemetry: bool = True) -> dict:
    """Write every data file for `year` into out_dir and return meta.json's content. `telemetry`
    False: no new lap-telemetry downloads (archived sessions' files are still written)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    events = calendar(year)
    exported: list[dict] = []
    pending: list[str] = []
    tel_budget = [config.SITE_TEL_MAX_FETCH if telemetry else 0]
    for ev, code in due_sessions(events, now):
        sid = session_id(ev["round"], code)
        try:
            rec = (export_quali if code in config.QUALI_CODES else export_session)(year, ev, code)
        except de.SessionRunningError:
            print(f"  {sid} {ev['event']}: still running", flush=True)
            continue
        except Exception as exc:  # noqa: BLE001 — data not out yet, or a broken feed
            print(f"  {sid} {ev['event']}: no data yet ({str(exc)[:200]})", flush=True)
            pending.append(sid)
            continue
        _write(out_dir / "races" / f"{sid}.json", {k: v for k, v in rec.items() if not k.startswith("_")})
        exported.append(rec)
        try:
            tel = site_telemetry.export(year, ev, code, out_dir, tel_budget)
        except Exception as exc:  # noqa: BLE001 — telemetry is extra: never fail the session for it
            tel = f"telemetry failed ({str(exc)[:120]})"
        print(f"  {sid} {ev['event']}: {'complete' if rec['complete'] else 'provisional'}, {tel}", flush=True)

    if not any(r["code"] in config.RACE_CODES for r in exported):
        # Nothing loaded (data not out yet, or a source's rate limit): publish the
        # calendar and what's pending, and the next run carries on from the download cache.
        meta = {"season": year, "generated": now.isoformat(),
                "calendar": [{**ev, **{f"done_{c}": False for c in config.WEEKEND_ORDER},
                              "winner": None, "winner_color": None} for ev in events],
                "sessions": [], "pending": pending, "next_round": None, "forecasts": [],
                "drivers": {}, "teams": {}, "progression": {}, "title_history": {}}
        _write(out_dir / "meta.json", meta)
        return meta

    exported.sort(key=_order)
    by_key = {(r["round"], r["code"]): r for r in exported}
    races = [r for r in exported if r["code"] == "R"]
    by_round = {r["round"]: r for r in races}
    scoring = [r for r in exported if r["code"] in config.RACE_CODES]

    # ---- standings and their progression ----
    pts_rows = []
    for r in scoring:
        res = r["_results"]
        pts_rows += [{"round": r["round"], "code": r["code"], "driver": d, "team": t, "points": p}
                     for d, t, p in zip(res["driver"], res["team"], res["points"])]
    pts = pd.DataFrame(pts_rows, columns=["round", "code", "driver", "team", "points"])
    team_of = pts.groupby("driver")["team"].last()
    color_of = pd.concat([r["_results"].set_index("driver")["color"] for r in exported]).groupby(level=0).last()
    name_of = pd.concat([r["_results"].set_index("driver")["name"] for r in exported]).groupby(level=0).last()
    team_color = pd.Series({t: color_of[d] for d, t in team_of.items() if d in color_of})

    def tally(r: dict) -> pd.DataFrame:
        res = r["_results"]
        return pd.DataFrame({"driver": res["driver"], "win": res["position"] == 1, "podium": res["position"] <= 3,
                             "dnf": res["dnf"], "start": ~res["dns"]})

    empty = pd.DataFrame(columns=["driver", "win", "podium", "dnf", "start"])
    gp = pd.concat([tally(r) for r in races]) if races else empty
    sp = pd.concat([tally(r) for r in scoring if r["code"] == "S"]) if any(r["code"] == "S" for r in scoring) else empty
    agg = gp.groupby("driver").agg(wins=("win", "sum"), podiums=("podium", "sum"),
                                   dnfs=("dnf", "sum"), starts=("start", "sum"))
    drivers = pts.groupby("driver")["points"].sum().rename("points").to_frame().join(agg).fillna(0)
    drivers["gp_points"] = pts[pts["code"] == "R"].groupby("driver")["points"].sum()
    drivers["sprint_points"] = pts[pts["code"] == "S"].groupby("driver")["points"].sum()
    drivers["sprint_wins"] = sp.groupby("driver")["win"].sum()
    poles = pd.Series([r["_results"]["driver"].iloc[0] for r in exported if r["code"] == "Q"], dtype=object)
    drivers["poles"] = poles.value_counts()
    drivers[["gp_points", "sprint_points", "sprint_wins", "poles"]] = \
        drivers[["gp_points", "sprint_points", "sprint_wins", "poles"]].fillna(0)
    drivers["team"] = team_of
    drivers["color"] = color_of
    drivers["name"] = name_of
    drivers = drivers.sort_values(["points", "wins"], ascending=False).reset_index()
    drivers["position"] = range(1, len(drivers) + 1)

    teams = pts.groupby("team")["points"].sum().sort_values(ascending=False).rename("points").reset_index()
    teams["color"] = teams["team"].map(team_color)
    teams["sprint_points"] = teams["team"].map(pts[pts["code"] == "S"].groupby("team")["points"].sum()).fillna(0)
    teams["wins"] = teams["team"].map(gp.assign(team=gp["driver"].map(team_of)).groupby("team")["win"].sum()).fillna(0)
    teams["position"] = range(1, len(teams) + 1)

    prog = pts.groupby(["round", "driver"])["points"].sum().unstack(fill_value=0).cumsum()
    progression = prog.stack().rename("points").reset_index()

    # ---- forecasts: every session from round 2, each from the sessions before it ----
    print("  last season: tyre wear and each team's circuit form…", flush=True)
    last = last_season(year - 1)
    upcoming = [ev for ev in events if ev["race_utc"] and ev["round"] not in by_round]
    next_ev = next((ev for ev in upcoming if pd.Timestamp(ev["race_utc"]) > now), upcoming[0] if upcoming else None)
    latest_round = max(r["round"] for r in exported)
    this_sev = {circuit_key(r["location"]): fc.tyre_severity(r["_field"]) for r in races}
    factor = fc.season_factor(this_sev, {k: fc.tyre_severity(v["field"]) for k, v in last["models"].items()})
    season_sev = [v for v in this_sev.values() if v is not None]
    forecasts_index, title_hist = [], []
    title_now = None

    def decorate(df: pd.DataFrame) -> dict:
        df = df.assign(color=df["driver"].map(color_of))
        return columns(df)

    for ev in events:
        prior = [r for r in exported if r["round"] < ev["round"]]
        if not any(r["code"] == "R" for r in prior):
            continue
        ahead = max(1, ev["round"] - latest_round)
        sessions = {}
        for code in weekend_codes(ev):
            pre = session_forecast(ev, code, prior, last, ahead, by_key)
            if pre is None:
                continue
            entry = {"pre": decorate(pre), "latest": None, "latest_after": []}
            this_weekend = [r for r in exported if r["round"] == ev["round"] and _order(r) < (ev["round"], config.WEEKEND_ORDER.index(code))]
            if this_weekend:
                latest = session_forecast(ev, code, prior + this_weekend, last, 1, by_key)
                if latest is not None:
                    entry["latest"] = decorate(latest)
                    entry["latest_after"] = [r["id"] for r in this_weekend]
            sessions[code] = entry
        if "R" not in sessions:
            continue
        data = {"round": ev["round"], "event": ev["event"], "based_on": sorted({r["round"] for r in prior if r["code"] == "R"}),
                "rounds_ahead": ahead, "sessions": sessions,
                # The Grand Prix before the weekend, as older files had it.
                "drivers": sessions["R"]["pre"]}
        if ev["round"] not in by_round:
            data["strategy"] = strategy_forecast(ev, last["models"], factor, season_sev, now, "R")
            if ev["sprint_utc"] and (ev["round"], "S") not in by_key:
                data["sprint_strategy"] = strategy_forecast(ev, last["models"], factor, season_sev, now, "S")
            for code in ("SQ", "Q"):
                if code in sessions and (ev["round"], code) not in by_key:
                    odds = rows_of(sessions[code]["latest"] or sessions[code]["pre"])
                    ref = last["qref"].get(circuit_key(ev["location"])) if code == "Q" else None
                    data.setdefault("quali_strategy", {})[code] = quali_strategy(ev, code, ref, odds, now)
        _write(out_dir / "forecasts" / f"r{ev['round']:02d}.json", data)
        forecasts_index.append(ev["round"])

        # Title odds as they stood before this round (rounds already raced only), and now.
        if ev["round"] in by_round or ev is next_ev:
            done_keys = {(r["round"], r["code"]) for r in prior}
            standing = pts[[(r, c) in done_keys for r, c in zip(pts["round"], pts["code"])]].groupby("driver")["points"].sum()
            race_form, quali_form = _form(prior, config.RACE_CODES), _form(prior, config.QUALI_CODES)
            line_up = prior[-1]["_results"]
            standing = standing.reindex(standing.index.union(line_up["driver"]), fill_value=0.0)
            cur_teams = line_up.set_index("driver")["team"]
            left = [(e, c) for e in events if e["round"] >= ev["round"] for c in ("S", "R")
                    if e.get(UTC_KEY[c]) and (e["round"], c) not in done_keys]
            plan = []
            for e, c in left:
                pace = fc.expected_pace(c, race_form, quali_form)
                pace = pace[pace.index.isin(line_up["driver"])]
                plan.append((c, fc.session_mean(c, pace, last["race"].get(circuit_key(e["location"])), cur_teams)))
            td, tc = fc.title_odds(standing, team_of.reindex(standing.index).fillna(cur_teams).fillna("?"), plan,
                                   _dnf(prior), seed=ev["round"])
            title_hist += [{"after": max(r["round"] for r in prior), "driver": d, "p_title": p}
                           for d, p in zip(td["driver"], td["p_title"])]
            if ev is next_ev:
                title_now = (td, tc)

    def first(rnd: int, code: str, col: str):
        rec = by_key.get((rnd, code))
        return rec["_results"].iloc[0][col] if rec is not None and not rec["_results"].empty else None

    meta = {
        "season": year,
        "generated": now.isoformat(),
        "calendar": [{**ev,
                      **{f"done_{c}": (ev["round"], c) in by_key for c in config.WEEKEND_ORDER},
                      "winner": first(ev["round"], "R", "driver"), "winner_color": first(ev["round"], "R", "color"),
                      "sprint_winner": first(ev["round"], "S", "driver"), "sprint_winner_color": first(ev["round"], "S", "color"),
                      "pole": first(ev["round"], "Q", "driver"), "pole_color": first(ev["round"], "Q", "color"),
                      "sprint_pole": first(ev["round"], "SQ", "driver"), "sprint_pole_color": first(ev["round"], "SQ", "color")}
                     for ev in events],
        "sessions": [{"id": r["id"], "round": r["round"], "code": r["code"], "complete": r["complete"]}
                     for r in exported],
        "pending": pending,
        "next_round": next_ev["round"] if next_ev else None,
        "forecasts": forecasts_index,
        "drivers": columns(drivers[["position", "driver", "name", "team", "color", "points", "gp_points",
                                    "sprint_points", "wins", "sprint_wins", "podiums", "poles", "dnfs", "starts"]]),
        "teams": columns(teams[["position", "team", "color", "points", "sprint_points", "wins"]]),
        "progression": columns(progression),
        "title_history": columns(pd.DataFrame(title_hist, columns=["after", "driver", "p_title"])),
    }
    if next_ev and title_now:
        td, tc = title_now
        td["color"] = td["driver"].map(color_of)
        tc["color"] = tc["team"].map(team_color)
        meta["title_odds"] = {"drivers": columns(td), "teams": columns(tc)}
    _write(out_dir / "meta.json", meta)
    return meta


def rows_of(cols: dict[str, list]) -> pd.DataFrame:
    """A column-wise table (columns()) back as a DataFrame."""
    return pd.DataFrame(cols)
