"""
pages/1_Race_Recap.py — Post-race strategy analysis & review.

Tabs
----
🎬 Race Replay      track replay from car telemetry (modules/replay_player.html), and
                   the lap-by-lap gap-to-leader chart (Plotly frames)
📝 Post-Mortem      tyre-strategy chart + rule-based report naming each cliff lap
📉 Degradation      fuel-corrected lap time vs tyre age with fitted deg lines
🌦️ Weather          track sensors lap by lap, and what the forecast said (Open-Meteo)
🚩 Penalties        the weekend's stewards' decisions (f1penalties.com) and power-unit elements
                   fitted, with each driver's count against the allocation (FIA documents)
⬇️ Export           Excel workbook (openpyxl) + an LLM-ready debrief prompt
"""

from __future__ import annotations

import io
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
from modules import penalties as pn
from modules import teams
from modules import telemetry as tm
from modules import weather as wx

REPLAY_KEY = "replay_session"      # the session whose track replay was asked for (survives reruns)
REPLAY_HEIGHT = 760


@st.cache_data(show_spinner=False, ttl=6 * 3600)
def weekend_penalties(year: int, rnd: int) -> tuple[pd.DataFrame, pd.DataFrame, dict, int | None]:
    """(the round's stewards' decisions, every car's power units before and after it, the season's
    allocation, the last round f1penalties.com has). Empty frames if a source is down."""
    try:
        dec = pn.stewards_decisions([year])
    except Exception:  # noqa: BLE001 — f1penalties.com down and nothing cached
        dec = pn.stewards_decisions([])
    last = int(dec["round"].max()) if not dec.empty else None
    dec = dec[dec["round"] == rnd]
    try:
        recs = pn.pu_season(year)
    except Exception:  # noqa: BLE001 — FIA site down: what's archived
        recs = {}
    limits = pn.season_limits(year, recs)
    table = pn.usage_table(year, recs, max(recs) if recs else 0) if recs else pd.DataFrame()
    if not table.empty:
        table = table[table["round"] == rnd]
    return dec, table, limits, last


# ---------------------------------------------------------------------------
# Data bundle (cached per session)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=6)
def recap_bundle(year: int, event: str, stype: str) -> dict:
    """Everything this page needs for one session, computed once."""
    info = de.get_session_info(year, event, stype)
    clean = de.get_cleaned_laps(year, event, stype)
    timeline = de.get_race_timeline(year, event, stype)
    deg = an.calculate_tyre_degradation(clean)
    reports = an.build_postmortem(clean, timeline, info["drivers"])
    return {
        "info": info, "clean": clean, "timeline": timeline,
        "deg_frame": an.degradation_to_frame(deg),
        "stints": an.stint_summary(timeline), "reports": reports,
    }


@st.cache_data(show_spinner=False, max_entries=6)
def recap_weather(year: int, event: str, stype: str) -> tuple[pd.DataFrame, pd.DataFrame | None, str | None]:
    """Track sensors by lap, the archived forecast lined up with the laps, and why it's missing."""
    info = de.get_session_info(year, event, stype)
    lw = de.get_lap_weather(year, event, stype)
    if lw.empty or info["start_utc"] is None:
        return lw, None, None
    coords = wx.circuit_coords(info["location"], info["event_name"])
    if coords is None:
        return lw, None, "no coordinates for this circuit"
    try:
        table = wx.hourly(*coords, info["start_utc"], hours=3)
    except wx.WeatherUnavailable as exc:
        return lw, None, str(exc)
    return lw, wx.forecast_by_lap(table, lw["UTC"]), None


def _runs(laps: list[int]) -> list[tuple[int, int]]:
    """Collapse [3,4,5,9,10] -> [(3,5),(9,10)] for shading neutralised periods."""
    runs: list[tuple[int, int]] = []
    for lap in sorted(laps):
        if runs and lap == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], lap)
        else:
            runs.append((lap, lap))
    return runs


