"""Geoapify API extraction client with persistent caching."""

import json
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
import requests
from ...core.config import config
from ...core.logging import logger

class GeoapifyClient:
    def __init__(self, api_key: Optional[str] = None, cache_file: Optional[Path] = None):
        self.api_key = api_key or config.GEOAPIFY_API_KEY
        self.cache_file = cache_file or config.GEOAPIFY_CACHE_FILE
        self._cache_lock = threading.RLock()
        self._cache: Dict[str, Any] = self._load_cache()

    def _load_cache(self) -> Dict[str, Any]:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Error loading Geoapify cache: {e}")
        return {}

    def _save_cache(self) -> None:
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving Geoapify cache: {e}")

    def geocode(self, query: str, proximity: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Search Geoapify inside the configured boundary, biased to the selected site."""
        query_clean = query.strip()
        if not query_clean or proximity is None:
            return []

        bias_point = proximity
        proximity_key = f"{bias_point[0]:.6f},{bias_point[1]:.6f}" if bias_point else "unbiased"
        cache_key = f"https://api.geoapify.com/v1/geocode/search|{query_clean.lower()}|{proximity_key}"
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        if not self.api_key:
            return []

        url = "https://api.geoapify.com/v1/geocode/search"
        params = {
            "text": query_clean,
            "limit": 5,
            "apiKey": self.api_key,
        }
        radius_m = config.FACILITY.max_service_radius_km * 1000
        params["filter"] = f"circle:{bias_point[1]},{bias_point[0]},{radius_m}"
        params["bias"] = f"proximity:{bias_point[1]},{bias_point[0]}"

        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                features = resp.json().get("features", [])
                candidates = []
                for feat in features:
                    props = feat.get("properties", {})
                    candidates.append({
                        "name": props.get("formatted") or props.get("name"),
                        "lat": props.get("lat"),
                        "lon": props.get("lon"),
                        "confidence": props.get("rank", {}).get("confidence", 0.0),
                        "confidence_street_level": props.get("rank", {}).get("confidence_street_level", 0.0),
                        "confidence_building_level": props.get("rank", {}).get("confidence_building_level", 0.0),
                        "match_type": props.get("rank", {}).get("match_type"),
                        "result_type": props.get("result_type"),
                    })
                if candidates:
                    with self._cache_lock:
                        self._cache[cache_key] = candidates
                        self._save_cache()
                return candidates
        except Exception as e:
            logger.warning(f"Geoapify request failed for '{query}': {e}")

        return []

geoapify_client = GeoapifyClient()
