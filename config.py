"""
config.py — Global constants for F1 Stratbox.

Every tunable modelling assumption lives here so the strategy group can audit
and override them in one place. Values marked "approx." are public-domain
estimates intended as prototype defaults; replace them with the team's own
measured numbers before relying on them for a live call.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
FASTF1_CACHE_DIR = PROJECT_ROOT / ".fastf1"

# Where session data comes from: "fastf1" (F1's live-timing archive; richest, the
# dashboard's default) or "openf1" (modules/openf1.py; the website's export uses it,
# because the live-timing server doesn't answer GitHub's runners).
DATA_SOURCE = "fastf1"

# The sessions of a weekend, by code. A sprint weekend runs SQ, S, Q, R; a conventional one Q, R.
# Names are FastF1's schedule names (and OpenF1's session_name); 2023 called SQ "Sprint Shootout".
# Practice (FP1-3; a sprint weekend has FP1 only) is loaded and analysed but isn't forecast: it's
# in PRACTICE_CODES, not WEEKEND_ORDER (the sessions that get odds).
SESSION_NAMES = {"R": "Race", "S": "Sprint", "Q": "Qualifying", "SQ": "Sprint Qualifying",
                 "FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3"}
SESSION_NAME_ALIASES = {"SQ": ("Sprint Qualifying", "Sprint Shootout")}
SESSION_LABELS = {"R": "Grand Prix", "S": "Sprint", "Q": "Qualifying", "SQ": "Sprint Qualifying",
                  "FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3"}
RACE_CODES = ("R", "S")
QUALI_CODES = ("Q", "SQ")
PRACTICE_CODES = ("FP1", "FP2", "FP3")
WEEKEND_ORDER = ("SQ", "S", "Q", "R")
ALL_SESSIONS = PRACTICE_CODES + WEEKEND_ORDER     # every kind loaded (a weekend's order is by start time)
RACE_OF_QUALI = {"Q": "R", "SQ": "S"}       # the race a qualifying session sets the grid for
QUALI_OF_RACE = {"R": "Q", "S": "SQ"}


def session_names(code: str) -> tuple[str, ...]:
    """Every schedule name a session code has gone by."""
    return SESSION_NAME_ALIASES.get(code, (SESSION_NAMES[code],))


# When a session counts as finished. Before EARLIEST_FINISH nobody looks; from then on a session
# is loaded once its last chequered flag (qualifying shows one at the end of each of Q1, Q2 and
# Q3) is FINISH_SETTLE old (the last cars cross the line and the feed catches up), or, with no
# flag at all (abandoned, or qualifying ended under a red flag), LATEST_FINISH after the start.
# scripts/needs_update.py keeps its own copy (standard library only): keep them in step.
EARLIEST_FINISH_MIN = {"R": 75, "S": 25, "Q": 55, "SQ": 40,   # start to the earliest last flag
                       "FP1": 55, "FP2": 55, "FP3": 55}
CHEQUERED_FLAGS = {"R": 1, "S": 1, "Q": 3, "SQ": 3, "FP1": 1, "FP2": 1, "FP3": 1}
FINISH_SETTLE_MIN = 5
LATEST_FINISH_H = {"R": 6, "S": 6, "Q": 3, "SQ": 3,             # race: 3 h limit plus a delayed start
                   "FP1": 3, "FP2": 3, "FP3": 3}
RECENT_DAYS = 4                            # until then a session may still change (penalties, grid)


def session_finished(start_utc, chequered_utc, now_utc, code: str = "R") -> bool:
    """Has a session that started at `start_utc` finished? All tz-aware timestamps;
    `chequered_utc` is its last chequered flag's time (last_chequered), or None."""
    if chequered_utc is not None:
        return now_utc >= chequered_utc + dt.timedelta(minutes=FINISH_SETTLE_MIN)
    return now_utc >= start_utc + dt.timedelta(hours=LATEST_FINISH_H[code])


def last_chequered(times, code: str):
    """The flag that ends the session: the CHEQUERED_FLAGS[code]-th one, or None before it."""
    times = sorted(t for t in times if t is not None)
    n = CHEQUERED_FLAGS[code]
    return times[n - 1] if len(times) >= n else None

# ---------------------------------------------------------------------------
# Pit lane time loss (seconds) — full green-flag stop, pit entry to pit exit
# versus staying out. Keys are display names; see PIT_LOSS_ALIASES for the
# FastF1 location / event-name spellings that map onto them. approx.
# ---------------------------------------------------------------------------
TRACK_PIT_LOSS: dict[str, float] = {
    "Silverstone": 22.5,
    "Monaco": 24.5,
    "Spa": 21.0,
    "Monza": 23.0,
    "Sakhir": 23.0,
    "Jeddah": 20.0,
    "Melbourne": 19.0,
    "Suzuka": 22.5,
    "Shanghai": 23.0,
    "Miami": 21.5,
    "Imola": 28.0,
    "Montreal": 18.5,
    "Barcelona": 22.0,
    "Spielberg": 20.5,
    "Budapest": 21.0,
    "Zandvoort": 21.5,
    "Baku": 20.5,
    "Marina Bay": 28.0,
    "Austin": 21.0,
    "Mexico City": 22.0,
    "Sao Paulo": 21.5,
    "Las Vegas": 21.0,
    "Lusail": 25.0,
    "Yas Island": 22.0,
    # 2026 venues. Location is matched before event name, so these stop the relocated
    # "Bahrain GP" (Kuala Lumpur) and "Spanish GP" (Madrid) inheriting Sakhir/Barcelona.
    "Sepang": 21.0,      # approx., from the pre-2018 Malaysian GP era
    "Madrid": 22.0,      # placeholder (new circuit) — replace with team data
}

