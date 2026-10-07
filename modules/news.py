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

A claim is *quoted* (`quoted`) when it comes from the team or the driver: an item's sentence about a
power-unit penalty that's attributed to them ("Mercedes confirmed", "Wolff said", "Russell told ...")
or carries their words in quotation marks, with no hedge ("could", "might", "if", "risk").
One site is enough for a quoted claim, and its plan gets PU_PLAN_HAZARD_QUOTED at that round:
several sites repeating one briefing isn't more evidence, the team saying it is.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import gzip
import hashlib
import html
import json
import urllib.parse
import urllib.robotparser
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
# Who is speaking: a team, its boss or a driver (surname / team key / role) next to one of these verbs.
ATTRIBUTION = re.compile(r"\b(confirm(?:s|ed|ing)?|announc(?:e|es|ed|ing)|sa(?:id|ys)|told|tells|explain(?:s|ed)|"
                         r"reveal(?:s|ed)|admit(?:s|ted)|insist(?:s|ed)|statement|according to)\b", re.I)
ROLE = re.compile(r"\b(team principal|team boss|boss|spokes(?:man|woman|person)|the team|engineer)\b", re.I)
# Words in double quotes (or ‘…’): four or more. Shorter is a scare quote ('double whammy'); a
# straight ' is left out, it's mostly an apostrophe.
QUOTE = re.compile(r"[\"“‘][^\"“”‘’]*?(?:\S+\s+){3,}[^\"“”‘’]*?[\"”’]")
# Speculation, even in the team's words, isn't a plan.
HEDGE = re.compile(r"\b(could|might|may|if|risk\w*|possib\w*|potential\w*|consider\w*|expect\w*|likely|set to|"
                   r"set for|rumou?r\w*|reportedly|understood|believe[sd]?|speculat\w*)\b|\?", re.I)
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


# The link text some feeds end a teaser with ("... Keep reading"): the item's link is shown anyway.
READ_ON = re.compile(r"\s*(?:\.\.\.|…)?\s*(?:keep reading|continue reading|read more|read the full (?:story|article))\.?\s*$", re.I)
# An item kept before summaries were stored whole: cut at 400 characters, mid-"Keep reading".
OLD_CUT = re.compile(r"\s*(?:\.\.\.|…)\s*\w{0,12}$")


def clean_summary(s: str) -> str:
    """The feed's summary in full, its "Keep reading" link text dropped; a teaser ends with "…"."""
    s = s or ""
    if READ_ON.search(s) or (len(s) == 400 and OLD_CUT.search(s[-20:])):
        s = (READ_ON if READ_ON.search(s) else OLD_CUT).sub("", s).rstrip() + " …"
    return s


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
                        "summary": clean_summary(summary)})
    return out


def fetch(now: dt.datetime | None = None) -> list[dict]:
    """Every feed's items merged into the kept list (.news/items.json), newest first."""
    now = now or dt.datetime.now(dt.timezone.utc)
    CACHE.mkdir(parents=True, exist_ok=True)
    store = CACHE / "items.json"
    kept = json.loads(store.read_text(encoding="utf-8")) if store.exists() else {"items": [], "fetched": {}}
    by_link = {i["link"]: {**i, "summary": clean_summary(i.get("summary", ""))} for i in kept["items"]}
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
            old = by_link.get(i["link"])
            if old is None:
                by_link[i["link"]] = i
            elif old.get("teaser", old["summary"]) != i["summary"]:     # an edited summary: complete it again
                old.update(title=i["title"], summary=i["summary"])
                old.pop("teaser", None)
            else:
                old["title"] = i["title"]
        kept["fetched"][source] = now.isoformat()
    complete_teasers(list(by_link.values()))
    cutoff = now - dt.timedelta(days=config.NEWS_KEEP_DAYS)
    items = sorted((i for i in by_link.values() if dt.datetime.fromisoformat(i["published"]) >= cutoff),
                   key=lambda i: i["published"], reverse=True)
    kept["items"] = items
    store.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    return items


