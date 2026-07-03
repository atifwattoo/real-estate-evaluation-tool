"""
models.py — Pydantic models for request/response validation
Every piece of data flowing through the pipeline has a typed shape here.
"""
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class InvestmentFlag(str, Enum):
    YES   = "YES"
    NO    = "NO"
    MAYBE = "MAYBE"   # In the configured threshold margin zone


class ConfidenceLevel(str, Enum):
    HIGH    = "HIGH"    # 3+ comps, low variance
    MEDIUM  = "MEDIUM"  # 1-2 comps or moderate variance
    LOW     = "LOW"     # 0-1 comps, high variance
    NONE    = "NONE"    # No data at all


# ── Raw input row from CCLBA CSV ─────────────────────────────────────────────

class RawProperty(BaseModel):
    parcel_id: str
    address: str
    owners: Optional[str] = None


# ── ATTOM API response shapes ────────────────────────────────────────────────

class PropertyDetails(BaseModel):
    sqft:         Optional[float] = None
    lot_size:     Optional[float] = None
    beds:         Optional[int]   = None
    baths:        Optional[float] = None
    year_built:   Optional[int]   = None
    prop_type:    Optional[str]   = None
    assessed_val: Optional[float] = None
    market_val:   Optional[float] = None
    last_sale_price: Optional[float] = None
    last_sale_date:  Optional[str]   = None
    latitude:     Optional[float] = None
    longitude:    Optional[float] = None
    attom_id:     Optional[str]   = None
    error:        Optional[str]   = None   # Set if ATTOM call failed


class ComparableSale(BaseModel):
    address:    str
    sale_price: float
    sale_date:  Optional[str]  = None
    sqft:       Optional[float] = None
    beds:       Optional[int]   = None
    baths:      Optional[float] = None
    distance_miles: Optional[float] = None


# ── Valuation output ─────────────────────────────────────────────────────────

class ValuationResult(BaseModel):
    estimated_value: Optional[float] = None
    investment_flag: InvestmentFlag   = InvestmentFlag.NO
    confidence:      ConfidenceLevel  = ConfidenceLevel.NONE
    comp_count:      int              = 0
    price_per_sqft:  Optional[float] = None
    comp_variance_pct: Optional[float] = None
    note:            Optional[str]   = None


# ── Final enriched property (one row in output CSV) ──────────────────────────

class EnrichedProperty(BaseModel):
    # From input
    parcel_id: str
    address:   str

    # From ATTOM enrichment
    attom_id:     Optional[str]   = None
    prop_type:    Optional[str]   = None
    sqft:         Optional[float] = None
    lot_size:     Optional[float] = None
    beds:         Optional[int]   = None
    baths:        Optional[float] = None
    year_built:   Optional[int]   = None
    assessed_val: Optional[float] = None
    market_val:   Optional[float] = None
    last_sale_price: Optional[float] = None
    last_sale_date:  Optional[str]   = None
    latitude:     Optional[float] = None
    longitude:    Optional[float] = None

    # Valuation
    estimated_value:   Optional[float] = None
    investment_flag:   InvestmentFlag  = InvestmentFlag.NO
    confidence:        ConfidenceLevel = ConfidenceLevel.NONE
    comp_count:        int             = 0
    price_per_sqft:    Optional[float] = None
    comp_variance_pct: Optional[float] = None

    # Status
    enrichment_status: str = "pending"   # pending | success | partial | failed
    notes:             Optional[str] = None


# ── API request/response models ──────────────────────────────────────────────

class ProcessRequest(BaseModel):
    filename: str = Field(..., description="CSV filename in the input/ folder")
    threshold: Optional[int] = Field(None, gt=0, description="Override default $300K threshold")
    margin_percent: Optional[float] = Field(None, description="Margin % around threshold (e.g. 0.25 or 25). Optional.")
    count: Optional[int] = Field(
        None,
        ge=0,
        description="Maximum cleaned properties to process. Omit, null, or 0 to process all.",
    )
    skiprows: int = Field(
        0,
        ge=0,
        description="Number of rows to skip before reading the CSV header.",
    )
    searchType: Optional[str] = None
    minComps: Optional[int] = None
    maxComps: Optional[int] = None
    miles: Optional[float] = None
    sameCity: Optional[bool] = None
    useSameTargetCode: Optional[bool] = None
    useCode: Optional[str] = None
    bedroomsRange: Optional[int] = None
    bathroomRange: Optional[int] = None
    sqFeetRange: Optional[int] = None
    lotSizeRange: Optional[int] = None
    onlyPropertiesWithPool: Optional[bool] = None
    saleDateRange: Optional[int] = None
    saleAmountRangeFrom: Optional[int] = None
    saleAmountRangeTo: Optional[int] = None
    unitNumberRange: Optional[int] = None
    yearBuiltRange: Optional[int] = None
    storiesRange: Optional[int] = None
    include0SalesAmounts: Optional[bool] = None
    includeFullSalesOnly: Optional[bool] = None
    ownerOccupied: Optional[str] = None
    distressed: Optional[str] = None


class ProcessResponse(BaseModel):
    job_id:       str
    status:       str
    total:        int
    processed:    int
    flagged_yes:  int
    flagged_maybe: int
    output_file:  Optional[str] = None
    message:      str


class JobStatus(BaseModel):
    job_id:    str
    status:    str          # queued | running | done | failed
    progress:  int          # 0–100
    total:     int
    processed: int
    message:   str