# Lower-case, accent-free aliases → TRACK_PIT_LOSS key.
PIT_LOSS_ALIASES: dict[str, str] = {
    "spa-francorchamps": "Spa",
    "spa francorchamps": "Spa",
    "belgian grand prix": "Spa",
    "monte carlo": "Monaco",
    "monaco grand prix": "Monaco",
    "british grand prix": "Silverstone",
    "italian grand prix": "Monza",
    "bahrain grand prix": "Sakhir",
    "bahrain": "Sakhir",
    "saudi arabian grand prix": "Jeddah",
    "australian grand prix": "Melbourne",
    "japanese grand prix": "Suzuka",
    "chinese grand prix": "Shanghai",
    "miami grand prix": "Miami",
    "emilia romagna grand prix": "Imola",
    "canadian grand prix": "Montreal",
    "spanish grand prix": "Barcelona",
    "austrian grand prix": "Spielberg",
    "hungarian grand prix": "Budapest",
    "dutch grand prix": "Zandvoort",
    "azerbaijan grand prix": "Baku",
    "singapore grand prix": "Marina Bay",
    "singapore": "Marina Bay",
    "united states grand prix": "Austin",
    "mexico city grand prix": "Mexico City",
    "sao paulo grand prix": "Sao Paulo",
    "las vegas grand prix": "Las Vegas",
    "qatar grand prix": "Lusail",
    "abu dhabi grand prix": "Yas Island",
    "yas marina": "Yas Island",
    "kuala lumpur": "Sepang",
    "malaysian grand prix": "Sepang",
    "barcelona grand prix": "Barcelona",
}

DEFAULT_PIT_LOSS = 22.0          # used when a circuit is not in the table
SC_PIT_LOSS_FACTOR = 0.55        # pit loss multiplier under SC/VSC (field is slowed)

# ---------------------------------------------------------------------------
# Fuel & timing model
# ---------------------------------------------------------------------------
# Car pace improves by exactly this many seconds per lap as fuel burns off.
FUEL_EFFECT_PER_LAP = 0.033

# A lap is "Clean Air" only if the timing-line gap to the car ahead is > this.
CLEAN_AIR_THRESHOLD_S = 1.5

# Laps slower than this multiple of the driver's median clean lap are treated
# as outliers (traffic, damage, local yellows) and excluded from model fitting.
OUTLIER_LAP_FACTOR = 1.07

# FastF1 TrackStatus codes: 1 green, 2 yellow, 4 SC, 5 red, 6 VSC, 7 VSC ending.
NEUTRALISED_TRACK_STATUS = ("4", "5", "6", "7")
SC_STATUS_CODES = ("4",)
VSC_STATUS_CODES = ("6", "7")
RED_FLAG_STATUS_CODES = ("5",)

# Qualifying (data_engine.get_quali_laps / get_quali_results).
QUALI_PUSH_FACTOR = 1.05        # a lap within this of the driver's best is a push lap, not a cool-down
QUALI_PACE_OUTLIER = 3.0        # % behind the field median beyond which a qualifying pace is ignored
QUALI_GAIN_MIN_DRIVERS = 5      # drivers with two push laps a segment needs for its track-gain slope

# Car telemetry (modules/telemetry.py): the track replay and lap head-to-head. Always FastF1:
# OpenF1 has car data too, but paging every car's 4 Hz feed at ~28 requests/min is too slow.
REPLAY_HZ = 2                   # replay frames per second of race (the player interpolates between)
REPLAY_OUTLINE_STEP_M = 5       # track outline resolution for placing cars along the lap (m)
FULL_THROTTLE_PCT = 98          # throttle at or above this counts as flat out
CORNER_GROUP_GAP_M = 150        # corners closer than this form one zone (Baku's 8-12)
CORNER_ZONE_PAD_M = 100         # a corner zone runs this far either side of its corners
CORNER_SPEED_CLASSES = ((120, "Low speed"), (200, "Medium speed"), (1e9, "High speed"))  # min km/h <
# The website's lap telemetry (modules/site_telemetry.py, from OpenF1): every driver's fastest lap.
SITE_TEL_STEP_M = 10            # distance between samples on the shared lap axis (m)
SITE_TEL_MAX_FETCH = 6          # sessions whose telemetry one export run may download (2 requests a driver)

