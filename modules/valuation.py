"""
valuation.py — Comparable sales analysis & valuation engine.

CLIENT REQUIREMENTS (Hicks Property Valuation Logic):

  Step 1 — Filter comps by similarity to subject property
    Tier 1: sqft within 10% of subject OR same beds/baths
    Tier 2: sqft within 10-20% of subject OR beds/baths ±1

  Step 2 — Investment flag determination
    YES:    3+ tier1 comps AND average sale price ≥ $300,000
    MAYBE:  3+ tier2 comps AND average sale price ≥ $300,000
    NO:     If conditions above are not met
    NONE:   No comparable sales found

  Step 3 — Estimated value calculation
    Average sale price of filtered comparable properties

  Step 4 — Confidence scoring
    HIGH:   3+ comps used for valuation
    MEDIUM: 1-2 comps used
    LOW:    <1 comp (fallback estimate)
    NONE:   No comps at all
"""
import statistics
from typing import Callable, List, Optional, TypeVar

import numpy as np

from config import settings
from modules.models import (
    ComparableSale, ValuationResult,
    InvestmentFlag, ConfidenceLevel
)


T = TypeVar("T")


def _filter_iqr_outliers(
    items: List[T],
    value_getter: Callable[[T], float],
) -> List[T]:
    """
    Remove outliers using the 1.5x IQR rule.
    Falls back to the original items if filtering would remove everything.
    """
    if len(items) < 4:
        return items

    values = [value_getter(item) for item in items]
    q1 = np.percentile(values, 25)
    q3 = np.percentile(values, 75)
    iqr = q3 - q1

    lower_bound = q1 - (1.5 * iqr)
    upper_bound = q3 + (1.5 * iqr)

    filtered = [
        item
        for item in items
        if lower_bound <= value_getter(item) <= upper_bound
    ]

    return filtered or items


