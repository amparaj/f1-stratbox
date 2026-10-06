"""
modules/forecast.py — Season forecasts for the website: driver form, odds for every session of
a weekend (Sprint Qualifying, Sprint, Qualifying, Grand Prix), title odds, and the best tyre
strategy for an upcoming race.

Driver pace
-----------
Race pace (race_pace): for one race or sprint, every clean lap (data_engine.get_cleaned_laps)
is fuel corrected and divided by the field median for its compound, so a lap on Hards isn't
compared with one on Softs. A driver's race pace is the median of those ratios, as a % against
the field median (negative = faster). Drivers need FORM_MIN_CLEAN_LAPS clean laps.
Qualifying pace (data_engine.quali_pace): each driver's best segment time against the Q3
runners' median in that segment, as a % against the field median.

Form = weighted mean of the paces from the last FORM_MAX_RACES rounds, each round back weighted
FORM_DECAY times the next; a sprint counts SPRINT_FORM_WEIGHT of a Grand Prix. Race form uses
races and sprints, qualifying form uses Qualifying and Sprint Qualifying.

Car + driver form (split_form; the sessions in SPLIT_FORM: Qualifying, Sprint Qualifying, Sprint):
the team's form (its drivers' paces less their ratings, weighted as above, this season only) +
the driver's rating against teammates (teammate_ratings: ridge regression on teammate gaps since
the start of last season, fading slowly, chained across pairings) + their streak (own form above
less those two) × STREAK_PERSIST per round ahead. The Grand Prix keeps the plain form.

Session forecasts (Monte Carlo)
-------------------------------
Every simulated session draws each driver's performance as

    expected pace + circuit term + horizon drift + Normal(0, SESSION_SD[code])

and, in a race, a retirement with the driver's DNF rate (shrunk towards the field's; a sprint
SPRINT_DNF_FACTOR of it). Finishing order is the order of performance. Once the race's grid
is known the spread is SESSION_SD_GRID[code]: the grid and that qualifying explain some of it.

  * expected pace: a race blends race form with qualifying form (RACE_QUALI_BLEND: one-lap
    speed says something about the car that a few races of race pace miss). Once the
    session's own qualifying is done, that session's qualifying pace replaces qualifying form
    and the grid adds GRID_WEIGHT[code] % per grid place: track position.
    Qualifying uses qualifying form only.
  * circuit term: how much faster or slower each team was at this circuit last season than
    its own season median (race pace for races, qualifying pace for qualifying), times
    CIRCUIT_WEIGHT, within ±CIRCUIT_CLIP %. Teams are matched across seasons by
    config.TEAM_LINEAGE; a new team has none.
  * horizon drift: one Normal(0, DRIFT_PER_ROUND * sqrt(rounds ahead - 1)) per driver per
    simulated session: form drifts, so a race further away is less certain.
  * power-unit penalty risk (Grand Prix, before the grid is known): each simulated race draws
    whether a driver takes an over-allocation element there (their chance from
    penalties.penalty_risk); if so they lose the places such penalties cost
    (penalties.expected_drop) × PENALTY_GRID_WEIGHT. An announced grid penalty is certain: its
    places count in full. Once the grid is known, a slot behind the qualifying position counts
    PENALTY_GRID_WEIGHT a place, not GRID_WEIGHT: a penalised car is faster than its slot.

10 000 sessions give win / podium / points (or pole / front row / Q3 / knocked out in Q1)
chances. Title odds play every remaining sprint and Grand Prix the same way (each with its own
circuit term) on top of the current points, after shifting each driver's form once per
simulated season by Normal(0, FORM_DRIFT_SD). The constants were fitted by replaying the
forecasts of 2025 and 2026 (scripts/calibrate_forecast.py; see the comments in config.py).

Strategy forecast
-----------------
Per-compound fits from one race are too noisy to plan on (a compound only a few drivers
ran in short late stints can fit with no wear at all), and their intercepts are skewed by
track evolution. So the circuit gets one number, its tyre severity: last season's
field-median deg at that circuit against the preset deg, averaged over compounds
(tyre_severity), times how this season's severity compares with last season's at the
circuits both have raced (season_factor). Every compound's deg is its preset times that,
so the presets set the split between compounds and their pace gaps and cliffs. Every 1-stop and 2-stop plan over the dry compounds is run
through the simulator's lap loop (a sprint also tries no stop at all); the best few go into its
Monte Carlo (Safety Cars, lap noise, and a weather scenario per race from the forecast or the climate,
modules/weather.py). The expected track temperature against last season's race there
moves the deg and cliffs (the simulator's track_temp_delta).
"""

