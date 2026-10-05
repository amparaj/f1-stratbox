"""
pages/5_Telemetry.py — lap head-to-head from car telemetry, for any session.

Two laps (any driver, any lap; the two fastest by default) on one distance axis:
  - cards: lap time, gap, tyre, and the share of the lap flat out / braking / cornering
  - track map coloured by who was faster through each corner zone and straight
  - stacked traces: speed (corner zones shaded by speed class, time won in each above),
    time delta, throttle, brake, RPM, gear and DRS, with every corner marked
  - the corner-zone table: minimum speeds and time gained

Telemetry is FastF1's car data (modules/telemetry.py); the first load of a session downloads
it (~15–30 s), then it's cached on disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # allow `import config`

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import config
from modules import data_engine as de
from modules import telemetry as tm

CLASS_SHADE = {"Low speed": "rgba(128,128,128,0.22)", "Medium speed": "rgba(128,128,128,0.13)",
               "High speed": "rgba(128,128,128,0.06)"}


def lap_time(s: float | None) -> str:
    """92.456 -> '1:32.456'."""
    if s is None or pd.isna(s):
        return "–"
    m = int(s // 60)
    return f"{m}:{s - 60 * m:06.3f}"


def lap_label(opts: list[tuple[int, float]]):
    best = opts[0][0] if opts else None
    times = dict(opts)
    return lambda n: f"Lap {n} · {lap_time(times[n])}" + (" · fastest" if n == best else "")


def driver_style(drivers: pd.DataFrame, a: str, b: str) -> tuple[dict, dict]:
    """Line styles: team colours; if both share one (teammates, or the same driver), B is dotted."""
    meta = drivers.set_index("Driver")
    ca, cb = meta.at[a, "Color"], meta.at[b, "Color"]
    return dict(color=ca, dash="solid"), dict(color=cb, dash="dot" if ca == cb else "solid")


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def build_traces(ta: pd.DataFrame, cmp: dict, a: str, b: str, sa: dict, sb: dict) -> go.Figure:
    tr, zones, corners = cmp["trace"], cmp["zones"], cmp["corner_dist"]
    has_drs = bool(ta["DRS"].any() or tr["DRSB"].any())          # none from 2026 (active aero)
    rows = [("Speed (km/h)", 3.0), ("Delta (s)", 1.3), ("Throttle (%)", 1.2), ("Brake", 0.6),
            ("RPM", 1.2), ("Gear", 1.0)] + ([("DRS", 0.5)] if has_drs else [])
    fig = make_subplots(rows=len(rows), cols=1, shared_xaxes=True, vertical_spacing=0.018,
                        row_heights=[h for _, h in rows])
    d = ta["Distance"]

    def pair(row: int, ya, yb, shape: str = "linear", fmt: str = ".0f") -> None:
        for name, y, style in ((a, ya, sa), (b, yb, sb)):
            fig.add_trace(go.Scatter(x=d, y=y, name=name, legendgroup=name, showlegend=row == 1,
                                     line=dict(width=1.6, shape=shape, **style),
                                     hovertemplate=f"{name} %{{y:{fmt}}}<extra></extra>"), row=row, col=1)

    pair(1, ta["Speed"], tr["SpeedB"])
    fig.add_trace(go.Scatter(x=d, y=tr["DeltaB"], name="Delta", showlegend=False,
                             line=dict(width=1.6, color="#888"), fill="tozeroy",
                             fillcolor="rgba(136,136,136,0.15)",
                             hovertemplate=f"{b} %{{y:+.3f}} s vs {a}<extra></extra>"), row=2, col=1)
    fig.add_hline(y=0, line=dict(width=1, color="rgba(128,128,128,0.6)"), row=2, col=1)
    pair(3, ta["Throttle"], tr["ThrottleB"])
    pair(4, ta["Brake"], (tr["BrakeB"] > 0.5).astype(float), shape="hv")
    pair(5, ta["RPM"], tr["RPMB"])
    pair(6, ta["Gear"], tr["GearB"].round(), shape="hv")
    if has_drs:
        pair(7, ta["DRS"].astype(float), tr["DRSB"], shape="hv")

    # Corner zones shaded by speed class on the speed trace, with the time won in each above it.
    top = float(max(ta["Speed"].max(), tr["SpeedB"].max())) * 1.12
    for z in zones.itertuples():
        if z.Kind == "corner":
            fig.add_vrect(x0=z.Start, x1=z.End, fillcolor=CLASS_SHADE.get(z.Class, "rgba(0,0,0,0)"),
                          line_width=0, layer="below", row=1, col=1)
        if abs(z.Delta) >= 0.02:                                  # the zones that decided the lap
            colour = sa["color"] if z.Delta > 0 else sb["color"]
            fig.add_annotation(x=(z.Start + z.End) / 2, y=top, text=f"{abs(z.Delta):.3f}",
                               showarrow=False, font=dict(size=10, color=colour), row=1, col=1)
    for label, dist in corners.items():
        fig.add_vline(x=dist, line=dict(width=1, color="rgba(128,128,128,0.35)"))
        fig.add_annotation(x=dist, y=1.0, yref="paper", yanchor="bottom", text=label, showarrow=False,
                           font=dict(size=10), bgcolor="rgba(128,128,128,0.25)", borderpad=2)
    for i, (title, _) in enumerate(rows, start=1):
        fig.update_yaxes(title_text=title, title_font=dict(size=11), row=i, col=1,
                         gridcolor="rgba(128,128,128,0.15)", zeroline=False)
    fig.update_yaxes(range=[0, top * 1.05], row=1, col=1)
    fig.update_yaxes(tickvals=[0, 1], ticktext=["off", "on"], range=[-0.15, 1.15], row=4, col=1)
    if has_drs:
        fig.update_yaxes(tickvals=[0, 1], ticktext=["shut", "open"], range=[-0.15, 1.15], row=7, col=1)
    fig.update_xaxes(showgrid=False)
    fig.update_xaxes(title_text="Distance from the line (m)", row=len(rows), col=1)
    fig.update_layout(height=980 if has_drs else 920, margin=dict(l=10, r=10, t=40, b=10),
                      hovermode="x unified", legend=dict(orientation="h", y=1.04, x=1, xanchor="right"))
    return fig


def build_map(ta: pd.DataFrame, cmp: dict, corners: pd.DataFrame, a: str, b: str,
              sa: dict, sb: dict) -> go.Figure:
    """A's line round the track, each zone in the colour of whoever was faster through it."""
    fig = go.Figure()
    d = ta["Distance"].to_numpy()
    for z in cmp["zones"].itertuples():
        m = (d >= z.Start - 5) & (d <= z.End + 5)
        if not m.any():
            continue
        style = sa if z.Delta >= 0 else sb
        who = a if z.Delta >= 0 else b
        fig.add_trace(go.Scatter(
            x=ta["X"][m], y=ta["Y"][m], mode="lines", showlegend=False,
            line=dict(color=style["color"], width=7, dash=style["dash"]),
            hovertemplate=f"{z.Label} · {z.Class}<br>{who} faster by {abs(z.Delta):.3f} s<extra></extra>"))
    for name, style in ((a, sa), (b, sb)):                       # legend entries
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", name=f"{name} faster",
                                 line=dict(color=style["color"], width=7, dash=style["dash"])))
    if not corners.empty:
        cx, cy = corners["X"].to_numpy(), corners["Y"].to_numpy()
        mx, my = float(ta["X"].mean()), float(ta["Y"].mean())
        r = np.hypot(cx - mx, cy - my) + 1e-9
        span = float(max(ta["X"].max() - ta["X"].min(), ta["Y"].max() - ta["Y"].min()))
        fig.add_trace(go.Scatter(x=cx + (cx - mx) / r * span * 0.045, y=cy + (cy - my) / r * span * 0.045,
                                 mode="text", text=corners["Label"], textfont=dict(size=10),
                                 showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=[ta["X"].iloc[0]], y=[ta["Y"].iloc[0]], mode="markers", showlegend=False,
                             marker=dict(symbol="square", size=9, color="#888"), hovertemplate="Start/finish<extra></extra>"))
    fig.update_xaxes(visible=False, scaleanchor="y", scaleratio=1)
    fig.update_yaxes(visible=False)
    fig.update_layout(height=430, margin=dict(l=0, r=0, t=10, b=0),
                      legend=dict(orientation="h", y=-0.02, x=0.5, xanchor="center"))
    return fig


