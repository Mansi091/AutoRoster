"""High-accuracy coordinate resolution pipeline."""

import difflib
import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Any, Tuple
from ...db.repositories.stops_repo import stops_repo
from ...etl.extract.geoapify_client import geoapify_client
from ...etl.extract.locationiq_client import locationiq_client
from ...etl.transform.standardize_pickups import standardize_pickup_name
from ...etl.transform.text_cleaner_llm import text_cleaner_llm
from ...etl.transform.candidate_scoring import evaluate_coordinate_candidate
from ...core.config import config
from ...core.logging import logger

def resolve_pickup_coordinates(
    pickup_names: List[str],
    facility: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Use the same master-first, dual-provider resolver as Excel planning."""
    return resolve_roster_pickup_coordinates(pickup_names, facility)


def _candidate_relevance(query: str, candidate: Dict[str, Any]) -> float:
    """Estimate whether a provider result refers to the requested pickup label."""
    def compact(text: str) -> str:
        text = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
        text = re.sub(r"\b(chawk|chouk|chowck)\b", "chowk", text)
        text = re.sub(r"\b(phatta|fata)\b", "phata", text)
        return text

    query_key = compact(query)
    candidate_name = compact(candidate.get("name", ""))
    candidate_text = compact(f"{candidate_name} {candidate.get('address', '')}")
    if not query_key or not candidate_text:
        return 0.0
    query_tokens = set(query_key.split())
    candidate_tokens = set(candidate_text.split())
    token_coverage = len(query_tokens & candidate_tokens) / max(1, len(query_tokens))
    name_similarity = difflib.SequenceMatcher(None, query_key, candidate_name).ratio()
    text_similarity = difflib.SequenceMatcher(None, query_key, candidate_text).ratio()
    return max(token_coverage, name_similarity, text_similarity)


def _best_candidate(
    query: str,
    candidates: List[Dict[str, Any]],
    provider: str,
    proximity: Optional[Tuple[float, float]] = None,
) -> Optional[Dict[str, Any]]:
    valid = []
    if proximity is None:
        return None
    for item in candidates:
        try:
            lat, lon = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        # Keep provider matches within the service radius of this roster's
        # confirmed destination, rather than accepting matches near a baked-in site.
        dlat = math.radians(lat - proximity[0])
        dlon = math.radians(lon - proximity[1])
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(proximity[0])) * math.cos(math.radians(lat)) * math.sin(dlon / 2) ** 2
        distance_km = 6371.0 * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))
        if distance_km > config.FACILITY.max_service_radius_km:
            continue
        relevance = _candidate_relevance(query, item)
        if provider == "Geoapify":
            confidence = float(item.get("confidence") or 0.0)
            score = 0.7 * confidence + 0.3 * relevance
        else:
            importance = float(item.get("importance") or 0.0)
            score = 0.8 * relevance + 0.2 * importance
        valid.append({**item, "lat": lat, "lon": lon, "relevance": relevance, "score": score, "provider": provider})
    return max(valid, key=lambda item: item["score"], default=None)


def resolve_roster_pickup_coordinates(
    pickup_names: List[str],
    facility: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Resolve unrecognized roster stops through Geoapify and LocationIQ.

    A new provider result is auto-accepted only when Geoapify and LocationIQ
    both return a relevant candidate within a short distance of one another.
    A single-provider result stays in review rather than being treated as a
    precise pickup point.
    """
    destination = facility or {"lat": None, "lon": None}
    try:
        proximity: Optional[Tuple[float, float]] = (
            float(destination["lat"]), float(destination["lon"])
        )
    except (KeyError, TypeError, ValueError):
        proximity = None

    unique_names = list(dict.fromkeys(str(name).strip() for name in pickup_names if str(name).strip()))

    if proximity is None:
        return {
            name: {
                "latitude": None,
                "longitude": None,
                "location_status": "LOCATION_UNVERIFIED",
                "canonical_name": name,
                "resolution_reason": "Choose and confirm a drop-off facility before resolving pickups.",
                "providers": [],
            }
            for name in unique_names
        }

    # Search the verified master first. Near-exact spelling variants are only
    # accepted if the master match is unambiguous; otherwise use both APIs.
    master_results: Dict[str, Dict[str, Any]] = {}
    unresolved_names = []
    for raw_name in unique_names:
        coords = stops_repo.get_conservative_coordinates(raw_name)
        if coords:
            lat, lon = coords
            status, reason = evaluate_coordinate_candidate(lat, lon, facility=facility)
            if status == "VERIFIED":
                master_results[raw_name] = {
                    "latitude": lat,
                    "longitude": lon,
                    "location_status": "VERIFIED",
                    "canonical_name": raw_name,
                    "resolution_reason": "Matched exact verified pickup master entry.",
                    "providers": ["verified master"],
                }
                continue
        unresolved_names.append(raw_name)

    def resolve_one(raw_name: str) -> Tuple[str, Dict[str, Any]]:
        query = raw_name.strip()
        # Keep the roster's real landmark terms intact for both geocoders. A
        # language model is not allowed to silently rewrite a pickup location.
        search_query = query

        geo_candidates = geoapify_client.geocode(search_query, proximity=proximity)
        best_geo = _best_candidate(raw_name, geo_candidates, "Geoapify", proximity)

        iq_candidates = locationiq_client.geocode(search_query, proximity=proximity)
        best_iq = _best_candidate(raw_name, iq_candidates, "LocationIQ", proximity)

        accepted = None
        if best_geo and best_iq:
            broad_geo = str(best_geo.get("result_type") or "").lower() in {
                "city", "state", "county", "district", "postcode", "country", "locality", "administrative"
            }
            broad_iq = (
                str(best_iq.get("category") or "").lower() in {"boundary", "place"}
                and str(best_iq.get("result_type") or "").lower()
                in {"city", "town", "village", "suburb", "county", "state", "postcode", "administrative"}
            )
            distance_m = math.hypot(
                (best_geo["lat"] - best_iq["lat"]) * 111_000,
                (best_geo["lon"] - best_iq["lon"]) * 111_000 * math.cos(math.radians(best_geo["lat"])),
            )
            if (
                not broad_geo
                and not broad_iq
                and distance_m <= 200
                and best_geo["relevance"] >= 0.55
                and best_iq["relevance"] >= 0.55
            ):
                # Keep Geoapify's point while requiring independent agreement.
                accepted = best_geo

        suggestions = [candidate for candidate in (best_geo, best_iq) if candidate]
        if accepted:
            result = {
                "latitude": accepted["lat"],
                "longitude": accepted["lon"],
                "location_status": "VERIFIED",
                "canonical_name": accepted.get("name") or query,
                "resolution_reason": "Location verified by matching Geoapify and LocationIQ results.",
                "providers": [candidate["provider"] for candidate in suggestions],
            }
            return raw_name, result

        suggestion_text = "; ".join(
            f"{item['provider']}: {item.get('name') or item.get('address') or 'candidate'}"
            for item in suggestions
        )
        reason = "Geocoding results were missing, broad, or did not agree; confirm the pickup location."
        if not geoapify_client.api_key or not locationiq_client.api_key:
            missing = []
            if not geoapify_client.api_key:
                missing.append("GEOAPIFY_API_KEY")
            if not locationiq_client.api_key:
                missing.append("LOCATIONIQ_API_KEY")
            reason = f"Set {', '.join(missing)} to verify new pickup locations with both providers."
        if suggestion_text:
            reason = f"{reason} Suggestions: {suggestion_text}"
        return raw_name, {
            "latitude": None,
            "longitude": None,
            "location_status": "LOCATION_UNVERIFIED",
            "canonical_name": query,
            "resolution_reason": reason,
            "providers": [candidate["provider"] for candidate in suggestions],
        }

    # Geocode unique stops concurrently to avoid making supervisors wait for a
    # serial request per roster row; results are persisted only after workers end.
    with ThreadPoolExecutor(max_workers=4) as executor:
        resolved = dict(executor.map(resolve_one, unresolved_names))
    resolved = {**master_results, **resolved}

    for raw_name, item in resolved.items():
        if item.get("location_status") == "VERIFIED":
            if not stops_repo.save_stop(raw_name, item["latitude"], item["longitude"]):
                item["location_status"] = "LOCATION_UNVERIFIED"
                item["resolution_reason"] = "Resolved coordinates could not be saved to the verified stop cache."
                item["latitude"] = None
                item["longitude"] = None
                continue
            logger.info("Resolved new roster pickup stop via geocoding providers: %s", raw_name)

    return resolved
