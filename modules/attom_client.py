"""
attom_client.py — ATTOM Data API integration.

WHY ATTOM:
  ATTOM is the only provider here with a proper commercial API.
  It covers property details AND comparable sales in one subscription.
  Zillow/Realtor have no official public API — only unofficial scrapers
  on RapidAPI that break without warning and violate ToS for commercial use.

ATTOM ENDPOINTS USED:
  1. /property/basicprofile                    → property details (sqft, beds, assessed value...)
  2. /property/v2/salescomparables/propid/{id} → purpose-built comparable sales (primary)
  3. /sale/snapshot?lat=&lon=&radius=          → area-based comp fallback

RATE LIMITING:
  ATTOM enforces limits based on your plan. We add a configurable delay
  (default 0.3s) between calls to stay safe. Async + semaphore controls
  how many calls run simultaneously.
"""
import asyncio
import re
from datetime import date, timedelta
import httpx
from typing import Optional

from config import settings
from modules.models import PropertyDetails, ComparableSale


# Sale Comparables API uses a different base URL (v2)
COMPS_BASE_URL = "https://api.gateway.attomdata.com/property/v2"


def _headers() -> dict:
    """Build ATTOM request headers using the current runtime API key."""
    return {
        "apikey": settings.ATTOM_API_KEY,
        "Accept": "application/json",
    }


def _split_address(address: str) -> tuple[str, str]:
    """
    Split a full address string into ATTOM's preferred address1 / address2 format.

    '438 N AVERS AVE, CHICAGO, IL, 60624'
        → address1='438 N AVERS AVE'
        → address2='CHICAGO, IL 60624'
    """
    parts = [p.strip() for p in address.split(',')]
    if len(parts) < 2:
        return address, ""

    address1 = parts[0]

    if len(parts) >= 4:
        # STREET, CITY, STATE, ZIP  →  CITY, STATE ZIP
        address2 = f"{parts[1]}, {parts[2].strip()} {parts[3].strip()}"
    elif len(parts) == 3:
        # STREET, CITY, STATE ZIP  (or STREET, CITY, STATE — no ZIP)
        address2 = f"{parts[1]}, {parts[2]}"
    else:
        address2 = parts[1]

    return address1, address2.strip()


# ── Property Detail ───────────────────────────────────────────────────────────

