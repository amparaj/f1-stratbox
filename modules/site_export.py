"""
modules/site_export.py — The website's data files (web/public/data/).

    meta.json                 season, calendar, standings, title odds, the next race
    races/r16-R.json          one finished race or sprint: results, laps, stints, deg, insights
    forecasts/r17.json        a race's forecast: win/podium odds (+ best strategies if still to run)

Every race from round 2 gets a forecast made only from the races before it, so finished
races can show the forecast next to what happened. Tables are written column-wise
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

log = logging.getLogger(__name__)

SPRINT_FORMATS = {"sprint", "sprint_shootout", "sprint_qualifying"}
SESSION_DURATION = dt.timedelta(hours=3)        # start time to "should be finished"


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
def calendar(year: int) -> list[dict]:
    """Every event of the season with its race (and sprint) start in UTC."""
    sched = fastf1.get_event_schedule(year, include_testing=False)
    events = []
    for _, ev in sched.iterrows():
        sessions = {ev[f"Session{i}"]: ev[f"Session{i}DateUtc"] for i in range(1, 6)}
        events.append({
            "round": int(ev["RoundNumber"]), "event": str(ev["EventName"]),
            "location": str(ev["Location"]), "country": str(ev["Country"]),
            "format": str(ev["EventFormat"]),
            "race_utc": _utc(sessions.get("Race")),
            "sprint_utc": _utc(sessions.get("Sprint")) if ev["EventFormat"] in SPRINT_FORMATS else None,
        })
    return events


def due_sessions(events: list[dict], now: dt.datetime) -> list[tuple[dict, str]]:
    """(event, 'R'|'S') for every session that should have finished by `now`."""
    out = []
    for ev in events:
        for code, key in (("S", "sprint_utc"), ("R", "race_utc")):
            start = ev.get(key)
            if start and pd.Timestamp(start) + SESSION_DURATION < now:
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
    pace = fc.race_pace(clean) if code == "R" else pd.Series(dtype=float)
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

    weather = {}
    try:
        session, _ = de.load_session(year, event, code)
        w = pd.DataFrame(session.weather_data)
        # The feed has the odd zero reading: ignore anything at or below 0 °C.
        air, track = w["AirTemp"][w["AirTemp"] > 0], w["TrackTemp"][w["TrackTemp"] > 0]
        weather = {"air": [float(air.min()), float(air.max())],
                   "track": [float(track.min()), float(track.max())],
                   "rain": bool(w["Rainfall"].any())}
    except Exception:  # noqa: BLE001 — weather is optional
        pass

    return {
        "id": session_id(ev["round"], code), "round": ev["round"], "code": code,
        "event": event, "location": ev["location"], "country": ev["country"],
        "start_utc": ev["sprint_utc"] if code == "S" else ev["race_utc"],
        "session_name": info["session_name"], "total_laps": info["total_laps"],
        "complete": complete, "neutralised": info["neutralised"], "rain_laps": rain_laps,
        "weather": weather, "fastest": fastest,
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
# Last season, for strategy calibration
# ---------------------------------------------------------------------------
def last_season_models(year: int) -> dict[str, dict]:
    """circuit -> {"event", "total_laps", "field"} for every race of `year`."""
    out = {}
    for ev in calendar(year):
        if not ev["race_utc"] or pd.Timestamp(ev["race_utc"]) + SESSION_DURATION > pd.Timestamp.now(tz="UTC"):
            continue
        try:
            info = de.get_session_info(year, ev["event"], "R")
            field = an.field_compound_model(an.calculate_tyre_degradation(de.get_cleaned_laps(year, ev["event"], "R")))
            out[circuit_key(ev["location"])] = {
                "event": f"{year} {ev['event']}", "total_laps": info["total_laps"],
                "field": {c: m for c, m in field.items() if c in config.DRY_COMPOUNDS},
            }
        except Exception as exc:  # noqa: BLE001 — a missing race just isn't used
            log.warning("Skipping %s %s for calibration: %s", year, ev["event"], exc)
    return out


def strategy_forecast(ev: dict, last: dict[str, dict], factor: float,
                      season_severity: list[float]) -> dict:
    """Best strategies for an upcoming race, calibrated as modules/forecast.py describes."""
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
    # A float base pace and deg rate make the simulator use the presets' compound pace gaps
    # and deg split; the deg is the Medium's, which the others scale from.
    medium_deg = config.COMPOUND_PRESETS["MEDIUM"]["deg_rate"] * severity
    result = fc.best_strategies(90.0, medium_deg, total_laps, pit_loss)
    return {
        "calibrated_on": source, "severity": severity, "season_factor": factor,
        "total_laps": total_laps, "pit_loss": pit_loss, "pit_loss_known": pit_src is not None,
        "deg": {c: config.COMPOUND_PRESETS[c]["deg_rate"] * severity for c in config.DRY_COMPOUNDS},
        "strategies": result["strategies"], "trace": columns(result["trace"]),
    }


# ---------------------------------------------------------------------------
# The whole season
# ---------------------------------------------------------------------------
def export_season(year: int, out_dir: Path, now: dt.datetime | None = None) -> dict:
    """Write every data file for `year` into out_dir and return meta.json's content."""
    now = now or dt.datetime.now(dt.timezone.utc)
    events = calendar(year)
    exported: list[dict] = []
    pending: list[str] = []
    for ev, code in due_sessions(events, now):
        sid = session_id(ev["round"], code)
        try:
            rec = export_session(year, ev, code)
        except Exception as exc:  # noqa: BLE001 — data not out yet, or a broken feed
            print(f"  {sid} {ev['event']}: no data yet ({str(exc)[:200]})", flush=True)
            pending.append(sid)
            continue
        _write(out_dir / "races" / f"{sid}.json", {k: v for k, v in rec.items() if not k.startswith("_")})
        exported.append(rec)
        print(f"  {sid} {ev['event']}: {'complete' if rec['complete'] else 'provisional'}", flush=True)

    if not exported:
        # Nothing loaded (data not out yet, or a source's rate limit): publish the
        # calendar and what's pending, and the next run carries on from the download cache.
        meta = {"season": year, "generated": now.isoformat(),
                "calendar": [{**ev, "done_R": False, "done_S": False, "winner": None, "winner_color": None}
                             for ev in events],
                "sessions": [], "pending": pending, "next_round": None, "forecasts": [],
                "drivers": {}, "teams": {}, "progression": {}, "title_history": {}}
        _write(out_dir / "meta.json", meta)
        return meta

    races = [r for r in exported if r["code"] == "R"]
    by_round = {r["round"]: r for r in races}

    # ---- standings and their progression ----
    pts_rows = []
    for r in exported:
        res = r["_results"]
        pts_rows += [{"round": r["round"], "code": r["code"], "driver": d, "team": t, "points": p}
                     for d, t, p in zip(res["driver"], res["team"], res["points"])]
    pts = pd.DataFrame(pts_rows, columns=["round", "code", "driver", "team", "points"])
    team_of = pts.groupby("driver")["team"].last()
    color_of = pd.concat([r["_results"].set_index("driver")["color"] for r in exported]).groupby(level=0).last() \
        if exported else pd.Series(dtype=str)
    team_color = pd.Series({t: color_of[d] for d, t in team_of.items() if d in color_of})

    def tally(r: dict, col: str) -> pd.DataFrame:
        res = r["_results"]
        return pd.DataFrame({col: res["driver"], "win": res["position"] == 1, "podium": res["position"] <= 3,
                             "dnf": res["dnf"], "start": ~res["dns"]})

    race_tallies = pd.concat([tally(r, "driver") for r in races]) if races else pd.DataFrame(
        columns=["driver", "win", "podium", "dnf", "start"])
    agg = race_tallies.groupby("driver").agg(wins=("win", "sum"), podiums=("podium", "sum"),
                                             dnfs=("dnf", "sum"), starts=("start", "sum"))
    drivers = pts.groupby("driver")["points"].sum().rename("points").to_frame().join(agg).fillna(0)
    drivers["team"] = team_of
    drivers["color"] = color_of
    drivers["name"] = pd.concat([r["_results"].set_index("driver")["name"] for r in exported]).groupby(level=0).last()
    drivers = drivers.sort_values(["points", "wins"], ascending=False).reset_index()
    drivers["position"] = range(1, len(drivers) + 1)

    teams = pts.groupby("team")["points"].sum().sort_values(ascending=False).rename("points").reset_index()
    teams["color"] = teams["team"].map(team_color)
    teams["wins"] = teams["team"].map(race_tallies.assign(team=race_tallies["driver"].map(team_of))
                                      .groupby("team")["win"].sum()).fillna(0)
    teams["position"] = range(1, len(teams) + 1)

    prog = pts.groupby(["round", "driver"])["points"].sum().unstack(fill_value=0).cumsum()
    progression = prog.stack().rename("points").reset_index()

    # ---- forecasts: every race from round 2, each from the races before it ----
    upcoming = [ev for ev in events if ev["race_utc"] and ev["round"] not in by_round]
    next_ev = next((ev for ev in upcoming if pd.Timestamp(ev["race_utc"]) > now), upcoming[0] if upcoming else None)
    forecasts_index, title_hist = [], []
    last = factor = None
    for ev in events:
        before = [r for r in races if r["round"] < ev["round"]]
        if not before:
            continue
        form = fc.driver_form([r["_pace"] for r in before])
        line_up = before[-1]["_results"]["driver"]
        form = form[form.index.isin(line_up)]
        a = pd.concat([tally(r, "driver") for r in before]).groupby("driver").agg(
            starts=("start", "sum"), dnfs=("dnf", "sum"))
        dnf = fc.dnf_rates(a["starts"], a["dnfs"])
        race_fc = fc.forecast_race(form, dnf, seed=ev["round"])
        race_fc["team"] = race_fc["driver"].map(team_of)
        race_fc["color"] = race_fc["driver"].map(color_of)
        data = {"round": ev["round"], "event": ev["event"], "based_on": [r["round"] for r in before],
                "drivers": columns(race_fc)}
        if ev["round"] not in by_round:
            if last is None:
                print("  calibrating strategies on last season…", flush=True)
                last = last_season_models(year - 1)
                this_sev = {circuit_key(r["location"]): fc.tyre_severity(r["_field"]) for r in races}
                factor = fc.season_factor(this_sev, {k: fc.tyre_severity(v["field"]) for k, v in last.items()})
            data["strategy"] = strategy_forecast(
                ev, last, factor, [v for v in (fc.tyre_severity(r["_field"]) for r in races) if v is not None])
        _write(out_dir / "forecasts" / f"r{ev['round']:02d}.json", data)
        forecasts_index.append(ev["round"])

        # Title odds as they stood before this round (rounds already raced only), and now.
        if ev["round"] in by_round or ev is next_ev:
            done_rounds = {r["round"] for r in before}
            standing = pts[pts["round"].isin(done_rounds)].groupby("driver")["points"].sum()
            standing = standing.reindex(standing.index.union(form.index), fill_value=0.0)
            left = [e for e in events if e["round"] not in done_rounds]
            td, tc = fc.title_odds(standing, team_of.reindex(standing.index).fillna("?"), form, dnf,
                                   races_left=len(left), sprints_left=sum(1 for e in left if e["sprint_utc"]),
                                   seed=ev["round"])
            title_hist += [{"after": max(done_rounds), "driver": d, "p_title": p}
                           for d, p in zip(td["driver"], td["p_title"])]
            if ev is next_ev:
                title_now = (td, tc)

    meta = {
        "season": year,
        "generated": now.isoformat(),
        "calendar": [{**ev,
                      "done_R": ev["round"] in by_round,
                      "done_S": any(r["round"] == ev["round"] and r["code"] == "S" for r in exported),
                      "winner": by_round[ev["round"]]["_results"].iloc[0]["driver"] if ev["round"] in by_round else None,
                      "winner_color": by_round[ev["round"]]["_results"].iloc[0]["color"] if ev["round"] in by_round else None}
                     for ev in events],
        "sessions": [{"id": r["id"], "round": r["round"], "code": r["code"], "complete": r["complete"]}
                     for r in exported],
        "pending": pending,
        "next_round": next_ev["round"] if next_ev else None,
        "forecasts": forecasts_index,
        "drivers": columns(drivers[["position", "driver", "name", "team", "color", "points", "wins",
                                    "podiums", "dnfs", "starts"]]),
        "teams": columns(teams[["position", "team", "color", "points", "wins"]]),
        "progression": columns(progression),
        "title_history": columns(pd.DataFrame(title_hist, columns=["after", "driver", "p_title"])),
    }
    if next_ev and races:
        td, tc = title_now
        td["color"] = td["driver"].map(color_of)
        tc["color"] = tc["team"].map(team_color)
        meta["title_odds"] = {"drivers": columns(td), "teams": columns(tc)}
    _write(out_dir / "meta.json", meta)
    return meta