from __future__ import annotations

from itertools import product

import numpy as np
import pandas as pd

import config
from modules import analytics as an
from modules import penalties as pn
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


def form_inputs(paces: list[pd.Series], rounds: list[int] | None = None,
                weights: list[float] | None = None) -> pd.DataFrame:
    """
    Every pace that counts towards form (oldest first in `paces`): columns `i` (its index in
    `paces`), driver, pace, used (the pace within ±FORM_CLIP of the driver's median over the
    window) and weight. With `rounds` (each pace's round), paces from the last FORM_MAX_RACES
    rounds count, each round back weighted FORM_DECAY; without, the last FORM_MAX_RACES paces,
    one step each. `weights` scales each pace (a sprint's).
    """
    cols = ["i", "driver", "pace", "used", "weight"]
    if not paces:
        return pd.DataFrame(columns=cols)
    weights = weights or [1.0] * len(paces)
    if rounds is None:
        rounds = list(range(len(paces)))
    latest = max(rounds)
    keep = [i for i, r in enumerate(rounds) if latest - r < config.FORM_MAX_RACES]
    parts = [pd.DataFrame({"i": i, "driver": paces[i].index, "pace": paces[i].to_numpy(float),
                           "weight": weights[i] * config.FORM_DECAY ** (latest - rounds[i])}) for i in keep]
    out = pd.concat(parts, ignore_index=True).dropna(subset=["pace"]) if parts else pd.DataFrame(columns=cols)
    out["used"] = out["pace"]
    if config.FORM_CLIP is not None and not out.empty:
        centre = out.groupby("driver")["pace"].transform("median")
        out["used"] = out["pace"].clip(centre - config.FORM_CLIP, centre + config.FORM_CLIP)
    return out[cols]


def driver_form(paces: list[pd.Series], rounds: list[int] | None = None,
                weights: list[float] | None = None) -> pd.Series:
    """Weighted mean of each driver's recent paces, outliers held to ±FORM_CLIP (form_inputs)."""
    inp = form_inputs(paces, rounds, weights)
    if inp.empty:
        return pd.Series(dtype=float)
    w = inp["weight"]
    den = w.groupby(inp["driver"]).sum()
    return ((inp["used"] * w).groupby(inp["driver"]).sum() / den.where(den > 0)).dropna()


def teammate_ratings(paces: list[pd.Series], rounds: list[int], teams: list[pd.Series],
                     weights: list[float] | None = None) -> pd.Series:
    """
    Driver -> pace against an average teammate (%, negative = faster), from every pair of
    teammates in `paces` (this season and last: `rounds` run on across seasons, last season's
    ending at 0). Ridge regression: each session's gap between teammates = the difference of
    their ratings; a gap counts TEAMMATE_DECAY per round back (last season's × TEAMMATE_CARRY),
    is held to ±TEAMMATE_CLIP of the pair's median gap (one car's failure or crash can't swing
    it), and the ratings are pulled towards 0 with TEAMMATE_PRIOR sessions' weight. Gaps chain
    across pairings, so a driver who changes team keeps their rating.
    """
    weights = weights or [1.0] * len(paces)
    latest = max(rounds) if rounds else 0
    rows = []
    for p, r, t, w in zip(paces, rounds, teams, weights):
        if latest - r >= config.TEAMMATE_MAX_ROUNDS:
            continue
        team = t.reindex(p.index).map(lambda x: team_now(x) if isinstance(x, str) else x)
        wt = w * config.TEAMMATE_DECAY ** (latest - r) * (config.TEAMMATE_CARRY if r <= 0 else 1.0)
        for _, g in p.dropna().groupby(team):
            if len(g) == 2:
                (a, pa), (b, pb) = sorted(g.items())
                rows.append((a, b, pa - pb, wt))
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["a", "b", "gap", "w"])
    centre = df.groupby(["a", "b"])["gap"].transform("median")
    df["gap"] = df["gap"].clip(centre - config.TEAMMATE_CLIP, centre + config.TEAMMATE_CLIP)
    drivers = sorted(set(df["a"]) | set(df["b"]))
    ix = {d: i for i, d in enumerate(drivers)}
    a = np.zeros((len(df), len(drivers)))
    a[np.arange(len(df)), df["a"].map(ix)] = 1.0
    a[np.arange(len(df)), df["b"].map(ix)] = -1.0
    w = df["w"].to_numpy()
    lhs = a.T @ (a * w[:, None]) + config.TEAMMATE_PRIOR * np.eye(len(drivers))
    return pd.Series(np.linalg.solve(lhs, a.T @ (w * df["gap"].to_numpy())), index=drivers)


