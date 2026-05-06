# Carepriced Tier 1 spoke page template

This file documents the static-HTML spoke page template used by `carepriced.com`. The first reference implementation is live at `/az/phoenix/assisted-living/index.html`.

Every Tier 1 page must implement all five elements described below. The Phoenix AL page is the canonical sample — copy it, swap the metadata, swap Clive's content, push.

## 1. Page metadata schema (Clive frontmatter contract)

Each page is a single self-contained `index.html`. Clive's drafts must supply the values listed here so Apollo's mass-renderer (or a manual copy-and-edit pass) can populate the template.

```yaml
slug:                "az/phoenix/assisted-living"   # = URL path under carepriced.com/
state:               { slug: "az", name: "Arizona" }
metro:               { slug: "phoenix", name: "Phoenix" }
careType:            "assisted-living"              # one of: assisted-living | memory-care | independent-living | in-home-care | nursing-home
honestMedian:        5200                            # USD/month, single integer (no range)
allInRange:          { low: 5800, high: 6400 }       # realistic monthly with typical add-ons
sources:                                             # exactly 3 published cost surveys
  - { name: "Genworth Cost of Care Survey", year: 2023, value: 5250, note: "survey discontinued in 2024" }
  - { name: "CareScout Cost of Care Report", year: 2024, value: 5180 }
  - { name: "SeniorLiving.org regional cost report", year: 2024, value: 5150 }
subAreas:                                            # 3-5 neighborhoods with price ranges
  - { name: "Scottsdale and North Scottsdale", low: 5800, high: 7200, note: "Higher land cost, newer construction." }
  - { name: "Central Phoenix and Arcadia", low: 5000, high: 6200 }
  - { name: "Mesa, Tempe, Chandler (East Valley)", low: 4600, high: 5600 }
  - { name: "Glendale, Peoria, Surprise (West Valley)", low: 3800, high: 4900 }
addOns:                                              # care-level breakdown
  - { name: "Care-level fee", low: 400, high: 1400 }
  - { name: "Medication management", low: 200, high: 400 }
  - { name: "Incontinence supplies and care", low: 100, high: 300 }
  - { name: "Two-person transfer", low: 300, high: 600 }
  - { name: "Community fee (one-time)", low: 1500, high: 5000, oneTime: true }
medicaid:                                            # state-specific Medicaid waiver
  programName:        "ALTCS"
  programLongName:    "Arizona Long Term Care System"
  assetLimit:         2000
  incomeLimit:        2901
  personalAllowance:  138.20
  agencyUrl:          "https://www.azahcccs.gov/Members/AlreadyCovered/altcs.html"
nearbyMetros:                                        # 3 same-care-type spokes in adjacent metros
  - { slug: "az/tucson/assisted-living", name: "Tucson, AZ" }
  - { slug: "nv/las-vegas/assisted-living", name: "Las Vegas, NV" }
  - { slug: "ca/los-angeles/assisted-living", name: "Los Angeles, CA" }
siblingSpokes:                                       # 4 other care types in the same metro
  - { slug: "az/phoenix/memory-care", label: "Memory care in Phoenix" }
  - { slug: "az/phoenix/independent-living", label: "Independent living in Phoenix" }
  - { slug: "az/phoenix/in-home-care", label: "In-home care in Phoenix" }
  - { slug: "az/phoenix/nursing-home", label: "Skilled nursing in Phoenix" }
hubUrl:              "/az/phoenix/"                  # breadcrumb up to metro hub
affiliateSlots:      { primary: true, inline: true, secondary: true }
dateModified:        "2026-05-06"
```

## 2. The 8-section structure (Tigger ALM-5436, brand-voice approved)

H1 → lede (honest median in dollars, no range, no "starting at").

| # | H2 heading | Content |
|---|------------|---------|
| 1 | Lede (no H2 — uses H1) | One-paragraph honest median + structural advisor positioning |
| 2 | What three published sources say about {care} in {metro} | 3-row source table, with Genworth-discontinued caveat where relevant |
| 3 | {Metro} sub-area pricing — what changes by neighborhood | 3-5 H3 sub-area blocks, each with low-high range |
| 4 | What pushes your {metro} {care} bill above the median | Add-ons bullet list with dollar ranges, ending with "realistic median + likely add-ons" summary |
| 5 | {State} {Medicaid program name}: who qualifies and how it works | Eligibility, financial limits, agency URL, ALTCS-style empathy line |
| 6 | The all-in monthly worksheet — what's actually in your bill | Printable 10-row table family fills in on tour |
| 7 | Six questions to ask when you tour a {metro} {care} community | Numbered list, ends with "if they won't itemize..." signal line |
| 8 | Sources cited on this page | Bulleted external link list with `rel="noopener"` |

