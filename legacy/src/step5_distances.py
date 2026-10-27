"""STEP 5 - Straight-line (Haversine) distances between approved pickup points.

Input : output/04_pickup_point_master.xlsx  ("Ready for Routing" sheet)
Output: output/05_stop_distances.xlsx
          - Distance Matrix : km between every pair of ready stops
          - Nearest Stops   : the 3 closest other stops for each stop
          - Close Pairs     : pairs closer than NEAR_STOP_THRESHOLD_KM

IMPORTANT: these are straight-line distances, not road distances. Use them to shortlist
which stops could share a bus; the real route still needs checking on a map.
"""
import sys
from pathlib import Path

import pandas as pd

import config
from utils import haversine_km, read_sheet, write_workbook

NOTE = ("Straight-line (Haversine) km. Not the road distance a bus travels - "
        "use only to shortlist nearby stops.")


def main():
    stops = read_sheet(config.OUT_MASTER, "Ready for Routing")
    stops = stops[["pickup_key", "pickup_point", "routable_employees", "latitude", "longitude"]].copy()
    stops["latitude"] = stops["latitude"].astype(float)
    stops["longitude"] = stops["longitude"].astype(float)
    names = stops["pickup_point"].tolist()
    n = len(stops)

    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_km(stops.at[i, "latitude"], stops.at[i, "longitude"],
                             stops.at[j, "latitude"], stops.at[j, "longitude"])
            matrix[i][j] = matrix[j][i] = round(d, 2)

    matrix_df = pd.DataFrame(matrix, index=names, columns=names).reset_index().rename(columns={"index": "pickup_point"})

    nearest_rows, pair_rows = [], []
    for i in range(n):
        dists = sorted(((matrix[i][j], names[j]) for j in range(n) if j != i))
        for rank, (d, other) in enumerate(dists[:3], start=1):
            nearest_rows.append({"pickup_point": names[i], "rank": rank,
                                 "nearest_stop": other, "distance_km": d})
        for d, other in dists:
            if d > config.NEAR_STOP_THRESHOLD_KM:
                break
            if names[i] < other:
                pair_rows.append({"stop_a": names[i], "stop_b": other, "distance_km": d})

    nearest_df = pd.DataFrame(nearest_rows)
    pairs_df = pd.DataFrame(pair_rows, columns=["stop_a", "stop_b", "distance_km"]).sort_values("distance_km")

    write_workbook(config.OUT_DISTANCES, {
        "Distance Matrix": matrix_df,
        "Nearest Stops": nearest_df,
        "Close Pairs": pairs_df,
    }, notes={"Distance Matrix": NOTE, "Nearest Stops": NOTE, "Close Pairs": NOTE})

    print(f"Stops with coordinates : {n}")
    print(f"Close pairs (<= {config.NEAR_STOP_THRESHOLD_KM} km): {len(pairs_df)}")
    print(f"Written                : {config.OUT_DISTANCES}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
