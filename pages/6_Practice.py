"""
pages/6_Practice.py — Free practice (FP1, FP2, FP3): the timesheet, one-lap pace and long runs.

Tabs
----
📋 Timesheet        best laps, gaps, tyres, laps run, one-lap and long-run pace
🏎️ Pace Map         one-lap pace against long-run pace: who's quick over one lap, who over a stint
📉 Long Runs        every long run lap by lap, fuel corrected, and each run's pace and wear
🧮 Weekend          the weekend's practice combined, as the forecasts use it

Picking a qualifying or race session in the sidebar shows the weekend's last practice before it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # allow `import config`

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from modules import data_engine as de
from modules import practice as pr
from modules import teams


def lap_time(s: float | None) -> str:
    if s is None or pd.isna(s):
        return "–"
    m = int(s // 60)
    return f"{m}:{s - 60 * m:06.3f}"


@st.cache_data(show_spinner=False, max_entries=6)
def practice_bundle(year: int, event: str, code: str) -> dict:
    laps = de.get_all_laps(year, event, code)
    runs, run_laps = pr.long_runs(laps)
    return {"laps": laps, "one_lap": pr.one_lap(laps), "runs": runs, "run_laps": run_laps,
            "pace": pr.session_pace(laps)}


st.title("🔧 Practice")
active = de.render_session_selector()
if not active:
    st.info("Pick a season, Grand Prix and session in the sidebar, then press **Load session**.")
    st.stop()
active = de.page_session(active, "practice")
year, event, code = active
info = de.load_active_session(active)
if info is None:
    st.stop()
try:
    with st.spinner("Building the practice analysis…"):
        B = practice_bundle(*active)
except Exception as exc:  # noqa: BLE001 — never crash the debrief screen
    st.error(f"Could not build the practice analysis for this session: {exc}")
    st.stop()

drivers = info["drivers"].set_index("Driver")
color = drivers["Color"].to_dict()
second = drivers["IsSecondDriver"].to_dict()
ol, runs, run_laps, pace = B["one_lap"], B["runs"], B["run_laps"], B["pace"]

st.markdown(f"{de.session_badge(code)} · {info['year']} {info['event_name']} · {info['location']}")
m = st.columns(4)
m[0].metric("Fastest", f"{ol.iloc[0]['driver']} {lap_time(ol.iloc[0]['best'])}" if not ol.empty else "–")
m[1].metric("Laps run", int(B["laps"].groupby("Driver")["LapNumber"].max().sum()))
m[2].metric("Long runs", len(runs))
m[3].metric("Red flags", len(info["neutralised"]["RED"]) and "Yes" or "None")
st.caption("Practice times hide fuel loads and engine modes, so read them as a guide. One-lap pace is each "
           "driver's best clean lap; a long run is at least "
           f"{config.PRACTICE_LONG_RUN_LAPS} consecutive laps within {config.PRACTICE_RUN_TOL:.1%} of the run's best, "
           "fuel corrected and compared on the same tyres.")

tab_sheet, tab_map, tab_runs, tab_weekend = st.tabs(["📋 Timesheet", "🏎️ Pace Map", "📉 Long Runs", "🧮 Weekend"])

with tab_sheet:
    sheet = ol.assign(gap=ol["best"] - ol["best"].min())
    sheet["Laps"] = sheet["driver"].map(B["laps"].groupby("Driver")["LapNumber"].max())
    sheet["One-lap %"] = sheet["driver"].map(pace["one_lap"])
    sheet["Long-run %"] = sheet["driver"].map(pace["long_run"])
    sheet["Team"] = sheet["driver"].map(drivers["Team"])
    st.dataframe(teams.with_badges(pd.DataFrame({
        "Pos": range(1, len(sheet) + 1), "Driver": sheet["driver"], "Team": sheet["Team"],
        "Best": sheet["best"].map(lap_time), "Gap": sheet["gap"].map(lambda g: "–" if g == 0 else f"+{g:.3f}"),
        "Tyre": sheet["compound"].str.title(), "Laps": sheet["Laps"],
        "One-lap %": sheet["One-lap %"].round(2), "Long-run %": sheet["Long-run %"].round(2),
    }), teams.team_colors(info["drivers"]), year), hide_index=True, width="stretch", column_config=teams.badge_column())
    st.caption("% columns: against the field median, negative = faster. Long-run pace is blank for drivers "
               "with no long run in this session.")

with tab_map:
    both = pd.concat([pace["one_lap"].rename("one"), pace["long_run"].rename("long")], axis=1).dropna()
    if both.empty:
        st.info("No driver did both a fast lap and a long run in this session.")
    else:
        fig = go.Figure()
        for d, r in both.iterrows():
            fig.add_trace(go.Scatter(x=[r["one"]], y=[r["long"]], mode="markers+text", text=[d], textposition="top center",
                                     marker=dict(size=12, color=color.get(d), symbol="diamond" if second.get(d) else "circle"),
                                     name=d, showlegend=False,
                                     hovertemplate=f"{d}<br>one-lap %{{x:+.2f}}%<br>long-run %{{y:+.2f}}%<extra></extra>"))
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.add_vline(x=0, line_dash="dot", line_color="gray")
        fig.update_layout(xaxis_title="One-lap pace (% against field, negative = faster)",
                          yaxis_title="Long-run pace (%)", height=520)
        fig.update_xaxes(autorange="reversed")
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, theme="streamlit", width="stretch")
        st.caption("Top right: quick over one lap and over a stint. Teammates share a colour; the second driver "
                   "is a diamond.")

with tab_runs:
    if runs.empty:
        st.info("No long runs in this session.")
    else:
        fig = go.Figure()
        for rid, r in run_laps.groupby("run_id", sort=False):
            d = r["Driver"].iloc[0]
            fig.add_trace(go.Scatter(x=r["run_lap"] + 1, y=r["fc"], mode="lines+markers", name=f"{d} ({r['Compound'].iloc[0].title()})",
                                     line=dict(color=color.get(d), dash="dot" if second.get(d) else "solid"),
                                     hovertemplate=f"{d} lap %{{x}} of the run: %{{y:.3f}} s<extra></extra>"))
        fig.update_layout(xaxis_title="Lap of the run", yaxis_title="Fuel-corrected lap time (s)", height=520)
        st.plotly_chart(fig, theme="streamlit", width="stretch")
        st.dataframe(runs.sort_values("pace").assign(
            compound=runs["compound"].str.title(), median=runs["median"].map(lap_time),
            deg=runs["deg"].round(3), pace=runs["pace"].round(2))
            .rename(columns={"driver": "Driver", "compound": "Tyre", "first": "From lap", "last": "To lap", "laps": "Laps",
                             "median": "Median (fuel corr.)", "deg": "Deg s/lap", "pace": "Pace %"})
            .drop(columns="stint"), hide_index=True, width="stretch")
        st.caption("Each lap + 0.033 s per lap into the run (the fuel burnt). Pace % allows for the tyre: lap time "
                   "= driver + compound, fitted on every long-run lap.")

with tab_weekend:
    done = [c for c in de.get_event_sessions(year).get(event, []) if c in config.PRACTICE_CODES]
    sessions = {}
    for c in done:
        try:
            sessions[c] = practice_bundle(year, event, c)["pace"]
        except Exception:  # noqa: BLE001 — a missing session just doesn't count
            continue
    wk = pr.weekend_practice(sessions)
    table = pd.concat([wk["one_lap"].rename("One-lap %"), wk["long_run"].rename("Long-run %")], axis=1)
    st.markdown(f"The weekend's practice so far ({', '.join(sessions) or 'none'}), combined as the forecasts use it: "
                f"each session weighted {', '.join(f'{c} {w}' for c, w in config.PRACTICE_SESSION_WEIGHT.items())}.")
    st.dataframe(table.sort_values("One-lap %").round(2), width="stretch")
    st.caption(f"In the forecasts: qualifying form moves towards the one-lap figure by {config.PRACTICE_QUALI_BLEND:.0%}, "
               f"race form towards the long-run figure by {config.PRACTICE_RACE_BLEND:.0%} (fitted by replaying "
               "2025-26: scripts/calibrate_forecast.py).")