## 3. Schema markup (JSON-LD)

Four mandatory `<script type="application/ld+json">` blocks in `<head>`, in this order:

1. **`Service`** — `serviceType`, `provider` (Carepriced Org), `areaServed` (City containedInPlace State), `offers` as `AggregateOffer` with `lowPrice`, `highPrice`, and an inner `UnitPriceSpecification` for the median (`unitText: "MONTH"`).
2. **`Place`** — Metro as `Place` with `containedInPlace` State + `geo` coordinates.
3. **`BreadcrumbList`** — exactly 4 items: Home → State → Metro → Care type (last item has no `item` URL because it's the current page).
4. **`FAQPage`** — minimum 5 Q/A pairs derived from the worksheet section. Use the Phoenix AL set as the question template, swap state-specific facts (Medicaid program name, base monthly).

## 4. Internal linking — hub-and-spoke (Kira ALM-5430)

Every spoke page renders an `<aside class="related">` block with three computed lists:

- **Sibling spokes:** 4 other care types in the same metro (rendered from `siblingSpokes` metadata).
- **Nearby-metro same-care:** 3 same-care-type spokes in adjacent metros (rendered from `nearbyMetros` metadata, computed from the Tier 1 + Tier 2 CSV).
- **Hub link:** "↑ All {Metro} care options" → `hubUrl`.

Breadcrumb (top of page) is the BreadcrumbList JSON-LD's HTML mirror.

Apollo's mass-renderer reads Kira's CSV (`carepriced_keywords.csv`) and Apollo's link-graph JSON (output of ALM-5430) and computes `siblingSpokes` + `nearbyMetros` automatically. Clive does NOT hand-curate these; Apollo emits the metadata with the link-graph populated.

## 5. Affiliate-link slots (Berenice clears positioning ~May 9)

Three empty slot containers, in fixed positions:

```html
<div data-affiliate-slot="primary"></div>     <!-- below CTA row, after lede -->
<div data-affiliate-slot="inline"></div>      <!-- between Section 4 (add-ons) and Section 5 (Medicaid) -->
<div data-affiliate-slot="secondary"></div>   <!-- after Section 7 (touring questions) -->
```

CSS hides empty slots (`[data-affiliate-slot]:empty { display: none; }`) so unfilled slots don't introduce whitespace. Once Berenice clears positioning, Clive populates the `innerHTML` of each slot with the approved widget/link block per page.

## 6. Build & deploy

The static site is served directly from the repo root by GitHub Pages — no build step. To ship a new Tier 1 page:

1. Compose the page HTML by copying `/az/phoenix/assisted-living/index.html` to `{slug}/index.html` and swapping the metadata-driven content.
2. Push to `main` via the GitHub Contents API (single file) or Git Database API (batch tree commit for multi-page batches — preferred for ≥10 pages, see `learnings/2026-05-06-tree-commit-pattern.md`).
3. Verify HTTP 200 at `https://carepriced.com/{slug}/` after ~30-60s GitHub Pages deploy.
4. Sitemap: append the new URL to `/sitemap.xml` (or regenerate from the Tier 1 CSV) and ping Google Search Console.

## 7. Brand-voice gates (Tigger sign-off)

Every Tier 1 page MUST read like Phoenix AL:

- Honest single-number median in the lede, never a range.
- Source disagreement framed as proof ("agree within 6%"), not noise.
- Add-ons named explicitly with dollar amounts ("Med management $200–$400").
- Touring questions arm the family — they don't position Carepriced as data-oracle.
- Medicaid section is empathetic, not transactional.
- "If they won't itemize, that's a signal worth weighing" — keep this construction or a close variant.

Tigger reviews the first page per metro before the remaining 4 sibling spokes for that metro ship.

## 8. Open items

- Apollo: confirm the link-graph JSON output format matches the metadata `siblingSpokes` + `nearbyMetros` schema above (or post the actual format on ALM-5430 and we'll adjust).
- Berenice: affiliate slot positioning clears May 9 → Clive populates Mon May 11.
- Clive: confirm any additional metadata fields needed for the memory-care variant (we'll likely need a `dementiaResources` block in Section 5 alongside Medicaid).
- Sitemap automation deferred until 50+ pages live.
