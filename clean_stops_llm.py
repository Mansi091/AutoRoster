"""CLI tool to clean and standardize employee pickup stops using Gemini Flash LLM.

Usage:
  # 1. Test directly with a list of stop names:
  py clean_stops_llm.py --stops "SHASTRI SHOWK, Wadgav khandoba mandir, punawale katewasti, marbal"

  # 2. Clean stops directly from an Excel sheet:
  py clean_stops_llm.py --file data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx --column "Pickup point"
"""
import argparse
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pandas as pd
from llm_cleaner import clean_stops_with_llm
import config

def main():
    parser = argparse.ArgumentParser(description="Clean bus pickup stop names using Gemini Flash.")
    parser.add_argument("--stops", type=str, help="Comma-separated raw pickup stop names")
    parser.add_argument("--file", type=str, help="Path to Excel or CSV file")
    parser.add_argument("--column", type=str, default="Pickup point", help="Column name containing stop names")
    parser.add_argument("--agentic", action="store_true", help="Run full Agentic Geocoding: Geoapify lookup + Gemini supervisor verification + auto-save to master_coords.json")
    args = parser.parse_args()

    if not config.GEMINI_API_KEY:
        print("[ERROR] GEMINI_API_KEY is missing from .env! Please set GEMINI_API_KEY in your .env file.")
        sys.exit(1)

    stops_to_clean = []

    if args.stops:
        stops_to_clean = [s.strip() for s in args.stops.split(",") if s.strip()]
    elif args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"[ERROR] File not found: {file_path}")
            sys.exit(1)
        if file_path.suffix.lower() in [".xlsx", ".xls"]:
            df = pd.read_excel(file_path)
        else:
            df = pd.read_csv(file_path)
        
        # Look for matching column
        matching_col = None
        for col in df.columns:
            if col.strip().lower() == args.column.strip().lower():
                matching_col = col
                break
        if not matching_col:
            for col in df.columns:
                if "pickup" in str(col).lower() or "stop" in str(col).lower():
                    matching_col = col
                    break
        if not matching_col:
            matching_col = df.columns[0]
            print(f"[INFO] Using column '{matching_col}'")

        raw_unique = df[matching_col].dropna().astype(str).unique().tolist()
        stops_to_clean = [s.strip() for s in raw_unique if s.strip() and s.strip().lower() != "nan"]
    else:
        # Default interactive / demo stops
        stops_to_clean = [
            "SHASTRI SHOWK",
            "Wadgav, khandoba mandir",
            "punawale katewasti",
            "marbal chowk",
            "nashik fata",
            "dange chwk thergaon",
            "bhosri gaon"
        ]

    if args.agentic:
        from agentic_geocoder import agentic_verify_stops
        print(f"\n=======================================================")
        print(f" Agentic Geocoder Supervisor (Gemini + Geoapify)")
        print(f"=======================================================")
        print(f"Total unique stops to verify: {len(stops_to_clean)}\n")

        agent_results = agentic_verify_stops(stops_to_clean)

        print(f"{'RAW STOP':<28} {'STATUS':<10} {'LATITUDE':<10} {'LONGITUDE':<10} {'CANONICAL NAME'}")
        print(f"{'-'*28} {'-'*10} {'-'*10} {'-'*10} {'-'*30}")
        for raw in stops_to_clean:
            res = agent_results.get(raw, {})
            st = res.get("status", "UNVERIFIED")
            lat = str(res.get("latitude") or "-")
            lon = str(res.get("longitude") or "-")
            canon = res.get("canonical_name", raw)
            print(f"{raw:<28} {st:<10} {lat:<10} {lon:<10} {canon}")

        print(f"\n[OK] Newly verified stops have been saved to master_coords.json!")
    else:
        print(f"\n=======================================================")
        print(f" Gemini Stop Cleaner (Model: {config.GEMINI_MODEL})")
        print(f"=======================================================")
        print(f"Total unique stops to process: {len(stops_to_clean)}\n")

        cleaned_map = clean_stops_with_llm(stops_to_clean)

        print(f"{'RAW INPUT STOP':<35} -> {'STANDARDIZED LANDMARK'}")
        print(f"{'-'*35}    {'-'*40}")
        for raw in stops_to_clean:
            clean = cleaned_map.get(raw, raw)
            diff = " [CHANGED]" if raw.strip().lower() != clean.strip().lower() else ""
            print(f"{raw:<35} -> {clean}{diff}")

        print(f"\n[OK] Cleaned stops cached in: {config.LLM_CACHE_FILE}")

if __name__ == "__main__":
    main()