async def get_property_detail(
    address: str,
    client: httpx.AsyncClient,
) -> PropertyDetails:
    """
    Fetch property details from ATTOM by address string.

    Returns PropertyDetails with .error set if the call fails.
    Failure is NON-FATAL — the pipeline marks the property as 'partial'
    and continues to the next one.
    """
    url = f"{settings.ATTOM_BASE_URL}/property/basicprofile"
    address1, address2 = _split_address(address)
    params = {"address1": address1, "address2": address2}

    try:
        r = await client.get(url, headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        prop = data.get("property", [{}])[0]
        building = prop.get("building", {})
        lot      = prop.get("lot", {})
        summary  = prop.get("summary", {})
        assess   = prop.get("assessment", {})
        sale     = prop.get("sale", {})
        location = prop.get("location", {})

        return PropertyDetails(
            sqft         = building.get("size", {}).get("universalsize"),
            lot_size     = lot.get("lotsize1"),
            beds         = building.get("rooms", {}).get("beds"),
            baths        = building.get("rooms", {}).get("bathstotal"),
            year_built   = summary.get("yearbuilt"),
            prop_type    = summary.get("proptype"),
            assessed_val = assess.get("assessed", {}).get("assdttlvalue"),
            market_val   = assess.get("market", {}).get("mktttlvalue"),
            last_sale_price = sale.get("amount", {}).get("saleamt"),
            last_sale_date  = sale.get("salesearchdate"),
            latitude     = location.get("latitude"),
            longitude    = location.get("longitude"),
            attom_id     = str(prop.get("identifier", {}).get("attomId", "")),
        )

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 404:
            return PropertyDetails(error="Address not found in ATTOM database")
        if status == 429:
            return PropertyDetails(error="Rate limit hit — try again later")
        return PropertyDetails(error=f"HTTP {status}: {e.response.text[:100]}")

    except httpx.TimeoutException:
        return PropertyDetails(error="Request timed out after 15s")

    except Exception as e:
        return PropertyDetails(error=f"Unexpected error: {str(e)[:100]}")

    finally:
        # Always pause after each call to respect rate limits
        await asyncio.sleep(settings.API_DELAY_SECONDS)


# ── Comparable Sales ─────────────────────────────────────────────────────────

def _parse_comp_properties(property_list: list) -> list[ComparableSale]:
    """Extract ComparableSale objects from an ATTOM 'property' array."""
    comps = []
    for prop in property_list:
        try:
            sale_amt = prop.get("sale", {}).get("amount", {}).get("saleamt", 0)
            if not sale_amt or sale_amt <= 0:
                continue  # Skip unsold / $0 transfers

            address_parts = prop.get("address", {})
            addr_str = " ".join(filter(None, [
                address_parts.get("line1", ""),
                address_parts.get("city", ""),
                address_parts.get("state", ""),
            ]))

            comps.append(ComparableSale(
                address    = addr_str or "Unknown",
                sale_price = float(sale_amt),
                sale_date  = prop.get("sale", {}).get("salesearchdate"),
                sqft       = prop.get("building", {}).get("size", {}).get("universalsize"),
                beds       = prop.get("building", {}).get("rooms", {}).get("beds"),
                baths      = prop.get("building", {}).get("rooms", {}).get("bathstotal"),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return comps


def filter_comps_by_similarity(
    comps: list[ComparableSale],
    subject_sqft: Optional[float] = None,
    subject_beds: Optional[int] = None,
    subject_baths: Optional[float] = None,
) -> tuple[list[ComparableSale], list[ComparableSale]]:
    """
    Filter comparable properties by similarity to subject property.
    
    Returns (tier1_comps, tier2_comps):
        - tier1: sqft within 10% OR same beds/baths
        - tier2: sqft within 10-20% OR beds/baths ±1 (excluding tier1)
    
    Client requirements:
        YES flag: 3+ tier1 comps with avg value ≥ $300K
        MAYBE flag: 3+ tier2 comps with avg value ≥ $300K
    """
    tier1 = []
    tier2 = []
    tier1_indices = set()

    for i, comp in enumerate(comps):
        # Skip comps with missing critical data
        if comp.sale_price <= 0:
            continue

        in_tier1 = False
        in_tier2 = False

        # ── Tier 1: sqft within 10% ──
        if subject_sqft and comp.sqft and comp.sqft > 0:
            sqft_min_10 = subject_sqft * (1 - settings.SQFT_TIER1_PERCENT)
            sqft_max_10 = subject_sqft * (1 + settings.SQFT_TIER1_PERCENT)
            if sqft_min_10 <= comp.sqft <= sqft_max_10:
                in_tier1 = True

        # ── Tier 1: same beds/baths ──
        if subject_beds is not None and subject_baths is not None:
            if comp.beds is not None and comp.baths is not None:
                if comp.beds == subject_beds and comp.baths == subject_baths:
                    in_tier1 = True

        if in_tier1:
            tier1.append(comp)
            tier1_indices.add(i)
            continue

        # ── Tier 2: sqft within 10-20% ──
        if subject_sqft and comp.sqft and comp.sqft > 0:
            sqft_min_20 = subject_sqft * (1 - settings.SQFT_TIER2_PERCENT)
            sqft_max_20 = subject_sqft * (1 + settings.SQFT_TIER2_PERCENT)
            if sqft_min_20 <= comp.sqft <= sqft_max_20:
                in_tier2 = True

        # ── Tier 2: beds/baths ±1 ──
        if subject_beds is not None and subject_baths is not None:
            if comp.beds is not None and comp.baths is not None:
                beds_diff = abs(comp.beds - subject_beds)
                baths_diff = abs(comp.baths - subject_baths)
                if beds_diff <= settings.BEDS_BATHS_RANGE and baths_diff <= settings.BEDS_BATHS_RANGE:
                    in_tier2 = True

        if in_tier2:
            tier2.append(comp)

    return tier1, tier2


async def _get_comps_by_propid(
    attom_id: str,
    client: httpx.AsyncClient,
    **kwargs,
) -> list[ComparableSale]:
    """
    Primary comp source: ATTOM Sale Comparables v2 API.
    Purpose-built for comp analysis — returns AI-selected nearby sold properties.
    Endpoint: GET /property/v2/salescomparables/propid/{attomId}
    """
    url = f"{COMPS_BASE_URL}/salescomparables/propid/{attom_id}"
    try:
        r = await client.get(url, headers=_headers(), params=kwargs, timeout=15)
        r.raise_for_status()
        data = r.json()
        return _parse_comp_properties(data.get("property", []))
    except Exception:
        return []
    finally:
        await asyncio.sleep(settings.API_DELAY_SECONDS)


async def _get_comps_by_radius(
    lat: float,
    lon: float,
    client: httpx.AsyncClient,
    radius_miles: Optional[float],
    months: Optional[int],
    **kwargs,
) -> list[ComparableSale]:
    """
    Fallback comp source: area-based sale/snapshot search using lat/lon radius.
    Uses correct ATTOM date format: YYYY/MM/DD (status code 10 = bad date format).
    Endpoint: GET /propertyapi/v1.0.0/sale/snapshot
    """
    radius = radius_miles or settings.COMP_RADIUS_MILES
    months_back = months or settings.COMP_MONTHS

    # ATTOM requires YYYY/MM/DD — NOT "-12M" or any relative format
    start_date = date.today() - timedelta(days=months_back * 30)
    start_date_str = start_date.strftime("%Y/%m/%d")

    url = f"{settings.ATTOM_BASE_URL}/sale/snapshot"
    params = {
        "latitude":            lat,
        "longitude":           lon,
        "radius":              radius,
        "startSaleSearchDate": start_date_str,
        "minSaleAmt":          1,
        "pageSize":            25,
    }
    params.update(kwargs)

    try:
        r = await client.get(url, headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return _parse_comp_properties(data.get("property", []))
    except Exception:
        return []
    finally:
        await asyncio.sleep(settings.API_DELAY_SECONDS)


async def get_comparable_sales(
    lat: float,
    lon: float,
    client: httpx.AsyncClient,
    attom_id: Optional[str] = None,
    radius_miles: Optional[float] = None,
    months: Optional[int] = None,
    **kwargs,
) -> list[ComparableSale]:
    """
    Fetch comparable sales for a property.

    Strategy:
      1. Try salescomparables/propid/{attomId} — purpose-built ATTOM comp engine.
         Returns AI-selected nearby comps. Best results when available.
      2. Fall back to sale/snapshot lat/lon radius search when attom_id is absent
         or returns no results.

    WHY THIS MATTERS FOR VALUATION:
      The estimated property value is calculated from these comps.
      More comps = higher confidence. Zero comps = UNKNOWN value.
    """
    # Primary: use dedicated Sales Comparables API (v2) if we have an ATTOM ID
    if attom_id:
        comps = await _get_comps_by_propid(attom_id, client, **kwargs)
        if comps:
            return comps

    # Fallback: radius-based area search
    if lat and lon:
        return await _get_comps_by_radius(lat, lon, client, radius_miles, months, **kwargs)

    return []


async def get_comparable_sales_with_dynamic_radius(
    lat: float,
    lon: float,
    client: httpx.AsyncClient,
    attom_id: Optional[str] = None,
    subject_sqft: Optional[float] = None,
    subject_beds: Optional[int] = None,
    subject_baths: Optional[float] = None,
    months: Optional[int] = None,
    **kwargs,
) -> list[ComparableSale]:
    """
    Fetch comparable sales with dynamic radius expansion per client requirements.
    
    Strategy:
      1. Try salescomparables/propid/{attomId} first (if available)
      2. If not enough comps, use radius search:
         - Start with 0.5 mile radius
         - If < MIN_COMPS_FOR_RADIUS_EXPANSION comps, expand to 1 mile
      3. Filter comps by similarity to subject property
    
    Client requirements:
      - If 5 properties with same sqft or beds/baths within 0.5 mile -> use them
      - If < 5 comps -> extend search radius to 1 mile
      - If still < 5 comps -> expand sqft tolerance to 20%
    """
    months_back = months or settings.COMP_MONTHS
    
    # Step 1: Try propid-based comps first
    if attom_id:
        comps = await _get_comps_by_propid(attom_id, client, **kwargs)
        if comps and len(comps) >= settings.MIN_COMPS_FOR_RADIUS_EXPANSION:
            return comps

    # Step 2: Dynamic radius search
    if lat and lon:
        # Try 0.5 mile first
        comps_05 = await _get_comps_by_radius(
            lat, lon, client, settings.COMP_RADIUS_INITIAL, months_back, **kwargs
        )
        
        # Filter by similarity
        tier1_05, tier2_05 = filter_comps_by_similarity(
            comps_05, subject_sqft, subject_beds, subject_baths
        )
        
        # If we have enough tier1 comps at 0.5 mile, use them
        if len(tier1_05) >= settings.MIN_COMPS_FOR_RADIUS_EXPANSION:
            return comps_05
        
        # If not enough, expand to 1 mile
        comps_1 = await _get_comps_by_radius(
            lat, lon, client, settings.COMP_RADIUS_EXPANDED, months_back, **kwargs
        )
        
        # Combine and filter
        all_comps = comps_05 + comps_1
        # Remove duplicates by address
        seen_addresses = set()
        unique_comps = []
        for comp in all_comps:
            if comp.address not in seen_addresses:
                seen_addresses.add(comp.address)
                unique_comps.append(comp)
        
        return unique_comps

    # Fallback: return whatever we got from propid
    return comps if attom_id else []
