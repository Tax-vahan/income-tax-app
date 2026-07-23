from unittest.mock import patch, MagicMock

import pytest
import requests

from fetcher.core import taxvahan_api


def _resp(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_fetch_manual_challans_forwards_auth_token_verbatim(monkeypatch):
    monkeypatch.setattr(taxvahan_api, "TAXVAHAN_API_BASE", "https://api.taxvahan.com")

    page = _resp({"challanList": [{"id": 1}], "totalRows": 1})
    with patch("fetcher.core.taxvahan_api.requests.post", return_value=page) as mock_post:
        result = taxvahan_api.fetch_manual_challans(
            deductor_id="404868", financial_year="2026-27", quarter="Q1", category_id=2,
            auth_token="Bearer some-caller-supplied-jwt",
        )

    assert result == [{"id": 1}]
    mock_post.assert_called_once()
    call = mock_post.call_args
    assert call.args[0] == "https://api.taxvahan.com/api/challan/fetch"
    assert call.kwargs["json"] == {
        "deductorId":    "404868",
        "financialYear": "2026-27",
        "quarter":       "Q1",
        "categoryId":    2,
        "pageNumber":    1,
        "pageSize":      50,
    }
    # Forwarded exactly as supplied — no "Bearer " prefix added or assumed by us.
    assert call.kwargs["headers"] == {"Authorization": "Bearer some-caller-supplied-jwt"}


def test_fetch_manual_challans_single_page_no_extra_calls():
    page = _resp({"challanList": [{"id": 1}, {"id": 2}], "totalRows": 2})
    with patch("fetcher.core.taxvahan_api.requests.post", return_value=page) as mock_post:
        result = taxvahan_api.fetch_manual_challans(
            deductor_id="404868", financial_year="2026-27", quarter="Q1", category_id=2,
            auth_token="token",
        )
    assert len(result) == 2
    assert mock_post.call_count == 1


def test_fetch_manual_challans_paginates_until_total_reached():
    page1 = _resp({"challanList": [{"id": 1}], "totalRows": 3})
    page2 = _resp({"challanList": [{"id": 2}, {"id": 3}], "totalRows": 3})
    with patch("fetcher.core.taxvahan_api.requests.post", side_effect=[page1, page2]) as mock_post:
        result = taxvahan_api.fetch_manual_challans(
            deductor_id="404868", financial_year="2026-27", quarter="Q1", category_id=2,
            auth_token="token", page_size=1,
        )
    assert [c["id"] for c in result] == [1, 2, 3]
    assert mock_post.call_count == 2
    assert mock_post.call_args_list[1].kwargs["json"]["pageNumber"] == 2
    # Auth header stays the same across pages.
    assert mock_post.call_args_list[1].kwargs["headers"] == {"Authorization": "token"}


def test_fetch_manual_challans_stops_on_empty_page():
    empty_page = _resp({"challanList": [], "totalRows": 5})
    with patch("fetcher.core.taxvahan_api.requests.post", return_value=empty_page) as mock_post:
        result = taxvahan_api.fetch_manual_challans(
            deductor_id="404868", financial_year="2026-27", quarter="Q1", category_id=2,
            auth_token="token",
        )
    assert result == []
    assert mock_post.call_count == 1


def test_fetch_manual_challans_raises_on_http_error():
    error_page = _resp({"detail": "unauthorized"}, status_code=401)
    with patch("fetcher.core.taxvahan_api.requests.post", return_value=error_page):
        with pytest.raises(requests.exceptions.HTTPError):
            taxvahan_api.fetch_manual_challans(
                deductor_id="404868", financial_year="2026-27", quarter="Q1", category_id=2,
                auth_token="token",
            )
