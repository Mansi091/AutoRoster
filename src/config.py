"""Central configuration for the bus routing pipeline.

Change values here (or set environment variables) instead of editing the step scripts.
"""
import os
from pathlib import Path

# Load .env from the project root so GEOAPIFY_API_KEY etc. are picked up
# automatically. Real environment variables always win.
try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional - fall back to plain os.environ
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if load_dotenv:
    load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "input"
REFERENCE_DIR = DATA_DIR / "reference"
MANUAL_DIR = DATA_DIR / "manual"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = PROJECT_ROOT / "output"

# ---- Input -------------------------------------------------------------------
RAW_FILE = INPUT_DIR / "MAIN_SHEET_TWO_RAW_DATA.xlsx"
RAW_HEADER_ROW = 2          # Excel row (1-based) that holds the column headers
RAW_FIRST_DATA_ROW = 3      # first Excel row with employee data

# ---- Reference / manual files (human-maintained) -----------------------------
ALIASES_FILE = REFERENCE_DIR / "pickup_aliases.csv"                 # merge spelling variants of the same stop
WORD_CORRECTIONS_FILE = REFERENCE_DIR / "pickup_word_corrections.csv"  # typo fixes applied word by word
PICKUP_MASTER_FILE = MANUAL_DIR / "pickup_point_master.xlsx"        # supervisor edits this file

# ---- Cache -------------------------------------------------------------------
GEOCODE_CACHE_FILE = CACHE_DIR / "geoapify_cache.json"

# ---- Output file names -------------------------------------------------------
OUT_CLEANED = OUTPUT_DIR / "01_cleaned_employees.xlsx"
OUT_PICKUPS = OUTPUT_DIR / "02_unique_pickup_points.xlsx"
OUT_CANDIDATES = OUTPUT_DIR / "03_geocode_candidates.xlsx"
OUT_MASTER = OUTPUT_DIR / "04_pickup_point_master.xlsx"
OUT_DISTANCES = OUTPUT_DIR / "05_stop_distances.xlsx"
OUT_GROUPING = OUTPUT_DIR / "06_grouping_result.xlsx"
OUT_FINAL = OUTPUT_DIR / "07_final_bus_routing_workbook.xlsx"

# ---- Routing rules -----------------------------------------------------------
BUS_CAPACITY = 32                 # seats per bus
MIN_EMPLOYEES_PER_BUS = 15        # aim for at least this many per bus
MAX_CLUSTER_RADIUS_KM = 5.0       # stops further apart than this are not put on the same bus
NEAR_STOP_THRESHOLD_KM = 3.0      # used for the "close stops" list
DUPLICATE_NAME_SIMILARITY = 0.85  # fuzzy score above which two stop names are flagged as possible duplicates

# ---- Geography (Pune district, India) ----------------------------------------
PUNE_CENTRE = (18.5204, 73.8567)  # (lat, lon) used as search bias
PUNE_BOUNDS = {"min_lat": 17.9, "max_lat": 19.2, "min_lon": 73.1, "max_lon": 74.6}

# ---- Geoapify ----------------------------------------------------------------
GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY", "")
GEOAPIFY_URL = "https://api.geoapify.com/v1/geocode/search"
GEOAPIFY_COUNTRY_CODE = "in"
GEOAPIFY_RESULT_LIMIT = 5
GEOAPIFY_QUERY_SUFFIX = ", Pune district, Maharashtra, India"
GEOAPIFY_TIMEOUT_SECONDS = 30

# ---- Gemini LLM (Spelling & Transliteration Cleaner) -------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
LLM_CACHE_FILE = CACHE_DIR / "llm_corrections_cache.json"


# ---- Allowed values ----------------------------------------------------------

VALID_SHIFTS = ("A", "B", "C")
MASTER_STATUSES = ("Needs review", "Approved", "Rejected", "Not found")
