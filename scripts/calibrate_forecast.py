"""
Fit the session-forecast constants in config.py ("Session forecasts") by replaying past seasons.

    .venv\\Scripts\\python scripts\\calibrate_forecast.py 2025 2026

Every Grand Prix, Sprint, Qualifying and Sprint Qualifying from round 2 on is forecast from the
sessions before it only (as the website does), and scored on what happened: the log of the
chance given to the actual winner (pole sitter), plus the mean log chance of a podium (top
three) given to the actual top three. Each is replayed as the website makes it: before the
weekend from the rounds before (horizon 1), from 2-6 rounds earlier (horizon drift), and,
for a race, after its qualifying ("weekend": that session's pace and the grid). A coordinate search moves one constant at a time over a grid and keeps
the best; the circuit term is fitted only on seasons that have the season before them loaded.

Session data comes through modules/openf1 (archive/openf1/), summarised once into
.calibration/sessions.pkl (delete it to rebuild).
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

import fastf1  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from modules import data_engine as de, forecast as fc, site_export  # noqa: E402

CACHE = ROOT / ".calibration" / "sessions.pkl"
SIMS = 4000
FLOOR = 1.0 / (2 * SIMS)


# ---------------------------------------------------------------------------
# Session summaries
# ---------------------------------------------------------------------------
def summarise(year: int, ev: dict, code: str) -> dict:
    """What a replay needs from one session: pace, result order, grid, teams, retirements."""
    event = ev["event"]
    if code in config.QUALI_CODES:
        res = de.get_quali_results(year, event, code)
        return {"year": year, "round": ev["round"], "code": code, "circuit": site_export.circuit_key(ev["location"]),
                "pace": res.set_index("Driver")["Pace"].dropna(),
                "order": res.sort_values("Position")["Driver"].tolist(),
                "teams": res.set_index("Driver")["Team"], "grid": None, "dnf": None}
    info = de.get_session_info(year, event, code)
    res = pd.DataFrame(de.load_session(year, event, code)[0].results)
    pace = fc.race_pace(de.get_cleaned_laps(year, event, code))
    status = res["Status"].astype(str)
    finished = res["ClassifiedPosition"].astype(str).str.isdigit()
    dns = status.str.contains("Did not start|Withdrew", case=False)
    return {"year": year, "round": ev["round"], "code": code, "circuit": site_export.circuit_key(ev["location"]),
            "pace": pace,
            "order": res[finished].sort_values("Position")["Abbreviation"].tolist(),
            "teams": info["drivers"].set_index("Driver")["Team"],
            "grid": res.set_index("Abbreviation")["GridPosition"].where(lambda g: g > 0),
            "dnf": pd.DataFrame({"start": ~dns, "dnf": ~finished & ~dns}).set_index(res["Abbreviation"])}


def load(years: list[int]) -> list[dict]:
    have = pickle.loads(CACHE.read_bytes()) if CACHE.exists() else []
    done = {(s["year"], s["round"], s["code"]) for s in have}
    now = pd.Timestamp.now(tz="UTC")
    for year in years:
        for ev in site_export.calendar(year):
            for code in config.WEEKEND_ORDER:
                start = ev.get(site_export.UTC_KEY[code])
                if (year, ev["round"], code) in done or not start or pd.Timestamp(start) > now - dt.timedelta(days=1):
                    continue
                try:
                    have.append(summarise(year, ev, code))
                    print(f"  {year} r{ev['round']:02d} {code}", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"  {year} r{ev['round']:02d} {code}: skipped ({str(exc)[:120]})", flush=True)
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_bytes(pickle.dumps(have))
    return have


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def key(s: dict) -> tuple[int, int]:
    return s["round"], config.WEEKEND_ORDER.index(s["code"])


def form_of(sessions: list[dict], codes: tuple[str, ...]) -> pd.Series:
    picked = [s for s in sessions if s["code"] in codes and not s["pace"].empty]
    return fc.driver_form([s["pace"] for s in picked], [s["round"] for s in picked],
                          [config.SPRINT_FORM_WEIGHT if s["code"] == "S" else 1.0 for s in picked])


def circuit_terms(prev: list[dict], circuit: str, code: str) -> pd.Series | None:
    """Last season's team offsets at this circuit: races from Grand Prix pace, qualifying from Q."""
    src = "R" if code in config.RACE_CODES else "Q"
    season = [s for s in prev if s["code"] == src and not s["pace"].empty]
    here = next((s for s in season if s["circuit"] == circuit), None)
    if here is None:
        return None
    teams = pd.concat([s["teams"] for s in season]).groupby(level=0).last()
    return fc.circuit_offset(here["pace"], [s["pace"] for s in season], teams)


