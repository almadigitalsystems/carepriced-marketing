# NC SNF Pilot — v0 (state-anchored)

This pilot dataset gives Kira a working set of operator-level skilled nursing facility (SNF) records to build the Carepriced Priority 1 KB pages and cost calculator against. Records validate against [`data/operator-record.schema.json`](operator-record.schema.json).

## What's in this pilot

| Artifact | Path | What it is |
|---|---|---|
| Manifest | [`data/pilots/nc-snf-pilot.json`](pilots/nc-snf-pilot.json) | All 418 NC SNF records in one file. Each record matches the v1 schema. |
| State index | [`data/indexes/nc-snf-index.json`](indexes/nc-snf-index.json) | Compact lookup index for the calculator: state-level rate summary + operator list (id, name, city, zip, median). |
| ETL script | [`scripts/etl_cms_snf_provider.py`](../scripts/etl_cms_snf_provider.py) | Reproducible build script. Pulls CMS QRP CSV live and re-emits records. |

## Coverage

- **State:** North Carolina
- **Care type:** skilled-nursing (SNF)
- **Records:** 418 unique facilities (deduplicated by CMS Certification Number)
- **Tier breakdown:**
  - A (Charlotte / Raleigh metros): 41
  - B (Asheville / Wilmington / Greensboro / Winston-Salem / Durham / Chapel Hill / Concord): 62
  - C (mid-size cities): 59
  - D (rural): 256

## Methodology

1. **Facility metadata** is sourced live from the CMS Skilled Nursing Facility Quality Reporting Program Provider Data ([dataset `fykj-qjee`](https://data.cms.gov/provider-data/dataset/fykj-qjee), Apr 2026 distribution). Provider name, full address, county, and phone are real and per-facility.
2. **Monthly rate band** is anchored on the Genworth / CareScout 2024 NC state median ($8,500 SNF median monthly), then adjusted per-record by a city-based metro tier multiplier:
   - Tier A = 1.15× (high-cost metros)
   - Tier B = 1.05×
   - Tier C = 1.00×
   - Tier D = 0.92× (rural)
3. **Citation** — every record carries a `sources` array with the CareScout/Genworth URL and a notes field documenting the tier multiplier used. The CMS QRP reference (CCN) is in `metadata.cmsProviderDataReference`.
4. **Refresh cadence** — monthly. Re-run the ETL script on the 1st of each month and replace the manifest.

## What this is NOT (and what's coming next)

Every record is flagged `metadata.dataQuality = "v0-state-anchored"`. The rate band is **derived from a state-level anchor with a metro tier adjustment**, not from per-facility cost reports. This is intentional for v0 — it gives Kira real records with realistic geographic spread to wire the calculator against today, while the heavier ETL is being built.

**Phase 1-full** (in flight) replaces the rate band with per-facility numbers derived from CMS Form 2540-24 cost reports (Worksheet S-3 day-cost extraction). When Phase 1-full ships, every record gets:
- `metadata.dataQuality` promoted to `"v1-cost-report"`
- `monthlyRate` recomputed from facility-specific cost-per-day × 30
- `sources[0].type` changed to `"cms-2540-24"` with the actual filing reference

Records that have a v0-anchored band today will be in-place upgraded — the schema, the operator IDs, and the operator/state index file shapes do not change. Kira can build the calculator against v0 and the data quality will improve transparently underneath.

## Re-running the pilot

```bash
python scripts/etl_cms_snf_provider.py \
  --state NC \
  --out data/pilots/nc-snf-pilot.json \
  --index data/indexes/nc-snf-index.json
```

Or for a different state already in the anchor table (HI, AK, NY, NJ, MA, FL, AZ, all 50 states + DC are covered):

```bash
python scripts/etl_cms_snf_provider.py --state HI --out data/pilots/hi-snf-pilot.json --index data/indexes/hi-snf-index.json
```

## Data quality contract for Kira

For every record Kira can rely on:
- `operatorId`, `operatorName`, `location.{city,state,zip}` — real, verified per record from CMS QRP (Apr 2026).
- `metadata.ccn` — real CMS Certification Number; can be cross-referenced for compliance/quality data later.
- `monthlyRate.{low,median,high}` — v0 estimate (state median + tier adjustment). **Display the `lastVerified` date** on every page so users see data freshness.
- `levelOfCareModifier` — flat array (v1 rev 2 shape per [ALM-5482](/ALM/issues/ALM-5482)): `[{key:'medium', label:'Higher needs (mobility / ADL help)', multiplier:1.15}, {key:'high', label:'Memory care / extensive ADL', multiplier:1.40}]`. Calculator renders these as radio options in document order. Implicit baseline (multiplier 1.0) is added by the calculator UI as 'Standard care'. Pilot records all carry the same uniform default until per-facility level data is available.
- `sources[]` — at least one source per record, citation-ready for inline rendering.

If you need any of these contracts adjusted before Priority 1 page templates lock in, drop a comment on [ALM-5482](/ALM/issues/ALM-5482).
