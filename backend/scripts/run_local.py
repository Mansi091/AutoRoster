"""CLI Runner for testing local end-to-end route optimization."""

import sys
from pathlib import Path

# Add backend directory to path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from backend.app.services.plan_service import plan_service
from backend.app.etl.extract.excel_reader import read_roster_excel
from backend.app.core.config import config
from backend.app.core.logging import logger

def main():
    sample_path = config.DATA_DIR / "input" / "MAIN_SHEET_TWO_RAW_DATA.xlsx"
    if not sample_path.exists():
        logger.error(f"Sample file not found at {sample_path}")
        return

    logger.info("Executing local transit optimization...")
    raw = read_roster_excel(sample_path)
    result = plan_service.plan_shift(raw_employees=raw)

    routes = result.get("routes", [])
    logger.info(f"Total Routes Generated: {len(routes)}")

    for r in routes:
        print(
            f"[{r['bus_id']}] {r['shift']} - {r['corridor']} | "
            f"Pax: {r['passengers_assigned']}/{r['capacity']} | "
            f"Stops: {r['total_stops']} | "
            f"Km: {r['total_km']} | "
            f"Time: {r['total_minutes']} min | "
            f"Status: {r['ride_time_status']}"
        )

if __name__ == "__main__":
    main()
