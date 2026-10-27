"""Agentic Geocoder with Spatial Cluster Coherence.

Uses Gemini 2.5 Flash + Geoapify Search API to:
1. Fetch geocode candidates for unverified stop names in parallel.
2. Calculate spatial proximity of each candidate to the existing employee cluster / corridor.
3. Evaluate candidates with Gemini 2.5 Flash using factory transit rules + cluster proximity scoring.
4. Auto-update `frontend/src/utils/master_coords.json` for permanent zero-latency caching.
"""

import concurrent.futures
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

import config
from utils import haversine_km, normalize_key, standardize_pickup

LOGGER = logging.getLogger(__name__)

MASTER_COORDS_PATH = config.PROJECT_ROOT / "frontend" / "src" / "utils" / "master_coords.json"
DEFAULT_DESTINATION = (18.7766, 73.6947)  # Talegaon Plant

AGENT_SYSTEM_PROMPT = """You are an expert transportation GIS supervisor for an industrial manufacturing plant in Talegaon, Pune (lat: 18.7766, lon: 73.6947).
Your job is to determine the exact commuter pickup stop GPS coordinates for factory workers living in Pune, PCMC, and Maval.

Evaluation Rules:
1. **COMMUTER TRANSIT CORRIDOR PRIORITY**:
   - Matches must lie within Pune / PCMC / Maval commuter transit corridors (Talegaon, Dehu Road, Nigdi, Akurdi, Chinchwad, Pimpri, Bhosari, Moshi, Chakan, Punawale, Tathawade, Ravet, Vadgaon Maval, Kamshet, Bebedohal, Navlakh Umbre).
   - REJECT tourist resorts, rural lakes, dam reservoirs, mountains, or farmland outside normal commuter transit (e.g. for "Pawana nagar", select the residential area in Chinchwad/Nigdi, NOT the Pawna dam reservoir campsite 35 km away).

2. **DISTANCE & BOUNDS CHECK**:
   - Coordinates must fall inside the Pune region (Lat: 18.3 to 19.1, Lon: 73.3 to 74.2).

3. **SPATIAL CLUSTER COHERENCE (PROXIMITY TO OTHER EMPLOYEES)**:
   - Each candidate includes `min_dist_to_cluster_km` indicating its distance to the nearest existing employee pickup point.
   - For ambiguous stop names that exist in multiple places across Maharashtra (e.g. Ambegaon, Pawana Nagar, MIDC, Vadgaon):
     FAVOR candidates that are close to the employee cluster (min_dist_to_cluster_km <= 8 km).
     STRICTLY REJECT distant rural/isolated outliers (>35 km away from other employees).

4. **CANDIDATE SELECTION & LANDMARK FALLBACK**:
   - If Geoapify candidates match the stop within Pune, select the best candidate closest to the transit cluster.
   - If Geoapify candidates are missing, inaccurate, or outside Pune/Maharashtra, use your verified geographic knowledge of Pune / PCMC / Maval / Talegaon landmarks to provide the true Lat/Lon coordinates (e.g. Bebadohal/Bebedohal -> 18.7303, 73.6552; Kohinoor Sapphire Tathwade -> 18.6185, 73.7543; Infinity Cube Punawale -> 18.6312, 73.7481; The Address Society Moshi -> 18.6780, 73.8450; Navlakh Umbre -> 18.7650, 73.6450; Moshi Toll Naka -> 18.6950, 73.8600; Talawade Chowk -> 18.6910, 73.7920; Rupeenagar Nigdi -> 18.6720, 73.8050; Vadgaon Bus Stand -> 18.7430, 73.6450; Chakan -> 18.7560, 73.8580; Amardeep Colony -> 18.7320, 73.6820; Wadgaon MIDC -> 18.7450, 73.6500; Ambegaon Budruk/Gada Chowk -> 18.4565, 73.8380).
   - Set status to "VERIFIED" whenever valid Pune coordinates are determined.

CRITICAL OUTPUT FORMAT:
Respond ONLY with a valid JSON object mapping each stop_name to its decision. Do NOT include markdown code fences, backticks, or conversational text.
Example:
{
  "RAW_STOP_NAME": {
    "status": "VERIFIED",
    "canonical_name": "Standard Clean Name, Area, Pune",
    "latitude": 18.12345,
    "longitude": 73.12345,
    "reasoning": "Selected closest candidate within 2.1 km of employee corridor"
  }
}
"""


