#!/usr/bin/env python3
"""
Carepriced operator-pricing ETL — Phase 1-full (CMS HCRIS 2540 cost-report).

Replaces v0 state-anchored monthlyRate values with per-facility numbers
derived from CMS Healthcare Cost Report Information System (HCRIS) public
use files for skilled nursing facilities (Form CMS-2540-10 and CMS-2540-24).

Output: an updated manifest where every record that has a matching cost
report gets:
  - monthlyRate.{low, median, high} recomputed from facility-specific data
  - sources[0].type swapped to "cms-2540-24" with the actual filing reference
  - metadata.dataQuality promoted from "v0-state-anchored" to "v1-cost-report"
  - metadata.costReportFiscalYearEnd set to the FY end date of the source filing
  - metadata.perDiemMethod set to whichever extraction method was used

Records without a matching CCN in the cost-report data are passed through
UNCHANGED (still v0-state-anchored). This is intentional — non-Medicare
SNFs don't file 2540 cost reports, so they keep the state anchor until a
licensing-filing parser fills in.

State-level rateSummary (the index file) is rebuilt from the new
distribution by re-running etl_cms_snf_provider.py with --from-manifest
against this script's output. percentileBandSource flips from
v0-record-distribution to cms-2540-24 automatically because the records
themselves carry the upgraded provenance now.

## HCRIS data source

HCRIS public-use files are published quarterly by CMS. Each quarterly
drop is a ZIP containing 3 CSVs per form revision:

  - <FORM>_RPT.csv       Report-level metadata (provider CCN, FY dates)
  - <FORM>_NMRC.csv      Numerical line items (worksheet/line/column → value)
  - <FORM>_ALPHA.csv     Alphanumeric line items (text labels)

For SNF, two form revisions are active:
  - CMS-2540-10 (older revision; most facilities still on this through FY2024)
  - CMS-2540-24 (current revision; facilities transitioning starting FY2024)

Download landing page:
  https://www.cms.gov/data-research/statistics-trends-and-reports/cost-reports/skilled-nursing-facility-2540-10-and-2540-96

Quarterly file naming convention (observed):
  snf10_<YYYY>q<N>.zip  (e.g. snf10_2024q4.zip)
  snf24_<YYYY>q<N>.zip  (e.g. snf24_2024q4.zip)

## Per-diem extraction methodology

The script extracts a per-facility per-diem rate using one of two
methods. Each per-diem becomes monthly rate via perDiem × 30. The
low/median/high band is then computed as median × {0.85, 1.00, 1.18}
to preserve the same band shape as v0 while anchoring the median on the
real cost-report number.

### Method A — `revenue-per-day` (DEFAULT, consumer-relevant)

Total patient revenue divided by total resident days. Reflects what
the average resident actually pays (mix of Medicare, Medicaid, private,
and other payers, weighted by census).

  numerator = Worksheet G-3, Line 3, Column 1   (Total patient revenue)
  denominator = Worksheet S-3 Part I, Line 1, Column 6  (Total resident days)
  per_diem = numerator / denominator

### Method B — `cost-per-day` (fallback, internal-cost view)

Total operating costs divided by total resident days. This is the
facility's *cost*, not price. Useful for cross-validation but not the
default because it doesn't match what a family using the calculator
would actually pay.

  numerator = Worksheet B Part I, Line 100, Column 18   (Total general service costs)
  denominator = Worksheet S-3 Part I, Line 1, Column 6  (Total resident days)
  per_diem = numerator / denominator

If revenue-per-day extraction fails (missing G-3 line 3 col 1 or zero
days), the script falls back to cost-per-day with the metadata flag
adjusted to reflect that.

## Usage

  # one-shot: update the existing NC manifest with cost-report data
  python etl_cms_costreport.py \
      --manifest-in data/pilots/nc-snf-pilot.json \
      --manifest-out data/pilots/nc-snf-pilot.json \
      --hcris-rpt /path/to/snf10_RPT.csv \
      --hcris-nmrc /path/to/snf10_NMRC.csv \
      --hcris-alpha /path/to/snf10_ALPHA.csv

  # then rebuild the index from the updated manifest:
  python etl_cms_snf_provider.py \
      --state NC \
      --from-manifest data/pilots/nc-snf-pilot.json \
      --index data/indexes/nc-snf-index.json

  # preview a single CCN without writing the manifest:
  python etl_cms_costreport.py \
      --inspect-ccn 345001 \
      --hcris-rpt /path/to/snf10_RPT.csv \
      --hcris-nmrc /path/to/snf10_NMRC.csv

The script never downloads from CMS automatically — the HCRIS quarterly
drops are large (~200MB+ per form revision) and CMS download endpoints
are flaky. Operator runs the download once per quarter; the script
re-uses the local extracted CSVs across all per-state ETL jobs.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Worksheet/line/column codes for SNF Form 2540-10 and 2540-24.
# The codes below are the same across both revisions for the line items
# we care about — CMS preserved the worksheet structure on the 2540-24
# revision to maintain HCRIS continuity.
#
# wksht_cd in NMRC.CSV is a 7-character code: <worksheet>_<part>_<subpart>
# padded with spaces. We compare with .strip() to be robust to padding.

WKSHT_S3_PART_I = "S300001"   # Worksheet S-3 Part I — Statistical Data
WKSHT_G3 = "G300000"          # Worksheet G-3 — Statement of Patient Revenues
WKSHT_B_PART_I = "B000001"    # Worksheet B Part I — General Service Costs

LINE_TOTAL_RESIDENT_DAYS = "00100"  # S-3 Part I Line 1 Col 6 — total resident days
COL_TOTAL_RESIDENT_DAYS = "00006"

LINE_TOTAL_PATIENT_REVENUE = "00300"  # G-3 Line 3 Col 1 — total patient revenue
COL_TOTAL_PATIENT_REVENUE = "00001"

LINE_TOTAL_GEN_SVC_COSTS = "10000"  # B Part I Line 100 Col 18 — total general service costs
COL_TOTAL_GEN_SVC_COSTS = "00018"

# Sanity bands. A SNF per-diem outside this range is rejected and the
# record is left at v0-state-anchored. Calibrated to 2024 SNF reality:
# private-pay rates from $200/day (rural Medicaid-heavy) to $700/day
# (urban premium private). Values outside this band almost always indicate
# a worksheet mis-extraction, not a real outlier.
PER_DIEM_MIN = 150.0
PER_DIEM_MAX = 800.0


def load_rpt(rpt_path: Path) -> dict[str, dict]:
    """Load HCRIS RPT.CSV. Returns {ccn: {rpt_rec_num, fy_end_dt, npi, ...}}.

    HCRIS RPT format (no header, position-indexed):
      0: rpt_rec_num
      1: prvdr_ctrl_type
      2: prvdr_num                <- CCN
      3: npi
      4: rpt_stus_cd
      5: fy_bgn_dt
      6: fy_end_dt
      7: proc_dt
      8: initl_rpt_sw
      9: last_rpt_sw
      10: trnsmtl_num
      11: fi_num
      12: adr_vndr_cd
      13: fi_creat_dt
      14: util_cd
      15: npr_dt
      16: spec_ind
      17: fi_rcpt_dt

    For each CCN we keep the most recent successfully-processed report
    (filtered by rpt_stus_cd='F' for "final settled" or 'A' for "amended").
    """
    rpt_by_ccn: dict[str, dict] = {}
    with rpt_path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 7:
                continue
            rpt_rec_num = row[0].strip()
            ccn = row[2].strip()
            stus_cd = row[4].strip().upper() if len(row) > 4 else ""
            fy_end_dt = row[6].strip() if len(row) > 6 else ""
            if not ccn or not rpt_rec_num:
                continue
            # Prefer settled / amended reports. 'I' = initial / unsettled.
            existing = rpt_by_ccn.get(ccn)
            new_rec = {
                "rpt_rec_num": rpt_rec_num,
                "fy_end_dt": fy_end_dt,
                "stus_cd": stus_cd,
                "npi": row[3].strip() if len(row) > 3 else "",
            }
            if existing is None or _is_better_report(new_rec, existing):
                rpt_by_ccn[ccn] = new_rec
    return rpt_by_ccn


def _is_better_report(new: dict, existing: dict) -> bool:
    """Prefer settled (F) > amended (A) > initial (I), then most recent FY end."""
    rank = {"F": 3, "A": 2, "I": 1}
    nr = rank.get(new["stus_cd"], 0)
    er = rank.get(existing["stus_cd"], 0)
    if nr != er:
        return nr > er
    return _date_str_key(new["fy_end_dt"]) > _date_str_key(existing["fy_end_dt"])


def _date_str_key(d: str) -> str:
    """HCRIS dates are MM/DD/YYYY. Convert to ISO for sortable comparison."""
    if not d or "/" not in d:
        return ""
    try:
        m, day, y = d.split("/")
        return f"{y.zfill(4)}-{m.zfill(2)}-{day.zfill(2)}"
    except ValueError:
        return ""


def load_nmrc(nmrc_path: Path, rpt_rec_nums: set[str]) -> dict[str, dict]:
    """Load HCRIS NMRC.CSV restricted to the rpt_rec_nums we care about.

    Returns: {rpt_rec_num: {(wksht_cd, line_num, clmn_num): item_value}}

    HCRIS NMRC format (no header, position-indexed):
      0: rpt_rec_num
      1: wksht_cd
      2: line_num
      3: clmn_num
      4: itm_val_num
    """
    out: dict[str, dict[tuple[str, str, str], float]] = defaultdict(dict)
    with nmrc_path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 5:
                continue
            rpt_rec_num = row[0].strip()
            if rpt_rec_num not in rpt_rec_nums:
                continue
            wksht = row[1].strip()
            line = row[2].strip()
            col = row[3].strip()
            try:
                val = float(row[4])
            except (ValueError, TypeError):
                continue
            out[rpt_rec_num][(wksht, line, col)] = val
    return out


def compute_per_diem(nmrc_data: dict[tuple[str, str, str], float]) -> tuple[float | None, str]:
    """Return (per_diem_dollars, method) or (None, reason) if extraction failed."""
    days = nmrc_data.get((WKSHT_S3_PART_I, LINE_TOTAL_RESIDENT_DAYS, COL_TOTAL_RESIDENT_DAYS))
    if not days or days <= 0:
        return None, "missing-or-zero-resident-days"

    revenue = nmrc_data.get((WKSHT_G3, LINE_TOTAL_PATIENT_REVENUE, COL_TOTAL_PATIENT_REVENUE))
    if revenue and revenue > 0:
        per_diem = revenue / days
        if PER_DIEM_MIN <= per_diem <= PER_DIEM_MAX:
            return per_diem, "revenue-per-day"

    cost = nmrc_data.get((WKSHT_B_PART_I, LINE_TOTAL_GEN_SVC_COSTS, COL_TOTAL_GEN_SVC_COSTS))
    if cost and cost > 0:
        per_diem = cost / days
        if PER_DIEM_MIN <= per_diem <= PER_DIEM_MAX:
            return per_diem, "cost-per-day"

    return None, "no-valid-per-diem-line"


def round_to_50(x: float) -> int:
    return int(round(x / 50) * 50)


def upgrade_record(record: dict, per_diem: float, method: str, rpt: dict, now_iso: str, today: str) -> dict:
    """Apply v1-cost-report data to a v0 record. Returns a new dict."""
    monthly_median = per_diem * 30
    new_rate = {
        "low": round_to_50(monthly_median * 0.85),
        "median": round_to_50(monthly_median),
        "high": round_to_50(monthly_median * 1.18),
        "currency": "USD",
    }

    new_record = dict(record)
    new_record["monthlyRate"] = new_rate
    new_record["lastVerified"] = today

    # Replace the citation chain. The CareScout/Genworth anchor is no
    # longer the primary source for this record; the CMS cost report is.
    ccn = record.get("metadata", {}).get("ccn", "")
    new_record["sources"] = [
        {
            "type": "cms-2540-24",
            "url": "https://www.cms.gov/data-research/statistics-trends-and-reports/cost-reports/skilled-nursing-facility-2540-10-and-2540-96",
            "retrievedAt": now_iso,
            "notes": (
                f"CMS HCRIS cost report (CCN {ccn}, FY ending {rpt.get('fy_end_dt', '?')}, "
                f"status {rpt.get('stus_cd', '?')}). Per-diem ${per_diem:.2f} via "
                f"{method}; monthly = perDiem × 30."
            ),
        }
    ]

    metadata = dict(record.get("metadata", {}))
    metadata["dataQuality"] = "v1-cost-report"
    metadata["dataQualityNotes"] = (
        f"v1-cost-report: monthlyRate.median anchored on the facility's CMS HCRIS "
        f"cost report (Form CMS-2540, FY ending {rpt.get('fy_end_dt', '?')}). "
        f"Per-diem ${per_diem:.2f} computed via {method}. Monthly rate = perDiem × 30; "
        f"low/high band = median × 0.85 / 1.18 to preserve schema shape."
    )
    metadata["costReportFiscalYearEnd"] = rpt.get("fy_end_dt", "")
    metadata["costReportStatusCode"] = rpt.get("stus_cd", "")
    metadata["costReportRptRecNum"] = rpt.get("rpt_rec_num", "")
    metadata["perDiemMethod"] = method
    metadata["perDiemDollars"] = round(per_diem, 2)
    new_record["metadata"] = metadata

    return new_record


def main():
    global PER_DIEM_MIN, PER_DIEM_MAX
    p = argparse.ArgumentParser()
    p.add_argument("--manifest-in", help="Existing v0 manifest path (input)")
    p.add_argument("--manifest-out", help="Updated v1-cost-report manifest path (output)")
    p.add_argument("--hcris-rpt", required=True, help="Path to HCRIS RPT.CSV")
    p.add_argument("--hcris-nmrc", required=True, help="Path to HCRIS NMRC.CSV")
    p.add_argument("--hcris-alpha", help="(Optional) Path to HCRIS ALPHA.CSV — currently unused")
    p.add_argument("--inspect-ccn", help="Print extracted per-diem for one CCN and exit")
    p.add_argument(
        "--per-diem-min", type=float, default=PER_DIEM_MIN,
        help="Reject extracted per-diems below this $/day (default 150)",
    )
    p.add_argument(
        "--per-diem-max", type=float, default=PER_DIEM_MAX,
        help="Reject extracted per-diems above this $/day (default 800)",
    )
    args = p.parse_args()

    PER_DIEM_MIN = args.per_diem_min
    PER_DIEM_MAX = args.per_diem_max

    rpt_path = Path(args.hcris_rpt)
    nmrc_path = Path(args.hcris_nmrc)
    if not rpt_path.exists():
        sys.exit(f"HCRIS RPT.CSV not found at {rpt_path}")
    if not nmrc_path.exists():
        sys.exit(f"HCRIS NMRC.CSV not found at {nmrc_path}")

    print(f"Loading HCRIS RPT from {rpt_path}...", file=sys.stderr)
    rpt_by_ccn = load_rpt(rpt_path)
    print(f"  {len(rpt_by_ccn)} unique CCNs in RPT", file=sys.stderr)

    if args.inspect_ccn:
        ccn = args.inspect_ccn.strip()
        rpt = rpt_by_ccn.get(ccn)
        if not rpt:
            sys.exit(f"CCN {ccn} not found in RPT.csv")
        nmrc = load_nmrc(nmrc_path, {rpt["rpt_rec_num"]})
        nmrc_data = nmrc.get(rpt["rpt_rec_num"], {})
        per_diem, method = compute_per_diem(nmrc_data)
        if per_diem is None:
            print(f"CCN {ccn}: extraction FAILED ({method})")
        else:
            print(
                f"CCN {ccn}: per_diem=${per_diem:.2f} method={method} "
                f"monthly=${per_diem * 30:.0f} fy_end={rpt['fy_end_dt']}"
            )
        return

    if not args.manifest_in or not args.manifest_out:
        sys.exit("--manifest-in and --manifest-out are required when not using --inspect-ccn")

    manifest_in_path = Path(args.manifest_in)
    if not manifest_in_path.exists():
        sys.exit(f"Manifest not found at {manifest_in_path}")

    print(f"Loading manifest from {manifest_in_path}...", file=sys.stderr)
    manifest = json.loads(manifest_in_path.read_text(encoding="utf-8"))
    records = manifest.get("records", [])
    print(f"  {len(records)} records loaded", file=sys.stderr)

    # CCNs we want to look up in NMRC.
    target_ccns = {
        r.get("metadata", {}).get("ccn", "").strip()
        for r in records
        if r.get("metadata", {}).get("ccn")
    }
    target_rpt_rec_nums = {
        rpt_by_ccn[ccn]["rpt_rec_num"]
        for ccn in target_ccns
        if ccn in rpt_by_ccn
    }
    print(
        f"  {len(target_ccns)} unique CCNs in manifest, "
        f"{len(target_rpt_rec_nums)} have an RPT match",
        file=sys.stderr,
    )

    print(f"Loading HCRIS NMRC line items (filtered)...", file=sys.stderr)
    nmrc_by_rpt = load_nmrc(nmrc_path, target_rpt_rec_nums)
    print(f"  {len(nmrc_by_rpt)} reports with NMRC line items", file=sys.stderr)

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    today = datetime.now(timezone.utc).date().isoformat()

    upgraded = 0
    skipped_no_rpt = 0
    skipped_no_perdiem = 0
    failure_reasons: dict[str, int] = defaultdict(int)
    method_counts: dict[str, int] = defaultdict(int)
    per_diems: list[float] = []

    new_records = []
    for record in records:
        ccn = record.get("metadata", {}).get("ccn", "").strip()
        if not ccn:
            new_records.append(record)
            continue
        rpt = rpt_by_ccn.get(ccn)
        if not rpt:
            skipped_no_rpt += 1
            new_records.append(record)
            continue
        nmrc_data = nmrc_by_rpt.get(rpt["rpt_rec_num"], {})
        per_diem, method = compute_per_diem(nmrc_data)
        if per_diem is None:
            skipped_no_perdiem += 1
            failure_reasons[method] += 1
            new_records.append(record)
            continue
        new_records.append(upgrade_record(record, per_diem, method, rpt, now_iso, today))
        upgraded += 1
        method_counts[method] += 1
        per_diems.append(per_diem)

    # Build the new manifest.
    new_manifest = dict(manifest)
    new_manifest["records"] = new_records
    new_manifest["recordCount"] = len(new_records)
    new_manifest["v1CostReportUpgrade"] = {
        "ranAt": now_iso,
        "recordsUpgraded": upgraded,
        "recordsLeftAtV0NoRpt": skipped_no_rpt,
        "recordsLeftAtV0NoPerDiem": skipped_no_perdiem,
        "perDiemMethodBreakdown": dict(method_counts),
        "perDiemFailureReasons": dict(failure_reasons),
        "perDiemDistribution": _describe_distribution(per_diems),
        "perDiemMin": PER_DIEM_MIN,
        "perDiemMax": PER_DIEM_MAX,
    }
    new_manifest["methodology"] = (
        "Phase 1-full upgrade: facility metadata still from CMS Skilled Nursing Facility QRP. "
        "Monthly rate is now per-facility — extracted from CMS HCRIS cost-report public-use file "
        "(Form CMS-2540-10 / 2540-24). Per-diem method preference: revenue-per-day "
        "(Worksheet G-3 Line 3 Col 1 / Worksheet S-3 Part I Line 1 Col 6) with cost-per-day fallback "
        "(Worksheet B Part I Line 100 Col 18 / Worksheet S-3 Part I Line 1 Col 6). "
        "Monthly = perDiem × 30; low/high = median × {0.85, 1.18}. Records without a matching cost "
        "report keep the v0 state-anchored band — see metadata.dataQuality per record."
    )

    out_path = Path(args.manifest_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(new_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(file=sys.stderr)
    print(f"Wrote {out_path}", file=sys.stderr)
    print(f"  upgraded to v1-cost-report: {upgraded}", file=sys.stderr)
    print(f"  left at v0 (no RPT match):  {skipped_no_rpt}", file=sys.stderr)
    print(f"  left at v0 (no per-diem):   {skipped_no_perdiem}", file=sys.stderr)
    if method_counts:
        print(f"  per-diem method breakdown:  {dict(method_counts)}", file=sys.stderr)
    if failure_reasons:
        print(f"  per-diem failure reasons:   {dict(failure_reasons)}", file=sys.stderr)
    if per_diems:
        d = _describe_distribution(per_diems)
        print(
            f"  per-diem distribution:      "
            f"min ${d['min']:.0f} / p25 ${d['p25']:.0f} / median ${d['median']:.0f} "
            f"/ p75 ${d['p75']:.0f} / max ${d['max']:.0f}",
            file=sys.stderr,
        )


def _describe_distribution(values: list[float]) -> dict:
    if not values:
        return {}
    s = sorted(values)
    n = len(s)
    return {
        "n": n,
        "min": round(s[0], 2),
        "p25": round(s[n // 4], 2),
        "median": round(statistics.median(s), 2),
        "p75": round(s[(3 * n) // 4], 2),
        "max": round(s[-1], 2),
        "mean": round(statistics.fmean(s), 2),
        "stdev": round(statistics.pstdev(s), 2) if n > 1 else 0.0,
    }


if __name__ == "__main__":
    main()
