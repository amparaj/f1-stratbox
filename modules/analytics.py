"""
modules/analytics.py — Tyre degradation modelling, cliff detection, undercut maths.

Conventions
-----------
* All times are float seconds (data_engine converts FastF1 timedeltas first).
* Fuel correction normalises every lap to a full-tank equivalent:
      FuelCorrectedLapTime = LapTime + FUEL_EFFECT_PER_LAP * (LapNumber - 1)
  so the remaining slope versus TyreLife is tyre degradation, not fuel burn.
* A degradation model is the straight line  LapTime_fc = base_pace + deg_rate * TyreLife
  (base_pace = intercept at zero tyre age, deg_rate = seconds lost per lap of age).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

import config

DegModel = dict[str, float]                     # {"base_pace", "deg_rate", ...}
DegTable = dict[str, dict[str, DegModel]]       # driver -> compound -> model


# ---------------------------------------------------------------------------
# Fuel correction & degradation fits
# ---------------------------------------------------------------------------
def fuel_correct(laps: pd.DataFrame, fuel_effect: float = config.FUEL_EFFECT_PER_LAP) -> pd.DataFrame:
    """Add FuelCorrectedLapTime (full-tank equivalent, seconds)."""
    out = laps.copy()
    out["FuelCorrectedLapTime"] = out["LapTime"].astype(float) + fuel_effect * (out["LapNumber"] - 1)
    return out


def _fit_line(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """np.polyfit degree-1 fit -> (slope, intercept, r_squared)."""
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), float(intercept), r2


def calculate_tyre_degradation(clean_laps_df: pd.DataFrame,
                               clean_air_only: bool = True,
                               min_laps: int = config.MIN_LAPS_FOR_FIT) -> DegTable:
    """
    Fit LapTime_fc = base_pace + deg_rate * TyreLife for every driver/compound.

    Uses clean-air laps when there are enough of them; otherwise falls back to
    all cleaned laps for that driver/compound and records that in 'sample'.
    Compounds with fewer than `min_laps` laps or < 3 distinct tyre ages are skipped.

    Returns {driver: {compound: {"base_pace", "deg_rate", "r_squared",
                                 "n_laps", "max_tyre_life", "sample"}}}.
    """
    if clean_laps_df.empty:
        return {}
    laps = fuel_correct(clean_laps_df)
    result: DegTable = {}
    for (driver, compound), grp in laps.groupby(["Driver", "Compound"]):
        sample = "clean air"
        data = grp[grp["CleanAir"]] if clean_air_only else grp
        if len(data) < min_laps or data["TyreLife"].nunique() < 3:
            data, sample = grp, "all laps"
        if len(data) < min_laps or data["TyreLife"].nunique() < 3:
            continue
        x = data["TyreLife"].to_numpy(dtype=float)
        y = data["FuelCorrectedLapTime"].to_numpy(dtype=float)
        slope, intercept, r2 = _fit_line(x, y)
        result.setdefault(driver, {})[compound] = {
            "base_pace": intercept,
            "deg_rate": slope,
            "r_squared": r2,
            "n_laps": int(len(data)),
            "max_tyre_life": float(x.max()),
            "sample": sample,
        }
    return result


def degradation_to_frame(deg: DegTable) -> pd.DataFrame:
    """Flatten a DegTable into a tidy DataFrame for tables and export."""
    rows = [{"Driver": d, "Compound": c, **m} for d, comps in deg.items() for c, m in comps.items()]
    cols = ["Driver", "Compound", "base_pace", "deg_rate", "r_squared", "n_laps",
            "max_tyre_life", "sample"]
    return pd.DataFrame(rows, columns=cols)


def field_compound_model(deg: DegTable) -> dict[str, DegModel]:
    """
    Field-wide model per compound: median base pace and median deg rate across
    drivers. Used as a fallback when a driver has no fit for a compound, and to
    calibrate the Future Sandbox. Negative deg (track evolution) is floored at 0.
    """
    df = degradation_to_frame(deg)
    out: dict[str, DegModel] = {}
    for compound, grp in df.groupby("Compound"):
        out[compound] = {
            "base_pace": float(grp["base_pace"].median()),
            "deg_rate": max(0.0, float(grp["deg_rate"].median())),
            "deg_iqr": float(stats.iqr(grp["deg_rate"])) if len(grp) > 1 else 0.0,
            "n_drivers": int(len(grp)),
        }
    return out


# ---------------------------------------------------------------------------
# Tyre cliff detection
# ---------------------------------------------------------------------------
def detect_tyre_cliff(stint_laps: pd.DataFrame) -> dict | None:
    """
    Find the lap where pace 'fell off the cliff' within one stint.

    Method: two-segment piecewise linear regression on fuel-corrected lap time
    vs tyre age. Every admissible breakpoint is tried; the best split is a
    cliff only if (a) the post-break slope exceeds the pre-break slope by at
    least CLIFF_MIN_SLOPE_JUMP s/lap and (b) splitting reduces the residual
    sum of squares by at least CLIFF_MIN_SSE_GAIN versus a single line.

    Returns {"lap", "tyre_life", "slope_before", "slope_after", "time_lost"} or None.
    """
    s = stint_laps.sort_values("TyreLife")
    if "FuelCorrectedLapTime" not in s:
        s = fuel_correct(s)
    n = len(s)
    if n < config.CLIFF_MIN_STINT_LAPS:
        return None
    x = s["TyreLife"].to_numpy(dtype=float)
    y = s["FuelCorrectedLapTime"].to_numpy(dtype=float)
    laps = s["LapNumber"].to_numpy()

    def sse(xs, ys):
        coef = np.polyfit(xs, ys, 1)
        return float(np.sum((ys - np.polyval(coef, xs)) ** 2)), float(coef[0])

    sse_one, _ = sse(x, y)
    if sse_one <= 1e-9:
        return None
    best = None
    k_min = max(config.CLIFF_MIN_SEGMENT_LAPS, 4)
    for k in range(k_min, n - config.CLIFF_MIN_SEGMENT_LAPS + 1):
        if len(np.unique(x[:k])) < 2 or len(np.unique(x[k:])) < 2:
            continue
        sse_a, slope_a = sse(x[:k], y[:k])
        sse_b, slope_b = sse(x[k:], y[k:])
        total = sse_a + sse_b
        if best is None or total < best[0]:
            best = (total, k, slope_a, slope_b)
    if best is None:
        return None
    total, k, slope_a, slope_b = best
    gain = 1.0 - total / sse_one
    # A cliff needs real post-break degradation, not merely a flattening of a
    # negative (track-evolution / drying-track) slope.
    if (slope_b - slope_a < config.CLIFF_MIN_SLOPE_JUMP
            or slope_b < config.CLIFF_MIN_SLOPE_JUMP
            or gain < config.CLIFF_MIN_SSE_GAIN):
        return None
    # Time lost after the cliff versus the pre-cliff trend (never assume the
    # tyre would have kept getting faster).
    pre_slope, pre_icpt = np.polyfit(x[:k], y[:k], 1)
    if pre_slope < 0:
        pre_slope, pre_icpt = 0.0, float(np.mean(y[:k]))
    time_lost = float(np.sum(y[k:] - (pre_slope * x[k:] + pre_icpt)))
    if time_lost < config.CLIFF_MIN_TIME_LOST:
        return None
    return {
        "lap": int(laps[k]),
        "tyre_life": int(x[k]),
        "slope_before": slope_a,
        "slope_after": slope_b,
        "time_lost": time_lost,
        "sse_gain": gain,
    }


# ---------------------------------------------------------------------------
# Undercut vulnerability
# ---------------------------------------------------------------------------
def resolve_model(deg: DegTable, field: dict[str, DegModel],
                  driver: str, compound: str) -> tuple[DegModel | None, str]:
    """Driver's own fit for a compound, else the field median, else None."""
    if compound in deg.get(driver, {}):
        return deg[driver][compound], "driver fit"
    if compound in field:
        return field[compound], "field median"
    return None, "no model"


