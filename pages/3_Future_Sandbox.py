"""
pages/3_Future_Sandbox.py — Strategic planning sandbox.

Sidebar control panel injects scenario modifiers into the simulator:
    Team Upgrade Impact (%)      -5 … +5   → deg slope × (1 − pct/100)
    Track Temperature Delta (°C) -15 … +15 → per-compound thermal deg & earlier cliff
    Rain Entry Lap               None or 1 … race distance → forced switch to Inter/Wet

Baseline pace/deg comes either from generic compound presets or is calibrated
from the session loaded on the other pages (field-median fits per compound).

Outputs: deterministic side-by-side comparison (time-behind-best trace and lap
time trace) plus a Monte Carlo run with lap noise and random Safety Cars.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # allow `import config`

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from modules import analytics as an
from modules import data_engine as de
from modules import simulator as sim

MAX_STRATEGIES = len(config.STRATEGY_COLORS)


@st.cache_data(show_spinner=False, max_entries=6)
def calibrated_baseline(year: int, event: str, stype: str) -> dict:
    """Field-median base pace & deg per dry compound from a real session."""
    info = de.get_session_info(year, event, stype)
    deg = an.calculate_tyre_degradation(de.get_cleaned_laps(year, event, stype))
    field = {c: m for c, m in an.field_compound_model(deg).items() if c in config.DRY_COMPOUNDS}
    return {"info": info, "field": field}


@st.cache_data(show_spinner=False, max_entries=32)
def cached_monte_carlo(strategies: dict, sim_kwargs: dict, n_sims: int, sc_prob: float, noise: float):
    return sim.run_monte_carlo(strategies, n_sims=n_sims, sc_probability=sc_prob,
                               lap_noise_sd=noise, **sim_kwargs)


# ---------------------------------------------------------------------------
# Sidebar — baseline
# ---------------------------------------------------------------------------
st.title("🧪 Future Sandbox")
active = st.session_state.get(de.ACTIVE_SESSION_KEY)

with st.sidebar:
    st.markdown("### 📐 Baseline")
    sources = ["Generic compound presets"] + (["Calibrate from loaded session"] if active else [])
    source = st.radio("Pace & degradation source", sources,
                      help="Load a session on Race Recap or Live Race Tracker to enable calibration.")

calib = None
if source.startswith("Calibrate"):
    try:
        with st.spinner("Calibrating from session…"):
            calib = calibrated_baseline(*active)
    except Exception as exc:  # noqa: BLE001 — fall back to presets, never crash
        st.warning(f"Calibration failed ({exc}); using generic presets.")

with st.sidebar:
    if calib:
        info = calib["info"]
        circuit_default, _ = config.get_pit_loss(info["location"], info["event_name"])
        total_laps = st.number_input("Race distance (laps)", 10, 90, int(info["total_laps"]))
        base_pace = {c: m["base_pace"] for c, m in calib["field"].items()}
        deg_rate = {c: m["deg_rate"] for c, m in calib["field"].items()}
        st.caption(f"Calibrated on {info['year']} {info['event_name']}.")
    else:
        circuits = sorted(config.TRACK_PIT_LOSS)
        circuit = st.selectbox("Circuit", circuits, index=circuits.index("Silverstone"))
        circuit_default = config.TRACK_PIT_LOSS[circuit]
        total_laps = st.number_input("Race distance (laps)", 10, 90, 52)
        base_pace = st.number_input("Fresh-Soft lap time, full fuel (s)", 60.0, 130.0, 91.0, 0.1)
        deg_rate = st.number_input("Medium deg rate (s/lap)", 0.0, 0.5,
                                   config.COMPOUND_PRESETS["MEDIUM"]["deg_rate"], 0.005, format="%.3f",
                                   help="Other compounds scale from the preset ratios.")
    pit_loss = st.number_input("Pit loss (s)", 10.0, 40.0, float(circuit_default), 0.5)

    # ---------------- Scenario controls (spec) ----------------
    st.markdown("### 🎛️ Scenario controls")
    upgrade = st.slider("Team Upgrade Impact (%)", -5.0, 5.0, 0.0, 0.5,
                        help="+2 % scales every degradation slope by 0.98; negative = worse.")
    temp_delta = st.slider("Track Temperature Delta (°C)", -15, 15, 0, 1,
                           help="Hotter track → higher thermal deg and an earlier cliff, softs most.")
    rain_choice = st.selectbox("Rain Entry Lap", ["None"] + list(range(1, int(total_laps) + 1)))
    rain_lap = None if rain_choice == "None" else int(rain_choice)
    intensity = st.radio("Rain intensity", list(config.RAIN_PROFILES), horizontal=True,
                         disabled=rain_lap is None)

    st.markdown("### 🎲 Monte Carlo")
    n_sims = st.select_slider("Simulated races", [100, 250, 500, 1000, 2000], value=config.MC_DEFAULT_SIMS)
    sc_prob = st.slider("Safety Car probability", 0.0, 1.0, config.MC_SC_PROBABILITY, 0.05)
    noise = st.slider("Lap-time noise σ (s)", 0.0, 1.0, config.MC_LAP_NOISE_SD, 0.05)

total_laps = int(total_laps)
weather = {"rain_lap": rain_lap, "intensity": intensity} if rain_lap else None

# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------
c1, c2 = st.columns([3, 2])
with c1:
    picked = st.multiselect("Strategies to compare", list(sim.STRATEGY_PRESETS),
                            default=list(sim.STRATEGY_PRESETS))
with c2:
    custom_txt = st.text_input("Custom strategy (optional)", placeholder="e.g. S-15, H  or  M-20, H-18, S",
                               help="S/M/H/I/W with lap counts; the last stint may omit its length.")

strategies: dict[str, list[sim.Stint]] = {n: sim.preset_strategy(n, total_laps) for n in picked}
if custom_txt.strip():
    try:
        custom = sim.parse_strategy(custom_txt, total_laps)
        strategies[f"Custom · {sim.strategy_label(custom)}"] = custom
    except ValueError as exc:
        st.warning(f"Custom strategy ignored: {exc}")
if not strategies:
    st.info("Select at least one strategy.")
    st.stop()
if len(strategies) > MAX_STRATEGIES:
    st.warning(f"Showing the first {MAX_STRATEGIES} strategies to keep the charts readable.")
    strategies = dict(list(strategies.items())[:MAX_STRATEGIES])

# Colour follows the strategy (fixed slot by preset order), never its ranking.
slot_names = list(sim.STRATEGY_PRESETS) + [n for n in strategies if n not in sim.STRATEGY_PRESETS]
colors = {n: config.STRATEGY_COLORS[slot_names.index(n) % MAX_STRATEGIES] for n in strategies}

sim_kwargs = dict(base_pace=base_pace, degradation_rate=deg_rate, total_laps=total_laps,
                  upgrade_modifier=upgrade, weather_modifier=weather, pit_loss=pit_loss,
                  track_temp_delta=float(temp_delta))
laps_long, summary = sim.compare_strategies(strategies, **sim_kwargs)

# ---------------------------------------------------------------------------
# Headline cards
# ---------------------------------------------------------------------------
st.markdown("##### Deterministic outcome")
cards = st.columns(len(summary))
for col, (_, row) in zip(cards, summary.iterrows()):
    with col, st.container(border=True):
        st.markdown(f"<span style='color:{colors[row['Strategy']]}'>●</span> **{row['Strategy']}**",
                    unsafe_allow_html=True)
        mins, secs = divmod(row["Total (s)"], 60)
        st.metric(row["Plan"], f"{int(mins)}:{secs:06.3f}",
                  delta=None if row["Δ to best (s)"] == 0 else f"+{row['Δ to best (s)']:.2f} s",
                  delta_color="inverse")
        tag = "🏆 Fastest plan · " if row["Δ to best (s)"] == 0 else ""
        st.caption(f"{tag}{row['Stops']} stop(s) · pit lap {', '.join(map(str, row['Pit laps'])) or '—'}")


def _rain_marker(fig: go.Figure) -> None:
    if rain_lap:
        fig.add_vrect(x0=rain_lap - 0.5, x1=total_laps + 0.5, fillcolor="rgba(42,120,214,0.08)",
                      line_width=0, layer="below", annotation_text=f"🌧️ {intensity} from lap {rain_lap}",
                      annotation_position="top left", annotation_font_size=11)


def build_gap_chart() -> go.Figure:
    """Cumulative time behind the fastest plan — the classic strategist trace."""
    fig = go.Figure()
    for name in strategies:
        d = laps_long[laps_long["Strategy"] == name]
        fig.add_trace(go.Scatter(
            x=d["Lap"], y=d["GapToBest"], mode="lines", name=name,
            line=dict(color=colors[name], width=2),
            customdata=d[["Compound", "TyreAge"]].to_numpy(),
            hovertemplate=f"<b>{name}</b><br>Lap %{{x}} · %{{y:+.2f}} s<br>"
                          "%{customdata[0]} age %{customdata[1]}<extra></extra>",
        ))
        pits = d[d["PitIn"]]
        fig.add_trace(go.Scatter(
            x=pits["Lap"], y=pits["GapToBest"], mode="markers", showlegend=False,
            marker=dict(symbol="triangle-down", size=10, color=colors[name], line=dict(width=1.5, color="white")),
            hovertemplate=f"{name} pits · lap %{{x}}<extra></extra>",
        ))
    _rain_marker(fig)
    fig.add_hline(y=0, line=dict(color="rgba(128,128,128,0.5)", width=1))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                      xaxis=dict(title="Lap", gridcolor="rgba(128,128,128,0.12)"),
                      yaxis=dict(title="Time behind fastest plan (s)", gridcolor="rgba(128,128,128,0.12)"),
                      legend=dict(orientation="h", y=1.1, x=0))
    return fig


def build_laptime_chart() -> go.Figure:
    """Modelled lap time per lap; in-laps (with pit loss) are clipped for readability."""
    fig = go.Figure()
    # Blank (not drop) the in-laps so each line breaks at a stop instead of joining across it.
    racing = laps_long.assign(LapTime=laps_long["LapTime"].where(~laps_long["PitIn"]))
    for name in strategies:
        d = racing[racing["Strategy"] == name]
        fig.add_trace(go.Scatter(
            x=d["Lap"], y=d["LapTime"], mode="lines", name=name, line=dict(color=colors[name], width=2),
            connectgaps=False, customdata=d[["Compound", "TyreAge"]].to_numpy(),
            hovertemplate=f"<b>{name}</b> · lap %{{x}}<br>%{{y:.3f}} s · %{{customdata[0]}} "
                          "age %{customdata[1]}<extra></extra>",
        ))
    _rain_marker(fig)
    lo, hi = racing["LapTime"].dropna().quantile([0.0, 0.98])
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                      xaxis=dict(title="Lap", gridcolor="rgba(128,128,128,0.12)"),
                      yaxis=dict(title="Lap time (s)", range=[lo - 0.5, hi + 0.8],
                                 gridcolor="rgba(128,128,128,0.12)"),
                      legend=dict(orientation="h", y=1.1, x=0))
    return fig


g1, g2 = st.columns(2)
with g1:
    st.markdown("**Time behind fastest plan**")
    st.plotly_chart(build_gap_chart(), width="stretch", theme="streamlit")
with g2:
    st.markdown("**Modelled lap time** (pit in-laps omitted)")
    st.plotly_chart(build_laptime_chart(), width="stretch", theme="streamlit")

with st.expander("Compound model in use (after modifiers)"):
    models = sim.build_compound_models(base_pace, deg_rate, upgrade, float(temp_delta), weather)
    mdf = pd.DataFrame(models).T.rename(columns={"base": "Base pace (s)", "deg": "Deg (s/lap)",
                                                 "cliff_age": "Cliff age (laps)",
                                                 "cliff_rate": "Post-cliff extra (s/lap)"})
    if calib:
        mdf["Calibrated from"] = [f"{calib['field'][c]['n_drivers']} drivers" if c in calib["field"]
                                  else "preset" for c in mdf.index]
    st.dataframe(mdf.style.format(precision=3), width="stretch")
    st.caption(f"Fuel effect {config.FUEL_EFFECT_PER_LAP} s/lap. Upgrade factor "
               f"×{1 - upgrade / 100:.3f} on deg. Rain forces a stop onto the "
               "wet compound at the end of the rain lap and cancels remaining dry stops.")

# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------
st.markdown("##### Stochastic outcome (Monte Carlo)")
with st.spinner(f"Simulating {n_sims} races…"):
    mc_results, mc_summary = cached_monte_carlo(strategies, sim_kwargs, n_sims, sc_prob, noise)

best_mean = mc_summary["Mean (s)"].min()
mc_results["Δ vs best mean (s)"] = mc_results["Total (s)"] - best_mean
order = mc_summary["Strategy"].tolist()

b1, b2 = st.columns([3, 2])
with b1:
    fig = go.Figure()
    for name in order:
        d = mc_results[mc_results["Strategy"] == name]
        fig.add_trace(go.Box(x=d["Δ vs best mean (s)"], name=name, marker_color=colors[name],
                             boxpoints=False, line=dict(width=1.5), orientation="h"))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
                      xaxis=dict(title="Race time vs best mean (s)", gridcolor="rgba(128,128,128,0.12)"),
                      yaxis=dict(categoryorder="array", categoryarray=order[::-1]))
    st.plotly_chart(fig, width="stretch", theme="streamlit")
with b2:
    fig = go.Figure(go.Bar(
        y=order, x=mc_summary["Win probability"] * 100, orientation="h",
        marker=dict(color=[colors[n] for n in order]),
        text=[f"{p:.0%}" for p in mc_summary["Win probability"]], textposition="outside",
        hovertemplate="%{y}: %{x:.1f}% of races fastest<extra></extra>",
    ))
    fig.update_layout(height=300, margin=dict(l=10, r=30, t=10, b=10),
                      xaxis=dict(title="Fastest in % of simulated races", range=[0, 110],
                                 gridcolor="rgba(128,128,128,0.12)"),
                      yaxis=dict(categoryorder="array", categoryarray=order[::-1]))
    st.plotly_chart(fig, width="stretch", theme="streamlit")

st.dataframe(mc_summary, hide_index=True, width="stretch", column_config={
    **{c: st.column_config.NumberColumn(format="%.2f") for c in ["Mean (s)", "P10 (s)", "P90 (s)", "95% CI ± (s)"]},
    "Win probability": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f"),
})
st.caption(f"{n_sims} races · lap noise σ={noise:.2f} s · SC probability {sc_prob:.0%} "
           f"(3–5 laps, pit loss ×{config.SC_PIT_LOSS_FACTOR}, stops due within "
           f"{config.MC_SC_PIT_WINDOW} laps are brought forward). Every strategy sees the same "
           "noise and SC draw in each simulated race.")