def load_master_coords() -> Dict[str, List[float]]:
    """Loads current master coordinates dictionary."""
    if MASTER_COORDS_PATH.exists():
        try:
            return json.loads(MASTER_COORDS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            LOGGER.warning("Could not load master coords %s: %s", MASTER_COORDS_PATH, e)
    return {}


def save_master_coords(coords: Dict[str, List[float]]) -> None:
    """Saves updated coordinates to master_coords.json."""
    try:
        MASTER_COORDS_PATH.write_text(
            json.dumps(coords, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        LOGGER.info("Updated %s with verified coordinates.", MASTER_COORDS_PATH)
    except Exception as e:
        LOGGER.warning("Could not save master coords: %s", e)


def compute_candidate_proximity(
    candidate: Dict[str, Any],
    known_coords: Optional[List[Tuple[float, float]]] = None,
    dest_coord: Tuple[float, float] = DEFAULT_DESTINATION,
) -> Dict[str, Any]:
    """Attaches distance to plant and proximity to existing employee cluster to candidate."""
    lat = candidate.get("lat")
    lon = candidate.get("lon")
    if lat is None or lon is None:
        return candidate

    plant_dist = haversine_km(lat, lon, dest_coord[0], dest_coord[1])
    candidate["dist_to_plant_km"] = round(plant_dist, 1)

    if known_coords and len(known_coords) > 0:
        min_cluster_dist = min(haversine_km(lat, lon, k[0], k[1]) for k in known_coords)
        candidate["min_dist_to_cluster_km"] = round(min_cluster_dist, 1)

        if min_cluster_dist <= 3.0:
            candidate["cluster_fit"] = "EXCELLENT_LOCAL_FIT"
        elif min_cluster_dist <= 8.0:
            candidate["cluster_fit"] = "GOOD_CORRIDOR_FIT"
        elif min_cluster_dist <= 25.0:
            candidate["cluster_fit"] = "MODERATE_DISTANCE"
        else:
            candidate["cluster_fit"] = "ISOLATED_OUTLIER"
    else:
        candidate["min_dist_to_cluster_km"] = None
        candidate["cluster_fit"] = "UNKNOWN"

    return candidate


def fetch_geoapify_candidates(
    query_text: str,
    limit: int = 5,
    known_coords: Optional[List[Tuple[float, float]]] = None,
    dest_coord: Tuple[float, float] = DEFAULT_DESTINATION,
) -> List[Dict[str, Any]]:
    """Fetches candidates from Geoapify Search API with cluster proximity scoring."""
    api_key = config.GEOAPIFY_API_KEY
    if not api_key:
        return []

    # Clean and add Pune context
    search_query = f"{query_text}, Pune district, Maharashtra, India"
    params = {
        "text": search_query,
        "filter": f"countrycode:{config.GEOAPIFY_COUNTRY_CODE}",
        "bias": f"proximity:{config.PUNE_CENTRE[1]},{config.PUNE_CENTRE[0]}",
        "limit": limit,
        "format": "json",
        "apiKey": api_key,
    }

    try:
        resp = requests.get(config.GEOAPIFY_URL, params=params, timeout=10)
        data = resp.json()
        candidates = []
        for r in data.get("results", []):
            lat, lon = r.get("lat"), r.get("lon")
            if lat is not None and lon is not None:
                cand = {
                    "formatted": r.get("formatted", ""),
                    "lat": round(float(lat), 6),
                    "lon": round(float(lon), 6),
                    "result_type": r.get("result_type", ""),
                    "confidence": (r.get("rank") or {}).get("confidence", 0.0),
                }
                cand = compute_candidate_proximity(cand, known_coords, dest_coord)
                candidates.append(cand)

        # Sort candidates: prioritize those closest to the employee cluster, then highest confidence
        if known_coords:
            candidates.sort(
                key=lambda c: (
                    c.get("min_dist_to_cluster_km") if c.get("min_dist_to_cluster_km") is not None else 999.0,
                    -c.get("confidence", 0.0)
                )
            )
        else:
            candidates.sort(key=lambda c: -c.get("confidence", 0.0))

        return candidates
    except Exception as e:
        LOGGER.warning("Geoapify candidate lookup failed for '%s': %s", query_text, e)
        return []


def agentic_verify_stops(
    raw_stops: List[str],
    known_coords: Optional[List[Tuple[float, float]]] = None,
    dest_coord: Tuple[float, float] = DEFAULT_DESTINATION,
) -> Dict[str, Dict[str, Any]]:
    """
    Agentic Geocoding Pipeline with Spatial Cluster Coherence:
    1. Check master_coords first.
    2. For unknown stops, fetch candidate locations via Geoapify in parallel with proximity scoring.
    3. Pass candidates to Gemini 2.5 Flash with factory transit rules and cluster proximity scores.
    4. Auto-update master_coords.json with newly verified stops.
    """
    master = load_master_coords()
    results: Dict[str, Dict[str, Any]] = {}
    stops_to_investigate = []

    # If known_coords not passed, populate from master_coords
    if not known_coords:
        known_coords = [
            (float(v[0]), float(v[1]))
            for v in master.values()
            if isinstance(v, (list, tuple)) and len(v) == 2 and v[0] and v[1]
        ]

    # Phase 1: Fast local master check
    for raw in dict.fromkeys(raw_stops):
        cleaned_raw = str(raw).strip()
        if not cleaned_raw:
            continue

        std_key = normalize_key(standardize_pickup(cleaned_raw))
        if std_key in master and master[std_key]:
            c = master[std_key]
            results[cleaned_raw] = {
                "status": "VERIFIED",
                "canonical_name": cleaned_raw,
                "latitude": float(c[0]),
                "longitude": float(c[1]),
                "source": "master_coords_cache",
                "reasoning": "Existing verified coordinate in master"
            }
        else:
            stops_to_investigate.append(cleaned_raw)

    if not stops_to_investigate:
        return results

    # Phase 2: Gather Geoapify candidates in parallel with cluster proximity scoring
    payload_for_llm = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_to_stop = {
            executor.submit(fetch_geoapify_candidates, s, 5, known_coords, dest_coord): s
            for s in stops_to_investigate
        }
        for future in concurrent.futures.as_completed(future_to_stop):
            s = future_to_stop[future]
            try:
                candidates = future.result()
            except Exception:
                candidates = []
            payload_for_llm.append({
                "stop_name": s,
                "candidates": candidates
            })

    # Phase 3: Send to Gemini for intelligent supervisor verification
    if config.GEMINI_API_KEY:
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=FutureWarning)
                import google.generativeai as genai

            genai.configure(api_key=config.GEMINI_API_KEY)
            model = genai.GenerativeModel(config.GEMINI_MODEL or "gemini-2.5-flash")

            prompt = (
                f"{AGENT_SYSTEM_PROMPT}\n\n"
                f"Evaluate the following pickup stops and their map candidates:\n"
                f"{json.dumps(payload_for_llm, ensure_ascii=False, indent=2)}"
            )

            response = model.generate_content(prompt)
            text = (response.text or "").strip()

            # Robust JSON extraction using regex
            match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
            clean_json_str = match.group(0) if match else text

            raw_evaluations = json.loads(clean_json_str)
            llm_evaluations = {}
            if isinstance(raw_evaluations, list):
                for item in raw_evaluations:
                    if isinstance(item, dict) and "stop_name" in item:
                        llm_evaluations[item["stop_name"]] = item
            elif isinstance(raw_evaluations, dict):
                llm_evaluations = raw_evaluations

            master_updated = False
            for stop_name, eval_data in llm_evaluations.items():
                if not isinstance(eval_data, dict):
                    continue
                if eval_data.get("status") == "VERIFIED" and eval_data.get("latitude") and eval_data.get("longitude"):
                    lat = float(eval_data["latitude"])
                    lon = float(eval_data["longitude"])
                    if 18.3 <= lat <= 19.1 and 73.3 <= lon <= 74.2:
                        canonical = eval_data.get("canonical_name", stop_name)

                        results[stop_name] = {
                            "status": "VERIFIED",
                            "canonical_name": canonical,
                            "latitude": lat,
                            "longitude": lon,
                            "source": "gemini_agentic_verified",
                            "reasoning": eval_data.get("reasoning", "")
                        }

                        # Auto-learn: save to master_coords.json
                        key = normalize_key(standardize_pickup(stop_name))
                        master[key] = [round(lat, 6), round(lon, 6)]
                        master_updated = True
                else:
                    results[stop_name] = {
                        "status": "UNVERIFIED",
                        "canonical_name": stop_name,
                        "latitude": None,
                        "longitude": None,
                        "reasoning": eval_data.get("reasoning", "No valid candidate satisfied transit rules")
                    }

            if master_updated:
                save_master_coords(master)

        except Exception as e:
            LOGGER.warning("Agentic verification call failed: %s", e)

    # Any remaining unverified stops
    for s in stops_to_investigate:
        if s not in results:
            results[s] = {
                "status": "UNVERIFIED",
                "canonical_name": s,
                "latitude": None,
                "longitude": None,
                "reasoning": "Could not be verified by agentic supervisor"
            }

    return results
