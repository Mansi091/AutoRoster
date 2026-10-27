"""STEP 1 - Clean the raw employee transport sheet.

Input : data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx
Output: output/01_cleaned_employees.xlsx
          - Employees        : rows that can be routed (warnings kept in a column)
          - Review Queue     : rows with blocking problems (never deleted, just moved)
          - Cleaning Log     : every value that was changed, with the rule used
          - Column Map       : original header -> standard column name
"""
import re
import sys
from pathlib import Path

import openpyxl
import pandas as pd

import config
from utils import clean_spaces, is_blank, smart_case, standardize_pickup, pickup_key, write_workbook

# Position of each raw column (1-based index in the sheet) -> standard name
RAW_COLUMNS = {
    1: ("Sr. No.", "sr_no_original"),
    2: ("EMP ID", "employee_id"),
    3: ("GRADE", "grade"),
    4: ("Name", "employee_name"),
    5: ("Shift", "shift"),
    6: ("Location", "area"),
    7: ("Bus Stop", "pickup_point_raw"),
    8: ("Mob. No.", "mobile"),
    9: ("line", "line"),
    10: (None, "unnamed_col_J"),
    11: (None, "section_tag"),
}

GRADE_MAP = {
    "DET": "DET",
    "ASSOCIATE": "Associate",
    "STAFF": "Staff",
    "TRAINEE": "Trainee",
    "TEAMELEASE": "TeamLease",
    "TEAMLEASE": "TeamLease",
}

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
BLOCKING = {"Critical", "High"}


class RowIssues:
    def __init__(self):
        self.items = []  # (severity, message)

    def add(self, severity, message):
        self.items.append((severity, message))

    def worst(self):
        if not self.items:
            return ""
        return min(self.items, key=lambda x: SEVERITY_ORDER[x[0]])[0]

    def text(self):
        return " | ".join(f"[{s}] {m}" for s, m in self.items)


def normalize_shift(raw):
    """Return (clean_shift, ok). 'A + B' -> 'A+B', 'a' -> 'A'."""
    text = re.sub(r"\s+", "", clean_spaces(raw).upper())
    if text in config.VALID_SHIFTS:
        return text, True
    if re.fullmatch(r"[ABC](\+[ABC])+", text):
        parts = sorted(set(text.split("+")))
        return "+".join(parts), True
    return "", False


def normalize_mobile(raw):
    """Return (digits, status) where status is 'ok', 'missing' or 'invalid'."""
    if is_blank(raw):
        return "", "missing"
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if re.fullmatch(r"[6-9]\d{9}", digits) and digits != "9999999999":
        return digits, "ok"
    return digits, "invalid"


def normalize_employee_id(raw):
    if is_blank(raw):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    text = re.sub(r"\D", "", str(raw))
    return int(text) if text else None


def normalize_grade(raw):
    text = clean_spaces(raw)
    if not text:
        return ""
    return GRADE_MAP.get(text.upper(), smart_case(text))


