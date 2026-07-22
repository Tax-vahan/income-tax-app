from fetcher.services.challan_verification import (
    normalize_voucher,
    normalize_bsr,
    normalize_date,
    normalize_amount,
    build_key,
)


def test_normalize_voucher_strips_whitespace():
    assert normalize_voucher("  12345  ") == "12345"


def test_normalize_voucher_handles_none():
    assert normalize_voucher(None) == ""


def test_normalize_bsr_preserves_leading_zeros():
    assert normalize_bsr("0180002") == "0180002"
    assert normalize_bsr("0180002") != normalize_bsr("180002")


def test_normalize_date_handles_multiple_formats():
    assert normalize_date("07/05/2026") == "2026-05-07"
    assert normalize_date("2026-05-07") == "2026-05-07"
    assert normalize_date("07-May-2026 09:42:46") == "2026-05-07"


def test_normalize_date_returns_empty_string_when_unparseable():
    assert normalize_date("not-a-date") == ""


def test_normalize_amount_treats_equivalent_values_as_equal():
    assert normalize_amount(55140) == normalize_amount(55140.0) == normalize_amount("55140.00")


def test_normalize_amount_returns_empty_string_when_unparseable():
    assert normalize_amount("not-a-number") == ""


def test_build_key_combines_normalized_fields():
    key = build_key("0180002", "12345", "07/05/2026", 55140)
    assert key == "0180002|12345|2026-05-07|55140.00"
