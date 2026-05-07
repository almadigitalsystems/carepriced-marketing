# Carepriced Operator Pricing Data — v1 Schema Spec

**Status:** v1 rev 2 — **LOCKED 2026-05-06** per [ALM-5482](/ALM/issues/ALM-5482) (Kira's calculator-render answers)
**Owner:** Emily (CTO)
**Consumers:** Kira (KB), Apollo (CVCO — page rendering)
**Schema file:** `operator-record.schema.json` (JSON Schema 2020-12)

## Purpose

Single source of truth for the per-operator pricing records that power the
Carepriced KB calculator pages and Priority 1 page rollout (50 states × 5
care types). Every record is citation-stamped and refreshable monthly.

## Storage shape

```
operator-records/
  <state-lower>/
    <zip>/
      <operator-id>.json          # current verified record (matches schema)
      <operator-id>.history/      # monthly snapshots
        2026-05.json
        2026-06.json
        ...
```

**v1 binding:** Cloudflare R2 bucket `carepriced-operator-records`, public
read via Worker proxy. KV namespace `carepriced-operator-index` for fast
lookup by `state:zip:careType`.

When `api.carepriced.com` is bound (waiting on [ALM-5520](/ALM/issues/ALM-5520)),
records are queryable at `https://api.carepriced.com/operators/<state>/<zip>`
and `https://api.carepriced.com/operators/<state>/<careType>`.

## Record fields

See `operator-record.schema.json` for the authoritative spec. Summary:

| Field | Required | Notes |
|---|---|---|
| `operatorId` | yes | kebab-case slug, unique within `state:zip` |
| `operatorName` | yes | public-facing name |
| `location.{city,state,zip}` | yes | state = 2-letter upper, zip = 5-digit |
| `location.{lat,lng}` | optional | filled when CMS or operator-site provides it |
| `careType` | yes | enum: IL / AL / MC / SNF / home-care |
| `monthlyRate.{low,median,high}` | required for IL/AL/MC/SNF | USD |
| `hourlyRate.{low,median,high}` | required for home-care | USD |
| `levelOfCareModifier` | optional | **flat array** of `{key, label, multiplier}` objects (rev 2) |
| `lastVerified` | yes | ISO date — displayed on every KB page |
| `sources[]` | yes (≥1) | citation chain, priority-ordered |

## levelOfCareModifier (rev 2 — array shape)

Replaced the prior object-map (`{ "medium": 1.15, "high": 1.40 }`) with an
ordered array. Calculator renders these as radio options in document order,
which the object map could not guarantee across legacy JSON parsers.

```json
"levelOfCareModifier": [
  { "key": "medium", "label": "Higher needs (mobility / ADL help)", "multiplier": 1.15 },
  { "key": "high",   "label": "Memory care / extensive ADL",        "multiplier": 1.40 }
]
```

- `key` — stable dev identifier (kebab-case). Used in events/analytics.
- `label` — UX copy rendered to users in the calculator.
- `multiplier` — applied to `monthlyRate.median` (or `hourlyRate.median`
  for home-care) when the user selects this option.
- The implicit **baseline (multiplier 1.0)** is added by the calculator UI
  as a "Standard care" option *above* this array. Records do not include it.
- Future-proofs care-type-specific levels (e.g. home-care `companion` /
  `personal-care` / `skilled`) without breaking the shape.

**Migration cost:** one full ETL re-emit (NC SNF pilot already re-emitted
on schema lock day; future state pilots emit the new shape natively).

## Care types

The 5 v1 enum values:

- `independent-living`
- `assisted-living`
- `memory-care`
- `skilled-nursing`
- `home-care`

**Multi-type operators emit one record per care type.** A single facility
that offers IL + AL + MC produces three records keyed by the same
`operatorId` but distinct `careType`. This keeps queries clean and lets
KB calculator pages render per-care-type pricing without joining.

## Source priority (waterfall)

Records merge sources in priority order. First hit wins for the rate
fields; all hits stamp the `sources[]` array.

1. `cms-2540-24` — public CMS cost reports (SNF only). Most authoritative for SNF.
2. `licensing-filing` — state long-term-care licensing portals (public records).
3. `operator-website` — direct from operator (rate sheets, brochure PDFs).
4. `nic-map` — NIC MAP licensed data (only if Roman approves cost — see
   side-door note in [ALM-5482](/ALM/issues/ALM-5482)).
5. `carescout-genworth` — state-level fallback already used by [ALM-5486](/ALM/issues/ALM-5486).
   Powers KB pages when no operator-level record exists yet.

## Refresh cadence

Cloudflare Workers cron, monthly (1st of month, 02:00 UTC):

- CMS 2540-24: re-fetch quarterly drop, diff, emit deltas.
- Licensing filings: re-fetch per state, diff, emit deltas.
- Operator websites: spread across 30 days (≈ 1/30th of operators per day).
- NIC MAP: per their API SLA, when/if licensed.

`lastVerified` updates on every successful re-confirmation, even if rates
are unchanged. The history snapshot folder retains the previous month's
record for trend lines.

## Pilot dataset

NC SNF dataset — **418 facilities** — first deliverable off Phase 1
(shipped 2026-05-06, 4 days early on the May 10 commitment).

NC chosen because:
- Sun Belt growth + M&A pocket Bruno flagged in [ALM-5457](/ALM/issues/ALM-5457).
- Mid-difficulty per Apollo's note.
- CMS 2540-24 covers all SNF facilities, so no scraping needed for v1.

Kira can build the calculator template against the pilot dataset shape and
expand as additional states ship.

## v1 questions — locked answers (Kira, 2026-05-06 in [ALM-5482](/ALM/issues/ALM-5482))

| Question | Lock |
|---|---|
| `levelOfCareModifier` shape | **Flat array** of `{key, label, multiplier}` (above). |
| `monthlyRate` granularity | `low / median / high` is sufficient on the operator record. `p10/p25/p50/p75/p90` percentile bands ship on the **state-level `rateSummary`** — see below. |
| Home-care monthly | **Hourly only on the record.** Calculator projects monthly = `hourlyRate.median × hoursPerWeek × 4.33`. `monthlyRate` stays `null` for home-care. |

## rateSummary (state index) — percentile bands LIVE

Each `data/indexes/<state>-<careType>-index.json` now ships percentile bands
in `rateSummary`. Calculator pages can render "you're in the top X% of
state SNF costs" framing today — a real differentiator vs.
SeniorLiving.org's static state-median copy.

Three quality tiers are stamped explicitly in `rateSummary.percentileBandSource`:

| Tier | `percentileBandSource` | What the bands mean | Status |
|---|---|---|---|
| v0.5-percentile-anchored | `v0-record-distribution` | Empirical p10/p25/p50/p75/p90 derived from the distribution of per-record `monthlyRate.median` values. Reflects v0 state-anchored + metro-tier inputs — NOT real cost-report data. | **shipped 2026-05-07** for NC SNF pilot |
| v1-cost-report | `cms-2540-24` | Empirical percentiles from the real per-facility distribution after CMS Form 2540-24 cost-report ingestion replaces the per-record monthly rate with worksheet-derived numbers. | Phase 1-full — separate ETL (`scripts/etl_cms_costreport.py`), in flight |
| v0-state-only (legacy) | absent | Index ships only `monthlyMedian / monthlyLow / monthlyHigh` — no bands. | superseded by v0.5 above for NC; still applies to states not yet piloted |

Today's NC SNF index (`data/indexes/nc-snf-index.json`):

```json
{
  "monthlyMedian": 8500,
  "monthlyLow": 6800,
  "monthlyHigh": 11200,
  "monthlyP10": 7800,
  "monthlyP25": 7800,
  "monthlyP50": 7800,
  "monthlyP75": 8500,
  "monthlyP90": 8900,
  "currency": "USD",
  "asOf": "2026-05-07",
  "sourceType": "carescout-genworth",
  "percentileBandSource": "v0-record-distribution",
  "percentileBandNotes": "..."
}
```

The narrow lower-band cluster (p10=p25=p50=7800) is a real artifact of the
v0 input distribution — Tier D (rural) facilities are 61% of NC SNFs, all
anchored to the same `state-median × 0.92` rate. When Phase 1-full lands,
the bands spread into a real per-facility distribution and
`percentileBandSource` flips to `cms-2540-24` with no record-ID churn or
schema change for Kira's calculator.

Phase 1-full target shape:

```json
{
  "monthlyMedian": 8500,
  "monthlyLow": 6800,
  "monthlyHigh": 11200,
  "monthlyP10": 6500,
  "monthlyP25": 7400,
  "monthlyP50": 8500,
  "monthlyP75": 9800,
  "monthlyP90": 11000,
  "currency": "USD",
  "asOf": "2026-XX-XX",
  "sourceType": "cms-2540-24",
  "percentileBandSource": "cms-2540-24"
}
```

## Home-care monthly projection (calculator-side formula — locked)

Records carry `hourlyRate.{low, median, high}` and `monthlyRate = null`.
The calculator computes monthly inline from user-selected hours/week:

```
monthlyEstimate = hourlyRate.median × hoursPerWeek × 4.33
```

`4.33` = average weeks per month. Kira's KB templates and any other
calculator embed must use the same constant so the math is consistent.

