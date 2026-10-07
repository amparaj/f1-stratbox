"""
Team badges for the dashboard: the team's logo where a freely licensed one exists, else its colour
with a short code. Same manifest and rules as the website (web/src/teamLogos.json, web/src/teams.ts);
the logo files are in web/public/logos/ (scripts/fetch_team_logos.py).

A badge is an SVG data URI, so it goes in an `st.column_config.ImageColumn` (`with_badges` adds one).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
import streamlit as st

import config

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "web" / "src" / "teamLogos.json"
LOGOS = ROOT / "web" / "public" / "logos"
STOP = {"team", "f1", "racing", "grand", "prix", "gp", "formula", "one", "the", "scuderia"}
BADGE_COLUMN = "Logo"


@lru_cache(maxsize=1)
def _teams() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["teams"]


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", config._normalise(name).replace("&amp;", "&"))


def _entry(name: str, year: int | None) -> dict | None:
    """Full name first, then the chassis part ('Lotus-Climax' -> 'lotus'); 'years' must hold the season."""
    full = _norm(name)
    for n in (full, full.split("-")[0].strip()):
        for t in _teams():
            yrs = t.get("years")
            if n in t["names"] and (year is None or not yrs or yrs[0] <= year <= yrs[1]):
                return t
    return None


def logo(name: str, year: int | None = None) -> dict | None:
    """The manifest's logo for that season (no season: the latest), if its file is there."""
    logos = (_entry(name, year) or {}).get("logos", [])
    if year is None:
        hit = logos[-1] if logos else None
    else:
        hit = next((lg for lg in logos if lg.get("since", 0) <= year <= lg.get("until", 9999)), None)
    return hit if hit and (LOGOS / hit["file"]).exists() else None


def short(name: str, year: int | None = None) -> str:
    """The manifest's code, else three letters of a one-word name or the initials of a longer one."""
    e = _entry(name, year)
    if e and e.get("short"):
        return e["short"]
    words = name.split("-")[0].replace("&amp;", "&").split()
    use = [w for w in words if _norm(w) not in STOP] or words
    return (use[0][:3] if len(use) == 1 else "".join(w[0] for w in use[:3])).upper()


def _ink(color: str) -> str:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", color or "")
    if not m:
        return "#fff"
    r, g, b = (int(m[1][i:i + 2], 16) for i in (0, 2, 4))
    return "#111" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#fff"


@lru_cache(maxsize=512)
def badge_uri(name: str, color: str | None = None, year: int | None = None) -> str:
    """84x40 SVG (shown at half size): logo on white edged in the team colour, or colour + code."""
    c = color if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#7f7f7f"
    lg = logo(name, year)
    if lg:
        path = LOGOS / lg["file"]
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data = base64.b64encode(path.read_bytes()).decode()
        body = (f'<clipPath id="r"><rect width="84" height="40" rx="8"/></clipPath><g clip-path="url(#r)">'
                f'<rect width="84" height="40" fill="#fff"/><rect width="6" height="40" fill="{c}"/></g>'
                f'<rect x="1" y="1" width="82" height="38" rx="7" fill="none" stroke="#0b0b0b" stroke-opacity="0.12" stroke-width="2"/>'
                f'<image href="data:{mime};base64,{data}" x="11" y="4" width="68" height="32" '
                f'preserveAspectRatio="xMidYMid meet"/>')
    else:
        body = (f'<rect width="84" height="40" rx="8" fill="{c}"/>'
                f'<text x="44" y="27" text-anchor="middle" font-family="Segoe UI, Arial, sans-serif" font-size="21" '
                f'font-weight="700" fill="{_ink(c)}">{escape(short(name, year))}</text>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="84" height="40" viewBox="0 0 84 40"><title>{escape(name)}</title>{body}</svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def with_badges(df: pd.DataFrame, colors: dict[str, str] | None = None, year: int | None = None,
                team_col: str = "Team") -> pd.DataFrame:
    """`df` with a badge column just before `team_col` (team -> colour from `colors`)."""
    colors = colors or {}
    badges = [badge_uri(t, colors.get(t), year) if isinstance(t, str) and t else None for t in df[team_col]]
    out = df.copy()
    out.insert(out.columns.get_loc(team_col), BADGE_COLUMN, badges)
    return out


def badge_column() -> dict:
    """column_config entry for the badge column."""
    return {BADGE_COLUMN: st.column_config.ImageColumn("", width=60)}


def team_colors(drivers: pd.DataFrame) -> dict[str, str]:
    """Team -> colour from a get_session_info driver table."""
    if drivers.empty or "Team" not in drivers or "Color" not in drivers:
        return {}
    return drivers.dropna(subset=["Team"]).groupby("Team")["Color"].first().to_dict()