def split_form(paces: list[pd.Series], rounds: list[int], teams: list[pd.Series], now: pd.Series,
               weights: list[float] | None = None, ahead: int = 1) -> pd.DataFrame:
    """
    Form as car + driver + streak. `paces` (oldest first) may include last season, its `rounds`
    <= 0; `teams` maps each session's drivers to their team then, `now` each driver to their
    team for the forecast, `ahead` the rounds ahead it is for. Columns: team (the team's form:
    its drivers' paces less their ratings, this season only, weighted as driver_form), driver
    (teammate_ratings), streak (how far the driver's own recent form, driver_form, is from
    team + driver, × STREAK_PERSIST per round ahead: a streak is real but fades) and form, their
    sum. A driver whose team has no form this season gets their own driver_form.
    """
    weights = weights or [1.0] * len(paces)
    rating = teammate_ratings(paces, rounds, teams, weights)
    this = [i for i, r in enumerate(rounds) if r > 0]
    team_paces = []
    for i in this:
        p = paces[i].dropna()
        team = teams[i].reindex(p.index).map(lambda x: team_now(x) if isinstance(x, str) else x)
        team_paces.append((p - rating.reindex(p.index).fillna(0.0)).groupby(team).mean())
    team_form = driver_form(team_paces, [rounds[i] for i in this], [weights[i] for i in this])
    own = driver_form([paces[i] for i in this], [rounds[i] for i in this], [weights[i] for i in this])
    now_team = now.map(lambda x: team_now(x) if isinstance(x, str) else x)
    out = pd.DataFrame({"team": now_team.map(team_form), "driver": rating.reindex(now.index).fillna(0.0)},
                       index=now.index)
    base = out["team"] + out["driver"]
    out["streak"] = ((own.reindex(now.index) - base) * config.STREAK_PERSIST ** max(ahead, 1)).fillna(0.0)
    out["form"] = (base + out["streak"]).fillna(own.reindex(now.index))
    return out.dropna(subset=["form"])


def dnf_rates(starts: pd.Series, dnfs: pd.Series) -> pd.Series:
    """Per-driver retirement chance, shrunk towards the field rate."""
    field = dnfs.sum() / max(starts.sum(), 1)
    k = config.DNF_PRIOR_STARTS
    return (dnfs + k * field) / (starts + k)


def team_now(team: str) -> str:
    """Today's name of a team (config.TEAM_LINEAGE), so last season's figures carry over."""
    return config.TEAM_LINEAGE.get(team, team)


def circuit_offset(at_circuit: pd.Series, season: list[pd.Series], teams: pd.Series) -> pd.Series:
    """
    Team (today's name) -> how much faster (negative) or slower its drivers were at one circuit
    than their own median pace over that season, in %, the mean of the team's drivers, within
    ±CIRCUIT_CLIP. `at_circuit` and `season` are paces of that season; `teams` maps its drivers
    to their team then.
    """
    if at_circuit.empty or not season:
        return pd.Series(dtype=float)
    median = pd.concat(season, axis=1).median(axis=1)
    resid = (at_circuit - median.reindex(at_circuit.index)).dropna()
    team = teams.reindex(resid.index).map(lambda t: team_now(t) if isinstance(t, str) else t)
    by_team = resid.groupby(team).mean()
    return by_team.clip(-config.CIRCUIT_CLIP, config.CIRCUIT_CLIP)