def _shade_neutralised(fig: go.Figure, neutralised: dict, axis: str = "x") -> None:
    """Add SC / VSC / red-flag bands as background rectangles."""
    styles = {"SC": config.SC_SHADE, "VSC": config.VSC_SHADE, "RED": config.RED_FLAG_SHADE}
    for kind, laps in neutralised.items():
        for a, b in _runs(laps):
            fig.add_vrect(x0=a - 0.5, x1=b + 0.5, fillcolor=styles[kind], line_width=0,
                          layer="below", annotation_text=kind, annotation_position="top left",
                          annotation_font_size=10)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def build_replay_figure(timeline: pd.DataFrame, drivers: pd.DataFrame,
                        neutralised: dict, y_cap: float) -> go.Figure:
    """
    Animated gap-to-leader replay. Every frame k draws each driver's trail up
    to lap k plus a labelled head marker. Drivers who retired keep their trail
    but it simply ends (marked ✕) — no padding, so arrays never mismatch.
    Trace count is constant across frames (2 per driver + 1 pit-stop layer).
    """
    max_lap = int(timeline["LapNumber"].max())
    per_driver = {d: g.sort_values("LapNumber") for d, g in timeline.groupby("Driver")}
    order = [d for d in drivers["Driver"] if d in per_driver]
    meta = drivers.set_index("Driver")

    def frame_traces(k: int) -> list[go.Scatter]:
        traces = []
        for d in order:
            g = per_driver[d]
            g = g[g["LapNumber"] <= k]
            color = meta.at[d, "Color"]
            dash = "dot" if meta.at[d, "IsSecondDriver"] else "solid"
            custom = np.stack([g["RunningPosition"], g["Compound"], g["TyreLife"].fillna(0)], axis=-1) \
                if not g.empty else None
            traces.append(go.Scatter(
                x=g["LapNumber"], y=g["GapToLeader"], mode="lines", name=d, legendgroup=d,
                line=dict(color=color, width=2, dash=dash), customdata=custom,
                hovertemplate=(f"<b>{d}</b> · Lap %{{x}}<br>Gap +%{{y:.1f}} s · P%{{customdata[0]}}"
                               "<br>%{customdata[1]} (age %{customdata[2]:.0f})<extra></extra>"),
            ))
            retired = bool(meta.at[d, "DNF"]) and not g.empty and g["LapNumber"].max() < k
            head = g.tail(1)
            traces.append(go.Scatter(
                x=head["LapNumber"], y=head["GapToLeader"], mode="markers+text",
                text=[d] if not head.empty else [], textposition="middle right",
                textfont=dict(size=10), legendgroup=d, showlegend=False, hoverinfo="skip",
                marker=dict(color=color, size=9, symbol="x" if retired else "circle",
                            line=dict(width=1.5, color="white")),
            ))
        pits = timeline[(timeline["PitIn"]) & (timeline["LapNumber"] <= k)
                        & timeline["Driver"].isin(order)]
        traces.append(go.Scatter(
            x=pits["LapNumber"], y=pits["GapToLeader"], mode="markers", name="Pit stop",
            marker=dict(symbol="triangle-down", size=9, color="rgba(0,0,0,0)",
                        line=dict(width=1.5, color="#888")),
            text=pits["Driver"], hovertemplate="<b>%{text}</b> pits · Lap %{x}<extra></extra>",
        ))
        return traces

    frames = [go.Frame(data=frame_traces(k), name=str(k)) for k in range(1, max_lap + 1)]
    fig = go.Figure(data=frame_traces(max_lap), frames=frames)
    _shade_neutralised(fig, neutralised)

    anim = dict(frame=dict(duration=140, redraw=True), transition=dict(duration=0), mode="immediate")
    fig.update_layout(
        height=620, margin=dict(l=10, r=10, t=40, b=10), hovermode="closest",
        xaxis=dict(title="Lap", range=[0.5, max_lap + 4], showgrid=False),
        yaxis=dict(title="Gap to leader (s) — leader at top", range=[y_cap, -2],
                   gridcolor="rgba(128,128,128,0.15)", zeroline=False),
        legend=dict(orientation="h", y=-0.12, font=dict(size=10)),
        updatemenus=[dict(
            type="buttons", direction="left", x=0, y=1.08, xanchor="left", showactive=False,
            buttons=[dict(label="▶ Replay", method="animate", args=[None, {**anim, "fromcurrent": False}]),
                     dict(label="⏸ Pause", method="animate",
                          args=[[None], dict(frame=dict(duration=0, redraw=False), mode="immediate")])],
        )],
        sliders=[dict(
            active=max_lap - 1, x=0.12, len=0.88, y=1.1, yanchor="bottom",
            currentvalue=dict(prefix="Lap ", font=dict(size=12)), pad=dict(t=0, b=0),
            steps=[dict(method="animate", label=str(k),
                        args=[[str(k)], dict(frame=dict(duration=0, redraw=True), mode="immediate")])
                   for k in range(1, max_lap + 1)],
        )],
    )
    return fig


