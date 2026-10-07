"""
modules/profiles.py — The site's driver and circuit pop-ups (web/public/data/profiles.json).

    drivers    everyone who raced or qualified this season: code, Jolpica id (the History files' `ref`),
               name, car number, nationality (ISO 3166 alpha-3, web/src/countries.json), born, Wikipedia,
               latest team and colour
    results    every finished Grand Prix, Sprint, Qualifying and Sprint Qualifying, one row per driver:
               position, grid, the qualifying behind it, points, status
    circuits   one per round: Jolpica's circuit (id, name, place, Wikipedia), MultiViewer's outline
               (rotated as F1 shows it), length and corners, the pit loss the strategy model uses, and
               for a finished round how its Grand Prix played (`race_analysis`: overtaking, stops,
               Safety Cars, tyre wear, track temperature), so the site can rank it against the season

Built from the session files already written to races/ and from Jolpica's season lists (driver and
race tables, cached in .openf1/ for a day), so `scripts/export_site.py --profiles-only` can rewrite it
without loading any session. The circuit outline comes from MultiViewer (FastF1's circuit-info
source): this season's if it has one yet, else last season's at the same circuit key, else none.
"""

from __future__ import annotations

import datetime as dt
import json
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

import config
from modules import openf1

COUNTRIES = config.PROJECT_ROOT / "web" / "src" / "countries.json"
OUTLINE_POINTS = 240         # outline points kept: plenty for a 300 px map, ~3 KB a circuit
DAY = dt.timedelta(days=1)


