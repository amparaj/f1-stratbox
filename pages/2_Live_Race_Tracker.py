"""
pages/2_Live_Race_Tracker.py — Live second-screen tactical tool.

Battle Map: every car's gap to the leader as a horizontal bar, with a
translucent "pit window shadow" stretching pit-loss seconds behind it — the
stretch of track the car would fall back through if it pitted now.

Shadow status (icon + label, never colour alone):
    🔴 Traffic   — rejoin point lands within the clean-air threshold of another car
    🟠 Loses places — other cars sit inside the shadow, but the rejoin is in clean air
    🟢 Free stop — nobody inside the shadow

Prototype note: the lap selector replays a completed session lap by lap
(optionally auto-advancing). The undercut models only use laps up to the
selected lap, so nothing leaks from the future. Wiring in FastF1's live-timing
client is the step that turns this into a true race-day feed.
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

st.caption(f"{info['year']} {info['event_name']} · {info['session_name']} — replaying lap by lap. "
           "Models only use laps already completed.")


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


st.fragment(live_view, run_every=f"{speed}s" if auto else None)()
