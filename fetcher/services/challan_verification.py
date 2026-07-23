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


def build_partial_key(bsr, voucher, date) -> str:
    """Same as build_key but without amount — used to detect an 'Amount Not
    Verified' match (BSR + Voucher + Date agree, amount doesn't)."""
    return "|".join((
        normalize_bsr(bsr),
        normalize_voucher(voucher),
        normalize_date(date),
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

    Each manual challan resolves to one of three statuses:
      - "Verified"             — BSR + Voucher + Date + Amount all match.
      - "Amount Not Verified"  — BSR + Voucher + Date match, Amount doesn't.
      - "Not Found"            — the BSR + Voucher + Date combination doesn't
                                  match any government challan at all.

    Government field mapping (from the existing, unmodified enrich() output):
    bsrCode, challanNum (voucher/serial), tenderDt (falls back conceptually
    to paymentDt upstream), totalAmt.
    """
    full_index: dict = {}
    partial_index: dict = {}
    for g in government:
        bsr, voucher = g.get("bsrCode"), g.get("challanNum")
        date, amount = g.get("tenderDt") or g.get("paymentDt"), g.get("totalAmt")
        crn = g.get("crn")
        full_index[build_key(bsr, voucher, date, amount)] = crn
        partial_index[build_partial_key(bsr, voucher, date)] = crn

    details = []
    verified = amount_not_verified = not_found = 0
    for item in manual:
        bsr, voucher = item.get("bsrCode"), item.get("voucherNo")
        date, amount = item.get("dateOfDeposit"), item.get("totalAmount")

        full_key = build_key(bsr, voucher, date, amount)
        if full_key in full_index:
            status, matched_crn = "Verified", full_index[full_key]
            verified += 1
        else:
            partial_key = build_partial_key(bsr, voucher, date)
            if partial_key in partial_index:
                status, matched_crn = "Amount Not Verified", partial_index[partial_key]
                amount_not_verified += 1
            else:
                status, matched_crn = "Not Found", None
                not_found += 1

        details.append({
            "id":         item.get("id"),
            "voucherNo":  item.get("voucherNo"),
            "status":     status,
            "matchedCrn": matched_crn,
        })

    total = len(manual)
    not_verified = amount_not_verified + not_found

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
        "amountNotVerified": amount_not_verified,
        "notFound":          not_found,
        "notVerified":       not_verified,
        "fromDate":          from_date,
        "toDate":            to_date,
        "governmentFetched": len(government),
        "details":           details,
    }
