"""Resolve pickup points from the verified master and the existing Geoapify cache."""
import json
import logging
import math
import time
from pathlib import Path

import requests

import config
from llm_cleaner import clean_stops_with_llm
from step3_geocode import geoapify_lookup, load_cache, save_cache
from utils import haversine_km, in_pune_bounds, normalize_key, standardize_pickup

LOGGER = logging.getLogger(__name__)
COORDINATE_MASTER = (
    config.PROJECT_ROOT / "frontend" / "src" / "utils" / "master_coords.json"
)
MIN_GEOCODE_CONFIDENCE = 0.8
MIN_NAME_MATCH_RATIO = 0.6
NON_PICKUP_RESULT_TYPES = {
    "city", "county", "country", "district", "state", "suburb",
}
GENERIC_NAME_TOKENS = {
    "a", "at", "and", "district", "india", "in", "maharashtra", "near",
    "of", "pune", "road", "the", "to",
}


def _load_coordinate_index():
    coordinate_master = json.loads(COORDINATE_MASTER.read_text(encoding="utf-8"))
    index = {}
    for name, coordinates in coordinate_master.items():
        key = normalize_key(name)
        normalized_coordinates = (float(coordinates[0]), float(coordinates[1]))
        if key in index and index[key] != normalized_coordinates:
            index[key] = None
        elif key not in index:
            index[key] = normalized_coordinates
    return index


