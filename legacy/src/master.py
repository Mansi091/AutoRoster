"""Pickup Point Master - the single file where approved stop locations live.

data/manual/pickup_point_master.xlsx is created automatically from the step-2 list.
New stops are appended; existing rows (and anything a supervisor typed) are never overwritten.
"""
import pandas as pd

import config
from utils import pickup_key, write_workbook, read_sheet

MASTER_COLUMNS = [
    "pickup_key", "pickup_point", "routable_employees", "search_query",
    "selected_candidate_no", "latitude", "longitude", "status",
    "reviewed_by", "review_notes",
]


def load_master(pickups: pd.DataFrame) -> pd.DataFrame:
    """Load the master, append any stops it does not have yet, and save it back."""
    if config.PICKUP_MASTER_FILE.exists():
        master = read_sheet(config.PICKUP_MASTER_FILE, "Pickup Point Master")
    else:
        master = pd.DataFrame(columns=MASTER_COLUMNS)

    for col in MASTER_COLUMNS:
        if col not in master.columns:
            master[col] = None
    master = master[MASTER_COLUMNS].copy()

    existing = set(master["pickup_key"].astype(str))
    new_rows = []
    for _, row in pickups.iterrows():
        name = str(row["pickup_point"])
        key = pickup_key(name)
        if key in existing:
            # keep the routable-employee count fresh, nothing else
            master.loc[master["pickup_key"].astype(str) == key, "routable_employees"] = row["employees_routable"]
            continue
        new_rows.append({
            "pickup_key": key,
            "pickup_point": name,
            "routable_employees": row["employees_routable"],
            "search_query": name + config.GEOAPIFY_QUERY_SUFFIX,
            "selected_candidate_no": None,
            "latitude": None,
            "longitude": None,
            "status": "Needs review",
            "reviewed_by": None,
            "review_notes": None,
        })
        existing.add(key)

    if new_rows:
        master = pd.concat([master, pd.DataFrame(new_rows)], ignore_index=True)
    save_master(master)
    return master


def save_master(master: pd.DataFrame) -> None:
    write_workbook(config.PICKUP_MASTER_FILE, {"Pickup Point Master": master[MASTER_COLUMNS]})
