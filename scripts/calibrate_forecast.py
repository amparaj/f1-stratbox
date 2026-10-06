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

The scores are then set against naive baselines on the same targets: equal chances, the last
session's order, the championship standings and (races after qualifying) the grid, each turned
into odds by the same Monte Carlo at its best spread. --report-only skips the search.

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
from modules import data_engine as de, forecast as fc, practice as pr, site_export, upgrades as up  # noqa: E402

CACHE = ROOT / ".calibration" / "sessions.pkl"
SIMS = 4000
FLOOR = 1.0 / (2 * SIMS)


# ---------------------------------------------------------------------------
# Session summaries
# ---------------------------------------------------------------------------
def summarise(year: int, ev: dict, code: str) -> dict:
    """What a replay needs from one session: pace, result order, grid, teams, retirements."""
    event = ev["event"]
    if code in config.PRACTICE_CODES:
        sp = pr.session_pace(de.get_all_laps(year, event, code))
        return {"year": year, "round": ev["round"], "code": code, "circuit": site_export.circuit_key(ev["location"]),
                "pace": pd.Series(dtype=float), "practice": sp, "order": [], "teams": pd.Series(dtype=object),
                "grid": None, "dnf": None}
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
            for code in ORDER:
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
ORDER = config.ALL_SESSIONS        # practice first, then the weekend's competitive sessions


def key(s: dict) -> tuple[int, int]:
    return s["round"], ORDER.index(s["code"])


UPGRADES: dict[int, pd.DataFrame] = {}


def upgrade_items(target: dict) -> pd.Series | None:
    """Performance items each driver's team brought to the target's round (the FIA's lists)."""
    y = target["year"]
    if y not in UPGRADES:
        try:
            UPGRADES[y] = up.performance_counts(up.season_upgrades(y))
        except Exception:  # noqa: BLE001 — no lists for that season
            UPGRADES[y] = pd.DataFrame()
    c = UPGRADES[y]
    if c.empty or target["round"] not in c.index:
        return None
    team = target["teams"].map(lambda t: fc.team_now(t) if isinstance(t, str) else t)
    return team.map(c.loc[target["round"]]).fillna(0.0)


def weekend_practice(sessions: list[dict], rnd: int) -> dict | None:
    fp = {s["code"]: s["practice"] for s in sessions if s["round"] == rnd and s["code"] in config.PRACTICE_CODES}
    return pr.weekend_practice(fp) if fp else None


def form_of(sessions: list[dict], codes: tuple[str, ...], prev: list[dict] | None = None,
            now: pd.Series | None = None, ahead: int = 1) -> pd.Series:
    """Form from this season's `sessions`; with `now` (each driver's team for the forecast), car +
    driver (forecast.split_form; last season's `prev` feeds the teammate ratings)."""
    def picked(ss):
        return [s for s in ss if s["code"] in codes and not s["pace"].empty]
    def weight(s):
        return config.SPRINT_FORM_WEIGHT if s["code"] == "S" else 1.0
    this = picked(sessions)
    if now is None:
        return fc.driver_form([s["pace"] for s in this], [s["round"] for s in this], [weight(s) for s in this])
    last = picked(prev or [])
    end = max((s["round"] for s in last), default=0)
    ss = last + this
    rounds = [s["round"] - end for s in last] + [s["round"] for s in this]
    return fc.split_form([s["pace"] for s in ss], rounds, [s["teams"] for s in ss], now,
                         [weight(s) for s in ss], ahead=ahead)["form"]


def circuit_terms(prev: list[dict], circuit: str, code: str) -> pd.Series | None:
    """Last season's team offsets at this circuit: races from Grand Prix pace, qualifying from Q."""
    src = "R" if code in config.RACE_CODES else "Q"
    season = [s for s in prev if s["code"] == src and not s["pace"].empty]
    here = next((s for s in season if s["circuit"] == circuit), None)
    if here is None:
        return None
    teams = pd.concat([s["teams"] for s in season]).groupby(level=0).last()
    return fc.circuit_offset(here["pace"], [s["pace"] for s in season], teams)


