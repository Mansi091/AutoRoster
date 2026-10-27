"""Create executive-ready Excel file: FINAL_BUS_ROSTER.xlsx.

Groups employees using route_planner with strict 32 capacity per bus for every shift.
"""
from pathlib import Path
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
import pandas as pd

import route_planner as rp
import plan_routes as pr

ROOT = Path(__file__).resolve().parent

def main():
    settings = pr.load_settings(ROOT / "settings.json")
    roster_path = ROOT / settings["roster_path"]
    stops_path = ROOT / settings["stops_path"]
    fac = settings["facility"]

    p = rp.RoutingParams(
        bus_capacity=int(settings["bus_capacity"]),
        max_ride_minutes=float(settings["max_ride_minutes"]),
        bus_fixed_cost_minutes=float(settings["bus_fixed_cost_minutes"]),
        boarding_seconds_per_passenger=float(settings["boarding_seconds_per_passenger"]),
        min_boarding_minutes=float(settings["min_boarding_minutes"]),
    )

    employees, _ = pr.read_roster(roster_path, settings.get("roster_sheet"), settings)
    stops = pr.read_stop_master(stops_path, settings.get("stops_sheet"), settings)
    priority = [s.upper() for s in settings["shift_priority"]]
    rows, _ = pr.assign_employees(employees, priority, stops)

    points = [(fac["latitude"], fac["longitude"])]
    node_of = {}
    for key in sorted({r["pickup_key"] for r in rows if r["pickup_key"] in stops}):
        coord = (stops[key]["lat"], stops[key]["lon"])
        if coord not in node_of:
            node_of[coord] = len(points)
            points.append(coord)

    matrix = rp.build_travel_matrix(
        points, settings["travel_time_provider"], settings["osrm_url"],
        float(settings["fallback_road_factor"]), float(settings["fallback_speed_kmph"])
    )
    M = matrix.minutes
    FAC = 0

    bus_summary = []
    assignments = []

    for shift in priority:
        units = pr.build_units(rows, shift, stops, node_of, p)
        if not units:
            continue
        routes = rp.group_units(units, M, FAC, p)

        for ridx, route in enumerate(routes, start=1):
            bus_id = f"Bus {shift}-{ridx:02d}"
            rides, pax_min = rp.evaluate(route, units, M, FAC)
            seq_nodes = [units[u].node for u in route]
            total_pax = sum(units[u].pax for u in route)
            route_min = rides[0] if rides else 0.0
            road_km = sum(matrix.km[seq_nodes[k]][seq_nodes[k + 1] if k + 1 < len(seq_nodes) else FAC]
                          for k in range(len(seq_nodes)))

            stops_names = [units[u].stop_name for u in route]
            route_text = " -> ".join(stops_names)

            bus_summary.append({
                "Bus ID": bus_id,
                "Shift": f"Shift {shift}",
                "Passengers": total_pax,
                "Capacity": p.bus_capacity,
                "Seats Free": p.bus_capacity - total_pax,
                "Total Stops": len(route),
                "Route (km)": round(road_km, 1),
                "Longest Ride (min)": round(max(rides), 1),
                "Route Sequence": route_text,
            })

            for pos, u in enumerate(route, 1):
                unit = units[u]
                ride = rides[pos - 1]
                for emp in unit.employees:
                    assignments.append({
                        "Shift": f"Shift {shift}",
                        "Assigned Bus": bus_id,
                        "Stop Sequence": pos,
                        "Employee ID": emp.get("employee_id", ""),
                        "Employee Name": emp.get("employee_name", ""),
                        "Pickup Point": emp.get("pickup_raw", ""),
                        "Area / Landmark": emp.get("area", ""),
                        "Mobile No": emp.get("mobile", ""),
                        "Ride Time (min)": round(ride, 1),
                    })

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Fleet Summary"
    ws1.views.sheetView[0].showGridLines = True

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")

    df_sum = pd.DataFrame(bus_summary)
    ws1.append(list(df_sum.columns))
    for cell in ws1[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r in df_sum.to_dict(orient="records"):
        ws1.append(list(r.values()))

    ws2 = wb.create_sheet(title="Passenger Manifest")
    ws2.views.sheetView[0].showGridLines = True
    df_emp = pd.DataFrame(assignments)
    ws2.append(list(df_emp.columns))
    for cell in ws2[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r in df_emp.to_dict(orient="records"):
        ws2.append(list(r.values()))

    for ws in (ws1, ws2):
        for col in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col), default=10)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 60)

    out_file = ROOT / "FINAL_BUS_ROSTER.xlsx"
    wb.save(out_file)
    print(f"Generated: {out_file} ({len(bus_summary)} buses, 32-capacity enforced)")

if __name__ == "__main__":
    main()
