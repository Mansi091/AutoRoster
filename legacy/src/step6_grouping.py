"""STEP 6 - Group employees into buses.

Rules applied (all configurable in src/config.py):
  * Shifts are separate pools: A, B, C. Multi-shift employees (e.g. A+B) form their own
    pool and are flagged for a supervisor to assign a shift.
  * A bus never exceeds BUS_CAPACITY seats.
  * Every stop on a bus must be within MAX_CLUSTER_RADIUS_KM of every other stop on that
    bus (straight-line). Distant stops are NOT forced together.
  * Buses below MIN_EMPLOYEES_PER_BUS are first merged with a nearby bus if that fits
    the seats; any still below the minimum are flagged for supervisor review.
  * Stops without an approved location are listed as unassigned, never guessed.

Method: greedy nearest-neighbour packing (largest stops seeded first), then a merge pass.
Density clustering (e.g. DBSCAN) was considered but does not enforce the seat limit, so it
is not used for the final grouping.

Input : output/01_cleaned_employees.xlsx, output/04_pickup_point_master.xlsx
Output: output/06_grouping_result.xlsx
"""
import sys
from pathlib import Path

import pandas as pd

import config
from utils import haversine_km, read_sheet, write_workbook


BUS_COLUMNS = ["bus_id", "shift", "seats_capacity", "employees", "seats_used_pct", "stops",
               "stop_list", "anchor_stop", "max_distance_between_stops_km", "status", "warnings"]
ASSIGN_COLUMNS = ["employee_id", "employee_name", "grade", "shift", "pickup_point", "mobile",
                  "bus_id", "bus_status", "bus_group_size", "distance_pickup_to_anchor_km",
                  "employee_warnings"]
STOP_COLUMNS = ["shift", "pickup_point", "employees", "latitude", "longitude", "bus_id"]


def stop_distance(a, b):
    return haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])


def load_total(bus, nodes):
    return sum(nodes[i]["count"] for i in bus)


def pack_pool(nodes):
    """Greedy packing of stop nodes into buses for one shift pool."""
    cap, radius = config.BUS_CAPACITY, config.MAX_CLUSTER_RADIUS_KM
    remaining = set(range(len(nodes)))
    buses = []
    while remaining:
        seed = min(remaining, key=lambda i: (-nodes[i]["count"], nodes[i]["name"]))
        remaining.remove(seed)
        bus = [seed]
        load = nodes[seed]["count"]
        while load < cap:
            best = None
            for c in remaining:
                if load + nodes[c]["count"] > cap:
                    continue
                # every stop must be within the radius of every other stop on the bus
                d_to_bus = max(stop_distance(nodes[m], nodes[c]) for m in bus)
                if d_to_bus > radius:
                    continue
                if best is None or d_to_bus < best[0]:
                    best = (d_to_bus, c)
            if best is None:
                break
            remaining.remove(best[1])
            bus.append(best[1])
            load += nodes[best[1]]["count"]
        buses.append(bus)

    # Merge pass: under-filled buses join a nearby bus when seats and distance allow
    merged = True
    while merged:
        merged = False
        for small in sorted(buses, key=lambda b: load_total(b, nodes)):
            if load_total(small, nodes) >= config.MIN_EMPLOYEES_PER_BUS:
                continue
            best = None
            for other in buses:
                if other is small:
                    continue
                if load_total(small, nodes) + load_total(other, nodes) > cap:
                    continue
                d = max(stop_distance(nodes[a], nodes[b]) for a in small for b in other)
                if d <= radius and (best is None or d < best[0]):
                    best = (d, other)
            if best:
                best[1].extend(small)
                buses.remove(small)
                merged = True
                break
    return buses


def max_internal_distance(bus, nodes):
    if len(bus) < 2:
        return 0.0
    return round(max(stop_distance(nodes[a], nodes[b]) for a in bus for b in bus if a < b), 2)


