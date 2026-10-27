"""OSRM routing and distance matrix extraction client."""

import math
from typing import Dict, List, Tuple, Any, Optional
import requests
from ...core.config import config
from ...core.logging import logger
from ...utils.geo import haversine_km

class OSRMClient:
    def __init__(self):
        self._table_cache: Dict[str, Tuple[List[List[int]], List[List[float]]]] = {}

    def fetch_time_distance_matrix(
        self,
        nodes: List[Dict[str, Any]],
        timeout_sec: int = 15,
    ) -> Tuple[List[List[int]], List[List[float]]]:
        """Fetches N x N road travel time (minutes) and distance (km) matrices.

        Falls back to calibrated Pune road network model if OSRM is unreachable.
        """
        n = len(nodes)
        if n == 0:
            return [], []

        coords_str = ";".join(f"{node['lon']:.5f},{node['lat']:.5f}" for node in nodes)
        if coords_str in self._table_cache:
            return self._table_cache[coords_str]

        url = f"{config.OSRM_TABLE_URL}/{coords_str}?annotations=duration,distance"
        try:
            resp = requests.get(url, timeout=timeout_sec)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and "durations" in data:
                    durations_raw = data["durations"]
                    distances_raw = data.get("distances", [])

                    time_matrix = []
                    dist_matrix = []
                    for i in range(n):
                        t_row = []
                        d_row = []
                        for j in range(n):
                            if i == j:
                                t_row.append(0)
                                d_row.append(0.0)
                            else:
                                sec = durations_raw[i][j]
                                metres = distances_raw[i][j] if distances_raw else None
                                if sec is not None and not math.isnan(sec):
                                    mins = max(1, math.ceil(sec / 60.0))
                                    km = round(metres / 1000.0, 2) if metres else round(mins * 0.5, 2)
                                else:
                                    h_km = haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
                                    km = round(h_km * config.COMMUTE.pune_road_curvature_factor, 2)
                                    mins = max(1, math.ceil((km / config.COMMUTE.transit_speed_kmh) * 60))
                                t_row.append(int(mins))
                                d_row.append(float(km))
                        time_matrix.append(t_row)
                        dist_matrix.append(d_row)

                    self._table_cache[coords_str] = (time_matrix, dist_matrix)
                    return time_matrix, dist_matrix
        except Exception as e:
            logger.warning(f"OSRM Table request failed ({e}); using calibrated Pune transit model.")

        # Calibrated Pune transit model fallback
        time_matrix = []
        dist_matrix = []
        speed = config.COMMUTE.transit_speed_kmh
        factor = config.COMMUTE.pune_road_curvature_factor

        for i in range(n):
            t_row = []
            d_row = []
            for j in range(n):
                if i == j:
                    t_row.append(0)
                    d_row.append(0.0)
                else:
                    straight_km = haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
                    road_km = round(straight_km * factor, 2)
                    mins = max(1, math.ceil((road_km / speed) * 60))
                    t_row.append(int(mins))
                    d_row.append(float(road_km))
            time_matrix.append(t_row)
            dist_matrix.append(d_row)

        return time_matrix, dist_matrix

osrm_client = OSRMClient()
