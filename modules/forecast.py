"""
modules/forecast.py — Season forecasts for the website: driver form, race and title
odds, and the best tyre strategy for an upcoming race.

Driver pace
-----------
For one race, every clean lap (data_engine.get_cleaned_laps) is fuel corrected and
divided by the field median for its compound, so a lap on Hards isn't compared with
one on Softs. A driver's race pace is the median of those ratios, as a % against the
field median (negative = faster). Drivers need FORM_MIN_CLEAN_LAPS clean laps.

Form = weighted mean of the last FORM_MAX_RACES race paces, each earlier race weighted
FORM_DECAY times the next.

Race forecast (Monte Carlo)
---------------------------
Each simulated race draws every driver's pace as form + Normal(0, FORECAST_RACE_SD)
and a retirement with the driver's DNF rate (shrunk towards the field's), then orders
the finishers. 10 000 races give win / podium / points chances and expected points.
Title odds play every remaining race (and sprint) the same way on top of the current
points, after shifting each driver's form once per simulated season by Normal(0,
FORM_DRIFT_SD): form isn't fixed for the rest of the year. It knows nothing about grid
position, upgrades or the circuit: it's pace form.

Strategy forecast
-----------------
Per-compound fits from one race are too noisy to plan on (a compound only a few drivers
ran in short late stints can fit with no wear at all), and their intercepts are skewed by
track evolution. So the circuit gets one number, its tyre severity: last season's
field-median deg at that circuit against the preset deg, averaged over compounds
(tyre_severity), times how this season's severity compares with last season's at the
circuits both have raced (season_factor). Every compound's deg is its preset times that,
so the presets set the split between compounds and their pace gaps and cliffs. Every 1-stop and 2-stop plan over the dry compounds is run
through the simulator's lap loop; the best few go into its Monte Carlo (Safety Cars,
lap noise).
"""

from __future__ import annotations

from itertools import product

import numpy as np
import pandas as pd

import config
from modules import analytics as an
from modules import simulator as sim


# ---------------------------------------------------------------------------
# Driver pace & form
# ---------------------------------------------------------------------------
def race_pace(clean_laps: pd.DataFrame) -> pd.Series:
    """Each driver's race pace in one race: % against the field median (negative = faster)."""
    if clean_laps.empty:
        return pd.Series(dtype=float)
    laps = an.fuel_correct(clean_laps)
    med = laps.groupby("Compound")["FuelCorrectedLapTime"].transform("median")
    laps["rel"] = (laps["FuelCorrectedLapTime"] / med - 1.0) * 100.0
    grp = laps.groupby("Driver")["rel"]
    pace = grp.median()[grp.count() >= config.FORM_MIN_CLEAN_LAPS]
    return pace - pace.median()


def driver_form(paces: list[pd.Series]) -> pd.Series:
    """Weighted mean of the most recent race paces (oldest first in `paces`)."""
    recent = paces[-config.FORM_MAX_RACES:]
    if not recent:
        return pd.Series(dtype=float)
    weights = [config.FORM_DECAY ** (len(recent) - 1 - i) for i in range(len(recent))]
    num = pd.concat([p * w for p, w in zip(recent, weights)], axis=1).sum(axis=1)
    den = pd.concat([p.notna() * w for p, w in zip(recent, weights)], axis=1).sum(axis=1)
    return (num / den.where(den > 0)).dropna()


def dnf_rates(starts: pd.Series, dnfs: pd.Series) -> pd.Series:
    """Per-driver retirement chance, shrunk towards the field rate."""
    field = dnfs.sum() / max(starts.sum(), 1)
    k = config.DNF_PRIOR_STARTS
    return (dnfs + k * field) / (starts + k)


# ---------------------------------------------------------------------------
# Monte Carlo races
# ---------------------------------------------------------------------------
def _finishing_positions(form: np.ndarray, dnf: np.ndarray, n_sims: int,
                         rng: np.random.Generator) -> np.ndarray:
    """
    (n_sims, n_drivers) finishing positions, 1-based, 0 for a retirement. `form` is one
    value per driver, or one row per simulation (title odds' drifted form).
    """
    n = dnf.shape[-1]
    perf = np.broadcast_to(form, (n_sims, n)) + rng.normal(0.0, config.FORECAST_RACE_SD, (n_sims, n))
    perf += 1e3 * (rng.random((n_sims, n)) < dnf[None, :])
    order = np.argsort(perf, axis=1)
    pos = np.empty_like(order)
    np.put_along_axis(pos, order, np.arange(1, n + 1)[None, :], axis=1)
    finished = perf < 1e2
    return np.where(finished, pos, 0)          # 0 = did not finish


