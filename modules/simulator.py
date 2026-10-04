"""
modules/simulator.py — Deterministic & stochastic race strategy sandbox.

Lap-time model (seconds), per lap L on compound c at tyre age a:

    lap = base[c] + deg_eff[c] * a + cliff_extra(c, a) - FUEL * (L - 1)
          + slick_penalty      (if raining and still on slicks)
          + pit_loss           (on the in-lap of a stop; scaled under SC)

    deg_eff[c] = deg[c] * upgrade_factor * temp_factor[c]
    upgrade_factor = 1 - upgrade_pct / 100     (+2 % upgrade -> slope x 0.98)
    temp_factor[c] = 1 + temp_sensitivity[c] * track_temp_delta   (hotter -> more deg,
                     and the cliff arrives earlier: cliff_age / temp_factor)

Weather: from the rain entry lap onwards the track is wet. A car on slicks
completes that lap with a heavy slick penalty and is forced to pit onto the
rain profile's compound (Intermediate or Wet); remaining planned dry stops
are cancelled and the wet compound's own deg slope takes over.

The same core (`_simulate_core`) drives both engines:
    run_sandbox_simulation()  deterministic, one strategy, full lap table
    compare_strategies()      deterministic, many strategies side by side
    run_monte_carlo()         stochastic: lap noise + random Safety Cars,
                              common random numbers across strategies
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from scipy import stats

import config

Stint = tuple[str, int]                 # (compound, laps)

# Preset strategies as (compound, fraction of race distance).
STRATEGY_PRESETS: dict[str, list[tuple[str, float]]] = {
    "1-Stop · M→H": [("MEDIUM", 0.40), ("HARD", 0.60)],
    "1-Stop · H→M": [("HARD", 0.58), ("MEDIUM", 0.42)],
    "2-Stop · S→M→S": [("SOFT", 0.25), ("MEDIUM", 0.47), ("SOFT", 0.28)],
    "2-Stop · M→H→S": [("MEDIUM", 0.30), ("HARD", 0.45), ("SOFT", 0.25)],
}


# ---------------------------------------------------------------------------
# Strategy construction
# ---------------------------------------------------------------------------
def preset_strategy(name: str, total_laps: int) -> list[Stint]:
    """Turn a fractional preset into integer stint lengths summing to total_laps."""
    fracs = STRATEGY_PRESETS[name]
    laps = [max(1, round(f * total_laps)) for _, f in fracs[:-1]]
    laps.append(max(1, total_laps - sum(laps)))
    return [(c, n) for (c, _), n in zip(fracs, laps)]


def parse_strategy(text: str, total_laps: int) -> list[Stint]:
    """
    Parse a compact strategy string, e.g. "S-18, M-22, S" or "M20 H".
    Letters S/M/H/I/W; the final stint may omit its length (runs to the flag).
    Raises ValueError on malformed input.
    """
    tokens = [t for t in re.split(r"[,\s→>]+", text.strip().upper()) if t]
    if not tokens:
        raise ValueError("empty strategy")
    stints: list[Stint] = []
    for i, tok in enumerate(tokens):
        m = re.fullmatch(r"([SMHIW])[-:]?(\d+)?", tok)
        if not m:
            raise ValueError(f"cannot parse '{tok}' (use e.g. S-18)")
        comp = config.SHORT_TO_COMPOUND[m.group(1)]
        if m.group(2):
            stints.append((comp, int(m.group(2))))
        elif i == len(tokens) - 1:
            stints.append((comp, -1))          # placeholder: rest of race
        else:
            raise ValueError(f"stint '{tok}' needs a lap count")
    return normalise_strategy(stints, total_laps)


def normalise_strategy(stints: list[Stint], total_laps: int) -> list[Stint]:
    """
    Make stint lengths sum exactly to total_laps. A negative length means
    "rest of the race"; overlong plans are trimmed, short ones extend the last stint.
    """
    out: list[Stint] = []
    used = 0
    for comp, n in stints:
        n = total_laps - used if n < 0 else min(n, total_laps - used)
        if n <= 0:
            break
        out.append((comp, n))
        used += n
    if not out:
        return [(stints[0][0], total_laps)]
    if used < total_laps:
        out[-1] = (out[-1][0], out[-1][1] + total_laps - used)
    return out


def strategy_label(stints: list[Stint]) -> str:
    return " → ".join(f"{config.COMPOUND_SHORT[c]}{n}" for c, n in stints)


# ---------------------------------------------------------------------------
# Compound model assembly
# ---------------------------------------------------------------------------
def build_compound_models(base_pace: float | dict[str, float],
                          degradation_rate: float | dict[str, float],
                          upgrade_modifier: float = 0.0,
                          track_temp_delta: float = 0.0,
                          weather_modifier: dict | None = None) -> dict[str, dict]:
    """
    Resolve per-compound {base, deg, cliff_age, cliff_rate} after modifiers.

    base_pace: float = fresh-SOFT full-fuel lap time (others offset by preset),
               or dict per compound (missing compounds inferred from presets).
    degradation_rate: float = MEDIUM-equivalent slope (others scaled by preset
               ratio), or dict per compound (missing ones from presets).
    """
    presets = config.COMPOUND_PRESETS
    # --- base pace per dry compound ---
    if isinstance(base_pace, dict):
        known = {c: v for c, v in base_pace.items() if c in config.DRY_COMPOUNDS}
        ref = np.mean([v - presets[c]["base_offset"] for c, v in known.items()]) if known else 90.0
        base = {c: known.get(c, ref + presets[c]["base_offset"]) for c in config.DRY_COMPOUNDS}
    else:
        base = {c: float(base_pace) + presets[c]["base_offset"] for c in config.DRY_COMPOUNDS}
    # --- raw deg per dry compound ---
    if isinstance(degradation_rate, dict):
        deg = {c: degradation_rate.get(c, presets[c]["deg_rate"]) for c in config.DRY_COMPOUNDS}
    else:
        scale = float(degradation_rate) / presets["MEDIUM"]["deg_rate"]
        deg = {c: presets[c]["deg_rate"] * scale for c in config.DRY_COMPOUNDS}

    upgrade_factor = 1.0 - float(upgrade_modifier) / 100.0
    models: dict[str, dict] = {}
    for c in config.DRY_COMPOUNDS:
        temp_factor = max(0.2, 1.0 + presets[c]["temp_sensitivity"] * float(track_temp_delta))
        models[c] = {
            "base": base[c],
            "deg": max(0.0, deg[c]) * upgrade_factor * temp_factor,
            "cliff_age": presets[c]["cliff_age"] / temp_factor,
            "cliff_rate": presets[c]["cliff_rate"],
        }

    # Wet-weather compound only exists in the model once rain is scheduled.
    if weather_modifier and weather_modifier.get("rain_lap"):
        prof = config.RAIN_PROFILES[weather_modifier.get("intensity", "Light rain")]
        wc = prof["compound"]
        models[wc] = {
            "base": min(base.values()) + float(prof["pace_offset"]),
            "deg": presets[wc]["deg_rate"] * float(prof["deg_multiplier"]) * upgrade_factor,
            "cliff_age": presets[wc]["cliff_age"],
            "cliff_rate": presets[wc]["cliff_rate"],
        }
    return models


# ---------------------------------------------------------------------------
# Core lap loop
# ---------------------------------------------------------------------------
def _tyre_lap(model: dict, age: int) -> float:
    extra = max(0.0, age - model["cliff_age"]) * model["cliff_rate"]
    return model["base"] + model["deg"] * age + extra


def _simulate_core(stints: list[Stint], total_laps: int, models: dict[str, dict],
                   pit_loss: float, fuel_effect: float, weather: dict | None,
                   noise: np.ndarray | None = None,
                   sc_laps: frozenset[int] = frozenset(),
                   sc_lap_time: float | None = None,
                   detail: bool = True):
    """
    Lap-by-lap race. Returns (total_time, rows) where rows is a list of per-lap
    dicts when detail=True, else None (fast path for Monte Carlo).
    """
    rain_lap = (weather or {}).get("rain_lap")
    wet_compound = None
    slick_penalty = 0.0
    if rain_lap:
        prof = config.RAIN_PROFILES[weather.get("intensity", "Light rain")]
        wet_compound, slick_penalty = prof["compound"], float(prof["slick_penalty"])

    planned = list(np.cumsum([n for _, n in stints])[:-1])     # end-of-lap pit laps
    next_compounds = [c for c, _ in stints[1:]]
    compound = stints[0][0]
    if rain_lap == 1:                                          # wet start
        compound, planned, next_compounds = wet_compound, [], []
    age, stint_no, total = 0, 1, 0.0
    rows = [] if detail else None

    for lap in range(1, total_laps + 1):
        age += 1
        wet = bool(rain_lap) and lap >= rain_lap
        t = _tyre_lap(models[compound], age) - fuel_effect * (lap - 1)
        if wet and compound in config.DRY_COMPOUNDS:
            t += slick_penalty
        if noise is not None:
            t += noise[lap - 1]
        under_sc = lap in sc_laps
        if under_sc and sc_lap_time is not None:
            t = max(t, sc_lap_time)

        # ---- pit decision at the end of this lap ----
        pit_to = None
        if lap < total_laps:
            if wet and compound in config.DRY_COMPOUNDS:
                pit_to, planned, next_compounds = wet_compound, [], []      # forced
            elif planned and lap == planned[0]:
                planned.pop(0)
                pit_to = next_compounds.pop(0)
            elif under_sc and planned and planned[0] - lap <= config.MC_SC_PIT_WINDOW:
                planned.pop(0)                                              # cheap SC stop
                pit_to = next_compounds.pop(0)
        if pit_to is not None:
            t += pit_loss * (config.SC_PIT_LOSS_FACTOR if under_sc else 1.0)

        total += t
        if detail:
            rows.append({"Lap": lap, "Compound": compound, "TyreAge": age, "Stint": stint_no,
                         "LapTime": t, "CumulativeTime": total, "PitIn": pit_to is not None,
                         "Wet": wet, "SafetyCar": under_sc})
        if pit_to is not None:
            compound, age, stint_no = pit_to, 0, stint_no + 1
    return total, rows


# ---------------------------------------------------------------------------
# Deterministic engines
# ---------------------------------------------------------------------------
def run_sandbox_simulation(base_pace: float | dict[str, float],
                           degradation_rate: float | dict[str, float],
                           total_laps: int,
                           upgrade_modifier: float = 0.0,
                           weather_modifier: dict | None = None,
                           strategy: list[Stint] | None = None,
                           pit_loss: float = config.DEFAULT_PIT_LOSS,
                           track_temp_delta: float = 0.0,
                           fuel_effect: float = config.FUEL_EFFECT_PER_LAP) -> dict:
    """
    Theoretical total race time for one stint strategy (default 1-Stop M→H).

    upgrade_modifier: percent; +2 scales every deg slope by 0.98.
    weather_modifier: None or {"rain_lap": int, "intensity": "Light rain"|"Heavy rain"}.
    Returns {"total_time", "laps" (DataFrame), "pit_laps", "strategy"}.
    """
    strategy = normalise_strategy(strategy or preset_strategy("1-Stop · M→H", total_laps), total_laps)
    models = build_compound_models(base_pace, degradation_rate, upgrade_modifier,
                                   track_temp_delta, weather_modifier)
    total, rows = _simulate_core(strategy, total_laps, models, pit_loss, fuel_effect, weather_modifier)
    laps = pd.DataFrame(rows)
    return {"total_time": total, "laps": laps,
            "pit_laps": laps.loc[laps["PitIn"], "Lap"].tolist(), "strategy": strategy}


def compare_strategies(strategies: dict[str, list[Stint]], **sim_kwargs) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Run several strategies deterministically with identical modifiers.

    Returns (laps_long, summary). laps_long has a Strategy column and
    GapToBest = cumulative time minus the fastest strategy's cumulative time.
    """
    frames, summary = [], []
    for name, stints in strategies.items():
        res = run_sandbox_simulation(strategy=stints, **sim_kwargs)
        df = res["laps"].assign(Strategy=name)
        frames.append(df)
        summary.append({"Strategy": name, "Plan": strategy_label(res["strategy"]),
                        "Stops": len(res["pit_laps"]), "Pit laps": res["pit_laps"],
                        "Total (s)": res["total_time"]})
    laps_long = pd.concat(frames, ignore_index=True)
    summ = pd.DataFrame(summary).sort_values("Total (s)").reset_index(drop=True)
    best = summ.iloc[0]["Strategy"]
    best_cum = laps_long[laps_long["Strategy"] == best].set_index("Lap")["CumulativeTime"]
    laps_long["GapToBest"] = laps_long["CumulativeTime"] - laps_long["Lap"].map(best_cum)
    summ["Δ to best (s)"] = summ["Total (s)"] - summ["Total (s)"].min()
    return laps_long, summ