def forecast_for(target: dict, season: list[dict], prev: list[dict], horizon: int | str, seed: int) -> pd.DataFrame | None:
    """The forecast for `target` made `horizon` rounds ahead (1: before its weekend, from the
    rounds before), or "weekend": just before it, this weekend's earlier sessions and grid in."""
    if horizon == "weekend":
        before = [s for s in season if key(s) < key(target)]
    else:
        before = [s for s in season if s["round"] <= target["round"] - horizon]
    if not any(s["code"] == "R" for s in before):
        return None
    code = target["code"]
    race_form, quali_form = form_of(before, config.RACE_CODES), form_of(before, config.QUALI_CODES)
    weekend = None
    grid = None
    if horizon == "weekend" and code in config.RACE_CODES:
        q = next((s for s in before if s["round"] == target["round"] and s["code"] == config.QUALI_OF_RACE[code]), None)
        weekend = q["pace"] if q is not None else None
        grid = target["grid"]
    pace = fc.expected_pace(code, race_form, quali_form, weekend)
    entrants = set(target["teams"].index)
    pace = pace[pace.index.isin(entrants)]
    if len(pace) < 10:
        return None
    circuit = circuit_terms(prev, target["circuit"], code) if prev else None
    mean = fc.session_mean(code, pace, circuit, target["teams"], grid)
    races = [s for s in before if s["dnf"] is not None]
    dnf = None
    if races:
        a = pd.concat([s["dnf"] for s in races]).groupby(level=0).sum()
        dnf = fc.dnf_rates(a["start"], a["dnf"])
    return fc.forecast_session(code, mean, dnf, rounds_ahead=1 if horizon == "weekend" else horizon,
                               seed=seed, n_sims=SIMS, grid_known=grid is not None)


def score(target: dict, f: pd.DataFrame) -> float:
    """log P(actual winner) + mean log P(top three) of the actual top three."""
    win_col, top_col = ("p_pole", "p_top3") if target["code"] in config.QUALI_CODES else ("p_win", "p_podium")
    p = f.set_index("driver")
    order = target["order"]
    if not order:
        return np.nan
    win = np.log(max(p[win_col].get(order[0], 0.0), FLOOR))
    top = np.mean([np.log(max(p[top_col].get(d, 0.0), FLOOR)) for d in order[:3]])
    return float(win + top)


def evaluate(data: list[dict], codes: tuple[str, ...], horizons=(1,), circuit_years: set[int] | None = None) -> float:
    """Mean score over every target of `codes` (rounds 2+) and horizon."""
    by_year: dict[int, list[dict]] = {}
    for s in data:
        by_year.setdefault(s["year"], []).append(s)
    total, n = 0.0, 0
    for year, season in by_year.items():
        season = sorted(season, key=key)
        prev = by_year.get(year - 1) if circuit_years is None or year in circuit_years else None
        for t in season:
            if t["code"] not in codes or t["round"] < 2:
                continue
            for i, h in enumerate(horizons):
                f = forecast_for(t, season, prev or [], h, seed=t["round"] * 10 + i)
                if f is None:
                    continue
                v = score(t, f)
                if np.isfinite(v):
                    total, n = total + v, n + 1
    return total / max(n, 1)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def setter(name: str, sub: str | None):
    def set_(v):
        if sub is None:
            setattr(config, name, v)
        else:
            getattr(config, name)[sub] = v
    def get():
        return getattr(config, name) if sub is None else getattr(config, name)[sub]
    return get, set_