# Minimum laps needed to fit a degradation line for one driver/compound.
MIN_LAPS_FOR_FIT = 5

# Tyre-cliff detector (two-segment piecewise regression on each stint).
CLIFF_MIN_STINT_LAPS = 8        # stint must have this many clean laps to test
CLIFF_MIN_SEGMENT_LAPS = 3      # each side of the breakpoint needs this many
CLIFF_MIN_SLOPE_JUMP = 0.15     # s/lap increase in deg rate to call a cliff
CLIFF_MIN_SSE_GAIN = 0.35       # two-segment fit must cut SSE by >= 35 %
CLIFF_MIN_TIME_LOST = 1.0       # s lost vs. pre-cliff trend before we headline it

# ---------------------------------------------------------------------------
# Compound presets for the Future Sandbox (used when no session is calibrated)
# base_offset: seconds relative to the reference lap time on fresh tyres.
# deg_rate: linear seconds lost per lap of tyre age.
# cliff_age / cliff_rate: beyond cliff_age laps, deg grows by cliff_rate s/lap.
# temp_sensitivity: fractional change in deg rate per +1 °C track temperature.
# ---------------------------------------------------------------------------
DRY_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")
WET_COMPOUNDS = ("INTERMEDIATE", "WET")

COMPOUND_PRESETS: dict[str, dict[str, float]] = {
    "SOFT":   {"base_offset": 0.00, "deg_rate": 0.110, "cliff_age": 18, "cliff_rate": 0.25, "temp_sensitivity": 0.025},
    "MEDIUM": {"base_offset": 0.45, "deg_rate": 0.065, "cliff_age": 30, "cliff_rate": 0.20, "temp_sensitivity": 0.018},
    "HARD":   {"base_offset": 0.90, "deg_rate": 0.040, "cliff_age": 42, "cliff_rate": 0.15, "temp_sensitivity": 0.012},
    "INTERMEDIATE": {"base_offset": 0.0, "deg_rate": 0.090, "cliff_age": 30, "cliff_rate": 0.20, "temp_sensitivity": 0.0},
    "WET":          {"base_offset": 0.0, "deg_rate": 0.060, "cliff_age": 40, "cliff_rate": 0.15, "temp_sensitivity": 0.0},
}

COMPOUND_SHORT = {"SOFT": "S", "MEDIUM": "M", "HARD": "H", "INTERMEDIATE": "I", "WET": "W"}
SHORT_TO_COMPOUND = {v: k for k, v in COMPOUND_SHORT.items()}

# Official-style compound colours (domain convention on the pit wall).
COMPOUND_COLORS = {
    "SOFT": "#DA291C",
    "MEDIUM": "#FFD12E",
    "HARD": "#9C9C9C",
    "INTERMEDIATE": "#43B02A",
    "WET": "#0067AD",
    "UNKNOWN": "#7F7F7F",
}

# ---------------------------------------------------------------------------
# Weather model for the simulator (modules/simulator.py). A rain spell runs from its
# rain lap to its dry lap (the first lap slicks are quicker again; none = wet to the flag).
# On a wet lap a car pays its tyre's offset for that intensity; a car on the wrong tyre
# pits for the right one unless staying out is cheaper over the wet laps left (the team
# knows when the rain stops). Once the track is dry, a car on wet tyres pays
# WET_TYRE_ON_DRY and pits for slicks unless staying out to the flag is cheaper.
# ---------------------------------------------------------------------------
RAIN_PROFILES: dict[str, dict] = {
    "Light rain": {
        "compound": "INTERMEDIATE",   # the tyre for it
        # lap time vs the fastest dry base pace, per wet tyre
        "pace_offset": {"INTERMEDIATE": 7.0, "WET": 10.0},
        "slick_penalty": 12.0,        # extra per lap on slicks, on top of the dry lap
    },
    "Heavy rain": {
        "compound": "WET",
        "pace_offset": {"INTERMEDIATE": 20.0, "WET": 14.0},
        "slick_penalty": 28.0,
    },
}
# Wet tyres on a dry track: seconds per lap slower than the fastest dry base pace, and their
# deg multiplied by WET_TYRE_DRY_DEG_FACTOR (they overheat). approx.
WET_TYRE_ON_DRY = {"INTERMEDIATE": 4.5, "WET": 9.0}
WET_TYRE_DRY_DEG_FACTOR = 3.0

# Forecast precipitation -> laps of rain (modules/weather.py). mm per hour at the circuit.
RAIN_WET_MM_H = 0.3              # a wet hour: enough to need intermediates
RAIN_HEAVY_MM_H = 3.0            # heavy: full wets
RACE_MINUTES = {"R": 95, "S": 32, "Q": 60, "SQ": 44}   # typical start-to-flag time: forecast hours -> laps
TRACK_DRYING_LAPS = 4            # laps after the rain stops before slicks are quicker. approx.

