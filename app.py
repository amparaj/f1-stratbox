"""
app.py — F1 Stratbox entry point.

Run with:   streamlit run app.py

Sets page config once, initialises the FastF1 disk cache, and declares the
sidebar navigation explicitly (st.navigation) so page order and labels are
controlled here rather than inferred from file names.
"""

from __future__ import annotations

import streamlit as st

import config
from modules.data_engine import ACTIVE_SESSION_KEY, initialize_fastf1

st.set_page_config(
    page_title="F1 Stratbox",
    page_icon="🏁",
    layout="wide",
    initial_sidebar_state="expanded",
)

initialize_fastf1()


def home() -> None:
    """Landing page: what each tool is for and the modelling assumptions in force."""
    st.title("🏁 F1 Stratbox")
    st.caption("Post-race review · qualifying analysis · telemetry head-to-head · "
               "live second-screen tactics · future-race planning")

    c1, c2, c3 = st.columns(3)
    c4, c5, c6 = st.columns(3)
    with c1, st.container(border=True):
        st.markdown("#### 📝 Race Recap")
        st.markdown("Track replay of every car from its telemetry, fuel-corrected tyre degradation per "
                    "driver/compound, and a rule-based post-mortem that names the exact "
                    "lap each stint fell off the cliff.")
        st.page_link("pages/1_Race_Recap.py", label="Open Race Recap", icon="➡️")
    with c2, st.container(border=True):
        st.markdown("#### 📡 Live Race Tracker")
        st.markdown("Battle map of gaps to the leader with a pit-window shadow behind "
                    "every car: see who rejoins in traffic and which pairs are open to "
                    "the undercut, lap by lap.")
        st.page_link("pages/2_Live_Race_Tracker.py", label="Open Live Race Tracker", icon="➡️")
    with c3, st.container(border=True):
        st.markdown("#### 🧪 Future Sandbox")
        st.markdown("Compare 1-stop vs 2-stop plans under upgrade, track-temperature "
                    "and rain scenarios, deterministically and via Monte Carlo with "
                    "random Safety Cars.")
        st.page_link("pages/3_Future_Sandbox.py", label="Open Future Sandbox", icon="➡️")
    with c4, st.container(border=True):
        st.markdown("#### ⏱️ Qualifying")
        st.markdown("Q1 / Q2 / Q3 times, how close everyone was to each cut-off, best sectors "
                    "and the ideal lap, track evolution, when each driver ran and what a late "
                    "run was worth, and one-lap against race pace.")
        st.page_link("pages/4_Qualifying.py", label="Open Qualifying", icon="➡️")
    with c5, st.container(border=True):
        st.markdown("#### 📈 Telemetry")
        st.markdown("Any two laps head to head: speed, throttle, brake, RPM, gear and DRS over the "
                    "lap, the time delta, who was faster through each corner on a track map, and "
                    "how much of each lap was flat out, braking and cornering.")
        st.page_link("pages/5_Telemetry.py", label="Open Telemetry", icon="➡️")
    with c6, st.container(border=True):
        st.markdown("#### 🔧 Practice")
        st.markdown("FP1, FP2 and FP3: the timesheet, one-lap pace from the qualifying simulations, "
                    "and every long run fuel corrected with its tyre wear: who looks quick over a race "
                    "stint before anyone has qualified.")
        st.page_link("pages/6_Practice.py", label="Open Practice", icon="➡️")

    st.markdown("---")
    left, right = st.columns([3, 2])
    with left:
        st.markdown("##### Getting started")
        st.markdown(
            "1. Pick a season, Grand Prix and session in the sidebar of any page and press "
            "**Load session**. A sprint weekend has Practice 1 then four sessions (Sprint Qualifying, "
            "Sprint, Qualifying, Grand Prix), a conventional one three practices, Qualifying and the "
            "Grand Prix. The choice carries across all pages: "
            "the race pages show the race a qualifying pick set the grid for, and the Qualifying "
            "page the qualifying for a race.\n"
            "2. The first load of a session downloads from the F1 timing API (around 30–90 s); "
            "after that it comes from the local `.fastf1/` cache and Streamlit's memory cache.\n"
            "3. If the full feed fails, the app falls back to lap timing only and tells you."
        )
        active = st.session_state.get(ACTIVE_SESSION_KEY)
        if active:
            st.success(f"Active session: **{active[0]} {active[1]}**, {config.SESSION_LABELS[active[2]]}")
    with right:
        st.markdown("##### Model assumptions")
        st.markdown(
            f"- Fuel effect: **{config.FUEL_EFFECT_PER_LAP} s/lap** gained as fuel burns\n"
            f"- Clean air: gap to car ahead **> {config.CLEAN_AIR_THRESHOLD_S} s**\n"
            f"- Pit loss under SC/VSC: **×{config.SC_PIT_LOSS_FACTOR}**\n"
            f"- Pit losses: {len(config.TRACK_PIT_LOSS)} circuits in `config.py` "
            "(approximate public figures; swap in team data)"
        )


pages = [
    st.Page(home, title="Home", icon="🏠", default=True),
    st.Page("pages/1_Race_Recap.py", title="Race Recap", icon="📝"),
    st.Page("pages/2_Live_Race_Tracker.py", title="Live Race Tracker", icon="📡"),
    st.Page("pages/3_Future_Sandbox.py", title="Future Sandbox", icon="🧪"),
    st.Page("pages/4_Qualifying.py", title="Qualifying", icon="⏱️"),
    st.Page("pages/5_Telemetry.py", title="Telemetry", icon="📈"),
    st.Page("pages/6_Practice.py", title="Practice", icon="🔧"),
]
st.navigation(pages).run()
