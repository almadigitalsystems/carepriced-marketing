# Carepriced Operator Pricing Data â€” v1 Schema Spec

**Status:** Phase 0 spec lock â€” May 6 2026
**Owner:** Emily (CTO)
**Consumers:** Kira (KB), Apollo (CVCO â€” page rendering)
**Schema file:** `operator-record.schema.json` (JSON Schema 2020-12)

## Purpose

Single source of truth for the per-operator pricing records that power the
Carepriced KB calculator pages and Priority 1 page rollout (50 states Ã— 5
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
| `levelOfCareModifier` | optional | object: `{ "medium": 1.15, "high": 1.40 }` |
| `lastVerified` | yes | ISO date â€” displayed on every KB page |
| `sources[]` | yes (â‰¥1) | citation chain, priority-ordered |

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

1. `cms-2540-24` â€” public CMS cost reports (SNF only). Most authoritative for SNF.
2. `licensing-filing` â€” state long-term-care licensing portals (public records).
3. `operator-website` â€” direct from operator (rate sheets, brochure PDFs).
4. `nic-map` â€” NIC MAP licensed data (only if Roman approves cost â€” see
   side-door note in [ALM-5482](/ALM/issues/ALM-5482)).
5. `carescout-genworth` â€” state-level fallback already used by [ALM-5486](/ALM/issues/ALM-5486).
   Powers KB pages when no operator-level record exists yet.

## Refresh cadence

Cloudflare Workers cron, monthly (1st of month, 02:00 UTC):

- CMS 2540-24: re-fetch quarterly drop, diff, emit deltas.
- Licensing filings: re-fetch per state, diff, emit deltas.
- Operator websites: spread across 30 days (â‰ˆ 1/30th of operators per day).
- NIC MAP: per their API SLA, when/if licensed.

`lastVerified` updates on every successful re-confirmation, even if rates
are unchanged. The history snapshot folder retains the previous month's
record for trend lines.

## Pilot dataset (Kira, May 10 EOD)

NC SNF dataset â€” ~400 facilities â€” first deliverable off Phase 1. NC chosen
because:

- Sun Belt growth + M&A pocket Bruno flagged in [ALM-5457](/ALM/issues/ALM-5457).
- Mid-difficulty per Apollo's note.
- CMS 2540-24 covers all SNF facilities, so no scraping needed for v1.

Kira can build the calculator template against the pilot dataset shape and
expand as additional states ship.

## Open questions for Kira

1. Does `levelOfCareModifier` rendering plan match this object shape, or do
   you want a flat array of `{ label, multiplier }` for easier iteration?
2. Is `monthlyRate.{low,median,high}` enough granularity, or do you need
   percentile bands (e.g., `p10, p25, p50, p75, p90`)?
3. For home-care, do you want `hourlyRate` plus an implied `monthlyRate`
   (4 hr/day Ã— 30 days projection), or hourly only with a calculator-side
   projection?

Drop answers in [ALM-5482](/ALM/issues/ALM-5482) and I'll lock the schema
revision before Phase 1 kicks off.
