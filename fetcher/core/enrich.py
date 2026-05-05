"""
Data transformation utilities — pure functions, no I/O.

needs_detail_fetch(item)    — decide whether copychallan API is needed
filter_by_date(items, ...)  — keep only challans in the requested range
enrich(summary, detail)     — merge list + detail into one flat record
"""

import re
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("TDS")


# ── Smart detail-fetch gate ────────────────────────────────────────────────────

def needs_detail_fetch(item: dict) -> bool:
    """
    Return True if the summary record is missing BSR code or a proper 5-digit challan number.
    The paymenthistory list API often returns CRN in the 'challanNum' field.
    """
    bsr = item.get("bsrCode")
    cnum = str(item.get("challanNum") or "")
    
    # If BSR is missing or challanNum looks like a CRN (14 digits), we need detail
    if not bsr or len(cnum) >= 14 or not cnum:
        return True
        
    # Check for basic breakdown fields — if they are missing, we still want detail
    for field in ("basicTax", "surCharge", "eduCess"):
        if item.get(field) in (None, "", 0):
            return True

    return False


def extract_bsr_code(data: dict) -> Optional[str]:
    """
    Extract the BSR code with strict 3-level priority:

    1. ``bsrCode`` field — if present and exactly 7 digits.
    2. ``alternateCin[:7]`` — if alternateCin is a 20-char all-numeric string.
    3. ``cin[:7]``          — if cin is a 20-char all-numeric string.

    Returns a 7-digit string, or ``None`` if all sources are exhausted or invalid.
    """
    # Priority 1: direct bsrCode
    bsr = data.get("bsrCode")
    if bsr and isinstance(bsr, str):
        bsr = bsr.strip()
        if bsr.isdigit() and len(bsr) == 7:
            return bsr

    # Priority 2: alternateCin  (format: BSR(7) + Date(8) + Serial(5) = 20 chars)
    alt_cin = str(data.get("alternateCin") or "").strip()
    if len(alt_cin) >= 7 and alt_cin[:7].isdigit():
        return alt_cin[:7]

    # Priority 3: cin  (only if it is the full 20-char numeric bank CIN)
    cin = str(data.get("cin") or "").strip()
    if len(cin) == 20 and cin.isdigit():
        return cin[:7]

    return None



# ── Date parsing helpers ───────────────────────────────────────────────────────

_MON_NUM = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,  "May": 5,  "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_FMT_MAP = [
    ("%d/%m/%Y",          10),
    ("%Y-%m-%d",          10),
    ("%d-%m-%Y",          10),
    ("%d/%m/%Y %H:%M:%S", 19),
    ("%Y-%m-%dT%H:%M:%S", 19),
]


def _parse_date_str(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip()

    # "11-Apr-2026 09:42:46" — locale-neutral month abbreviation
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})", s)
    if m:
        try:
            d, mo, y = int(m.group(1)), m.group(2).capitalize(), int(m.group(3))
            if mo in _MON_NUM:
                return datetime(y, _MON_NUM[mo], d)
        except Exception:
            pass

    for fmt, slen in _FMT_MAP:
        try:
            return datetime.strptime(s[:slen], fmt)
        except ValueError:
            pass

    return None


def _parse_epoch(v) -> Optional[datetime]:
    try:
        return datetime.utcfromtimestamp(float(v) / 1000)
    except Exception:
        return None


def _date_from_crn(crn: str) -> Optional[datetime]:
    """
    Extract payment date from CRN prefix.
    CRN format: YYMMDDSSSSSSS  →  26020600200908 = 06-Feb-2026
    """
    crn = str(crn or "").strip()
    if len(crn) < 6:
        return None
    try:
        yy, mm, dd = int(crn[0:2]), int(crn[2:4]), int(crn[4:6])
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return datetime(2000 + yy, mm, dd)
    except (ValueError, TypeError):
        pass
    return None


def _best_date(item: dict) -> tuple[Optional[datetime], Optional[str]]:
    """Return (datetime, source_field_name) for the most reliable date on an item."""
    # CRN prefix is the most reliable source (it IS the payment date by design)
    dt = _date_from_crn(item.get("crn") or item.get("cin", "")[:14])
    if dt:
        return dt, "crn"

    for field in ("paymentDt", "paymentDate", "tenderDt", "tenderDate",
                  "dateOfDeposit", "paymentTime"):
        v = item.get(field)
        if v:
            dt = (_parse_epoch(v) if isinstance(v, (int, float))
                  else _parse_date_str(str(v)))
            if dt:
                return dt, field

    return None, None