def history(target: dict, season: list[dict], horizon: int | str) -> list[dict]:
    """The sessions a forecast for `target` made at `horizon` may use."""
    if horizon == "practice":
        return [s for s in season if s["round"] < target["round"]]
    if horizon == "weekend":
        return [s for s in season if key(s) < key(target)]
    return [s for s in season if s["round"] <= target["round"] - horizon]


def dnf_of(before: list[dict]) -> pd.Series | None:
    races = [s for s in before if s["dnf"] is not None]
    if not races:
        return None
    a = pd.concat([s["dnf"] for s in races]).groupby(level=0).sum()
    return fc.dnf_rates(a["start"], a["dnf"])


def forecast_for(target: dict, season: list[dict], prev: list[dict], horizon: int | str, seed: int) -> pd.DataFrame | None:
    """The forecast for `target` made `horizon` rounds ahead (1: before its weekend, from the
    rounds before), "practice": after this weekend's practice (nothing competitive yet), or
    "weekend": just before it, this weekend's earlier sessions (practice too) and grid in."""
    practice = None
    before = history(target, season, horizon)
    if horizon in ("practice", "weekend"):
        practice = weekend_practice(season, target["round"])
        if horizon == "practice" and practice is None:
            return None
    if not any(s["code"] == "R" for s in before):
        return None
    code = target["code"]
    now = target["teams"] if code in config.SPLIT_FORM else None
    ahead = horizon if isinstance(horizon, int) else 1
    race_form = form_of(before, config.RACE_CODES, prev, now, ahead)
    quali_form = form_of(before, config.QUALI_CODES, prev, now, ahead)
    weekend = None
    grid = None
    if horizon == "weekend" and code in config.RACE_CODES:
        q = next((s for s in before if s["round"] == target["round"] and s["code"] == config.QUALI_OF_RACE[code]), None)
        weekend = q["pace"] if q is not None else None
        grid = target["grid"]
    pace = fc.expected_pace(code, race_form, quali_form, weekend, practice)
    entrants = set(target["teams"].index)
    pace = pace[pace.index.isin(entrants)]
    if len(pace) < 10:
        return None
    circuit = circuit_terms(prev, target["circuit"], code) if prev else None
    mean = fc.session_terms(code, pace, circuit, target["teams"], grid,
                            upgrade_items=upgrade_items(target) if horizon in (1, "practice", "weekend") else None)["mean"]
    return fc.forecast_session(code, mean, dnf_of(before), rounds_ahead=1 if horizon in ("weekend", "practice") else horizon,
                               seed=seed, n_sims=SIMS, grid_known=grid is not None)


# ---------------------------------------------------------------------------
# Baselines: what the model has to beat
# ---------------------------------------------------------------------------
# Each naive ordering is turned into odds by the same Monte Carlo (retirements included), its
# place k given an expected pace of k x scale % with the session's usual spread. The scale is
# picked per baseline, session kind and horizon from BASELINE_SCALES (its best), so a baseline
# is not beaten merely for being badly spread. "uniform" is everyone's equal chance.
BASELINES = ("uniform", "last session", "standings", "grid")
BASELINE_SCALES = (0.02, 0.04, 0.07, 0.1, 0.15, 0.25, 0.4)


def baseline_order(name: str, target: dict, before: list[dict], horizon: int | str) -> pd.Series | None:
    """Driver -> place (1 = best) under a naive baseline; None where it doesn't apply."""
    entrants = target["teams"].index
    if name == "last session":
        # The latest session of the same kind (races for R/S, qualifying for Q/SQ).
        kind = config.RACE_CODES if target["code"] in config.RACE_CODES else config.QUALI_CODES
        last = next((s for s in reversed(before) if s["code"] in kind and s["order"]), None)
        if last is None:
            return None
        place = pd.Series(range(1, len(last["order"]) + 1), index=last["order"], dtype=float)
    elif name == "standings":
        # Championship points so far this season (Grand Prix and sprint results).
        pts = pd.Series(0.0, index=entrants)
        for s in before:
            if s["code"] in config.RACE_CODES and s["order"]:
                table = config.SPRINT_POINTS if s["code"] == "S" else config.RACE_POINTS
                got = pd.Series(table[:len(s["order"])], index=s["order"][:len(table)], dtype=float)
                pts = pts.add(got, fill_value=0.0)
        place = pts.rank(ascending=False, method="min")
    elif name == "grid":
        if horizon != "weekend" or target["code"] not in config.RACE_CODES or target["grid"] is None:
            return None
        place = target["grid"].dropna()
    else:
        raise ValueError(name)
    place = place.reindex(entrants)
    return place.fillna(place.max() + 1 if place.notna().any() else 1.0)


