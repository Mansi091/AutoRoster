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
from .. import stop_store

class StopsRepository:
    """Manages verified bus stop coordinates seeded from master stores."""

    def __init__(self, seed_file: Optional[Path] = None):
        self.seed_file = seed_file or config.MASTER_STOPS_FILE
        self._stops: Dict[str, Tuple[float, float]] = {}
        self._normalized_index: Dict[str, Tuple[float, float]] = {}
        self._loaded = False
        self.source = "not loaded"

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_seed()

    def load_seed(self) -> None:
        """Load stop coordinates from the stop memory (Supabase, its local copy, or the seed file)."""
        self._stops = {}
        self._normalized_index = {}
        records, self.source = stop_store.load_stop_records()
        for record in records:
            lat, lon = float(record["lat"]), float(record["lon"])
            if is_within_pune_geofence(lat, lon):
                self._stops[record["alias_text"]] = (lat, lon)
                self._normalized_index[normalize_coordinate_key(record["alias_text"])] = (lat, lon)

        # Also load from data/manual/pickup_point_master.xlsx if present
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

        self._loaded = True
        logger.info(f"Loaded {len(self._stops)} stops ({len(self._normalized_index)} normalized keys) from {self.source}.")

    def get_coordinates(self, stop_name: str) -> Optional[Tuple[float, float]]:
        """Finds coordinates for a stop name using multi-tier automatic matching."""
        self._ensure_loaded()
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
        self._ensure_loaded()
        if not stop_name:
            return None
        return self._normalized_index.get(normalize_coordinate_key(stop_name))

    def get_conservative_coordinates(self, stop_name: str) -> Optional[Tuple[float, float]]:
        """Resolve only an unambiguous, near-exact spelling variant in the master."""
        self._ensure_loaded()
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

    def save_stop(
        self,
        stop_name: str,
        lat: float,
        lon: float,
        *,
        quality: str = "approximate",
        source: str = "",
        changed_by: str = "",
    ) -> bool:
        """Save a stop location to the stop memory.

        quality="confirmed" when a person placed or checked the pin, "approximate"
        for automatic map-search results. With Supabase configured the change is
        written there (with history); otherwise to the local seed file.
        """
        self._ensure_loaded()
        if not is_within_pune_geofence(lat, lon):
            logger.warning(f"Coordinate [{lat}, {lon}] rejected for '{stop_name}' (outside service area).")
            return False

        if stop_store.supabase_settings():
            try:
                outcome = stop_store.save_stop_to_supabase(
                    stop_name, lat, lon, quality=quality, source=source, changed_by=changed_by
                )
            except Exception as e:
                logger.error(f"Error saving stop '{stop_name}' to Supabase: {e}")
                return False
            logger.info(f"Stop '{stop_name}' -> [{lat}, {lon}] {quality}: {outcome['action']} in Supabase")
            self._loaded = False  # re-read: a linked spelling uses its existing stop's point
            return True

        self._stops[stop_name] = (lat, lon)
        self._normalized_index[normalize_coordinate_key(stop_name)] = (lat, lon)
        try:
            with open(self.seed_file, "w", encoding="utf-8") as f:
                json.dump({name: list(coords) for name, coords in self._stops.items()}, f, indent=2)
            stop_store.invalidate()
            logger.info(f"Persisted stop '{stop_name}' -> [{lat}, {lon}] to {self.seed_file}")
            return True
        except Exception as e:
            logger.error(f"Error persisting stop '{stop_name}': {e}")
            return False

    def mark_needs_location(self, stop_name: str, reason: str) -> bool:
        """Remember a stop that could not be placed (Supabase only; no-op otherwise)."""
        if not stop_store.supabase_settings() or not str(stop_name or "").strip():
            return False
        try:
            outcome = stop_store.mark_needs_location(stop_name, reason)
        except Exception as e:
            logger.warning(f"Could not record '{stop_name}' as needing a location: {e}")
            return False
        logger.info(f"Stop '{stop_name}': {outcome['action']}")
        return True

    def list_all(self) -> Dict[str, Tuple[float, float]]:
        self._ensure_loaded()
        return dict(self._stops)

stops_repo = StopsRepository()
