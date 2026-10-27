"""Approve only coordinate-master or strictly verified Geoapify locations."""
import pandas as pd
import config
from geocoding import resolve_pickup_points
from master import load_master, save_master
from utils import read_sheet

def resolve_all():
    pickups = read_sheet(config.OUT_PICKUPS, "Unique Pickup Points")
    master = load_master(pickups)
    names = master["pickup_point"].fillna("").astype(str).str.strip().tolist()
    resolutions = resolve_pickup_points(names)
    verified_count = 0
    unverified_count = 0

    for idx, row in master.iterrows():
        pickup_point = str(row["pickup_point"]).strip()
        result = resolutions[pickup_point]
        reviewed_by = str(row.get("reviewed_by", "")).strip().lower()
        has_manual_approval = (
            str(row.get("status", "")).strip() == "Approved"
            and pd.notna(row.get("latitude"))
            and pd.notna(row.get("longitude"))
            and reviewed_by not in {"auto_geoapify", "auto_local_fallback"}
        )
        if has_manual_approval:
            verified_count += 1
            continue

        if result["location_status"] == "VERIFIED":
            master.at[idx, "latitude"] = result["latitude"]
            master.at[idx, "longitude"] = result["longitude"]
            master.at[idx, "selected_candidate_no"] = None
            master.at[idx, "status"] = "Approved"
            master.at[idx, "reviewed_by"] = "auto_verified_geocoding"
            master.at[idx, "review_notes"] = (
                f"{result['coordinate_source']}: "
                f"{result.get('formatted_address', result['canonical_name'])}"
            )
            verified_count += 1
        else:
            master.at[idx, "latitude"] = None
            master.at[idx, "longitude"] = None
            master.at[idx, "selected_candidate_no"] = None
            if str(row.get("status", "")).strip() != "Rejected":
                master.at[idx, "status"] = "Needs review"
            master.at[idx, "reviewed_by"] = "auto_verified_geocoding"
            master.at[idx, "review_notes"] = result["resolution_reason"]
            unverified_count += 1

    save_master(master)
    print(f"Total stops in master       : {len(master)}")
    print(f"Verified coordinate matches : {verified_count}")
    print(f"Left for review             : {unverified_count}")

if __name__ == "__main__":
    resolve_all()