# Track temperature from a forecast: air + offset + gain x sunshine (W/m², shortwave
# radiation). Fitted on the track sensors of the 34 dry 2025-26 races against Open-Meteo's
# archived forecast for the race window: RMSE 3.5 °C (forecast air is 0.5 °C under the sensor's).
TRACK_TEMP_SUN_GAIN = 0.020      # °C per W/m²
TRACK_TEMP_BASE_OFFSET = 5.3     # °C over air with no sun (night races: 4-6 °C)
REFERENCE_TRACK_TEMP = 35.0      # °C the compound presets stand for (no calibration session)

# Open-Meteo (free, no key, non-commercial use, credit "Weather data by Open-Meteo.com",
# CC BY 4.0). Ensemble members give each forecast's own rain timeline; climate (beyond the
# ensemble's range) the same race window on CLIMATE_DAYS days around the date, CLIMATE_YEARS years.
OPEN_METEO_CACHE_DIR = PROJECT_ROOT / ".openmeteo"
ENSEMBLE_MODELS = (("icon_seamless", 7), ("gfs_seamless", 16))   # (model, days ahead it covers)
CLIMATE_YEARS = 10
CLIMATE_DAYS = 3                 # +- days around the race date
FORECAST_FRESH_HOURS = 3         # re-download a forecast after this

# Circuit coordinates (lat, lon) by TRACK_PIT_LOSS key, for weather lookups. Anything not
# listed is looked up by name with Open-Meteo's geocoder.
CIRCUIT_COORDS: dict[str, tuple[float, float]] = {
    "Silverstone": (52.0786, -1.0169), "Monaco": (43.7347, 7.4206), "Spa": (50.4372, 5.9714),
    "Monza": (45.6156, 9.2811), "Sakhir": (26.0325, 50.5106), "Jeddah": (21.6319, 39.1044),
    "Melbourne": (-37.8497, 144.9680), "Suzuka": (34.8431, 136.5410), "Shanghai": (31.3389, 121.2200),
    "Miami": (25.9581, -80.2389), "Imola": (44.3439, 11.7167), "Montreal": (45.5000, -73.5228),
    "Barcelona": (41.5700, 2.2611), "Spielberg": (47.2197, 14.7647), "Budapest": (47.5789, 19.2486),
    "Zandvoort": (52.3888, 4.5409), "Baku": (40.3725, 49.8533), "Marina Bay": (1.2914, 103.8640),
    "Austin": (30.1328, -97.6411), "Mexico City": (19.4042, -99.0907), "Sao Paulo": (-23.7036, -46.6997),
    "Las Vegas": (36.1147, -115.1730), "Lusail": (25.4900, 51.4542), "Yas Island": (24.4672, 54.6031),
    "Sepang": (2.7608, 101.7382), "Madrid": (40.4637, -3.6164),
}

# ---------------------------------------------------------------------------
# Stochastic (Monte Carlo) engine defaults
# ---------------------------------------------------------------------------
MC_DEFAULT_SIMS = 500
MC_LAP_NOISE_SD = 0.30           # s, per-lap random variation
MC_SC_PROBABILITY = 0.45         # probability of >= 1 Safety Car in a race
MC_SC_DURATION_LAPS = (3, 5)     # inclusive range
MC_SC_LAP_FACTOR = 1.40          # SC lap time = reference lap * this
MC_SC_PIT_WINDOW = 8             # pit early under SC if a stop was due within N laps

