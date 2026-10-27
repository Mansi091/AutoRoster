"""Excel workbook exporter for bus routes and passenger manifests."""

import io
from pathlib import Path
from typing import List, Dict, Any, Union
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

NAVY = "1B365D"
WHITE = "FFFFFF"
LIGHT_GRAY = "F8FAFC"
BORDER_GRAY = "E2E8F0"

def _header_style():
    return {
        "font": Font(name="Calibri", size=11, bold=True, color=WHITE),
        "fill": PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid"),
        "alignment": Alignment(horizontal="center", vertical="center", wrap_text=True),
    }

def _thin_border():
    s = Side(border_style="thin", color=BORDER_GRAY)
    return Border(left=s, right=s, top=s, bottom=s)

def export_roster_workbook(
    routes: List[Dict[str, Any]],
    employees: List[Dict[str, Any]],
    unverified: List[Dict[str, Any]],
    output_target: Union[str, Path, io.BytesIO],
) -> None:
    """Exports formatted multi-tab Excel workbook with complete transit manifests."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # Remove default sheet

    h_style = _header_style()
    border = _thin_border()

    # --- Sheet 1: Fleet Summary ---
    ws_sum = wb.create_sheet(title="Fleet Summary")
    ws_sum.views.sheetView[0].showGridLines = True
    sum_headers = ["Bus ID", "Shift", "Corridor", "Assigned Pax", "Capacity", "Occupancy %", "Stops", "Route Km", "Est Minutes", "Commute Status"]
    ws_sum.append(sum_headers)

    for cell in ws_sum[1]:
        cell.font = h_style["font"]
        cell.fill = h_style["fill"]
        cell.alignment = h_style["alignment"]

    for r in routes:
        pax = r.get("passengers_assigned", 0)
        cap = r.get("capacity", 32)
        occ = f"{round((pax / cap) * 100, 1)}%"
        ws_sum.append([
            r.get("bus_id"),
            r.get("shift"),
            r.get("corridor"),
            pax,
            cap,
            occ,
            r.get("total_stops", len(r.get("stops", []))),
            r.get("total_km"),
            r.get("total_minutes"),
            r.get("ride_time_status"),
        ])

    # --- Sheet 2: Route Stops ---
    ws_stops = wb.create_sheet(title="Route Stops")
    ws_stops.views.sheetView[0].showGridLines = True
    stops_headers = ["Bus ID", "Shift", "Corridor", "Seq", "Stop Name", "Boarding Pax", "Distance from Prev (km)", "Cumulative Km", "Commute to Plant (min)"]
    ws_stops.append(stops_headers)

    for cell in ws_stops[1]:
        cell.font = h_style["font"]
        cell.fill = h_style["fill"]
        cell.alignment = h_style["alignment"]

    for r in routes:
        for s in r.get("stops", []):
            ws_stops.append([
                r.get("bus_id"),
                r.get("shift"),
                r.get("corridor"),
                s.get("seq"),
                s.get("stop_name"),
                s.get("passengers_boarding"),
                s.get("distance_from_prev_km"),
                s.get("cumulative_km"),
                s.get("ride_minutes_to_destination"),
            ])

    # --- Sheet 3: Employee Manifest ---
    ws_emp = wb.create_sheet(title="Passenger Manifest")
    ws_emp.views.sheetView[0].showGridLines = True
    emp_headers = ["Emp ID", "Employee Name", "Grade", "Shift", "Assigned Bus", "Corridor", "Pickup Stop", "Mobile"]
    ws_emp.append(emp_headers)

    for cell in ws_emp[1]:
        cell.font = h_style["font"]
        cell.fill = h_style["fill"]
        cell.alignment = h_style["alignment"]

    for e in employees:
        ws_emp.append([
            e.get("employee_id"),
            e.get("employee_name"),
            e.get("grade"),
            e.get("shift"),
            e.get("assigned_bus"),
            e.get("corridor"),
            e.get("pickup_point"),
            e.get("mobile"),
        ])

    # Auto-adjust column widths
    for sheet in (ws_sum, ws_stops, ws_emp):
        for col in sheet.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            sheet.column_dimensions[col_letter].width = max(max_len + 3, 12)

    wb.save(output_target)
