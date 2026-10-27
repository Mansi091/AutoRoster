"""STEP 7 - Build the final Excel workbook for supervisor review.

Input : outputs of steps 1-6
Output: output/07_final_bus_routing_workbook.xlsx

Sheets
  Summary              headline numbers
  Bus Groups           one row per proposed bus, with status and warnings
  Employee Assignments employee, shift, pickup point, bus, group size, distance, warnings
  Pickup Stops         every stop with coordinates, status and bus
  Stop Distances       nearest 3 stops for each approved stop (straight-line km)
  Review Queue         employees blocked from routing - supervisor decision needed
  Validation Warnings  every warning in one list
  Cleaning Log         every value changed in step 1

The supervisor reviews this file before it is used operationally.
"""
import sys
from pathlib import Path

import pandas as pd

import config
from utils import read_sheet, write_workbook


def warning_rows(source, df, ref_col, warn_col, level_col=None):
    rows = []
    if df is None or df.empty or warn_col not in df.columns:
        return rows
    for _, r in df.iterrows():
        text = str(r.get(warn_col, "") or "").strip()
        if not text or text == "nan":
            continue
        for part in text.split(" | "):
            level = "Info"
            msg = part
            if part.startswith("[") and "]" in part:
                level, msg = part[1:].split("] ", 1)
            rows.append({"source": source, "reference": r.get(ref_col, ""),
                         "employee_name": r.get("employee_name", ""), "level": level, "message": msg})
    return rows


def main():
    cleaned_emp = read_sheet(config.OUT_CLEANED, "Employees")
    review = read_sheet(config.OUT_CLEANED, "Review Queue")
    log = read_sheet(config.OUT_CLEANED, "Cleaning Log")
    master = read_sheet(config.OUT_MASTER, "Pickup Point Master")
    nearest = read_sheet(config.OUT_DISTANCES, "Nearest Stops")
    buses = read_sheet(config.OUT_GROUPING, "Bus Groups")
    assignments = read_sheet(config.OUT_GROUPING, "Employee Assignments")
    stop_to_bus = read_sheet(config.OUT_GROUPING, "Stop to Bus")
    unassigned = read_sheet(config.OUT_GROUPING, "Unassigned")

    # ---- Pickup stops with bus assignment ------------------------------------
    bus_for_stop = {}
    for _, r in stop_to_bus.iterrows():
        bus_for_stop.setdefault(r["pickup_point"], []).append(f"{r['shift']}: {r['bus_id']}")
    stops = master.copy()
    stops["buses"] = stops["pickup_point"].map(lambda p: ", ".join(bus_for_stop.get(p, [])) or "Not assigned")
    stops = stops[["pickup_point", "status", "latitude", "longitude", "validation",
                   "coordinate_source", "routable_employees", "ready_for_routing", "buses"]]

    # ---- Validation warnings -----------------------------------------------------
    warnings = []
    warnings += warning_rows("Step 1 - employee", cleaned_emp, "employee_id", "warnings")
    warnings += warning_rows("Step 1 - review queue", review, "review_no", "problems")
    for _, r in master.iterrows():
        if r["ready_for_routing"] != "Yes":
            warnings.append({"source": "Step 4 - pickup point", "reference": r["pickup_point"],
                             "employee_name": "", "level": "High" if r["status"] != "Needs review" else "Medium",
                             "message": r["validation"] or "Not approved"})
    for _, r in buses.iterrows():
        if str(r.get("warnings", "")).strip() and str(r.get("warnings")) != "nan":
            warnings.append({"source": "Step 6 - bus", "reference": r["bus_id"], "employee_name": "",
                             "level": "High", "message": r["warnings"]})
    warnings_df = pd.DataFrame(warnings, columns=["source", "reference", "employee_name", "level", "message"])
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Info": 4}
    if len(warnings_df):
        warnings_df["_o"] = warnings_df["level"].map(lambda x: order.get(x, 5))
        warnings_df = warnings_df.sort_values(["_o", "source", "reference"]).drop(columns="_o")

    # ---- Summary -------------------------------------------------------------------
    assigned = assignments[assignments["bus_id"] != "UNASSIGNED"]
    summary = pd.DataFrame([
        ("Employees in raw sheet", len(cleaned_emp) + len(review)),
        ("Employees ready for routing (step 1)", len(cleaned_emp)),
        ("Employees in review queue (step 1)", len(review)),
        ("Distinct pickup points", len(master)),
        ("Pickup points approved with coordinates", int((master["ready_for_routing"] == "Yes").sum())),
        ("Pickup points still pending", int((master["ready_for_routing"] != "Yes").sum())),
        ("Employees assigned to a bus", len(assigned)),
        ("Employees unassigned (no approved location / review)", len(unassigned)),
        ("Buses proposed", len(buses)),
        ("Buses with status OK", int((buses["status"].astype(str).str.startswith("OK")).sum())),
        ("Buses needing supervisor review", int((~buses["status"].astype(str).str.startswith("OK")).sum())),
        ("Bus seats per bus", config.BUS_CAPACITY),
        ("Minimum employees per bus (target)", config.MIN_EMPLOYEES_PER_BUS),
        ("Max stop radius per bus (km, straight line)", config.MAX_CLUSTER_RADIUS_KM),
    ], columns=["metric", "value"])

    write_workbook(config.OUT_FINAL, {
        "Summary": summary,
        "Bus Groups": buses,
        "Employee Assignments": assignments,
        "Pickup Stops": stops,
        "Stop Distances": nearest,
        "Review Queue": review,
        "Validation Warnings": warnings_df,
        "Cleaning Log": log,
    }, notes={
        "Pickup Stops": "Coordinates are only filled for stops approved in the Pickup Point Master.",
        "Stop Distances": "Straight-line (Haversine) km - not road distance.",
    })

    print(f"Final workbook written : {config.OUT_FINAL}")
    print(f"Warnings listed        : {len(warnings_df)}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