def estimate_value(
    comps: List[ComparableSale],
    subject_sqft: Optional[float] = None,
    subject_beds: Optional[int] = None,
    subject_baths: Optional[float] = None,
) -> ValuationResult:
    """
    Core valuation function implementing Hicks Property Valuation Logic.
    
    Filters comps by similarity to subject property, then determines
    investment flag based on client requirements:
        YES: 3+ similar comps (sqft 10% or same beds/baths) AND avg ≥ $300K
        MAYBE: 3+ semi-similar comps (sqft 10-20% or beds/baths ±1) AND avg ≥ $300K
        NO: If conditions not met
    """

    # ── Guard: no comps ───────────────────────────────────────
    if not comps:
        return ValuationResult(
            estimated_value = None,
            investment_flag = InvestmentFlag.NO,
            confidence      = ConfidenceLevel.NONE,
            comp_count      = 0,
            note            = "No comparable sales found in radius",
        )

    # ── Step 1: Filter comps by similarity ────────────────────
    # Tier 1: sqft within 10% OR same beds/baths (for YES flag)
    # Tier 2: sqft within 10-20% OR beds/baths ±1 (for MAYBE flag)
    from modules.attom_client import filter_comps_by_similarity
    tier1_comps, tier2_comps = filter_comps_by_similarity(
        comps, subject_sqft, subject_beds, subject_baths
    )

    # ── Step 2: Remove outliers from tier1 comps ──────────────
    valid_tier1 = [c for c in tier1_comps if c.sale_price and c.sale_price > 0]
    if len(valid_tier1) >= 4:
        filtered_tier1 = _filter_iqr_outliers(
            valid_tier1, lambda comp: comp.sale_price
        )
    else:
        filtered_tier1 = valid_tier1

    # ── Step 3: Calculate estimated value ─────────────────────
    estimated = None
    price_per_sqft = None
    comp_count = 0

    if filtered_tier1:
        # Use tier1 comps for valuation
        prices = [c.sale_price for c in filtered_tier1]
        comp_count = len(filtered_tier1)

        # Method: Average sale price of filtered comps
        estimated = statistics.mean(prices)

        # Calculate price per sqft if subject sqft is available
        if subject_sqft and subject_sqft > 0:
            ppsf_list = [
                c.sale_price / c.sqft
                for c in filtered_tier1
                if c.sqft and c.sqft > 0
            ]
            if ppsf_list:
                price_per_sqft = statistics.mean(ppsf_list)
    else:
        # Fallback: use all valid comps (tier2 + remaining)
        all_valid = [c for c in comps if c.sale_price and c.sale_price > 0]
        if all_valid:
            filtered_all = _filter_iqr_outliers(
                all_valid, lambda comp: comp.sale_price
            ) if len(all_valid) >= 4 else all_valid

            prices = [c.sale_price for c in filtered_all]
            comp_count = len(filtered_all)
            estimated = statistics.mean(prices)

            if subject_sqft and subject_sqft > 0:
                ppsf_list = [
                    c.sale_price / c.sqft
                    for c in filtered_all
                    if c.sqft and c.sqft > 0
                ]
                if ppsf_list:
                    price_per_sqft = statistics.mean(ppsf_list)

    # ── Step 4: Investment flag (Client Requirements) ─────────
    # Calculate average prices for tier1 and tier2
    tier1_avg = None
    tier2_avg = None

    if valid_tier1:
        tier1_avg = statistics.mean([c.sale_price for c in valid_tier1])

    valid_tier2 = [c for c in tier2_comps if c.sale_price and c.sale_price > 0]
    if valid_tier2:
        tier2_avg = statistics.mean([c.sale_price for c in valid_tier2])

    # Determine flag based on client requirements
    flag = InvestmentFlag.NO  # Default
    flag_reason = ""

    # YES: 3+ tier1 comps AND average ≥ $300K
    if len(valid_tier1) >= 3 and tier1_avg is not None:
        if tier1_avg >= settings.THRESHOLD:
            flag = InvestmentFlag.YES
            flag_reason = f"{len(valid_tier1)} tier1 comps (sqft 10% or same beds/baths), avg ${tier1_avg:,.0f} ≥ ${settings.THRESHOLD:,}"
        else:
            flag = InvestmentFlag.NO
            flag_reason = f"{len(valid_tier1)} tier1 comps but avg ${tier1_avg:,.0f} < ${settings.THRESHOLD:,}"
    # MAYBE: 3+ tier2 comps AND average ≥ $300K
    elif len(valid_tier2) >= 3 and tier2_avg is not None:
        if tier2_avg >= settings.THRESHOLD:
            flag = InvestmentFlag.MAYBE
            flag_reason = f"{len(valid_tier2)} tier2 comps (sqft 10-20% or beds/baths ±1), avg ${tier2_avg:,.0f} ≥ ${settings.THRESHOLD:,}"
        else:
            flag = InvestmentFlag.NO
            flag_reason = f"{len(valid_tier2)} tier2 comps but avg ${tier2_avg:,.0f} < ${settings.THRESHOLD:,}"
    else:
        flag = InvestmentFlag.NO
        tier1_count = len(valid_tier1)
        tier2_count = len(valid_tier2)
        flag_reason = f"Insufficient comps: {tier1_count} tier1, {tier2_count} tier2 (need 3+)"

    # ── Step 5: Confidence scoring (based on comp count) ──────
    if comp_count >= settings.MIN_COMPS_HIGH_CONF:
        confidence = ConfidenceLevel.HIGH
    elif comp_count >= settings.MIN_COMPS_MED_CONF:
        confidence = ConfidenceLevel.MEDIUM
    elif comp_count > 0:
        confidence = ConfidenceLevel.LOW
    else:
        confidence = ConfidenceLevel.NONE

    # ── Step 6: Calculate variance for note ───────────────────
    variance_pct = None
    if estimated and comp_count > 1:
        prices_for_variance = [c.sale_price for c in (filtered_tier1 if filtered_tier1 else comps)]
        prices_for_variance = [p for p in prices_for_variance if p and p > 0]
        if len(prices_for_variance) > 1:
            try:
                stdev = statistics.stdev(prices_for_variance)
                variance_pct = (stdev / estimated * 100) if estimated > 0 else 100
            except statistics.StatisticsError:
                variance_pct = None

    # ── Build note ────────────────────────────────────────────
    note_parts = []
    if tier1_comps:
        note_parts.append(f"{len(valid_tier1)} tier1 comp(s)")
    if valid_tier2:
        note_parts.append(f"{len(valid_tier2)} tier2 comp(s)")
    note_parts.append(f"{comp_count} comp(s) used for valuation")
    if variance_pct is not None:
        note_parts.append(f"variance {variance_pct:.1f}%")
    if price_per_sqft:
        note_parts.append(f"${price_per_sqft:.0f}/sqft")
    note_parts.append(flag_reason)

    return ValuationResult(
        estimated_value   = round(estimated, 2) if estimated else None,
        investment_flag   = flag,
        confidence        = confidence,
        comp_count        = comp_count,
        price_per_sqft    = round(price_per_sqft, 2) if price_per_sqft else None,
        comp_variance_pct = round(variance_pct, 1) if variance_pct else None,
        note              = " | ".join(note_parts),
    )
