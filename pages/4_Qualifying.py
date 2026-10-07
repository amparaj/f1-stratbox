"""
pages/4_Qualifying.py — Qualifying and Sprint Qualifying: results, analysis, strategy.

Tabs
----
📋 Result           Q1 / Q2 / Q3 times, gap to pole, segment reached, teammate gaps, pace
✂️ Cut-offs         how close each driver was to going out in Q1 and Q2
🧩 Sectors          best sectors, the ideal lap and time left on the table, speed trap
📈 Track Evolution  every flying lap on the session clock, segment-to-segment gains
🎯 Strategy         when each driver ran, what running late was worth, tyres on the best lap
🏁 Quali vs Race    one-lap pace against the weekend's race pace (once the race is in)

Picking a race in the sidebar shows the qualifying that set its grid (Grand Prix ->
Qualifying, Sprint -> Sprint Qualifying).
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
from modules import data_engine as de
from modules import forecast as fc
from modules import teams


def lap_time(s: float | None) -> str:
    """92.456 -> '1:32.456'."""
    if s is None or pd.isna(s):
        return "–"
    m = int(s // 60)
    return f"{m}:{s - 60 * m:06.3f}"


@st.cache_data(show_spinner=False, max_entries=6)
def quali_bundle(year: int, event: str, code: str) -> dict:
    """Everything this page needs for one qualifying session, computed once."""
    res = de.get_quali_results(year, event, code)
    laps = de.get_quali_laps(year, event, code)
    return {"res": res, "laps": laps, "sectors": de.quali_sectors(laps),
            "evolution": de.quali_evolution(res), "gain": de.quali_track_gain(laps)}


@st.cache_data(show_spinner=False, max_entries=6)
def race_pace_of(year: int, event: str, code: str) -> pd.Series | None:
    """The weekend's race pace per driver, or None until the race is in."""
    try:
        return fc.race_pace(de.get_cleaned_laps(year, event, code))
    except Exception:  # noqa: BLE001 — not raced yet, or no data
        return None


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.title("⏱️ Qualifying")
active = de.render_session_selector()
if not active:
    st.info("Pick a season, Grand Prix and session in the sidebar, then press **Load session**.")
    st.stop()
active = de.page_session(active, "quali")
info = de.load_active_session(active)
if info is None:
    st.stop()
try:
    with st.spinner("Building the qualifying analysis…"):
        B = quali_bundle(*active)
except Exception as exc:  # noqa: BLE001 — never crash the debrief screen
    st.error(f"Could not build the qualifying analysis for this session: {exc}")
    st.stop()

res, laps, sectors = B["res"], B["laps"], B["sectors"]
evo, gain = B["evolution"], B["gain"]
code = active[2]
to_q2, to_q3 = de.quali_cutoffs(len(res))
style = res.set_index("Driver")[["Color", "IsSecondDriver"]]
race = config.RACE_OF_QUALI[code]

st.markdown(f"{de.session_badge(code)} · {info['year']} {info['event_name']} · {info['location']} · "
            f"sets the {de.session_label(race)} grid · {len(res)} drivers: {to_q2} into Q2, {to_q3} into Q3")
pole = res.iloc[0] if not res.empty else None
m = st.columns(5)
m[0].metric("Pole", f"{pole['Driver']}" if pole is not None else "—",
            lap_time(pole["Best"]) if pole is not None else None, delta_color="off")
m[1].metric("Margin to P2", f"{res.iloc[1]['Best'] - pole['Best']:.3f} s" if len(res) > 1 else "—")
m[2].metric("Q1 → Q2", f"{evo['Q1_Q2']:+.3f} s" if evo.get("Q1_Q2") is not None else "—",
            help="Median change of the same drivers' times from one segment to the next.")
m[3].metric("Q2 → Q3", f"{evo['Q2_Q3']:+.3f} s" if evo.get("Q2_Q3") is not None else "—")
m[4].metric("Red flags", len(info["neutralised"]["RED"]) and "Yes" or "None")

tab_res, tab_cut, tab_sec, tab_evo, tab_strat, tab_race = st.tabs(
    ["📋 Result", "✂️ Cut-offs", "🧩 Sectors", "📈 Track Evolution", "🎯 Strategy", "🏁 Quali vs Race"])