# ---------------------------------------------------------------------------
# Season forecasts (modules/forecast.py) for the website. Prototype assumptions.
# ---------------------------------------------------------------------------
RACE_POINTS = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)
SPRINT_POINTS = (8, 7, 6, 5, 4, 3, 2, 1)
FORM_MIN_CLEAN_LAPS = 8          # clean laps a driver needs for a race-pace figure
# Free practice (modules/practice.py). A long run: at least PRACTICE_LONG_RUN_LAPS consecutive laps
# within PRACTICE_RUN_TOL of the run's best. A weekend's sessions count PRACTICE_SESSION_WEIGHT each.
PRACTICE_LONG_RUN_LAPS = 6
PRACTICE_RUN_TOL = 0.035
PRACTICE_SESSION_WEIGHT = {"FP1": 1.0, "FP2": 1.0, "FP3": 1.0}   # FP1 at 0.25, 0.5 or 1: within 0.004
# Share of practice in the expected pace once a weekend's practice is in (forecast.expected_pace):
# one-lap pace into qualifying form, long-run pace into race form, each moving a driver at most
# PRACTICE_CLIP (%) from their form (a crash or an aborted programme puts a driver 5-10% off: unclipped,
# even a 0.1 share made qualifying forecasts worse, -2.80 -> -3.10). Replays of 2025-26 after practice
# (calibrate_forecast.py): qualifying -2.803 without practice, -2.711 with one-lap at 0.2 clipped at 1%
# (0.15-0.25 and clips 0.75-1.5 within 0.01); races and sprints -2.649 -> -2.590 (through qualifying
# form). Long runs added nothing at any share or clip (0 best, 0.1-0.3 within 0.01), so they stay out.
PRACTICE_QUALI_BLEND = 0.2
PRACTICE_RACE_BLEND = 0.0
PRACTICE_CLIP = 1.0
# Recency. Form counts the last FORM_MAX_RACES rounds of this season only (never last season), each round
# back weighted FORM_DECAY. Replays of 2025-26 (races + qualifying, 1-2 rounds ahead) by window: last 2
# rounds -3.066, 3 -2.931, 4 -2.844, 6 -2.771, 8 -2.768, 12 -2.748; decay 0.4-1.0 within 0.012 (0.65
# best). A short window is worse: one session is noisy, so more rounds help as long as recent ones count
# most (with 0.65, the last three rounds carry about three-quarters of the weight).
FORM_DECAY = 0.65                # weight of each earlier race vs the next one (most recent = 1)
FORM_MAX_RACES = 12              # rounds that count towards form
# % of lap time: a session's pace counts at most this far from the driver's median pace over the form
# window, so one crash, failure or scrappy lap (2026 Baku Q: ANT set only a Q1 banker, +0.38% against
# a -0.6% norm) can't swing form. None = no limit. Replays (calibrate_forecast.py, scores as below):
# 0.15 -2.774, 0.2 -2.775, 0.25 -2.777, 0.35 -2.790, 0.5 -2.809, none -2.821 (races + quali, 1-2 ahead):
# 0.15-0.25 tie, 0.25 kept (best summed over every session kind and the after-qualifying forecasts).
FORM_CLIP = 0.25
# Car + driver form (forecast.split_form). A session's pace = the car's level that round + the driver's rating
# against teammates + noise. The car's form fades FORM_DECAY a round, this season only (upgrades move it fast);
# the rating is a ridge regression on teammate gaps since the start of last season, fading TEAMMATE_DECAY a
# round, so it carries over winters and team changes. On top, a driver's streak (own recent form against car +
# rating) is real but fades: STREAK_PERSIST of it is left per round ahead.
# Replays of 2025-26 (calibrate_forecast.py; 2025 has no 2024 to carry over), current form -> split:
#   Q next round -2.718 -> -2.725, 3 rounds ahead -2.993 -> -2.846, after practice -2.657 -> -2.654
#   SQ -3.253 -> -3.033, 3 ahead -3.166 -> -2.916; S -3.094 -> -3.012, 3 ahead -2.919 -> -2.820 (11 weekends)
#   R -2.526 -> -2.525, 3 ahead -2.688 -> -2.746: no persistence (0-1) or setting beat the current form, so
#   the Grand Prix keeps it. Without the streak (persist 0) Q next round fell to -2.769: a driver's run of form
#   does carry into the next session, then fades. Teammate gaps unclipped: SQ -3.087 (clip 0.15: -2.932).
SPLIT_FORM: tuple[str, ...] = ("Q", "SQ", "S")   # the sessions whose forecasts use it
STREAK_PERSIST = 0.5             # share of a streak left one round on (0.5-0.7 tie for Q; SQ, S prefer 0)
TEAMMATE_DECAY = 0.9             # weight of each earlier round's teammate gap vs the next one (0.8-0.97 tie)
TEAMMATE_CARRY = 0.5             # weight on last season's gaps, on top of the decay (0: Q 3 ahead -2.898)
TEAMMATE_PRIOR = 0.25            # ratings pulled towards 0 with this many sessions' weight (0.1-0.5 tie)
TEAMMATE_CLIP = 0.15             # % a session's teammate gap counts at most from the pair's median gap
TEAMMATE_MAX_ROUNDS = 40         # rounds back that count (this season and last)
FORECAST_SIMS = 10_000          # simulated races per forecast / simulated seasons for title odds
FORM_DRIFT_SD = 0.35             # % of lap time: how far a driver's form moves over the rest of a season, one
                                 # draw per simulated season (title odds). 2026: the sd of (mean pace over the
                                 # next 7 races - form now) is 0.41, part of which is race-to-race noise
