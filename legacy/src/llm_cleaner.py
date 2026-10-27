"""Gemini-powered stop name cleaner and spelling corrector for Pune / PCMC bus routing."""
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List

import config

LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert geographical assistant specializing in Pune, Pimpri-Chinchwad (PCMC), and Maharashtra transit routes.
Your task is to take messy, misspelt, or Romanized Marathi/Hindi employee pickup stop names and convert each into its clean, standardized Indian postal/landmark name for geocoding.

Key Pune/PCMC Phonetic & Landmark Rules:
1. "showk", "chouk", "chawk", "chok" -> "Chowk" (e.g., "shastri showk" -> "Shastri Chowk, Bhosari, Pune")
2. "phata", "fata" -> "Phata" (e.g., "nashik fata" -> "Nashik Phata, Kasarwadi, Pune")
3. "wadgav", "wadgon" -> "Vadgaon" (e.g., "wadgav maval" -> "Vadgaon Maval, Pune")
4. "katewasti", "kate wasti" -> "Kate Wasti, Punawale, Pune"
5. "marbal", "marbel" -> "Marble Market"
6. "stn", "st" -> "Station"
7. "bhosri" -> "Bhosari"
8. "kothrud dep" -> "Kothrud Depot"
9. "dange chwk" -> "Dange Chowk, Thergaon"

Return ONLY a strict valid JSON object mapping each raw input string to its corrected landmark name.
Do not include markdown code block ticks (```) or any explanations.
Format:
{
  "raw_input_1": "Standard Landmark Name, Area, Pune",
  "raw_input_2": "Standard Landmark Name, Area, Pune"
}
"""

def load_llm_cache() -> Dict[str, str]:
    cache_path = Path(config.LLM_CACHE_FILE)
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            LOGGER.warning("Could not read LLM cache %s: %s", cache_path, e)
    return {}

def save_llm_cache(cache: Dict[str, str]):
    cache_path = Path(config.LLM_CACHE_FILE)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        LOGGER.warning("Could not save LLM cache %s: %s", cache_path, e)

def strip_city_suffix(text: str) -> str:
    """Strips redundant trailing ', Pune', ', Maharashtra', ', India' from LLM responses."""
    cleaned = re.sub(r",\s*(pune(\s+district)?|maharashtra|india)\b.*$", "", text, flags=re.IGNORECASE).strip()
    return cleaned.strip(" ,")


def _call_gemini_batch(uncached_stops: List[str]) -> Dict[str, str]:
    """Calls Gemini API with a batch of raw stop names."""
    api_key = config.GEMINI_API_KEY
    if not api_key:
        LOGGER.info("No GEMINI_API_KEY found. Skipping LLM correction.")
        return {s: s for s in uncached_stops}

    model_name = config.GEMINI_MODEL or "gemini-2.5-flash"
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Correct the following list of pickup stops:\n"
        f"{json.dumps(uncached_stops, ensure_ascii=False)}"
    )

    try:
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning)
            import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # Clean potential markdown fences ```json ... ```
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
            
        result = json.loads(text)
        if isinstance(result, dict):
            cleaned_batch = {}
            for k, v in result.items():
                v_str = str(v).strip()
                v_clean = strip_city_suffix(v_str)
                cleaned_batch[k] = v_clean if v_clean else v_str
            return cleaned_batch
    except Exception as e:
        LOGGER.warning("Gemini LLM call failed for batch: %s", e)

    return {s: s for s in uncached_stops}

def clean_stops_with_llm(raw_stops: List[str]) -> Dict[str, str]:
    """
    Cleans and standardizes a list of raw pickup names.
    Uses disk cache to avoid redundant LLM queries.
    Returns: dict mapping raw_stop -> cleaned_stop
    """
    cache = load_llm_cache()
    cleaned_mapping = {}
    missing_stops = []

    for stop in raw_stops:
        stop_clean = str(stop).strip()
        if not stop_clean:
            cleaned_mapping[stop] = ""
            continue
        if stop_clean in cache:
            cleaned_mapping[stop] = cache[stop_clean]
        else:
            missing_stops.append(stop_clean)

    if missing_stops and config.GEMINI_API_KEY:
        # Batch in chunks of 30 to stay well within token limits and latency targets
        batch_size = 30
        for i in range(0, len(missing_stops), batch_size):
            chunk = missing_stops[i:i + batch_size]
            corrections = _call_gemini_batch(chunk)
            for raw, clean in corrections.items():
                cache[raw] = clean
                cleaned_mapping[raw] = clean
            # If any in chunk failed to be in corrections, map to itself
            for s in chunk:
                if s not in cleaned_mapping:
                    cache[s] = s
                    cleaned_mapping[s] = s

        save_llm_cache(cache)
    else:
        for s in missing_stops:
            cleaned_mapping[s] = s

    return cleaned_mapping
