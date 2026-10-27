"""STEP 4 - Build the approved Pickup Point Master with latitude / longitude.

Reads  : data/manual/pickup_point_master.xlsx   (supervisor decisions)
         data/cache/geoapify_cache.json          (candidates from step 3)
Writes : output/04_pickup_point_master.xlsx
           - Pickup Point Master : every stop with its coordinates and validation result
           - Ready for Routing   : only stops that are Approved AND have valid coordinates
           - Pending Review      : everything else, with the reason

A stop is used for routing only when the supervisor has set status = Approved and either
  (a) selected_candidate_no points to a candidate from step 3, or
  (b) latitude and longitude were typed manually.
"""
import json
import sys
from pathlib import Path

import pandas as pd

import config
from master import load_master
from step3_geocode import load_cache, selected_no
from utils import in_pune_bounds, read_sheet, to_float, write_workbook


def resolve_row(row, cache) -> dict:
    status = str(row["status"]).strip() if pd.notna(row["status"]) else "Needs review"
    out = {
        "pickup_key": row["pickup_key"],
        "pickup_point": row["pickup_point"],
        "routable_employees": row["routable_employees"],
        "status": status,
        "latitude": None,
        "longitude": None,
        "coordinate_source": "",
        "validation": "",
        "ready_for_routing": "No",
    }

    if status != "Approved":
        out["validation"] = f"Not approved (status: {status})"
        return out

    lat, lon = to_float(row["latitude"]), to_float(row["longitude"])
    if lat is not None and lon is not None:
        out["coordinate_source"] = "Manual entry"
    else:
        number = selected_no(row["selected_candidate_no"])
        if number is None:
            out["validation"] = "Approved but no candidate chosen and no coordinates typed"
            return out
        candidates = cache.get(str(row["search_query"]), {}).get("candidates", [])
        if number < 1 or number > len(candidates):
            out["validation"] = (f"Candidate #{number} does not exist "
                                 f"(step 3 returned {len(candidates)} candidates)")
            return out
        chosen = candidates[number - 1]
        lat, lon = chosen["lat"], chosen["lon"]
        out["coordinate_source"] = f"Geoapify candidate #{number}: {chosen['formatted']}"

    if lat is None or lon is None:
        out["validation"] = "Coordinates missing"
        return out
    if not in_pune_bounds(lat, lon):
        out["latitude"], out["longitude"] = round(lat, 6), round(lon, 6)
        out["validation"] = "Coordinates outside Pune district - re-check this stop"
        return out

    out["latitude"], out["longitude"] = round(lat, 6), round(lon, 6)
    out["validation"] = "OK"
    out["ready_for_routing"] = "Yes"
    return out


def main():
    pickups = read_sheet(config.OUT_PICKUPS, "Unique Pickup Points")
    master = load_master(pickups)
    cache = load_cache()

    resolved = pd.DataFrame([resolve_row(r, cache) for _, r in master.iterrows()])
    resolved = resolved.sort_values(["ready_for_routing", "pickup_point"], ascending=[False, True])
    ready = resolved[resolved["ready_for_routing"] == "Yes"].reset_index(drop=True)
    pending = resolved[resolved["ready_for_routing"] != "Yes"].reset_index(drop=True)

    write_workbook(config.OUT_MASTER, {
        "Pickup Point Master": resolved,
        "Ready for Routing": ready,
        "Pending Review": pending,
    })

    print(f"Stops in master      : {len(resolved)}")
    print(f"Ready for routing    : {len(ready)}")
    print(f"Pending review       : {len(pending)}")
    print(f"Written              : {config.OUT_MASTER}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
