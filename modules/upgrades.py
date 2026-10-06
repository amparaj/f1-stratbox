"""
modules/upgrades.py — Car upgrades, from the FIA's "Car Presentation Submissions".

At every event (since 2023) each team lists the components it has updated, with the primary
reason ("Performance - Local Load", "Circuit specific - Drag Range", "Reliability", "Cooling")
and a short description. The FIA publishes it as a PDF on the Thursday: `season_upgrades` reads
it for every round (through modules/penalties.py's FIA fetching and cache), one row per item,
team matched to the site's team names. An event is archived (archive/fia/<year>/rNN-upgrades.json.gz)
FIA_FINAL_DAYS after its race.

Whether an upgrade works is a separate question: `upgrade_effects` measures each team's pace at
the rounds after a performance upgrade against its pace before, and forecast.expected_pace adds
UPGRADE_EFFECT per performance item (fitted by calibrate_forecast.py; it may well be ~0: teams
bring parts every few rounds and many are trims).
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import re

import pandas as pd

import config
from modules import penalties as pn

ARCHIVE = pn.FIA_ARCHIVE
FIRST_YEAR = 2023

# FIA team names -> one key each, checked in order ("Racing Bulls" before "Red Bull").
TEAM_KEYS = [
    ("racing bulls", "Racing Bulls"), ("visa cash app", "Racing Bulls"), ("alphatauri", "Racing Bulls"),
    ("scuderia alphatauri", "Racing Bulls"), ("rb f1", "Racing Bulls"),
    ("red bull", "Red Bull Racing"), ("mclaren", "McLaren"), ("mercedes", "Mercedes"), ("ferrari", "Ferrari"),
    ("williams", "Williams"), ("aston martin", "Aston Martin"), ("haas", "Haas F1 Team"), ("alpine", "Alpine"),
    ("audi", "Audi"), ("sauber", "Audi"), ("alfa romeo", "Audi"), ("kick", "Audi"), ("stake", "Audi"),
    ("cadillac", "Cadillac"),
]
REASONS = [("circuit", r"circuit\s*specific"), ("reliability", r"reliability"), ("cooling", r"cooling"),
           ("performance", r"performance")]


def team_of(name: str) -> str | None:
    n = config._normalise(name)
    return next((key for k, key in TEAM_KEYS if k in n), None)


def parse(text: str) -> list[dict]:
    """One row per updated component: team (key), team_name, item, component, reason, text."""
    rows = []
    blocks = re.split(r"Car Presentation\s*\W\s*[^\n]*\n", text)[1:]
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines()]
        name = next((ln for ln in lines if ln and not ln.startswith(("Updated", "component"))), "")
        team = team_of(name)
        if team is None or "no updates" in block.lower():
            continue
        items, cur, expect = [], None, 1
        for ln in lines:
            m = re.match(r"^(\d{1,2})\s+(\S.*)$", ln)
            if m and int(m.group(1)) == expect:
                cur = {"n": expect, "lines": [m.group(2)]}
                items.append(cur)
                expect += 1
            elif cur is not None and ln:
                cur["lines"].append(ln)
        for it in items:
            body = " ".join(it["lines"])
            head = " ".join(it["lines"][:4]).lower()
            reason = next((r for r, pat in REASONS if re.search(pat, head)), "other")
            if reason == "other" and re.search(r"load|drag|flow conditioning|downforce", head):
                reason = "performance"
            # The component is what comes before the reason on the item's first lines.
            cut = re.search(r"performance|circuit\s*specific|reliability|cooling", " ".join(it["lines"][:3]), re.I)
            comp = " ".join(it["lines"][:3])[:cut.start()].strip(" -") if cut else it["lines"][0]
            rows.append({"team": team, "team_name": name, "item": it["n"], "component": comp[:60],
                         "reason": reason, "text": body[:600]})
    return rows


def archive_path(year: int, rnd: int):
    return ARCHIVE / str(year) / f"r{rnd:02d}-upgrades.json.gz"


def season_upgrades(year: int, now: dt.datetime | None = None) -> pd.DataFrame:
    """Every round's upgrade items for `year`: round + parse()'s columns. Archived rounds from the
    repo, the rest from fia.com (penalties.py's cache), archiving those that are final."""
    now = now or dt.datetime.now(dt.timezone.utc)
    rows, have = [], set()
    for p in sorted((ARCHIVE / str(year)).glob("r*-upgrades.json.gz")):
        rec = json.loads(gzip.decompress(p.read_bytes()))
        have.add(rec["round"])
        rows += [{"round": rec["round"], **r} for r in rec["items"]]
    sched = pn._schedule(year)
    due = sched[(sched["start"] - pd.Timedelta(days=2) <= now) & ~sched["round"].isin(have)]
    if not due.empty:
        fresh = dt.timedelta(hours=config.FIA_PAGE_FRESH_HOURS)
        try:
            season_url = pn._season_pages(fresh).get(year)
            events = pn._events(season_url, None if now > sched["race"].max() + pd.Timedelta(days=5) else fresh) \
                if season_url else {}
        except Exception as exc:  # noqa: BLE001 — FIA site down: what's archived
            print(f"  fia.com (upgrades): {str(exc)[:120]}", flush=True)
            events = {}
        for name, url in events.items():
            try:
                docs = pn._documents(url, None)
                rnd = pn._round_of(docs, sched)
                if rnd is None or rnd in have or rnd not in set(due["round"]):
                    continue
                race = sched.loc[sched["round"] == rnd, "race"].iloc[0]
                final = now > race + pd.Timedelta(days=pn.FIA_FINAL_DAYS)
                if not final:
                    docs = pn._documents(url, fresh)
                doc = next((d for d in docs if re.search(r"car presentation", d["title"], re.I)), None)
                if doc is None:
                    continue
                items = parse(pn._pdf_text(doc["url"]))
                rows += [{"round": rnd, **r} for r in items]
                if final and items:
                    pn._write_gz(archive_path(year, rnd), json.dumps(
                        {"year": year, "round": rnd, "event": name, "source": doc["url"], "items": items},
                        sort_keys=True, separators=(",", ":")).encode())
            except Exception as exc:  # noqa: BLE001 — one bad document: skip the event
                print(f"  fia.com upgrades {year} {name}: {str(exc)[:120]}", flush=True)
    return pd.DataFrame(rows, columns=["round", "team", "team_name", "item", "component", "reason", "text"])


def performance_counts(ups: pd.DataFrame) -> pd.DataFrame:
    """Performance items per team and round (index round, columns team)."""
    if ups.empty:
        return pd.DataFrame()
    p = ups[ups["reason"] == "performance"]
    return p.groupby(["round", "team"]).size().unstack(fill_value=0)
