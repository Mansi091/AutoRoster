"""Copy data/seed/stops_seed.json into the Supabase pickup stop memory (one time).

Each normalized stop name becomes one stop; every spelling becomes an alias.
Stops whose spellings were saved at different points are marked 'conflict' and keep
all points in `candidates`. Nothing is marked confirmed: a person confirms stops later.

Safe to re-run: groups whose spellings already exist in Supabase are skipped.

    python scripts/import_stops_to_supabase.py           # dry run: show what would happen
    python scripts/import_stops_to_supabase.py --apply   # write to Supabase
"""

import argparse
import json
import sys
import uuid
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import config  # noqa: E402
from backend.app.db import stop_store  # noqa: E402

CHUNK = 200


def existing_alias_keys() -> set:
    keys, start = set(), 0
    while True:
        rows = stop_store._rest("GET", stop_store.ALIASES_TABLE, params={
            "select": "alias_key", "offset": start, "limit": 1000,
        }) or []
        keys.update(r["alias_key"] for r in rows)
        if len(rows) < 1000:
            return keys
        start += 1000


def build_rows(groups, skip_keys):
    stops, aliases, changes, skipped = [], [], [], []
    for group in groups:
        keys = {stop_store.alias_key(a) for a in group["aliases"]}
        if keys & skip_keys:
            skipped.append(group["name"])
            continue
        stop_id = str(uuid.uuid4())
        stops.append({k: group[k] for k in ("name", "lat", "lon", "quality", "source", "candidates")} | {"id": stop_id})
        seen = set()
        for spelling in group["aliases"]:
            key = stop_store.alias_key(spelling)
            if key not in seen:
                seen.add(key)
                aliases.append({"alias_key": key, "alias_text": spelling, "stop_id": stop_id})
        changes.append({
            "stop_id": stop_id, "action": "imported", "old_value": None, "changed_by": "import script",
            "new_value": {"lat": group["lat"], "lon": group["lon"], "quality": group["quality"],
                          "source": group["source"], "candidates": len(group["candidates"])},
        })
    return stops, aliases, changes, skipped


def post_chunks(table, rows):
    for i in range(0, len(rows), CHUNK):
        stop_store._rest("POST", table, body=rows[i:i + CHUNK], prefer="return=minimal")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write to Supabase (default: dry run)")
    parser.add_argument("--seed", type=Path, default=config.MASTER_STOPS_FILE)
    args = parser.parse_args()

    seed = json.loads(args.seed.read_text(encoding="utf-8"))
    groups = stop_store.group_seed_for_import(seed)
    quality = Counter(g["quality"] for g in groups)
    print(f"Seed file: {args.seed} -> {len(seed)} spellings -> {len(groups)} stops "
          f"({quality['approximate']} approximate, {quality['conflict']} conflict)")

    if not stop_store.supabase_settings():
        raise SystemExit("Supabase is not configured: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")
    try:
        skip_keys = existing_alias_keys()
    except RuntimeError as exc:
        if "PGRST205" in str(exc):
            raise SystemExit(
                "The stop tables do not exist in Supabase yet. Open Supabase -> SQL Editor, run "
                "supabase/migrations/20261111_pickup_stops.sql, then run this script again."
            )
        raise
    stops, aliases, changes, skipped = build_rows(groups, skip_keys)
    print(f"Already in Supabase: {len(skip_keys)} spellings -> skipping {len(skipped)} stops")
    print(f"To add: {len(stops)} stops, {len(aliases)} spellings, {len(changes)} history rows")
    for stop in [s for s in stops if s["quality"] == "conflict"][:10]:
        points = "; ".join(f"{c['lat']:.5f},{c['lon']:.5f} ({len(c['spellings'])} spellings)" for c in stop["candidates"])
        print(f"  conflict: {stop['name']}: {points}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to write to Supabase.")
        return
    post_chunks(stop_store.STOPS_TABLE, stops)
    post_chunks(stop_store.ALIASES_TABLE, aliases)
    post_chunks(stop_store.CHANGES_TABLE, changes)
    records, source = stop_store.load_stop_records(force=True)
    print(f"\nImported. Stop memory now reads {len(records)} spellings from {source}.")


if __name__ == "__main__":
    main()