DNF_PRIOR_STARTS = 10            # shrink each driver's DNF rate to the field's, as if from this many starts
# Session forecasts (forecast.forecast_session), per session code. Fitted by replaying every session
# of 2025-26 from the sessions before it (scripts/calibrate_forecast.py 2025 2026, score = log chance
# of the actual winner/pole + mean log chance of a podium/top 3 for the actual top three). Oct 2026,
# 102 sessions (2025 Baku qualifying gap-filled from F1 live timing):
#   race before the weekend -2.57, after qualifying -1.74; sprint -3.18 / -1.56; qualifying -2.82.
# With FORM_CLIP (re-run Oct 2026, same sessions; without the cap in brackets): race -2.558 (-2.574),
# after qualifying -1.799 (-1.807); sprint -3.141 (-3.159) / -1.511 (-1.543); qualifying -2.754
# (-2.818); sprint qualifying -3.318 (-3.403). The re-run also found RACE_QUALI_BLEND 0.6 and 0.75 tied
# (-2.627 each), SPRINT_FORM_WEIGHT flat, CIRCUIT_WEIGHT[R] 0 vs 0.25 within 0.006, DRIFT_PER_ROUND
# 0.15 vs 0.1 within 0.005: all kept.
SESSION_SD = {"R": 0.35, "S": 0.6, "Q": 0.35, "SQ": 0.35}   # % of lap time: spread on the day (pre-grid)
SESSION_SD_GRID = {"R": 0.2, "S": 0.45}   # ... of a race once its grid (qualifying) is known
SPRINT_FORM_WEIGHT = 0.75        # a sprint's race pace counts this much of a Grand Prix's (0-1 all within 0.01)
RACE_QUALI_BLEND = 0.75          # share of qualifying form in a race's expected pace
RACE_QUALI_BLEND_WEEKEND = 0.85  # ... of the pace in the race's own qualifying, once it's done
GRID_WEIGHT = {"R": 0.02, "S": 0.2}    # % per grid place: track position (a sprint is mostly decided by it)
# % per grid place lost to a penalty. GRID_WEIGHT is fitted on ordinary grids, where the slot and the pace
# go together, so it undervalues a drop: in 94 power-unit penalty starts (2020-25, f1penalties.com against
# Jolpica results) drivers finished 0.237 places worse per grid place dropped (front-runners 0.238), where
# GRID_WEIGHT gives 0.09-0.10. 0.05 gives 0.237 in the simulation (0.052 before the grid is known, 0.049
# after). Used for announced penalties and power-unit risk before the grid, and for a grid slot behind
# the qualifying position once the grid is in (on top of GRID_WEIGHT).
PENALTY_GRID_WEIGHT = {"R": 0.05, "S": 0.2}
# Share of last season's team-circuit offset. On 2026 (new rules) 0 and 0.25 tie for races (-2.667 vs
# -2.672) and qualifying prefers 0: kept at 0.25 for races only, as circuits do suit some cars.
# Re-fitted with practice, upgrades and the longer form window: races 0 -2.622, 0.25 -2.624; qualifying
# 0 best. Set to 0: last season (another car, often other rules) says nothing the replays can find.
CIRCUIT_WEIGHT = {"R": 0.0, "S": 0.0, "Q": 0.0, "SQ": 0.0}
CIRCUIT_CLIP = 1.0               # % bound on a team's circuit offset
DRIFT_PER_ROUND = 0.1            # % of form drift per sqrt(round) beyond the next one
SPRINT_DNF_FACTOR = 0.4          # a sprint's retirement chance against a Grand Prix's (a third the distance)
QUALI_NO_TIME = 0.01             # chance a driver sets no qualifying time (crash, failure)
QUALI_EDGE = (0.25, 0.75)        # a driver whose chance of a cut-off is in here is "on the edge" of it
# Teams under their current name, so last season's circuit figures carry over a rename.
TEAM_LINEAGE = {
    "Kick Sauber": "Audi", "Stake F1 Team Kick Sauber": "Audi", "Sauber": "Audi", "Alfa Romeo": "Audi",
    "RB": "Racing Bulls", "Visa Cash App RB": "Racing Bulls", "AlphaTauri": "Racing Bulls",
    "Haas": "Haas F1 Team",
}
CLASSIFIED_FRACTION = 0.9        # share of the winner's laps needed to be classified (FIA rule)
# Grid penalties announced before a race's grid is out (power-unit or gearbox changes, carried-over
# penalties): {season: {round: {driver: places back, or "back" (back of the grid) / "pit" (pit lane)}}}.
# Kept by hand from the stewards' documents: no free feed has them before qualifying. Once
# qualifying is done the forecast takes the grid from OpenF1's starting_grid (penalties applied) and
# these only fill in until it's out; after the race the official grid is used.
GRID_PENALTIES: dict[int, dict[int, dict[str, int | str]]] = {}

