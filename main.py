"""
main.py — FastAPI application for the Real Estate Enrichment Tool.

ENDPOINTS:
  POST /process          Start processing a CSV file (async background job)
  GET  /job/{job_id}     Check job status and progress
  GET  /results/{job_id} Download the output CSV
  GET  /health           Health check
  GET  /docs             Auto-generated Swagger UI (FastAPI built-in)

HOW TO RUN:
  uvicorn main:app --reload --port 8000

THEN OPEN:
  http://localhost:8000/docs   ← Interactive API documentation
"""
import uuid
import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from config import settings
from modules.models import ProcessRequest, ProcessResponse, JobStatus
from modules.csv_handler import load_cclba_csv, export_to_csv
from modules.pipeline import run_pipeline, get_job


app = FastAPI(
    title="Real Estate Deal Filter & Valuation Tool",
    description="""
    Processes CCLBA property CSV files, enriches each property using the ATTOM Data API,
    performs comparable sales analysis, and flags properties likely worth > $300K.

    ## How to use
    1. Place your CSV file in the `input/` folder
    2. POST to `/process` with the filename
    3. Poll `/job/{job_id}` to track progress
    4. GET `/results/{job_id}` to download the output CSV when done

    ## Output fields
    Each output row contains: estimated value, YES/NO/MAYBE flag, confidence score,
    comparable count, property details (beds/baths/sqft), and notes.
    """,
    version="1.0.0",
)


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    """Basic health check. Returns API key status so you know if ATTOM is configured."""
    key_set = settings.ATTOM_API_KEY != "YOUR_ATTOM_API_KEY_HERE"
    return {
        "status":       "ok",
        "attom_key_set": key_set,
        "threshold":    f"${settings.THRESHOLD:,}",
        "version":      "1.0.0",
    }


# ── Process CSV ───────────────────────────────────────────────────────────────

@app.post("/process", response_model=ProcessResponse, tags=["Processing"])
async def process_csv(request: ProcessRequest, background_tasks: BackgroundTasks):
    """
    Start processing a CSV file.

    - Reads the file from the `input/` directory
    - Validates and cleans addresses
    - Queues enrichment as a background job
    - Returns a job_id you can poll for progress

    **Note:** Processing 981 properties takes ~20–40 minutes depending on
    your ATTOM API plan speed and rate limits.
    """
    filepath = Path(settings.INPUT_DIR) / request.filename
    if not filepath.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File '{request.filename}' not found in input/ directory"
        )

    # Override threshold if provided
    if request.threshold:
        settings.THRESHOLD = request.threshold

    # Load and validate CSV
    try:
        properties = load_cclba_csv(filepath)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse CSV: {str(e)}")

    if not properties:
        raise HTTPException(status_code=422, detail="No valid properties found in CSV")

    # Create job
    job_id = str(uuid.uuid4())[:8]
    output_file = Path(settings.OUTPUT_DIR) / f"results_{job_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    # Run pipeline in background so the HTTP response returns immediately
    async def run_and_save():
        results = await run_pipeline(properties, job_id)
        export_to_csv(results, output_file)

    background_tasks.add_task(run_and_save)

    return ProcessResponse(
        job_id        = job_id,
        status        = "queued",
        total         = len(properties),
        processed     = 0,
        flagged_yes   = 0,
        flagged_maybe = 0,
        output_file   = None,
        message       = f"Processing {len(properties)} properties. Poll /job/{job_id} for progress.",
    )


# ── Job status ────────────────────────────────────────────────────────────────

@app.get("/job/{job_id}", response_model=JobStatus, tags=["Processing"])
def job_status(job_id: str):
    """
    Check the status of a running or completed job.

    Returns:
    - status: queued | running | done | failed
    - progress: 0–100
    - processed: number of properties completed so far
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    processed = job.get("processed", 0)
    total     = job.get("total", 0)
    progress  = job.get("progress", 0)
    status    = job.get("status", "running")

    if status == "done":
        yes   = job.get("yes_count", 0)
        maybe = job.get("maybe_count", 0)
        msg   = f"Complete. {yes} YES flags, {maybe} MAYBE flags out of {total} properties."
    else:
        msg = f"Processing... {processed}/{total} done"

    return JobStatus(
        job_id    = job_id,
        status    = status,
        progress  = progress,
        total     = total,
        processed = processed,
        message   = msg,
    )


# ── Download results ──────────────────────────────────────────────────────────

@app.get("/results/{job_id}", tags=["Processing"])
def download_results(job_id: str):
    """
    Download the output CSV for a completed job.
    Returns the file directly for download.
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job.get("status") != "done":
        raise HTTPException(
            status_code=202,
            detail=f"Job not done yet. Status: {job.get('status')} ({job.get('progress', 0)}%)"
        )

    # Find the output file
    output_dir = Path(settings.OUTPUT_DIR)
    matches = list(output_dir.glob(f"results_{job_id}_*.csv"))
    if not matches:
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(
        path         = matches[0],
        media_type   = "text/csv",
        filename     = matches[0].name,
    )


# ── List available input files ────────────────────────────────────────────────

@app.get("/files", tags=["System"])
def list_input_files():
    """List all CSV files available in the input/ directory."""
    input_dir = Path(settings.INPUT_DIR)
    if not input_dir.exists():
        return {"files": []}

    files = [
        {
            "filename": f.name,
            "size_kb":  round(f.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
        for f in input_dir.glob("*.csv")
    ]
    return {"files": files}


# ── Summary stats for a completed job ────────────────────────────────────────

@app.get("/summary/{job_id}", tags=["Processing"])
def job_summary(job_id: str):
    """
    Returns summary statistics for a completed job.
    Useful for a quick overview without downloading the full CSV.
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job.get("status") != "done":
        raise HTTPException(status_code=202, detail="Job not complete yet")

    total    = job.get("total", 0)
    yes      = job.get("yes_count", 0)
    maybe    = job.get("maybe_count", 0)
    failed   = job.get("failed", 0)
    no       = total - yes - maybe - failed

    return {
        "job_id":           job_id,
        "total_properties": total,
        "flagged_yes":      yes,
        "flagged_maybe":    maybe,
        "flagged_no":       no,
        "enrichment_failed": failed,
        "yes_rate_pct":     round(yes / total * 100, 1) if total else 0,
        "started_at":       job.get("started_at"),
        "finished_at":      job.get("finished_at"),
    }
