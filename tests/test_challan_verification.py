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


import pytest

from fetcher.services.challan_verification import compute_date_range


def test_compute_date_range_returns_min_and_max():
    manual = [
        {"id": "a", "dateOfDeposit": "07/05/2026"},
        {"id": "b", "dateOfDeposit": "05/06/2026"},
        {"id": "c", "dateOfDeposit": "01/07/2026"},
    ]
    assert compute_date_range(manual) == ("07/05/2026", "01/07/2026")


def test_compute_date_range_single_challan():
    manual = [{"id": "a", "dateOfDeposit": "11/04/2026"}]
    assert compute_date_range(manual) == ("11/04/2026", "11/04/2026")


def test_compute_date_range_raises_on_unparseable_date():
    manual = [{"id": "a", "dateOfDeposit": "not-a-date"}]
    with pytest.raises(ValueError, match="Unparseable dateOfDeposit"):
        compute_date_range(manual)


from fetcher.services.challan_verification import verify_challans


def _gov(bsr, voucher, date, amount, crn):
    return {"bsrCode": bsr, "challanNum": voucher, "tenderDt": date, "totalAmt": amount, "crn": crn}


def _manual(id_, bsr, voucher, date, amount):
    return {"id": id_, "bsrCode": bsr, "voucherNo": voucher, "dateOfDeposit": date, "totalAmount": amount}


def test_single_verified_challan():
    manual = [_manual("m1", "0180002", "37358", "07/05/2026", 52830)]
    government = [_gov("0180002", "37358", "07/05/2026", 52830, "CRN1")]
    result = verify_challans(manual, government)
    assert result["totalManual"] == 1
    assert result["verified"] == 1
    assert result["notVerified"] == 0
    assert result["details"][0]["status"] == "Verified"
    assert result["details"][0]["matchedCrn"] == "CRN1"
    assert result["message"] == "Verification completed."


def test_multiple_verified_challans():
    manual = [
        _manual("m1", "0180002", "37358", "07/05/2026", 52830),
        _manual("m2", "0180002", "17758", "05/06/2026", 45000),
    ]
    government = [
        _gov("0180002", "37358", "07/05/2026", 52830, "CRN1"),
        _gov("0180002", "17758", "05/06/2026", 45000, "CRN2"),
    ]
    result = verify_challans(manual, government)
    assert result["verified"] == 2
    assert result["notVerified"] == 0


def test_no_matches_marks_not_found():
    manual = [_manual("m1", "0180002", "37358", "07/05/2026", 52830)]
    government = [_gov("0180002", "99999", "07/05/2026", 52830, "CRNX")]
    result = verify_challans(manual, government)
    assert result["verified"] == 0
    assert result["notFound"] == 1
    assert result["amountNotVerified"] == 0
    assert result["notVerified"] == 1
    assert result["details"][0]["status"] == "Not Found"
    assert result["details"][0]["matchedCrn"] is None
    assert result["message"] == "Verification completed. 0 challans verified."


def test_amount_mismatch_marks_amount_not_verified():
    # BSR + Voucher + Date all match, but the amount differs — per spec this
    # is a distinct status from "Not Found", not just "Not Verified".
    manual = [_manual("m1", "0180002", "37358", "07/05/2026", 52830)]
    government = [_gov("0180002", "37358", "07/05/2026", 99999, "CRN1")]
    result = verify_challans(manual, government)
    assert result["verified"] == 0
    assert result["amountNotVerified"] == 1
    assert result["notFound"] == 0
    assert result["notVerified"] == 1
    assert result["details"][0]["status"] == "Amount Not Verified"
    assert result["details"][0]["matchedCrn"] == "CRN1"


def test_duplicate_voucher_numbers_different_bsr_codes():
    manual = [
        _manual("m1", "0180002", "12345", "07/05/2026", 1000),
        _manual("m2", "0987654", "12345", "07/05/2026", 2000),
    ]
    government = [
        _gov("0180002", "12345", "07/05/2026", 1000, "CRN1"),
        _gov("0987654", "12345", "07/05/2026", 2000, "CRN2"),
    ]
    result = verify_challans(manual, government)
    assert result["verified"] == 2
    by_id = {d["id"]: d for d in result["details"]}
    assert by_id["m1"]["matchedCrn"] == "CRN1"
    assert by_id["m2"]["matchedCrn"] == "CRN2"


