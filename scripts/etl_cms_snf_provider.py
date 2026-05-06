#!/usr/bin/env python3
"""
Carepriced operator-pricing ETL — CMS SNF provider data + state-anchored rates.

This is the v0 ingestion path that produces operator-level SNF records by
combining CMS Skilled Nursing Facility QRP Provider Data (per-facility
metadata) with Genworth/CareScout state-level rate anchors (per-state
median monthly rate). Output records validate against
data/operator-record.schema.json.

v0 vs Phase 1-full:
  - v0 (this script): facility metadata per record + state-level rate band
    with metro tier multiplier. Each record is flagged
    metadata.dataQuality = "v0-state-anchored".
  - Phase 1-full (next): replaces rate band with per-facility numbers
    derived from CMS Form 2540-24 cost report (Worksheet S-3 day-cost
    extraction). Promotes records to dataQuality = "v1-cost-report".

Usage:
  python etl_cms_snf_provider.py --state NC --out data/pilots/nc-snf-pilot.json
  python etl_cms_snf_provider.py --state ALL --out data/snapshots/snf-all-{YYYY-MM}.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

CMS_QRP_DATASET_ID = "fykj-qjee"
CMS_DATASET_INDEX = (
    "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items?title=nursing%20home"
)

# Genworth / CareScout 2024 SNF state median monthly rates (USD).
# Source: https://www.carescout.com/cost-of-care
# Update this table when refreshing state-level anchors annually.
STATE_SNF_MEDIANS = {
    "AK": {"low": 16800, "median": 19300, "high": 23500},
    "AL": {"low": 6900, "median": 7800, "high": 9700},
    "AR": {"low": 6200, "median": 7100, "high": 8500},
    "AZ": {"low": 7400, "median": 8800, "high": 10800},
    "CA": {"low": 9500, "median": 11200, "high": 14500},
    "CO": {"low": 7900, "median": 9100, "high": 11000},
    "CT": {"low": 11000, "median": 12500, "high": 15500},
    "DC": {"low": 11200, "median": 13000, "high": 15800},
    "DE": {"low": 9300, "median": 10800, "high": 13000},
    "FL": {"low": 7800, "median": 9100, "high": 11000},
    "GA": {"low": 6700, "median": 7700, "high": 9300},
    "HI": {"low": 11200, "median": 13500, "high": 16800},
    "IA": {"low": 6100, "median": 7300, "high": 9000},
    "ID": {"low": 7200, "median": 8500, "high": 10300},
    "IL": {"low": 6300, "median": 7400, "high": 9200},
    "IN": {"low": 6800, "median": 8000, "high": 9700},
    "KS": {"low": 5900, "median": 6900, "high": 8500},
    "KY": {"low": 6800, "median": 7900, "high": 9500},
    "LA": {"low": 5800, "median": 6700, "high": 8200},
    "MA": {"low": 11500, "median": 13200, "high": 16000},
    "MD": {"low": 9100, "median": 10600, "high": 12800},
    "ME": {"low": 9700, "median": 11200, "high": 13500},
    "MI": {"low": 8800, "median": 10100, "high": 12200},
    "MN": {"low": 9400, "median": 10800, "high": 13200},
    "MO": {"low": 5500, "median": 6500, "high": 8000},
    "MS": {"low": 6300, "median": 7300, "high": 8900},
    "MT": {"low": 7800, "median": 9000, "high": 10800},
    "NC": {"low": 6800, "median": 8500, "high": 11200},
    "ND": {"low": 9800, "median": 11500, "high": 13800},
    "NE": {"low": 6800, "median": 7900, "high": 9500},
    "NH": {"low": 10500, "median": 12300, "high": 14800},
    "NJ": {"low": 10500, "median": 12100, "high": 14500},
    "NM": {"low": 7400, "median": 8500, "high": 10300},
    "NV": {"low": 8500, "median": 9800, "high": 11800},
    "NY": {"low": 11500, "median": 13500, "high": 16500},
    "OH": {"low": 7100, "median": 8300, "high": 10000},
    "OK": {"low": 5500, "median": 6500, "high": 8000},
    "OR": {"low": 9300, "median": 10800, "high": 13000},
    "PA": {"low": 8800, "median": 10300, "high": 12500},
    "RI": {"low": 9700, "median": 11200, "high": 13500},
    "SC": {"low": 7000, "median": 8200, "high": 10000},
    "SD": {"low": 6800, "median": 7900, "high": 9500},
    "TN": {"low": 6500, "median": 7600, "high": 9200},
    "TX": {"low": 5500, "median": 6500, "high": 8000},
    "UT": {"low": 6900, "median": 8000, "high": 9800},
    "VA": {"low": 7900, "median": 9200, "high": 11200},
    "VT": {"low": 9800, "median": 11500, "high": 13800},
    "WA": {"low": 9500, "median": 11000, "high": 13500},
    "WI": {"low": 8200, "median": 9500, "high": 11500},
    "WV": {"low": 8800, "median": 10100, "high": 12200},
    "WY": {"low": 7000, "median": 8200, "high": 10000},
}

# Per-state metro tier maps. Cities not listed default to D-rural (0.92x).
METRO_TIERS = {
    "NC": {
        "A": {"CHARLOTTE", "RALEIGH", "CARY", "APEX", "HUNTERSVILLE", "MATTHEWS", "MORRISVILLE"},
        "B": {"ASHEVILLE", "WILMINGTON", "GREENSBORO", "WINSTON-SALEM", "WINSTON SALEM", "DURHAM", "CHAPEL HILL", "CONCORD"},
        "C": {"GREENVILLE", "FAYETTEVILLE", "HIGH POINT", "ROCKY MOUNT", "GASTONIA", "HICKORY", "BURLINGTON", "JACKSONVILLE", "KANNAPOLIS", "SALISBURY", "MONROE", "THOMASVILLE", "GOLDSBORO", "NEW BERN"},
    },
}
TIER_MULTIPLIER = {"A": 1.15, "B": 1.05, "C": 1.00, "D": 0.92}


def slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return re.sub(r"-+", "-", text)


def title_case(name: str) -> str:
    if not name:
        return name
    return " ".join(
        w.capitalize() if not (re.match(r"^[A-Z]{2,}$", w) and len(w) <= 4) else w
        for w in name.split()
    )


def metro_tier(state: str, city: str) -> str:
    cu = (city or "").upper().strip()
    tiers = METRO_TIERS.get(state, {})
    for letter in ("A", "B", "C"):
        if cu in tiers.get(letter, set()):
            return letter
    return "D"


def fetch_cms_qrp_csv_url() -> str:
    with urllib.request.urlopen(CMS_DATASET_INDEX, timeout=60) as resp:
        index = json.load(resp)
    for entry in index:
        if entry.get("identifier") == CMS_QRP_DATASET_ID:
            for dist in entry.get("distribution", []):
                url = dist.get("downloadURL")
                if url and url.endswith(".csv"):
                    return url
    raise RuntimeError(f"Could not resolve download URL for dataset {CMS_QRP_DATASET_ID}")


def stream_unique_facilities(csv_url: str, state_filter: str | None):
    facilities: "OrderedDict[str, dict]" = OrderedDict()
    with urllib.request.urlopen(csv_url, timeout=300) as resp:
        text = io.TextIOWrapper(resp, encoding="utf-8", newline="")
        reader = csv.DictReader(text)
        for row in reader:
            state = row.get("State")
            if state_filter and state_filter != "ALL" and state != state_filter:
                continue
            ccn = row.get("CMS Certification Number (CCN)")
            if ccn and ccn not in facilities:
                facilities[ccn] = {
                    "ccn": ccn,
                    "name": row.get("Provider Name"),
                    "address": row.get("Address Line 1"),
                    "city": row.get("City/Town"),
                    "state": state,
                    "zip": row.get("ZIP Code"),
                    "county": row.get("County/Parish"),
                    "phone": row.get("Telephone Number"),
                }
    return list(facilities.values())


def emit_record(facility: dict, now_iso: str, today: str) -> dict:
    state = facility["state"]
    medians = STATE_SNF_MEDIANS.get(state)
    if not medians:
        return None  # state not in v0 anchor table; skip
    tier = metro_tier(state, facility["city"])
    mult = TIER_MULTIPLIER[tier]
    rate = {
        "low": int(round(medians["low"] * mult / 50) * 50),
        "median": int(round(medians["median"] * mult / 50) * 50),
        "high": int(round(medians["high"] * mult / 50) * 50),
        "currency": "USD",
    }
    name_clean = title_case(facility["name"])
    op_id = (slugify(name_clean)[:80] or f"snf-{facility['ccn']}").lower()
    record = {
        "operatorId": f"{op_id}-{facility['ccn'].lower()}",
        "operatorName": name_clean,
        "location": {
            "city": title_case(facility["city"]),
            "state": state,
            "zip": facility["zip"],
        },
        "careType": "skilled-nursing",
        "monthlyRate": rate,
        "hourlyRate": None,
        "levelOfCareModifier": {"medium": 1.15, "high": 1.40},
        "lastVerified": today,
        "sources": [
            {
                "type": "carescout-genworth",
                "url": "https://www.carescout.com/cost-of-care",
                "retrievedAt": now_iso,
                "notes": (
                    f"{state} state-level SNF median anchor (Genworth/CareScout 2024); "
                    f"metro tier {tier} multiplier {mult:.2f} applied based on city ({facility['city']})."
                ),
            }
        ],
        "metadata": {
            "ccn": facility["ccn"],
            "county": facility["county"],
            "phone": facility["phone"],
            "address1": facility["address"],
            "cmsProviderDataUrl": "https://data.cms.gov/provider-data/dataset/fykj-qjee",
            "cmsProviderDataReference": f"CMS QRP CCN {facility['ccn']}",
            "dataQuality": "v0-state-anchored",
            "dataQualityNotes": (
                "Pilot v0: facility metadata verified per-record from CMS QRP. "
                "Monthly rate is state-level (Genworth/CareScout median) with metro tier adjustment, "
                "NOT per-facility cost-report-derived. Phase 1-full will replace rates with CMS 2540-24 "
                "cost-report-derived per-facility numbers."
            ),
            "metroTier": tier,
        },
    }
    return record


def build_index(state: str, records: list[dict], now_iso: str, today: str) -> dict:
    medians = STATE_SNF_MEDIANS.get(state, {})
    return {
        "state": state,
        "careType": "skilled-nursing",
        "updatedAt": now_iso,
        "recordCount": len(records),
        "rateSummary": {
            "state": state,
            "careType": "skilled-nursing",
            "monthlyMedian": medians.get("median"),
            "monthlyLow": medians.get("low"),
            "monthlyHigh": medians.get("high"),
            "currency": "USD",
            "asOf": today,
            "sourceType": "carescout-genworth",
        },
        "operators": [
            {
                "operatorId": r["operatorId"],
                "operatorName": r["operatorName"],
                "city": r["location"]["city"],
                "zip": r["location"]["zip"],
                "monthlyMedian": r["monthlyRate"]["median"],
            }
            for r in records
        ],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", default="NC", help="2-letter state code, or ALL")
    p.add_argument("--out", default="data/pilots/nc-snf-pilot.json", help="Output manifest path")
    p.add_argument("--index", help="Optional output index path")
    args = p.parse_args()

    state = args.state.upper()
    if state != "ALL" and state not in STATE_SNF_MEDIANS:
        sys.exit(f"State {state} not in v0 anchor table — extend STATE_SNF_MEDIANS first.")

    print(f"Resolving CMS SNF QRP CSV URL...", file=sys.stderr)
    csv_url = fetch_cms_qrp_csv_url()
    print(f"  {csv_url}", file=sys.stderr)

    print(f"Streaming unique facilities for state={state}...", file=sys.stderr)
    facilities = stream_unique_facilities(csv_url, state if state != "ALL" else None)
    print(f"  {len(facilities)} unique facilities", file=sys.stderr)

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    today = datetime.now(timezone.utc).date().isoformat()
    records = [r for r in (emit_record(f, now_iso, today) for f in facilities) if r]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "pilot": f"{state}-SNF-v0",
        "generatedAt": now_iso,
        "recordCount": len(records),
        "methodology": (
            "Facility metadata: CMS Skilled Nursing Facility QRP Provider Data (live download). "
            "Rate band: Genworth/CareScout 2024 state median, with metro tier multipliers (A=1.15, B=1.05, C=1.00, rural=0.92). "
            "Each record carries metadata.dataQuality=\"v0-state-anchored\" until CMS 2540-24 cost-report ETL replaces with per-facility numbers in Phase 1-full."
        ),
        "records": records,
    }
    out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path} ({len(records)} records)", file=sys.stderr)

    if args.index and state != "ALL":
        idx_path = Path(args.index)
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        idx_path.write_text(
            json.dumps(build_index(state, records, now_iso, today), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Wrote {idx_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
