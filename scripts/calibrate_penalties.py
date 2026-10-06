"""
Fit config.py's "Power-unit penalties" constants on every round since 2022.

    .venv\\Scripts\\python scripts\\calibrate_penalties.py            # 2022 to this season
    .venv\\Scripts\\python scripts\\calibrate_penalties.py 2023 2026

For every round and car it takes the elements used before the round (the FIA's Technical Delegate
documents, modules/penalties.py) and whether an over-allocation element was fitted there, then:

  * checks those against f1penalties.com's power-unit penalties (matched by name);
  * the circuit factors: how often teams took a power-unit penalty at each circuit (f1penalties,
    2020 on), against the average race;
  * fits the hazard (logistic: deficit, over, left, log circuit factor), scored leave one season
    out against a constant and a circuit-only model (log loss per round and car);
  * the sizes of the drops (PU_DROP_SHARE).

Prints the values to paste into config.py.
"""
import datetime as dt
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

import fastf1  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from modules import penalties as pn  # noqa: E402

FEATURES = ["deficit", "over", "left", "circuit"]


def schedule(year: int) -> pd.DataFrame:
    s = fastf1.get_event_schedule(year, include_testing=False)
    s = s[s["RoundNumber"] > 0]
    return pd.DataFrame({"year": year, "round": s["RoundNumber"].astype(int), "location": s["Location"],
                         "circuit": s["Location"].map(pn.circuit_key),
                         "race": pd.to_datetime(s["Session5DateUtc"]).dt.tz_localize("UTC")})