def build_zone_bars(zones: pd.DataFrame, a: str, b: str, sa: dict, sb: dict) -> go.Figure:
    """Time won in each zone, lap order top to bottom; bars point to whoever won it."""
    z = zones[(zones["End"] - zones["Start"]) > 30].reset_index(drop=True)
    labels = [f"{r.Label} ({r.Class.split()[0].lower()})" if r.Kind == "corner" else f"Straight {i}"
              for i, r in enumerate(z.itertuples(), start=1)]
    labels = [f"{k + 1:02d}. {lab}" for k, lab in enumerate(labels)]
    fig = go.Figure(go.Bar(
        y=labels, x=-z["Delta"], orientation="h",
        marker=dict(color=[sa["color"] if v >= 0 else sb["color"] for v in z["Delta"]],
                    pattern=dict(shape=["" if v >= 0 or sa["color"] != sb["color"] else "/" for v in z["Delta"]])),
        hovertemplate="%{y}: %{x:+.3f} s<extra></extra>"))
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=10))
    fig.update_xaxes(title_text=f"← {a} faster · time (s) · {b} faster →", zeroline=True,
                     zerolinecolor="rgba(128,128,128,0.6)", gridcolor="rgba(128,128,128,0.15)")
    fig.update_layout(height=max(300, 22 * len(z) + 80), margin=dict(l=10, r=10, t=10, b=10))
    return fig


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.title("📈 Telemetry")
active = de.render_session_selector()
if not active:
    st.info("Pick a season, Grand Prix and session in the sidebar, then press **Load session**.")
    st.stop()