def search(data, label, name, sub, grid, codes, **kw) -> None:
    get, set_ = setter(name, sub)
    results = []
    for v in grid:
        set_(v)
        results.append((evaluate(data, codes, **kw), v))
    best = max(results)
    set_(best[1])
    shown = ", ".join(f"{v}: {s:.3f}" for s, v in results)
    print(f"{label:<28} -> {best[1]}   ({shown})", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("years", type=int, nargs="+")
    ap.add_argument("--passes", type=int, default=2)
    args = ap.parse_args()
    config.DATA_SOURCE = "openf1"
    config.FASTF1_CACHE_DIR.mkdir(exist_ok=True)
    fastf1.Cache.enable_cache(str(config.FASTF1_CACHE_DIR))
    data = load(args.years)
    have = {s["year"] for s in data}
    with_prev = {y for y in have if y - 1 in have}
    print(f"{len(data)} sessions; circuit term fitted on {sorted(with_prev) or 'none'}")

    races, quali = ("R",), ("Q", "SQ")
    wk = ("weekend",)
    for p in range(args.passes):
        print(f"--- pass {p + 1}")
        search(data, "SESSION_SD[Q]", "SESSION_SD", "Q", [0.2, 0.25, 0.3, 0.35, 0.45, 0.55], quali)
        config.SESSION_SD["SQ"] = config.SESSION_SD["Q"]
        search(data, "SESSION_SD[R]", "SESSION_SD", "R", [0.2, 0.25, 0.3, 0.35, 0.45, 0.55], races)
        search(data, "SESSION_SD[S]", "SESSION_SD", "S", [0.2, 0.3, 0.45, 0.6, 0.8], ("S",))
        search(data, "SESSION_SD_GRID[R]", "SESSION_SD_GRID", "R", [0.15, 0.2, 0.25, 0.3, 0.4, 0.5], races, horizons=wk)
        search(data, "SESSION_SD_GRID[S]", "SESSION_SD_GRID", "S", [0.15, 0.2, 0.3, 0.45, 0.6], ("S",), horizons=wk)
        search(data, "SPRINT_FORM_WEIGHT", "SPRINT_FORM_WEIGHT", None, [0.0, 0.25, 0.5, 0.75, 1.0], ("R", "S"))
        search(data, "RACE_QUALI_BLEND", "RACE_QUALI_BLEND", None, [0.3, 0.45, 0.6, 0.75, 0.9, 1.0], races, horizons=(1, 2))
        search(data, "RACE_QUALI_BLEND_WEEKEND", "RACE_QUALI_BLEND_WEEKEND", None, [0.4, 0.55, 0.7, 0.85, 1.0], races, horizons=wk)
        search(data, "GRID_WEIGHT[R]", "GRID_WEIGHT", "R", [0.0, 0.02, 0.03, 0.05, 0.07, 0.1], races, horizons=wk)
        search(data, "GRID_WEIGHT[S]", "GRID_WEIGHT", "S", [0.04, 0.06, 0.1, 0.14, 0.2, 0.3], ("S",), horizons=wk)
        if with_prev:
            search(data, "CIRCUIT_WEIGHT[R]", "CIRCUIT_WEIGHT", "R", [0.0, 0.25, 0.5, 0.75, 1.0], races,
                   horizons=(1, 2), circuit_years=with_prev)
            config.CIRCUIT_WEIGHT["S"] = config.CIRCUIT_WEIGHT["R"]
            search(data, "CIRCUIT_WEIGHT[Q]", "CIRCUIT_WEIGHT", "Q", [0.0, 0.25, 0.5, 0.75, 1.0], quali,
                   horizons=(1, 2), circuit_years=with_prev)
            config.CIRCUIT_WEIGHT["SQ"] = config.CIRCUIT_WEIGHT["Q"]
        search(data, "DRIFT_PER_ROUND", "DRIFT_PER_ROUND", None, [0.0, 0.05, 0.1, 0.15, 0.2, 0.3], races + quali,
               horizons=(2, 3, 4, 6))

    print("\nScores (mean log-likelihood, higher is better) with the fitted constants:")
    for codes in (("R",), ("S",), ("Q",), ("SQ",)):
        line = f"  {codes[0]:<3} before the weekend {evaluate(data, codes):.3f}"
        if codes[0] in config.RACE_CODES:
            line += f" · after qualifying {evaluate(data, codes, horizons=wk):.3f}"
        print(line + f" · 3 rounds ahead {evaluate(data, codes, horizons=(3,)):.3f}")
    print("\nFitted:", {k: getattr(config, k) for k in ("SESSION_SD", "SESSION_SD_GRID", "SPRINT_FORM_WEIGHT",
                                                        "RACE_QUALI_BLEND", "RACE_QUALI_BLEND_WEEKEND", "GRID_WEIGHT",
                                                        "CIRCUIT_WEIGHT", "DRIFT_PER_ROUND")})


if __name__ == "__main__":
    main()
