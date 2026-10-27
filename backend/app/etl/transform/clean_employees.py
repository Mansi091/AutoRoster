"""Employee record cleaning and attribute normalization."""

from typing import List, Dict, Any
from ...utils.text import format_mobile_number, normalize_pickup_text
from ...core.config import config

def clean_employee_records(raw_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sanitizes raw roster records into clean domain format."""
    cleaned = []
    for idx, row in enumerate(raw_records, 1):
        emp_id = str(row.get("employee_id") or f"EMP{idx:03d}").strip()
        emp_name = str(row.get("employee_name") or "Unnamed Employee").strip()
        
        # Shift extraction (A, B, C)
        shift_raw = str(row.get("shift") or "A").upper()
        if "A" in shift_raw:
            shift = "A"
        elif "B" in shift_raw:
            shift = "B"
        elif "C" in shift_raw:
            shift = "C"
        else:
            shift = "A"

        pickup = normalize_pickup_text(row.get("pickup_point"))
        mobile = format_mobile_number(row.get("mobile"))
        grade = str(row.get("grade") or "").strip()

        # Preserve any pre-existing verified coordinates from upload
        lat = row.get("latitude") or row.get("lat")
        lon = row.get("longitude") or row.get("lon")
        try:
            lat = float(lat) if lat is not None and str(lat).strip() else None
            lon = float(lon) if lon is not None and str(lon).strip() else None
        except (ValueError, TypeError):
            lat = lon = None

        cleaned.append({
            "employee_id": emp_id,
            "employee_name": emp_name,
            "shift": shift,
            "pickup_point": pickup,
            "mobile": mobile,
            "grade": grade,
            "latitude": lat,
            "longitude": lon,
            "location_status": row.get("location_status", "UNVERIFIED"),
            "resolution_reason": row.get("resolution_reason"),
        })

    return cleaned
