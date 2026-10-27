import google.protobuf.runtime_version
google.protobuf.runtime_version.ValidateProtobufRuntimeVersion = lambda *args, **kwargs: None

from pathlib import Path
import pandas as pd
import run_route_planner

print("Testing shift route optimization with natural geographic corridor constraint...")
raw_path = Path("D:/bus_routing_project/data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx")
stop_lookup = run_route_planner.load_all_verified_stops()
facility = run_route_planner.DEFAULT_FACILITY

df = pd.read_excel(raw_path, header=1).dropna(how="all").reset_index(drop=True)
all_employees = []
for idx, r in df.iterrows():
    s_val = str(r.get("Shift", "A")).upper()
    shift = "A" if "A" in s_val else "B" if "B" in s_val else "C" if "C" in s_val else "A"
    all_employees.append({
        "sr_no": idx + 1,
        "emp_type": str(r.get("GRADE", "DET")).strip(),
        "emp_name": str(r.get("Name", f"Emp_{idx+1}")).strip(),
        "shift": shift,
        "raw_stop": str(r.get("Bus Stop", "")).strip(),
        "mobile": str(r.get("Mob. No.", "")).replace(".0", "").strip(),
    })

shift_a_emps = [e for e in all_employees if e["shift"] == "A"]
print(f"Shift A total employees: {len(shift_a_emps)}")

res = run_route_planner.solve_shift_routes("A", shift_a_emps, stop_lookup, facility)

print(f"Buses used: {res['buses_used']}")
print(f"Assigned passengers: {res['assigned_passengers']} / {len(shift_a_emps)}")
print(f"Outliers: {len(res['outliers'])}")
for r in res["routes"]:
    print(f"\nRoute {r['route_no']} | Corridor: {r['corridor_name']}")
    print(f"  Pax: {r['total_passengers']} (Capacity: 32) | Dist: {r['total_distance_km']} km | Duration: {r['route_duration_min']} min")
    stops_summary = [f"{s['stop_name']} ({s['demand']}p)" for s in r['stops']]
    print(f"  Stops ({len(r['stops'])}): {', '.join(stops_summary[:6])}{'...' if len(stops_summary) > 6 else ''}")
