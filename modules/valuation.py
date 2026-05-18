"""
valuation.py — Comparable sales analysis & valuation engine.

HOW THE VALUATION WORKS (step by step):

  Step 1 — Collect comp prices
    We get a list of nearby sold properties from ATTOM.
    We extract their sale prices (skip any $0 or null entries).

  Step 2 — Estimate value (two methods, then blend)
    Method A: Median sale price of all comps
      Simple and robust. Not affected by outliers as much as average.

    Method B: Price-per-sqft adjustment (only if subject sqft is known)
      Calculate avg $/sqft from comps → multiply by subject's sqft.
      This adjusts for size differences between the subject and comps.

    Final estimate = average of Method A and Method B (if both available),
                     or just Method A if sqft is unknown.

  Step 3 — Confidence scoring
    HIGH:   3+ comps AND price variance < 20%
    MEDIUM: 1-2 comps OR variance 20-40%
    LOW:    <1 comp OR variance > 40%
    NONE:   No comps at all

  Step 4 — Investment flag
    > $325K  → YES
    < $275K  → NO
    In between → MAYBE (needs manual review)
    No estimate → UNKNOWN
"""
import statistics
from typing import List, Optional

from config import settings
from modules.models import (
    ComparableSale, ValuationResult,
    InvestmentFlag, ConfidenceLevel
)


def estimate_value(
    comps: List[ComparableSale],
    subject_sqft: Optional[float] = None,
) -> ValuationResult:
    """
    Core valuation function.
    Takes a list of comparable sales + optionally the subject's sqft.
    Returns a ValuationResult with estimated value, flag, and confidence.
    """

    # ── Guard: no comps ───────────────────────────────────────
    if not comps:
        return ValuationResult(
            estimated_value = None,
            investment_flag = InvestmentFlag.UNKNOWN,
            confidence      = ConfidenceLevel.NONE,
            comp_count      = 0,
            note            = "No comparable sales found in radius",
        )

    # ── Step 1: Extract sale prices ───────────────────────────
    valid_comps = [c for c in comps if c.sale_price and c.sale_price > 0]
    if not valid_comps:
        return ValuationResult(
            estimated_value = None,
            investment_flag = InvestmentFlag.UNKNOWN,
            confidence      = ConfidenceLevel.NONE,
            comp_count      = 0,
            note            = "Comps found but all had $0 sale price",
        )

    sale_prices = [c.sale_price for c in valid_comps]
    comp_count  = len(sale_prices)

    # ── Step 2A: Median price method ──────────────────────────
    median_estimate = statistics.median(sale_prices)

    # ── Step 2B: Price-per-sqft method (if sqft available) ────
    sqft_estimate = None
    price_per_sqft = None

    if subject_sqft and subject_sqft > 0:
        ppsf_list = [
            c.sale_price / c.sqft
            for c in valid_comps
            if c.sqft and c.sqft > 0
        ]
        if ppsf_list:
            price_per_sqft = statistics.mean(ppsf_list)
            sqft_estimate  = price_per_sqft * subject_sqft

    # ── Blend the two methods ─────────────────────────────────
    if sqft_estimate:
        # Both methods available → blend equally
        estimated = (median_estimate + sqft_estimate) / 2
    else:
        # Only median method
        estimated = median_estimate

    # ── Step 3: Confidence scoring ────────────────────────────
    variance_pct = None
    if len(sale_prices) > 1:
        try:
            stdev = statistics.stdev(sale_prices)
            variance_pct = (stdev / estimated * 100) if estimated > 0 else 100
        except statistics.StatisticsError:
            variance_pct = None

    # Assign confidence tier
    if comp_count >= settings.MIN_COMPS_HIGH_CONF:
        if variance_pct is None or variance_pct < 20:
            confidence = ConfidenceLevel.HIGH
        elif variance_pct < 40:
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW
    elif comp_count >= settings.MIN_COMPS_MED_CONF:
        confidence = ConfidenceLevel.MEDIUM
    else:
        confidence = ConfidenceLevel.LOW

    # ── Step 4: Investment flag ───────────────────────────────
    if estimated > settings.UPPER_MARGIN:
        flag = InvestmentFlag.YES
    elif estimated < settings.LOWER_MARGIN:
        flag = InvestmentFlag.NO
    else:
        flag = InvestmentFlag.MAYBE   # In the $275K–$325K review zone

    # ── Build note ────────────────────────────────────────────
    note_parts = [f"{comp_count} comp(s) used"]
    if variance_pct is not None:
        note_parts.append(f"variance {variance_pct:.1f}%")
    if sqft_estimate:
        note_parts.append("size-adjusted")

    return ValuationResult(
        estimated_value   = round(estimated, 2),
        investment_flag   = flag,
        confidence        = confidence,
        comp_count        = comp_count,
        price_per_sqft    = round(price_per_sqft, 2) if price_per_sqft else None,
        comp_variance_pct = round(variance_pct, 1) if variance_pct else None,
        note              = " | ".join(note_parts),
    )
