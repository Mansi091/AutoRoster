"""Test runner connecting Step 2 (Aggregator), Step 3 (Matrix), and Step 4 (OR-Tools CVRPTW)."""

import pprint
from aggregator_cvrptw import prepare_cvrptw_inputs_for_shift, ROOT
from optimizer_cvrptw import optimize_routes

def run_test():
    data_file = ROOT / "output/01_cleaned_employees.xlsx"
    print(f"Loading data from {data_file}...")
    prepared = prepare_cvrptw_inputs_for_shift(data_file, shift="A", max_stops=10)

    print(f"Optimizing Shift A ({prepared['total_stops']} stops, {prepared['total_employees']} employees)...")

    # Shift A starts at 6:50 AM (410 min from midnight).
    # Latest planned arrival at workplace: 6:40 AM (400 min).
    # Earliest departure from depot: 5:30 AM (330 min).
    res = optimize_routes(
        time_matrix=prepared["time_matrix"],
        demands=prepared["demands"],
        stop_names=prepared["stop_names"],
        service_minutes=prepared["service_minutes"],
        num_buses=5,
        earliest_departure=330,  # 05:30
        latest_departure=360,    # 06:00
        shift_start=410,         # 06:50
        max_route_minutes=55,
        arrival_buffer=10,
        bus_capacity=32,
    )

    print("\n================ CVRPTW OPTIMIZED SOLUTION ================")
    print(f"Status: {res['status']}")
    print(f"Total Buses Used: {res.get('total_buses_used')}")
    print(f"Total Routed Passengers: {res.get('total_passengers')}")
    print(f"Total Fleet Driving Minutes: {res.get('total_travel_minutes')}")
    print("\nDetailed Bus Routes:")
    for r in res.get("routes", []):
        bus_label = r["bus_id"]
        pax = r["passengers_assigned"]
        dur = r["route_duration_minutes"]
        dep = r["departure_time"]
        arr = r["arrival_time"]
        print(f"\n--- {bus_label} ({pax}/32 seats | Duration: {dur} mins | Depart: {dep} -> Arrive: {arr}) ---")
        for s in r["stops"]:
            t_str = s["time_clock"]
            name = s["stop_name"]
            boarding = s["passengers_boarding"]
            load = s["bus_load"]
            print(f"   {t_str} | {name:<42} (+{boarding} boarding, {load} on bus)")

if __name__ == "__main__":
    run_test()