def build_stint_chart(stints: pd.DataFrame, reports: list[dict], drivers: pd.DataFrame) -> go.Figure:
    """Tyre-strategy timeline: one row per driver, bars coloured by compound, cliffs marked."""
    order = [d for d in drivers["Driver"] if d in set(stints["Driver"])]
    fig = go.Figure()
    for comp in stints["Compound"].unique():
        s = stints[stints["Compound"] == comp]
        fig.add_trace(go.Bar(
            y=s["Driver"], x=s["Laps"], base=s["FirstLap"] - 1, orientation="h", name=comp.title(),
            marker=dict(color=config.COMPOUND_COLORS.get(comp, config.COMPOUND_COLORS["UNKNOWN"]),
                        line=dict(width=2, color="rgba(255,255,255,0.9)")),
            text=[config.COMPOUND_SHORT.get(comp, "?")] * len(s), textposition="inside",
            insidetextanchor="middle", textangle=0, textfont=dict(color="#111", size=10),
            customdata=np.stack([s["FirstLap"], s["LastLap"], s["Laps"]], axis=-1),
            hovertemplate="%{y} · " + comp.title() + "<br>Laps %{customdata[0]}–%{customdata[1]} "
                          "(%{customdata[2]} laps)<extra></extra>",
        ))
    cliffs = [(r["driver"], c) for r in reports for c in r["cliffs"]]
    if cliffs:
        fig.add_trace(go.Scatter(
            x=[c["lap"] - 0.5 for _, c in cliffs], y=[d for d, _ in cliffs], mode="markers",
            name="Tyre cliff / weather drop",
            marker=dict(symbol=["x" if c["cause"] == "tyre" else "diamond-open" for _, c in cliffs],
                        size=11, color=["#d03b3b" if c["cause"] == "tyre" else "#2a78d6" for _, c in cliffs],
                        line=dict(width=2)),
            text=[f"{'Tyre cliff' if c['cause'] == 'tyre' else 'Rain-related drop'} · Lap {c['lap']}"
                  f" · ≈{c['time_lost']:.1f}s lost" for _, c in cliffs],
            hovertemplate="%{y} · %{text}<extra></extra>",
        ))
    fig.update_layout(
        barmode="overlay", height=max(320, 26 * len(order) + 80), bargap=0.25,
        margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(categoryorder="array", categoryarray=order[::-1], title=None),
        xaxis=dict(title="Lap", gridcolor="rgba(128,128,128,0.15)"),
        legend=dict(orientation="h", y=1.04, x=0, font=dict(size=11)),
    )
    return fig