# ---------------------------------------------------------------------------
# Stochastic engine
# ---------------------------------------------------------------------------
def run_monte_carlo(strategies: dict[str, list[Stint]], base_pace, degradation_rate,
                    total_laps: int, upgrade_modifier: float = 0.0,
                    weather_modifier: dict | None = None,
                    pit_loss: float = config.DEFAULT_PIT_LOSS,
                    track_temp_delta: float = 0.0,
                    n_sims: int = config.MC_DEFAULT_SIMS,
                    lap_noise_sd: float = config.MC_LAP_NOISE_SD,
                    sc_probability: float = config.MC_SC_PROBABILITY,
                    seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Monte Carlo race outcomes with lap-time noise and random Safety Cars.

    Common random numbers: within one simulated race every strategy sees the
    same noise draw and the same SC timing, so differences between strategies
    come from the strategy, not from luck of the draw.

    Returns (results_long[Sim, Strategy, Total], summary) where summary holds
    mean, P10/P90, a 95 % CI on the mean and the win probability.
    """
    rng = np.random.default_rng(seed)
    models = build_compound_models(base_pace, degradation_rate, upgrade_modifier,
                                   track_temp_delta, weather_modifier)
    sc_lap_time = min(m["base"] for m in models.values()) * config.MC_SC_LAP_FACTOR
    norm = {k: normalise_strategy(v, total_laps) for k, v in strategies.items()}
    lo, hi = config.MC_SC_DURATION_LAPS

    out = np.empty((n_sims, len(norm)))
    for i in range(n_sims):
        noise = rng.normal(0.0, lap_noise_sd, total_laps)
        sc = frozenset()
        if total_laps > 6 and rng.random() < sc_probability:
            start = int(rng.integers(2, total_laps - 3))
            sc = frozenset(range(start, min(total_laps, start + int(rng.integers(lo, hi + 1)))))
        for j, stints in enumerate(norm.values()):
            out[i, j], _ = _simulate_core(stints, total_laps, models, pit_loss,
                                          config.FUEL_EFFECT_PER_LAP, weather_modifier,
                                          noise=noise, sc_laps=sc, sc_lap_time=sc_lap_time,
                                          detail=False)

    names = list(norm)
    results = pd.DataFrame(out, columns=names).rename_axis("Sim").reset_index() \
        .melt(id_vars="Sim", var_name="Strategy", value_name="Total (s)")
    winners = pd.Series(np.array(names)[out.argmin(axis=1)]).value_counts(normalize=True)
    sem = stats.sem(out, axis=0) if n_sims > 1 else np.zeros(len(names))
    summary = pd.DataFrame({
        "Strategy": names,
        "Mean (s)": out.mean(axis=0),
        "P10 (s)": np.percentile(out, 10, axis=0),
        "P90 (s)": np.percentile(out, 90, axis=0),
        "95% CI ± (s)": sem * stats.t.ppf(0.975, max(n_sims - 1, 1)),
        "Win probability": [float(winners.get(n, 0.0)) for n in names],
    }).sort_values("Mean (s)").reset_index(drop=True)
    return results, summary
