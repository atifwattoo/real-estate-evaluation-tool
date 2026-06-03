"""
csv_handler.py — Reads and cleans the CCLBA CSV format.

The CCLBA CSV has a quirky format:
  - First 4 rows are metadata/filters (we skip them)
  - Parcel IDs are wrapped in Excel formula format: ="31042000050000"
  - Some addresses are missing ZIP codes (~49 out of 981)
  - Owners column is always "-" (blank data — enrichment fills this)

This module handles all of that so the rest of the pipeline gets clean data.
"""
import re
import pandas as pd
from pathlib import Path
from typing import List

from modules.models import RawProperty


# Known city-to-ZIP mapping for Illinois addresses missing ZIP codes.
# Used to fill in missing ZIPs before sending to ATTOM.
CITY_ZIP_MAP = {
    "CHICAGO":             "60601",
    "DOLTON":              "60419",
    "RIVERDALE":           "60827",
    "MAYWOOD":             "60153",
    "CALUMET CITY":        "60409",
    "HARVEY":              "60426",
    "CICERO":              "60804",
    "MATTESON":            "60443",
    "SOUTH HOLLAND":       "60473",
    "PARK FOREST":         "60466",
    "SAUK VILLAGE":        "60411",
    "RICHTON PARK":        "60471",
    "COUNTRY CLUB HILLS":  "60478",
    "TINLEY PARK":         "60477",
    "CHICAGO HEIGHTS":     "60411",
    "BERKELEY":            "60163",
    "HAZEL CREST":         "60429",
    "OLYMPIA FIELDS":      "60461",
    "GLENWOOD":            "60425",
    "STONE PARK":          "60165",
}


def _clean_parcel_id(raw: str) -> str:
    """
    ATTOM format: strip Excel formula wrapper.
    ="31042000050000"  →  31042000050000
    """
    return re.sub(r'[="\'"]', "", str(raw)).strip()


def _fill_missing_zip(address: str) -> str:
    """
    If a ZIP code (5 digits) is missing from the address,
    look up the city in our map and append it.

    '12 S WHIPPLE ST, Chicago, IL'  →  '12 S WHIPPLE ST, Chicago, IL 60601'
    """
    if re.search(r"\d{5}", address):
        return address  # ZIP already present

    # Extract city name from "STREET, City, IL" pattern
    match = re.search(r",\s*([^,]+),\s*IL", address, re.IGNORECASE)
    if match:
        city_raw = match.group(1).strip().upper()
        zip_code = CITY_ZIP_MAP.get(city_raw)
        if zip_code:
            return address.rstrip() + f" {zip_code}"

    return address  # Return as-is if we can't find a ZIP


def _normalize_address(address: str) -> str:
    """
    Standardize address for ATTOM API lookups.

    Rules:
      - Uppercase everything (ATTOM is case-insensitive but uppercase is canonical)
      - Remove 'HSE' suffix — a CCLBA-specific annotation that means "house" and
        confuses ATTOM's address parser
      - Do NOT expand abbreviations (N/S/E/W, ST, AVE, etc.).
        ATTOM indexes addresses with standard USPS abbreviated forms,
        and expanding them can cause mismatches.
    """
    address = address.upper().strip()

    # Remove CCLBA-specific unit annotations that ATTOM does not recognise
    address = re.sub(r'\s+HSE\b', '', address)

    # Collapse multiple spaces
    address = re.sub(r'\s+', ' ', address).strip()

    return address


def load_cclba_csv(filepath: str | Path) -> List[RawProperty]:
    """
    Main entry point. Reads the CCLBA CSV, skips header rows,
    cleans Parcel IDs, fills missing ZIPs, deduplicates.

    Returns a list of RawProperty objects ready for the pipeline.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"CSV not found: {filepath}")

    # Skip first 4 rows (filter metadata added by CCLBA export)
    df = pd.read_csv(filepath, skiprows=4)
    df.columns = ["parcel_id", "address", "owners"]

    # Step 1: Clean parcel IDs
    df["parcel_id"] = df["parcel_id"].apply(_clean_parcel_id)

    # Step 2: Drop rows with no address
    df = df[df["address"].notna() & (df["address"].str.strip() != "")]

    # Step 3: Deduplicate by parcel ID
    before = len(df) # Save original length before removing duplicates
    df = df.drop_duplicates(subset=["parcel_id"])
    dupes_removed = before - len(df)

    # Step 4: Fill missing ZIP codes
    df["address"] = df["address"].apply(_fill_missing_zip)

    # Step 5: Normalize address formatting
    df["address"] = df["address"].apply(_normalize_address)

    print(f"[CSV] Loaded {len(df)} properties ({dupes_removed} duplicates removed)")
    print(f"[CSV] Cities: {df['address'].str.extract(r', ([^,]+), IL')[0].value_counts().head(5).to_dict()}")

    return [
        RawProperty(
            parcel_id=row["parcel_id"],
            address=row["address"],
            owners=row.get("owners"),
        )
        for _, row in df.iterrows()
    ]


def export_to_csv(properties: list, output_path: str | Path) -> Path:
    """
    Write enriched properties to a CSV report.
    Only outputs fields useful for investment decisions.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for p in properties:
        rows.append({
            "Parcel ID":        p.parcel_id,
            "Address":          p.address,
            "Investment Flag":  p.investment_flag,
            "Estimated Value":  f"${p.estimated_value:,.0f}" if p.estimated_value else "N/A",
            "Confidence":       p.confidence,
            "Comp Count":       p.comp_count,
            "Price/sqft":       f"${p.price_per_sqft:.0f}" if p.price_per_sqft else "N/A",
            "Sqft":             p.sqft,
            "Beds":             p.beds,
            "Baths":            p.baths,
            "Year Built":       p.year_built,
            "Assessed Value":   f"${p.assessed_val:,.0f}" if p.assessed_val else "N/A",
            "Last Sale Price":  f"${p.last_sale_price:,.0f}" if p.last_sale_price else "N/A",
            "Last Sale Date":   p.last_sale_date,
            "Prop Type":        p.prop_type,
            "Status":           p.enrichment_status,
            "Notes":            p.notes,
        })

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)
    print(f"[CSV] Report saved: {output_path} ({len(rows)} rows)")
    return output_path