# --- Result -----------------------------------------------------------------
with tab_res:
    table = pd.DataFrame({
        "Pos": res["Position"].astype("Int64"), "Driver": res["Driver"], "Team": res["Team"],
        "Q1": res["Q1"].map(lap_time), "Q2": res["Q2"].map(lap_time), "Q3": res["Q3"].map(lap_time),
        "To pole (s)": res["GapToPole"].round(3),
        "Out in": res["Reached"].map({1: "Q1", 2: "Q2", 3: "Q3 (top 10)"}),
        "Teammate (s)": res["TeammateGap"].round(3),
        "Pace (%)": res["Pace"].round(2),
    })
    st.dataframe(teams.with_badges(table, teams.team_colors(res), info["year"]), hide_index=True, width="stretch",
                 column_config=teams.badge_column())
    st.caption("Pace puts every driver on one scale although the track gets faster: each segment's times "
               "against the median time of the Q3 runners in that segment, the best of them, centred on "
               "the field median (negative = faster). Teammate: against the teammate in the last segment "
               "both set a time.")

# --- Cut-offs ---------------------------------------------------------------
with tab_cut:
    c1, c2 = st.columns(2)
    for col, q, n in ((c1, "Q1", to_q2), (c2, "Q2", to_q3)):
        d = res[res[f"{q}Margin"].notna() & (res[f"{q}Margin"].abs() <= 1.0)].sort_values(f"{q}Margin")
        with col:
            st.markdown(f"**{q} cut-off** (top {n} through)")
            if d.empty:
                st.caption("No times either side of the line.")
                continue
            fig = go.Figure(go.Bar(
                x=d[f"{q}Margin"], y=d["Driver"], orientation="h",
                marker=dict(color=d["Color"], opacity=np.where(d[f"{q}Margin"] < 0, 1.0, 0.45),
                            pattern_shape=np.where(d["IsSecondDriver"], "/", "")),
                text=d[f"{q}Margin"].map(lambda v: f"{v:+.3f}"), textposition="outside",
                hovertemplate="%{y}: %{x:+.3f} s<extra></extra>"))
            fig.add_vline(x=0, line_color="#888")
            fig.update_layout(height=60 + 24 * len(d), margin=dict(l=10, r=10, t=10, b=30),
                              xaxis_title="Seconds against the cut-off (negative = through)",
                              yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig, theme="streamlit", width="stretch")
    st.caption("A driver who went through is measured against the fastest driver knocked out; one knocked "
               "out against the slowest who went through. Faded bars missed out; hatched bars are the "
               "team's second driver. Drivers more than a second from the line are left off.")

# --- Sectors ----------------------------------------------------------------
with tab_sec:
    if sectors.empty:
        st.info("No valid laps with sector times.")
    else:
        best = {k: sectors[k].min() for k in ("S1", "S2", "S3")}
        ideal = sum(best.values())
        k1, k2, k3 = st.columns(3)
        k1.metric("Best sectors combined", lap_time(ideal),
                  f"{ideal - pole['Best']:+.3f} s vs pole" if pole is not None else None, delta_color="off")
        k2.metric("Most left on the table", sectors.loc[sectors["LostToIdeal"].idxmax(), "Driver"],
                  f"{sectors['LostToIdeal'].max():.3f} s", delta_color="off")
        k3.metric("Top speed", sectors.loc[sectors["TopSpeed"].idxmax(), "Driver"] if sectors["TopSpeed"].notna().any() else "—",
                  f"{sectors['TopSpeed'].max():.0f} km/h" if sectors["TopSpeed"].notna().any() else None, delta_color="off")
        view = sectors.assign(**{
            "Best lap": sectors["BestLap"].map(lap_time), "Ideal": sectors["Ideal"].map(lap_time),
            "Lost (s)": sectors["LostToIdeal"].round(3), "Tyre": sectors["BestCompound"].str.title(),
            "Segment": sectors["BestSegment"].map(lambda s: f"Q{int(s)}" if pd.notna(s) else "–"),
        })[["Driver", "Best lap", "Segment", "Tyre", "Ideal", "Lost (s)", "S1", "S2", "S3", "TopSpeed", "PushLaps"]]
        st.dataframe(
            view.style.format({"S1": "{:.3f}", "S2": "{:.3f}", "S3": "{:.3f}", "TopSpeed": "{:.0f}"}, na_rep="–")
                .highlight_min(subset=["S1", "S2", "S3"], props="font-weight: bold"),
            hide_index=True, width="stretch")
        st.caption("Ideal = the driver's best three sectors from any of their laps added up; Lost = best lap "
                   "minus ideal. Push laps = laps within "
                   f"{(config.QUALI_PUSH_FACTOR - 1) * 100:.0f}% of the driver's best. Fastest sectors in bold.")
        deficit = sectors.assign(**{k: sectors[k] - best[k] for k in ("S1", "S2", "S3")}).set_index("Driver")
        fig = go.Figure()
        for k, shade in zip(("S1", "S2", "S3"), ("#2a78d6", "#eb6834", "#1baf7a")):
            fig.add_bar(y=deficit.index, x=deficit[k], name=k, orientation="h", marker_color=shade,
                        hovertemplate=f"%{{y}} {k}: +%{{x:.3f}} s<extra></extra>")
        fig.update_layout(barmode="stack", height=40 + 20 * len(deficit), margin=dict(l=10, r=10, t=30, b=30),
                          xaxis_title="Time lost to the fastest sector (s)", yaxis=dict(autorange="reversed"),
                          title=dict(text="Where each driver lost time to the best sectors", font_size=14))
        st.plotly_chart(fig, theme="streamlit", width="stretch")

