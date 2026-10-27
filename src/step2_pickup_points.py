"""STEP 2 - Extract the unique pickup points and keep a link back to employees.

Input : output/01_cleaned_employees.xlsx
Output: output/02_unique_pickup_points.xlsx
          - Unique Pickup Points : one row per distinct stop (what must be verified)
          - Employee Mapping     : which employees use which stop
          - Possible Duplicates  : stop names that look like the same place (human decides)
"""
import sys
from pathlib import Path

import pandas as pd

import config
from utils import find_similar_stops, read_sheet, write_workbook


def join_unique(values):
    items = sorted({str(v) for v in values if str(v).strip() and str(v) != "nan"})
    return ", ".join(items)


def main():
    emp = read_sheet(config.OUT_CLEANED, "Employees")
    rev = read_sheet(config.OUT_CLEANED, "Review Queue")

    emp["in_routing"] = "Yes"
    rev["in_routing"] = "No (in review queue)"
    rev = rev.rename(columns={"problem_level": "warning_level", "problems": "warnings"})
    rev["review_no"] = rev.get("review_no")

    combined = pd.concat([emp, rev], ignore_index=True, sort=False)
    combined = combined[combined["pickup_point"].notna() & (combined["pickup_point"].astype(str).str.strip() != "")]

    # One row per stop key; display name = most common spelling
    combined["pickup_key"] = combined["pickup_key"].astype(str)
    keys = sorted(combined["pickup_key"].unique())
    pickup_id = {k: f"PP-{i:03d}" for i, k in enumerate(keys, start=1)}
    combined["pickup_id"] = combined["pickup_key"].map(pickup_id)

    display = (combined.groupby("pickup_key")["pickup_point"]
               .agg(lambda s: s.value_counts().index[0]))

    rows = []
    for key in keys:
        sub = combined[combined["pickup_key"] == key]
        routed = sub[sub["in_routing"] == "Yes"]
        rows.append({
            "pickup_id": pickup_id[key],
            "pickup_point": display[key],
            "employees_total": len(sub),
            "employees_routable": len(routed),
            "employees_in_review": len(sub) - len(routed),
            "shifts": join_unique(sub["shift"]),
            "areas_served": join_unique(sub["area"]),
            "verification_status": "Needs location check",
        })
    pickups = pd.DataFrame(rows)

    mapping = combined[["pickup_id", "pickup_point", "source_row", "employee_id",
                        "employee_name", "shift", "in_routing"]].copy()
    mapping = mapping.sort_values(["pickup_id", "shift", "employee_name"]).reset_index(drop=True)
    mapping["pickup_point"] = mapping["pickup_id"].map(dict(zip(pickups["pickup_id"], pickups["pickup_point"])))
    mapping["employee_id"] = mapping["employee_id"].apply(lambda v: "" if pd.isna(v) else int(float(v)))

    similar = find_similar_stops(dict(zip(pickups["pickup_point"].str.lower().str.replace(r"[^a-z0-9]+", " ", regex=True).str.strip(),
                                          pickups["pickup_point"])),
                                 config.DUPLICATE_NAME_SIMILARITY)
    dup_rows = []
    id_of = dict(zip(pickups["pickup_point"], pickups["pickup_id"]))
    for a, b, score in similar:
        dup_rows.append({"pickup_a": a, "pickup_a_id": id_of.get(a, ""),
                         "pickup_b": b, "pickup_b_id": id_of.get(b, ""),
                         "similarity": score,
                         "action": "Check on map - merge in pickup_aliases.csv if same bus stop"})
    duplicates = pd.DataFrame(dup_rows, columns=["pickup_a", "pickup_a_id", "pickup_b", "pickup_b_id",
                                                 "similarity", "action"])

    write_workbook(config.OUT_PICKUPS, {
        "Unique Pickup Points": pickups,
        "Employee Mapping": mapping,
        "Possible Duplicates": duplicates,
    })

    print(f"Distinct pickup points : {len(pickups)}")
    print(f"Employee-stop links    : {len(mapping)}")
    print(f"Possible duplicates    : {len(duplicates)}")
    print(f"Written                : {config.OUT_PICKUPS}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
