"""Nagebootste icloudpd voor tests. Gedrag staat in het JSON-bestand uit FAKE_ICLOUD_SPEC.

{
  "auth": "ok" | "fail" | "hang",
  "downloads": {"Favorites": [{"name": "IMG_1.jpg", "src": "/pad/bron.jpg"}], "ALL": [...]},
  "fail_album": "Favorites",         # optioneel: deze download eindigt met een fout
  "libraries": ["PrimarySync", "SharedSync-ABC"],   # uitvoer van --list-libraries
  "libraries_fail": "has not finished indexing yet"  # optioneel: --list-libraries eindigt met deze fout
}
Met --library X wordt in "downloads" eerst op "X|album" gezocht (bv. "SharedSync-ABC|ALL").
Elke aanroep wordt (als JSON-regel) toegevoegd aan het bestand uit FAKE_ICLOUD_LOG.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

args = sys.argv[1:]


def opt(name):
    return args[args.index(name) + 1] if name in args else None


spec = json.loads(Path(os.environ["FAKE_ICLOUD_SPEC"]).read_text())
with open(os.environ["FAKE_ICLOUD_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")

if "--auth-only" in args:
    mode = spec.get("auth", "ok")
    if mode == "hang":
        print("Two-step authentication required, waiting for code...", flush=True)
        time.sleep(120)
    if mode == "fail":
        print("Invalid email/password combination", flush=True)
        sys.exit(1)
    print("Authentication OK", flush=True)
    sys.exit(0)

if "--list-libraries" in args:
    if spec.get("libraries_fail"):
        print(f"ERROR    library exception: Apple iCloud Photo Library {spec['libraries_fail']}", flush=True)
        sys.exit(1)
    print("INFO     Processing user: user@example.com", flush=True)
    for name in spec.get("libraries", ["PrimarySync"]):
        print(name, flush=True)
    sys.exit(0)

if "--list-albums" in args:
    for name in spec.get("albums", ["All Photos", "Favorites"]):
        print(name, flush=True)
    sys.exit(0)

album = opt("--album") or "ALL"
if spec.get("fail_album") == album:
    print("Could not download: connection reset", flush=True)
    sys.exit(2)

directory = Path(opt("--directory"))
downloads = spec.get("downloads", {})
library = opt("--library")
entries = downloads.get(f"{library}|{album}") if library else None
if entries is None:
    entries = downloads.get(album, [])
for entry in entries:
    target = directory / entry["name"]
    if target.exists():
        print(f"{target} already exists", flush=True)
        continue
    shutil.copyfile(entry["src"], target)
    print(f"Downloading {target}", flush=True)
sys.exit(0)
