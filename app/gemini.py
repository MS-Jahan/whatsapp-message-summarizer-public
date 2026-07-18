from __future__ import annotations
import logging
import time
from app.models import Settings

log = logging.getLogger(__name__)

ROUNDS_PER_KEY = 3
RETRY_GAP_SECONDS = 10


class GeminiError(Exception):
    pass


class GeminiClient:
    def __init__(self, settings: Settings, sleep=time.sleep):
        self.settings = settings
        self.sleep = sleep

    def _call(self, api_key: str, model: str, parts: list) -> str:
        from google import genai
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(model=model, contents=parts)
        return resp.text

    def generate(self, parts: list, primary_model: str, fallback_model: str) -> str:
        """Try each key in turn. For each key, run ROUNDS_PER_KEY rounds, and each
        round attempts the primary model then the fallback model. Order:
        free[primary, fallback] x3, then paid[primary, fallback] x3.
        Sleep RETRY_GAP_SECONDS between every attempt; raise on total exhaustion."""
        keys = [("free", self.settings.gemini_key_free),
                ("paid", self.settings.gemini_key_paid)]
        models = [("primary", primary_model), ("fallback", fallback_model)]
        last_err: Exception | None = None
        first = True
        for key_label, key in keys:
            for round_no in range(1, ROUNDS_PER_KEY + 1):
                for model_label, model in models:
                    if not first:
                        self.sleep(RETRY_GAP_SECONDS)
                    first = False
                    try:
                        return self._call(key, model, parts)
                    except Exception as e:
                        last_err = e
                        log.warning(
                            "gemini %s key %s model round %d failed: %s",
                            key_label, model_label, round_no, e)
        raise GeminiError(f"all gemini attempts failed: {last_err}")