def _valid_coordinates(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    if not in_pune_bounds(lat, lon):
        return None
    return lat, lon


def _name_tokens(value):
    return {
        token
        for token in normalize_key(value).split()
        if token not in GENERIC_NAME_TOKENS
    }


def _candidate_matches_pickup(pickup_name, formatted_address):
    candidate_tokens = _name_tokens(formatted_address)
    input_tokens = _name_tokens(pickup_name)
    if not input_tokens or not candidate_tokens:
        return False

    clauses = [
        _name_tokens(clause)
        for clause in pickup_name.split(",")
        if _name_tokens(clause)
    ]
    if any(not clause.intersection(candidate_tokens) for clause in clauses):
        return False

    match_ratio = len(input_tokens.intersection(candidate_tokens)) / len(input_tokens)
    return match_ratio >= MIN_NAME_MATCH_RATIO


def _select_geocode_candidate(pickup_name, candidates):
    eligible = []
    for candidate in candidates:
        coordinates = _valid_coordinates(candidate.get("lat"), candidate.get("lon"))
        confidence = candidate.get("confidence")
        formatted = str(candidate.get("formatted") or "").strip()
        result_type = str(candidate.get("result_type") or "").lower()

        # Reject generic regional boundaries (county, city, state, country)
        if result_type in NON_PICKUP_RESULT_TYPES:
            continue

        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5

        # Reject low confidence guesses
        if confidence < 0.35:
            continue

        if coordinates is None:
            continue

        # Check service radius from facility (max 28 km)
        try:
            from aggregator_cvrptw import DEFAULT_FACILITY
        except Exception:
            DEFAULT_FACILITY = {"name": "Facility", "lat": 18.7765871, "lon": 73.6669931}
        dist_from_facility = haversine_km(coordinates[0], coordinates[1], DEFAULT_FACILITY["lat"], DEFAULT_FACILITY["lon"])
        if dist_from_facility > 28.0:
            continue

        if not _candidate_matches_pickup(pickup_name, formatted):
            continue

        eligible.append((confidence, coordinates, formatted))

    if not eligible:
        return None

    # Sort by highest confidence and return the best available match within Pune bounds
    eligible.sort(key=lambda item: item[0], reverse=True)
    return eligible[0]


def _find_master_fallback(normalized_text, coordinate_index):
    """Finds best matching coordinate in master based on token intersection or sub-phrases."""
    tokens = set(t for t in normalized_text.split() if t not in GENERIC_NAME_TOKENS and len(t) > 2)
    if not tokens:
        return None

    best_match = None
    best_overlap = 0

    for master_name, coords in coordinate_index.items():
        if coords is None:
            continue
        master_tokens = set(t for t in master_name.split() if t not in GENERIC_NAME_TOKENS and len(t) > 2)
        overlap = len(tokens.intersection(master_tokens))
        # Require at least 2 distinct matching keywords, or if single token, exact equality
        if overlap >= 2 or (overlap == 1 and len(tokens) == 1 and len(master_tokens) == 1):
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = coords

    return best_match if best_match is not None else None


def _distance_km(point_a, point_b):
    return haversine_km(point_a[0], point_a[1], point_b[0], point_b[1])


def _unverified_result(pickup_point, canonical_name, reason):
    return {
        "pickup_point": pickup_point,
        "canonical_name": canonical_name,
        "location_status": "LOCATION_UNVERIFIED",
        "latitude": None,
        "longitude": None,
        "resolution_reason": reason,
    }


def resolve_pickup_points(pickup_points, allow_agentic: bool = False):
    """Return verified coordinates or an explicit unverified status for each name."""
    coordinate_index = _load_coordinate_index()
    cache = load_cache()
    cache_changed = False
    results = {}
    geocoded_this_request = set()

    unique_points = [str(value).strip() for value in dict.fromkeys(pickup_points) if str(value).strip()]
    unresolved_points = []

    # Phase 1: Fast direct lookup in verified master coordinates
    for pickup_point in unique_points:
        canonical_name = standardize_pickup(pickup_point)
        key = normalize_key(canonical_name)
        coordinates = _valid_coordinates(*(coordinate_index.get(key) or (None, None)))
        if coordinates is not None:
            results[pickup_point] = {
                "pickup_point": pickup_point,
                "canonical_name": canonical_name,
                "location_status": "VERIFIED",
                "latitude": coordinates[0],
                "longitude": coordinates[1],
                "coordinate_source": "coordinate_master",
            }
            continue

        fallback_coords = _find_master_fallback(key, coordinate_index)
        if fallback_coords is not None:
            results[pickup_point] = {
                "pickup_point": pickup_point,
                "canonical_name": canonical_name,
                "location_status": "VERIFIED",
                "latitude": fallback_coords[0],
                "longitude": fallback_coords[1],
                "coordinate_source": "master_anchor_match",
            }
            continue

        unresolved_points.append(pickup_point)

    if not unresolved_points:
        return results

    # Phase 2: Use Gemini LLM to correct misspellings, phonetic slang, and regional terms
    llm_corrections = clean_stops_with_llm(unresolved_points) if config.GEMINI_API_KEY else {}

    # Phase 3: Check master with LLM cleaned name, or query Geoapify with corrected text
    for pickup_point in unresolved_points:
        canonical_name = standardize_pickup(pickup_point)
        cleaned_name = llm_corrections.get(pickup_point, canonical_name)

        # If LLM corrected the name, see if it now matches the master!
        if cleaned_name and cleaned_name.strip() and cleaned_name.lower() != canonical_name.lower():
            llm_key = normalize_key(standardize_pickup(cleaned_name))
            llm_coords = _valid_coordinates(*(coordinate_index.get(llm_key) or (None, None)))
            if llm_coords is None:
                llm_coords = _find_master_fallback(llm_key, coordinate_index)
            if llm_coords is not None:
                results[pickup_point] = {
                    "pickup_point": pickup_point,
                    "canonical_name": cleaned_name,
                    "location_status": "VERIFIED",
                    "latitude": llm_coords[0],
                    "longitude": llm_coords[1],
                    "coordinate_source": "master_llm_resolved",
                }
                continue

        # If not in master, query Geoapify with the cleaned name
        search_query_base = cleaned_name if (cleaned_name and cleaned_name.strip()) else canonical_name
        query = search_query_base + config.GEOAPIFY_QUERY_SUFFIX

        if query not in cache:
            if not config.GEOAPIFY_API_KEY:
                results[pickup_point] = _unverified_result(
                    pickup_point,
                    search_query_base,
                    "No verified master coordinate or cached geocode; Geoapify API key is not configured.",
                )
                continue
            try:
                cache[query] = {
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "candidates": geoapify_lookup(query),
                }
                geocoded_this_request.add(query)
                cache_changed = True
            except (requests.exceptions.RequestException, RuntimeError) as error:
                LOGGER.warning("Geocoding failed for pickup point %r: %s", pickup_point, error)
                results[pickup_point] = _unverified_result(
                    pickup_point,
                    search_query_base,
                    f"Geocoding request failed: {error}",
                )
                continue

        chosen = _select_geocode_candidate(
            search_query_base,
            cache[query].get("candidates", []),
        )
        if chosen is None:
            if allow_agentic:
                try:
                    from agentic_geocoder import agentic_verify_stops
                    agent_res = agentic_verify_stops([pickup_point]).get(pickup_point, {})
                    if agent_res.get("status") == "VERIFIED" and agent_res.get("latitude") and agent_res.get("longitude"):
                        results[pickup_point] = {
                            "pickup_point": pickup_point,
                            "canonical_name": agent_res.get("canonical_name", search_query_base),
                            "location_status": "VERIFIED",
                            "latitude": agent_res["latitude"],
                            "longitude": agent_res["longitude"],
                            "coordinate_source": "gemini_agentic_verified",
                            "formatted_address": agent_res.get("canonical_name", ""),
                            "confidence": 0.95,
                        }
                        continue
                except Exception as e:
                    LOGGER.warning("Agentic fallback failed for %r: %s", pickup_point, e)

            results[pickup_point] = _unverified_result(
                pickup_point,
                search_query_base,
                "No unique, high-confidence geocode matched the pickup name and Pune service area.",
            )
            continue

        confidence, coordinates, formatted = chosen
        results[pickup_point] = {
            "pickup_point": pickup_point,
            "canonical_name": search_query_base,
            "location_status": "VERIFIED",
            "latitude": coordinates[0],
            "longitude": coordinates[1],
            "coordinate_source": (
                "Geoapify" if query in geocoded_this_request else "Geoapify cache"
            ),
            "formatted_address": formatted,
            "confidence": confidence,
        }

    if cache_changed:
        save_cache(cache)
    return results