def _points(pos: np.ndarray, table: tuple[int, ...]) -> np.ndarray:
    lookup = np.zeros(len(pos[0]) + 2)
    lookup[1:len(table) + 1] = table
    return lookup[pos]


def forecast_race(form: pd.Series, dnf: pd.Series, seed: int = 0) -> pd.DataFrame:
    """Win / podium / points chances and expected finish for each driver in `form`."""
    drivers = form.sort_values().index.tolist()
    rng = np.random.default_rng(seed)
    pos = _finishing_positions(form[drivers].to_numpy(), dnf.reindex(drivers).fillna(dnf.mean()).to_numpy(),
                               config.FORECAST_SIMS, rng)
    finished = pos > 0
    # Expected position counts a retirement as last place.
    pos_or_last = np.where(finished, pos, len(drivers))
    return pd.DataFrame({
        "driver": drivers,
        "form": form[drivers].to_numpy(),
        "p_win": (pos == 1).mean(axis=0),
        "p_podium": (finished & (pos <= 3)).mean(axis=0),
        "p_points": (finished & (pos <= 10)).mean(axis=0),
        "p_dnf": (~finished).mean(axis=0),
        "exp_pos": pos_or_last.mean(axis=0),
        "exp_points": _points(pos, config.RACE_POINTS).mean(axis=0),
    })


