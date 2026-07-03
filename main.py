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
import shutil
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from modules.models import ProcessRequest, ProcessResponse, JobStatus
from modules.csv_handler import load_cclba_csv, export_to_csv
from modules.pipeline import run_pipeline, get_job, list_jobs, cancel_job


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

# Mount static files for the web UI
import os
static_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.exists(static_path):
    app.mount("/ui", StaticFiles(directory=static_path, html=True), name="ui")


def _apply_threshold(threshold: Optional[int]):
    if threshold is None:
        return
    if threshold <= 0:
        raise HTTPException(status_code=400, detail="threshold must be greater than 0")
    settings.THRESHOLD = int(threshold)


def _normalise_margin_percent(value) -> float:
    margin_percent = float(value)
    if margin_percent < 0:
        raise ValueError("margin_percent must be 0 or greater")
    if margin_percent > 1:
        margin_percent = margin_percent / 100
    return margin_percent


def _apply_margin_percent(value):
    if value is None:
        return
    try:
        settings.MARGIN_PERCENT = _normalise_margin_percent(value)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


def _config_snapshot() -> dict:
    return {
        "threshold": settings.THRESHOLD,
        "margin_percent": settings.MARGIN_PERCENT,
        "margin_percentage": round(settings.MARGIN_PERCENT * 100, 2),
        "margin_value": settings.MARGIN_VALUE,
        "lower_margin": settings.LOWER_MARGIN,
        "upper_margin": settings.UPPER_MARGIN,
    }


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/", tags=["System"])
def root():
    """Redirect to web UI"""
    return RedirectResponse(url="/ui/index.html")


@app.get("/health", tags=["System"])
def health():
    """Basic health check. Returns API key status so you know if ATTOM is configured."""
    key_set = settings.ATTOM_API_KEY != "YOUR_ATTOM_API_KEY_HERE"
    return {
        "status":       "ok",
        "attom_key_set": key_set,
        "threshold":    f"${settings.THRESHOLD:,}",
        "margin_percent": settings.MARGIN_PERCENT,
        "lower_margin": f"${settings.LOWER_MARGIN:,}",
        "upper_margin": f"${settings.UPPER_MARGIN:,}",
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
    _apply_threshold(request.threshold)

    # Override margin_percent if provided
    _apply_margin_percent(request.margin_percent)

    # Load and validate CSV
    try:
        properties = load_cclba_csv(
            filepath,
            skiprows=request.skiprows,
            count=request.count,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse CSV: {str(e)}")

    if not properties:
        raise HTTPException(status_code=422, detail="No valid properties found in CSV")

    # Create job
    job_id = str(uuid.uuid4())[:8]
    output_file = Path(settings.OUTPUT_DIR) / f"results_{job_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    # Extract comps parameters
    comps_kwargs = request.model_dump(
        exclude={"filename", "threshold", "count", "skiprows"},
        exclude_none=True
    ) if hasattr(request, "model_dump") else request.dict(
        exclude={"filename", "threshold", "count", "skiprows"},
        exclude_none=True
    )

    # Run pipeline in background so the HTTP response returns immediately
    async def run_and_save():
        results = await run_pipeline(properties, job_id, comps_kwargs=comps_kwargs)
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


# ── Upload CSV and process ────────────────────────────────────────────────────

@app.post("/upload", tags=["System"])
async def upload_file(
    file: UploadFile = File(...),
):
    """
    Upload a CSV file directly.
    The file is saved to the input/ directory.
    Returns the filename and path.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")

    input_dir = Path(settings.INPUT_DIR)
    input_dir.mkdir(exist_ok=True)

    # Sanitise filename to prevent path traversal
    safe_name = Path(file.filename).name
    filepath = input_dir / safe_name

    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return {
        "filename": safe_name,
        "path": str(filepath),
        "message": f"Uploaded '{safe_name}' successfully."
    }


# ── List all jobs ─────────────────────────────────────────────────────────────

@app.get("/jobs", tags=["Processing"])
def get_all_jobs():
    """Return all jobs (running and completed) sorted newest-first."""
    return {"jobs": list_jobs()}


# ── Runtime config update ─────────────────────────────────────────────────────

@app.post("/config", tags=["System"])
def update_config(body: dict):
    """
    Update runtime configuration.
    Accepts: attom_api_key, threshold, margin_percent, margin_percentage.
    Persists attom_api_key to PostgreSQL (upsert).
    """
    if "attom_api_key" in body:
        settings.ATTOM_API_KEY = str(body["attom_api_key"]).strip()

    if "threshold" in body:
        _apply_threshold(int(body["threshold"]))

    margin_value = body.get("margin_percent", body.get("margin_percentage"))
    _apply_margin_percent(margin_value)

    return {
        "status": "ok",
        "message": "Configuration updated successfully",
        "config": _config_snapshot(),
    }


# ── Cancel a running job ────────────────────────────────────────────────────

@app.post("/job/{job_id}/cancel", tags=["Processing"])
def cancel_job_endpoint(job_id: str):
    """Cancel a running or queued job."""
    if not cancel_job(job_id):
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return {"status": "ok", "message": f"Job {job_id} cancellation requested"}


# ── List output files ─────────────────────────────────────────────────────────

@app.get("/output-files", tags=["System"])
def list_output_files():
    """List all CSV result files in the output/ directory, newest first."""
    output_dir = Path(settings.OUTPUT_DIR)
    if not output_dir.exists():
        return {"files": []}
    files = []
    for f in sorted(output_dir.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True):
        files.append({
            "filename": f.name,
            "size_kb":  round(f.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return {"files": files}


# ── Download output file by name ───────────────────────────────────────────────

@app.get("/output/{filename}", tags=["Processing"])
def download_output_file(filename: str):
    """Download any output CSV file by filename."""
    safe_name  = Path(filename).name   # prevent path traversal
    output_dir = Path(settings.OUTPUT_DIR)
    filepath   = output_dir / safe_name
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail=f"File '{safe_name}' not found")
    return FileResponse(path=filepath, media_type="text/csv", filename=safe_name)