# ---------------------------------------------------------------------------
# Power-unit penalties (modules/penalties.py; the model's constants are fitted by
# scripts/calibrate_penalties.py on every round since 2022)
# ---------------------------------------------------------------------------
PENALTY_FRESH_HOURS = 6          # f1penalties.com's export is fetched again after this
FIA_PAGE_FRESH_HOURS = 3         # an FIA event page (its document list) is fetched again after this
# Each element's allocation per season, where the FIA's documents haven't stated it yet (they do
# once someone goes past it: "the fifth (5th) of the four (4) ... allowed").
PU_LIMITS = {
    2022: {"ICE": 3, "TC": 3, "MGU-H": 3, "MGU-K": 3, "ES": 2, "CE": 2, "EX": 8},
    2023: {"ICE": 4, "TC": 4, "MGU-H": 4, "MGU-K": 4, "ES": 2, "CE": 2, "EX": 8},
    2024: {"ICE": 4, "TC": 4, "MGU-H": 4, "MGU-K": 4, "ES": 2, "CE": 2, "EX": 8},
    2025: {"ICE": 4, "TC": 4, "MGU-H": 4, "MGU-K": 4, "ES": 2, "CE": 2, "EX": 8},
    2026: {"ICE": 4, "TC": 4, "EX": 4, "MGU-K": 3, "ES": 3, "CE": 3, "ANC": 6},
}
# Grid drops (Sporting Regulations): the first element of a kind past the allocation, each one after
# it, and more than PU_BACK_OVER places in all = the back of the grid.
PU_FIRST_DROP = 10
PU_NEXT_DROP = 5
PU_BACK_OVER = 15
# How big a power-unit penalty turns out to be, as shares (f1penalties.com, 75 penalties 2022-26;
# "back" includes pit-lane starts: teams mostly fit a whole new unit at once).
PU_DROP_SHARE = {"back": 0.65, 15: 0.05, 10: 0.14, 5: 0.16}
# The hazard model: logit of the chance of an over-allocation element at a Grand Prix =
# intercept + deficit·elements short + over·already over + left·share of season left
# + circuit·log(circuit factor). Fitted on 2,272 car-rounds of 2022-26 (97 over-allocation changes,
# FIA documents). Leave one season out it scores log loss 0.1635, against 0.1726 for the circuit
# alone and 0.1792 for a constant. Taking one ("over") lowers the next round's chance (a fresh
# pool); the circuit factor counts almost one for one (0.95).
PU_HAZARD = {"intercept": -4.033, "deficit": 1.066, "over": -0.910, "left": -1.076, "circuit": 0.951}
PU_CIRCUIT_PRIOR = 3.0           # races' worth of "average circuit" mixed into each circuit's factor
PU_LATE_SHARE = 0.1              # of a round's hazard left once its "New PU elements" are out without the driver
# Reported plans (hand-kept, from team statements): {season: {driver: {"rounds": [...], "note", "source"}
# or a list of them}}. Each is one penalty, PU_PLAN_HAZARD at each of its rounds until it happens,
# independent of the usage model and of other plans. One is retired automatically once the FIA's
# tables show the driver went past the allocation at one of its rounds. News reports add more
# (modules/news.py, NEWS_PLAN_MIN_SOURCES below).
PU_PLAN_HAZARD = 0.6
# F1 news (modules/news.py): the main sites' RSS feeds, scanned for power-unit penalties, stewards'
# penalties and upgrades. A (driver, round) power-unit penalty reported by NEWS_PLAN_MIN_SOURCES sites
# within NEWS_PLAN_DAYS counts as a reported plan (as PU_PLANS). PlanetF1's feed is gone (404) and
# RaceFans' refuses scripts (403).
NEWS_FEEDS = {
    "The Race": "https://www.the-race.com/feed/",
    "Crash.net": "https://www.crash.net/rss/f1",
    "Autosport": "https://www.autosport.com/rss/f1/news/",
    "Motorsport.com": "https://www.motorsport.com/rss/f1/news/",
    "Formula1.com": "https://www.formula1.com/en/latest/all.xml",
    "BBC Sport": "https://feeds.bbci.co.uk/sport/formula1/rss.xml",
    "Sky Sports": "https://www.skysports.com/rss/12433",
    "GPFans": "https://www.gpfans.com/en/rss.xml",
}
NEWS_FRESH_HOURS = 0.15          # every export reads the feeds (scheduled runs are >= 10 min apart)
NEWS_KEEP_DAYS = 45
NEWS_PLAN_MIN_SOURCES = 2
NEWS_PLAN_DAYS = 10
# Upgrades (modules/upgrades.py, the FIA's "Car Presentation Submissions"): a team's pace at a round
# where it brought performance parts is compared with its average over the UPGRADE_BEFORE rounds before.
UPGRADE_BEFORE = 3
# The forecast's upgrade term: % of pace per performance item a team brings to the round (negative =
# quicker), at most UPGRADE_MAX_ITEMS items. Fitted by calibrate_forecast.py (0 = upgrades on the
# FIA's list don't predict a faster car that weekend, on average).
# Replays of 2025-26 (every session kind, the round's forecast): -0.1 -2.727, -0.05 -2.684, -0.025
# -2.687, 0 -2.706, +0.025 -2.760 (the same in both passes). On average an upgrade helps a little,
# though a single team's pace after one moves either way (news.json's upgrade_effect).
UPGRADE_EFFECT = -0.05
UPGRADE_MAX_ITEMS = 4
PU_PLANS: dict[int, dict[str, dict]] = {
    2026: {
        "RUS": {"rounds": [18, 19],
                "note": "Mercedes plan to take Russell's penalty with their ADUO power-unit upgrade, expected at "
                        "the United States or Mexico City Grand Prix (a fresh engine in Singapore after his Malaysia "
                        "failure would be a second one: the news scan picks that up).",
                "source": "https://www.crash.net/f1/news/1104120/1/reason-george-russell-and-mercedes-are-delaying-his-f1-engine-penalty"},
    },
}

