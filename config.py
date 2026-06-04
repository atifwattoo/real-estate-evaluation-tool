"""
config.py — Central settings for the Real Estate Enrichment Tool
Put your ATTOM API key here before running.
"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── ATTOM API ─────────────────────────────────────────────
    ATTOM_API_KEY: str = os.getenv("ATTOM_API_KEY", "YOUR_ATTOM_API_KEY_HERE")
    ATTOM_BASE_URL: str = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"

    # ── Valuation thresholds ───────────────────────────────────
    THRESHOLD: int = 300_000          # Main investment flag line

    MARGIN_PERCENT: float = 0.25      # 25% margin around threshold

    @property
    def MARGIN_VALUE(self) -> int:
        return int(self.THRESHOLD * self.MARGIN_PERCENT)

    @property
    def LOWER_MARGIN(self) -> int:
        return self.THRESHOLD - self.MARGIN_VALUE      # "MAYBE" zone start

    @property
    def UPPER_MARGIN(self) -> int:
        return self.THRESHOLD + self.MARGIN_VALUE      # "MAYBE" zone end

    # ── Comparable sales filters ───────────────────────────────
    COMP_RADIUS_MILES: float = 1.0    # How far to look for comps
    COMP_MONTHS: int = 12             # How many months back
    MIN_COMPS_HIGH_CONF: int = 3      # Need 3+ comps for HIGH confidence
    MIN_COMPS_MED_CONF: int = 1       # Need 1+ for MEDIUM confidence

    # ── Processing ────────────────────────────────────────────
    API_DELAY_SECONDS: float = 0.3    # Pause between ATTOM calls (rate limit)
    MAX_CONCURRENT: int = 5           # Async workers at once
    BATCH_SIZE: int = 50              # Properties per batch log

    # ── File paths ────────────────────────────────────────────
    INPUT_DIR: str = "input"
    OUTPUT_DIR: str = "output"

    class Config:
        env_file = ".env"


settings = Settings()