def main():
    wb = openpyxl.load_workbook(config.RAW_FILE, data_only=True)
    ws = wb.worksheets[0]
    source_sheet_name = ws.title

    records, log, issues_by_row = [], [], {}
    seen_ids = {}      # employee_id -> (name, row)
    seen_mobiles = {}  # mobile -> [names]

    for excel_row in range(config.RAW_FIRST_DATA_ROW, ws.max_row + 1):
        raw = {std: ws.cell(row=excel_row, column=col).value
               for col, (_, std) in RAW_COLUMNS.items()}
        if all(is_blank(v) for v in raw.values()):
            continue

        issues = RowIssues()
        rec = {"source_row": excel_row}

        # --- Column-shift fix for the PDI block -------------------------------
        # In the PDI rows the values sit one column to the left: B holds the grade
        # ("DET") and C holds the serial number, and the EMP ID column is empty.
        section = clean_spaces(raw["section_tag"]).upper()
        rec["source_section"] = section or "Main list"
        if section == "PDI":
            shifted_grade = raw["employee_id"]
            shifted_sr = raw["grade"]
            log.append((excel_row, raw["employee_name"], "column layout",
                        f"B={shifted_grade!r}, C={shifted_sr!r}",
                        "grade and serial number moved to the correct columns", "PDI block shifted left"))
            raw["grade"] = shifted_grade
            raw["employee_id"] = None
            raw["sr_no_original"] = shifted_sr

        # --- Name ---------------------------------------------------------------
        name_raw = raw["employee_name"]
        name = smart_case(clean_spaces(name_raw))
        if not name:
            issues.add("High", "Employee name is missing")
        if name != str(name_raw):
            log.append((excel_row, name, "employee_name", name_raw, name,
                        "trimmed spaces / standard capitalisation"))
        rec["employee_name"] = name

        # --- Employee ID --------------------------------------------------------
        emp_id = normalize_employee_id(raw["employee_id"])
        if emp_id is None:
            issues.add("Medium", "Employee ID missing")
        elif not is_blank(raw["employee_id"]) and str(emp_id) != str(raw["employee_id"]).strip():
            log.append((excel_row, name, "employee_id", raw["employee_id"], emp_id, "converted to number"))
        rec["employee_id"] = emp_id

        # --- Grade --------------------------------------------------------------
        grade = normalize_grade(raw["grade"])
        if not grade:
            issues.add("Low", "Grade missing")
        elif grade != raw["grade"]:
            log.append((excel_row, name, "grade", raw["grade"], grade, "standard grade spelling"))
        rec["grade"] = grade

        # --- Shift --------------------------------------------------------------
        shift, shift_ok = normalize_shift(raw["shift"])
        if not shift_ok:
            issues.add("High", f"Shift missing or not recognised: {raw['shift']!r}")
        else:
            if shift != raw["shift"]:
                log.append((excel_row, name, "shift", raw["shift"], shift, "standard shift code"))
            if "+" in shift:
                issues.add("Low", f"Multi-shift employee ({shift}) - supervisor to assign a shift")
        rec["shift"] = shift

        # --- Pickup point -------------------------------------------------------
        pickup_raw = raw["pickup_point_raw"]
        pickup = standardize_pickup(pickup_raw)
        if not pickup:
            issues.add("Critical", "Bus stop (pickup point) missing - employee cannot be routed")
        elif pickup != clean_spaces(pickup_raw):
            log.append((excel_row, name, "pickup_point", pickup_raw, pickup,
                        "standardised pickup point name"))
        rec["pickup_point"] = pickup
        rec["pickup_key"] = pickup_key(pickup) if pickup else ""

        # --- Area (residence zone) ----------------------------------------------
        area = smart_case(clean_spaces(raw["area"]))
        if not area:
            issues.add("Low", "Location / area missing")
        rec["area"] = area

        # --- Mobile -------------------------------------------------------------
        mobile, mobile_status = normalize_mobile(raw["mobile"])
        if mobile_status == "missing":
            issues.add("Low", "Mobile number missing")
        elif mobile_status == "invalid":
            issues.add("Medium", f"Mobile number invalid or placeholder: {clean_spaces(raw['mobile'])!r}")
        rec["mobile"] = mobile if mobile else ""
        if mobile_status == "ok":
            seen_mobiles.setdefault(mobile, []).append(name)

        # --- Line / department ----------------------------------------------------
        rec["line"] = smart_case(clean_spaces(raw["line"]))
        rec["sr_no_original"] = raw["sr_no_original"] if not is_blank(raw["sr_no_original"]) else ""

        if emp_id is not None:
            seen_ids.setdefault(emp_id, []).append(rec)

        rec["_issues"] = issues
        records.append(rec)

    # --- Employee ID shared by different people: flag every row involved ----------
    for emp_id, recs in seen_ids.items():
        names = {r["employee_name"].lower() for r in recs}
        if len(names) > 1:
            for r in recs:
                others = sorted({o["employee_name"] for o in recs if o is not r})
                r["_issues"].add("High", f"Employee ID {emp_id} is shared with a different person: "
                                         f"{', '.join(others)} - identity conflict")

    # --- Duplicate checks across rows ------------------------------------------
    exact_seen = {}
    for rec in records:
        issues = rec["_issues"]
        key = (rec["employee_name"].lower(), rec["employee_id"] or "", rec["pickup_key"], rec["shift"])
        if key in exact_seen:
            issues.add("High", f"Possible duplicate record of row {exact_seen[key]}")
        else:
            exact_seen[key] = rec["source_row"]

    for rec in records:
        mobile = rec["mobile"]
        if mobile and len(seen_mobiles.get(mobile, [])) > 1:
            others = [n for n in seen_mobiles[mobile] if n != rec["employee_name"]]
            if others:
                rec["_issues"].add("Low", f"Mobile number shared with: {', '.join(sorted(set(others)))}")

    # --- Build outputs -------------------------------------------------------------
    clean_rows, review_rows = [], []
    for rec in records:
        issues = rec.pop("_issues")
        rec["severity"] = issues.worst() or "OK"
        rec["issues"] = issues.text()
        if issues.worst() in BLOCKING:
            review_rows.append(rec)
        else:
            clean_rows.append(rec)

    columns = ["employee_id", "employee_name", "grade", "shift", "pickup_point", "pickup_key",
               "area", "mobile", "line", "source_section", "source_row", "severity", "issues"]

    clean_df = pd.DataFrame(clean_rows)
    review_df = pd.DataFrame(review_rows)
    for df in (clean_df, review_df):
        for col in columns:
            if col not in df.columns:
                df[col] = ""
    clean_df = clean_df[columns].copy()
    review_df = review_df[columns].copy() if len(review_df) else pd.DataFrame(columns=columns)

    clean_df.insert(0, "sr_no", range(1, len(clean_df) + 1))
    clean_df = clean_df.rename(columns={"severity": "warning_level", "issues": "warnings"})
    review_df = review_df.rename(columns={"severity": "problem_level", "issues": "problems"})
    review_df.insert(0, "review_no", range(1, len(review_df) + 1))
    review_df["review_status"] = "Needs supervisor decision"

    log_df = pd.DataFrame(log, columns=["source_row", "employee_name", "field", "original_value",
                                        "cleaned_value", "rule"])
    # the sheet-shift log entries have a different shape; keep them readable
    log_df = log_df.rename(columns={"cleaned_value": "cleaned_value"})

    column_map = pd.DataFrame(
        [(src or "(no header)", std, f"Column {openpyxl.utils.get_column_letter(col)}")
         for col, (src, std) in RAW_COLUMNS.items()],
        columns=["original_header", "standard_name", "raw_column"])

    write_workbook(config.OUT_CLEANED, {
        "Employees": clean_df,
        "Review Queue": review_df,
        "Cleaning Log": log_df,
        "Column Map": column_map,
    })

    print(f"Source sheet          : {source_sheet_name}")
    print(f"Rows read             : {len(records)}")
    print(f"Clean (routable)      : {len(clean_df)}")
    print(f"Review queue (blocked): {len(review_df)}")
    print(f"Values changed (log)  : {len(log_df)}")
    print(f"Written               : {config.OUT_CLEANED}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