def baseline_forecasts(name: str, target: dict, season: list[dict], horizon: int | str,
                       seed: int) -> dict[float, pd.DataFrame] | None:
    """Odds for `target` under baseline `name`, one forecast per scale (uniform: one, key 0)."""
    drivers = target["teams"].index
    if name == "uniform":
        n = len(drivers)
        return {0.0: pd.DataFrame({"driver": drivers, "p_win": 1 / n, "p_pole": 1 / n,
                                   "p_podium": 3 / n, "p_top3": 3 / n})}
    before = history(target, season, horizon)
    place = baseline_order(name, target, before, horizon)
    if place is None:
        return None
    dnf = dnf_of(before)
    return {k: fc.forecast_session(target["code"], place * k, dnf, seed=seed, n_sims=SIMS)
            for k in BASELINE_SCALES}


def report(data: list[dict], codes: tuple[str, ...], horizon: int | str) -> tuple[float, dict[str, float], int]:
    """
    The model's mean score and each baseline's (best scale) over the same targets: those the
    model forecasts. Returns (model, {baseline: score}, targets).
    """
    by_year: dict[int, list[dict]] = {}
    for s in data:
        by_year.setdefault(s["year"], []).append(s)
    model, n = 0.0, 0
    base: dict[str, dict[float, float]] = {b: {} for b in BASELINES}
    base_n: dict[str, int] = dict.fromkeys(BASELINES, 0)
    for year, season in by_year.items():
        season = sorted(season, key=key)
        prev = by_year.get(year - 1) or []
        for t in season:
            if t["code"] not in codes or t["round"] < 2 or not t["order"]:
                continue
            seed = t["round"] * 10
            f = forecast_for(t, season, prev, horizon, seed=seed)
            if f is None or not np.isfinite(v := score(t, f)):
                continue
            model, n = model + v, n + 1
            for b in BASELINES:
                fs = baseline_forecasts(b, t, season, horizon, seed)
                if fs is None:
                    continue
                base_n[b] += 1
                for k, bf in fs.items():
                    base[b][k] = base[b].get(k, 0.0) + score(t, bf)
    # A baseline that doesn't apply to every target (grid: races after qualifying) isn't comparable.
    out = {b: max(s.values()) / n for b, s in base.items() if s and base_n[b] == n}
    return model / max(n, 1), out, n


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
            if t["code"] not in codes or t["round"] < 2 or not t["order"]:
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
    ap.add_argument("--report-only", action="store_true",
                    help="skip the search: score config's constants against the baselines")
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
    for p in range(0 if args.report_only else args.passes):
        print(f"--- pass {p + 1}")
        # 9.0 = no limit in practice (no driver's pace is 9% off their median).
        search(data, "FORM_CLIP", "FORM_CLIP", None, [0.15, 0.2, 0.25, 0.35, 0.5, 9.0], races + quali, horizons=(1, 2))
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
        # Recency: how many rounds count, and how fast older ones fade.
        search(data, "FORM_MAX_RACES", "FORM_MAX_RACES", None, [2, 3, 4, 6, 8, 12], races + quali, horizons=(1, 2))
        search(data, "FORM_DECAY", "FORM_DECAY", None, [0.4, 0.55, 0.65, 0.75, 0.85, 1.0], races + quali, horizons=(1, 2))
        # Car + driver form, on the sessions that use it.
        split = tuple(config.SPLIT_FORM)
        if split:
            search(data, "STREAK_PERSIST", "STREAK_PERSIST", None, [0.0, 0.3, 0.5, 0.7, 0.85], split, horizons=(1, 3))
            search(data, "TEAMMATE_DECAY", "TEAMMATE_DECAY", None, [0.8, 0.9, 0.97, 1.0], split, horizons=(1, 3))
            search(data, "TEAMMATE_CARRY", "TEAMMATE_CARRY", None, [0.0, 0.5, 1.0], split, horizons=(1, 3))
            search(data, "TEAMMATE_PRIOR", "TEAMMATE_PRIOR", None, [0.1, 0.25, 0.5, 1.0], split, horizons=(1, 3))
            search(data, "TEAMMATE_CLIP", "TEAMMATE_CLIP", None, [0.1, 0.15, 0.25, 0.5], split, horizons=(1, 3))
        # Practice: how much this weekend's practice counts, scored on forecasts made after it.
        pr_h = ("practice",)
        search(data, "PRACTICE_QUALI_BLEND", "PRACTICE_QUALI_BLEND", None, [0.0, 0.1, 0.2, 0.3, 0.45, 0.6], quali, horizons=pr_h)
        search(data, "PRACTICE_RACE_BLEND", "PRACTICE_RACE_BLEND", None, [0.0, 0.1, 0.2, 0.3, 0.45, 0.6], races, horizons=pr_h)
        search(data, "PRACTICE_SESSION_WEIGHT[FP1]", "PRACTICE_SESSION_WEIGHT", "FP1", [0.0, 0.25, 0.5, 1.0],
               races + quali, horizons=pr_h)
        # Upgrades: does a team bringing performance parts (the FIA's list) go quicker that weekend?
        search(data, "UPGRADE_EFFECT", "UPGRADE_EFFECT", None, [-0.1, -0.05, -0.025, 0.0, 0.025], races + quali,
               horizons=(1,))

    print("\nScores (mean log-likelihood, higher is better) with the fitted constants:")
    for codes in (("R",), ("S",), ("Q",), ("SQ",)):
        line = f"  {codes[0]:<3} before the weekend {evaluate(data, codes):.3f}"
        if codes[0] in config.RACE_CODES:
            line += f" · after qualifying {evaluate(data, codes, horizons=wk):.3f}"
        line += f" · after practice {evaluate(data, codes, horizons=('practice',)):.3f}"
        print(line + f" · 3 rounds ahead {evaluate(data, codes, horizons=(3,)):.3f}")

    print("\nAgainst naive baselines (same targets; each baseline at its best spread):")
    labels = {1: "before the weekend", "weekend": "after qualifying", 3: "3 rounds ahead"}
    for code in ("R", "S", "Q", "SQ"):
        for h, label in labels.items():
            if h == "weekend" and code not in config.RACE_CODES:
                continue
            m, b, n = report(data, (code,), h)
            if not n:
                continue
            best = max(b, key=b.get)
            shown = " · ".join(f"{k} {v:.3f}" for k, v in b.items())
            print(f"  {code:<3} {label:<19} model {m:.3f} (n={n})  vs  {shown}   "
                  f"[model {m - b[best]:+.3f} on {best}]")
    print("\nFitted:", {k: getattr(config, k) for k in ("FORM_CLIP", "SESSION_SD", "SESSION_SD_GRID", "SPRINT_FORM_WEIGHT",
                                                        "RACE_QUALI_BLEND", "RACE_QUALI_BLEND_WEEKEND", "GRID_WEIGHT",
                                                        "CIRCUIT_WEIGHT", "DRIFT_PER_ROUND", "FORM_MAX_RACES",
                                                        "FORM_DECAY", "PRACTICE_QUALI_BLEND", "PRACTICE_RACE_BLEND",
                                                        "PRACTICE_SESSION_WEIGHT", "UPGRADE_EFFECT", "STREAK_PERSIST",
                                                        "TEAMMATE_DECAY", "TEAMMATE_CARRY", "TEAMMATE_PRIOR",
                                                        "TEAMMATE_CLIP")})


if __name__ == "__main__":
    main()