info = de.load_active_session(active)
if info is None:
    st.stop()

drivers = info["drivers"]
laps = de.get_all_laps(*active)
options = {d: tm.driver_lap_options(laps, d) for d in drivers["Driver"]}
ranked = sorted((o[0][1], d) for d, o in options.items() if o)
if len(ranked) < 1:
    st.warning("No timed laps in this session.")
    st.stop()
codes = [d for _, d in ranked]                       # quickest first
default_a = codes[0]
default_b = codes[1] if len(codes) > 1 else codes[0]
names = drivers.set_index("Driver")["FullName"].to_dict() if "FullName" in drivers else {}

st.markdown(f"{de.session_badge(active[2])} · {info['year']} {info['event_name']} · {info['location']}"
            " · any two laps from this session, on one distance axis")
key = "|".join(map(str, active))
c = st.columns(4)
a = c[0].selectbox("Driver A", codes, index=codes.index(default_a), key=f"tel_a|{key}",
                   format_func=lambda d: f"{d} · {names.get(d, d)}")
lap_a = c[1].selectbox("Lap A", [n for n, _ in options[a]], key=f"tel_la|{key}|{a}",
                       format_func=lap_label(options[a]))
b = c[2].selectbox("Driver B", codes, index=codes.index(default_b), key=f"tel_b|{key}",
                   format_func=lambda d: f"{d} · {names.get(d, d)}")
lap_b = c[3].selectbox("Lap B", [n for n, _ in options[b]], key=f"tel_lb|{key}|{b}",
                       format_func=lap_label(options[b]))
if a == b and lap_a == lap_b:
    st.info("Pick two different laps (the same driver's two laps works too).")
    st.stop()

try:
    with st.spinner("Loading car telemetry — the first time for a session downloads it (about 20 s)…"):
        ta = tm.lap_trace(*active, a, int(lap_a))
        tb = tm.lap_trace(*active, b, int(lap_b))
        corners = tm.session_corners(*active)
