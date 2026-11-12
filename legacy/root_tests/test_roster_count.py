import pandas as pd
from run_route_planner import load_all_verified_stops, DEFAULT_FACILITY

df = pd.read_excel('D:/bus_routing_project/data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx', header=1)
lookup = load_all_verified_stops()

print("Total roster rows:", len(df))
shifts = {}
for i, r in df.iterrows():
    s = str(r.get('Shift', 'A')).strip()
    s_key = 'A' if 'A' in s.upper() else ('B' if 'B' in s.upper() else 'C')
    shifts.setdefault(s_key, []).append(r)

for s, rows in shifts.items():
    print(f"Shift {s}: {len(rows)} employees")
