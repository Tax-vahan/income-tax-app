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
    Return True if the summary record is missing any key tax breakdown field.
    Avoids calling copychallan when the list API already returned full data.
    """
    return not all([
        item.get("basicTax"),
        item.get("interest"),
        item.get("penalty"),
    ])


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

    for field in ("paymentDate", "tenderDt", "tenderDate", "dateOfDeposit"):
        v = item.get(field)
        if v:
            dt = (_parse_epoch(v) if isinstance(v, (int, float))
                  else _parse_date_str(str(v)))
            if dt:
                return dt, field

    v = item.get("paymentTime")
    if v:
        dt = (_parse_epoch(v) if isinstance(v, (int, float))
              else _parse_date_str(str(v)))
        if dt:
            return dt, "paymentTime"

    return None, None


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

        if in_range:
            filtered.append(item)
        else:
            skipped += 1
            log.debug("Date-filter DROP  CRN=%s  dt=%s  (src=%s)",
                      item.get("crn", "?"), dt.date(), src)

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

    # ── Field alias normalisation ──────────────────────────────────────
    if not m.get("bsrCode")   and m.get("bsrCd"):
        m["bsrCode"]  = m["bsrCd"]
    if not m.get("tenderDt")  and m.get("tenderDate"):
        m["tenderDt"] = m["tenderDate"]
    if not m.get("totalAmt"):
        m["totalAmt"] = m.get("totalAmount") or m.get("amount") or 0
    if not m.get("secCd")     and m.get("natureOfPayment"):
        m["secCd"]    = m["natureOfPayment"]
    if not m.get("paymentDt") and m.get("paymentDate"):
        m["paymentDt"] = m["paymentDate"]

    # ── Decode BSR code / tender date / challan number from CIN ───────
    cin = m.get("cin", "")
    if cin and len(cin) == 18 and cin.isdigit():
        if not m.get("bsrCode"):
            m["bsrCode"]   = cin[:7]
        if not m.get("tenderDt"):
            m["tenderDt"]  = f"{cin[6:8]}/{cin[8:10]}/20{cin[10:12]}"
        if not m.get("challanNum"):
            m["challanNum"] = cin[13:18]
    elif cin:
        if not m.get("challanNum"):
            m["challanNum"] = m.get("crn", "")

    if not m.get("paymentDt"):
        m["paymentDt"] = m.get("tenderDt") or ""

    # ── Coerce monetary fields to integers ─────────────────────────────
    for field in ("basicTax", "surCharge", "eduCess",
                  "interest", "penalty", "others"):
        v = m.get(field, "")
        try:
            m[field] = int(float(v)) if v else ""
        except (ValueError, TypeError):
            m[field] = ""

    v_tot = m.get("totalAmt") or m.get("totalAmount") or m.get("amount") or 0
    try:
        m["totalAmt"] = int(float(v_tot)) if v_tot else 0
    except (ValueError, TypeError):
        m["totalAmt"] = 0

    # ── Normalise date strings ─────────────────────────────────────────
    m["paymentDt"] = _fmt_date(m.get("paymentDt"))
    m["tenderDt"]  = _fmt_date(m.get("tenderDt"))

    return m