def fit_logistic(X: np.ndarray, y: np.ndarray, ridge: float = 1e-2, iters: int = 50) -> np.ndarray:
    """Logistic regression by Newton's method (a small ridge keeps it stable); X has a 1s column."""
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + ridge * np.r_[0, w[1:]]
        H = (X * (p * (1 - p))[:, None]).T @ X + ridge * np.diag(np.r_[0, np.ones(len(w) - 1)])
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def design(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    parts = [np.ones(len(df))]
    for c in cols:
        parts.append(np.log(df["factor"].to_numpy()) if c == "circuit" else df[c].to_numpy(float))
    return np.column_stack(parts)


def main() -> None:
    years = [int(a) for a in sys.argv[1:]] or list(range(pn.FIA_FIRST_YEAR, dt.date.today().year + 1))
    years = list(range(years[0], years[-1] + 1)) if len(years) == 2 else years
    fastf1.Cache.enable_cache(str(config.FASTF1_CACHE_DIR))
    fastf1.set_log_level("ERROR")
    now = pd.Timestamp.now(tz="UTC")

    sched = pd.concat([schedule(y) for y in range(2020, max(years) + 1)], ignore_index=True)
    hosted = sched[sched["race"] < now]
    decisions = pn.stewards_decisions()
    events = pn.pu_penalty_events(decisions).merge(sched[["year", "round", "circuit"]], on=["year", "round"], how="left")
    print(f"f1penalties: {len(decisions)} decisions, {len(events)} power-unit penalties (driver and race), "
          f"{int(events['circuit'].isna().sum())} unmatched to a round")

    tables = []
    for y in years:
        recs = pn.pu_season(y)
        n = int(sched[sched["year"] == y]["round"].max())
        t = pn.usage_table(y, recs, n)
        t = t[t["round"].isin(hosted.loc[hosted["year"] == y, "round"])]
        print(f"{y}: {len(recs)} rounds of FIA documents, limits {pn.season_limits(y, recs)}, "
              f"{int(t['changed'].sum())} over-allocation changes")
        tables.append(t.merge(sched[["year", "round", "circuit"]], on=["year", "round"], how="left"))
    data = pd.concat(tables, ignore_index=True)

    # ---- FIA usage against f1penalties ----
    data["key"] = data["who"].map(pn.name_key)
    ev = events[events["year"].isin(years)]
    got = data[data["changed"]]

    def matched(row, pool):
        same = pool[(pool["year"] == row["year"]) & (pool["round"] == row["round"])]
        return any(row["key"].endswith(k[-6:]) for k in same["name_key"])
    fia_hit = got.apply(lambda r: matched(r, ev), axis=1).mean() if len(got) else float("nan")
    ev_keys = ev.assign(key=ev["name_key"])
    pen_hit = ev_keys.apply(lambda r: any(k.endswith(r["key"][-6:]) for k in
                                          got[(got["year"] == r["year"]) & (got["round"] == r["round"])]["key"]),
                            axis=1).mean() if len(ev) else float("nan")
    print(f"Over-allocation changes in the FIA tables that f1penalties lists: {fia_hit:.0%}; "
          f"f1penalties' power-unit penalties found in the FIA tables: {pen_hit:.0%}")
    missing = ev_keys[~ev_keys.apply(lambda r: any(k.endswith(r["key"][-6:]) for k in
                                                   got[(got["year"] == r["year"]) & (got["round"] == r["round"])]["key"]), axis=1)]
    if len(missing):
        print("  in f1penalties, not the FIA tables:", ", ".join(f"{a} r{b} {c}" for a, b, c in
                                                            missing[["year", "round", "driver"]].itertuples(index=False)))

    # ---- drop sizes ----
    sizes = ev["grid"].map(lambda g: "back" if g in ("back", "pit") else (15 if g >= 15 else 10 if g >= 10 else 5)
                           if g is not None else None).dropna()
    share = sizes.value_counts(normalize=True)
    print("PU_DROP_SHARE = {" + ", ".join(f'{k!r}: {share.get(k, 0):.2f}' for k in ("back", 15, 10, 5)) + "}"
          f"   ({len(sizes)} penalties, {min(years)} on)")

    # ---- the hazard, leave one season out ----
    def factors_without(y):
        e, h = events[events["year"] != y], hosted[hosted["year"] != y]
        return pn.circuit_factors(e.dropna(subset=["circuit"]), h)
    data["factor"] = 1.0
    oos = {"constant": [], "circuit": [], "model": []}
    ys = []
    for y in years:
        f = factors_without(y)
        test = data["year"] == y
        data.loc[test, "factor"] = data.loc[test, "circuit"].map(f).fillna(1.0)
    for y in years:
        train, test = data[data["year"] != y], data[data["year"] == y]
        if test.empty or train["changed"].sum() == 0:
            continue
        yt = test["changed"].to_numpy(float)
        ys.append(yt)
        oos["constant"].append(np.full(len(test), train["changed"].mean()))
        for name, cols in (("circuit", ["circuit"]), ("model", FEATURES)):
            w = fit_logistic(design(train, cols), train["changed"].to_numpy(float))
            oos[name].append(1 / (1 + np.exp(-design(test, cols) @ w)))
        print(f"  {y}: log loss constant {log_loss(yt, oos['constant'][-1]):.4f}, circuit "
              f"{log_loss(yt, oos['circuit'][-1]):.4f}, model {log_loss(yt, oos['model'][-1]):.4f} "
              f"({int(yt.sum())} changes in {len(yt)} car-rounds)")
    y_all = np.concatenate(ys)
    for name in oos:
        print(f"Leave one season out, {name}: log loss {log_loss(y_all, np.concatenate(oos[name])):.4f}")

    # The model's chance of at least one change in the rest of a season, against what happened,
    # from the state at each round (the question the site answers).
    w = fit_logistic(design(data, FEATURES), data["changed"].to_numpy(float))
    print("PU_HAZARD = {" + ", ".join(f'"{k}": {v:.3f}' for k, v in zip(["intercept"] + FEATURES, w)) + "}")
    data["h"] = 1 / (1 + np.exp(-design(data, FEATURES) @ w))
    rows = []
    for (y, car), g in data.sort_values("round").groupby(["year", "car"]):
        h, c = g["h"].to_numpy(), g["changed"].to_numpy()
        for i in range(len(g)):
            rows.append({"p": 1 - np.prod(1 - h[i:]), "any": c[i:].any()})
    b = pd.DataFrame(rows)
    b["bin"] = pd.cut(b["p"], [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0], include_lowest=True)
    print("Chance of a penalty in the rest of the season (in-sample), against how often one came:")
    print(b.groupby("bin", observed=True).agg(n=("any", "size"), predicted=("p", "mean"), actual=("any", "mean")).round(3).to_string())


if __name__ == "__main__":
    main()
