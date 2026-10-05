"""
pages/3_Future_Sandbox.py — Strategic planning sandbox.

Sidebar control panel injects scenario modifiers into the simulator:
    Team Upgrade Impact (%)      -5 … +5   → deg slope × (1 − pct/100)
    Track Temperature Delta (°C) -15 … +15 → per-compound thermal deg & earlier cliff
                                              (default: forecast track temp − reference)
    Rain Entry Lap / Dry Lap     a what-if rain spell for the deterministic run
    Rain risk (Monte Carlo)      none, the race's forecast or climate (Open-Meteo, one
                                 scenario per ensemble member or past day), or set by hand

Baseline pace/deg comes either from generic compound presets or is calibrated
from the session loaded on the other pages (field-median fits per compound).

Outputs: deterministic side-by-side comparison (time-behind-best trace and lap
time trace) plus a Monte Carlo run with lap noise and random Safety Cars.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # allow `import config`

import fastf1
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from modules import analytics as an
from modules import data_engine as de
from modules import simulator as sim
from modules import weather as wx

MAX_STRATEGIES = len(config.STRATEGY_COLORS)


@st.cache_data(show_spinner=False, max_entries=6)
def calibrated_baseline(year: int, event: str, stype: str) -> dict:
    """Field-median base pace & deg per dry compound from a real session."""
    info = de.get_session_info(year, event, stype)
    deg = an.calculate_tyre_degradation(de.get_cleaned_laps(year, event, stype))
    field = {c: m for c, m in an.field_compound_model(deg).items() if c in config.DRY_COMPOUNDS}
    return {"info": info, "field": field}


@st.cache_data(show_spinner=False, max_entries=32)
def cached_monte_carlo(strategies: dict, sim_kwargs: dict, n_sims: int, sc_prob: float, noise: float,
                       scenarios: list | None):
    return sim.run_monte_carlo(strategies, n_sims=n_sims, sc_probability=sc_prob,
                               lap_noise_sd=noise, weather_scenarios=scenarios, **sim_kwargs)


@st.cache_data(show_spinner=False, max_entries=16, ttl=3600)
def session_track_temp(year: int, event: str, stype: str) -> float | None:
    """Mean track temperature of a session (the calibration's reference)."""
    lw = de.get_lap_weather(year, event, stype)
    return float(lw["TrackTemp"].mean()) if lw["TrackTemp"].notna().any() else None


@st.cache_data(show_spinner=False, max_entries=16, ttl=3600)
def next_race_at(circuit: str, code: str = "R") -> pd.Timestamp | None:
    """This season's (or next season's) race ("R") or sprint ("S") start at a circuit, if it's still to come."""
    now = pd.Timestamp.now(tz="UTC")
    for year in (now.year, now.year + 1):
        try:
            sched = fastf1.get_event_schedule(year, include_testing=False)
        except Exception:  # noqa: BLE001 — offline
            return None
        for _, ev in sched.iterrows():
            if config.get_pit_loss(ev["Location"], ev["EventName"])[1] != circuit:
                continue
            for i in range(1, 6):
                if ev[f"Session{i}"] == config.SESSION_NAMES[code] and pd.notna(ev[f"Session{i}DateUtc"]):
                    start = pd.Timestamp(ev[f"Session{i}DateUtc"]).tz_localize("UTC")
                    if start > now:
                        return start
    return None


@st.cache_data(show_spinner=False, max_entries=16, ttl=1800)
def cached_outlook(names: tuple, start_utc: pd.Timestamp, total_laps: int, reference_start, code: str = "R"):
    try:
        return wx.outlook(names, start_utc, total_laps, code, reference_start=reference_start), None
    except wx.WeatherUnavailable as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Sidebar — baseline
# ---------------------------------------------------------------------------
st.title("🧪 Future Sandbox")
active = st.session_state.get(de.ACTIVE_SESSION_KEY)
if active and active[2] in config.QUALI_CODES:
    # Tyre wear comes from a race: a qualifying pick calibrates on the race it set the grid for.
    active = (active[0], active[1], config.RACE_OF_QUALI[active[2]])

with st.sidebar:
    st.markdown("### 📐 Baseline")
    race_type = st.radio("Race type", ["R", "S"], horizontal=True, format_func=de.session_label,
                         index=1 if active and active[2] == "S" else 0,
                         help="A Sprint is about 100 km (a third of a Grand Prix) with no compulsory stop, so "
                              "its presets are no-stop plans; the distance follows.")
    sprint = race_type == "S"
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
        circuit_default, circuit = config.get_pit_loss(info["location"], info["event_name"])
        circuit_names = (info["location"], info["event_name"])
        # The loaded session's distance, scaled when it's the other kind of race.
        gp_laps = int(info["total_laps"]) if active[2] == "R" else int(round(info["total_laps"] * 305 / 100))
        own = int(info["total_laps"]) if active[2] == race_type else (sim.sprint_laps(gp_laps) if sprint else gp_laps)
        total_laps = st.number_input("Race distance (laps)", 5, 90, own)
        base_pace = {c: m["base_pace"] for c, m in calib["field"].items()}
        deg_rate = {c: m["deg_rate"] for c, m in calib["field"].items()}
        st.caption(f"Calibrated on {info['year']} {info['event_name']}.")
    else:
        circuits = sorted(config.TRACK_PIT_LOSS)
        circuit = st.selectbox("Circuit", circuits, index=circuits.index("Silverstone"))
        circuit_default = config.TRACK_PIT_LOSS[circuit]
        circuit_names = (circuit,)
        total_laps = st.number_input("Race distance (laps)", 5, 90, sim.sprint_laps(52) if sprint else 52)
        base_pace = st.number_input("Fresh-Soft lap time, full fuel (s)", 60.0, 130.0, 91.0, 0.1)
        deg_rate = st.number_input("Medium deg rate (s/lap)", 0.0, 0.5,
                                   config.COMPOUND_PRESETS["MEDIUM"]["deg_rate"], 0.005, format="%.3f",
                                   help="Other compounds scale from the preset ratios.")
    pit_loss = st.number_input("Pit loss (s)", 10.0, 40.0, float(circuit_default), 0.5)

    # ---------------- Scenario controls (spec) ----------------
    st.markdown("### 🎛️ Scenario controls")
    upgrade = st.slider("Team Upgrade Impact (%)", -5.0, 5.0, 0.0, 0.5,
                        help="+2 % scales every degradation slope by 0.98; negative = worse.")

    # ---------------- Weather ----------------
    st.markdown("### 🌦️ Weather")
    risk_src = st.radio("Rain risk in the Monte Carlo", ["Forecast / climate", "Set by hand", "None"],
                        help="Forecast: Open-Meteo's ensemble for the race (up to 16 days ahead), one "
                             "scenario per member; further out, the climate: the race window on the same "
                             "dates in the last 10 years. Each simulated race draws one scenario.")
    outlook, outlook_err, race_start = None, None, None
    if risk_src == "Forecast / climate":
        default_start = next_race_at(circuit, race_type) if circuit else None
        if default_start is None and calib and calib["info"].get("start_utc") is not None:
            default_start = calib["info"]["start_utc"]
        default_start = default_start or (pd.Timestamp.now(tz="UTC").normalize() + pd.Timedelta(days=7, hours=13))
        d = st.date_input("Race date", default_start.date())
        h = st.time_input("Start (UTC)", default_start.time().replace(second=0, microsecond=0))
        race_start = pd.Timestamp.combine(d, h).tz_localize("UTC")
        # Calibrated: the temperature change against the calibration race, estimated the same
        # way from the same source for both (modules/weather.py).
        ref_start = calib["info"].get("start_utc") if calib else None
        with st.spinner("Fetching the weather outlook…"):
            outlook, outlook_err = cached_outlook(circuit_names, race_start, int(total_laps), ref_start, race_type)
        if outlook_err:
            st.warning(f"No weather outlook ({outlook_err}).")
    elif risk_src == "Set by hand":
        chance = st.slider("Chance of rain", 0.0, 1.0, 0.3, 0.05)
        window = st.slider("Rain starts between laps", 1, int(total_laps), (1, int(total_laps)))
        to_flag = st.toggle("Rain lasts to the flag", value=False)
        duration = None if to_flag else st.slider("Rain lasts (laps)", 1, int(total_laps), (5, 20))
        heavy_share = st.slider("Share of it heavy", 0.0, 1.0, 0.2, 0.05)

    # Track temperature: the forecast's against what the baseline stands for.
    ref_temp = (session_track_temp(*active) if calib else None) or config.REFERENCE_TRACK_TEMP
    forecast_delta = None
    if outlook and outlook.get("track") is not None:
        forecast_delta = outlook.get("temp_delta") if calib else outlook["track"] - ref_temp
    temp_default = int(round(max(-15, min(15, forecast_delta)))) if forecast_delta is not None else 0
    temp_delta = st.slider("Track Temperature Delta (°C)", -15, 15, temp_default, 1,
                           help="Hotter track → higher thermal deg and an earlier cliff, softs most. "
                                f"Against {ref_temp:.0f} °C ({'the calibration session' if calib else 'the presets'}); "
                                "set from the forecast when there is one.")

    st.caption("What-if rain spell (deterministic run)")
    rain_choice = st.selectbox("Rain Entry Lap", ["None"] + list(range(1, int(total_laps) + 1)))
    rain_lap = None if rain_choice == "None" else int(rain_choice)
    dry_choice = st.selectbox("Track dry again from lap", ["Never (wet to the flag)"]
                              + list(range((rain_lap or 1) + 1, int(total_laps) + 1)), disabled=rain_lap is None)
    dry_lap = None if rain_lap is None or isinstance(dry_choice, str) else int(dry_choice)
    intensity = st.radio("Rain intensity", list(config.RAIN_PROFILES), horizontal=True,
                         disabled=rain_lap is None)

    st.markdown("### 🎲 Monte Carlo")
    n_sims = st.select_slider("Simulated races", [100, 250, 500, 1000, 2000], value=config.MC_DEFAULT_SIMS)
    sc_prob = st.slider("Safety Car probability", 0.0, 1.0, config.MC_SC_PROBABILITY, 0.05)
    noise = st.slider("Lap-time noise σ (s)", 0.0, 1.0, config.MC_LAP_NOISE_SD, 0.05)

total_laps = int(total_laps)
weather = {"rain_lap": rain_lap, "dry_lap": dry_lap, "intensity": intensity} if rain_lap else None
scenarios = None
if outlook:
    scenarios = outlook["scenarios"]
elif risk_src == "Set by hand":
    scenarios = wx.manual_scenarios(chance, window, duration, heavy_share, total_laps)

# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------
presets = sim.SPRINT_PRESETS if sprint else sim.STRATEGY_PRESETS
st.markdown(f"{de.session_badge(race_type)} · {total_laps} laps"
            + (" · no compulsory stop, sprint points to the top eight" if sprint else ""))
c1, c2 = st.columns([3, 2])
with c1:
    picked = st.multiselect("Strategies to compare", list(presets), default=list(presets), key=f"presets_{race_type}")
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
slot_names = list(presets) + [n for n in strategies if n not in presets]
colors = {n: config.STRATEGY_COLORS[slot_names.index(n) % MAX_STRATEGIES] for n in strategies}

sim_kwargs = dict(base_pace=base_pace, degradation_rate=deg_rate, total_laps=total_laps,
                  upgrade_modifier=upgrade, weather_modifier=weather, pit_loss=pit_loss,
                  track_temp_delta=float(temp_delta))
laps_long, summary = sim.compare_strategies(strategies, **sim_kwargs)

# ---------------------------------------------------------------------------
# Weather outlook
# ---------------------------------------------------------------------------
if outlook:
    with st.container(border=True):
        src = (f"{outlook['model'].split('_')[0].upper()} ensemble, {outlook['samples']} members"
               if outlook["source"] == "ensemble"
               else f"climate: {outlook['samples']} days around {race_start:%d %b} in the last {config.CLIMATE_YEARS} years")
        st.markdown(f"**🌦️ Weather outlook** · {race_start:%a %d %b %Y %H:%M} UTC · {src}")
        o = st.columns(4)
        o[0].metric("Chance of rain", f"{outlook['rain_chance']:.0%}")
        o[1].metric("Heavy rain", f"{outlook['heavy_chance']:.0%}")
        o[2].metric("Typical start", f"lap {outlook['rain_lap_median']:.0f}" if outlook["rain_lap_median"] else "—")
        expected = ref_temp + forecast_delta if calib and forecast_delta is not None else outlook.get("track")
        o[3].metric("Track (expected)", f"{expected:.0f} °C" if expected is not None else "—",
                    delta=f"{forecast_delta:+.1f} °C vs {'calibration race' if calib else 'presets'}"
                    if forecast_delta is not None else None, delta_color="off",
                    help="Air temperature plus sunshine; calibrated, the change against the "
                         "calibration race (estimated the same way) added to its sensor reading.")
        st.caption(f"Rain = {config.RAIN_WET_MM_H} mm/h or more in the hour a lap falls in "
                   f"(heavy from {config.RAIN_HEAVY_MM_H}); first spell only. {wx.CREDIT}.")
elif scenarios:
    s_ = wx.summarise(scenarios)
    st.caption(f"🌦️ Rain risk by hand: {s_['rain_chance']:.0%} of simulated races see rain, "
               f"{s_['heavy_chance']:.0%} heavy.")

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
        end = (dry_lap or total_laps + 1) - 0.5
        label = f"🌧️ {intensity} laps {rain_lap}–{dry_lap - 1}" if dry_lap else f"🌧️ {intensity} from lap {rain_lap}"
        fig.add_vrect(x0=rain_lap - 0.5, x1=end, fillcolor="rgba(42,120,214,0.08)",
                      line_width=0, layer="below", annotation_text=label,
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
               f"×{1 - upgrade / 100:.3f} on deg. Wet tyres' base is the fastest dry base; on a wet "
               "lap they add the intensity's pace offset, on a dry one "
               f"{config.WET_TYRE_ON_DRY}. A car on the wrong tyre in the rain pits for the right one "
               "unless staying out costs less; once the track is dry it goes back onto slicks.")

# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------
st.markdown("##### Stochastic outcome (Monte Carlo)")
with st.spinner(f"Simulating {n_sims} races…"):
    mc_results, mc_summary = cached_monte_carlo(strategies, sim_kwargs, n_sims, sc_prob, noise, scenarios)

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
           f"{config.MC_SC_PIT_WINDOW} laps are brought forward)"
           + (", one weather scenario per race" if scenarios else "")
           + ". Every strategy sees the same noise, SC and weather draw in each simulated race.")
