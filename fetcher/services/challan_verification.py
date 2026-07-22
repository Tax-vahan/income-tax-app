"""
Pure in-memory matching logic for the challan verification endpoint.

No I/O, no FastAPI, no Selenium — every function here is a plain
transformation over dicts and is safe to unit test in isolation.
"""

from typing import Optional

from ..core.enrich import _parse_date_str


def normalize_voucher(value) -> str:
    """Trim whitespace. None becomes an empty string."""
    return str(value if value is not None else "").strip()


def normalize_bsr(value) -> str:
    """Trim whitespace only — never reformat digits, callers own leading zeros."""
    return str(value if value is not None else "").strip()


def normalize_date(value) -> str:
    """Parse many date formats and return 'YYYY-MM-DD', or '' if unparseable."""
    dt = _parse_date_str(str(value if value is not None else ""))
    return dt.strftime("%Y-%m-%d") if dt else ""


def normalize_amount(value) -> str:
    """Fixed 2-decimal string so 55140 / 55140.0 / '55140.00' all compare equal."""
    try:
        return f"{round(float(value), 2):.2f}"
    except (TypeError, ValueError):
        return ""


def build_key(bsr, voucher, date, amount) -> str:
    return "|".join((
        normalize_bsr(bsr),
        normalize_voucher(voucher),
        normalize_date(date),
        normalize_amount(amount),
    ))


def compute_date_range(manual_challans: list[dict]) -> tuple[str, str]:
    """
    Return (from_date, to_date) as DD/MM/YYYY strings spanning every manual
    challan's dateOfDeposit. Raises ValueError if any date is unparseable.
    """
    dates = []
    for item in manual_challans:
        raw = item.get("dateOfDeposit")
        dt = _parse_date_str(str(raw or ""))
        if dt is None:
            raise ValueError(
                f"Unparseable dateOfDeposit for challan id={item.get('id')!r}: {raw!r}"
            )
        dates.append(dt)
    return min(dates).strftime("%d/%m/%Y"), max(dates).strftime("%d/%m/%Y")


def verify_challans(
    manual: list[dict],
    government: list[dict],
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    """
    Compare manual challans against government-fetched challans using an
    O(n) composite-key lookup, and return the verification result payload.

    Government field mapping (from the existing, unmodified enrich() output):
    bsrCode, challanNum (voucher/serial), tenderDt (falls back conceptually
    to paymentDt upstream), totalAmt.
    """
    gov_index: dict = {}
    for g in government:
        key = build_key(
            g.get("bsrCode"), g.get("challanNum"),
            g.get("tenderDt") or g.get("paymentDt"), g.get("totalAmt"),
        )
        gov_index[key] = g.get("crn")

    details = []
    verified = 0
    for item in manual:
        key = build_key(
            item.get("bsrCode"), item.get("voucherNo"),
            item.get("dateOfDeposit"), item.get("totalAmount"),
        )
        matched_crn = gov_index.get(key)
        status = "Verified" if matched_crn is not None else "Not Verified"
        if status == "Verified":
            verified += 1
        details.append({
            "id":         item.get("id"),
            "voucherNo":  item.get("voucherNo"),
            "status":     status,
            "matchedCrn": matched_crn,
        })

    total = len(manual)
    not_verified = total - verified

    if not government:
        message = "No challans found on the government portal for the selected date range."
    elif verified == 0:
        message = "Verification completed. 0 challans verified."
    else:
        message = "Verification completed."

    return {
        "success":           True,
        "message":           message,
        "totalManual":       total,
        "verified":          verified,
        "notVerified":       not_verified,
        "fromDate":          from_date,
        "toDate":            to_date,
        "governmentFetched": len(government),
        "details":           details,
    }
