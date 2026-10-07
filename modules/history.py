"""
modules/history.py — Every championship season since 1950, for the website's History pages.

The data is Jolpica's database dump (the successor to Ergast): one zip of CSV tables, free for
non-commercial use under CC BY-NC-SA 4.0. The free dump runs 14 days behind; that's fine here,
because the history ends with the last finished season (the current one is on the Season page).

Archive: each finished season's rows, from every table of the dump, are kept in the repo as
archive/jolpica/<year>.json.gz (CSV text per table, deterministic gzip) and read from there for
good. A season goes in once a dump from after ARCHIVE_AFTER of the next year has it (late
corrections in). Jolpica is only asked for the dump when a season to show isn't archived yet:
in practice once a year, for the season just finished. The zip is cached in .jolpica/ meanwhile.
Don't hand-edit archive files; delete one to have it fetched again.

    history/index.json         every season: champions, rounds; all-time totals
    history/drivers.json       every driver's career, and each of their seasons
    history/circuits.json      every circuit and every Grand Prix held there
    history/seasons/1988.json  a season: calendar, results of every race, standings, progression
    history/races/1988-16-R.json   lap-by-lap running order and pit stops (1996 on; sprints "-S")

What the dump has depends on the era: results, grids and standings from 1950 (pole is the car
that started first), lap times from 1996, fastest laps from 2004, pit stops from 2011.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import logging
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

import config
from modules.site_export import _write, columns

log = logging.getLogger(__name__)

DUMPS = "https://api.jolpi.ca/data/dumps/download/"
CACHE_DIR = config.PROJECT_ROOT / ".jolpica"
ZIP = CACHE_DIR / "jolpica-csv.zip"
INFO = CACHE_DIR / "jolpica-csv.json"           # the dump's listing entry: hash and upload time
ARCHIVE_DIR = config.PROJECT_ROOT / "archive" / "jolpica"
ARCHIVE_AFTER = (1, 15)                         # (month, day) of the next year a season counts as final
FIRST_YEAR = 1950
LICENCE = "CC BY-NC-SA 4.0"

RACE, SPRINT = "R", "SR"
QUALI = ("Q1", "Q2", "Q3", "QB", "QA")          # QO (2003-05 running order) isn't a result
FINISHED, LAPPED, DNS = 0, 1, 30               # Jolpica SessionStatus; 40/41 didn't (pre)qualify
NOT_STARTED = (30, 40, 41)

# The columns the pages use, per table. The archive keeps every column.
COLUMNS = {
    "season": ["id", "year"],
    "round": ["id", "season_id", "number", "name", "date", "circuit_id", "is_cancelled", "wikipedia"],
    "circuit": ["id", "reference", "name", "locality", "country", "latitude", "longitude"],
    "session": ["id", "round_id", "type", "number", "scheduled_laps"],
    "sessionentry": ["id", "session_id", "round_entry_id", "position", "grid", "is_classified", "status",
                     "detail", "laps_completed", "points", "time", "fastest_lap_rank"],
    "roundentry": ["id", "round_id", "team_driver_id", "car_number"],
    "teamdriver": ["id", "driver_id", "team_id", "season_id"],
    "driver": ["id", "reference", "abbreviation", "forename", "surname", "country_code", "date_of_birth", "wikipedia"],
    "team": ["id", "reference", "name", "country_code", "primary_color"],
    "driverchampionship": ["driver_id", "year", "round_number", "session_number", "points", "position", "win_count"],
    "teamchampionship": ["team_id", "year", "round_number", "session_number", "points", "position", "win_count"],
    "lap": ["id", "session_entry_id", "number", "position", "time", "is_entry_fastest_lap"],
    "pitstop": ["session_entry_id", "lap_id", "number", "duration"],
}
# Shared by many seasons: each season's file carries the rows it refers to, so they repeat across files.
REFERENCE_TABLES = ("driver", "team", "circuit")


# ---------------------------------------------------------------------------
# The dump
# ---------------------------------------------------------------------------
def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "f1-stratbox (history export)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_dump() -> Path:
    """The free (delayed) CSV dump, downloaded only when Jolpica has posted a newer one.

    If Jolpica can't be reached, the cached copy is used; with no cached copy this raises."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    have = json.loads(INFO.read_text()) if INFO.exists() and ZIP.exists() else None
    try:
        listing = _get_json(DUMPS)["delayed_dumps"]["csv"]
    except Exception as e:                                  # offline, or Jolpica down
        if have:
            print(f"Jolpica dump listing unavailable ({e}); using the cached dump of {have['uploaded_at']}")
            return ZIP
        raise
    if have and have["file_hash"] == listing["file_hash"]:
        return ZIP
    req = urllib.request.Request(listing["download_url"], headers={"User-Agent": "f1-stratbox (history export)"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != listing["file_hash"]:
        raise RuntimeError("Jolpica dump download doesn't match its published hash")
    ZIP.write_bytes(data)
    INFO.write_text(json.dumps(listing))
    return ZIP


def split_dump(path: Path) -> dict[int, dict[str, str]]:
    """The dump cut into seasons: {year: {table: CSV text of that season's rows}}. Every column,
    values exactly as in the dump (read as text, nothing parsed)."""
    with zipfile.ZipFile(path) as z:
        t = {n[len("formula_one_"):-len(".csv")]: pd.read_csv(z.open(n), dtype=str, keep_default_na=False)
             for n in z.namelist() if n.startswith("formula_one_") and n.endswith(".csv")}
    out = {}
    for s in t["season"].itertuples():
        rounds = t["round"][t["round"].season_id == s.id]
        sessions = t["session"][t["session"].round_id.isin(rounds.id)]
        tds = t["teamdriver"][t["teamdriver"].season_id == s.id]
        entries = t["sessionentry"][t["sessionentry"].session_id.isin(sessions.id)]
        dch = t["driverchampionship"][t["driverchampionship"].year == s.year]
        tch = t["teamchampionship"][t["teamchampionship"].year == s.year]
        rows = {
            "season": t["season"][t["season"].id == s.id],
            "round": rounds,
            "session": sessions,
            "roundentry": t["roundentry"][t["roundentry"].round_id.isin(rounds.id)],
            "sessionentry": entries,
            "teamdriver": tds,
            "lap": t["lap"][t["lap"].session_entry_id.isin(entries.id)],
            "pitstop": t["pitstop"][t["pitstop"].session_entry_id.isin(entries.id)],
            "penalty": t["penalty"][t["penalty"].earned_id.isin(entries.id)],
            "driverchampionship": dch,
            "teamchampionship": tch,
            "championshipadjustment": t["championshipadjustment"][t["championshipadjustment"].season_id == s.id],
            "driver": t["driver"][t["driver"].id.isin(set(tds.driver_id) | set(dch.driver_id))],
            "team": t["team"][t["team"].id.isin(set(tds.team_id) | set(tch.team_id))],
            "circuit": t["circuit"][t["circuit"].id.isin(rounds.circuit_id)],
            "championshipsystem": t["championshipsystem"][t["championshipsystem"].id == s.championship_system_id],
            "pointsystem": t["pointsystem"][t["pointsystem"].id.isin(sessions.point_system_id)],
        }
        out[int(s.year)] = {name: df.to_csv(index=False, lineterminator="\n") for name, df in rows.items()}
    return out


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------
def archive_path(year: int) -> Path:
    return ARCHIVE_DIR / f"{year}.json.gz"


def _write_archive(path: Path, data: dict) -> None:
    """Deterministic gzip (sorted keys, no timestamp), so a rewrite is never a git change."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))


def _read_archive(year: int) -> dict:
    return json.loads(gzip.decompress(archive_path(year).read_bytes()))


def is_final(year: int, uploaded_at: str) -> bool:
    """Whether a dump taken at `uploaded_at` has `year` in its final form."""
    month, day = ARCHIVE_AFTER
    return dt.datetime.fromisoformat(uploaded_at.replace("Z", "+00:00")).date() >= dt.date(year + 1, month, day)


def season_tables(last_year: int) -> tuple[dict[int, dict[str, str]], dict]:
    """Every season from 1950 to `last_year` as CSV text per table: archived seasons from the repo,
    the rest from the dump (fetched only if one is missing), archiving any that are now final."""
    have = {y: _read_archive(y) for y in range(FIRST_YEAR, last_year + 1) if archive_path(y).exists()}
    missing = [y for y in range(FIRST_YEAR, last_year + 1) if y not in have]
    seasons = {y: a["tables"] for y, a in have.items()}
    snapshots = [a["source"]["uploaded_at"] for a in have.values()]
    if missing:
        path = fetch_dump()
        listing = json.loads(INFO.read_text())
        split = split_dump(path)
        for y in missing:
            if y not in split:
                continue                                    # not in the dump yet
            seasons[y] = split[y]
            if is_final(y, listing["uploaded_at"]):
                source = {k: listing[k] for k in ("uploaded_at", "file_hash", "dump_type")}
                _write_archive(archive_path(y), {"year": y, "source": source, "licence": LICENCE, "tables": split[y]})
                print(f"Archived the {y} season to {archive_path(y).relative_to(config.PROJECT_ROOT)}")
        snapshots.append(listing["uploaded_at"])
    return dict(sorted(seasons.items())), {"uploaded_at": max(snapshots) if snapshots else None}


def _tables(seasons: dict[int, dict[str, str]]) -> dict[str, pd.DataFrame]:
    """The seasons' CSV text joined back into one table each (the columns the pages use)."""
    out = {}
    for name, cols in COLUMNS.items():
        parts = [pd.read_csv(io.StringIO(s[name]), dtype=str, keep_default_na=False) for s in seasons.values()]
        text = pd.concat(parts, ignore_index=True)[cols].to_csv(index=False)
        df = pd.read_csv(io.StringIO(text), low_memory=False)       # typed as if read from the dump itself
        if name in REFERENCE_TABLES:
            # The same driver, team or circuit in files archived from different dumps must agree on its id.
            clash = df.drop_duplicates(["id", "reference"]).id.duplicated()
            if clash.any():
                raise RuntimeError(f"history archive: {name} ids {sorted(df.id[clash].unique())[:5]} mean different "
                                   "things in different seasons' files; delete the newer files to fetch them again")
            df = df.drop_duplicates("id").reset_index(drop=True)
        out[name] = df
    return out


def _seconds(s: pd.Series) -> pd.Series:
    """'01:32:03.897' -> 5523.897; missing stays NaN."""
    return pd.to_timedelta(s, errors="coerce").dt.total_seconds()


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()


# ---------------------------------------------------------------------------
# Joining the tables
# ---------------------------------------------------------------------------
def _team_colors(team: pd.DataFrame) -> dict[int, str]:
    """A colour per team id: Jolpica's for today's teams, config's for the rest, else a fixed slot."""
    out = {}
    for t in team.itertuples():
        ref = str(t.reference)
        c = t.primary_color if isinstance(t.primary_color, str) and t.primary_color else None
        c = c or config.HISTORY_TEAM_COLORS.get(ref) or config.HISTORY_TEAM_COLORS.get(ref.split("-")[0])
        if not c:
            slot = int(hashlib.md5(ref.encode()).hexdigest(), 16) % len(config.FALLBACK_DRIVER_COLORS)
            c = config.FALLBACK_DRIVER_COLORS[slot]
        out[t.id] = c.lower()
    return out


def _driver_codes(driver: pd.DataFrame) -> dict[int, str]:
    """Three letters per driver: the official code where there is one, else from the surname."""
    out = {}
    for d in driver.itertuples():
        if isinstance(d.abbreviation, str) and d.abbreviation:
            out[d.id] = d.abbreviation
        else:
            letters = "".join(ch for ch in _ascii(d.surname).upper() if ch.isalpha())
            out[d.id] = (letters + "XXX")[:3]
    return out


class Data:
    """The dump's tables joined into what the pages need."""

    def __init__(self, t: dict[str, pd.DataFrame], last_year: int):
        self.last_year = last_year
        year_of = t["season"].set_index("id").year
        rounds = t["round"][t["round"].is_cancelled != "t"].copy()
        rounds["year"] = rounds.season_id.map(year_of)
        rounds = rounds[rounds.year <= last_year]
        circ = t["circuit"].set_index("id")
        rounds["circuit"] = rounds.circuit_id.map(circ.reference)
        rounds = rounds.rename(columns={"number": "round"}).sort_values(["year", "round"])
        self.rounds = rounds
        self.circuits = t["circuit"]

        sess = t["session"][t["session"].round_id.isin(rounds.id)].copy()
        sess = sess.merge(rounds[["id", "year", "round"]].rename(columns={"id": "round_id"}), on="round_id")
        self.sessions = sess

        driver, team = t["driver"], t["team"]
        self.driver = driver.set_index("id")
        self.team = team.set_index("id")
        self.code = _driver_codes(driver)
        self.color = _team_colors(team)

        re = t["roundentry"].merge(t["teamdriver"][["id", "driver_id", "team_id"]].rename(columns={"id": "team_driver_id"}),
                                   on="team_driver_id")
        e = t["sessionentry"].merge(re[["id", "driver_id", "team_id", "car_number"]].rename(columns={"id": "round_entry_id"}),
                                    on="round_entry_id")
        e = e.merge(sess[["id", "type", "round_id", "year", "round", "number"]].rename(columns={"id": "session_id", "number": "snum"}),
                    on="session_id")
        e["secs"] = _seconds(e.time)
        self.entries = e

        # Pole: whoever started from the front, as the record books count it (a qualifying P1
        # sent down the grid before the start loses it). P1 in the last qualifying session only
        # where no grid is recorded. Qualifying positions start in 1994, and from 2022 they're
        # per session, not overall: hence the last session.
        g = e[(e.type == RACE) & (e.grid == 1)].groupby("round_id").driver_id.first()
        q = e[e.type.isin(QUALI) & (e.position == 1)].sort_values("snum").groupby("round_id").driver_id.last()
        self.pole = g.combine_first(q)

        race = e[e.type == RACE]
        fl = race[race.fastest_lap_rank == 1].groupby("round_id").driver_id.first()
        self.fastest = fl

        def standings(df, key):
            df = df[df.year <= last_year].dropna(subset=["position"])
            # A round can have a sprint and a race: the standings after the round are the later one.
            df = df.sort_values(["year", "round_number", "session_number"])
            return df.groupby(["year", "round_number", key], as_index=False).last()
        self.dstand = standings(t["driverchampionship"], "driver_id")
        self.tstand = standings(t["teamchampionship"], "team_id")

        self.laps = t["lap"]
        self.pits = t["pitstop"]

    # -- small lookups
    def name(self, did) -> str:
        d = self.driver.loc[did]
        return f"{d.forename} {d.surname}"

    def team_name(self, tid) -> str:
        return str(self.team.loc[tid, "name"])

    def final_standings(self, df: pd.DataFrame) -> pd.DataFrame:
        last = df.groupby("year").round_number.transform("max")
        return df[df.round_number == last]


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------
def _result_rows(d: Data, e: pd.DataFrame, codes: dict[int, str]) -> pd.DataFrame:
    """One session's classification as exported rows, in finishing order."""
    e = e.copy()
    e["started"] = ~e.status.isin(NOT_STARTED)
    e["classified"] = e.is_classified == "t"
    e = e.sort_values(["classified", "position", "laps_completed"], ascending=[False, True, False], na_position="last")
    winner = e.secs.iloc[0] if len(e) and e.position.iloc[0] == 1 else np.nan
    gap = np.where(e.classified & (e.status == FINISHED) & (e.position > 1), e.secs - winner, np.nan)
    # Before 1960 or so a "time" can be the absolute race time of the car, or missing: only keep sane gaps.
    gap = np.where((gap > 0) & (gap < 3600), gap, np.nan)
    teams = e.team_id.tolist()
    seen: set[int] = set()
    second = []
    for t in teams:
        second.append(t in seen)
        seen.add(t)
    return pd.DataFrame({
        "pos": e.position.where(e.classified),
        "driver": e.driver_id.map(codes),
        "ref": e.driver_id.map(d.driver.reference),
        "name": [d.name(x) for x in e.driver_id],
        "team": [d.team_name(x) for x in e.team_id],
        "color": e.team_id.map(d.color),
        "second_driver": second,
        "number": e.car_number,
        "grid": e.grid.where(e.grid > 0),
        "laps": e.laps_completed,
        "time": np.where(e.position == 1, e.secs, np.nan),
        "gap": gap,
        "status": e.detail.fillna(""),
        "started": e.started,
        "points": e.points.fillna(0),
        "fl_rank": e.fastest_lap_rank,
    })


def _season_codes(d: Data, year: int) -> dict[int, str]:
    """Driver codes that are unique within the season (two Schumachers: MSC and RSC already;
    1950s namesakes get the forename's initial)."""
    ids = d.entries.loc[d.entries.year == year, "driver_id"].unique()
    codes = {i: d.code[i] for i in ids}
    counts = pd.Series(codes).value_counts()
    for i, c in codes.items():
        if counts[c] > 1:
            dr = d.driver.loc[i]
            codes[i] = (_ascii(dr.forename)[:1] + "".join(ch for ch in _ascii(dr.surname).upper() if ch.isalpha())[:2]).upper()
    return codes


def season_file(d: Data, year: int) -> dict:
    rounds = d.rounds[d.rounds.year == year]
    codes = _season_codes(d, year)
    e = d.entries[d.entries.year == year]
    circ = d.circuits.set_index("reference")

    cal, results = [], []
    for r in rounds.itertuples():
        here = e[e.round_id == r.id]
        race = here[here.type == RACE]
        sprint = here[here.type == SPRINT]
        res = _result_rows(d, race, codes)
        win = res.iloc[0] if len(res) else None
        pole, fl = d.pole.get(r.id), d.fastest.get(r.id)
        sess = d.sessions[(d.sessions.round_id == r.id) & (d.sessions.type == RACE)]
        cal.append({
            "round": int(r.round), "event": r.name, "date": r.date, "circuit": r.circuit,
            "circuit_name": circ.loc[r.circuit, "name"], "locality": circ.loc[r.circuit, "locality"],
            "country": circ.loc[r.circuit, "country"],
            "winner": win["driver"] if win is not None else None, "winner_name": win["name"] if win is not None else None,
            "winner_team": win["team"] if win is not None else None, "winner_color": win["color"] if win is not None else None,
            "pole": codes.get(pole) if pole is not None else None,
            "fastest": codes.get(fl) if fl is not None else None,
            "laps": int(race.laps_completed.max()) if len(race) else None,
            "scheduled_laps": _int(sess.scheduled_laps.iloc[0]) if len(sess) else None,
            "starters": int((~race.status.isin(NOT_STARTED)).sum()),
            "finishers": int((race.is_classified == "t").sum()),
            "sprint": bool(len(sprint)),
            "has_laps": bool(year >= 1996 and len(race)),
            "wikipedia": r.wikipedia if isinstance(r.wikipedia, str) else None,
        })
        res.insert(0, "round", int(r.round))
        res.insert(1, "session", "R")
        results.append(res)
        if len(sprint):
            sres = _result_rows(d, sprint, codes)
            sres.insert(0, "round", int(r.round))
            sres.insert(1, "session", "S")
            results.append(sres)
    results = pd.concat(results, ignore_index=True) if results else pd.DataFrame()

    # Standings: the dump's own, which apply each era's dropped-score rules.
    ds = d.dstand[d.dstand.year == year]
    final = ds[ds.round_number == ds.round_number.max()].sort_values("position")
    race_res = results[results.session == "R"] if len(results) else results
    by_driver = {}
    for ref, g in race_res.groupby("ref"):
        by_driver[ref] = g
    team_of = (e[e.type == RACE].groupby(["driver_id", "team_id"]).size().reset_index(name="n")
               .sort_values("n", ascending=False))
    drivers = []
    for s in final.itertuples():
        ref = d.driver.loc[s.driver_id, "reference"]
        g = by_driver.get(ref, pd.DataFrame(columns=race_res.columns))
        tids = team_of[team_of.driver_id == s.driver_id].team_id.tolist()
        drivers.append({
            "position": _int(s.position), "driver": codes.get(s.driver_id, d.code[s.driver_id]), "ref": ref,
            "name": d.name(s.driver_id), "team": " / ".join(d.team_name(t) for t in tids),
            "color": d.color[tids[0]] if tids else None, "points": float(s.points), "wins": _int(s.win_count),
            "podiums": int((g.pos <= 3).sum()), "poles": int(sum(1 for r in rounds.id if d.pole.get(r) == s.driver_id)),
            "starts": int(g.started.sum()), "best": _int(g.pos.min()) if g.pos.notna().any() else None,
            "points_scored": float(g.points.sum()),
        })
    ts = d.tstand[d.tstand.year == year]
    tfinal = ts[ts.round_number == ts.round_number.max()].sort_values("position")
    teams = [{"position": _int(s.position), "team": d.team_name(s.team_id), "color": d.color[s.team_id],
              "points": float(s.points), "wins": _int(s.win_count)} for s in tfinal.itertuples()]

    # Points through the season, for everyone who finished in the top ten.
    top = final.driver_id.head(10).tolist()
    prog = ds[ds.driver_id.isin(top)].sort_values(["round_number", "position"])
    progression = pd.DataFrame({
        "round": prog.round_number.astype(int), "driver": prog.driver_id.map(codes),
        "points": prog.points, "position": prog.position.astype(int),
    })
    return {
        "year": year,
        "rounds": cal,
        "drivers": drivers,
        "teams": teams,
        "progression": columns(progression),
        "results": columns(results),
    }


def race_files(d: Data, year: int) -> dict[str, dict]:
    """Lap-by-lap running order (and pit stops, from 2011) for each race and sprint of a season."""
    codes = _season_codes(d, year)
    e = d.entries[(d.entries.year == year) & d.entries.type.isin([RACE, SPRINT])]
    laps = d.laps[d.laps.session_entry_id.isin(e.id) & d.laps.number.notna()]
    if laps.empty:
        return {}
    laps = laps.merge(e[["id", "round", "type", "driver_id"]].rename(columns={"id": "session_entry_id"}), on="session_entry_id")
    laps["secs"] = _seconds(laps.time)
    pits = d.pits[d.pits.session_entry_id.isin(e.id)].merge(
        d.laps[["id", "number"]].rename(columns={"id": "lap_id", "number": "lap"}), on="lap_id", how="left")
    pits = pits.merge(e[["id", "round", "type", "driver_id"]].rename(columns={"id": "session_entry_id"}), on="session_entry_id")
    pits["secs"] = _seconds(pits.duration)
    out = {}
    for (rnd, typ), g in laps.groupby(["round", "type"]):
        g = g.sort_values(["driver_id", "number"])
        p = pits[(pits["round"] == rnd) & (pits.type == typ)].sort_values(["lap", "number"])
        pit_laps = set(zip(p.driver_id, p.lap))
        code = "S" if typ == SPRINT else "R"
        out[f"{year}-{int(rnd):02d}-{code}"] = {
            "laps": columns(pd.DataFrame({
                "driver": g.driver_id.map(codes), "lap": g.number.astype(int), "pos": g.position,
                "lap_time": g.secs, "pit": [(dr, n) in pit_laps for dr, n in zip(g.driver_id, g.number)],
            })),
            "pits": columns(pd.DataFrame({
                "driver": p.driver_id.map(codes), "stop": p.number.astype(int), "lap": p.lap,
                "duration": p.secs.where(p.secs < 120),     # a red-flag "stop" lasts the stoppage
            })),
        }
    return out


def driver_file(d: Data) -> dict:
    """Every driver's career totals, and one row per driver and season."""
    e = d.entries
    race = e[e.type == RACE].copy()
    race["started"] = ~race.status.isin(NOT_STARTED)
    race["classified"] = race.is_classified == "t"
    race["pos"] = race.position.where(race.classified)
    # Shared drives in the 1950s put one driver in two cars: count a start once a round.
    per_round = race.groupby(["driver_id", "year", "round_id"]).agg(
        started=("started", "max"), pos=("pos", "min"), points=("points", "sum"), team_id=("team_id", "first"),
    ).reset_index()
    per_round["win"] = per_round.pos == 1
    per_round["podium"] = per_round.pos <= 3
    per_round["pole"] = [d.pole.get(r) == dr for dr, r in zip(per_round.driver_id, per_round.round_id)]
    per_round["fastest"] = [d.fastest.get(r) == dr for dr, r in zip(per_round.driver_id, per_round.round_id)]

    final = d.final_standings(d.dstand)
    champs = final[final.position == 1]
    stand = final.set_index(["year", "driver_id"])

    seasons = per_round.groupby(["driver_id", "year"]).agg(
        starts=("started", "sum"), wins=("win", "sum"), podiums=("podium", "sum"), poles=("pole", "sum"),
        best=("pos", "min"), team_id=("team_id", lambda s: s.value_counts().index[0]),
    ).reset_index()
    seasons["position"] = [stand.position.get((y, dr)) for y, dr in zip(seasons.year, seasons.driver_id)]
    seasons["points"] = [stand.points.get((y, dr)) for y, dr in zip(seasons.year, seasons.driver_id)]
    seasons = seasons.sort_values(["driver_id", "year"])

    career = per_round.groupby("driver_id").agg(
        starts=("started", "sum"), wins=("win", "sum"), podiums=("podium", "sum"), poles=("pole", "sum"),
        fastest=("fastest", "sum"), first=("year", "min"), last=("year", "max"), seasons=("year", "nunique"),
    ).reset_index()
    pts = final.groupby("driver_id").points.sum()
    career["points"] = career.driver_id.map(pts).fillna(0)
    career["titles"] = career.driver_id.map(champs.driver_id.value_counts()).fillna(0).astype(int)
    career["title_years"] = career.driver_id.map(champs.groupby("driver_id").year.apply(list))
    teams = seasons.groupby("driver_id").team_id.apply(lambda s: list(dict.fromkeys(s)))
    career["team"] = career.driver_id.map(lambda i: d.team_name(teams[i][-1]))
    career["color"] = career.driver_id.map(lambda i: d.color[teams[i][-1]])
    career["teams"] = career.driver_id.map(lambda i: [d.team_name(t) for t in teams[i]])
    career = career[career.starts > 0]
    dr = d.driver.loc[career.driver_id]
    return {
        "drivers": columns(pd.DataFrame({
            "ref": dr.reference.values, "driver": career.driver_id.map(d.code).values,
            "name": (dr.forename + " " + dr.surname).values, "country": dr.country_code.values,
            "born": dr.date_of_birth.values, "wikipedia": dr.wikipedia.values,
            **{k: career[k].values for k in ["first", "last", "seasons", "starts", "wins", "podiums", "poles",
                                              "fastest", "titles", "points", "team", "color"]},
        })) | {
            # Lists don't go through columns()'s scalar cleaning untouched, so add them as they are.
            "title_years": [v if isinstance(v, list) else [] for v in career.title_years],
            "teams": career.teams.tolist(),
        },
        "seasons": columns(pd.DataFrame({
            "ref": seasons.driver_id.map(d.driver.reference), "year": seasons.year,
            "team": seasons.team_id.map(d.team_name), "color": seasons.team_id.map(d.color),
            "position": seasons.position, "points": seasons.points, "starts": seasons.starts, "wins": seasons.wins,
            "podiums": seasons.podiums, "poles": seasons.poles, "best": seasons.best,
        })),
    }


def team_rows(d: Data) -> pd.DataFrame:
    """Every constructor's totals: entries counted once a round, however many cars it ran."""
    race = d.entries[d.entries.type == RACE].copy()
    race["started"] = ~race.status.isin(NOT_STARTED)
    race["pos"] = race.position.where(race.is_classified == "t")
    per_round = race.groupby(["team_id", "year", "round_id"]).agg(started=("started", "max"), best=("pos", "min")).reset_index()
    pole_team = race[race.grid == 1].groupby("round_id").team_id.first()   # the pole car's team
    pole_by = {r: race[(race.round_id == r) & (race.driver_id == dr)].team_id.iloc[0]
               for r, dr in d.pole.items() if ((race.round_id == r) & (race.driver_id == dr)).any()}
    per_round["pole"] = [pole_by.get(r, pole_team.get(r)) == t for t, r in zip(per_round.team_id, per_round.round_id)]
    totals = per_round.groupby("team_id").agg(
        races=("started", "sum"), wins=("best", lambda s: int((s == 1).sum())), podium_races=("best", lambda s: int((s <= 3).sum())),
        poles=("pole", "sum"), first=("year", "min"), last=("year", "max"), seasons=("year", "nunique"),
    ).reset_index()
    tfinal = d.final_standings(d.tstand)
    totals["titles"] = totals.team_id.map(tfinal[tfinal.position == 1].team_id.value_counts()).fillna(0).astype(int)
    dfinal = d.final_standings(d.dstand)
    champ_team = {}
    for s in dfinal[dfinal.position == 1].itertuples():
        here = race[(race.year == s.year) & (race.driver_id == s.driver_id)]
        champ_team[s.year] = here.team_id.value_counts().index[0]
    totals["driver_titles"] = totals.team_id.map(pd.Series(champ_team).value_counts()).fillna(0).astype(int)
    totals = totals[totals.races > 0]
    return pd.DataFrame({
        "team": totals.team_id.map(d.team_name), "color": totals.team_id.map(d.color),
        "country": totals.team_id.map(d.team.country_code),
        **{k: totals[k] for k in ["first", "last", "seasons", "races", "wins", "podium_races", "poles", "titles", "driver_titles"]},
    })


def index_file(d: Data, source: dict) -> dict:
    dfinal = d.final_standings(d.dstand)
    tfinal = d.final_standings(d.tstand)
    race = d.entries[d.entries.type == RACE]
    seasons = []
    for year, rounds in d.rounds.groupby("year"):
        ds = dfinal[dfinal.year == year].sort_values("position")
        ts = tfinal[tfinal.year == year].sort_values("position")
        champ, second = (ds.iloc[0], ds.iloc[1]) if len(ds) > 1 else (None, None)
        here = race[race.year == year]
        ct = here[here.driver_id == champ.driver_id].team_id.value_counts().index[0] if champ is not None else None
        seasons.append({
            "year": int(year), "rounds": len(rounds),
            "champion": d.code[champ.driver_id] if champ is not None else None,
            "champion_ref": d.driver.loc[champ.driver_id, "reference"] if champ is not None else None,
            "champion_name": d.name(champ.driver_id) if champ is not None else None,
            "champion_team": d.team_name(ct) if ct is not None else None,
            "champion_color": d.color[ct] if ct is not None else None,
            "champion_points": float(champ.points) if champ is not None else None,
            "champion_wins": _int(champ.win_count) if champ is not None else None,
            "runner_up": d.name(second.driver_id) if second is not None else None,
            "margin": float(champ.points - second.points) if champ is not None else None,
            "constructor": d.team_name(ts.iloc[0].team_id) if len(ts) else None,
            "constructor_color": d.color[ts.iloc[0].team_id] if len(ts) else None,
            "drivers": int(here.driver_id.nunique()),
        })
    return {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": {"name": "Jolpica F1 (Ergast) database dump", "licence": LICENCE,
                   "uploaded_at": source["uploaded_at"]},
        "first_year": int(d.rounds.year.min()), "last_year": int(d.rounds.year.max()),
        "races": int(len(d.rounds)), "drivers": int(race[~race.status.isin(NOT_STARTED)].driver_id.nunique()),
        "seasons": seasons,
        "teams": columns(team_rows(d)),
    }


def circuit_file(d: Data) -> dict:
    race = d.entries[(d.entries.type == RACE) & (d.entries.position == 1) & (d.entries.is_classified == "t")]
    win = race.groupby("round_id").first()
    r = d.rounds.copy()
    r["winner_id"] = r.id.map(win.driver_id)
    r["team_id"] = r.id.map(win.team_id)
    r["winner_grid"] = r.id.map(win.grid)
    sched = d.sessions[d.sessions.type == RACE].groupby("round_id").scheduled_laps.first()
    r["sched"] = r.id.map(sched).fillna(r.id.map(win.laps_completed))
    r = r.dropna(subset=["winner_id"])
    # Each race's fastest lap (lap times from 1996): the quickest timed lap of any car in it.
    entries = d.entries[d.entries.type == RACE].set_index("id")
    laps = d.laps[d.laps.session_entry_id.isin(entries.index)][["session_entry_id", "time"]].copy()
    laps["secs"] = _seconds(laps.time)
    laps = laps.dropna(subset=["secs"])
    laps["round_id"] = laps.session_entry_id.map(entries.round_id)
    best = laps.loc[laps.groupby("round_id").secs.idxmin()].set_index("round_id")
    best_driver = best.session_entry_id.map(entries.driver_id)
    races = pd.DataFrame({
        "circuit": r.circuit, "year": r.year, "round": r["round"], "event": r.name,
        "winner": r.winner_id.map(d.code), "winner_ref": r.winner_id.map(d.driver.reference),
        "winner_name": [d.name(x) for x in r.winner_id], "team": r.team_id.map(d.team_name),
        "color": r.team_id.map(d.color), "pole": [d.code.get(d.pole.get(i)) for i in r.id],
        "winner_grid": r.winner_grid,
        "fl_time": r.id.map(best.secs),
        "fl_name": [d.name(best_driver[i]) if i in best_driver.index else None for i in r.id],
        "laps": r.sched,
    })
    c = d.circuits.set_index("reference")
    rows = []
    for ref, g in races.groupby("circuit"):
        top = g.winner_name.value_counts()
        # Fastest race lap on today's layout: a layout change (Silverstone 2010) usually changes the
        # scheduled distance in laps, so only races over the latest race's lap count; and laps under 90%
        # of its fastest (a shorter loop under the same name, such as Bahrain's outer circuit) don't count.
        timed = g.dropna(subset=["fl_time"]).sort_values("year")
        if len(timed):
            latest = timed.iloc[-1]
            timed = timed[(timed.laps == latest.laps) & (timed.fl_time >= 0.9 * latest.fl_time)]
            rec = timed.loc[timed.fl_time.idxmin()]
        rows.append({"circuit": ref, "name": c.loc[ref, "name"], "locality": c.loc[ref, "locality"],
                     "country": c.loc[ref, "country"], "lat": c.loc[ref, "latitude"], "lon": c.loc[ref, "longitude"],
                     "races": len(g), "first": int(g.year.min()), "last": int(g.year.max()),
                     "top_winner": top.index[0], "top_wins": int(top.iloc[0]),
                     "record": float(rec.fl_time) if len(timed) else None,
                     "record_by": rec.fl_name if len(timed) else None,
                     "record_year": int(rec.year) if len(timed) else None})
    return {"circuits": columns(pd.DataFrame(rows)), "races": columns(races)}


def _int(v):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v) else int(v)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def export_history(out: Path, last_year: int | None = None) -> dict:
    """Write history/ under `out` for every season up to `last_year` (default: last year)."""
    last_year = last_year or dt.date.today().year - 1
    seasons, source = season_tables(last_year)
    d = Data(_tables(seasons), last_year)
    base = out / "history"
    index = index_file(d, source)
    _write(base / "index.json", index)
    _write(base / "drivers.json", driver_file(d))
    _write(base / "circuits.json", circuit_file(d))
    n_laps = 0
    for year in sorted(d.rounds.year.unique()):
        _write(base / "seasons" / f"{year}.json", season_file(d, int(year)))
        for name, data in race_files(d, int(year)).items():
            _write(base / "races" / f"{name}.json", data)
            n_laps += 1
    return {"seasons": len(index["seasons"]), "races": index["races"], "lap_files": n_laps}