except de.DataUnavailableError as exc:
    st.warning(f"No car telemetry for this session: {exc}")
    st.stop()
except Exception as exc:  # noqa: BLE001 — keep the page alive
    st.error(f"Could not load the telemetry for these laps: {exc}")
    st.stop()

cmp = tm.compare_laps(ta, tb, corners)
sa, sb = driver_style(drivers, a, b)
gap = tb.attrs["lap_time"] - ta.attrs["lap_time"]
team = drivers.set_index("Driver")["Team"].to_dict()

# --- Cards ------------------------------------------------------------------------------
cards = st.columns(2)
for col, drv, tr, style, sign, who in ((cards[0], a, ta, sa, -1, "A"), (cards[1], b, tb, sb, 1, "B")):
    sh = cmp["shares"][who]
    with col, st.container(border=True):
        tyre = tr.attrs["compound"].title() + (f", {tr.attrs['tyre_life']} laps old" if tr.attrs["tyre_life"] else "")
        delta = sign * gap
        delta_col = config.STATUS_COLORS["good"] if delta < 0 else config.STATUS_COLORS["critical"]
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:8px'><span style='width:6px;height:20px;"
            f"background:{style['color']};border-radius:2px'></span><b>{names.get(drv, drv)}</b>"
            f"<span style='opacity:.7'>{team.get(drv, '')}</span></div>"
            f"<div style='margin:6px 0 2px;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px'>"
            f"<span style='font-size:1.9rem;font-weight:700;font-variant-numeric:tabular-nums'>"
            f"{lap_time(tr.attrs['lap_time'])}</span>"
            + (f"<span style='color:{delta_col};font-weight:600'>{'▼' if delta < 0 else '▲'} {delta:+.3f} s</span>"
               if gap else "")
            + f"</div><div style='opacity:.8;font-size:.9rem'>Lap {tr.attrs['lap_number']} · {tyre} · "
            f"top speed {sh['top_speed']:.0f} km/h</div>",
            unsafe_allow_html=True)
        st.progress(min(1.0, sh["full_throttle"] / 100), text=f"Full throttle · {sh['full_throttle']:.0f}% of the lap")
        st.progress(min(1.0, sh["braking"] / 100), text=f"Braking · {sh['braking']:.0f}%")
        st.progress(min(1.0, sh["cornering"] / 100), text=f"Cornering and part throttle · {sh['cornering']:.0f}%")

# --- Map + zones --------------------------------------------------------------------------
left, right = st.columns([5, 4])
with left:
    st.markdown("##### Who was faster where")
    st.plotly_chart(build_map(ta, cmp, corners, a, b, sa, sb), width="stretch", theme="streamlit")
with right:
    st.markdown("##### Time won, zone by zone")
    st.plotly_chart(build_zone_bars(cmp["zones"], a, b, sa, sb), width="stretch", theme="streamlit")

# --- Traces -------------------------------------------------------------------------------
st.markdown("##### Lap traces")
st.plotly_chart(build_traces(ta, cmp, a, b, sa, sb), width="stretch", theme="streamlit")
st.caption(f"Delta: how far {b} is behind {a} at each point of the lap (rising = {a} gaining). "
           f"{b}'s distance is scaled to {a}'s lap length, so the delta ends at the lap-time gap. "
           "Shaded bands on the speed trace are corner zones (darker = slower corner); the numbers above "
           "them are the time won there, in the winner's colour. Full throttle = at least "
           f"{config.FULL_THROTTLE_PCT}%.")

zones = cmp["zones"]
cz = zones[zones["Kind"] == "corner"]
if not cz.empty:
    st.markdown("##### Corners")
    table = pd.DataFrame({
        "Corner": cz["Label"], "Type": cz["Class"],
        f"Min speed {a}": cz["MinA"].round(0), f"Min speed {b}": cz["MinB"].round(0),
        "Faster": np.where(cz["Delta"] >= 0, a, b), "Time won (s)": cz["Delta"].abs().round(3),
    })
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"Time won (s)": st.column_config.NumberColumn(format="%.3f")})