def test_amount_normalization_in_matching():
    manual = [_manual("m1", "0180002", "37358", "07/05/2026", 55140.0)]
    government = [_gov("0180002", "37358", "07/05/2026", "55140.00", "CRN1")]
    result = verify_challans(manual, government)
    assert result["details"][0]["status"] == "Verified"


def test_empty_government_list_marks_all_not_found():
    manual = [_manual("m1", "0180002", "37358", "07/05/2026", 52830)]
    result = verify_challans(manual, [])
    assert result["verified"] == 0
    assert result["notFound"] == 1
    assert result["amountNotVerified"] == 0
    assert result["notVerified"] == 1
    assert result["governmentFetched"] == 0
    assert result["details"][0]["status"] == "Not Found"
    assert result["message"] == "No challans found on the government portal for the selected date range."


def test_no_manual_challans_returns_zero_totals():
    result = verify_challans([], [_gov("0180002", "37358", "07/05/2026", 52830, "CRN1")])
    assert result["totalManual"] == 0
    assert result["verified"] == 0
    assert result["notVerified"] == 0
    assert result["details"] == []


def test_from_date_to_date_passthrough():
    result = verify_challans([], [], from_date="07/05/2026", to_date="01/07/2026")
    assert result["fromDate"] == "07/05/2026"
    assert result["toDate"] == "01/07/2026"


from fetcher.services.challan_verification import filter_eligible_manual_challans

# Exact shape (trimmed to the fields we read) from a real
# POST https://api.taxvahan.com/api/challan/fetch response.
_MAIN_API_CHALLAN_LIST = [
    {
        "id": 3680, "challanVoucherNo": "00121", "dateOfDeposit": "11/04/2026",
        "bsrCode": "0180002", "tdsDepositByBook": "N", "sectionCode": "194Q",
        "totalTaxDeposit": 18455.00,
    },
    {
        "id": 3704, "challanVoucherNo": "49872", "dateOfDeposit": "07/05/2026",
        "bsrCode": "0180002", "tdsDepositByBook": "N", "sectionCode": "",
        "totalTaxDeposit": 55140.00,
    },
    {
        "id": 3705, "challanVoucherNo": "37358", "dateOfDeposit": "07/05/2026",
        "bsrCode": "0180002", "tdsDepositByBook": "N", "sectionCode": "",
        "totalTaxDeposit": 52830.00,
    },
    {
        "id": 3706, "challanVoucherNo": "17758", "dateOfDeposit": "05/06/2026",
        "bsrCode": "0180002", "tdsDepositByBook": "N", "sectionCode": "",
        "totalTaxDeposit": 45000.00,
    },
    {
        "id": 3707, "challanVoucherNo": "16510", "dateOfDeposit": "05/06/2026",
        "bsrCode": "0180002", "tdsDepositByBook": "N", "sectionCode": "",
        "totalTaxDeposit": 83461.00,
    },
    {
        "id": 3708, "challanVoucherNo": "09583", "dateOfDeposit": "07/07/2026",
        "bsrCode": "0180002", "tdsDepositByBook": "N", "sectionCode": "1009",
        "totalTaxDeposit": 112702.00,
    },
    {
        # sectionCode is null (not just empty) AND tdsDepositByBook is 'Y' —
        # must be excluded on the book-entry rule even though SectionCode alone
        # would have made it eligible.
        "id": 3785, "challanVoucherNo": "87656", "dateOfDeposit": "01/07/2026",
        "bsrCode": "0987654", "tdsDepositByBook": "Y", "sectionCode": None,
        "totalTaxDeposit": 100.00,
    },
]


def test_filter_eligible_manual_challans_excludes_section_code_and_book_entry():
    eligible = filter_eligible_manual_challans(_MAIN_API_CHALLAN_LIST)
    eligible_ids = {c["id"] for c in eligible}
    assert eligible_ids == {"3704", "3705", "3706", "3707"}


def test_filter_eligible_manual_challans_maps_fields():
    eligible = filter_eligible_manual_challans(_MAIN_API_CHALLAN_LIST)
    by_id = {c["id"]: c for c in eligible}
    assert by_id["3705"] == {
        "id":            "3705",
        "voucherNo":     "37358",
        "bsrCode":       "0180002",
        "dateOfDeposit": "07/05/2026",
        "totalAmount":   52830.00,
    }


def test_filter_eligible_manual_challans_empty_input():
    assert filter_eligible_manual_challans([]) == []
