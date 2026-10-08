"""
modules/practice.py — Free practice (FP1, FP2, FP3): what a session says about a weekend.

Teams run two kinds of programme, and the analysis splits them:

* **One-lap pace** (the qualifying simulations): each driver's best clean lap (green track, not
  deleted, not an in or out lap), as a % against the field median of best laps. Fuel loads and
  engine modes are unknown, so it's noisy, most of all in FP1.
* **Long runs** (the race simulations), as analysts usually define them (F1 publishes no definition):
  a stint on one set of tyres with at least PRACTICE_LONG_RUN_LAPS laps once in and out laps and slow
  laps (past PRACTICE_RUN_TOL of the stint's best: traffic, yellows) are left out; a cool-down lap (past F1's
  107%, OUTLIER_LAP_FACTOR) ends the run, as a qualifying-style programme. Each lap is fuel corrected (+FUEL_EFFECT_PER_LAP per lap into the run: the fuel burnt).
  Lap time = driver effect + compound effect, fitted together by alternating medians (few drivers
  run the Soft long, so a per-compound median alone would flatter them); a driver's long-run pace is
  their effect as a % of the median lap. Deg is the slope of fuel-corrected time against tyre age.

`weekend_practice` combines a weekend's sessions (later sessions count more: PRACTICE_SESSION_WEIGHT)
into the two figures the forecasts use (forecast.expected_pace's `practice`).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config


def _clean(laps: pd.DataFrame) -> pd.DataFrame:
    """Laps that could be push or run laps: timed, green, not deleted, not in or out laps."""
    ok = laps["LapTime"].notna() & ~laps["PitIn"] & ~laps["PitOut"]
    ok &= ~laps["TrackStatus"].astype(str).str.contains("[24567]", regex=True)
    if "Deleted" in laps:
        ok &= ~laps["Deleted"].fillna(False).astype(bool)
    return laps[ok]


def one_lap(laps: pd.DataFrame) -> pd.DataFrame:
    """Each driver's best clean lap: driver, best (s), compound, pace (% against the field median)."""
    c = _clean(laps)
    if c.empty:
        return pd.DataFrame(columns=["driver", "best", "compound", "pace"])
    best = c.loc[c.groupby("Driver")["LapTime"].idxmin()]
    out = pd.DataFrame({"driver": best["Driver"].to_numpy(), "best": best["LapTime"].to_numpy(),
                        "compound": best["Compound"].fillna("?").to_numpy()})
    out["pace"] = (out["best"] / out["best"].median() - 1.0) * 100.0
    return out.sort_values("best").reset_index(drop=True)


