"""
Download a flag for every country in web/src/countries.json into web/public/flags/<a2>.svg.

    .venv\\Scripts\\python scripts\\fetch_flags.py            # missing files only
    .venv\\Scripts\\python scripts\\fetch_flags.py --refresh  # every file again

The flags are flag-icons' 4x3 SVGs (https://github.com/lipis/flag-icons, MIT licence; credited on About).
Images, not emoji: Windows has no flag emoji and shows two letters instead. Run locally and commit the files.
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COUNTRIES = ROOT / "web" / "src" / "countries.json"
OUT = ROOT / "web" / "public" / "flags"
VERSION = "7.2.3"
URL = "https://cdn.jsdelivr.net/npm/flag-icons@{version}/flags/4x3/{a2}.svg"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true", help="download every file again")
    args = ap.parse_args()
    countries = json.loads(COUNTRIES.read_text(encoding="utf-8"))["countries"]
    OUT.mkdir(parents=True, exist_ok=True)
    for a3, c in sorted(countries.items()):
        if not c.get("a2"):
            continue
        path = OUT / f"{c['a2']}.svg"
        if path.exists() and not args.refresh:
            continue
        req = urllib.request.Request(URL.format(version=VERSION, a2=c["a2"]), headers={"User-Agent": "f1-stratbox flags"})
        path.write_bytes(urllib.request.urlopen(req, timeout=30).read())
        print(f"{path.name} <- {a3} {c['name']}")
        time.sleep(0.2)


if __name__ == "__main__":
    main()