def _blend(base: pd.Series, extra: pd.Series | None, share: float) -> pd.Series:
    """base × (1 - share) + extra × share where a driver has both, else base."""
    if extra is None or extra.empty or not share:
        return base
    both = pd.concat([base.rename("b"), extra.rename("e")], axis=1)
    # Practice is noisy (fuel, programmes, a crash): it moves a driver at most PRACTICE_CLIP from form.
    e = both["b"] + (both["e"] - both["b"]).clip(-config.PRACTICE_CLIP, config.PRACTICE_CLIP)
    return (both["b"] * (1 - share) + e * share).fillna(both["b"]).reindex(base.index)


def expected_pace(code: str, race_form: pd.Series, quali_form: pd.Series,
                  weekend_quali: pd.Series | None = None,
                  practice: dict[str, pd.Series] | None = None) -> pd.Series:
    """
    A driver's expected pace (%, negative = faster) in a session, before the circuit term.
    Qualifying: qualifying form. A race: race form blended with qualifying form, or with the
    pace in this race's own qualifying once that's done. A driver with only one of the two
    gets that one; one with neither is left out. `practice` (practice.weekend_practice: this
    weekend's one-lap and long-run pace) moves qualifying form towards the one-lap pace by
    PRACTICE_QUALI_BLEND and race form towards the long-run pace by PRACTICE_RACE_BLEND.
    """
    if practice:
        quali_form = _blend(quali_form, practice.get("one_lap"), config.PRACTICE_QUALI_BLEND)
        race_form = _blend(race_form, practice.get("long_run"), config.PRACTICE_RACE_BLEND)
    if code in config.QUALI_CODES:
        return quali_form.dropna()
    q = weekend_quali if weekend_quali is not None and not weekend_quali.empty else quali_form
    w = config.RACE_QUALI_BLEND_WEEKEND if q is weekend_quali else config.RACE_QUALI_BLEND
    both = pd.concat([race_form.rename("r"), q.rename("q")], axis=1)
    out = both["r"] * (1 - w) + both["q"] * w
    out = out.fillna(both["r"]).fillna(both["q"])
    return out.dropna()


# ---------------------------------------------------------------------------
# Monte Carlo sessions
# ---------------------------------------------------------------------------
def _finishing_positions(form: np.ndarray, dnf: np.ndarray, n_sims: int,
                         rng: np.random.Generator, sd: float | None = None) -> np.ndarray:
    """
    (n_sims, n_drivers) finishing positions, 1-based, 0 for a retirement. `form` is one
    value per driver, or one row per simulation (drift already added).
    """
    n = dnf.shape[-1]
    sd = config.SESSION_SD["R"] if sd is None else sd
    perf = np.broadcast_to(form, (n_sims, n)) + rng.normal(0.0, sd, (n_sims, n))
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


def penalised_grid(order: pd.Series, penalties: dict[str, int | str] | None) -> pd.Series:
    """
    Qualifying order (driver -> place) with grid penalties applied: N places back, "back" (the
    back of the grid) or "pit" (a pit-lane start, behind everyone). Drivers dropped behind the
    same slot keep their qualifying order. Returns driver -> grid slot, 1 = pole.
    """
    if not penalties:
        return order
    key = order.astype(float).copy()
    for d, p in penalties.items():
        if d not in key.index:
            continue
        key[d] = 2000 + key[d] if p == "pit" else 1000 + key[d] if p == "back" else key[d] + int(p) + 0.5
    return key.rank(method="first").astype(int)


def penalty_places(pace: pd.Series, penalties: dict[str, int | str] | None) -> pd.Series:
    """
    Before qualifying: grid places each driver is expected to lose to a penalty. Their expected
    place is their rank on pace; a drop can't take them past the back ("back"/"pit" = to it).
    """
    out = pd.Series(0.0, index=pace.index)
    if not penalties:
        return out
    rank = pace.rank(method="first")
    n = len(pace)
    for d, p in penalties.items():
        if d in out.index:
            room = n - rank[d] + (1 if p == "pit" else 0)
            out[d] = room if p in ("back", "pit") else min(int(p), room)
    return out