def long_runs(laps: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    (runs, run laps). runs: one row per long run (driver, stint, compound, first, last, laps,
    median fuel-corrected lap, deg in s/lap, pace %). run laps: every lap in a long run with its
    fuel-corrected time and `rel` (% against the field median on that compound).
    """
    cols = ["driver", "stint", "compound", "first", "last", "laps", "median", "deg", "pace"]
    if laps.empty:
        return pd.DataFrame(columns=cols), pd.DataFrame()
    laps = laps.sort_values(["Driver", "LapNumber"])
    keep = []
    for (drv, stint), g in laps.groupby(["Driver", "Stint"], sort=False):
        good = (_clean(g)["LapTime"]).reindex(g.index)
        if good.notna().sum() < config.PRACTICE_LONG_RUN_LAPS:
            continue
        # The usual long-run definition (there's no official one): a stint on one set of tyres with at
        # least PRACTICE_LONG_RUN_LAPS laps once in/out laps and slow laps are taken out. Slow = past
        # PRACTICE_RUN_TOL of the stint's best (traffic, yellows, a deleted lap): left out of the figures.
        # A lap past F1's 107% (OUTLIER_LAP_FACTOR) of the best is a cool-down: a qualifying-style
        # programme (push, cool-down, push), so it ends the run.
        best = good.min()
        ok = good.notna() & (good <= best * (1 + config.PRACTICE_RUN_TOL))
        cool = g["LapTime"] > best * config.OUTLIER_LAP_FACTOR
        for _, b in g[ok].groupby(cool.cumsum()[ok]):
            if len(b) >= config.PRACTICE_LONG_RUN_LAPS:
                b = b.copy()
                b["run_lap"] = (b["LapNumber"] - b["LapNumber"].iloc[0]).astype(int).to_numpy()   # fuel burnt, slow laps too
                b["run_id"] = f"{drv}-{int(stint) if pd.notna(stint) else 0}-{int(b['LapNumber'].iloc[0])}"
                keep.append(b)
    if not keep:
        return pd.DataFrame(columns=cols), pd.DataFrame()
    run_laps = pd.concat(keep)
    run_laps["fc"] = run_laps["LapTime"] + config.FUEL_EFFECT_PER_LAP * run_laps["run_lap"]
    drv, comp = _effects(run_laps)
    base = run_laps["fc"].median()
    # A lap against the field on the same tyres: what's left after the compound's effect.
    run_laps["rel"] = (run_laps["fc"] - run_laps["Compound"].map(comp) - base) / base * 100.0
    run_laps.attrs["driver_effect"] = drv / base * 100.0
    rows = []
    for rid, r in run_laps.groupby("run_id", sort=False):
        age = r["TyreLife"].to_numpy(float)
        deg = float(np.polyfit(age, r["fc"].to_numpy(float), 1)[0]) if np.isfinite(age).all() and np.ptp(age) > 0 else np.nan
        rows.append({"driver": r["Driver"].iloc[0], "stint": r["Stint"].iloc[0], "compound": r["Compound"].iloc[0],
                     "first": int(r["LapNumber"].iloc[0]), "last": int(r["LapNumber"].iloc[-1]), "laps": len(r),
                     "median": float(r["fc"].median()), "deg": deg, "pace": float(r["rel"].median())})
    return pd.DataFrame(rows, columns=cols), run_laps


def _effects(run_laps: pd.DataFrame, iters: int = 20) -> tuple[pd.Series, pd.Series]:
    """Driver and compound effects (s) on fuel-corrected lap time, by alternating medians."""
    y = run_laps["fc"] - run_laps["fc"].median()
    comp = pd.Series(0.0, index=run_laps["Compound"].unique())
    drv = pd.Series(0.0, index=run_laps["Driver"].unique())
    for _ in range(iters):
        drv = (y - run_laps["Compound"].map(comp)).groupby(run_laps["Driver"]).median()
        comp = (y - run_laps["Driver"].map(drv)).groupby(run_laps["Compound"]).median()
        comp -= comp.mean()
    return drv - drv.median(), comp


def session_pace(laps: pd.DataFrame) -> dict[str, pd.Series]:
    """{"one_lap": driver -> %, "long_run": driver -> % (drivers with long-run laps)}, each centred
    on the field median (negative = faster)."""
    ol = one_lap(laps).set_index("driver")["pace"]
    _, rl = long_runs(laps)
    lr = rl.attrs["driver_effect"] if not rl.empty else pd.Series(dtype=float)
    return {"one_lap": ol - ol.median() if not ol.empty else ol,
            "long_run": lr - lr.median() if not lr.empty else lr}


def weekend_practice(sessions: dict[str, dict[str, pd.Series]]) -> dict[str, pd.Series]:
    """A weekend's practice ({code: session_pace}) as one one-lap and one long-run figure per
    driver: each session's figure weighted PRACTICE_SESSION_WEIGHT[code] (later counts more)."""
    out = {}
    for kind in ("one_lap", "long_run"):
        num, den = pd.Series(dtype=float), pd.Series(dtype=float)
        for code, p in sessions.items():
            s = p.get(kind)
            if s is None or s.empty:
                continue
            w = config.PRACTICE_SESSION_WEIGHT.get(code, 1.0)
            num = num.add(s * w, fill_value=0.0)
            den = den.add(pd.Series(w, index=s.index), fill_value=0.0)
        v = (num / den).dropna() if not num.empty else num
        out[kind] = v - v.median() if not v.empty else v
    return out
