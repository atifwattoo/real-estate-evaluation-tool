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


HEADERS = {
    "apikey": settings.ATTOM_API_KEY,
    "Accept": "application/json",
}

# Sale Comparables API uses a different base URL (v2)
COMPS_BASE_URL = "https://api.gateway.attomdata.com/property/v2"


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
        r = await client.get(url, headers=HEADERS, params=params, timeout=15)
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
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return comps


async def _get_comps_by_propid(
    attom_id: str,
    client: httpx.AsyncClient,
) -> list[ComparableSale]:
    """
    Primary comp source: ATTOM Sale Comparables v2 API.
    Purpose-built for comp analysis — returns AI-selected nearby sold properties.
    Endpoint: GET /property/v2/salescomparables/propid/{attomId}
    """
    url = f"{COMPS_BASE_URL}/salescomparables/propid/{attom_id}"
    try:
        r = await client.get(url, headers=HEADERS, timeout=15)
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

    try:
        r = await client.get(url, headers=HEADERS, params=params, timeout=15)
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
        comps = await _get_comps_by_propid(attom_id, client)
        if comps:
            return comps

    # Fallback: radius-based area search
    if lat and lon:
        return await _get_comps_by_radius(lat, lon, client, radius_miles, months)

    return []
