# Real Estate Deal Filter & Valuation Tool

> FastAPI-based tool that takes the CCLBA property CSV, enriches each property using ATTOM Data API, estimates market value using comparable sales, and flags investment opportunities above $300,000.

---

## Table of Contents

1. [What this tool does](#1-what-this-tool-does)
2. [The client data — CCLBA CSV](#2-the-client-data--cclba-csv)
3. [What is data enrichment?](#3-what-is-data-enrichment)
4. [What is data analysis here?](#4-what-is-data-analysis-here)
5. [The full pipeline — step by step](#5-the-full-pipeline--step-by-step)
6. [Why ATTOM only?](#6-why-attom-only)
7. [Project structure](#7-project-structure)
8. [How to run](#8-how-to-run)
9. [API endpoints](#9-api-endpoints)
10. [Output CSV explained](#10-output-csv-explained)
11. [Understanding the investment flags](#11-understanding-the-investment-flags)
12. [Confidence scores explained](#12-confidence-scores-explained)
13. [Limitations and edge cases](#13-limitations-and-edge-cases)

---

## 1. What this tool does

The client receives a list of ~1,000 property addresses from CCLBA (Cook County Land Bank Authority). These properties are available for investment. The problem: the CSV only has addresses — no property details, no values, nothing.

The client currently researches each property manually. That takes days.

This tool automates that entire process:

```
Raw CSV (address only)
        ↓
   Clean & validate addresses
        ↓
   Hit ATTOM API for property details
        ↓
   Hit ATTOM API for nearby comparable sales
        ↓
   Run valuation math on the comps
        ↓
   Output CSV with: value estimate + YES/NO/MAYBE flag + confidence score
```

The client now gets a filtered list in hours instead of days.

---

## 2. The client data — CCLBA CSV

The file `CCLBA_data_04_2026.csv` has a non-standard format:

```csv
Filters:
"In program","'Residential/Community Developer'"


"Parcel ID","Address","Owners"
="31042000050000","5100 183RD ST, Tinley Park, IL","-"
="31364160230000","4 APPLE LN, Park Forest, IL, 60466","-"
```

**Problems we found and fix:**

| Problem | Count | Fix |
|---|---|---|
| 4 header rows of metadata | Always | Skip with `skiprows=4` |
| Parcel IDs wrapped in `="..."` | All 981 | Strip with regex |
| Owners column is always `-` | All 981 | Leave blank — ATTOM fills this |
| Missing ZIP codes | 49 properties | Look up by city name from a map |
| Abbreviated address names | Many | Expand: ST→STREET, AVE→AVENUE, etc. |
| Duplicate properties | Variable | Deduplicate by Parcel ID |

After cleaning we have **981 valid properties** across 20 Illinois cities, mostly Chicago (883).

---

## 3. What is data enrichment?

Data enrichment means taking data that is **incomplete** and adding missing fields from external sources.

Our raw data has only:
- Parcel ID
- Address
- Owners (blank)

After enrichment from ATTOM, each property also has:

| Field | Source | Why it matters |
|---|---|---|
| Square footage | ATTOM property detail | Needed for price-per-sqft calculation |
| Beds / Baths | ATTOM property detail | Comp matching, property type context |
| Year built | ATTOM property detail | Older homes may need more repairs |
| Lot size | ATTOM property detail | Land value component |
| Property type | ATTOM property detail | Residential vs commercial |
| Assessed value | ATTOM / tax records | What government thinks it's worth |
| Last sale price + date | ATTOM historical | What it last sold for |
| Latitude / Longitude | ATTOM | Required to find nearby comps |
| Comparable sales | ATTOM sale snapshot | The basis for our value estimate |

Enrichment is the **most important step**. Without it, we cannot do any analysis.

---

## 4. What is data analysis here?

After enrichment, we perform **comparable sales analysis** — the same method real estate appraisers use.

The idea is simple: if 5 similar homes nearby sold for $280K, $310K, $295K, $320K, and $305K last year, then your property is probably worth around $300K.

### Our analysis does three things:

**A. Collect comparable sales**
We ask ATTOM: "Show me all residential properties that sold within 0.5 miles of this address in the last 12 months."

**B. Estimate the value (two methods blended)**

*Method 1 — Median sale price*
Take the median of all comp prices. Median is better than average because it's not skewed by one outlier sale.

*Method 2 — Price per square foot adjustment* (only if we know the subject's sqft)
Calculate the average $/sqft from comps → multiply by the subject property's sqft.
This adjusts for size differences. A 1,000 sqft home and a 2,000 sqft home in the same area will both appear in comps, but they shouldn't be compared at face value.

*Final estimate = (Method 1 + Method 2) / 2*

**C. Score confidence**
Not all valuations are equally reliable. We score them:

| Confidence | Meaning |
|---|---|
| HIGH | 3+ comparable sales, price variance < 20% |
| MEDIUM | 1-2 comps, or variance 20-40% |
| LOW | Only 1 comp, or high variance |
| NONE | No comparable sales found at all |

---

## 5. The full pipeline — step by step

```
┌────────────────────────────────────────────────────────────────────┐
│  Stage 1: CSV Load & Validation (csv_handler.py)                   │
│                                                                    │
│  • Skip 4 metadata rows                                            │
│  • Clean parcel ID format: ="123" → 123                            │
│  • Remove duplicates by parcel ID                                  │
│  • Fill missing ZIP codes from city lookup map                     │
│  • Expand address abbreviations (ST→STREET, etc.)                  │
│  Output: List of 981 clean RawProperty objects                     │
└────────────────────────────┬───────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│  Stage 2: ATTOM Property Enrichment (attom_client.py)              │
│                                                                    │
│  For each property → GET /property/basicprofile?address=...        │
│  • Extracts: sqft, beds, baths, year built, prop type              │
│  • Extracts: assessed value, last sale price/date                  │
│  • Extracts: latitude, longitude (CRITICAL for Stage 3)            │
│  • If 404 → mark as "failed", continue to next property            │
│  • Rate limit: 0.3s pause between calls                            │
│  • Max 5 concurrent async calls                                    │
└────────────────────────────┬───────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│  Stage 3: Comparable Sales Lookup (attom_client.py)                │
│                                                                    │
│  For each property with lat/lon → GET /sale/snapshot               │
│  Parameters: radius=0.5mi, last 12 months                          │
│  • Returns list of nearby sold properties                          │
│  • Filters out $0 and null sale prices                             │
│  • If no lat/lon → skip, mark as "partial"                         │
└────────────────────────────┬───────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│  Stage 4: Valuation Engine (valuation.py)                          │
│                                                                    │
│  • Calculate median comp price                                     │
│  • Calculate avg price/sqft → size-adjusted estimate               │
│  • Blend the two estimates                                         │
│  • Score confidence (HIGH / MEDIUM / LOW / NONE)                   │
│  • Set investment flag:                                            │
│      > $325K → YES                                                 │
│      $275K–$325K → MAYBE (manual review zone)                      │
│      < $275K → NO                                                  │
│      No estimate → UNKNOWN                                         │
└────────────────────────────┬───────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│  Stage 5: Output CSV (csv_handler.py)                              │
│                                                                    │
│  • Write one row per property                                      │
│  • Columns: address, flag, estimated value, confidence,            │
│    comp count, sqft, beds, baths, assessed value, notes            │
│  • Downloadable via GET /results/{job_id}                          │
└────────────────────────────────────────────────────────────────────┘
```

---

## 6. Why ATTOM only?

**Zillow and Realtor.com do NOT have public APIs.** They have unofficial scrapers on RapidAPI — third-party services that screen-scrape Zillow/Realtor and expose that data. These break frequently without warning, violate the original sites' terms of service for commercial use, and have very low rate limits.

**ATTOM** is a proper commercial property data provider:
- Official API with SLA and support
- Covers property details AND comparable sales in one subscription
- Full Illinois / Cook County coverage (our dataset)
- Legal to use commercially

Start with ATTOM alone. If coverage gaps appear (>15% of properties returning 404), then consider adding a secondary source. But for this dataset (mostly Chicago), ATTOM coverage should be very high.

---

## 7. Project structure

```
real_estate_tool/
│
├── main.py                  ← FastAPI app — all HTTP endpoints
├── config.py                ← All settings (API key, thresholds, etc.)
├── requirements.txt
├── .env.example             ← Copy to .env and add your ATTOM key
├── README.md                ← This file
│
├── modules/
│   ├── __init__.py
│   ├── models.py            ← Pydantic data models (typed shapes for all data)
│   ├── csv_handler.py       ← Read/clean CCLBA CSV + write output CSV
│   ├── attom_client.py      ← ATTOM API calls (property detail + comps)
│   ├── valuation.py         ← Comparable sales analysis + confidence scoring
│   └── pipeline.py          ← Orchestrates all stages, async processing
│
├── input/
│   └── CCLBA_data_04_2026.csv    ← Put client CSV files here
│
└── output/
    └── results_<jobid>_<date>.csv  ← Generated output files go here
```

---

## 8. How to run

### Step 1: Install dependencies

```bash
cd real_estate_tool
pip install -r requirements.txt
```

### Step 2: Set your ATTOM API key

```bash
cp .env.example .env
# Edit .env and replace YOUR_ATTOM_API_KEY_HERE with your real key
```

Get your ATTOM key from: https://api.developer.attomdata.com/home

### Step 3: Start the server

```bash
uvicorn main:app --reload --port 8000
```

### Step 4: Open the API docs

```
http://localhost:8000/docs
```

This is the interactive Swagger UI. You can trigger every endpoint from the browser.

### Step 5: Process the CSV

**Option A — via Swagger UI:**
1. Open `/docs`
2. Click `POST /process`
3. Enter `{"filename": "CCLBA_data_04_2026.csv"}`
4. Copy the `job_id` from the response

**Option B — via curl:**
```bash
curl -X POST http://localhost:8000/process \
  -H "Content-Type: application/json" \
  -d '{"filename": "CCLBA_data_04_2026.csv"}'
```

### Step 6: Track progress

```bash
curl http://localhost:8000/job/{job_id}
```

### Step 7: Download results when done

```bash
curl http://localhost:8000/results/{job_id} -o results.csv
```

---

## 9. API endpoints

| Method | Endpoint | What it does |
|---|---|---|
| `GET` | `/health` | Check if server is up and API key is set |
| `GET` | `/files` | List CSV files available in input/ |
| `POST` | `/process` | Start processing a CSV file |
| `GET` | `/job/{job_id}` | Poll job status (0–100% progress) |
| `GET` | `/summary/{job_id}` | Quick stats: how many YES/MAYBE/NO |
| `GET` | `/results/{job_id}` | Download the output CSV |
| `GET` | `/docs` | Interactive Swagger UI |

---

## 10. Output CSV explained

| Column | Example | What it means |
|---|---|---|
| `Parcel ID` | 31042000050000 | Cook County parcel identifier |
| `Address` | 5100 183RD STREET, Tinley Park, IL 60477 | Cleaned, normalized address |
| `Investment Flag` | YES | YES / NO / MAYBE / UNKNOWN |
| `Estimated Value` | $342,000 | Our comp-based estimate |
| `Confidence` | HIGH | HIGH / MEDIUM / LOW / NONE |
| `Comp Count` | 5 | How many comparable sales were found |
| `Price/sqft` | $185 | Average $/sqft from comps |
| `Sqft` | 1,850 | Property square footage from ATTOM |
| `Beds` | 3 | Bedrooms |
| `Baths` | 1.5 | Bathrooms |
| `Year Built` | 1962 | Year of construction |
| `Assessed Value` | $89,000 | Tax assessed value (often lower than market) |
| `Last Sale Price` | $45,000 | Most recent sale on record |
| `Last Sale Date` | 2019-03-15 | When it last sold |
| `Prop Type` | RESIDENTIAL | Property type from ATTOM |
| `Status` | success | success / partial / failed |
| `Notes` | 5 comp(s) used, variance 12.3% | Why value is what it is |

---

## 11. Understanding the investment flags

```
         $0        $275K      $300K      $325K        ∞
          |────────__|──────────|──────────|──────────|
               NO          MAYBE               YES
```

**YES** — Estimated value above $325K. Strong investment candidate.

**MAYBE** — Estimated value between $275K–$325K. In the margin zone. These need a quick manual review — they could easily be above or below $300K once you look more carefully.

**NO** — Estimated value below $275K. Probably not worth pursuing at the target threshold.

**UNKNOWN** — Could not estimate. No comparable sales found, or ATTOM had no data for this address. Requires manual lookup.

---

## 12. Confidence scores explained

Confidence tells you how much to trust the estimated value.

| Score | What it means | Action |
|---|---|---|
| `HIGH` | 3+ comps, < 20% price variance | Trust the estimate |
| `MEDIUM` | 1-2 comps, or 20-40% variance | Use with caution, spot check |
| `LOW` | Only 1 comp or high variance | Verify manually before deciding |
| `NONE` | No comps at all | Cannot estimate — manual lookup required |

**Example:** If `Investment Flag = YES` but `Confidence = LOW`, don't skip manual review — that single comp might not be representative.

**Best properties to act on:** `Investment Flag = YES` AND `Confidence = HIGH`.

---

## 13. Limitations and edge cases

**ATTOM coverage gaps**
ATTOM's coverage is excellent in Cook County but not 100%. Some properties — especially those with unusual or inconsistent address formats — may return 404. These are marked as `Status = failed` in the output.

**Missing ZIP codes**
49 properties had no ZIP code. We fill these using a city-to-ZIP lookup table. However, Chicago has multiple ZIP codes — we default to 60601. ATTOM generally handles this well since it also matches by Parcel ID internally, but a handful may still fail.

**Comparable sales radius**
We use 0.5 miles as the default search radius. In dense urban areas like Chicago this usually returns plenty of comps. In suburban or rural areas, you may need to increase `COMP_RADIUS_MILES` in config.py.

**Market timing**
Our comps look back 12 months. If you're running this in a fast-moving market, recent comps may not reflect current prices. Reduce `COMP_MONTHS` to 6 for a more conservative estimate, or increase to 18 if you're not finding enough comps.

**Rate limits**
Processing all 981 properties makes ~2,000 ATTOM API calls (one for details, one for comps). With the default 0.3s delay and 5 concurrent workers, total runtime is roughly 20-40 minutes. This is within ATTOM's standard plan limits, but confirm your specific plan's call limits.