# --- Track evolution --------------------------------------------------------
with tab_evo:
    push = laps[laps["Push"] & laps["Time"].notna()].copy()
    if push.empty:
        st.info("No flying laps with timing.")
    else:
        t0 = laps["LapStartTime"].min()
        push["Minute"] = (push["Time"] - t0) / 60.0
        fig = go.Figure()
        for d, g in push.groupby("Driver"):
            fig.add_scatter(x=g["Minute"], y=g["LapTime"], mode="markers", name=d,
                            marker=dict(color=style.at[d, "Color"] if d in style.index else "#888", size=8,
                                        symbol="diamond" if d in style.index and style.at[d, "IsSecondDriver"] else "circle"),
                            customdata=np.stack([g["Segment"].fillna(0), g["Compound"]], axis=-1),
                            hovertemplate=f"<b>{d}</b> Q%{{customdata[0]:.0f}} · %{{customdata[1]}}<br>"
                                          "%{y:.3f} s at %{x:.0f} min<extra></extra>")
        for q, dash in (("Q1", "dash"), ("Q2", "dot")):
            cut = res.loc[res["Reached"] >= (2 if q == "Q1" else 3), q].max()
            if pd.notna(cut):
                fig.add_hline(y=cut, line_dash=dash, line_color="#888", annotation_text=f"{q} cut-off",
                              annotation_position="top left")
        lo = push["LapTime"].min()
        fig.update_layout(height=480, margin=dict(l=10, r=10, t=30, b=40),
                          xaxis_title="Minutes into the session", yaxis_title="Lap time (s)",
                          yaxis=dict(range=[lo - 0.3, lo * 1.035]))
        st.plotly_chart(fig, theme="streamlit", width="stretch")
        st.caption("Every push lap by when it ended, in team colours (diamonds: the team's second driver). "
                   "The lines are the slowest times that got through Q1 and Q2.")
        g1, g2, g3 = st.columns(3)
        for col, q in zip((g1, g2, g3), de.SEGMENTS):
            v = gain.get(q)
            col.metric(f"Within {q}", f"{v:+.3f} s/min" if v is not None else "—",
                       help="Slope of push-lap times on the clock, each lap against the same driver's "
                            "mean push lap in that segment: what a later lap was worth.")

