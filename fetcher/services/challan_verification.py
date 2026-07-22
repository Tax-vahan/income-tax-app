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
