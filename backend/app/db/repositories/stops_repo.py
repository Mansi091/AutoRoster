"""Stops repository: Single source of truth for physical stop coordinates."""

import difflib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
from ...core.config import config
from ...core.logging import logger
from ...utils.text import normalize_coordinate_key
from ...utils.geo import is_within_pune_geofence

class StopsRepository:
    """Manages verified bus stop coordinates seeded from master stores."""

    def __init__(self, seed_file: Optional[Path] = None):
        self.seed_file = seed_file or config.MASTER_STOPS_FILE
        self._stops: Dict[str, Tuple[float, float]] = {}
        self._normalized_index: Dict[str, Tuple[float, float]] = {}
        self.load_seed()

    def load_seed(self) -> None:
        """Loads master stop coordinates from seed JSON and Excel master into memory index."""
        self._stops = {}
        self._normalized_index = {}

        # 1. Load from stops_seed.json
        if self.seed_file.exists():
            try:
                with open(self.seed_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, coords in data.items():
                    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                        lat, lon = float(coords[0]), float(coords[1])
                        if is_within_pune_geofence(lat, lon):
                            self._stops[name] = (lat, lon)
                            self._normalized_index[normalize_coordinate_key(name)] = (lat, lon)
            except Exception as e:
                logger.error(f"Failed to load stops seed: {e}")

        # 2. Also load from data/manual/pickup_point_master.xlsx if present
        manual_master = config.DATA_DIR / "manual" / "pickup_point_master.xlsx"
        if manual_master.exists():
            try:
                df = pd.read_excel(manual_master)
                for _, row in df.iterrows():
                    if pd.notna(row.get("latitude")) and pd.notna(row.get("longitude")):
                        lat, lon = float(row["latitude"]), float(row["longitude"])
                        if is_within_pune_geofence(lat, lon):
                            if pd.notna(row.get("pickup_point")):
                                name = str(row["pickup_point"]).strip()
                                self._stops[name] = (lat, lon)
                                self._normalized_index[normalize_coordinate_key(name)] = (lat, lon)
                            if pd.notna(row.get("pickup_key")):
                                pkey = str(row["pickup_key"]).strip()
                                self._normalized_index[normalize_coordinate_key(pkey)] = (lat, lon)
            except Exception as e:
                logger.warning(f"Could not supplement from pickup_point_master.xlsx: {e}")

        logger.info(f"Loaded {len(self._stops)} verified stops ({len(self._normalized_index)} normalized keys) into master repository.")

    def get_coordinates(self, stop_name: str) -> Optional[Tuple[float, float]]:
        """Finds coordinates for a stop name using multi-tier automatic matching."""
        if not stop_name:
            return None
        norm_key = normalize_coordinate_key(stop_name)

        # 1. Exact match
        if norm_key in self._normalized_index:
            return self._normalized_index[norm_key]

        # 2. Substring / superstring token subset match
        for key, coords in self._normalized_index.items():
            if len(key) >= 5 and (norm_key in key or key in norm_key):
                return coords

        # 3. Landmark token overlap match (shared significant place words)
        GENERIC_WORDS = {"near", "opp", "opposite", "behind", "front", "bus", "stop", "road", "chowk", "phata", "hospital", "corner", "bridge", "line"}
        tokens = set(norm_key.split())
        sig_tokens = tokens - GENERIC_WORDS
        best_match = None
        best_score = 0
        for key, coords in self._normalized_index.items():
            k_tokens = set(key.split())
            common_sig = sig_tokens & (k_tokens - GENERIC_WORDS)
            if len(common_sig) >= 1:
                score = len(common_sig) * 2 + len(tokens & k_tokens)
                if score > best_score:
                    best_score = score
                    best_match = coords
        if best_match and best_score >= 3:
            return best_match

        # 4. Fuzzy matching with high similarity threshold
        matches = difflib.get_close_matches(norm_key, self._normalized_index.keys(), n=1, cutoff=0.75)
        if matches:
            return self._normalized_index[matches[0]]

        return None

    def get_exact_coordinates(self, stop_name: str) -> Optional[Tuple[float, float]]:
        """Return only an exact normalized master match; never fuzzy-guess a site."""
        if not stop_name:
            return None
        return self._normalized_index.get(normalize_coordinate_key(stop_name))

    def get_conservative_coordinates(self, stop_name: str) -> Optional[Tuple[float, float]]:
        """Resolve only an unambiguous, near-exact spelling variant in the master."""
        if not stop_name:
            return None
        canonical_name = stop_name.lower()
        canonical_name = re.sub(r"\b(chawk|chouk|chowck)\b", "chowk", canonical_name)
        canonical_name = re.sub(r"\b(phatta|fata)\b", "phata", canonical_name)
        key = normalize_coordinate_key(canonical_name)
        if key in self._normalized_index:
            return self._normalized_index[key]
        matches = []
        for master_key, coords in self._normalized_index.items():
            ratio = difflib.SequenceMatcher(None, key, master_key).ratio()
            if ratio >= 0.94:
                matches.append((ratio, coords))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        best_score, best_coords = matches[0]
        # Different nearby stops can have similar names. Only accept when the
        # best spelling is clearly stronger, or all near-ties share a point.
        tied_coords = {coords for score, coords in matches if best_score - score < 0.04}
        if len(tied_coords) > 1:
            return None
        return best_coords

    def save_stop(self, stop_name: str, lat: float, lon: float) -> bool:
        """Saves a new verified coordinate to the master store and persists to disk."""
        if not is_within_pune_geofence(lat, lon):
            logger.warning(f"Coordinate [{lat}, {lon}] rejected for '{stop_name}' (outside Pune geofence).")
            return False

        self._stops[stop_name] = (lat, lon)
        norm_key = normalize_coordinate_key(stop_name)
        self._normalized_index[norm_key] = (lat, lon)

        try:
            with open(self.seed_file, "w", encoding="utf-8") as f:
                json.dump(self._stops, f, indent=2)
            logger.info(f"Persisted stop '{stop_name}' -> [{lat}, {lon}] to {self.seed_file}")
            return True
        except Exception as e:
            logger.error(f"Error persisting stop '{stop_name}': {e}")
            return False

    def list_all(self) -> Dict[str, Tuple[float, float]]:
        return dict(self._stops)

stops_repo = StopsRepository()