# Strategy search for upcoming races: every 1- and 2-stop plan over the dry compounds.
STRATEGY_MIN_STINT = 8           # shortest stint the search considers (laps)
STRATEGY_SEARCH_STEP = 2         # stop-lap step for 2-stop plans (laps)
STRATEGY_TOP_N = 5               # plans carried into the Monte Carlo
DEG_RATIO_CLIP = (0.5, 2.0)      # bounds on this season's / last season's tyre severity
STRATEGY_MIN_DRIVERS = 4        # drivers a compound's deg fit needs to count towards severity
SEVERITY_MIN_USABLE = 0.25      # below this a race's wear was swamped by track evolution: not used

# ---------------------------------------------------------------------------
# Visual tokens (status palette: good / warning / critical, never reused for series)
# ---------------------------------------------------------------------------
STATUS_COLORS = {"good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b"}
STRATEGY_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7", "#e87ba4", "#008300"]
FALLBACK_DRIVER_COLORS = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
    "#008300", "#4a3aa7", "#e34948", "#7f7f7f", "#17becf",
]
# History pages (modules/history.py): a colour for each constructor of the past, keyed by the
# Jolpica team reference or its first part ("lotus-climax" -> "lotus"). Approximate liveries,
# kept away from near-black and near-white so lines read on both themes. Today's teams use
# Jolpica's own primary colours; anything not listed gets a FALLBACK_DRIVER_COLORS slot.
HISTORY_TEAM_COLORS = {
    "ferrari": "#dc0000", "alfa": "#9b1b30", "maserati": "#b5372d", "lago": "#2471a3",
    "gordini": "#1f4e9c", "mercedes": "#8a9aa8", "vanwall": "#1b6b3a", "connaught": "#3f8f4f",
    "cooper": "#2e6b3e", "brm": "#5b8c3a", "lotus": "#1d6b3a", "team_lotus": "#1d6b3a",
    "brabham": "#1e9a9a", "honda": "#b0485a", "mclaren": "#f47600", "matra": "#2d6cdf",
    "march": "#d94f00", "tyrrell": "#1f4f9a", "surtees": "#c0392b", "shadow": "#6b7a8a",
    "hesketh": "#8f8f9e", "penske": "#c44d9c", "wolf": "#a07a2d", "ligier": "#2e74c9",
    "williams": "#1868db", "renault": "#f2c300", "arrows": "#f28c28", "footwork": "#f28c28",
    "ats": "#d4ac0d", "toleman": "#3498db", "benetton": "#13a36b", "lola": "#e67e22",
    "osella": "#3d6fb6", "zakspeed": "#d64545", "minardi": "#8a7c3c", "dallara": "#c0392b",
    "larrousse": "#4a90d9", "jordan": "#e6c200", "sauber": "#01c00e", "stewart": "#6aa9d8",
    "prost": "#2d5bd7", "bar": "#c9505a", "jaguar": "#1e7a5a", "toyota": "#cc2233",
    "super_aguri": "#d65050", "bmw_sauber": "#2e86c1", "red_bull": "#4781d7",
    "toro_rosso": "#3550a0", "force_india": "#e8901a", "spyker": "#ef7d00", "hrt": "#a08b5b",
    "virgin": "#cc2b2b", "lotus_racing": "#3a8a3a", "caterham": "#0b8e5f", "marussia": "#c0392b",
    "lotus_f1": "#c9a227", "manor": "#e05050", "haas": "#9c9fa2", "racing_point": "#f596c8",
    "alphatauri": "#4a6890", "alpine": "#00a1e8", "rb": "#6c98ff", "aston_martin": "#229971",
    "kurtis_kraft": "#8e44ad", "fittipaldi": "#d4b82c", "ensign": "#16a085", "porsche": "#a3a3a3",
}
SC_SHADE = "rgba(250, 178, 25, 0.18)"
VSC_SHADE = "rgba(250, 178, 25, 0.09)"
RED_FLAG_SHADE = "rgba(208, 59, 59, 0.15)"


def _normalise(name: str) -> str:
    """Lower-case and strip accents so 'Montréal' matches 'montreal'."""
    folded = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return folded.strip().lower()


def get_pit_loss(*names: str | None) -> tuple[float, str | None]:
    """
    Look up the pit loss for a circuit.

    Accepts any number of candidate names (FastF1 Location, EventName, ...)
    and returns (seconds, matched_key). Falls back to DEFAULT_PIT_LOSS with
    matched_key=None when nothing matches.
    """
    lookup = {_normalise(k): k for k in TRACK_PIT_LOSS}
    for name in names:
        if not name:
            continue
        key = _normalise(name)
        if key in lookup:
            return TRACK_PIT_LOSS[lookup[key]], lookup[key]
        if key in PIT_LOSS_ALIASES:
            match = PIT_LOSS_ALIASES[key]
            return TRACK_PIT_LOSS[match], match
    return DEFAULT_PIT_LOSS, None
