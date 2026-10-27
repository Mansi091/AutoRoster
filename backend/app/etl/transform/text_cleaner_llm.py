"""LLM-assisted text cleaning for ambiguous or messy Indian landmark strings.

IMPORTANT RULE: This module ONLY cleans search text strings for downstream geocoding.
It NEVER generates, predicts, or returns geographical coordinates.
"""

import json
from typing import Dict, Optional
from ...core.config import config
from ...core.logging import logger
from ...utils.text import normalize_pickup_text

class LLMTextCleaner:
    def __init__(self):
        self.cache_file = config.LLM_CACHE_FILE
        self._cache: Dict[str, str] = self._load_cache()

    def _load_cache(self) -> Dict[str, str]:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Error loading LLM cleaner cache: {e}")
        return {}

    def _save_cache(self) -> None:
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving LLM cleaner cache: {e}")

    def clean_search_text(self, messy_text: str) -> str:
        """Transforms noisy colloquial text (e.g., 'opp dmart near bridge') into clean query."""
        base_clean = normalize_pickup_text(messy_text)
        if not base_clean:
            return ""

        cache_key = base_clean.lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        api_key = config.GEMINI_API_KEY
        if not api_key:
            return base_clean

        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            prompt = (
                f"Extract the official Pune/PCMC landmark name from this colloquial input: '{base_clean}'. "
                "Output ONLY the standardized landmark name string without quotes, explanations, or coordinates."
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            cleaned = response.text.strip().replace('"', '').replace("'", "")
            if cleaned:
                self._cache[cache_key] = cleaned
                self._save_cache()
                return cleaned
        except Exception as e:
            logger.warning(f"LLM text cleaning failed for '{messy_text}': {e}")

        return base_clean

text_cleaner_llm = LLMTextCleaner()
