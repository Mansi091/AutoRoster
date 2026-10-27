from run_route_planner import load_all_verified_stops, solve_shift_routes, get_confirmed_facility
import pandas as pd

df = pd.read_excel('D:/bus_routing_project/data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx', header=1)
emps = [
    {
        'sr_no': i + 1,
        'emp_name': str(r.get('Name', '')),
        'emp_type': str(r.get('GRADE', 'DET')),
        'shift': str(r.get('Shift', 'A')).strip(),
        'raw_stop': str(r.get('Bus Stop', '')).strip(),
        'mobile': str(r.get('Mob. No.', ''))
    }
    for i, r in df.iterrows() if 'A' in str(r.get('Shift', '')).upper()
]

lookup = load_all_verified_stops()
facility = get_confirmed_facility()
res = solve_shift_routes('A', emps, lookup, facility)
print("TOTAL BUSES:", res['buses_used'])
for r in res['routes']:
    stops = [s['stop_name'] for s in r['stops']]
    print(f"Bus {r['route_no']}: pax={r['total_passengers']}, duration={r['route_duration_min']}m, km={r['total_distance_km']}, stops={stops}")
