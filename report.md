# How We Estimated the Value of Your Properties

---

## Table of Contents

1. [What You Gave Us (Input CSV)](#1-what-you-gave-us-input-csv)
2. [Where We Got Property Data (ATTOM API)](#2-where-we-got-property-data-attom-api)
3. [Where We Got Comparable Sales (ATTOM Comps API)](#3-where-we-got-comparable-sales-attom-comps-api)
4. [How We Cleaned the Data First](#4-how-we-cleaned-the-data-first)
5. [How We Removed Bad Comparable Sales (Outlier Removal)](#5-how-we-removed-bad-comparable-sales-outlier-removal)
6. [Our Valuation Formula — Step by Step](#6-our-valuation-formula--step-by-step)
7. [How Confident Are We in Each Estimate?](#7-how-confident-are-we-in-each-estimate)
8. [Investment Recommendation (YES / NO / MAYBE)](#8-investment-recommendation-yes--no--maybe)
9. [The Output File You Receive](#9-the-output-file-you-receive)
10. [Summary of Results So Far](#10-summary-of-results-so-far)

---

## 1. What You Gave Us (Input CSV)

You provided a CSV file named **"CCLBA data 04.2026.csv"** that contained a list of properties.

### What was in the file:

| Column | What It Contains | Example |
|---|---|---|
| **Parcel ID** | County's unique ID for each property | `31042000050000` |
| **Address** | Full street address of the property | `438 N AVERS AVE, CHICAGO, IL 60624` |
| **Owners** | Owner name | (this field was blank in the source data) |

We processed **981 properties** from this list.

---

## 2. Where We Got Property Data (ATTOM API)

We used a service called **ATTOM Data** (the largest property data provider in the US) to look up each property.

### Step 1: Find the Property

We sent ATTOM the address of each property. For example:

> **What we sent:** `"438 N AVERS AVE, CHICAGO, IL 60624"`

ATTOM searched its database and returned details about that property.

### Step 2: What ATTOM Sent Back

For each property, ATTOM returned the following information:

| Information We Got | Example Value | Why We Need It |
|---|---|---|
| **Square footage (Sqft)** | 1,200 sqft | To compare size with similar properties |
| **Lot size** | 5,000 sqft | Property land area |
| **Bedrooms** | 3 | Property type info |
| **Bathrooms** | 1.5 | Property type info |
| **Year built** | 1954 | Age of property |
| **Property type** | Single Family | House, apartment, etc. |
| **Assessed value** | $12,000 | County's tax assessment |
| **Market value** | $180,000 | ATTOM's own estimate |
| **Last sale price** | $145,000 | When it last sold |
| **Last sale date** | March 2020 | When it last sold |
| **Latitude & Longitude** | 41.883, -87.724 | Location coordinates |
| **ATTOM ID** | 12345678 | ATTOM's internal ID |

> If ATTOM couldn't find a property (address not in their database), we marked it as "Failed" and moved to the next one. This happens for about 1-2% of addresses.

---

## 3. Where We Got Comparable Sales (ATTOM Comps API)

### What Are Comparable Sales?

Comparable sales (or "comps") are **recently sold properties that are similar to yours and located nearby**. Real estate agents use the same concept when pricing a house.

### How We Found Comps

We used two methods:

**Method A (Preferred):** If ATTOM gave us an ATTOM ID for the property, we used their special "Sales Comparables" tool that automatically finds the most relevant nearby sold properties.

**Method B (Fallback):** If we only had coordinates (latitude/longitude), we searched for all sold properties within a **1-mile radius** that sold in the **last 12 months**.

### What Data We Got for Each Comparable Sale

| Information | Example | Used For |
|---|---|---|
| **Sale price** | $200,000 | Main input for valuation |
| **Sale date** | 2025-08-15 | To check if sale is recent |
| **Square footage** | 1,250 sqft | For price-per-sqft calculation |
| **Address** | 440 N AVERS AVE | Just for identification |

We **skipped** any comps with a $0 sale price (these are usually tax transfers, not real market sales).

### Typical Results

- Properties with good comps: **5-10 nearby sold properties found**
- Some properties: **0 comps** (too rural, unique property type)
- Average comps per property: **4-6**

---

## 4. How We Cleaned the Data First

Before running the valuation, we fixed some issues in your CSV:

### Problem 1: Excel Format in Parcel IDs
Your CSV had parcel IDs like `="31042000050000"` (Excel formula format). We stripped it to just `31042000050000`.

### Problem 2: Duplicate Properties
About **30-40 properties** appeared twice in the list. We removed duplicates.

### Problem 3: Address Format
ATTOM reads addresses better in UPPERCASE, so we converted everything to capital letters and removed special CCLBA annotations like "HSE" (meaning "house") that confused ATTOM.

---

## 5. How We Removed Bad Comparable Sales (Outlier Removal)

Before calculating value, we **remove extreme prices** from our comparable sales list. This is important because:

### Real Example

Imagine we find 10 nearby sold properties with these prices:

```
$180,000  $185,000  $190,000  $195,000  $200,000
$205,000  $210,000  $215,000  $220,000  $2,000,000
```

The last property at **$2 million** is clearly different (maybe a mansion while others are small homes). If we include it, the average would be falsely high.

### The Formula We Used (IQR Method)

We used a statistical method called **IQR (Interquartile Range)** to automatically detect and remove such outliers:

```
Step 1: Sort all prices from lowest to highest
Step 2: Find Q1 = the price at the 25% mark
Step 3: Find Q3 = the price at the 75% mark
Step 4: IQR = Q3 - Q1  (the middle spread)
Step 5: Lower limit = Q1 - (1.5 x IQR)
Step 6: Upper limit = Q3 + (1.5 x IQR)
Step 7: Remove any price below lower limit or above upper limit
```

### Simple Analogy

Think of it like judging a diving competition. The judges drop the highest and lowest scores before calculating the average. We do the same — any comp that is **way too high or way too low** compared to the rest is removed.

> **Safety check:** If removing outliers would remove ALL comps (very rare), we keep the original list.

---

## 6. Our Valuation Formula — Step by Step

After removing bad comps, we use **two different methods** to estimate the property value, then combine them.

---

### Method A: Median Price (Simple & Reliable)

We take the **middle value** (median) of all comparable sale prices.

**Formula:**
```
Median Estimate = The middle price when all comp prices are sorted
```

**Example:**
If comp prices are: $180K, $190K, **$200K**, $210K, $220K
- The median is **$200,000** (the middle value)

**Why median, not average?**
- Average (mean) = all prices added divided by count
- If one comp is $2M instead of $200K, the average jumps to $560K — wrong!
- The median stays at **$200K** no matter what — it is stable

---

### Method B: Price-per-Square-Foot (Size Adjustment)

This method adjusts for size differences between your property and the comparable sales.

**Step-by-step:**

```
Step 1: For each comp, calculate:
         Price per sqft = Sale Price / Square Footage

Step 2: Take the average of all price-per-sqft values
         (also remove outliers here using the same IQR method)

Step 3: Multiply by your property's square footage:
         Sqft Estimate = Average Price per sqft x Your Property's Sqft
```

**Real Example:**

| Comp | Sold For | Size | Price per Sqft |
|---|---|---|---|
| House A | $200,000 | 1,200 sqft | $166.67/sqft |
| House B | $220,000 | 1,300 sqft | $169.23/sqft |
| House C | $190,000 | 1,150 sqft | $165.22/sqft |

Average price per sqft = ($166.67 + $169.23 + $165.22) / 3 = **$167.04/sqft**

If your property is **1,250 sqft**:
> Sqft Estimate = $167.04 x 1,250 = **$208,800**

---

### Final Blend: Combining Both Methods

We don't rely on just one method. We combine them with **60% weight on the Median** and **40% weight on the Sqft method**:

```
Final Value = (Median x 0.6) + (Sqft Estimate x 0.4)
```

**Why 60/40?**
- The Median is more **stable** (outliers can't affect it) — so it gets 60%
- The Sqft method adds **size adjustment** — it gets 40%
- If we don't know your property's square footage, we use only the Median

**Example calculation:**

```
Median Estimate     = $200,000
Sqft Estimate       = $208,800

Final Value = ($200,000 x 0.6) + ($208,800 x 0.4)
            = $120,000 + $83,520
            = $203,520
```

---

## 7. How Confident Are We in Each Estimate?

Not all estimates are equally reliable. We assign a **confidence level** based on two factors:

### Factor 1: How Many Comps Did We Find?
- **3+ comps** = Good (more data = more reliable)
- **1-2 comps** = OK
- **0 comps** = Cannot estimate

### Factor 2: How Consistent Are the Comps? (Variance)

We calculate **variance** — a measure of how spread out the comp prices are:

```
Variance % = (Standard Deviation of comp prices / Estimated Value) x 100
```

### Plain English: What Does Variance Mean?

| If comps look like this | Variance | Meaning |
|---|---|---|
| $190K, $195K, $200K, $205K, $210K | **Low (~4%)** | Comps are very consistent — high reliability |
| $150K, $200K, $250K, $300K, $350K | **High (~38%)** | Comps are all over the place — low reliability |

### Confidence Levels

| Confidence | When Does It Happen? | What It Means |
|---|---|---|
| **HIGH** | 3+ comps AND prices are consistent (variance under 20%) | You can rely on this estimate |
| **MEDIUM** | 1-2 comps OR prices are somewhat spread out (variance 20-40%) | Reasonable estimate, but some uncertainty |
| **LOW** | Very few comps OR prices are highly scattered (variance over 40%) | Use with caution |
| **NONE** | No comps found at all | We cannot estimate |

---

## 8. Investment Recommendation (YES / NO / MAYBE)

Based on the estimated value, we flag each property:

### The Threshold: $300,000

We set **$300,000** as the main decision line. But we don't use a hard cut — we add a **25% margin** to avoid borderline mistakes.

### The Three Zones

```
       SKIP ZONE               REVIEW ZONE                   BUY ZONE
├────────────────────┼──────────────────────────┼────────────────────┤
                     $225,000                  $375,000
```

| Estimated Value | Flag | What To Do |
|---|---|---|
| **Above $375,000** | **YES** | This property is likely worth significantly more than $300K — worth pursuing |
| **Below $225,000** | **NO** | This property is well below $300K — likely not worth it |
| **Between $225K and $375K** | **MAYBE** | Borderline case — needs manual review |
| **No estimate** | **UNKNOWN** | Not enough data to evaluate |

### Why a Margin Zone?

A property worth $299,000 is essentially the same as one worth $301,000. If we used a strict "$300K or above" rule, one would be NO and the other YES — which makes no sense. The 25% margin ($75,000 buffer) catches these borderline cases and flags them for **human review**.

---

## 9. The Output File You Receive

After processing, we save the results as a CSV file in the `output/` folder:

```
output/results_[job_id]_[date]_[time].csv
```

### What Each Row Contains

| Column | Description |
|---|---|
| **Parcel ID** | County's unique ID |
| **Address** | Full address of the property |
| **Investment Flag** | YES / NO / MAYBE / UNKNOWN |
| **Estimated Value** | Our calculated market value |
| **Confidence** | HIGH / MEDIUM / LOW / NONE |
| **Comp Count** | Number of comparable sales used (after removing bad ones) |
| **Price/sqft** | Average price per square foot from comps |
| **Sqft** | Property's square footage |
| **Beds** | Number of bedrooms |
| **Baths** | Number of bathrooms |
| **Year Built** | Year the property was built |
| **Assessed Value** | County's assessment |
| **Last Sale Price** | Most recent sale price |
| **Last Sale Date** | When it last sold |
| **Prop Type** | Single Family, Multi-Family, etc. |
| **Status** | success / partial / failed |
| **Notes** | Processing notes (e.g. "5 comps used, 1 removed, variance 15%") |

### Typical Notes You'll See

```
5 comp(s) used | 1 outlier(s) removed | variance 15.2% | size-adjusted
```

This means:
- We found 5 comparable sales
- 1 was removed as an outlier (too high or too low)
- The remaining 4 have a variance of 15.2% (consistent)
- We used the size-adjustment method (sqft was available)

---

## 10. Summary of Results So Far

Across all 981 properties processed:

| Metric | Count |
|---|---|
| **Total properties** | 981 |
| **YES (Buy)** | *(varies by run)* |
| **NO (Skip)** | *(varies by run)* |
| **MAYBE (Review)** | *(varies by run)* |
| **Failed (no data)** | *(varies by run)* |

---

## Appendix: Complete Flow Diagram (Simple Version)

```
                    YOUR CSV FILE
                          |
                          v
            +-------------------------+
            |  Step 1: Clean the Data |
            |  - Fix parcel IDs       |
            |  - Remove duplicates    |
            |  - Fix missing ZIPs     |
            +-------------------------+
                          |
                          v
            +-------------------------+
            |  Step 2: ATTOM Lookup   |
            |  Send address           |
            |  Get: sqft, beds,       |
            |  baths, assessed value  |
            +-------------------------+
                          |
                          v
            +-------------------------+
            |  Step 3: Find Comps     |
            |  Search nearby sold     |
            |  properties (1 mile,    |
            |  12 months)             |
            +-------------------------+
                          |
                          v
            +-------------------------+
            |  Step 4: Remove Bad     |
            |  Comps (Outlier Filter) |
            +-------------------------+
                          |
                          v
            +-------------------------+
            |  Step 5: Calculate      |
            |  Value                  |
            |  Median (60%) +         |
            |  Sqft Method (40%)      |
            +-------------------------+
                          |
                          v
            +-------------------------+
            |  Step 6: Confidence     |
            |  & Investment Flag      |
            +-------------------------+
                          |
                          v
                  YOUR OUTPUT CSV
            (Ready to review or share)
```

---

*End of Report*