def main():
    emp = read_sheet(config.OUT_CLEANED, "Employees")
    ready = read_sheet(config.OUT_MASTER, "Ready for Routing")
    coords = {str(r["pickup_key"]): {"lat": float(r["latitude"]), "lon": float(r["longitude"]),
                                     "name": str(r["pickup_point"])}
              for _, r in ready.iterrows()}

    emp["shift"] = emp["shift"].astype(str)
    emp["pickup_key"] = emp["pickup_key"].astype(str)
    emp["located"] = emp["pickup_key"].isin(coords.keys())

    # Shift pool order: A, B, C, then multi-shift pools
    pools = sorted(emp["shift"].unique(), key=lambda s: (("+" in s), s))

    bus_rows, assignment_rows = [], []
    stop_rows = []

    for pool in pools:
        pool_emp = emp[emp["shift"] == pool]
        located = pool_emp[pool_emp["located"]]
        unlocated = pool_emp[~pool_emp["located"]]
        is_multi = "+" in pool

        # one node per stop within this shift pool
        nodes = []
        for key, sub in located.groupby("pickup_key"):
            c = coords[key]
            nodes.append({"key": key, "name": c["name"], "lat": c["lat"], "lon": c["lon"],
                          "count": len(sub), "emp_idx": list(sub.index)})
        nodes.sort(key=lambda n: (-n["count"], n["name"]))

        buses = pack_pool(nodes) if nodes else []
        buses.sort(key=lambda b: (-load_total(b, nodes), nodes[b[0]]["name"]))

        for number, bus in enumerate(buses, start=1):
            bus_id = f"{pool}-{number:02d}"
            load = load_total(bus, nodes)
            anchor = nodes[bus[0]]
            flags = []
            if load > config.BUS_CAPACITY:
                status = "Over capacity - split needed"
            elif load < config.MIN_EMPLOYEES_PER_BUS:
                status = "Below minimum - supervisor review"
            else:
                status = "OK"
            if is_multi:
                flags.append("Multi-shift pool - supervisor to assign a shift")
            if status != "OK":
                flags.append(status)
            if len(bus) > 1 and max_internal_distance(bus, nodes) > config.MAX_CLUSTER_RADIUS_KM:
                flags.append("Stops further apart than radius")

            bus_rows.append({
                "bus_id": bus_id,
                "shift": pool,
                "seats_capacity": config.BUS_CAPACITY,
                "employees": load,
                "seats_used_pct": round(100 * load / config.BUS_CAPACITY, 1),
                "stops": len(bus),
                "stop_list": " | ".join(nodes[i]["name"] for i in bus),
                "anchor_stop": anchor["name"],
                "max_distance_between_stops_km": max_internal_distance(bus, nodes),
                "status": status if not is_multi else f"{status} (multi-shift)",
                "warnings": "; ".join(flags),
            })
            for i in bus:
                n = nodes[i]
                stop_rows.append({"shift": pool, "pickup_point": n["name"], "employees": n["count"],
                                  "latitude": n["lat"], "longitude": n["lon"], "bus_id": bus_id})
                for idx in n["emp_idx"]:
                    e = emp.loc[idx]
                    assignment_rows.append({
                        "employee_id": e["employee_id"], "employee_name": e["employee_name"],
                        "grade": e["grade"], "shift": pool, "pickup_point": n["name"],
                        "mobile": e["mobile"], "bus_id": bus_id, "bus_status": status,
                        "bus_group_size": load,
                        "distance_pickup_to_anchor_km": round(stop_distance(n, anchor), 2),
                        "employee_warnings": e.get("warnings", ""),
                    })

        for idx, e in unlocated.iterrows():
            assignment_rows.append({
                "employee_id": e["employee_id"], "employee_name": e["employee_name"],
                "grade": e["grade"], "shift": pool, "pickup_point": e["pickup_point"],
                "mobile": e["mobile"], "bus_id": "UNASSIGNED", "bus_status": "No approved location",
                "bus_group_size": None, "distance_pickup_to_anchor_km": None,
                "employee_warnings": e.get("warnings", ""),
            })

    buses_df = pd.DataFrame(bus_rows, columns=BUS_COLUMNS)
    assign_df = pd.DataFrame(assignment_rows, columns=ASSIGN_COLUMNS)
    stops_df = pd.DataFrame(stop_rows, columns=STOP_COLUMNS)
    unassigned_df = assign_df[assign_df["bus_id"] == "UNASSIGNED"].reset_index(drop=True)

    write_workbook(config.OUT_GROUPING, {
        "Bus Groups": buses_df,
        "Stop to Bus": stops_df,
        "Employee Assignments": assign_df,
        "Unassigned": unassigned_df,
    })

    print(f"Shift pools            : {', '.join(pools)}")
    print(f"Buses proposed         : {len(buses_df)}")
    if len(buses_df):
        print(f"  status OK            : {(buses_df['status'] == 'OK').sum()}")
        print(f"  needs review         : {(buses_df['status'] != 'OK').sum()}")
    print(f"Employees assigned     : {(assign_df['bus_id'] != 'UNASSIGNED').sum()}")
    print(f"Employees unassigned   : {len(unassigned_df)}")
    print(f"Written                : {config.OUT_GROUPING}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