def title_odds(points: pd.Series, teams: pd.Series, form: pd.Series, dnf: pd.Series,
               races_left: int, sprints_left: int, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Simulate the rest of the season. `points` is every driver's current total,
    `teams` maps driver -> team. Drivers without a form figure score nothing more.
    Returns (drivers, constructors) with the title chance and expected final points.
    """
    drivers = points.index.tolist()
    rng = np.random.default_rng(seed)
    n = config.FORECAST_SIMS
    total = np.tile(points.to_numpy(float), (n, 1))
    racing = [d for d in drivers if d in form.index]
    idx = [drivers.index(d) for d in racing]
    p_dnf = dnf.reindex(racing).fillna(dnf.mean()).to_numpy()
    f = form[racing].to_numpy()[None, :] + rng.normal(0.0, config.FORM_DRIFT_SD, (n, len(racing)))
    for table, count in ((config.RACE_POINTS, races_left), (config.SPRINT_POINTS, sprints_left)):
        for _ in range(count):
            total[:, idx] += _points(_finishing_positions(f, p_dnf, n, rng), table)

    # Ties go to whoever is first in the list (more points now): good enough for odds.
    champ = np.bincount(total.argmax(axis=1), minlength=len(drivers)) / n
    drv = pd.DataFrame({"driver": drivers, "team": teams[drivers].to_numpy(),
                        "points": points.to_numpy(float), "exp_points": total.mean(axis=0),
                        "p_title": champ}).sort_values(["exp_points"], ascending=False)

    team_names = sorted(set(teams[drivers]))
    t_total = np.stack([total[:, [i for i, d in enumerate(drivers) if teams[d] == t]].sum(axis=1)
                        for t in team_names], axis=1)
    t_now = np.array([points[[d for d in drivers if teams[d] == t]].sum() for t in team_names])
    cons = pd.DataFrame({"team": team_names, "points": t_now, "exp_points": t_total.mean(axis=0),
                         "p_title": np.bincount(t_total.argmax(axis=1), minlength=len(team_names)) / n}
                        ).sort_values("exp_points", ascending=False)
    return drv.reset_index(drop=True), cons.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Strategy forecast
# ---------------------------------------------------------------------------
def tyre_severity(field: dict[str, dict]) -> float | None:
    """
    How hard one race was on tyres: the field's deg on each dry compound divided by the
    preset deg for it, averaged over compounds weighted by drivers fitted (compounds with
    fewer than STRATEGY_MIN_DRIVERS are left out). 1.0 = the presets. None when there's no
    usable fit, or it's under SEVERITY_MIN_USABLE: wear swamped by the track getting faster.
    """
    pairs = [(m["deg_rate"] / config.COMPOUND_PRESETS[c]["deg_rate"], m["n_drivers"])
             for c, m in field.items()
             if c in config.DRY_COMPOUNDS and m["n_drivers"] >= config.STRATEGY_MIN_DRIVERS]
    if not pairs:
        return None
    severity = float(sum(r * n for r, n in pairs) / sum(n for _, n in pairs))
    return severity if severity >= config.SEVERITY_MIN_USABLE else None


def season_factor(this_season: dict[str, float | None], last_season: dict[str, float | None]) -> float:
    """
    This season's tyre severity against last season's at the same circuits (median of the
    ratios, clipped to DEG_RATIO_CLIP): new tyres or cars change wear everywhere.
    """
    ratios = [this_season[k] / last_season[k] for k in set(this_season) & set(last_season)
              if this_season[k] and last_season[k] and last_season[k] > 0.05]
    return float(np.clip(np.median(ratios), *config.DEG_RATIO_CLIP)) if ratios else 1.0


def _candidate_plans(total_laps: int) -> list[list[sim.Stint]]:
    """Every 1-stop and 2-stop plan over the dry compounds using at least two of them."""
    lo, step = config.STRATEGY_MIN_STINT, config.STRATEGY_SEARCH_STEP
    plans = []
    for a, b in product(config.DRY_COMPOUNDS, repeat=2):
        if a != b:
            plans += [[(a, n), (b, total_laps - n)] for n in range(lo, total_laps - lo + 1)]
    for a, b, c in product(config.DRY_COMPOUNDS, repeat=3):
        if len({a, b, c}) < 2:
            continue
        for n1 in range(lo, total_laps - 2 * lo + 1, step):
            for n2 in range(lo, total_laps - n1 - lo + 1, step):
                plans.append([(a, n1), (b, n2), (c, total_laps - n1 - n2)])
    return plans


def _compound_set(plan: list[sim.Stint]) -> tuple:
    return tuple(sorted(config.COMPOUND_SHORT[c] for c, _ in plan))


def _softest_first(stint: sim.Stint) -> int:
    return config.DRY_COMPOUNDS.index(stint[0])


def best_strategies(base_pace: dict[str, float], deg: dict[str, float], total_laps: int,
                    pit_loss: float, seed: int = 7) -> dict:
    """
    Search every plan deterministically and keep the best plan for each set of compounds
    (with no Safety Car or rain the order of the same stints doesn't change the race time,
    so S→H→H and H→H→S are one plan; the soft-first, harder-later order is kept), then Monte Carlo the top STRATEGY_TOP_N (always including the best 1-stop and
    2-stop). Returns {"strategies": [...], "trace": DataFrame(strategy, lap, gap_to_best)}.
    """
    models = sim.build_compound_models(base_pace, deg)
    best_by_order: dict[str, tuple[float, list[sim.Stint]]] = {}
    for plan in _candidate_plans(total_laps):
        total, _ = sim._simulate_core(plan, total_laps, models, pit_loss,
                                      config.FUEL_EFFECT_PER_LAP, None, detail=False)
        order = _compound_set(plan)
        if order not in best_by_order or total < best_by_order[order][0] - 1e-9:
            best_by_order[order] = (total, sorted(plan, key=_softest_first))

    ranked = sorted(best_by_order.values(), key=lambda t: t[0])
    one = next(p for _, p in ranked if len(p) == 2)
    two = next(p for _, p in ranked if len(p) == 3)
    picked = [one, two] + [p for _, p in ranked if p is not one and p is not two]
    picked = sorted(picked[:config.STRATEGY_TOP_N], key=lambda p: best_by_order[_compound_set(p)][0])

    named = {f"{len(p) - 1}-Stop · {sim.strategy_label(p)}": p for p in picked}
    kwargs = dict(base_pace=base_pace, degradation_rate=deg, total_laps=total_laps, pit_loss=pit_loss)
    laps_long, summary = sim.compare_strategies(named, **kwargs)
    _, mc = sim.run_monte_carlo(named, seed=seed, **kwargs)
    mc = mc.set_index("Strategy")
    strategies = [{
        "name": r["Strategy"], "plan": r["Plan"], "stops": int(r["Stops"]),
        "pit_laps": [int(x) for x in r["Pit laps"]], "total": float(r["Total (s)"]),
        "delta": float(r["Δ to best (s)"]),
        "mean": float(mc.loc[r["Strategy"], "Mean (s)"]),
        "p10": float(mc.loc[r["Strategy"], "P10 (s)"]),
        "p90": float(mc.loc[r["Strategy"], "P90 (s)"]),
        "win_prob": float(mc.loc[r["Strategy"], "Win probability"]),
        "stints": [{"compound": c, "laps": int(n)} for c, n in named[r["Strategy"]]],
    } for _, r in summary.iterrows()]
    trace = laps_long[["Strategy", "Lap", "GapToBest"]].rename(
        columns={"Strategy": "strategy", "Lap": "lap", "GapToBest": "gap_to_best"})
    return {"strategies": strategies, "trace": trace}
