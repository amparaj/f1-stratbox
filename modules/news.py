"""
modules/news.py — F1 news from the main sites' RSS feeds, scanned for power-unit penalties,
stewards' penalties and car upgrades.

Feeds (config.NEWS_FEEDS): The Race, Crash.net, Autosport, Motorsport.com, Formula1.com, BBC Sport,
Sky Sports, GPFans. Each is read at most every NEWS_FRESH_HOURS; items are kept in .news/items.json
for NEWS_KEEP_DAYS (the feeds only show the last day or two), never in the repo: only the headline,
link, date and the feed's own short summary, and the site links to the article.

Each item gets topics (pu: a power-unit change or penalty; penalty: the stewards; upgrade: new
parts) and what it names: drivers (by surname, from the season's line-up), teams, and rounds of the
calendar (event, city, country or circuit nicknames). A sentence with a negation ("won't", "avoid",
"no penalty") doesn't count.

`pu_signals`: a driver, a round still to run and a power-unit penalty in the same sentence. When at
least NEWS_PLAN_MIN_SOURCES different sites say so within NEWS_PLAN_DAYS, it's a reported plan
(`news_plans`), which the penalty model counts like config.PU_PLANS: one penalty, at the first of
its rounds it doesn't slip past, with at least PU_PLAN_HAZARD at each.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET

import pandas as pd
import requests

import config
from modules.upgrades import TEAM_KEYS

CACHE = config.PROJECT_ROOT / ".news"
HEADERS = {"User-Agent": "Mozilla/5.0 (f1-stratbox; +https://amparaj.github.io/f1-stratbox/)"}

TOPICS = {
    "pu": re.compile(r"\b(engine|power[ -]?units?|PU|ICE|turbo(charger)?|MGU-?[KH]|energy store|gearbox|ADUO)\b", re.I),
    "penalty": re.compile(r"penalt|grid drop|back of the grid|pit[ -]lane start|stewards|investigat|disqualif|reprimand", re.I),
    "upgrade": re.compile(r"upgrade|update package|(aero|floor|wing|car) update|new (floor|front wing|rear wing|"
                          r"sidepods?|bodywork|diffuser|package|parts)|aero package|ADUO", re.I),
}
PU_PENALTY = re.compile(r"grid (penalty|drop)|engine penalty|back of the grid|pit[ -]lane start|penalt", re.I)
NEGATION = re.compile(r"\b(won't|will not|not (?:take|get|face|receive|need)|avoid\w*|escape\w*|no (?:grid )?penalty|"
                      r"rule[sd]? out|dodge\w*)\b", re.I)
# Circuit nicknames -> words in the calendar's event, location or country.
ROUND_ALIASES = {"austin": "United States", "cota": "United States", "sepang": "Malaysia", "kuala lumpur": "Malaysia",
                 "interlagos": "São Paulo", "brazil": "São Paulo", "vegas": "Las Vegas", "lusail": "Qatar",
                 "yas": "Abu Dhabi", "marina bay": "Singapore", "monza": "Italian", "spa": "Belgian", "zandvoort": "Dutch",
                 "silverstone": "British", "suzuka": "Japanese", "shanghai": "Chinese", "melbourne": "Australian",
                 "baku": "Azerbaijan", "montreal": "Canadian", "imola": "Emilia", "jeddah": "Saudi",
                 "mexico": "Mexico City", "madrid": "Spanish", "barcelona": "Barcelona", "hungaroring": "Hungarian",
                 "red bull ring": "Austrian", "miami": "Miami", "monaco": "Monaco"}


def _text(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _date(s: str | None) -> str | None:
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat()


def parse_feed(source: str, body: bytes) -> list[dict]:
    """RSS (or Atom) items: source, title, link, published (UTC ISO), summary (plain text)."""
    root = ET.fromstring(body)
    out = []
    for it in root.iter():
        tag = it.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        get = {c.tag.rsplit("}", 1)[-1]: c for c in it}
        link = get.get("link")
        href = (link.get("href") or link.text) if link is not None else None
        title = _text(get["title"].text if "title" in get else "")
        summary = _text((get.get("description") if get.get("description") is not None else get.get("summary")).text
                        if (get.get("description") is not None or get.get("summary") is not None) else "")
        when = _date((get.get("pubDate") if get.get("pubDate") is not None else get.get("published")).text
                     if (get.get("pubDate") is not None or get.get("published") is not None) else None)
        if title and href:
            out.append({"source": source, "title": title, "link": href.strip(), "published": when,
                        "summary": summary[:400]})
    return out


def fetch(now: dt.datetime | None = None) -> list[dict]:
    """Every feed's items merged into the kept list (.news/items.json), newest first."""
    now = now or dt.datetime.now(dt.timezone.utc)
    CACHE.mkdir(parents=True, exist_ok=True)
    store = CACHE / "items.json"
    kept = json.loads(store.read_text(encoding="utf-8")) if store.exists() else {"items": [], "fetched": {}}
    by_link = {i["link"]: i for i in kept["items"]}
    for source, url in config.NEWS_FEEDS.items():
        last = kept["fetched"].get(source)
        if last and now - dt.datetime.fromisoformat(last) < dt.timedelta(hours=config.NEWS_FRESH_HOURS):
            continue
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            items = parse_feed(source, r.content)
        except Exception as exc:  # noqa: BLE001 — one feed down: the others still count
            print(f"  news {source}: {str(exc)[:100]}", flush=True)
            continue
        for i in items:
            i.setdefault("published", now.isoformat())
            i["published"] = i["published"] or now.isoformat()
            by_link.setdefault(i["link"], i)
        kept["fetched"][source] = now.isoformat()
    cutoff = now - dt.timedelta(days=config.NEWS_KEEP_DAYS)
    items = sorted((i for i in by_link.values() if dt.datetime.fromisoformat(i["published"]) >= cutoff),
                   key=lambda i: i["published"], reverse=True)
    kept["items"] = items
    store.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    return items


