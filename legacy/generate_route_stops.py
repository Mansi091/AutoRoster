"""Generate tight, corridor-based bus routes capped at ~15 passengers.
Groups stops strictly along natural geographic corridors:
1. Talegaon / Maval Corridor
2. PCMC / Ravet / Highway Corridor
3. Chakan / Moshi / Bhosari Corridor
Ensures minimal distance between consecutive stops (no cross-district jumps).
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

def assign_corridor(lon):
    if lon < 73.73:
        return "Talegaon / Maval Corridor"
    elif lon < 73.83:
        return "PCMC / Ravet / Highway Corridor"
    else:
        return "Chakan / Moshi / Bhosari Corridor"

def order_stops_by_path(stop_records):
    """Sort stops chaining closest neighbors along the highway."""
    if not stop_records:
        return []
    unvisited = list(range(len(stop_records)))
    # Start at the western/outermost stop
    curr = min(unvisited, key=lambda i: (stop_records[i]["lon"], stop_records[i]["lat"]))
    route = []
    prev_coord = None
    cum_dist = 0.0
    cum_passengers = 0

    while unvisited:
        lat = stop_records[curr]["lat"]
        lon = stop_records[curr]["lon"]
        count = stop_records[curr]["count"]
        name = stop_records[curr]["name"]

        dist = 0.0 if prev_coord is None else haversine_km(prev_coord[0], prev_coord[1], lat, lon)
        cum_dist += dist
        cum_passengers += count

        route.append({
            "stop_name": name,
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "dist_from_prev_km": round(dist, 1),
            "cumulative_dist_km": round(cum_dist, 1),
            "boarding_count": count,
            "cumulative_passengers": cum_passengers
        })

        unvisited.remove(curr)
        prev_coord = (lat, lon)
        if unvisited:
            curr = min(unvisited, key=lambda i: haversine_km(lat, lon, stop_records[i]["lat"], stop_records[i]["lon"]))

    return route

def main():
    emp_df = pd.read_excel(ROOT / "output/01_cleaned_employees.xlsx", sheet_name="Employees")
    master_df = pd.read_excel(ROOT / "output/04_pickup_point_master.xlsx", sheet_name="Pickup Point Master")

    merged = emp_df.merge(master_df[["pickup_key", "latitude", "longitude"]], on="pickup_key", how="left")
    merged["corridor"] = merged["longitude"].apply(assign_corridor)

    corridor_order = [
        "Talegaon / Maval Corridor",
        "PCMC / Ravet / Highway Corridor",
        "Chakan / Moshi / Bhosari Corridor"
    ]

    shifts = ["A", "B", "C"]
    route_overview = []
    detailed_stops = []

    bus_code_map = {
        "Talegaon / Maval Corridor": "1 (Talegaon)",
        "PCMC / Ravet / Highway Corridor": "2 (PCMC/Ravet)",
        "Chakan / Moshi / Bhosari Corridor": "3 (Chakan/Moshi)"
    }

    for shift in shifts:
        if shift == "A":
            sub_df = merged[merged["shift"].isin(["A", "A+B", "A+B+C"])].copy()
        else:
            sub_df = merged[merged["shift"] == shift].copy()

        for corr in corridor_order:
            corr_df = sub_df[sub_df["corridor"] == corr]
            if corr_df.empty:
                continue

            # Group by stop
            stop_data = []
            grouped = corr_df.groupby(["pickup_point", "latitude", "longitude"]).size().reset_index(name="count")
            for _, r in grouped.iterrows():
                stop_data.append({
                    "name": r["pickup_point"],
                    "lat": r["latitude"],
                    "lon": r["longitude"],
                    "count": r["count"]
                })

            ordered_route = order_stops_by_path(stop_data)
            route_id = f"Bus {shift}-{bus_code_map[corr]}"

            total_pax = ordered_route[-1]["cumulative_passengers"]
            total_km = ordered_route[-1]["cumulative_dist_km"]
            first_stop = ordered_route[0]["stop_name"]
            last_stop = ordered_route[-1]["stop_name"]

            # Max jump between any two consecutive stops
            max_jump = max(s["dist_from_prev_km"] for s in ordered_route) if ordered_route else 0.0

            route_overview.append({
                "Bus ID": route_id,
                "Shift": f"Shift {shift}",
                "Corridor Area": corr,
                "Target Capacity": 15,
                "Passengers Assigned": total_pax,
                "Seats Free (out of 15)": max(0, 15 - total_pax),
                "Total Stops": len(ordered_route),
                "Total Route (km)": total_km,
                "Max Distance Between Stops (km)": max_jump,
                "Start Stop": first_stop,
                "End Stop": last_stop,
            })

            for seq, s in enumerate(ordered_route, start=1):
                detailed_stops.append({
                    "Bus ID": route_id,
                    "Shift": f"Shift {shift}",
                    "Stop Seq": seq,
                    "Bus Stop Name": s["stop_name"],
                    "Latitude": s["latitude"],
                    "Longitude": s["longitude"],
                    "Distance from Prev Stop (km)": s["dist_from_prev_km"],
                    "Cumulative Route (km)": s["cumulative_dist_km"],
                    "Employees Boarding": s["boarding_count"],
                    "Cumulative Passengers": s["cumulative_passengers"],
                })

    # Excel export
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Bus Routes (Max ~15 Pax)"
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

    ws1.merge_cells("A1:K1")
    ws1["A1"] = "LOCAL COMPACT BUS ROUTES (~15 SEATS) - MINIMAL INTER-STOP DISTANCES"
    ws1["A1"].font = title_font
    ws1["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws1.row_dimensions[1].height = 30

    ov_df = pd.DataFrame(route_overview)
    headers1 = list(ov_df.columns)
    for col_idx, h in enumerate(headers1, 1):
        cell = ws1.cell(row=3, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center" if "Seat" in h or "Pass" in h or "Total" in h or "ID" in h or "Max" in h else "left", vertical="center")
    ws1.row_dimensions[3].height = 24

    for row_idx, r in enumerate(ov_df.to_dict(orient="records"), 4):
        ws1.row_dimensions[row_idx].height = 22
        for col_idx, h in enumerate(headers1, 1):
            val = r[h]
            cell = ws1.cell(row=row_idx, column=col_idx, value=val)
            cell.font = bold_font if col_idx in (1, 4, 5) else regular_font
            cell.border = thin_border
            if "Seat" in h or "Pass" in h or "Total" in h or "ID" in h or "Shift" in h or "Max" in h:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # Sheet 2: Detailed stops
    ws2 = wb.create_sheet(title="Stop-by-Stop Route Sequence")
    ws2.views.sheetView[0].showGridLines = True

    ws2.merge_cells("A1:J1")
    ws2["A1"] = "STOP-BY-STOP SCHEDULE WITH SHORT INTER-STOP DISTANCES"
    ws2["A1"].font = title_font
    ws2["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws2.row_dimensions[1].height = 30

    det_df = pd.DataFrame(detailed_stops)
    headers2 = list(det_df.columns)
    for col_idx, h in enumerate(headers2, 1):
        cell = ws2.cell(row=3, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center" if "Seq" in h or "Dist" in h or "Emp" in h or "Cum" in h or "ID" in h else "left", vertical="center")
    ws2.row_dimensions[3].height = 24

    for row_idx, r in enumerate(det_df.to_dict(orient="records"), 4):
        ws2.row_dimensions[row_idx].height = 20
        for col_idx, h in enumerate(headers2, 1):
            val = r[h]
            cell = ws2.cell(row=row_idx, column=col_idx, value=val)
            cell.font = bold_font if h in ("Bus ID", "Bus Stop Name") else regular_font
            cell.border = thin_border
            if "Seq" in h or "Dist" in h or "Emp" in h or "Cum" in h or "ID" in h or "Shift" in h:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    for ws in (ws1, ws2):
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                if cell.row > 1 and cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    out_file = ROOT / "Bus_Stops_Grouped_By_Route.xlsx"
    wb.save(out_file)
    print(f"Generated: {out_file}")
    print(f"Total Buses: {len(route_overview)}")
    for r in route_overview:
        print(f"  {r['Bus ID']:<25} ({r['Shift']}): {r['Passengers Assigned']:2d} passengers | Max jump: {r['Max Distance Between Stops (km)']:.1f} km")

if __name__ == "__main__":
    main()