def assess_undercut(gap_a_to_b: float,
                    a_model: DegModel, a_tyre_age: float,
                    b_fresh_model: DegModel,
                    laps_to_respond: int = 1,
                    out_lap_penalty: float = 0.0) -> dict:
    """
    Is Driver A (ahead) vulnerable if Driver B (behind) pits now?

    Driver B's next lap is projected at their fresh-compound base pace
    (base + deg * 1). Driver A stays out on worn tyres (base + deg * (age + i)).
    Both are on the same lap so fuel cancels, and pit losses cancel once A
    responds. If the gap A->B is smaller than B's projected delta advantage
    summed over the laps before A can respond, A is 'Vulnerable to Undercut'.
    """
    advantage = 0.0
    for i in range(1, laps_to_respond + 1):
        a_lap = a_model["base_pace"] + max(a_model["deg_rate"], 0.0) * (a_tyre_age + i)
        b_lap = b_fresh_model["base_pace"] + max(b_fresh_model["deg_rate"], 0.0) * i
        advantage += a_lap - b_lap
    advantage -= out_lap_penalty
    vulnerable = bool(advantage > 0 and gap_a_to_b < advantage)
    return {
        "vulnerable": vulnerable,
        "status": "Vulnerable to Undercut" if vulnerable else "Covered",
        "gap": float(gap_a_to_b),
        "undercut_delta": float(advantage),
        "margin": float(advantage - gap_a_to_b),
    }


