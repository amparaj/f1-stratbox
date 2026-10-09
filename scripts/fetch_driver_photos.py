"""
Download a photo of each driver from Wikimedia Commons into web/public/drivers/<ref>.jpg, listed in
web/src/driverPhotos.json (the site's driver pop-up shows it, About credits it).

    .venv\\Scripts\\python scripts\\fetch_driver_photos.py                 # this season's drivers, missing only
    .venv\\Scripts\\python scripts\\fetch_driver_photos.py --refresh       # every photo again
    .venv\\Scripts\\python scripts\\fetch_driver_photos.py hamilton senna  # these History refs too

The photo is the lead image of the driver's English Wikipedia article (the article from Jolpica's driver
list, or web/public/data/profiles.json for this season's line-up), downloaded as a 400 px wide thumbnail.
Only files on Commons under a free licence (public domain, CC0, CC BY/BY-SA, OGL) are kept; an article whose
image is missing, local or not free gets no photo (the pop-up shows the car number instead). F1's own
headshots (OpenF1's headshot_url) are copyrighted, so they're not used. Set 'source' in the manifest by
hand to pick a different Commons file; `--refresh` keeps it. Run locally and commit the files.
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "web" / "src" / "driverPhotos.json"
PROFILES = ROOT / "web" / "public" / "data" / "profiles.json"
HISTORY = ROOT / "web" / "public" / "data" / "history" / "drivers.json"
OUT = ROOT / "web" / "public" / "drivers"
WIKI = "https://en.wikipedia.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
UA = "f1-stratbox driver photos (https://github.com/amparaj/f1-stratbox)"
FREE = re.compile(r"^(public domain|pd\b|cc0|cc by(-sa)? \d|ogl \d)", re.I)    # OGL: UK Open Government Licence
WIDTH = 400


def _get(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode({**params, "format": "json"})
    for attempt in range(5):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception:  # noqa: BLE001 — Wikimedia rate-limits bursts: back off and retry
            if attempt == 4:
                raise
            time.sleep(8 * (attempt + 1))
    raise AssertionError


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=60).read()


def lead_image(wikipedia: str) -> str | None:
    """The Commons file title of a Wikipedia article's lead image."""
    title = urllib.parse.unquote(wikipedia.rstrip("/").rsplit("/", 1)[-1]).replace("_", " ")
    pages = _get(WIKI, {"action": "query", "titles": title, "prop": "pageimages", "piprop": "name",
                        "redirects": 1})["query"]["pages"]
    page = next(iter(pages.values()))
    return page.get("pageimage")


def file_info(name: str) -> dict | None:
    pages = _get(COMMONS, {"action": "query", "titles": f"File:{name}", "prop": "imageinfo",
                           "iiprop": "url|extmetadata", "iiurlwidth": WIDTH,
                           "iiextmetadatafilter": "LicenseShortName|Artist"})["query"]["pages"]
    page = next(iter(pages.values()))
    if "imageinfo" not in page:
        return None         # not on Commons (a local, usually non-free, file)
    ii = page["imageinfo"][0]
    meta = {k: v["value"] for k, v in ii.get("extmetadata", {}).items()}
    author = unescape(re.sub(r"<[^>]+>", "", meta.get("Artist", ""))).strip() or "Unknown author"
    return {"thumb": ii.get("thumburl") or ii["url"], "page": ii["descriptionurl"],
            "licence": meta.get("LicenseShortName", "?"), "author": re.sub(r"\s+", " ", author)[:80]}


def targets(extra: list[str]) -> dict[str, str]:
    """ref -> Wikipedia article URL: this season's drivers, plus any History refs asked for."""
    out = {}
    if PROFILES.exists():
        for d in json.loads(PROFILES.read_text(encoding="utf-8"))["drivers"]:
            if d.get("ref") and d.get("wikipedia"):
                out[d["ref"]] = d["wikipedia"]
    if extra and HISTORY.exists():
        cols = json.loads(HISTORY.read_text(encoding="utf-8"))["drivers"]
        wiki = dict(zip(cols["ref"], cols["wikipedia"]))
        for ref in extra:
            if wiki.get(ref):
                out[ref] = wiki[ref]
            else:
                print(f"{ref}: not in the History files", file=sys.stderr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("refs", nargs="*", help="History driver refs to add (e.g. senna)")
    ap.add_argument("--refresh", action="store_true", help="download every photo again")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"_doc": [], "drivers": {}}
    photos = manifest["drivers"]
    OUT.mkdir(parents=True, exist_ok=True)
    for ref, wikipedia in sorted({**{r: None for r in photos}, **targets(args.refs)}.items()):
        entry = photos.get(ref, {})
        path = OUT / f"{ref}.jpg"
        if path.exists() and entry and not args.refresh:
            continue
        source = entry.get("source") or (lead_image(wikipedia) if wikipedia else None)
        if not source:
            print(f"{ref}: no lead image", file=sys.stderr)
            continue
        info = file_info(source)
        if info is None:
            print(f"{ref}: {source} isn't on Commons, skipped", file=sys.stderr)
            continue
        if not FREE.match(info["licence"]):
            print(f"{ref}: licence {info['licence']!r} is not free, skipped", file=sys.stderr)
            continue
        path.write_bytes(_download(info["thumb"]))
        photos[ref] = {"file": path.name, "source": source, "licence": info["licence"],
                       "author": info["author"], "page": info["page"]}
        print(f"{path.name} <- {source} ({info['licence']})")
        time.sleep(1)
    manifest["_doc"] = [
        "Driver photos (scripts/fetch_driver_photos.py): History driver ref -> the file in web/public/drivers/,",
        "its Commons title, licence, author and page. Free licences only.",
    ]
    rows = ",\n".join(f"    {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in sorted(photos.items()))
    MANIFEST.write_text("{\n  \"_doc\": " + json.dumps(manifest["_doc"], indent=4).replace("\n]", "\n  ]")
                        + ",\n  \"drivers\": {\n" + rows + "\n  }\n}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
