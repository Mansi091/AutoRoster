"""LocationIQ forward geocoding with a persistent local response cache."""

import json
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from ...core.config import config
from ...core.logging import logger


class LocationIQClient:
    def __init__(self, api_key: Optional[str] = None, cache_file: Optional[Path] = None):
        self.api_key = api_key or config.LOCATIONIQ_API_KEY
        self.cache_file = cache_file or (config.CACHE_DIR / "locationiq_cache.json")
        self._cache_lock = threading.RLock()
        self._cache: Dict[str, Any] = self._load_cache()

    def _load_cache(self) -> Dict[str, Any]:
        if self.cache_file.exists():
            try:
                with self.cache_file.open("r", encoding="utf-8") as cache:
                    return json.load(cache)
            except Exception as exc:
                logger.warning("Could not load LocationIQ cache: %s", exc)
        return {}

    def _save_cache(self) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_file.open("w", encoding="utf-8") as cache:
                json.dump(self._cache, cache, indent=2)
        except Exception as exc:
            logger.warning("Could not save LocationIQ cache: %s", exc)

    def geocode(self, query: str, proximity: Optional[Tuple[float, float]] = None) -> List[Dict[str, Any]]:
        """Search within the configured service area around the selected facility."""
        query_clean = str(query or "").strip()
        # Never bias an address search to a sample or previously configured
        # facility. The caller must provide the supervisor-confirmed location.
        if not query_clean or not self.api_key or proximity is None:
            return []

        bias_point = proximity
        cache_key = f"https://us1.locationiq.com/v1/search|{query_clean.lower()}|{bias_point[0]:.6f},{bias_point[1]:.6f}"
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        params = {
            "key": self.api_key,
            "q": query_clean,
            "format": "json",
            "limit": 5,
            "countrycodes": "in",
            "normalizecity": 1,
        }
        radius_km = config.FACILITY.max_service_radius_km
        lat_delta = radius_km / 111.0
        lon_delta = radius_km / max(1.0, 111.0 * abs(math.cos(math.radians(bias_point[0]))))
        params["viewbox"] = (
            f"{bias_point[1] - lon_delta},{bias_point[0] + lat_delta},"
            f"{bias_point[1] + lon_delta},{bias_point[0] - lat_delta}"
        )
        params["bounded"] = 1

        try:
            response = requests.get("https://us1.locationiq.com/v1/search", params=params, timeout=8)
            if response.status_code != 200:
                logger.warning("LocationIQ search returned HTTP %s", response.status_code)
                return []

            payload = response.json()
            if not isinstance(payload, list):
                logger.warning("LocationIQ returned an unexpected search response")
                return []
            candidates = []
            for item in payload:
                try:
                    candidates.append({
                        "name": item.get("name") or item.get("display_name"),
                        "address": item.get("display_name", ""),
                        "lat": float(item["lat"]),
                        "lon": float(item["lon"]),
                        "importance": float(item.get("importance") or 0.0),
                        "result_type": item.get("type"),
                        "category": item.get("class"),
                    })
                except (KeyError, TypeError, ValueError):
                    continue

            if candidates:
                with self._cache_lock:
                    self._cache[cache_key] = candidates
                    self._save_cache()
            return candidates
        except Exception as exc:
            logger.warning("LocationIQ geocoding failed for pickup query: %s", exc)
            return []


locationiq_client = LocationIQClient()
