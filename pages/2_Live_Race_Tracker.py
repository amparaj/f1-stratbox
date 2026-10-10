"""
pages/2_Live_Race_Tracker.py — the live timing board, for following a session on TV.

Live        F1's live-timing feed (modules/live.py), no login: a timing tower for whatever session is on
            (practice, qualifying, sprint qualifying, sprint, Grand Prix), track status, race control,
            weather and the clock, refreshed every couple of seconds. The connection runs in a thread of
            the Streamlit server, starts when this page is open and stops ten minutes after it was last
            looked at. A delay holds the board back to match the TV picture (the timing usually runs
            a few seconds to half a minute ahead of the broadcast).
Race        battle map with pit-window shadows, pit rejoin forecast and undercut threats
            (modules/race_tracker.py) from the laps seen so far, plus the tyre strategies.
Qualifying  segment times, the knockout line and each car's margin to it.
Practice    best laps, the tyres each car has run and long runs as they build up (modules/practice.py).
Replay      any finished session from F1's archive on a virtual clock (1-60x): the same board, for
            checking the page between race weekends.

No car positions (the track map needs an F1 TV login). Race Recap's "Lap by Lap" tab has the old
lap-by-lap replay of a finished race from FastF1's timing.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # allow `import config`

import logging

import fastf1
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from modules import analytics as an
from modules import data_engine as de
from modules import live
from modules import practice as pr
from modules import race_tracker as rt

CURSOR_KEY = "live_cursor"
REPLAY_KEY = "live_replay"          # the replay clock: {"id", "anchor_wall", "anchor_t", "speed", "playing"}
LIVE_REFRESH_S = 2
REPLAY_REFRESH_S = 1

PURPLE, GREEN = "#a855f7", "#22c55e"
FLAG_BANNER = {"1": ("green", "GREEN FLAG"), "2": ("orange", "YELLOW FLAG"), "4": ("orange", "SAFETY CAR"),
               "5": ("red", "RED FLAG"), "6": ("orange", "VIRTUAL SAFETY CAR"), "7": ("orange", "VSC ENDING")}
FINISHED = ("Finished", "Finalised", "Ends")


@st.cache_resource(show_spinner=False)
def live_feed() -> live.LiveFeed:
    return live.LiveFeed()


@st.cache_resource(show_spinner=False, max_entries=2)
def replay_events(year: int, event: str, code: str) -> list:
    return live.archive_events(year, event, code)


@st.cache_data(show_spinner=False, ttl=3600)
def upcoming(n: int = 3) -> list[tuple[str, str, pd.Timestamp]]:
    """The sessions on now (started in the last 2 h) and next: (event, session name, start UTC)."""
    logging.getLogger("fastf1").setLevel(logging.ERROR)
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    out = []
    for year in (now.year, now.year + 1):
        try:
            sched = fastf1.get_event_schedule(year, include_testing=False)
        except Exception:  # noqa: BLE001
            continue
        for _, e in sched.iterrows():
            for i in range(1, 6):
                when = pd.to_datetime(e.get(f"Session{i}DateUtc"), errors="coerce")
                if pd.notna(when) and when > now - pd.Timedelta(hours=2):
                    out.append((e["EventName"], e[f"Session{i}"], when.tz_localize("UTC")))
        if len(out) >= n:
            break
    return sorted(out, key=lambda x: x[2])[:n]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def _gap_text(gap: float, laps_down: int, leader: bool) -> str:
    if leader:
        return "Leader"
    if laps_down:
        return f"+{laps_down} L"
    return f"+{gap:.3f}" if pd.notna(gap) else ""


def _tyre_text(row) -> str:
    comp = config.COMPOUND_SHORT.get(row["Compound"], "?")
    age = "" if pd.isna(row["TyreLife"]) else f" {int(row['TyreLife'])}"
    if row["FreshTyre"] is True and row["TyreLife"] == 0:
        return f"{comp} new"
    return f"{comp}{age}"


def _status_text(row, part: int = 0) -> str:
    if row["Retired"]:
        return "OUT"
    if row["Stopped"]:
        return "STOPPED"
    if row["KnockedOut"]:
        return "Knocked out"
    if row["InPit"]:
        return "In pit"
    if row["PitOut"]:
        return "Pit exit"
    return ""


def _compound_style(comp: str) -> str:
    bg = config.COMPOUND_COLORS.get(comp, config.COMPOUND_COLORS.get("UNKNOWN", "#888"))
    fg = "#fff" if comp in ("SOFT", "WET") else "#111"
    return f"background-color: {bg}; color: {fg}; font-weight: 600"


def styled_tower(tower: pd.DataFrame, kind: str, part: int, entries: list[int]) -> tuple[pd.io.formats.style.Styler,
                                                                                       dict]:
    """The timing tower as a Styler: team colour strip, purple/green times, compound colours, knockout zone."""
    t = tower.reset_index(drop=True)
    out = pd.DataFrame({"Pos": t["Pos"].where(t["Pos"] < 99, None), " ": "", "Driver": t["Driver"]})
    if kind == "race":
        out["Gap"] = [_gap_text(g, d, p == 1) for g, d, p in zip(t["Gap"], t["LapsDown"], t["Pos"])]
        out["Interval"] = [("" if p == 1 else f"+{d} L" if d else f"+{i:.3f}" if pd.notna(i) else "")
                           for i, d, p in zip(t["Interval"], t["IntervalLaps"], t["Pos"])]
        out["Last"] = t["Last"].map(live.fmt_laptime)
        out["Best"] = t["Best"].map(live.fmt_laptime)
    else:
        best = t["SegBest"] if kind == "quali" else t["Best"]
        fastest = best.min()
        out["Best"] = best.map(live.fmt_laptime)
        out["Gap"] = ["" if pd.isna(b) or b == fastest else f"+{b - fastest:.3f}" for b in best]
        if kind == "quali":
            out["To the cut"] = _cut_margins(t, best, part, entries)
        out["Last"] = t["Last"].map(live.fmt_laptime)
    for s in ("S1", "S2", "S3"):
        out[s] = t[s].map(lambda v: f"{v:.3f}" if pd.notna(v) else "")
    out["Tyre"] = [_tyre_text(r) for _, r in t.iterrows()]
    out["Laps"] = t["Laps"]
    if kind == "race":
        out["Pits"] = t["Pits"]
        out["Grid"] = [("" if g is None or pd.isna(g) or p == 99 else f"{int(g) - p:+d}" if int(g) != p else "=")
                       for g, p in zip(t["Grid"], t["Pos"])]
    out["Status"] = [_status_text(r, part) for _, r in t.iterrows()]

    flags = {"Last": t["LastFlag"], "S1": t["S1Flag"], "S2": t["S2Flag"], "S3": t["S3Flag"]}
    if kind == "race":
        best_flag = pd.Series(["purple" if b == t["Best"].min() else "" for b in t["Best"]])
        flags["Best"] = best_flag
    knocked = pd.Series(False, index=t.index)
    if kind == "quali" and part in (1, 2) and len(entries) > part:
        knocked = t["Pos"] > entries[part]
    knocked |= t["KnockedOut"]

    def style(df: pd.DataFrame) -> pd.DataFrame:
        css = pd.DataFrame("", index=df.index, columns=df.columns)
        css[" "] = [f"background-color: {c}" for c in t["Color"]]
        for col, fl in flags.items():
            css[col] = [f"color: {PURPLE}; font-weight: 700" if f == "purple"
                        else f"color: {GREEN}; font-weight: 600" if f == "green" else "" for f in fl]
        css["Tyre"] = [_compound_style(c) for c in t["Compound"]]
        css.loc[knocked, "Pos"] = "background-color: rgba(208,59,59,0.28)"
        css.loc[knocked, "Driver"] = "color: rgba(128,128,128,0.9)"
        css.loc[t["Retired"] | t["Stopped"], "Driver"] = "color: rgba(128,128,128,0.9); text-decoration: line-through"
        return css

    config_cols = {" ": st.column_config.TextColumn(" ", width=8),
                   "Pos": st.column_config.NumberColumn("Pos", width=40),
                   "Driver": st.column_config.TextColumn("Driver", width=60)}
    return out.style.apply(style, axis=None), config_cols


def _cut_margins(t: pd.DataFrame, best: pd.Series, part: int, entries: list[int]) -> list[str]:
    """Qualifying: how far inside (−) or outside (+) the knockout line each car's best lap is. The line is
    the time of the first car out (for those through) or the last car through (for those out)."""
    if part not in (1, 2) or len(entries) <= part:
        return [""] * len(t)
    through = entries[part]
    ranked = best.dropna().sort_values()
    if len(ranked) <= through:
        line_in = line_out = np.nan
    else:
        line_in, line_out = ranked.iloc[through], ranked.iloc[through - 1]
    out = []
    for i, b in best.items():
        if pd.isna(b) or t.at[i, "KnockedOut"]:
            out.append("")
        elif t.at[i, "Pos"] <= through:
            out.append("" if pd.isna(line_in) else f"safe by {line_in - b:.3f}")
        else:
            out.append("" if pd.isna(line_out) else f"needs {b - line_out:.3f}")
    return out


def stint_figure(stints: pd.DataFrame, order: list[str]) -> go.Figure:
    """Tyres run so far: one row per car, a bar per set, coloured by compound."""
    s = stints.copy()
    s["First"] = s.groupby("Driver")["Laps"].cumsum() - s["Laps"]
    fig = go.Figure()
    for comp in s["Compound"].unique():
        c = s[s["Compound"] == comp]
        fig.add_trace(go.Bar(
            y=c["Driver"], x=c["Laps"], base=c["First"], orientation="h", name=comp.title(),
            marker=dict(color=config.COMPOUND_COLORS.get(comp, config.COMPOUND_COLORS["UNKNOWN"]),
                        line=dict(width=2, color="rgba(255,255,255,0.9)")),
            text=[f"{config.COMPOUND_SHORT.get(comp, '?')}{'' if n else '*'}" for n in c["New"]],
            textposition="inside", insidetextanchor="middle", textangle=0, textfont=dict(color="#111", size=10),
            customdata=np.stack([c["Laps"], c["StartAge"], c["New"].map({True: "new", False: "used"})], axis=-1),
            hovertemplate="%{y} · " + comp.title() + ": %{customdata[0]} laps (set %{customdata[2]}, "
                          "%{customdata[1]} laps old at fitting)<extra></extra>",
        ))
    order = [d for d in order if d in set(s["Driver"])]
    fig.update_layout(barmode="overlay", height=max(320, 24 * len(order) + 80), bargap=0.25,
                      margin=dict(l=10, r=10, t=10, b=10),
                      yaxis=dict(categoryorder="array", categoryarray=order[::-1], title=None),
                      xaxis=dict(title="Laps", gridcolor="rgba(128,128,128,0.15)"),
                      legend=dict(orientation="h", y=1.04, x=0, font=dict(size=11)))
    return fig


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.title("📡 Live Race Tracker")
with st.sidebar:
    st.markdown("### 📡 Feed")
    source = st.radio("Source", ["Live", "Replay a finished session"], key="live_source",
                      help="Live: F1's live timing, for the session on now. Replay: a finished session from "
                           "F1's archive, on a clock you control (for trying the page between race weekends).")
    delay = st.number_input("Delay to match the TV (s)", 0, 300, 0, 1, key="live_delay",
                            help="The timing usually runs a few seconds to half a minute ahead of the "
                                 "broadcast. Hold the board back by this much so it doesn't spoil what's "
                                 "coming on screen.")

active = None
if source != "Live":
    active = de.render_session_selector()
    if not active:
        st.info("Pick a season, Grand Prix and session in the sidebar, then press **Load session**.")
        st.stop()
    with st.spinner("Downloading the session's timing from F1's archive (first time only, about 30 s)…"):
        try:
            events = replay_events(*active)
        except live.LiveUnavailable as exc:
            st.error(f"No replay: {exc}")
            st.stop()
    start, end = live.session_start(events), live.session_end(events)
    rid = f"{active}"
    clock = st.session_state.get(REPLAY_KEY)
    if not clock or clock.get("id") != rid:
        clock = {"id": rid, "anchor_wall": time.time(), "anchor_t": start - 30, "speed": 1, "playing": True}
        st.session_state[REPLAY_KEY] = clock

    def replay_now() -> float:
        c = st.session_state[REPLAY_KEY]
        t = c["anchor_t"] + ((time.time() - c["anchor_wall"]) * c["speed"] if c["playing"] else 0.0)
        return min(t, end + 120)

    def _reanchor(**changes) -> None:
        c = st.session_state[REPLAY_KEY]
        c["anchor_t"], c["anchor_wall"] = replay_now(), time.time()
        c.update(changes)

    def _seek() -> None:
        c = st.session_state[REPLAY_KEY]
        c["anchor_t"], c["anchor_wall"] = start + 60 * st.session_state["live_seek"], time.time()

    with st.sidebar:
        st.markdown("### ⏯️ Replay")
        c1, c2 = st.columns(2)
        c1.button("Pause" if clock["playing"] else "Play", width="stretch",
                  on_click=lambda: _reanchor(playing=not st.session_state[REPLAY_KEY]["playing"]))
        c2.button("Restart", width="stretch", on_click=lambda: _reanchor() or
                  st.session_state[REPLAY_KEY].update(anchor_t=start - 30, anchor_wall=time.time()))
        st.select_slider("Speed", [1, 2, 5, 10, 30, 60], value=clock["speed"], key="live_speed",
                         format_func=lambda v: f"{v}×",
                         on_change=lambda: _reanchor(speed=st.session_state["live_speed"]))
        st.slider("Jump to (minutes after the start)", 0, max(1, int((end - start) / 60) + 1),
                  int(max(0, replay_now() - start) // 60), key="live_seek", on_change=_seek)
        st.divider()

with st.sidebar:
    st.markdown("### 🛠️ Pit wall")
    fresh = st.selectbox("Undercut tyre for car behind", ["HARD", "MEDIUM", "SOFT", "INTERMEDIATE"],
                         key="live_fresh")
    respond = st.slider("Laps before car ahead can respond", 1, 3, 1, key="live_respond")
    pit_override = st.number_input("Green-flag pit loss (s, 0 = circuit default)", 0.0, 40.0, 0.0, 0.5,
                                   key="live_pit_loss")


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
def board_view() -> None:
    if source == "Live":
        feed = live_feed()
        feed.ensure_running()
        generation, evs = feed.view()
        now, source_id = time.time(), "live"
    else:
        feed, generation, evs = None, 0, events
        now, source_id = replay_now(), rid
    cutoff = now - delay

    cur = st.session_state.get(CURSOR_KEY)
    if cur is None or cur.source_id != source_id:
        cur = live.BoardCursor(source_id)
        st.session_state[CURSOR_KEY] = cur
    board = cur.advance(evs, cutoff, generation)
    info = board.session()

    # --- connection / clock line
    if feed is not None:
        age = time.time() - feed.last_message if feed.last_message else None
        state = {"connected": "🟢 connected", "connecting": "connecting…", "reconnecting": "🟠 reconnecting…",
                 "stopped": "stopped", "idle": "starting…"}.get(feed.status, feed.status)
        bits = [f"F1 live timing: {state}"]
        if age is not None:
            bits.append(f"last update {age:.0f} s ago")
        if delay:
            bits.append(f"board held back {delay} s")
        if feed.error and feed.status != "connected":
            bits.append(f"({feed.error})")
        st.caption(" · ".join(bits))
    else:
        c = st.session_state[REPLAY_KEY]
        st.caption(f"Replay from F1's archive · {pd.Timestamp(cutoff, unit='s', tz='UTC'):%H:%M:%S} UTC · "
                   f"{_clock(max(0.0, cutoff - start))} after the start · "
                   f"{c['speed']}× {'playing' if c['playing'] else 'paused'}")

    if not info.get("key"):
        st.info("Waiting for F1 live timing… The board fills in as soon as the feed sends the session.")
        _next_sessions()
        return

    kind = "race" if info["type"] == "Race" else "quali" if info["type"] == "Qualifying" else "practice"
    part, entries = board.quali_part()
    flag_code, flag_name = board.track_status()
    status = info["status"]
    seg = ("Q" if info["code"] == "Q" else "SQ") + str(part)
    # Qualifying sends "Finished" at the end of each segment: only the last one ends the session.
    between = kind == "quali" and status == "Finished" and 0 < part < 3
    over = status in FINISHED and not between

    head = f"{de.session_badge(info['code'])} · **{info['event']}** · {info['location']}"
    if kind == "quali" and part:
        head += f" · **{seg}**"
    st.markdown(head)
    colour, label = FLAG_BANNER.get(flag_code, ("gray", flag_name.upper() or "—"))
    if between:
        colour, label = "gray", f"END OF {seg}"
    elif over:
        colour, label = "gray", "CHEQUERED FLAG" if status in ("Finished", "Finalised") else "SESSION ENDED"
    elif status in ("Inactive", ""):
        colour, label = "gray", "NOT STARTED"
    elif status == "Aborted":
        colour, label = "red", "SESSION SUSPENDED"
    st.markdown(f"### :{colour}-background[ {label} ]")

    tower = board.tower()
    w = board.weather()
    m = st.columns(5)
    lap, total = board.lap_count()
    if kind == "race" and lap:
        m[0].metric("Lap", f"{lap} / {total}" if total else f"{lap}")
    else:
        m[0].metric("Time left", _clock(board.remaining(cutoff)) if not over else "0:00")
    leader = tower.iloc[0]["Driver"] if not tower.empty else "—"
    m[1].metric("Leader" if kind == "race" else "Fastest", leader)
    m[2].metric("Track temp", f"{w['TrackTemp']:.1f} °C" if pd.notna(w["TrackTemp"]) else "—")
    m[3].metric("Air temp", f"{w['AirTemp']:.1f} °C" if pd.notna(w["AirTemp"]) else "—")
    m[4].metric("Rain", "Yes" if w["Rainfall"] else "No")
    if over:
        st.caption("The session is over: this is the final timing (the official classification can still change "
                   "with penalties).")
        if feed is not None:
            _next_sessions()

    if tower.empty:
        st.info("No timing yet.")
        return

    styler, cfg = styled_tower(tower, kind, part, entries)
    st.dataframe(styler, hide_index=True, width="stretch", height=36 * len(tower) + 40, column_config=cfg)
    legend = (f":violet[purple] = fastest overall, :green[green] = personal best. Tyre = compound and laps "
              "on the set.")
    if kind == "quali" and part in (1, 2) and len(entries) > part:
        legend += (f" Shaded positions are in the drop zone: the top {entries[part]} go through. "
                   "**To the cut**: how far inside the line a lap is, or how much a car needs to find.")
    if kind == "race":
        legend += " Grid = places gained (+) or lost (−) since the start."
    st.caption(legend)

    c1, c2 = st.columns([3, 2])
    with c1:
        _stints(board, tower)
    with c2:
        _race_control(board)
    laps = board.lap_frame()
    if kind == "race":
        race_panels(board, tower, laps, info, flag_code)
    elif kind == "practice":
        practice_panels(laps)


def race_panels(board: live.Board, tower: pd.DataFrame, laps: pd.DataFrame, info: dict, flag_code: str) -> None:
    running = tower[~tower["Retired"] & ~tower["Stopped"] & (tower["Pos"] < 99)].copy()
    if running.empty:
        return
    # Lapped cars have no time gap: a lap down = the leader's recent lap time.
    lead_lap = laps.loc[laps["Position"] == 1, "LapTime"].tail(5).median() if not laps.empty else np.nan
    lead_lap = lead_lap if pd.notna(lead_lap) else 95.0
    gap = running["Gap"].where(running["LapsDown"] == 0, running["LapsDown"] * lead_lap)
    snap = pd.DataFrame({"Driver": running["Driver"], "RunningPosition": running["Pos"],
                         "GapToLeader": gap.fillna(gap.max()), "Compound": running["Compound"],
                         "TyreLife": running["TyreLife"].fillna(0)})
    base_loss, matched = config.get_pit_loss(info["location"], info["event"])
    pit_loss = pit_override or base_loss
    neutral = flag_code in config.SC_STATUS_CODES + config.VSC_STATUS_CODES
    eff_loss = pit_loss * (config.SC_PIT_LOSS_FACTOR if neutral else 1.0)
    clean = live.clean_laps(laps)
    deg = an.calculate_tyre_degradation(clean) if not clean.empty else {}
    field = an.field_compound_model(deg) if deg else {}
    rejoin = rt.pit_rejoin_analysis(snap, eff_loss)
    drivers = board.drivers()
    st.markdown("---")
    st.caption(f"Pit loss {eff_loss:.1f} s" + (f" (×{config.SC_PIT_LOSS_FACTOR} under the "
                                                 f"{'Safety Car' if flag_code == '4' else 'VSC'})" if neutral else "")
               + f", {matched or 'circuit not in config.py: default'}. Tyre models from the "
               f"{len(clean)} clean laps seen so far"
               + (f" (from lap {int(laps['LapNumber'].min())}: the board joined mid-session)"
                  if not laps.empty and laps["LapNumber"].min() > 3 else "") + ".")
    rt.strategy_panels(snap, rejoin, drivers, eff_loss, deg, field, fresh, respond)


def practice_panels(laps: pd.DataFrame) -> None:
    st.markdown("##### Long runs so far")
    runs, _ = pr.long_runs(laps) if not laps.empty else (pd.DataFrame(), None)
    if runs.empty:
        st.info(f"No long runs yet: a run is a stint of at least {config.PRACTICE_LONG_RUN_LAPS} laps "
                "on one set once in/out laps and slow laps are left out.")
        return
    show = runs.rename(columns={"driver": "Driver", "compound": "Tyre", "first": "From lap", "last": "To lap",
                                "laps": "Laps", "median": "Median lap", "deg": "Deg (s/lap)",
                                "pace": "Pace (%)"}).drop(columns=["stint"])
    show["Median lap"] = show["Median lap"].map(live.fmt_laptime)
    st.dataframe(show.sort_values("Pace (%)"), hide_index=True, width="stretch",
                 column_config={"Deg (s/lap)": st.column_config.NumberColumn(format="%+.3f"),
                                "Pace (%)": st.column_config.NumberColumn(format="%+.2f")})
    st.caption("Fuel corrected to the start of the run. Pace = against the field on the same tyres "
               "(negative = quicker). Same definition as the Practice page.")


def _stints(board: live.Board, tower: pd.DataFrame) -> None:
    stints = board.stints()
    if stints.empty:
        return
    st.markdown("##### Tyres")
    st.plotly_chart(stint_figure(stints, tower["Driver"].tolist()), width="stretch", theme="streamlit")
    st.caption("Each bar is a set of tyres in the order fitted, in laps. * = a used set.")


def _race_control(board: live.Board) -> None:
    st.markdown("##### Race control")
    rc = board.race_control(14)
    if rc.empty:
        st.caption("No messages yet.")
        return
    tz = _local_tz()
    rows = []
    for _, r in rc.iterrows():
        when = r["Utc"].tz_convert(tz).strftime("%H:%M:%S") if pd.notna(r["Utc"]) else ""
        lap = f" · L{int(r['Lap'])}" if pd.notna(r["Lap"]) and r["Lap"] else ""
        rows.append(f"`{when}{lap}` {r['Message']}")
    st.markdown("  \n".join(rows))


def _local_tz():
    """The laptop's time zone (times are shown as the viewer's clock)."""
    return datetime.now().astimezone().tzinfo


def _next_sessions() -> None:
    nxt = upcoming(3)
    if nxt:
        st.markdown("**On now and coming up** (your time):  \n" + "  \n".join(
            f"{e} · {s} · {w.tz_convert(_local_tz()):%a %d %b %H:%M}" for e, s, w in nxt))


st.fragment(board_view, run_every=f"{LIVE_REFRESH_S if source == 'Live' else REPLAY_REFRESH_S}s")()
