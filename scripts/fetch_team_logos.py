"""
Download the team logos listed in web/src/teamLogos.json from Wikimedia Commons.

    .venv\\Scripts\\python scripts\\fetch_team_logos.py            # missing files only
    .venv\\Scripts\\python scripts\\fetch_team_logos.py --refresh  # every file again

Each logo's 'source' is a Commons file title. The file goes to web/public/logos/<file>, and its licence,
author and page url are written back into the manifest (the site's About page credits them from there).
A file whose licence isn't public domain, CC0 or CC BY(-SA) is refused: Commons also hosts some logos
under fair use only. Run locally and commit the files; the export doesn't fetch them.

A logo with no 'source' was supplied by hand: its original is kept at its 'archive' path (archive/logos/), and
is copied back to web/public/logos/ if missing (or with --refresh).
"""
import argparse
import json
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "web" / "src" / "teamLogos.json"
OUT = ROOT / "web" / "public" / "logos"
API = "https://commons.wikimedia.org/w/api.php"
UA = "f1-stratbox team logos (https://github.com/amparaj/f1-stratbox)"
FREE = re.compile(r"^(public domain|pd\b|cc0|cc by(-sa)? \d)", re.I)


def _get(url: str, data: bytes | None = None) -> bytes:
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=30).read()
        except Exception:  # noqa: BLE001 — Commons rate-limits bursts: back off and retry
            if attempt == 4:
                raise
            time.sleep(8 * (attempt + 1))
    raise AssertionError


def file_info(titles: list[str]) -> dict[str, dict]:
    """Commons title -> {url, page, licence, author}."""
    out = {}
    for i in range(0, len(titles), 40):
        data = urllib.parse.urlencode({
            "action": "query", "format": "json", "titles": "|".join(f"File:{t}" for t in titles[i:i + 40]),
            "prop": "imageinfo", "iiprop": "url|extmetadata",
            "iiextmetadatafilter": "LicenseShortName|Artist"}).encode()
        pages = json.loads(_get(API, data))["query"]["pages"].values()
        for p in pages:
            if "imageinfo" not in p:
                continue
            ii = p["imageinfo"][0]
            meta = {k: v["value"] for k, v in ii.get("extmetadata", {}).items()}
            author = unescape(re.sub(r"<[^>]+>", "", meta.get("Artist", ""))).strip()
            author = re.sub(r"(Unknown author)+", "Unknown author", author) or "Unknown author"
            out[p["title"][5:]] = {"url": ii["url"], "page": ii["descriptionurl"],
                                   "licence": meta.get("LicenseShortName", "?"), "author": author[:80]}
        time.sleep(2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true", help="download every file again")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    logos = [lg for team in manifest["teams"] for lg in team["logos"]]
    OUT.mkdir(parents=True, exist_ok=True)
    for lg in logos:
        if "source" not in lg:
            path, kept = OUT / lg["file"], ROOT / lg["archive"]
            if args.refresh or not path.exists():
                shutil.copyfile(kept, path)
                print(f"{lg['file']} <- {lg['archive']}")
    logos = [lg for lg in logos if "source" in lg]
    info = file_info(sorted({lg["source"] for lg in logos}))
    for lg in logos:
        src = info.get(lg["source"])
        if src is None:
            print(f"{lg['source']}: not on Commons", file=sys.stderr)
            continue
        if not FREE.match(src["licence"]):
            print(f"{lg['source']}: licence {src['licence']!r} is not free, skipped", file=sys.stderr)
            continue
        lg.update(licence=src["licence"], author=src["author"], page=src["page"])
        path = OUT / lg["file"]
        if args.refresh or not path.exists():
            path.write_bytes(_get(src["url"]))
            print(f"{lg['file']} <- {lg['source']} ({src['licence']})")
            time.sleep(1)
    MANIFEST.write_text(_dump(manifest), encoding="utf-8")


def _dump(manifest: dict) -> str:
    """One team a line (its logos indented under it), so a diff shows which team changed."""
    head = json.dumps(manifest["_doc"], ensure_ascii=False, indent=4).replace("\n]", "\n  ]")
    rows = []
    for team in manifest["teams"]:
        logos = team["logos"]
        rest = json.dumps({k: v for k, v in team.items() if k != "logos"}, ensure_ascii=False)[:-1]
        if not logos:
            rows.append(f"    {rest}, \"logos\": []}}")
            continue
        inner = ",\n".join(f"      {json.dumps(lg, ensure_ascii=False)}" for lg in logos)
        rows.append(f"    {rest}, \"logos\": [\n{inner}\n    ]}}")
    return "{\n  \"_doc\": " + head + ",\n  \"teams\": [\n" + ",\n".join(rows) + "\n  ]\n}\n"


if __name__ == "__main__":
    main()
