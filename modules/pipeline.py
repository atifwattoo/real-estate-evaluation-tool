"""
pipeline.py — The full data enrichment pipeline.

WHAT THIS FILE DOES:
  Orchestrates the entire enrichment process for a list of properties.
  For each property it:
    1. Calls ATTOM to get property details
    2. Calls ATTOM again to get comparable sales (using lat/lon from step 1)
    3. Runs the valuation engine on the comps
    4. Builds an EnrichedProperty output record

  Uses asyncio + semaphore to process multiple properties concurrently
  without hammering the ATTOM API beyond its rate limits.

PIPELINE STAGES (in order):
  RAW CSV  →  [Validate]  →  [ATTOM Enrich]  →  [Comp Lookup]  →  [Valuation]  →  OUTPUT CSV
"""
import asyncio
import httpx
from datetime import datetime
from typing import List, Callable, Optional

from config import settings
from modules.models import RawProperty, EnrichedProperty, InvestmentFlag, ConfidenceLevel
from modules.attom_client import get_property_detail, get_comparable_sales
from modules.valuation import estimate_value


# Job state store (in-memory; in production use Redis or a DB)
_jobs: dict[str, dict] = {}
_cancelled: set[str] = set()


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


def cancel_job(job_id: str) -> bool:
    """Mark a job for cancellation. Returns True if found, False otherwise."""
    if job_id not in _jobs:
        return False
    _cancelled.add(job_id)
    _update_job(job_id, status="cancelled", finished_at=datetime.utcnow().isoformat())
    return True


def list_jobs() -> list[dict]:
    """Return all jobs sorted by start time (newest first)."""
    jobs = []
    for job_id, job in _jobs.items():
        jobs.append({"job_id": job_id, **job})
    jobs.sort(key=lambda j: j.get("started_at", ""), reverse=True)
    return jobs


def _update_job(job_id: str, **kwargs):
    if job_id in _jobs:
        _jobs[job_id].update(kwargs)


async def _enrich_one(
    prop: RawProperty,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    index: int,
    total: int,
) -> EnrichedProperty:
    """
    Enrich a single property through all pipeline stages.
    Semaphore limits concurrent calls to MAX_CONCURRENT.
    All errors are caught — the pipeline never crashes on a single bad property.
    """
    async with semaphore:
        enriched = EnrichedProperty(
            parcel_id = prop.parcel_id,
            address   = prop.address,
        )

        # ── Stage 1: Property details from ATTOM ─────────────
        details = await get_property_detail(prop.address, client)

        if details.error:
            # ATTOM couldn't find this property — mark partial and return
            enriched.enrichment_status = "failed"
            enriched.notes = details.error
            print(f"  [{index}/{total}] FAILED  {prop.address[:50]} → {details.error}")
            return enriched

        # Populate enriched record with property details
        enriched.attom_id      = details.attom_id
        enriched.sqft          = details.sqft
        enriched.lot_size      = details.lot_size
        enriched.beds          = details.beds
        enriched.baths         = details.baths
        enriched.year_built    = details.year_built
        enriched.prop_type     = details.prop_type
        enriched.assessed_val  = details.assessed_val
        enriched.market_val    = details.market_val
        enriched.last_sale_price = details.last_sale_price
        enriched.last_sale_date  = details.last_sale_date
        enriched.latitude      = details.latitude
        enriched.longitude     = details.longitude

        # ── Stage 2: Comparable sales (needs lat/lon or attom_id) ────
        comps = []
        if details.latitude and details.longitude:
            comps = await get_comparable_sales(
                details.latitude, details.longitude, client,
                attom_id=details.attom_id,
            )
        else:
            enriched.notes = "No lat/lon from ATTOM — skipped comp lookup"

        # ── Stage 3: Valuation ────────────────────────────────
        valuation = estimate_value(comps, subject_sqft=enriched.sqft)

        enriched.estimated_value   = valuation.estimated_value
        enriched.investment_flag   = valuation.investment_flag
        enriched.confidence        = valuation.confidence
        enriched.comp_count        = valuation.comp_count
        enriched.price_per_sqft    = valuation.price_per_sqft
        enriched.comp_variance_pct = valuation.comp_variance_pct
        enriched.enrichment_status = "success" if comps else "partial"

        # Merge notes
        existing_note = enriched.notes or ""
        val_note      = valuation.note or ""
        enriched.notes = " | ".join(filter(None, [existing_note, val_note]))

        flag_symbol = "✓" if enriched.investment_flag == InvestmentFlag.YES else "✗"
        val_str = f"${enriched.estimated_value:,.0f}" if enriched.estimated_value else "N/A"
        print(f"  [{index}/{total}] {flag_symbol} {prop.address[:45]:<45} → {val_str} ({enriched.confidence})")

        return enriched


async def run_pipeline(
    properties: List[RawProperty],
    job_id: str,
    on_progress: Optional[Callable] = None,
) -> List[EnrichedProperty]:
    """
    Main pipeline entry point.
    Processes all properties concurrently (up to MAX_CONCURRENT at a time).

    Args:
        properties:  List of cleaned RawProperty objects from csv_handler
        job_id:      Unique job ID for tracking progress via API
        on_progress: Optional callback(processed, total) for live updates

    Returns:
        List of EnrichedProperty objects (one per input row)
    """
    total = len(properties)
    results: List[EnrichedProperty] = []
    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT)

    _jobs[job_id] = {
        "status":    "running",
        "progress":  0,
        "total":     total,
        "processed": 0,
        "started_at": datetime.utcnow().isoformat(),
    }

    print(f"\n[Pipeline] Starting job {job_id}")
    print(f"[Pipeline] {total} properties | max {settings.MAX_CONCURRENT} concurrent | {settings.API_DELAY_SECONDS}s delay\n")

    async with httpx.AsyncClient() as client:
        tasks = [
            _enrich_one(prop, client, semaphore, i + 1, total)
            for i, prop in enumerate(properties)
        ]

        for i, coro in enumerate(asyncio.as_completed(tasks)):
            if job_id in _cancelled:
                print(f"[Pipeline] Job {job_id} cancelled at {i}/{total}")
                return results
            result = await coro
            results.append(result)

            processed = i + 1
            progress  = int(processed / total * 100)
            _update_job(job_id, processed=processed, progress=progress)

            if on_progress:
                on_progress(processed, total)

            # Batch log every N properties
            if processed % settings.BATCH_SIZE == 0:
                yes_count = sum(1 for r in results if r.investment_flag == InvestmentFlag.YES)
                print(f"\n[Pipeline] Progress: {processed}/{total} | YES flags so far: {yes_count}\n")

    # Final stats
    yes_count   = sum(1 for r in results if r.investment_flag == InvestmentFlag.YES)
    maybe_count = sum(1 for r in results if r.investment_flag == InvestmentFlag.MAYBE)
    failed      = sum(1 for r in results if r.enrichment_status == "failed")

    _update_job(job_id,
        status     = "done",
        progress   = 100,
        yes_count  = yes_count,
        maybe_count= maybe_count,
        failed     = failed,
        finished_at= datetime.utcnow().isoformat(),
    )

    print(f"\n[Pipeline] Done! {total} processed | {yes_count} YES | {maybe_count} MAYBE | {failed} failed")
    return results