def _financial_year(dt: datetime) -> str:
    """Return Indian FY string e.g. '2025-26' for any date in that year."""
    if dt.month >= 4:
        return f"{dt.year}-{str(dt.year + 1)[2:]}"
    return f"{dt.year - 1}-{str(dt.year)[2:]}"


# ── Date filter ────────────────────────────────────────────────────────────────

def filter_by_date(items: list, from_date: str, to_date: str) -> list:
    """
    Keep only challans whose payment date falls in [from_date, to_date].
    Both bounds are DD/MM/YYYY strings.
    Items with no parseable date are kept (safe-fail policy).
    """
    if not from_date and not to_date:
        return items

    dt_from = _parse_date_str(from_date)
    dt_to   = _parse_date_str(to_date)

    filtered, skipped = [], 0
    for item in items:
        dt, src = _best_date(item)
        if dt is None:
            filtered.append(item)       # no date found → keep
            continue

        in_range = True
        if dt_from and dt < dt_from:
            in_range = False
        if dt_to and dt > dt_to.replace(hour=23, minute=59, second=59):
            in_range = False

        if not in_range:
            skipped += 1
            log.debug("  Dropped challan: date %s (from %s) is outside range", dt.strftime("%Y-%m-%d"), src)
            continue

        filtered.append(item)

    if skipped:
        log.info(
            "Date-filter: kept %d/%d  (%d dropped outside %s–%s)",
            len(filtered), len(items), skipped, from_date, to_date,
        )
    return filtered


# ── Enrichment ────────────────────────────────────────────────────────────────

def _fmt_date(v) -> str:
    """Normalise any date representation to DD/MM/YYYY string."""
    if not v:
        return ""
    s = str(v).strip().split(" ")[0]

    if s.isdigit() and len(s) >= 10:
        try:
            return datetime.utcfromtimestamp(int(s) / 1000).strftime("%d/%m/%Y")
        except Exception:
            pass

    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})", s)
    if m:
        _M = {
            "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
            "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
            "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
        }
        d, mo, y = m.group(1), m.group(2).capitalize(), m.group(3)
        return f"{int(d):02d}/{_M.get(mo, '01')}/{y}"

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"

    return s


