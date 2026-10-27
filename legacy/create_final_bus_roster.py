"""Create a single, executive-ready Excel file: FINAL_BUS_ROSTER.xlsx.
Groups 93 employees into realistic 32-seater buses (4-5 buses total) by shift & geographic corridor.
"""
import math
from pathlib import Path
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
import pandas as pd

ROOT = Path(__file__).resolve().parent

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2)**2
    return 2 * R * math.asin(math.sqrt(a))

def sort_stops_as_route(stops_coords):
    """Sort stops in a logical driving path from westernmost/northernmost stop."""
    if not stops_coords:
        return []
    unvisited = list(stops_coords.keys())
    # Start at the stop furthest from Pune centre (westernmost/start of route)
    curr = min(unvisited, key=lambda s: (stops_coords[s][1], stops_coords[s][0]))
    route = [curr]
    unvisited.remove(curr)
    while unvisited:
        nxt = min(unvisited, key=lambda s: haversine_km(stops_coords[curr][0], stops_coords[curr][1],
                                                       stops_coords[s][0], stops_coords[s][1]))
        route.append(nxt)
        unvisited.remove(nxt)
        curr = nxt
    return route

def main():
    emp_df = pd.read_excel(ROOT / "output/01_cleaned_employees.xlsx", sheet_name="Employees")
    master_df = pd.read_excel(ROOT / "output/04_pickup_point_master.xlsx", sheet_name="Pickup Point Master")

    # Merge coordinates
    merged = emp_df.merge(master_df[["pickup_key", "latitude", "longitude"]], on="pickup_key", how="left")

    # Shift mapping
    # 34 Shift A, 26 Shift B, 23 Shift C, 9 A+B, 1 A+B+C
    # In practice:
    # Shift A: ~34 employees (+ 5 multi-shift = ~39) -> 2 buses: Bus A-1 (Ravet/Talegaon corridor) and Bus A-2 (Chakan/PCMC corridor)
    # Shift B: ~26 employees (+ 4 multi-shift = ~30) -> 1 bus: Bus B-1 (Capacity 32)
    # Shift C: ~23 employees (+ 1 multi-shift = ~24) -> 1 bus: Bus C-1 (Capacity 32)

    assignments = []
    bus_groups = []

    # --- PROCESS SHIFT A ---
    shift_a_emp = merged[merged["shift"].isin(["A", "A+B", "A+B+C"])].copy()
    # Let's split Shift A into 2 geographic corridors if count > 32
    # Corridor split based on Longitude: West (Talegaon/Ravet/Dehu, lon < 73.78) vs East (Chakan/Moshi/Bhosari, lon >= 73.78)
    # Or k-means / median split
    med_lon = shift_a_emp["longitude"].median()
    bus_a1_emp = shift_a_emp[shift_a_emp["longitude"] <= med_lon].copy()
    bus_a2_emp = shift_a_emp[shift_a_emp["longitude"] > med_lon].copy()

    # If one is over 32, rebalance nearest stops
    while len(bus_a1_emp) > 32 and len(bus_a2_emp) < 32:
        move_emp = bus_a1_emp.sort_values("longitude", ascending=False).iloc[0]
        bus_a1_emp = bus_a1_emp[bus_a1_emp["employee_name"] != move_emp["employee_name"]]
        bus_a2_emp = pd.concat([bus_a2_emp, pd.DataFrame([move_emp])], ignore_index=True)

    while len(bus_a2_emp) > 32 and len(bus_a1_emp) < 32:
        move_emp = bus_a2_emp.sort_values("longitude", ascending=True).iloc[0]
        bus_a2_emp = bus_a2_emp[bus_a2_emp["employee_name"] != move_emp["employee_name"]]
        bus_a1_emp = pd.concat([bus_a1_emp, pd.DataFrame([move_emp])], ignore_index=True)

    # --- PROCESS SHIFT B ---
    shift_b_emp = merged[merged["shift"] == "B"].copy()

    # --- PROCESS SHIFT C ---
    shift_c_emp = merged[merged["shift"] == "C"].copy()

    buckets = [
        ("Bus A-1", "Shift A", bus_a1_emp, "Talegaon - Ravet - Dehu Corridor"),
        ("Bus A-2", "Shift A", bus_a2_emp, "Chakan - Moshi - PCMC Corridor"),
        ("Bus B-1", "Shift B", shift_b_emp, "General Industrial Corridor (Shift B)"),
        ("Bus C-1", "Shift C", shift_c_emp, "General Industrial Corridor (Shift C)"),
    ]

    for bus_id, shift_name, b_df, corridor_desc in buckets:
        # Group stops and compute route sequence
        stop_coords = {}
        for _, r in b_df.iterrows():
            stop_coords[r["pickup_point"]] = (r["latitude"], r["longitude"])

        route_stops = sort_stops_as_route(stop_coords)
        route_text = " -> ".join(route_stops)

        # Passenger count
        passengers = len(b_df)
        seats_left = 32 - passengers

        bus_groups.append({
            "Bus ID": bus_id,
            "Shift": shift_name,
            "Corridor / Area": corridor_desc,
            "Passengers Assigned": passengers,
            "Seats Available": seats_left,
            "Bus Capacity": 32,
            "Total Stops": len(route_stops),
            "Pickup Route (In Sequence)": route_text
        })

        for _, r in b_df.iterrows():
            assignments.append({
                "Sr No": len(assignments) + 1,
                "Employee ID": r.get("employee_id", ""),
                "Employee Name": r["employee_name"],
                "Shift": r["shift"],
                "Pickup Point": r["pickup_point"],
                "Assigned Bus": bus_id,
                "Mobile No": r.get("mobile", ""),
                "Area / Landmark": r.get("area", "")
            })

    bus_summary_df = pd.DataFrame(bus_groups)
    passengers_df = pd.DataFrame(assignments)

    # Write styled Excel file
    out_path = ROOT / "output/FINAL_BUS_ROSTER.xlsx"
    root_out_path = ROOT / "FINAL_BUS_ROSTER.xlsx"

    wb = openpyxl.Workbook()
    # Tab 1: Bus Routes
    ws1 = wb.active
    ws1.title = "Bus Routes Summary"
    ws1.views.sheetView[0].showGridLines = True

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    title_font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    regular_font = Font(name="Calibri", size=11)
    bold_font = Font(name="Calibri", size=11, bold=True)
    thin_border = Border(left=Side(style="thin", color="D9D9D9"),
                         right=Side(style="thin", color="D9D9D9"),
                         top=Side(style="thin", color="D9D9D9"),
                         bottom=Side(style="thin", color="D9D9D9"))

    # Title Banner
    ws1.merge_cells("A1:H1")
    ws1["A1"] = "COMPANY EMPLOYEE TRANSPORTATION - BUS ROSTER & ROUTES"
    ws1["A1"].font = title_font
    ws1["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws1.row_dimensions[1].height = 30

    # Write Bus summary
    headers1 = list(bus_summary_df.columns)
    for col_idx, h in enumerate(headers1, 1):
        cell = ws1.cell(row=3, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center" if "Seat" in h or "Pass" in h or "Stop" in h else "left", vertical="center")
    ws1.row_dimensions[3].height = 24

    for row_idx, r in enumerate(bus_summary_df.to_dict(orient="records"), 4):
        ws1.row_dimensions[row_idx].height = 22
        for col_idx, h in enumerate(headers1, 1):
            val = r[h]
            cell = ws1.cell(row=row_idx, column=col_idx, value=val)
            cell.font = bold_font if col_idx in (1, 4, 5) else regular_font
            cell.border = thin_border
            if "Seat" in h or "Pass" in h or "Stop" in h or "ID" in h:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # Tab 2: Passenger list
    ws2 = wb.create_sheet(title="Employee Passenger List")
    ws2.views.sheetView[0].showGridLines = True

    ws2.merge_cells("A1:H1")
    ws2["A1"] = "EMPLOYEE BUS ASSIGNMENT & SEATING ROSTER"
    ws2["A1"].font = title_font
    ws2["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws2.row_dimensions[1].height = 30

    headers2 = list(passengers_df.columns)
    for col_idx, h in enumerate(headers2, 1):
        cell = ws2.cell(row=3, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center" if h in ("Sr No", "Shift", "Assigned Bus", "Mobile No") else "left", vertical="center")
    ws2.row_dimensions[3].height = 24

    for row_idx, r in enumerate(passengers_df.to_dict(orient="records"), 4):
        ws2.row_dimensions[row_idx].height = 20
        for col_idx, h in enumerate(headers2, 1):
            val = r[h]
            cell = ws2.cell(row=row_idx, column=col_idx, value=val)
            cell.font = bold_font if h == "Assigned Bus" else regular_font
            cell.border = thin_border
            if h in ("Sr No", "Shift", "Assigned Bus", "Mobile No"):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # Auto-fit columns
    for ws in (ws1, ws2):
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                if cell.row > 1 and cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 65)

    wb.save(out_path)
    wb.save(root_out_path)
    print(f"Generated clean roster at: {out_path}")
    print(f"Generated clean roster at: {root_out_path}")
    print(f"Buses created: {len(bus_summary_df)}")
    print(f"Employees assigned: {len(passengers_df)}")

if __name__ == "__main__":
    main()
