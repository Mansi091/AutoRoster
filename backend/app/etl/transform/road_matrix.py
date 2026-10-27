"""Road matrix builder for routing nodes."""

from typing import List, Dict, Tuple, Any
from ...etl.extract.osrm_client import osrm_client

def build_corridor_road_matrix(
    depot_node: Dict[str, Any],
    pickup_nodes: List[Dict[str, Any]],
    destination_node: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[List[int]], List[List[float]]]:
    """Assembles all nodes [Depot, Stops..., Destination] and computes road travel matrix.

    Virtual open start: Zeroes out travel time from depot (Node 0) to all pickup stops,
    allowing the solver to select the natural earliest pickup origin.
    """
    all_nodes = [depot_node, *pickup_nodes, destination_node]
    time_matrix, dist_matrix = osrm_client.fetch_time_distance_matrix(all_nodes)

    # Virtual open start: zero time from depot to all nodes
    for j in range(len(all_nodes)):
        time_matrix[0][j] = 0

    return all_nodes, time_matrix, dist_matrix
