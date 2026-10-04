"""
config.py — Global constants for F1 Stratbox.

Every tunable modelling assumption lives here so the strategy group can audit
and override them in one place. Values marked "approx." are public-domain
estimates intended as prototype defaults; replace them with the team's own
measured numbers before relying on them for a live call.
"""

from __future__ import annotations

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
# Weather model for the sandbox. When rain arrives, slicks become undriveable,
# the car is forced onto the wet-weather compound, and the deg slope switches
# to that compound's wet-track behaviour.
# ---------------------------------------------------------------------------
RAIN_PROFILES: dict[str, dict[str, float | str]] = {
    "Light rain": {
        "compound": "INTERMEDIATE",
        "pace_offset": 7.0,           # inter lap vs. fastest dry base pace
        "slick_penalty": 12.0,        # extra time per lap still on slicks
        "deg_multiplier": 1.0,        # applied to the wet compound's preset deg
    },
    "Heavy rain": {
        "compound": "WET",
        "pace_offset": 14.0,
        "slick_penalty": 28.0,
        "deg_multiplier": 1.0,
    },
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
FORM_DECAY = 0.75                # weight of each earlier race vs the next one (most recent = 1)
FORM_MAX_RACES = 6               # races that count towards form
FORECAST_SIMS = 10_000           # simulated races per forecast / simulated seasons for title odds
FORECAST_RACE_SD = 0.45          # % of lap time: race-to-race spread of a driver's pace. Best log-likelihood of
                                 # the actual winners over 2026 rounds 2-16 replayed (podium Brier score prefers ~0.7)
FORM_DRIFT_SD = 0.35             # % of lap time: how far a driver's form moves over the rest of a season, one
                                 # draw per simulated season (title odds). 2026: the sd of (mean pace over the
                                 # next 7 races - form now) is 0.41, part of which is race-to-race noise
DNF_PRIOR_STARTS = 10            # shrink each driver's DNF rate to the field's, as if from this many starts
CLASSIFIED_FRACTION = 0.9        # share of the winner's laps needed to be classified (FIA rule)

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
