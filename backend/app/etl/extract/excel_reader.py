"""Excel file ingestion and raw record extraction."""

import io
from pathlib import Path
from typing import List, Dict, Any, Union
import pandas as pd
from ...core.logging import logger

def read_roster_excel(file_source: Union[str, Path, bytes, io.BytesIO]) -> List[Dict[str, Any]]:
    """Reads raw employee roster Excel workbook and normalizes column headers."""
    try:
        # Check first 5 rows to locate header row containing 'name', 'shift', or 'stop'
        df_preview = pd.read_excel(file_source, nrows=5, header=None)
        header_row = 0
        for r_idx, row in df_preview.iterrows():
            row_str = " ".join(str(v).lower() for v in row.values)
            if ("name" in row_str or "emp" in row_str) and ("shift" in row_str or "stop" in row_str or "pickup" in row_str or "location" in row_str):
                header_row = r_idx
                break

        # Seek if file_source is stream
        if hasattr(file_source, "seek"):
            file_source.seek(0)

        df = pd.read_excel(file_source, header=header_row)
    except Exception as e:
        logger.error(f"Failed to read Excel workbook: {e}")
        raise ValueError(f"Unable to parse Excel file: {e}")

    # Standardize column names
    col_map = {}
    for col in df.columns:
        c_clean = str(col).strip().lower().replace(" ", "_")
        if "emp" in c_clean and "id" in c_clean:
            col_map[col] = "employee_id"
        elif "emp" in c_clean and "name" in c_clean:
            col_map[col] = "employee_name"
        elif "name" in c_clean and "employee_name" not in col_map.values():
            col_map[col] = "employee_name"
        elif "shift" in c_clean:
            col_map[col] = "shift"
        elif "pickup" in c_clean or "stop" in c_clean or "location" in c_clean:
            col_map[col] = "pickup_point"
        elif "mobile" in c_clean or "contact" in c_clean or "phone" in c_clean or "mob" in c_clean:
            col_map[col] = "mobile"
        elif "grade" in c_clean:
            col_map[col] = "grade"

    df = df.rename(columns=col_map)
    records = df.to_dict(orient="records")
    logger.info(f"Extracted {len(records)} raw employee rows from Excel.")
    return records