def session_terms(code: str, pace: pd.Series, circuit: pd.Series | None = None,
                  teams: pd.Series | None = None, grid: pd.Series | None = None,
                  penalties: dict[str, int | str] | None = None,
                  pu_risk: pd.Series | None = None, quali_order: pd.Series | None = None,
                  upgrade_items: pd.Series | None = None) -> pd.DataFrame:
    """
    Expected performance (%) for the simulation and what makes it: columns pace, circuit_term,
    grid_term, penalty_places, penalty_term, pu_risk, pu_places, pu_term and mean (their sum).
    `penalties` (announced grid penalties for this round: config.GRID_PENALTIES and the FIA's
    power-unit documents) and `pu_risk` (driver -> chance of a power-unit penalty here that isn't
    announced yet; Grand Prix only) count only while the grid isn't known: once it is, they're in
    it. pu_term is the expected cost, pu_risk × PENALTY_GRID_WEIGHT × pu_places (places lost if it
    happens). Places lost to a penalty count PENALTY_GRID_WEIGHT each: with the grid known, a slot
    behind `quali_order` (driver -> qualifying position) adds the difference over GRID_WEIGHT.
    `upgrade_items` (driver -> performance items their team brings to the round, the FIA's list)
    adds UPGRADE_EFFECT each, up to UPGRADE_MAX_ITEMS (upgrade_term).
    """
    out = pd.DataFrame({"pace": pace.astype(float)})
    out["circuit_term"] = 0.0
    out["grid_term"] = 0.0
    out["penalty_places"] = 0.0
    out["penalty_term"] = 0.0
    out["pu_risk"] = 0.0
    out["pu_places"] = 0.0
    out["pu_term"] = 0.0
    out["upgrade_items"] = 0.0
    out["upgrade_term"] = 0.0
    if upgrade_items is not None and config.UPGRADE_EFFECT:
        out["upgrade_items"] = upgrade_items.reindex(out.index).fillna(0.0).clip(upper=config.UPGRADE_MAX_ITEMS)
        out["upgrade_term"] = config.UPGRADE_EFFECT * out["upgrade_items"]
    if circuit is not None and not circuit.empty and teams is not None:
        team = teams.reindex(out.index).map(lambda t: team_now(t) if isinstance(t, str) else t)
        out["circuit_term"] = config.CIRCUIT_WEIGHT[code] * team.map(circuit).fillna(0.0).to_numpy()
    if code in config.RACE_CODES:
        if grid is not None and grid.notna().any():
            # Grid place, 1 = pole; a missing slot (pit lane, no time) goes behind everyone.
            g = grid.reindex(out.index)
            g = g.fillna(g.max() + 1 if g.notna().any() else len(out))
            out["grid_term"] = config.GRID_WEIGHT[code] * (g - g.mean()).to_numpy()
            if quali_order is not None and not quali_order.empty:
                dropped = (g - quali_order.reindex(out.index)).clip(lower=0).fillna(0.0)
                out["penalty_places"] = dropped.to_numpy()
                out["penalty_term"] = (config.PENALTY_GRID_WEIGHT[code] - config.GRID_WEIGHT[code]) * out["penalty_places"]
        else:
            if penalties:
                out["penalty_places"] = penalty_places(pace, penalties)
                out["penalty_term"] = config.PENALTY_GRID_WEIGHT[code] * out["penalty_places"]
            if pu_risk is not None and code == "R":
                rank, n = pace.rank(method="first"), len(pace)
                risk = pu_risk.reindex(out.index).fillna(0.0).clip(0.0, 1.0)
                risk[out["penalty_places"] > 0] = 0.0       # announced: counted in full above
                out["pu_risk"] = risk
                out["pu_places"] = [pn.expected_drop(rank[d], n) for d in out.index]
                out["pu_term"] = config.PENALTY_GRID_WEIGHT[code] * out["pu_risk"] * out["pu_places"]
    out["mean"] = out[["pace", "circuit_term", "grid_term", "penalty_term", "pu_term", "upgrade_term"]].sum(axis=1)
    return out