# --- Strategy ---------------------------------------------------------------
with tab_strat:
    st.markdown("##### When each driver ran")
    runs = laps[laps["Push"] & laps["Time"].notna()].copy()
    if not runs.empty:
        t0 = laps["LapStartTime"].min()
        runs["Minute"] = (runs["Time"] - t0) / 60.0
        order = [d for d in res["Driver"] if d in set(runs["Driver"])]
        fig = go.Figure()
        for comp, g in runs.groupby("Compound"):
            fig.add_scatter(x=g["Minute"], y=g["Driver"], mode="markers", name=comp.title(),
                            marker=dict(color=config.COMPOUND_COLORS.get(comp, "#7f7f7f"), size=10,
                                        line=dict(width=1, color="#444")),
                            customdata=g["LapTime"],
                            hovertemplate="%{y} · %{x:.0f} min · %{customdata:.3f} s<extra></extra>")
        best_laps = laps[laps["Valid"]].loc[lambda x: x.groupby("Driver")["LapTime"].transform("min") == x["LapTime"]]
        fig.add_scatter(x=(best_laps["Time"] - t0) / 60.0, y=best_laps["Driver"], mode="markers", name="Best lap",
                        marker=dict(symbol="star", size=14, color="rgba(0,0,0,0)", line=dict(width=1.5, color="#888")),
                        hoverinfo="skip")
        fig.update_layout(height=60 + 22 * len(order), margin=dict(l=10, r=10, t=10, b=40),
                          xaxis_title="Minutes into the session",
                          yaxis=dict(categoryorder="array", categoryarray=order[::-1]))
        st.plotly_chart(fig, theme="streamlit", width="stretch")
        st.caption("Each marker is a push lap, coloured by tyre; the outlined star is the driver's best lap. "
                   "Drivers in qualifying order, pole at the top.")

    st.markdown("##### What it says about running the session")
    tips = []
    if gain.get("Q1") is not None and gain["Q1"] < -0.01:
        tips.append(f"Within Q1 the track came **{abs(gain['Q1']):.3f} s a minute**: a lap in the last minutes was "
                    f"worth about {abs(gain['Q1']) * 15:.1f} s over one at the start, so the final run decided it.")
    if evo.get("Q1_Q2") is not None:
        tips.append(f"The same drivers found **{-evo['Q1_Q2']:.3f} s** from Q1 to Q2"
                    + (f" and **{-evo['Q2_Q3']:.3f} s** from Q2 to Q3." if evo.get("Q2_Q3") is not None else "."))
    near = res[(res["Reached"] == 1) & (res["Q1Margin"] <= 0.15)]
    if not near.empty:
        tips.append("Out in Q1 by less than 0.15 s: " + ", ".join(f"{r.Driver} ({r.Q1Margin:+.3f})" for r in near.itertuples())
                    + ". One more run, or a later one, would likely have saved them.")
    one_run = sectors[sectors["PushLaps"] <= 1]["Driver"].tolist()
    if one_run:
        tips.append("Only one push lap all session: " + ", ".join(one_run) + " (a red flag, a crash, or saving tyres).")
    for t in tips:
        st.markdown(f"- {t}")
    if not tips:
        st.caption("Not enough laps to say.")

# --- Quali vs race ----------------------------------------------------------
with tab_race:
    rp = race_pace_of(active[0], active[1], race)
    if rp is None or rp.empty:
        st.info(f"The {de.session_label(race)} isn't in yet: its race pace appears here once it is.")
    else:
        both = pd.DataFrame({"quali": res.set_index("Driver")["Pace"], "race": rp}).dropna()
        fig = go.Figure()
        for d, r in both.iterrows():
            fig.add_scatter(x=[r["quali"]], y=[r["race"]], mode="markers+text", text=[d], textposition="top center",
                            marker=dict(color=style.at[d, "Color"] if d in style.index else "#888", size=11,
                                        symbol="diamond" if d in style.index and style.at[d, "IsSecondDriver"] else "circle"),
                            showlegend=False, hovertemplate=f"<b>{d}</b><br>Quali %{{x:+.2f}}% · race %{{y:+.2f}}%<extra></extra>")
        lim = [min(both.min()) - 0.2, max(both.max()) + 0.2]
        fig.add_scatter(x=lim, y=lim, mode="lines", line=dict(color="#888", dash="dot"), showlegend=False, hoverinfo="skip")
        fig.update_layout(height=520, margin=dict(l=10, r=10, t=10, b=40),
                          xaxis_title=f"{de.session_label(code)} pace (% vs field, negative = faster)",
                          yaxis_title=f"{de.session_label(race)} race pace (% vs field)")
        st.plotly_chart(fig, theme="streamlit", width="stretch")
        st.caption("Below the dotted line: quicker over a race than over one lap (a Sunday car, or good tyre "
                   "management); above it: quicker on Saturday. Race pace is fuel corrected, per compound, "
                   "clean laps only.")