def _key(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()


def country_codes() -> dict[str, str]:
    """Any name, nationality or alias in countries.json (lower case, no accents) -> alpha-3."""
    data = json.loads(COUNTRIES.read_text(encoding="utf-8"))["countries"]
    out = {}
    for a3, c in data.items():
        for name in [a3, c["name"], c["nationality"], *c.get("aliases", [])]:
            out[_key(name)] = a3
    return out


def _jolpica(year: int, what: str) -> list[dict]:
    data = openf1._get(f"{openf1.JOLPICA}{year}/{what}.json", {"limit": 100}, DAY)
    table = data["MRData"]["DriverTable" if what == "drivers" else "RaceTable"]
    return table["Drivers" if what == "drivers" else "Races"]


def _session_files(out_dir: Path) -> list[dict]:
    files = []
    for path in sorted((out_dir / "races").glob("r??-*.json")):
        code = path.stem.split("-", 1)[1]
        if code in config.WEEKEND_ORDER:
            files.append(json.loads(path.read_text(encoding="utf-8")))
    return files


def _results(files: list[dict]) -> pd.DataFrame:
    rows = []
    for f in files:
        res = pd.DataFrame(f["results"])
        if res.empty:
            continue
        quali = f["code"] in config.QUALI_CODES
        for r in res.to_dict("records"):
            rows.append({
                "round": f["round"], "code": f["code"], "driver": r["driver"], "name": r.get("name"),
                "number": r.get("number"), "team": r["team"], "color": r["color"],
                "position": r.get("position"),
                "grid": None if quali else r.get("grid"),
                "quali": None if quali else r.get("quali"),
                "points": 0 if quali else r.get("points", 0),
                "status": "" if quali else r.get("status", ""),
                "dnf": False if quali else bool(r.get("dnf")),
                "dns": False if quali else bool(r.get("dns")),
                "gap": r.get("gap_to_pole") if quali else r.get("gap"),
                "reached": r.get("reached") if quali else None,
            })
    return pd.DataFrame(rows)


def race_analysis(f: dict) -> dict:
    """How a finished Grand Prix played: overtaking, strategy, neutralisations, conditions (all from its session file).

    Passes on track are an estimate from the lap-end running order: places a car gained over a lap it neither
    started nor ended in the pit lane, outside lap 1 and neutralised laps (cars passing a car that pitted, or one
    that went off, count too, so it reads high on chaotic races; it's for comparing races, not a count)."""
    res = pd.DataFrame(f["results"])
    laps = pd.DataFrame(f["laps"])
    stints = pd.DataFrame(f["stints"])
    started = res[~res["dns"].astype(bool)]
    fin = started[started["classified"].astype(bool)]
    moved = fin.dropna(subset=["grid", "position"])
    moved = moved[moved["grid"] > 0]
    change = (moved["grid"] - moved["position"]).astype(float)
    neutral = {int(x) for k in ("SC", "VSC", "RED") for x in f["neutralised"].get(k, [])}

    passes, lead_changes = 0, 0
    if not laps.empty:
        laps = laps.sort_values(["driver", "lap"])
        laps["prev"] = laps.groupby("driver")["pos"].shift()
        laps["prev_pit"] = laps.groupby("driver")["pit"].shift(fill_value=False).astype(bool)
        ok = (laps["lap"] > 1) & ~laps["pit"].astype(bool) & ~laps["prev_pit"] & ~laps["lap"].isin(neutral) \
            & ~(laps["lap"] - 1).isin(neutral)
        passes = int((laps.loc[ok, "prev"] - laps.loc[ok, "pos"]).clip(lower=0).sum())
        leader = laps[laps["pos"] == 1].sort_values("lap")["driver"]
        lead_changes = int((leader != leader.shift()).sum() - 1) if len(leader) else 0

    stops = (stints.groupby("driver")["stint"].max() - 1) if not stints.empty else pd.Series(dtype=float)
    stops = stops[stops.index.isin(fin["driver"])]
    plans = (stints[stints["driver"].isin(fin["driver"])].sort_values(["driver", "stint"])
             .groupby("driver")["compound"].agg(lambda s: "-".join(c[:1] for c in s)))
    top_plan = plans.value_counts() if len(plans) else pd.Series(dtype=int)
    best = change.idxmax() if len(change) else None
    w = res[res["position"] == 1]
    weather = f.get("weather") or {}
    return {
        "starters": int(len(started)), "finishers": int(len(fin)), "dnfs": int(len(started) - len(fin)),
        "places_moved": round(float(change.abs().mean()), 2) if len(change) else None,
        "passes": passes, "passes_per_lap": round(passes / max(1, f["total_laps"]), 2),
        "lead_changes": lead_changes,
        "winner": w["driver"].iloc[0] if len(w) else None,
        "winner_grid": _clean(w["grid"].iloc[0]) if len(w) else None,
        "climber": moved.loc[best, "driver"] if best is not None and change[best] > 0 else None,
        "climber_from": _clean(moved.loc[best, "grid"]) if best is not None else None,
        "climber_to": _clean(moved.loc[best, "position"]) if best is not None else None,
        "stops": round(float(stops.mean()), 2) if len(stops) else None,
        "one_stop_share": round(float((stops == 1).mean()), 2) if len(stops) else None,
        "top_plan": top_plan.index[0] if len(top_plan) else None,
        "top_plan_n": int(top_plan.iloc[0]) if len(top_plan) else 0,
        "deg": {c["compound"]: c["deg_rate"] for c in f.get("compounds") or []},
        "sc_laps": len(f["neutralised"].get("SC", [])), "vsc_laps": len(f["neutralised"].get("VSC", [])),
        "red_laps": len(f["neutralised"].get("RED", [])),
        "rain_laps": len(f.get("rain_laps") or []), "track_temp": weather.get("track_mean"),
        "fastest": f.get("fastest"), "total_laps": f["total_laps"],
    }


def _outline(year: int, circuit_key: int | None) -> dict | None:
    """MultiViewer's track outline, rotated as F1 draws it: x/y in metres from the centre, plus corners."""
    if circuit_key is None:
        return None
    from fastf1.mvapi.api import get_circuit
    for y in (year, year - 1):
        try:
            d = get_circuit(year=y, circuit_key=int(circuit_key))
        except Exception:  # noqa: BLE001 — the map is a nice-to-have
            d = None
        if d and d.get("x"):
            break
    else:
        return None
    x, yy = np.asarray(d["x"], float) / 10, np.asarray(d["y"], float) / 10      # 1/10 m -> m
    length = float(np.hypot(np.diff(np.r_[x, x[:1]]), np.diff(np.r_[yy, yy[:1]])).sum())
    a = np.deg2rad(float(d.get("rotation") or 0))
    rot = lambda px, py: (px * np.cos(a) - py * np.sin(a), px * np.sin(a) + py * np.cos(a))  # noqa: E731
    rx, ry = rot(x, yy)
    cx, cy = (rx.max() + rx.min()) / 2, (ry.max() + ry.min()) / 2
    keep = np.unique(np.linspace(0, len(rx) - 1, min(OUTLINE_POINTS, len(rx))).round().astype(int))
    corners = []
    for c in d.get("corners") or []:
        px, py = rot(np.float64(c["trackPosition"]["x"]) / 10, np.float64(c["trackPosition"]["y"]) / 10)
        corners.append({"n": f"{c['number']}{c.get('letter') or ''}", "x": round(float(px - cx)), "y": round(float(py - cy))})
    return {"x": [round(float(v - cx)) for v in rx[keep]], "y": [round(float(v - cy)) for v in ry[keep]],
            "length_m": round(length), "corners": corners, "outline_year": y}


def _outline_from_telemetry(out_dir: Path, rnd: int) -> dict | None:
    """A finished round's outline from its lap-telemetry file (already rotated, in metres): for a
    circuit MultiViewer doesn't have yet."""
    for code in ("R", "Q", "S", "SQ"):
        path = out_dir / "telemetry" / f"r{rnd:02d}-{code}.json"
        if not path.exists():
            continue
        t = json.loads(path.read_text(encoding="utf-8"))
        x, y = np.asarray(t["x"], float), np.asarray(t["y"], float)
        cx, cy = (x.max() + x.min()) / 2, (y.max() + y.min()) / 2
        keep = np.unique(np.linspace(0, len(x) - 1, min(OUTLINE_POINTS, len(x))).round().astype(int))
        return {"x": [round(float(v - cx)) for v in x[keep]], "y": [round(float(v - cy)) for v in y[keep]],
                "length_m": round(float(t["length"])),
                "corners": [{"n": c["label"], "x": round(c["x"] - cx), "y": round(c["y"] - cy)} for c in t.get("corners", [])],
                "outline_year": None}
    return None


def _circuit_key(year: int, ev: dict) -> int | None:
    for code, col in (("R", "race_utc"), ("Q", "quali_utc")):
        if not ev.get(col):
            continue
        try:
            return int(openf1.find_session(year, pd.Timestamp(ev[col]), code)["circuit_key"])
        except Exception:  # noqa: BLE001 — not listed yet
            continue
    return None


def _match_race(ev: dict, races: list[dict]) -> dict | None:
    """Jolpica's race for a calendar round: same round within a few days, else the nearest date."""
    if not ev.get("race_utc"):
        return None
    day = pd.Timestamp(ev["race_utc"]).tz_convert(None).normalize()
    near = [(abs((pd.Timestamp(r["date"]) - day).days), r) for r in races]
    near = [(d, r) for d, r in near if d <= 3]
    if not near:
        return None
    same = [r for d, r in near if int(r["round"]) == ev["round"]]
    return same[0] if same else min(near, key=lambda t: t[0])[1]


def export(year: int, out_dir: Path, events: list[dict]) -> dict:
    """Write profiles.json from the session files under out_dir and return it."""
    codes = country_codes()
    files = _session_files(out_dir)
    res = _results(files)
    try:
        jd = {d.get("code"): d for d in _jolpica(year, "drivers") if d.get("code")}
    except Exception as exc:  # noqa: BLE001 — the pop-ups still work on the session files alone
        print(f"  profiles: Jolpica drivers failed ({str(exc)[:120]})", flush=True)
        jd = {}
    try:
        jr = _jolpica(year, "races")
    except Exception as exc:  # noqa: BLE001
        print(f"  profiles: Jolpica races failed ({str(exc)[:120]})", flush=True)
        jr = []

    drivers = []
    if not res.empty:
        res = res.sort_values(["round", "code"])
        latest = res.groupby("driver").last()
        for code, r in latest.iterrows():
            j = jd.get(code, {})
            nums = res.loc[(res["driver"] == code) & res["number"].notna(), "number"]
            name = r["name"] or f"{j.get('givenName', '')} {j.get('familyName', '')}".strip() or code
            drivers.append({
                "driver": code, "ref": j.get("driverId"), "name": name,
                "number": str(nums.iloc[-1]) if len(nums) else j.get("permanentNumber"),
                "country": codes.get(_key(j.get("nationality", ""))),
                "nationality": j.get("nationality"), "born": j.get("dateOfBirth"), "wikipedia": j.get("url"),
                "team": r["team"], "color": r["color"],
            })

    circuits = []
    for ev in events:
        race = _match_race(ev, jr)
        c = (race or {}).get("Circuit", {})
        loc = c.get("Location", {})
        key = _circuit_key(year, ev)
        out = _outline(year, key) or _outline_from_telemetry(out_dir, ev["round"])
        pit, known = config.get_pit_loss(ev.get("location"), ev.get("event"))
        race_file = next((f for f in files if f["round"] == ev["round"] and f["code"] == "R"), None)
        analysis = None
        if race_file is not None:
            try:
                analysis = race_analysis(race_file)
            except Exception as exc:  # noqa: BLE001 — one odd file shouldn't lose every profile
                print(f"  profiles: round {ev['round']} analysis failed ({type(exc).__name__}: {str(exc)[:100]})", flush=True)
        circuits.append({
            "round": ev["round"], "event": ev["event"], "ref": c.get("circuitId"),
            "name": c.get("circuitName") or ev["location"], "locality": loc.get("locality") or ev["location"],
            "country": codes.get(_key(loc.get("country") or ev.get("country") or "")),
            "country_name": loc.get("country") or ev.get("country"),
            "wikipedia": c.get("url"), "circuit_key": key,
            "pit_loss": pit, "pit_loss_known": known is not None,
            "analysis": analysis,
            **(out or {}),
        })

    keep = ["round", "code", "driver", "team", "position", "grid", "quali", "points", "status", "dnf", "dns", "gap", "reached"]
    data = {
        "season": year,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "drivers": drivers,
        "results": {k: [_clean(v) for v in res[k]] for k in keep} if not res.empty else {k: [] for k in keep},
        "circuits": circuits,
    }
    path = out_dir / "profiles.json"
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return data


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and not np.isfinite(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v
