"""
pages/2_Live_Race_Tracker.py — Live second-screen tactical tool.

Battle Map: every car's gap to the leader as a horizontal bar, with a
translucent "pit window shadow" stretching pit-loss seconds behind it — the
stretch of track the car would fall back through if it pitted now.

Shadow status (icon + label, never colour alone):
    🔴 Traffic   — rejoin point lands within the clean-air threshold of another car
    🟠 Loses places — other cars sit inside the shadow, but the rejoin is in clean air
    🟢 Free stop — nobody inside the shadow

Weather & rain call: the track sensors at the selected lap, a rain outlook (Open-Meteo's
15-minute forecast for that moment, archived for replays, or set by hand) and, for every
car, the time over the rest of the race of staying out, pitting now for wet tyres or for
new slicks, from the simulator's lap loop.

Prototype note: the lap selector replays a completed session lap by lap
(optionally auto-advancing). The undercut models only use laps up to the
selected lap, so nothing leaks from the future. Wiring in FastF1's live-timing
client is the step that turns this into a true race-day feed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # allow `import config`

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from modules import analytics as an
from modules import data_engine as de
from modules import simulator as sim
from modules import weather as wx

STATUS = {
    "critical": ("🔴", "Traffic"),
    "warning": ("🟠", "Loses places"),
    "good": ("🟢", "Free stop"),
}


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def pit_rejoin_analysis(snap: pd.DataFrame, pit_loss: float) -> pd.DataFrame:
    """
    For every car: where it would rejoin if it pitted now, which cars sit inside
    its pit window shadow (they'd pass it), and whether it rejoins in traffic.
    """
    snap = snap.sort_values("RunningPosition").reset_index(drop=True)
    rows = []
    for _, car in snap.iterrows():
        gap, rejoin = float(car["GapToLeader"]), float(car["GapToLeader"]) + pit_loss
        others = snap[snap["Driver"] != car["Driver"]]
        inside = others[(others["GapToLeader"] > gap) & (others["GapToLeader"] <= rejoin)]
        ahead = others[others["GapToLeader"] <= rejoin]
        behind = others[others["GapToLeader"] > rejoin]
        gap_ahead = rejoin - ahead["GapToLeader"].max() if not ahead.empty else float("inf")
        gap_behind = behind["GapToLeader"].min() - rejoin if not behind.empty else float("inf")
        car_ahead = ahead.loc[ahead["GapToLeader"].idxmax(), "Driver"] if not ahead.empty else "—"
        if gap_ahead <= config.CLEAN_AIR_THRESHOLD_S and not inside.empty:
            status = "critical"
        elif not inside.empty:
            status = "warning"
        else:
            status = "good"
        rows.append({
            "Pos": int(car["RunningPosition"]), "Driver": car["Driver"], "Gap": gap,
            "Tyre": f'{config.COMPOUND_SHORT.get(car["Compound"], "?")}{int(car["TyreLife"]) if pd.notna(car["TyreLife"]) else ""}',
            "Rejoin gap": rejoin, "Rejoin pos": int(len(ahead) + 1),
            "Cars in shadow": int(len(inside)), "Inside": ", ".join(inside["Driver"]),
            "Rejoins behind": car_ahead, "Air ahead (s)": gap_ahead, "Air behind (s)": gap_behind,
            "status": status,
        })
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False, max_entries=64)
def models_up_to_lap(year: int, event: str, stype: str, lap: int):
    """Degradation models fitted only on laps already completed (no future leakage)."""
    clean = de.get_cleaned_laps(year, event, stype)
    deg = an.calculate_tyre_degradation(clean[clean["LapNumber"] <= lap])
    return deg, an.field_compound_model(deg)


@st.cache_data(show_spinner=False, max_entries=64, ttl=900)
def cached_nowcast(lat: float, lon: float, at_utc: pd.Timestamp):
    try:
        return wx.nowcast(lat, lon, at_utc), None
    except wx.WeatherUnavailable as exc:
        return None, str(exc)


def rain_call(snap: pd.DataFrame, models: dict, weather: dict | None, laps_left: int,
              pit_loss: float) -> pd.DataFrame:
    """
    For every car, the time over the rest of the race of three calls now: stay out, pit
    for the rain tyre, or pit for the best new slick. Each call then takes its best
    continuation: no more stops, or one more onto any slick at the best lap (the simulator
    also reacts to the rain by itself). Weather laps count from the next lap.
    """
    wet_tyre = config.RAIN_PROFILES[(weather or {}).get("intensity") or "Light rain"]["compound"]
    n = laps_left

    def best(first: str, age: int) -> tuple[float, str]:
        plans = [[(first, n)]] + [[(first, k), (c, n - k)] for k in range(1, n) for c in config.DRY_COMPOUNDS]
        times = [sim._simulate_core(p, n, models, pit_loss, 0.0, weather, detail=False,
                                    start_age=age, race_start=False)[0] for p in plans]
        i = int(np.argmin(times))
        return times[i], sim.strategy_label(plans[i])

    memo: dict[tuple[str, int], tuple[float, str]] = {}

    def cached(first: str, age: int) -> tuple[float, str]:
        if (first, age) not in memo:
            memo[(first, age)] = best(first, age)
        return memo[(first, age)]

    rows = []
    for _, car in snap.sort_values("RunningPosition").iterrows():
        comp = car["Compound"]
        if comp not in models:
            continue
        age = int(car["TyreLife"]) if pd.notna(car["TyreLife"]) else 0
        stay, stay_plan = cached(comp, age)
        wet, wet_plan = cached(wet_tyre, 0)
        slick_t, slick_plan = min(cached(c, 0) for c in config.DRY_COMPOUNDS)
        options = {"Stay out": (stay, stay_plan),
                   f"Pit for {wet_tyre.title()}s": (wet + pit_loss, wet_plan),
                   "Pit for slicks": (slick_t + pit_loss, slick_plan)}
        call = min(options, key=lambda k: options[k][0])
        rows.append({"Pos": int(car["RunningPosition"]), "Driver": car["Driver"],
                     "Tyre": f"{config.COMPOUND_SHORT.get(comp, '?')}{age}",
                     **{k: options[k][0] - stay for k in options if k != "Stay out"},
                     "Best call": call, "Then": options[call][1], "Gain (s)": stay - options[call][0]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Battle map
# ---------------------------------------------------------------------------
def build_battle_map(rejoin: pd.DataFrame, drivers: pd.DataFrame, pit_loss: float) -> go.Figure:
    meta = drivers.set_index("Driver")
    df = rejoin.copy()
    df["Color"] = df["Driver"].map(meta["Color"]).fillna("#888888")
    order = df.sort_values("Pos")["Driver"].tolist()
    status_hex = df["status"].map(config.STATUS_COLORS)

    fig = go.Figure()
    # 1) Pit window shadow — drawn first so it sits behind the car bars.
    fig.add_trace(go.Bar(
        y=df["Driver"], x=[pit_loss] * len(df), base=df["Gap"], orientation="h", width=0.8,
        name="Pit window shadow", marker=dict(color=[_hex_to_rgba(c, 0.22) for c in status_hex],
                                              line=dict(color=status_hex.tolist(), width=1.5)),
        customdata=df[["Rejoin pos", "Cars in shadow", "Inside", "Rejoins behind", "Air ahead (s)"]].to_numpy(),
        hovertemplate=("<b>%{y}</b> pits now → rejoins P%{customdata[0]}<br>"
                       "Cars in shadow: %{customdata[1]} %{customdata[2]}<br>"
                       "Behind %{customdata[3]} by %{customdata[4]:.1f} s<extra>pit window</extra>"),
    ))
    # 2) Car bars: gap to leader (team colour; teammates share a hue).
    fig.add_trace(go.Bar(
        y=df["Driver"], x=df["Gap"], orientation="h", width=0.32, name="Gap to leader",
        marker=dict(color=df["Color"]), hovertemplate="<b>%{y}</b> +%{x:.1f} s<extra></extra>",
    ))
    # 3) Car head marker + label.
    fig.add_trace(go.Scatter(
        y=df["Driver"], x=df["Gap"], mode="markers", showlegend=False, hoverinfo="skip",
        marker=dict(size=11, color=df["Color"], line=dict(width=2, color="white")),
    ))
    # 4) Status label at the end of each shadow (icon + words).
    labels = [f"{STATUS[s][0]} {STATUS[s][1]}" + (f" ({n})" if n else "")
              for s, n in zip(df["status"], df["Cars in shadow"])]
    fig.add_trace(go.Scatter(
        y=df["Driver"], x=df["Rejoin gap"], mode="text", text=labels, textposition="middle right",
        textfont=dict(size=10), showlegend=False, hoverinfo="skip",
    ))
    x_max = float(df["Rejoin gap"].max()) + 14
    fig.update_layout(
        barmode="overlay", height=max(420, 30 * len(df) + 80), margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(categoryorder="array", categoryarray=order[::-1], title=None),
        xaxis=dict(title="Gap to leader (s)", range=[-1, x_max], gridcolor="rgba(128,128,128,0.15)"),
        legend=dict(orientation="h", y=1.03, x=0),
    )
    return fig


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.title("📡 Live Race Tracker")
active = de.render_session_selector()
if not active:
    st.info("Pick a season, Grand Prix and session in the sidebar, then press **Load session**.")
    st.stop()
active = de.page_session(active, "race")
info = de.load_active_session(active)
if info is None:
    st.stop()
try:
    timeline = de.get_race_timeline(*active)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not build the lap timeline: {exc}")
    st.stop()

base_loss, matched = config.get_pit_loss(info["location"], info["event_name"])
with st.sidebar:
    st.markdown("### 🛠️ Pit wall settings")
    pit_loss = st.number_input("Green-flag pit loss (s)", 10.0, 40.0, float(base_loss), 0.5,
                               help=f"Default from config.py ({matched or 'circuit not listed — default'}).")
    fresh = st.selectbox("Undercut tyre for car behind", ["HARD", "MEDIUM", "SOFT", "INTERMEDIATE"])
    respond = st.slider("Laps before car ahead can respond", 1, 3, 1)
    st.divider()
    auto = st.toggle("Auto-advance laps (replay)", value=False)
    speed = st.select_slider("Seconds per lap", [1, 2, 3, 5, 8], value=2, disabled=not auto)
    st.divider()
    st.markdown("### 🌧️ Rain call")
    rain_src = st.radio("Rain outlook", ["Open-Meteo forecast", "Set by hand"],
                        help="Forecast: Open-Meteo's 15-minute forecast for the circuit at the selected "
                             "lap (archived for a replay; it's a weather model, not radar, so it can miss "
                             "a shower). By hand: what your own radar or weather service says.")
    if rain_src == "Set by hand":
        rain_in = st.number_input("Rain arrives in (laps)", 0, 80, 3, help="0 = it's raining now")
        rain_for = st.number_input("Track wet for (laps, 0 = to the flag)", 0, 80, 10,
                                   help="Until slicks are quicker again, drying time included.")
        rain_int = st.radio("Intensity", list(config.RAIN_PROFILES), horizontal=True, key="live_rain_int")

st.markdown(f"{de.session_badge(active[2])} · {info['year']} {info['event_name']} — replaying lap by lap. "
            "Models only use laps already completed.")
lap_wx = de.get_lap_weather(*active)
coords = wx.circuit_coords(info["location"], info["event_name"])


def live_view() -> None:
    """Everything that changes per lap. Re-runs on its own timer when auto-advancing."""
    max_lap = int(timeline["LapNumber"].max())
    key = "live_lap"
    current = int(st.session_state.get(key, min(10, max_lap)))
    if auto:
        current = current + 1 if current < max_lap else max_lap
    st.session_state[key] = max(1, min(current, max_lap))
    lap = st.slider("Lap", 1, max_lap, key=key, disabled=auto)

    snap = timeline[timeline["LapNumber"] == lap]
    neutral = next((k for k, laps in info["neutralised"].items() if lap in laps), None)
    eff_loss = pit_loss * (config.SC_PIT_LOSS_FACTOR if neutral in ("SC", "VSC") else 1.0)
    rejoin = pit_rejoin_analysis(snap, eff_loss)

    m = st.columns(4)
    m[0].metric("Lap", f"{lap} / {max_lap}")
    m[1].metric("Leader", snap.sort_values("RunningPosition")["Driver"].iloc[0])
    m[2].metric("Track", {"SC": "Safety Car", "VSC": "Virtual SC", "RED": "Red flag"}.get(neutral, "Green flag"))
    m[3].metric("Effective pit loss", f"{eff_loss:.1f} s",
                delta=f"×{config.SC_PIT_LOSS_FACTOR} under {neutral}" if neutral in ("SC", "VSC") else None,
                delta_color="off")

    st.markdown("##### Battle map")
    st.plotly_chart(build_battle_map(rejoin, info["drivers"], eff_loss),
                    width="stretch", theme="streamlit")
    st.caption(f"Shadow = where each car would rejoin after a {eff_loss:.1f} s stop.  "
               "🔴 Traffic: rejoins within "
               f"{config.CLEAN_AIR_THRESHOLD_S} s of another car · 🟠 Loses places but rejoins in clean air · "
               "🟢 Free stop: nobody inside the shadow.")

    left, right = st.columns(2)
    with left:
        st.markdown("##### Pit rejoin forecast")
        show = rejoin.assign(Status=[f"{STATUS[s][0]} {STATUS[s][1]}" for s in rejoin["status"]])
        st.dataframe(
            show[["Pos", "Driver", "Tyre", "Gap", "Rejoin pos", "Cars in shadow", "Rejoins behind",
                  "Air ahead (s)", "Status"]],
            hide_index=True, width="stretch", height=420,
            column_config={"Gap": st.column_config.NumberColumn("Gap (s)", format="%.1f"),
                           "Air ahead (s)": st.column_config.NumberColumn(format="%.1f")},
        )
    with right:
        st.markdown("##### Undercut threats")
        deg, field = models_up_to_lap(*active, lap)
        threats = an.undercut_threats(snap, deg, field, fresh, respond)
        if threats.empty:
            st.info("Not enough clean laps yet to model tyre degradation — check back in a few laps.")
        else:
            threats["Status"] = threats["Status"].map(
                {"Vulnerable to Undercut": "⚠️ Vulnerable to Undercut", "Covered": "✅ Covered"})
            st.dataframe(
                threats, hide_index=True, width="stretch", height=420,
                column_config={c: st.column_config.NumberColumn(format="%+.2f")
                               for c in ["Gap A→B (s)", "B undercut gain (s)", "Margin (s)"]},
            )
            st.caption(f"B pits now onto fresh {fresh.title()}; A stays out {respond} lap(s). "
                       "A is vulnerable when the gap is smaller than B's projected gain.")

    weather_panel(lap, max_lap, snap, eff_loss)


def weather_panel(lap: int, max_lap: int, snap: pd.DataFrame, eff_loss: float) -> None:
    """Conditions at this lap, the rain outlook and the rain call for every car."""
    st.markdown("##### Weather & rain call")
    now = lap_wx[lap_wx["LapNumber"] == lap]
    if not now.empty:
        r = now.iloc[0]
        w = st.columns(4)
        w[0].metric("Track temp", f"{r['TrackTemp']:.1f} °C" if pd.notna(r["TrackTemp"]) else "—")
        w[1].metric("Air temp", f"{r['AirTemp']:.1f} °C" if pd.notna(r["AirTemp"]) else "—")
        w[2].metric("Humidity", f"{r['Humidity']:.0f}%" if pd.notna(r["Humidity"]) else "—")
        w[3].metric("Track sensor", "Rain" if r["Rainfall"] else "Dry")

    laps_left = max_lap - lap
    if laps_left < 1:
        st.info("Last lap: nothing left to call.")
        return
    weather, note, known = None, "", True
    if rain_src == "Set by hand":
        if rain_in <= laps_left:
            start = max(1, int(rain_in))
            dry = start + int(rain_for) if rain_for else None
            weather = {"rain_lap": start, "dry_lap": dry if dry and dry <= laps_left else None,
                       "intensity": rain_int}
    else:
        at = now["UTC"].iloc[0] if not now.empty else pd.NaT
        if coords is None or pd.isna(at):
            known, note = False, "No forecast: circuit position or time of day unknown. Set the rain by hand."
        else:
            rain, err = cached_nowcast(*coords, at.floor("15min"))
            if err:
                known, note = False, f"Forecast unavailable ({err}). Set the rain by hand."
            else:
                led = timeline[(timeline["LapNumber"] <= lap) & (timeline["RunningPosition"] == 1)]
                lap_s = float(led["LapTime"].tail(5).median()) if led["LapTime"].notna().any() else 95.0
                raining = bool(now["Rainfall"].iloc[0])
                weather = wx.nowcast_rain_laps(rain, at, lap_s, laps_left, raining_now=raining)
                note = (f"Open-Meteo 15-minute forecast from {at:%H:%M} UTC, laps of {lap_s:.0f} s"
                        + (", and the track sensor reports rain now" if raining else "")
                        + f"; the track dries {config.TRACK_DRYING_LAPS} laps after the rain stops. "
                        f"{wx.CREDIT}.")
    if weather:
        a = lap + weather["rain_lap"]
        b = lap + weather["dry_lap"] if weather["dry_lap"] else None
        st.markdown(f"**Outlook:** {weather['intensity'].lower()} from lap {a}"
                    + (f" until lap {b - 1}." if b else " to the flag."))
    elif known:
        st.markdown("**Outlook:** dry for the rest of the race.")
    if note:
        st.caption(note)

    _, field = models_up_to_lap(*active, lap)
    dry = {c: m for c, m in field.items() if c in config.DRY_COMPOUNDS}
    if dry:
        models = sim.build_compound_models({c: m["base_pace"] for c, m in dry.items()},
                                           {c: m["deg_rate"] for c, m in dry.items()})
    else:
        models = sim.build_compound_models(float(snap["LapTime"].median()),
                                           config.COMPOUND_PRESETS["MEDIUM"]["deg_rate"])
    call = rain_call(snap, models, weather, laps_left, eff_loss)
    if call.empty:
        return
    call["Best call"] = ["✅ " + b if b == "Stay out" else "🔧 " + b for b in call["Best call"]]
    st.dataframe(call, hide_index=True, width="stretch", height=min(420, 36 * len(call) + 40),
                 column_config={**{c: st.column_config.NumberColumn(format="%+.1f")
                                   for c in call.columns if c.startswith("Pit for")},
                                "Gain (s)": st.column_config.NumberColumn(format="%.1f")})
    st.caption(f"Seconds over the last {laps_left} laps against staying out (negative = quicker), "
               f"with a {eff_loss:.1f} s stop. Every call then takes its best continuation (no more "
               "stops, or one more onto a slick: **Then**, in laps from now), and the simulator still "
               "reacts to rain by itself (wet tyres when it lasts long enough, slicks once it's dry). "
               "Field-median tyre models up to this lap; wet-tyre pace offsets are config.py assumptions.")
    if not lap_wx.empty:
        with st.expander("Weather so far"):
            st.plotly_chart(wx.build_weather_figure(lap_wx, upto_lap=lap, height=280),
                            width="stretch", theme="streamlit")


st.fragment(live_view, run_every=f"{speed}s" if auto else None)()