def build_degradation_figure(clean: pd.DataFrame, deg_frame: pd.DataFrame,
                             drivers: pd.DataFrame, selected: list[str]) -> go.Figure:
    """Fuel-corrected lap time vs tyre age with fitted lines, per driver & compound."""
    laps = an.fuel_correct(clean[clean["Driver"].isin(selected)])
    meta = drivers.set_index("Driver")
    symbols = {"SOFT": "circle", "MEDIUM": "square", "HARD": "diamond",
               "INTERMEDIATE": "triangle-up", "WET": "triangle-down"}
    fig = go.Figure()
    for (d, comp), g in laps.groupby(["Driver", "Compound"]):
        color = meta.at[d, "Color"] if d in meta.index else "#888"
        fig.add_trace(go.Scatter(
            x=g["TyreLife"], y=g["FuelCorrectedLapTime"], mode="markers",
            name=f"{d} · {comp.title()}", legendgroup=f"{d}{comp}",
            marker=dict(color=color, size=8, symbol=symbols.get(comp, "circle"), opacity=0.75,
                        line=dict(width=1, color="white")),
            customdata=np.stack([g["LapNumber"], g["CleanAir"].map({True: "clean air", False: "traffic"})], axis=-1),
            hovertemplate=f"<b>{d}</b> {comp.title()} · age %{{x}}<br>%{{y:.3f}} s (fuel-corr.)"
                          "<br>Lap %{customdata[0]} · %{customdata[1]}<extra></extra>",
        ))
        fit = deg_frame[(deg_frame["Driver"] == d) & (deg_frame["Compound"] == comp)]
        if not fit.empty:
            m = fit.iloc[0]
            xs = np.array([g["TyreLife"].min(), g["TyreLife"].max()])
            fig.add_trace(go.Scatter(
                x=xs, y=m["base_pace"] + m["deg_rate"] * xs, mode="lines", legendgroup=f"{d}{comp}",
                showlegend=False, line=dict(color=color, width=2,
                                            dash="dot" if meta.at[d, "IsSecondDriver"] else "solid"),
                hovertemplate=f"{d} {comp.title()} fit: {m['deg_rate']:+.3f} s/lap<extra></extra>",
            ))
    fig.update_layout(
        height=520, margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(title="Tyre age (laps)", gridcolor="rgba(128,128,128,0.15)"),
        yaxis=dict(title="Fuel-corrected lap time (s)", gridcolor="rgba(128,128,128,0.15)"),
        legend=dict(orientation="h", y=-0.22, font=dict(size=10)),
    )
    return fig