def session_mean(code: str, pace: pd.Series, circuit: pd.Series | None = None,
                 teams: pd.Series | None = None, grid: pd.Series | None = None,
                 penalties: dict[str, int | str] | None = None) -> pd.Series:
    """Expected performance (%) for the simulation: pace + circuit + grid (or announced penalty)
    terms. Power-unit risk isn't in it: title_odds draws it race by race."""
    return session_terms(code, pace, circuit, teams, grid, penalties)["mean"]


def forecast_session(code: str, mean: pd.Series, dnf: pd.Series | None = None,
                     rounds_ahead: int = 1, seed: int = 0, n_sims: int | None = None,
                     grid_known: bool = False, terms: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Chances for every driver in `mean` (expected performance, session_mean). Races: win,
    podium, points, retirement, expected position and points (a retirement counts as last).
    Qualifying: pole, front row, top three, Q3, knocked out in Q1, expected position.
    `rounds_ahead` = 1 for the next round: anything further adds horizon drift. With
    `grid_known` (the race's qualifying is in) the spread is SESSION_SD_GRID. `terms`
    (session_terms, whose mean `mean` is) with a power-unit risk: each simulated race draws
    whether the penalty happens.
    """
    drivers = mean.sort_values().index.tolist()
    n = len(drivers)
    rng = np.random.default_rng(seed)
    n_sims = n_sims or config.FORECAST_SIMS
    drift = config.DRIFT_PER_ROUND * np.sqrt(max(rounds_ahead - 1, 0))
    form = mean[drivers].to_numpy()[None, :] + rng.normal(0.0, drift, (n_sims, n))
    if terms is not None and "pu_risk" in terms and terms["pu_risk"].gt(0).any():
        t = terms.reindex(drivers)
        risk = t["pu_risk"].fillna(0.0).to_numpy()
        hit = config.PENALTY_GRID_WEIGHT[code] * t["pu_places"].fillna(0.0).to_numpy()
        # The mean has the expected cost in: swap it for a drawn penalty (all or nothing).
        form = form + hit[None, :] * ((rng.random((n_sims, n)) < risk[None, :]) - risk[None, :])
    if code in config.QUALI_CODES:
        p_dnf = np.full(n, config.QUALI_NO_TIME)
    else:
        base = dnf.reindex(drivers).fillna(dnf.mean()).to_numpy() if dnf is not None and not dnf.empty \
            else np.full(n, 0.08)
        p_dnf = base * (config.SPRINT_DNF_FACTOR if code == "S" else 1.0)
    sd = config.SESSION_SD_GRID[code] if grid_known and code in config.SESSION_SD_GRID else config.SESSION_SD[code]
    pos = _finishing_positions(form, p_dnf, n_sims, rng, sd=sd)
    finished = pos > 0
    pos_or_last = np.where(finished, pos, n)
    out = {"driver": drivers, "pace": mean[drivers].to_numpy()}
    if code in config.QUALI_CODES:
        # No time counts as last: the back of the grid.
        to_q2, to_q3 = _cutoffs(n)
        out.update(p_pole=(pos == 1).mean(axis=0),
                   p_front_row=(finished & (pos <= 2)).mean(axis=0),
                   p_top3=(finished & (pos <= 3)).mean(axis=0),
                   p_q3=(finished & (pos <= to_q3)).mean(axis=0),
                   p_q1_out=(pos_or_last > to_q2).mean(axis=0),
                   exp_pos=pos_or_last.mean(axis=0))
    else:
        table = config.SPRINT_POINTS if code == "S" else config.RACE_POINTS
        out.update(p_win=(pos == 1).mean(axis=0),
                   p_podium=(finished & (pos <= 3)).mean(axis=0),
                   p_points=(finished & (pos <= len(table))).mean(axis=0),
                   p_dnf=(~finished).mean(axis=0),
                   exp_pos=pos_or_last.mean(axis=0),
                   exp_points=_points(pos, table).mean(axis=0))
    return pd.DataFrame(out)


def _cutoffs(n_drivers: int) -> tuple[int, int]:
    """Drivers through to Q2 and to Q3 (data_engine.quali_cutoffs, without importing Streamlit)."""
    return 10 + -(-(n_drivers - 10) // 2), 10


def forecast_race(form: pd.Series, dnf: pd.Series, seed: int = 0) -> pd.DataFrame:
    """Grand Prix chances from form alone (no circuit, grid or horizon terms)."""
    return forecast_session("R", form, dnf, seed=seed).rename(columns={"pace": "form"})


def title_odds(points: pd.Series, teams: pd.Series, sessions: list[tuple],
               dnf: pd.Series, seed: int = 0,
               pu_groups: list[tuple[str, dict[int, float]]] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Simulate the rest of the season. `points` is every driver's current total, `teams` maps
    driver -> team, `sessions` lists every race and sprint still to run, in order, as (code,
    expected performance: session_mean) or (code, mean, pu, round) with `pu` that Grand Prix's
    power-unit risk: index driver, columns h_before / h_after (penalties.penalty_risk: the usage
    model's chance there before and after the driver's first penalty of the simulated season) and
    places (lost if it happens). `pu_groups` (penalty_risk's attrs["groups"]): reported plans, each
    drawn once per simulated season (which round, if any). Drivers missing from a session score
    nothing in it. Returns (drivers, constructors) with the title chance and expected final points.
    """
    drivers = points.index.tolist()
    rng = np.random.default_rng(seed)
    n = config.FORECAST_SIMS
    total = np.tile(points.to_numpy(float), (n, 1))
    # One shift of form per driver per simulated season, shared by all its sessions.
    drift = pd.DataFrame(rng.normal(0.0, config.FORM_DRIFT_SD, (n, len(drivers))), columns=drivers)
    taken = np.zeros((n, len(drivers)), dtype=bool)       # has taken a power-unit penalty already
    # Each reported plan's round in each simulated season (0: it doesn't happen).
    planned = []
    for d, q in pu_groups or []:
        if d not in drivers or not q:
            continue
        rounds = list(q)
        probs = np.array([q[r] for r in rounds], float)
        pick = rng.choice(len(rounds) + 1, size=n, p=np.append(probs, max(0.0, 1.0 - probs.sum())) / max(1.0, probs.sum()))
        planned.append((drivers.index(d), np.append(np.array(rounds), 0)[pick]))
    for code, mean, *rest in sessions:
        racing = [d for d in drivers if d in mean.index]
        if not racing:
            continue
        idx = [drivers.index(d) for d in racing]
        p_dnf = dnf.reindex(racing).fillna(dnf.mean()).to_numpy() * (config.SPRINT_DNF_FACTOR if code == "S" else 1.0)
        f = mean[racing].to_numpy()[None, :] + drift[racing].to_numpy()
        pu = rest[0] if rest else None
        rnd = rest[1] if len(rest) > 1 else None
        if pu is not None and not pu.empty:
            r = pu.reindex(racing)
            h = np.where(taken[:, idx], r["h_after"].fillna(0.0).to_numpy()[None, :],
                         r["h_before"].fillna(0.0).to_numpy()[None, :])
            hit = rng.random(h.shape) < h
            for i, chosen in planned:
                if i in idx:
                    hit[:, idx.index(i)] |= chosen == rnd
            f = f + hit * (config.PENALTY_GRID_WEIGHT[code] * r["places"].fillna(0.0).to_numpy())[None, :]
            taken[:, idx] |= hit
        table = config.SPRINT_POINTS if code == "S" else config.RACE_POINTS
        total[:, idx] += _points(_finishing_positions(f, p_dnf, n, rng, sd=config.SESSION_SD[code]), table)

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


def _candidate_plans(total_laps: int, sprint: bool = False) -> list[list[sim.Stint]]:
    """Every 1-stop and 2-stop plan over the dry compounds using at least two of them; a
    sprint (no two-compound rule) also has every no-stop plan and 1-stops on one compound."""
    lo, step = config.STRATEGY_MIN_STINT, config.STRATEGY_SEARCH_STEP
    plans = []
    if sprint:
        lo = min(lo, max(total_laps // 4, 3))
        plans += [[(c, total_laps)] for c in config.DRY_COMPOUNDS]
        plans += [[(c, n), (c, total_laps - n)] for c in config.DRY_COMPOUNDS
                  for n in range(lo, total_laps - lo + 1)]
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


def plan_name(plan: list[sim.Stint]) -> str:
    stops = len(plan) - 1
    return f"{'No stop' if stops == 0 else f'{stops}-Stop'} · {sim.strategy_label(plan)}"


def best_strategies(base_pace: dict[str, float], deg: dict[str, float], total_laps: int,
                    pit_loss: float, seed: int = 7, track_temp_delta: float = 0.0,
                    weather_scenarios: list[dict | None] | None = None, sprint: bool = False) -> dict:
    """
    Search every plan deterministically and keep the best plan for each set of compounds
    (with no Safety Car or rain the order of the same stints doesn't change the race time,
    so S→H→H and H→H→S are one plan; the soft-first, harder-later order is kept), then Monte Carlo the top STRATEGY_TOP_N (always including the best 1-stop and
    2-stop). The search and the trace are a dry race; the Monte Carlo draws a weather
    scenario per race when given some ("mean_dry" is its mean without them). A sprint
    (`sprint`) also searches no-stop plans, and always carries the best of them.
    Returns {"strategies": [...], "trace": DataFrame(strategy, lap, gap_to_best)}.
    """
    models = sim.build_compound_models(base_pace, deg, track_temp_delta=track_temp_delta)
    best_by_order: dict[str, tuple[float, list[sim.Stint]]] = {}
    for plan in _candidate_plans(total_laps, sprint):
        total, _ = sim._simulate_core(plan, total_laps, models, pit_loss,
                                      config.FUEL_EFFECT_PER_LAP, None, detail=False)
        order = _compound_set(plan)
        if order not in best_by_order or total < best_by_order[order][0] - 1e-9:
            best_by_order[order] = (total, sorted(plan, key=_softest_first))

    ranked = sorted(best_by_order.values(), key=lambda t: t[0])
    # The best plan of each stop count is always carried (a sprint: none, one; a race: one, two).
    must = [next(p for _, p in ranked if len(p) == k) for k in ((1, 2) if sprint else (2, 3))]
    picked = must + [p for _, p in ranked if all(p is not m for m in must)]
    picked = sorted(picked[:config.STRATEGY_TOP_N], key=lambda p: best_by_order[_compound_set(p)][0])

    named = {plan_name(p): p for p in picked}
    kwargs = dict(base_pace=base_pace, degradation_rate=deg, total_laps=total_laps, pit_loss=pit_loss,
                  track_temp_delta=track_temp_delta)
    laps_long, summary = sim.compare_strategies(named, **kwargs)
    _, mc = sim.run_monte_carlo(named, seed=seed, weather_scenarios=weather_scenarios, **kwargs)
    mc = mc.set_index("Strategy")
    if weather_scenarios and any(weather_scenarios):
        mc_dry = sim.run_monte_carlo(named, seed=seed, **kwargs)[1].set_index("Strategy")
    else:
        mc_dry = mc
    strategies = [{
        "name": r["Strategy"], "plan": r["Plan"], "stops": int(r["Stops"]),
        "pit_laps": [int(x) for x in r["Pit laps"]], "total": float(r["Total (s)"]),
        "delta": float(r["Δ to best (s)"]),
        "mean": float(mc.loc[r["Strategy"], "Mean (s)"]),
        "p10": float(mc.loc[r["Strategy"], "P10 (s)"]),
        "p90": float(mc.loc[r["Strategy"], "P90 (s)"]),
        "win_prob": float(mc.loc[r["Strategy"], "Win probability"]),
        "mean_dry": float(mc_dry.loc[r["Strategy"], "Mean (s)"]),
        "stints": [{"compound": c, "laps": int(n)} for c, n in named[r["Strategy"]]],
    } for _, r in summary.iterrows()]
    trace = laps_long[["Strategy", "Lap", "GapToBest"]].rename(
        columns={"Strategy": "strategy", "Lap": "lap", "GapToBest": "gap_to_best"})
    return {"strategies": strategies, "trace": trace}