def undercut_threats(snapshot: pd.DataFrame, deg: DegTable, field: dict[str, DegModel],
                     fresh_compound: str, laps_to_respond: int = 1,
                     out_lap_penalty: float = 0.0) -> pd.DataFrame:
    """
    Check every adjacent pair in the running order at one lap.

    `snapshot` needs Driver, RunningPosition, GapToLeader, Compound, TyreLife.
    Returns one row per pair (car ahead A, car behind B).
    """
    snap = snapshot.sort_values("RunningPosition").reset_index(drop=True)
    rows = []
    for i in range(1, len(snap)):
        a, b = snap.iloc[i - 1], snap.iloc[i]
        a_model, a_src = resolve_model(deg, field, a["Driver"], a["Compound"])
        b_model, b_src = resolve_model(deg, field, b["Driver"], fresh_compound)
        gap = float(b["GapToLeader"] - a["GapToLeader"])
        if a_model is None or b_model is None or np.isnan(gap):
            continue
        res = assess_undercut(gap, a_model, float(a["TyreLife"]), b_model,
                              laps_to_respond, out_lap_penalty)
        rows.append({
            "Ahead (A)": a["Driver"], "A tyre": f'{a["Compound"][:1]}{int(a["TyreLife"])}',
            "Behind (B)": b["Driver"], "Gap A→B (s)": gap,
            "B undercut gain (s)": res["undercut_delta"], "Margin (s)": res["margin"],
            "Status": res["status"], "Model source": f"A: {a_src} · B: {b_src}",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Stints & post-mortem
# ---------------------------------------------------------------------------
def stint_summary(timeline: pd.DataFrame) -> pd.DataFrame:
    """One row per driver stint: compound, first/last lap, length."""
    tl = timeline.dropna(subset=["Stint"])
    agg = tl.groupby(["Driver", "Stint"]).agg(
        Compound=("Compound", lambda s: s.mode().iat[0] if not s.mode().empty else "UNKNOWN"),
        FirstLap=("LapNumber", "min"), LastLap=("LapNumber", "max"),
        StartAge=("TyreLife", "min"),
    ).reset_index()
    agg["Laps"] = agg["LastLap"] - agg["FirstLap"] + 1
    return agg


def build_postmortem(clean_laps: pd.DataFrame, timeline: pd.DataFrame,
                     drivers: pd.DataFrame) -> list[dict]:
    """
    Rule-based post-mortem for every classified driver.

    For each stint: compound, laps, fitted deg rate and, if detected, the exact
    lap the pace collapsed ('the cliff'). Returns a list of dicts ordered by
    finishing position, each with a ready-to-render 'markdown' field.
    """
    stints = stint_summary(timeline)
    laps_fc = fuel_correct(clean_laps)
    reports = []
    for _, drv in drivers.iterrows():
        code = drv["Driver"]
        d_stints = stints[stints["Driver"] == code].sort_values("Stint")
        if d_stints.empty:
            continue
        stint_rows, cliffs = [], []
        d_tl = timeline[timeline["Driver"] == code]
        rain_laps = set(d_tl.loc[d_tl["Rainfall"].astype(bool), "LapNumber"]) \
            if "Rainfall" in d_tl else set()
        for _, st_row in d_stints.iterrows():
            st_laps = laps_fc[(laps_fc["Driver"] == code) & (laps_fc["Stint"] == st_row["Stint"])]
            deg_rate = base = None
            if len(st_laps) >= config.MIN_LAPS_FOR_FIT and st_laps["TyreLife"].nunique() >= 3:
                deg_rate, base, _ = _fit_line(st_laps["TyreLife"].to_numpy(float),
                                              st_laps["FuelCorrectedLapTime"].to_numpy(float))
            cliff = detect_tyre_cliff(st_laps)
            if cliff:
                # A pace break within ±3 laps of rainfall is weather, not tyre wear.
                near_rain = any(l in rain_laps for l in range(cliff["lap"] - 3, cliff["lap"] + 4))
                cliff["cause"] = "weather" if near_rain else "tyre"
                cliffs.append({**cliff, "compound": st_row["Compound"], "stint": int(st_row["Stint"])})
            stint_rows.append({
                "stint": int(st_row["Stint"]), "compound": st_row["Compound"],
                "first": int(st_row["FirstLap"]), "last": int(st_row["LastLap"]),
                "laps": int(st_row["Laps"]), "deg_rate": deg_rate, "base": base,
                "clean_laps": int(len(st_laps)), "cliff": cliff,
            })

        strategy = " → ".join(f'{config.COMPOUND_SHORT.get(s["compound"], "?")}({s["laps"]})'
                              for s in stint_rows)
        pos = drv.get("ClassifiedPosition", "")
        status = str(drv.get("Status", "") or "")
        header = f"**P{pos} · {code}** — {drv.get('Team', '')} · `{strategy}`" if str(pos).isdigit() \
            else f"**{pos or '—'} · {code}** — {drv.get('Team', '')} · `{strategy}` · {status}"

        lines = [header, ""]
        for s in stint_rows:
            deg_txt = (f"deg **{s['deg_rate']:+.3f} s/lap** over {s['clean_laps']} clean laps"
                       if s["deg_rate"] is not None else "too few clean laps to model")
            line = (f"- Stint {s['stint']} · {s['compound'].title()} · laps {s['first']}–{s['last']}"
                    f" ({s['laps']} laps): {deg_txt}.")
            if s["cliff"] and s["cliff"]["cause"] == "weather":
                c = s["cliff"]
                line += (f" 🌧️ Pace dropped from Lap {c['lap']} but it coincides with rainfall — "
                         f"weather, not tyre wear (≈{c['time_lost']:.1f} s lost).")
            elif s["cliff"]:
                c = s["cliff"]
                line += (f" ⚠️ **Cliff on Lap {c['lap']}** (tyre age {c['tyre_life']}): deg jumped "
                         f"{c['slope_before']:+.2f} → {c['slope_after']:+.2f} s/lap, "
                         f"≈{c['time_lost']:.1f} s lost vs. pre-cliff trend.")
            elif s["deg_rate"] is not None:
                line += " No cliff — pace stayed on the linear trend."
            lines.append(line)
        if bool(drv.get("DNF", False)):
            lines.append(f"- Retired after lap {int(d_stints['LastLap'].max())} ({status}). "
                         "Trace stops there in all charts.")
        reports.append({"driver": code, "position": pos, "strategy": strategy,
                        "stints": stint_rows, "cliffs": cliffs, "markdown": "\n".join(lines)})
    return reports


def build_llm_prompt(info: dict, reports: list[dict], deg_frame: pd.DataFrame) -> str:
    """Structured debrief prompt a strategist can paste into an LLM for a narrative write-up."""
    lines = [
        "You are an F1 race strategy analyst. Write a concise post-race strategy debrief "
        "for the team. Ground every claim in the data below; do not invent numbers.",
        "",
        f"EVENT: {info['year']} {info['event_name']} ({info['session_name']}), "
        f"{info['total_laps']} laps.",
        f"NEUTRALISED LAPS: SC {info['neutralised']['SC'] or 'none'}; "
        f"VSC {info['neutralised']['VSC'] or 'none'}; RED {info['neutralised']['RED'] or 'none'}.",
        f"FUEL CORRECTION: {config.FUEL_EFFECT_PER_LAP} s/lap. Clean air = gap > "
        f"{config.CLEAN_AIR_THRESHOLD_S} s.",
        "",
        "DRIVER STRATEGIES (finishing order):",
    ]
    for r in reports:
        cliff_txt = "; ".join(
            f"{c['compound']} {'cliff' if c['cause'] == 'tyre' else 'rain-related pace drop'} "
            f"lap {c['lap']} (age {c['tyre_life']}, {c['time_lost']:.1f}s lost)"
            for c in r["cliffs"]) or "no cliff"
        lines.append(f"- P{r['position']} {r['driver']}: {r['strategy']} | {cliff_txt}")
    if not deg_frame.empty:
        lines += ["", "COMPOUND DEGRADATION (field median s/lap):"]
        for comp, grp in deg_frame.groupby("Compound"):
            lines.append(f"- {comp}: {grp['deg_rate'].median():+.3f} (n={len(grp)} drivers)")
    lines += ["", "Cover: winning strategy and why, who lost time to tyre cliffs, "
              "undercut/overcut outcomes, and one lesson for the next race."]
    return "\n".join(lines)
