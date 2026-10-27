"""Seed and index verified physical stop coordinates into data/seed/stops_seed.json."""

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.config import config
from backend.app.utils.geo import is_within_pune_geofence
from backend.app.core.logging import logger

def seed_stops():
    master_file = config.MASTER_STOPS_FILE
    frontend_coords = ROOT_DIR / "frontend" / "src" / "utils" / "master_coords.json"
    
    loaded = {}
    if frontend_coords.exists():
        with open(frontend_coords, "r", encoding="utf-8") as f:
            data = json.load(f)
        for name, coords in data.items():
            if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                lat, lon = float(coords[0]), float(coords[1])
                if is_within_pune_geofence(lat, lon):
                    loaded[name] = [lat, lon]

    master_file.parent.mkdir(parents=True, exist_ok=True)
    with open(master_file, "w", encoding="utf-8") as f:
        json.dump(loaded, f, indent=2)

    logger.info(f"Successfully seeded {len(loaded)} verified stops into {master_file}")

if __name__ == "__main__":
    seed_stops()