_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def _allowed(url: str) -> bool:
    """The site's robots.txt lets us read this page (none, or a 4xx: yes; unreachable: no)."""
    host = urllib.parse.urlsplit(url)
    root = f"{host.scheme}://{host.netloc}"
    if root not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(root + "/robots.txt", headers=HEADERS, timeout=15)
            rp.parse(r.text.splitlines() if r.status_code < 400 else [])
            _robots[root] = rp if r.status_code < 500 else None
        except Exception:  # noqa: BLE001
            _robots[root] = None
    rp = _robots[root]
    return rp is not None and rp.can_fetch(HEADERS["User-Agent"], url)


def _norm(s: str) -> str:
    s = html.unescape(s).translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0xA0: " "})
    return re.sub(r"\s+", " ", s).strip()


def _page_text(url: str) -> str | None:
    """The article's paragraphs as plain text, or None (not allowed or not reachable). Read once,
    for the end of one sentence; never stored."""
    if not _allowed(url):
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception:  # noqa: BLE001
        return None
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</>", " ", r.text)
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", body)
    return _norm(" ".join(re.sub(r"<[^>]+>", "", p) for p in paras))


SENTENCE_END = re.compile(r"[.!?][\"')\]]?(?=\s|$)")


def complete_summary(teaser: str, page: str | None) -> str:
    """A teaser ("... cut mid-sentence …") with its last sentence finished from the article page,
    else cut back to its last full sentence, else left as it is."""
    base = _norm(re.sub(r"\s*(?:\.\.\.|…)$", "", teaser))
    if page:
        tail = base[-60:].lower()
        at = page.lower().find(tail)
        if at >= 0:
            after = page[at + len(tail):]
            m = SENTENCE_END.search(after[:config.NEWS_COMPLETE_MAX_CHARS])
            if m:
                return base + after[:m.end()]
    ends = list(SENTENCE_END.finditer(base))
    if ends and ends[-1].end() >= 80:
        return base[:ends[-1].end()]
    return teaser


def complete_teasers(items: list[dict]) -> None:
    """Finish the sentence each teaser was cut in (complete_summary), at most NEWS_COMPLETE_MAX
    articles a run, each once: the feed's text is kept in "teaser", the result in "summary"."""
    todo = [i for i in items if "teaser" not in i and i["summary"].endswith("…")]
    for i in sorted(todo, key=lambda i: i["published"], reverse=True)[:config.NEWS_COMPLETE_MAX]:
        page = _page_text(i["link"])
        i["teaser"] = i["summary"]
        i["summary"] = complete_summary(i["summary"], page)


ARCHIVE = config.PROJECT_ROOT / "archive" / "news"


def archive_days(items: list[dict], now: dt.datetime | None = None) -> list[str]:
    """Each UTC day's items (source, title, link, published, summary) to
    archive/news/<year>/<date>.json.gz once the day is NEWS_ARCHIVE_AFTER_DAYS old (feeds can show an
    item a day or two late), and never again: the kept list only holds NEWS_KEEP_DAYS. Days at the
    kept list's edge (possibly cut) aren't written. Returns the days written."""
    now = now or dt.datetime.now(dt.timezone.utc)
    last = (now - dt.timedelta(days=config.NEWS_ARCHIVE_AFTER_DAYS)).date()
    first = (now - dt.timedelta(days=config.NEWS_KEEP_DAYS - 1)).date()
    by_day: dict[dt.date, list[dict]] = {}
    for i in items:
        day = dt.datetime.fromisoformat(i["published"]).astimezone(dt.timezone.utc).date()
        if first <= day <= last:
            by_day.setdefault(day, []).append({k: i.get(k) for k in ("source", "title", "link", "published", "summary")})
    written = []
    for day, rows in sorted(by_day.items()):
        path = ARCHIVE / str(day.year) / f"{day.isoformat()}.json.gz"
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(rows, key=lambda r: (r["published"], r["link"]))
        path.write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False, indent=0).encode("utf-8"),
                                       compresslevel=9, mtime=0))
        written.append(day.isoformat())
    return written


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

    def quoted(sents: list[str], d: str, r: int, named: set[str]) -> bool:
        """A sentence of the item about a power-unit penalty in the team's or the driver's own words,
        naming round `r` or no round (one about another race doesn't back this one)."""
        who = [surname[d]] + [k for k, key in TEAM_KEYS if key in named]
        for s in sents:
            if not (TOPICS["pu"].search(s) or PU_PENALTY.search(s)) or NEGATION.search(s):
                continue
            if rounds_in(s) and r not in rounds_in(s):
                continue
            low = config._normalise(s)
            speaker = any(re.search(rf"\b{re.escape(config._normalise(w))}\b", low) for w in who if w) or ROLE.search(s)
            if (QUOTE.search(s) and speaker) or (speaker and ATTRIBUTION.search(s) and not HEDGE.search(s)):
                return True
        return False

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
                    "pu_claims": [{"driver": d, "round": r, "quoted": quoted(_sentences(i), d, r, named)}
                                  for s in _sentences(i) if TOPICS["pu"].search(s) and PU_PENALTY.search(s)
                                  and not NEGATION.search(s)
                                  for d in drivers_in(s) for r in rounds_in(s)]})
    return out