def build_excel(bundle: dict) -> bytes:
    """Workbook for the debrief pack: report, stints, degradation, timeline."""
    report_rows = []
    for r in bundle["reports"]:
        for s in r["stints"]:
            c = s["cliff"] or {}
            report_rows.append({
                "Position": r["position"], "Driver": r["driver"], "Strategy": r["strategy"],
                "Stint": s["stint"], "Compound": s["compound"], "First lap": s["first"],
                "Last lap": s["last"], "Laps": s["laps"], "Clean laps": s["clean_laps"],
                "Deg (s/lap)": s["deg_rate"], "Cliff lap": c.get("lap"),
                "Cliff cause": c.get("cause"), "Time lost (s)": c.get("time_lost"),
            })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        pd.DataFrame(report_rows).to_excel(xl, sheet_name="Post-Mortem", index=False)
        bundle["deg_frame"].to_excel(xl, sheet_name="Degradation", index=False)
        bundle["stints"].to_excel(xl, sheet_name="Stints", index=False)
        bundle["timeline"].to_excel(xl, sheet_name="Timeline", index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.title("📝 Race Recap")
active = de.render_session_selector()
if not active:
    st.info("Pick a season, Grand Prix and session in the sidebar, then press **Load session**.")
    st.stop()
active = de.page_session(active, "race")
if de.load_active_session(active) is None:
    st.stop()
try:
    with st.spinner("Building recap…"):
        B = recap_bundle(*active)
except Exception as exc:  # noqa: BLE001 — never crash the debrief screen
    st.error(f"Could not build the recap for this session: {exc}")
    st.stop()

info, drivers = B["info"], B["info"]["drivers"]
classified = drivers[drivers["ClassifiedPosition"].astype(str).str.isdigit()] \
    if "ClassifiedPosition" in drivers else drivers
winner = classified.iloc[0]["Driver"] if not classified.empty else "—"
tyre_cliffs = [(r["driver"], c) for r in B["reports"] for c in r["cliffs"] if c["cause"] == "tyre"]
weather_drops = [(r["driver"], c) for r in B["reports"] for c in r["cliffs"] if c["cause"] == "weather"]

st.markdown(f"{de.session_badge(active[2])} · {info['year']} {info['event_name']} · {info['location']}"
            + (" · a sprint: about a third of the distance, no compulsory stop, points to the top eight"
               if active[2] == "S" else ""))
m = st.columns(5)
m[0].metric("Winner", winner)
m[1].metric("Laps", info["total_laps"])
m[2].metric("SC / VSC laps", f"{len(info['neutralised']['SC'])} / {len(info['neutralised']['VSC'])}")
m[3].metric("Tyre cliffs found", len(tyre_cliffs))
m[4].metric("Retirements", int(drivers["DNF"].sum()))

tab_replay, tab_pm, tab_deg, tab_wx, tab_pen, tab_export = st.tabs(
    ["🎬 Race Replay", "📝 Post-Mortem", "📉 Degradation", "🌦️ Weather", "🚩 Penalties", "⬇️ Export"])

# --- Replay -----------------------------------------------------------------
with tab_replay:
    if st.session_state.get(REPLAY_KEY) != active:
        st.markdown("Every car on the circuit, replayed from its telemetry: running order with tyres "
                    "and gaps, speed, gear, throttle, brake and DRS for the drivers you pick, flags, "
                    "weather and race-control messages. Car telemetry is a separate download "
                    "(about 20 s the first time, then cached).")
        if st.button("Load track replay", type="primary"):
            st.session_state[REPLAY_KEY] = active
            st.rerun()
    else:
        try:
            with st.spinner("Building the replay from car telemetry…"):
                payload = tm.replay_payload(*active)
        except de.DataUnavailableError as exc:
            st.warning(f"No car telemetry for this session, so no track replay: {exc}")
        except Exception as exc:  # noqa: BLE001 — keep the debrief screen alive
            st.error(f"Could not build the track replay: {exc}")
        else:
            st.iframe(tm.replay_html(payload), height=REPLAY_HEIGHT)
            st.caption("Positions come from F1's car position feed (about 4 per second, smoothed "
                       "between). Running order: laps completed plus how far round the lap each car "
                       "is. Gaps are behind the leader at the same point on track.")

    with st.expander("Gap to leader, lap by lap"):
        c1, c2 = st.columns([3, 1])
        with c2:
            all_drivers = drivers["Driver"].tolist()
            picked = st.multiselect("Drivers", all_drivers, default=all_drivers, key="replay_drivers")
            y_cap = st.slider("Max gap shown (s)", 10, 180, 80, step=5,
                              help="Lapped cars run off the bottom of the chart beyond this gap.")
            st.caption("Dotted line = second car of a team. ▼ = pit stop. ✕ = retirement. "
                       "Amber bands = SC/VSC.")
        with c1:
            if picked:
                fig = build_replay_figure(B["timeline"][B["timeline"]["Driver"].isin(picked)],
                                          drivers[drivers["Driver"].isin(picked)],
                                          info["neutralised"], float(y_cap))
                st.plotly_chart(fig, width="stretch", theme="streamlit")
            else:
                st.info("Select at least one driver.")

# --- Post-mortem --------------------------------------------------------------
with tab_pm:
    st.markdown("##### Tyre strategy")
    st.plotly_chart(build_stint_chart(B["stints"], B["reports"], drivers),
                    width="stretch", theme="streamlit")

    st.markdown("##### Headlines")
    heads = []
    for d, c in sorted(tyre_cliffs, key=lambda x: -x[1]["time_lost"])[:3]:
        heads.append(f"⚠️ **{d}** fell off the cliff on **Lap {c['lap']}** ({c['compound'].title()}, "
                     f"age {c['tyre_life']}) — ≈{c['time_lost']:.1f} s lost.")
    if weather_drops:
        laps = sorted({c["lap"] for _, c in weather_drops})
        heads.append(f"🌧️ {len(weather_drops)} pace drops coincided with rainfall "
                     f"(laps {', '.join(map(str, laps[:6]))}{'…' if len(laps) > 6 else ''}) "
                     "and are attributed to weather, not tyre wear.")
    dfm = B["deg_frame"]
    # Only rank credible fits: enough laps and a non-negative slope (a negative
    # slope means track evolution / drying dominated, not good tyre management).
    credible = dfm[dfm["Compound"].isin(config.DRY_COMPOUNDS) & (dfm["n_laps"] >= 8) & (dfm["deg_rate"] >= 0)]
    for comp, g in credible.groupby("Compound"):
        best = g.loc[g["deg_rate"].idxmin()]
        heads.append(f"Best {comp.title()} management: **{best['Driver']}** "
                     f"({best['deg_rate']:+.3f} s/lap vs field median {g['deg_rate'].median():+.3f}).")
    st.markdown("\n".join(f"- {h}" for h in heads) or "_No notable events detected._")

    st.markdown("##### Driver reports")
    focus = st.selectbox("Driver", ["All drivers"] + [r["driver"] for r in B["reports"]])
    for r in B["reports"]:
        if focus in ("All drivers", r["driver"]):
            with st.container(border=True):
                st.markdown(r["markdown"])

# --- Degradation --------------------------------------------------------------
with tab_deg:
    default = classified["Driver"].head(3).tolist()
    sel = st.multiselect("Drivers to compare", drivers["Driver"].tolist(), default=default,
                         key="deg_drivers")
    if sel:
        st.plotly_chart(build_degradation_figure(B["clean"], dfm, drivers, sel),
                        width="stretch", theme="streamlit")
        st.caption(f"Lap time corrected by +{config.FUEL_EFFECT_PER_LAP} s × (lap − 1) to a "
                   "full-tank equivalent. Fits use clean-air laps when there are enough of them.")
    st.dataframe(
        dfm[dfm["Driver"].isin(sel) if sel else slice(None)].sort_values(["Compound", "deg_rate"]),
        hide_index=True, width="stretch",
        column_config={
            "base_pace": st.column_config.NumberColumn("Base pace (s)", format="%.3f"),
            "deg_rate": st.column_config.NumberColumn("Deg (s/lap)", format="%+.3f"),
            "r_squared": st.column_config.ProgressColumn("R²", min_value=0.0, max_value=1.0, format="%.2f"),
            "n_laps": "Laps", "max_tyre_life": "Max age", "sample": "Sample",
        },
    )

# --- Weather ------------------------------------------------------------------
with tab_wx:
    lap_wx, fc_laps, fc_error = recap_weather(*active)
    if lap_wx.empty or lap_wx[["AirTemp", "TrackTemp"]].isna().all().all():
        st.info("No weather feed for this session (timing-only load).")
    else:
        wet_laps = int(lap_wx["Rainfall"].sum())
        w = st.columns(4)
        w[0].metric("Track temperature", f"{lap_wx['TrackTemp'].min():.0f}–{lap_wx['TrackTemp'].max():.0f} °C")
        w[1].metric("Air temperature", f"{lap_wx['AirTemp'].min():.0f}–{lap_wx['AirTemp'].max():.0f} °C")
        w[2].metric("Laps with rain", wet_laps)
        if fc_laps is not None and fc_laps["rain_prob"].notna().any():
            w[3].metric("Forecast rain chance (max)", f"{fc_laps['rain_prob'].max():.0f}%")
        st.plotly_chart(wx.build_weather_figure(lap_wx, fc_laps), width="stretch", theme="streamlit")
        st.caption("Lines and blue bands: the circuit's own sensors at the moment the leader finished "
                   "each lap. Bars: Open-Meteo's archived hourly forecast for the hour each lap was run "
                   f"in. {wx.CREDIT}." + (f" (Forecast unavailable: {fc_error}.)" if fc_error else ""))

# --- Penalties ----------------------------------------------------------------
with tab_pen:
    dec, pu_rows, limits, last_round = weekend_penalties(info["year"], info["round"])
    st.markdown("Every stewards' decision of the weekend, all sessions "
                "([f1penalties.com](https://www.f1penalties.com/data)), and the power-unit elements each car "
                "fitted (the FIA Technical Delegate's documents). A new element past the season's allocation "
                "costs grid places in the Grand Prix: 10 for the first of a kind, 5 for each one after, the back "
                "of the grid past 15.")
    if dec.empty:
        st.info("f1penalties.com hasn't added this round yet" + (f" (it has up to round {last_round})" if last_round else "")
                + ": it runs a few days to a few weeks behind the FIA.")
    else:
        only = st.toggle("Penalties only (hide no further action and warnings)", value=True)
        show = dec[~dec["outcome"].astype(str).str.match(r"(No Further Action|Warning|Not Investigated|Noted)")] if only else dec
        order = {s: i for i, s in enumerate(["FP1", "FP2", "FP3", "SQ", "S", "Q", "R"])}
        show = show.assign(_o=show["session"].map(order)).sort_values("_o")
        table = (show[["session", "driver", "team", "allegation", "involving", "outcome", "time_s", "grid",
                       "points", "fine", "notes"]]
                 .rename(columns={"session": "Session", "driver": "Driver", "team": "Team", "allegation": "Offence",
                                  "involving": "With", "outcome": "Outcome", "time_s": "Time (s)", "grid": "Grid",
                                  "points": "Points", "fine": "Fine (€)", "notes": "Notes"})
                 .astype({"Grid": str}).replace({"Grid": {"None": "", "back": "Back of grid", "pit": "Pit lane"}}))
        colors = drivers.set_index("Driver")["Color"]
        st.dataframe(teams.with_badges(table, dict(zip(table["Team"], table["Driver"].map(colors))), info["year"]),
                     hide_index=True, width="stretch", column_config=teams.badge_column())
    if pu_rows.empty:
        st.info("No FIA power-unit documents for this round (yet).")
    else:
        els = [e for e in ("ICE", "TC", "MGU-H", "MGU-K", "ES", "CE", "EX", "ANC") if e in limits]
        fitted = pu_rows[pu_rows["fitted"].map(lambda f: any(f.values()))]
        if fitted.empty:
            st.caption("No new power-unit elements this weekend.")
        else:
            st.markdown("**New power-unit elements**")
            st.dataframe(pd.DataFrame({
                "Car": fitted["car"], "Driver": fitted["who"],
                "Fitted": fitted["fitted"].map(lambda f: ", ".join(f"{e}×{n}" if n > 1 else e for e, n in f.items() if n)),
                "Grid penalty": fitted["drop"].map(lambda d: "" if d is None else "Back of grid" if d == "back" else f"{d} places"),
            }), hide_index=True, width="stretch")
        st.markdown(f"**Elements used after this round** (allocation: {', '.join(f'{e} {limits[e]}' for e in els)})")
        after = pd.DataFrame([{"Driver": w, **{e: a.get(e, 0) for e in els}} for w, a in zip(pu_rows["who"], pu_rows["after"])])
        st.dataframe(after.style.apply(lambda col: ["color: #e66767; font-weight: 600" if col.name in limits and v > limits[col.name]
                                                    else "font-weight: 600" if col.name in limits and v == limits[col.name] else ""
                                                    for v in col], axis=0),
                     hide_index=True, width="stretch")
        st.caption("Red: past the allocation; bold: at it. The website's Next Race page has each driver's chance "
                   "of a power-unit penalty at every Grand Prix left.")

# --- Export -------------------------------------------------------------------
with tab_export:
    st.download_button(
        "⬇️ Download debrief workbook (.xlsx)", data=build_excel(B),
        file_name=f"{info['year']}_{info['event_name'].replace(' ', '_')}_{active[2]}_recap.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.markdown("##### LLM-ready debrief prompt")
    st.caption("Structured facts from this page. Paste into your team's approved LLM for a narrative write-up.")
    st.code(an.build_llm_prompt(info, B["reports"], dfm), language="text")
