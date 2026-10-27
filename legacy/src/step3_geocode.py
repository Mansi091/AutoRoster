"""STEP 3 - Send each unique pickup point to Geoapify and collect candidate locations.

Nothing is accepted automatically. Every stop gets up to 5 candidates in
output/03_geocode_candidates.xlsx. A supervisor picks the right one in
data/manual/pickup_point_master.xlsx (column "selected_candidate_no") and sets
status = Approved (or types exact coordinates if none of the candidates is right).

Requires the environment variable GEOAPIFY_API_KEY. Without it the step only reads the
cache and reports which stops are still waiting for a lookup.
Responses are cached in data/cache/geoapify_cache.json, so each stop is looked up once.
"""
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

import config
from master import load_master
from utils import in_pune_bounds, read_sheet, to_float, write_workbook


def load_cache() -> dict:
    if config.GEOCODE_CACHE_FILE.exists():
        return json.loads(config.GEOCODE_CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    config.GEOCODE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.GEOCODE_CACHE_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")


def parse_results(payload: dict) -> list:
    candidates = []
    for r in payload.get("results", [])[: config.GEOAPIFY_RESULT_LIMIT]:
        lat, lon = to_float(r.get("lat")), to_float(r.get("lon"))
        candidates.append({
            "formatted": r.get("formatted", ""),
            "lat": lat,
            "lon": lon,
            "result_type": r.get("result_type", ""),
            "confidence": (r.get("rank") or {}).get("confidence"),
            "in_pune_region": bool(lat is not None and lon is not None and in_pune_bounds(lat, lon)),
        })
    return candidates


def geoapify_lookup(query: str) -> list:
    """One HTTP call, with simple retry on rate-limit / server errors."""
    params = {
        "text": query,
        "filter": f"countrycode:{config.GEOAPIFY_COUNTRY_CODE}",
        "bias": f"proximity:{config.PUNE_CENTRE[1]},{config.PUNE_CENTRE[0]}",
        "limit": config.GEOAPIFY_RESULT_LIMIT,
        "format": "json",
        "apiKey": config.GEOAPIFY_API_KEY,
    }
    last_error = None
    for attempt in range(3):
        resp = requests.get(config.GEOAPIFY_URL, params=params, timeout=config.GEOAPIFY_TIMEOUT_SECONDS)
        if resp.status_code in (401, 403):
            raise RuntimeError(
                "Geoapify rejected the API key (HTTP %d). Check GEOAPIFY_API_KEY."
                % resp.status_code
            )
        if resp.status_code in (429, 500, 502, 503, 504):
            last_error = resp.status_code
            time.sleep(2 ** (attempt + 1))
            continue
        resp.raise_for_status()
        return parse_results(resp.json())
    raise RuntimeError(f"Geoapify kept failing for '{query}' (HTTP {last_error})")


def selected_no(value):
    """Candidate number typed by the supervisor, or None if blank / not a number."""
    number = to_float(value)
    return int(number) if number is not None else None


def main(skip_lookup: bool = False):
    pickups = read_sheet(config.OUT_PICKUPS, "Unique Pickup Points")
    master = load_master(pickups)
    cache = load_cache()

    have_key = bool(config.GEOAPIFY_API_KEY) and not skip_lookup
    looked_up, waiting = 0, []

    for _, row in master.iterrows():
        if str(row["status"]) == "Approved":
            continue
        query = str(row["search_query"])
        if query in cache:
            continue
        if not have_key:
            waiting.append(row["pickup_point"])
            continue
        cache[query] = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "candidates": geoapify_lookup(query)}
        looked_up += 1
        save_cache(cache)
        time.sleep(0.2)  # be gentle with the free-tier rate limit

    # ---- Build the candidate review sheet -----------------------------------
    rows = []
    for _, row in master.iterrows():
        query = str(row["search_query"])
        selected = row["selected_candidate_no"]
        candidates = cache.get(query, {}).get("candidates", [])
        base = {
            "pickup_key": row["pickup_key"],
            "pickup_point": row["pickup_point"],
            "routable_employees": row["routable_employees"],
            "master_status": row["status"],
        }
        if not candidates:
            rows.append({**base, "candidate_no": None, "formatted_address": "(no lookup yet)" if query not in cache
                         else "(Geoapify returned no results - enter coordinates manually)",
                         "latitude": None, "longitude": None, "result_type": None,
                         "confidence": None, "inside_pune_district": None,
                         "selected_in_master": None})
            continue
        for i, c in enumerate(candidates, start=1):
            sel = str(selected_no(selected)) == str(i)
            rows.append({
                **base,
                "candidate_no": i,
                "formatted_address": c["formatted"],
                "latitude": c["lat"],
                "longitude": c["lon"],
                "result_type": c["result_type"],
                "confidence": c["confidence"],
                "inside_pune_district": "Yes" if c["in_pune_region"] else "NO - check",
                "selected_in_master": "Yes" if sel else "",
            })

    candidates_df = pd.DataFrame(rows)
    write_workbook(config.OUT_CANDIDATES, {"Geocode Candidates": candidates_df})

    print(f"Pickup points in master : {len(master)}")
    print(f"Approved already        : {(master['status'] == 'Approved').sum()}")
    print(f"Looked up this run      : {looked_up}")
    if waiting:
        print(f"Waiting for a lookup    : {len(waiting)} - set GEOAPIFY_API_KEY and run again")
    print(f"Candidate sheet written : {config.OUT_CANDIDATES}")
    print("NOTE: nothing is auto-approved. Review candidates and set status in the master file.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main(skip_lookup="--skip-lookup" in sys.argv)