def pu_penalty_ids(items: list[dict], now: dt.datetime | None = None) -> list[str]:
    """Ids of the items from the last NEWS_PLAN_DAYS with a sentence about a power-unit penalty
    (whoever and wherever it names): meta.json keeps them, and scripts/needs_update.py (its own copy
    of this test) rebuilds as soon as a feed has one that isn't in the list."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=config.NEWS_PLAN_DAYS)
    return sorted(hashlib.sha1(i["link"].encode()).hexdigest()[:12] for i in items
                  if dt.datetime.fromisoformat(i["published"]) >= cutoff
                  and any(TOPICS["pu"].search(s) and PU_PENALTY.search(s) and not NEGATION.search(s)
                          for s in _sentences(i)))


def news_plans(tagged: list[dict], next_round: int | None, now: dt.datetime | None = None) -> dict[str, list[dict]]:
    """
    Reported power-unit penalties: {driver: [{"rounds": [r], "sources": [...], "links": [...]}]} for
    each (driver, round still to run) at least NEWS_PLAN_MIN_SOURCES sites put in the same sentence as
    a power-unit penalty within NEWS_PLAN_DAYS, or one site quoting the team or the driver (then
    "quoted": True and "hazard": PU_PLAN_HAZARD_QUOTED).
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
            s = seen.setdefault((c["driver"], c["round"]), {"sources": set(), "links": [], "quoted": set()})
            if c.get("quoted"):
                s["quoted"].add(i["source"])
            if i["source"] not in s["sources"]:
                s["sources"].add(i["source"])
                s["links"].append({"source": i["source"], "title": i["title"], "link": i["link"], "published": i["published"]})
    out: dict[str, list[dict]] = {}
    for (d, r), s in sorted(seen.items()):
        if s["quoted"]:
            out.setdefault(d, []).append({"rounds": [r], "sources": sorted(s["sources"]), "links": s["links"],
                                          "quoted": True, "hazard": config.PU_PLAN_HAZARD_QUOTED,
                                          "note": f"Team or driver quoted by {', '.join(sorted(s['quoted']))}"})
        elif len(s["sources"]) >= config.NEWS_PLAN_MIN_SOURCES:
            out.setdefault(d, []).append({"rounds": [r], "sources": sorted(s["sources"]), "links": s["links"],
                                          "note": f"Reported by {', '.join(sorted(s['sources']))}"})
    return out


def table(tagged: list[dict]) -> pd.DataFrame:
    """The tagged items that matter here (any topic), for the site."""
    rows = [i for i in tagged if i["topics"]]
    return pd.DataFrame(rows, columns=["id", "source", "title", "link", "published", "summary", "topics", "drivers",
                                       "teams", "rounds", "pu_claims"])