def enrich(summary: dict, detail: dict) -> dict:
    """
    Merge a summary record (from paymenthistory) with a detail record
    (from copychallan) into a single flat dict ready for Excel output.

    Detail values win over summary for any non-empty field.
    """
    m = summary.copy()
    for k, v in detail.items():
        if v not in (None, ""):
            m[k] = v

    # ── Field alias normalisation ──────────────────────────────────
    if not m.get("bsrCode")   and m.get("bsrCd"):
        m["bsrCode"]  = m["bsrCd"]
    if not m.get("tenderDt")  and m.get("tenderDate"):
        m["tenderDt"] = m["tenderDate"]
    if not m.get("totalAmt"):
        m["totalAmt"] = m.get("totalAmount") or m.get("amount") or 0

    # ── BSR code — apply correct priority (direct > alternateCin > cin) ───
    bsr = extract_bsr_code(m)
    if bsr:
        m["bsrCode"] = bsr
        log.debug("BSR code resolved: %s", bsr)

    # ── Section Code / Nature of Payment normalization ────────────────
    # We prefer natureOfPayment or secDesc (e.g. '206C') over internal codes like '6CE'.
    val_sec = m.get("secDesc") or m.get("natureOfPayment") or m.get("secCd")
    if val_sec:
        m["secCd"] = str(val_sec).strip()

    # ── Section Code from paymentType (fallback for summary records) ────
    # paymentType = "TDS/TCS Payable by Taxpayer(200)" → use description if ID matches minor head
    if m.get("secCd") in ("200", "400") or not m.get("secCd"):
        pt = str(m.get("paymentType") or "").strip()
        if pt:
            import re as _re
            match = _re.match(r'^(.*?)\(\d+\)$', pt)
            if match:
                m["secCd"] = match.group(1).strip()
            else:
                m["secCd"] = pt

    # ── Decode challan serial from the "CRN+BANK" CIN hybrid ─────────
    # The paymenthistory CIN is "<14-digit-CRN><4-letter-bank>" e.g. "26041100002738KKBK".
    # The last 5 digits of the CRN are the challan serial number.
    # Format: YYMMDDSSSSSSS (14 digits) → last 7 chars = serial
    if not m.get("challanNum"):
        raw_cin = str(m.get("cin") or "").strip()
        if len(raw_cin) >= 14 and raw_cin[:14].isdigit():
            # Last 5 digits of the CRN portion are the serial / challan number
            m["challanNum"] = raw_cin[9:14]
            log.debug("challanNum extracted from CIN prefix: %s → %s",
                      raw_cin[:14], m["challanNum"])

    # ── Decode from alternateCin (Standard Bank CIN) ──────────────────
    acin = str(m.get("alternateCin", "")).strip()
    if len(acin) == 20 and acin.isdigit():
        if not m.get("bsrCode"):
            m["bsrCode"]   = acin[:7]
        if not m.get("tenderDt"):
            # Alternate CIN format: BSR(7) + Date(DDMMYYYY)(8) + Serial(5)
            d, mo, y = acin[7:9], acin[9:11], acin[11:15]
            m["tenderDt"] = f"{d}/{mo}/{y}"
        if not m.get("challanNum"):
            m["challanNum"] = acin[15:20]

    # ── Decode BSR code / tender date / challan number from CIN ───────

    cin = str(m.get("cin", "")).strip()
    # Standard Bank CIN: BSR(7) + Date(8) + Serial(5) = 20 chars
    if len(cin) == 20 and cin.isdigit():
        if not m.get("bsrCode"):
            m["bsrCode"]   = cin[:7]
        if not m.get("tenderDt"):
            # Date is usually chars 7-15 (DDMMYYYY)
            d, mo, y = cin[7:9], cin[9:11], cin[11:15]
            m["tenderDt"] = f"{d}/{mo}/{y}"
        if not m.get("challanNum"):
            m["challanNum"] = cin[15:20]


    if not m.get("paymentDt"):
        m["paymentDt"] = m.get("tenderDt") or ""

    # ── Bank code from CIN hybrid (e.g. "26041100002738KKBK" → "KKBK") ──
    cin_raw = str(m.get("cin") or "").strip()
    if len(cin_raw) >= 18 and cin_raw[:14].isdigit() and not cin_raw[14:18].isdigit():
        m.setdefault("bankCode", cin_raw[14:18].upper())

    # ── Coerce monetary fields to integers ─────────────────────────────
    for field in ("basicTax", "surCharge", "eduCess",
                  "interest", "penalty", "others"):
        v = m.get(field)
        if v is None or v == "":
            m[field] = 0
        else:
            try:
                m[field] = int(float(v))
            except (ValueError, TypeError):
                m[field] = 0

    v_tot = m.get("totalAmt") or m.get("totalAmount") or m.get("amount") or 0
    try:
        m["totalAmt"] = int(float(v_tot)) if v_tot else 0
    except (ValueError, TypeError):
        m["totalAmt"] = 0

    # ── Final date fallback + derived time fields ──────────────────────
    best_dt, _ = _best_date(m)
    if best_dt:
        if not m.get("paymentDt"):
            m["paymentDt"] = best_dt.strftime("%d/%m/%Y")
        fy = _financial_year(best_dt)
        m.setdefault("financialYear",   fy)
        fy_start = int(fy.split("-")[0])
        m.setdefault("assessmentYear",  f"{fy_start + 1}-{str(fy_start + 2)[2:]}")
        m.setdefault("paymentMonth",    best_dt.strftime("%b %Y"))      # "Apr 2026"
        m.setdefault("paymentMonthNum", f"{best_dt.month:02d}/{best_dt.year}")  # "04/2026"

    # ── Normalise date strings ─────────────────────────────────────────
    m["paymentDt"] = _fmt_date(m.get("paymentDt"))
    m["tenderDt"]  = _fmt_date(m.get("tenderDt"))

    return m