def _sentences(item: dict) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\s+\|\s+", item["title"] + ". " + item["summary"]) if s.strip()]


def tag(items: list[dict], drivers: dict[str, str], teams: list[str], events: list[dict]) -> list[dict]:
    """Add topics, drivers (codes), teams and rounds to each item. `drivers`: code -> full name;
    `events`: the calendar ({"round", "event", "location", "country"})."""
    surname = {c: n.split()[-1] for c, n in drivers.items() if isinstance(n, str) and n}
    words = []
    for e in events:
        names = {e["event"].replace(" Grand Prix", ""), e["location"], e["country"]}
        names |= {k for k, v in ROUND_ALIASES.items() if any(v.lower() in n.lower() for n in names | {e["event"]})}
        words.append((e["round"], [n for n in names if n and len(n) > 3]))

    def rounds_in(text: str) -> list[int]:
        low = text.lower()
        return sorted({r for r, ns in words if any(re.search(rf"\b{re.escape(n.lower())}\b", low) for n in ns)})

    def drivers_in(text: str) -> list[str]:
        return [c for c, s in surname.items() if re.search(rf"\b{re.escape(s)}\b", text, re.I)]

    out = []
    for i in items:
        text = i["title"] + " " + i["summary"]
        topics = [t for t, rx in TOPICS.items() if rx.search(text)]
        low = config._normalise(text)
        named = {key for k, key in TEAM_KEYS if re.search(rf"\b{re.escape(k)}\b", low)}
        # "Red Bull" inside "Racing Bulls" isn't Red Bull Racing.
        if "Racing Bulls" in named and not re.search(r"\bred bull\b(?! ?racing bulls)", low.replace("racing bulls", "")):
            named.discard("Red Bull Racing")
        out.append({**i, "id": hashlib.sha1(i["link"].encode()).hexdigest()[:12], "topics": topics,
                    "drivers": drivers_in(text), "teams": sorted(t for t in named if t in teams),
                    "rounds": rounds_in(text),
                    "pu_claims": [{"driver": d, "round": r}
                                  for s in _sentences(i) if TOPICS["pu"].search(s) and PU_PENALTY.search(s)
                                  and not NEGATION.search(s)
                                  for d in drivers_in(s) for r in rounds_in(s)]})
    return out


def news_plans(tagged: list[dict], next_round: int | None, now: dt.datetime | None = None) -> dict[str, list[dict]]:
    """
    Reported power-unit penalties: {driver: [{"rounds": [r], "sources": [...], "links": [...]}]} for
    each (driver, round still to run) at least NEWS_PLAN_MIN_SOURCES sites put in the same sentence as
    a power-unit penalty within NEWS_PLAN_DAYS.
    """
    if next_round is None:
        return {}
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=config.NEWS_PLAN_DAYS)
    seen: dict[tuple[str, int], dict] = {}
    for i in tagged:
        if dt.datetime.fromisoformat(i["published"]) < cutoff:
            continue
        for c in i["pu_claims"]:
            if c["round"] < next_round:
                continue
            s = seen.setdefault((c["driver"], c["round"]), {"sources": set(), "links": []})
            if i["source"] not in s["sources"]:
                s["sources"].add(i["source"])
                s["links"].append({"source": i["source"], "title": i["title"], "link": i["link"], "published": i["published"]})
    out: dict[str, list[dict]] = {}
    for (d, r), s in sorted(seen.items()):
        if len(s["sources"]) >= config.NEWS_PLAN_MIN_SOURCES:
            out.setdefault(d, []).append({"rounds": [r], "sources": sorted(s["sources"]), "links": s["links"],
                                          "note": f"Reported by {', '.join(sorted(s['sources']))}"})
    return out


def table(tagged: list[dict]) -> pd.DataFrame:
    """The tagged items that matter here (any topic), for the site."""
    rows = [i for i in tagged if i["topics"]]
    return pd.DataFrame(rows, columns=["id", "source", "title", "link", "published", "summary", "topics", "drivers",
                                       "teams", "rounds", "pu_claims"])
