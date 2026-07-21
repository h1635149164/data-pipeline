"""tests/test_scrapper.py — Unit tests for src/support/scrapper.py

Real API endpoints (monitor.statnipokladna.gov.cz):
  Chapters:  GET /api/kapitola                              → JSON array
  Summary:   GET /api/rozpocet/souhrnny?obdobi=2605&kapitola=313  → JSON dict
"""

import httpx
import pytest
import respx

from src.support.scrapper import fetch_chapters, fetch_json, fetch_summary

BASE_URL = "https://api.example.com"
CHAPTERS_URL = f"{BASE_URL}/kapitola"
SUMMARY_URL = f"{BASE_URL}/rozpocet/souhrnny"


# ---------------------------------------------------------------------------
# fetch_json
# ---------------------------------------------------------------------------


class TestFetchJson:
    @respx.mock
    def test_happy_path_list(self):
        respx.get(CHAPTERS_URL).mock(
            return_value=httpx.Response(200, json=[{"id": 1}])
        )
        with httpx.Client() as client:
            result = fetch_json(CHAPTERS_URL, client)
        assert result == [{"id": 1}]

    @respx.mock
    def test_happy_path_dict(self):
        respx.get(CHAPTERS_URL).mock(
            return_value=httpx.Response(200, json={"key": "value"})
        )
        with httpx.Client() as client:
            result = fetch_json(CHAPTERS_URL, client)
        assert result == {"key": "value"}

    @respx.mock
    def test_passes_query_params(self):
        """fetch_json must forward query params to the request."""
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(str(request.url))
            return httpx.Response(200, json={})

        respx.get(CHAPTERS_URL).mock(side_effect=handler)
        with httpx.Client() as client:
            fetch_json(CHAPTERS_URL, client, params={"foo": "bar"})
        assert "foo=bar" in captured[0]

    @respx.mock
    def test_passes_czech_firefox_headers(self):
        """fetch_json must send Czech Firefox Accept-Language and User-Agent headers."""
        headers_captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            headers_captured.update(dict(request.headers))
            return httpx.Response(200, json=[])

        respx.get(CHAPTERS_URL).mock(side_effect=handler)
        with httpx.Client() as client:
            fetch_json(CHAPTERS_URL, client)

        assert "accept-language" in headers_captured
        assert "cs" in headers_captured["accept-language"]
        assert "Firefox" in headers_captured["user-agent"]


    @respx.mock
    def test_raises_on_404(self):
        respx.get(CHAPTERS_URL).mock(return_value=httpx.Response(404))
        with httpx.Client() as client:
            with pytest.raises(httpx.HTTPStatusError):
                fetch_json(CHAPTERS_URL, client)

    @respx.mock
    def test_raises_on_500(self):
        respx.get(CHAPTERS_URL).mock(return_value=httpx.Response(500))
        with httpx.Client() as client:
            with pytest.raises(httpx.HTTPStatusError):
                fetch_json(CHAPTERS_URL, client)

    @respx.mock
    def test_raises_on_401(self):
        respx.get(CHAPTERS_URL).mock(return_value=httpx.Response(401))
        with httpx.Client() as client:
            with pytest.raises(httpx.HTTPStatusError):
                fetch_json(CHAPTERS_URL, client)

    @respx.mock
    def test_raises_on_invalid_json(self):
        respx.get(CHAPTERS_URL).mock(
            return_value=httpx.Response(200, content=b"not json at all")
        )
        with httpx.Client() as client:
            with pytest.raises(ValueError, match="not valid JSON"):
                fetch_json(CHAPTERS_URL, client)

    @respx.mock
    def test_raises_on_timeout(self):
        respx.get(CHAPTERS_URL).mock(side_effect=httpx.TimeoutException("timeout"))
        with httpx.Client() as client:
            with pytest.raises(httpx.TimeoutException):
                fetch_json(CHAPTERS_URL, client)

    @respx.mock
    def test_raises_on_connect_error(self):
        respx.get(CHAPTERS_URL).mock(side_effect=httpx.ConnectError("refused"))
        with httpx.Client() as client:
            with pytest.raises(httpx.HTTPError):
                fetch_json(CHAPTERS_URL, client)


# ---------------------------------------------------------------------------
# fetch_chapters
# ---------------------------------------------------------------------------


class TestFetchChapters:
    @respx.mock
    def test_happy_path(self):
        payload = [
            {
                "id": 301,
                "name": "Office of the President",
                "startDate": "1900-01-01",
                "endDate": "9999-12-31",
                "expired": False,
            }
        ]
        respx.get(CHAPTERS_URL).mock(return_value=httpx.Response(200, json=payload))
        with httpx.Client() as client:
            result = fetch_chapters(CHAPTERS_URL, client)
        assert result == payload

    @respx.mock
    def test_raises_when_response_is_dict(self):
        """Endpoint should return a list; a dict is invalid."""
        respx.get(CHAPTERS_URL).mock(
            return_value=httpx.Response(200, json={"key": "value"})
        )
        with httpx.Client() as client:
            with pytest.raises(ValueError, match="Expected a JSON list"):
                fetch_chapters(CHAPTERS_URL, client)

    @respx.mock
    def test_http_error_propagated(self):
        respx.get(CHAPTERS_URL).mock(return_value=httpx.Response(503))
        with httpx.Client() as client:
            with pytest.raises(httpx.HTTPStatusError):
                fetch_chapters(CHAPTERS_URL, client)

    @respx.mock
    def test_empty_list_is_valid(self):
        respx.get(CHAPTERS_URL).mock(return_value=httpx.Response(200, json=[]))
        with httpx.Client() as client:
            result = fetch_chapters(CHAPTERS_URL, client)
        assert result == []


# ---------------------------------------------------------------------------
# fetch_summary
# ---------------------------------------------------------------------------


class TestFetchSummary:
    @respx.mock
    def test_happy_path(self):
        """fetch_summary should call ?obdobi=2605&kapitola=313 and return a dict."""
        payload = {"name": "Ministry of Labour and Social Affairs", "budget": {}, "children": []}
        # respx matches on the base URL; params are passed through httpx
        respx.get(SUMMARY_URL).mock(return_value=httpx.Response(200, json=payload))
        with httpx.Client() as client:
            result = fetch_summary(SUMMARY_URL, "2605", 313, client)
        assert result == payload

    @respx.mock
    def test_query_params_are_set_correctly(self):
        """fetch_summary must build ?obdobi=<timeframe>&kapitola=<chapter_id>."""
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(str(request.url))
            return httpx.Response(200, json={"name": "x", "budget": {}, "children": []})

        respx.get(SUMMARY_URL).mock(side_effect=handler)
        with httpx.Client() as client:
            fetch_summary(SUMMARY_URL, "2512", 999, client)

        assert len(captured) == 1
        assert "obdobi=2512" in captured[0]
        assert "kapitola=999" in captured[0]

    @respx.mock
    def test_raises_when_response_is_list(self):
        """Endpoint should return a dict; a list is invalid."""
        respx.get(SUMMARY_URL).mock(
            return_value=httpx.Response(200, json=[1, 2, 3])
        )
        with httpx.Client() as client:
            with pytest.raises(ValueError, match="Expected a JSON dict"):
                fetch_summary(SUMMARY_URL, "2601", 313, client)

    @respx.mock
    def test_http_error_propagated(self):
        respx.get(SUMMARY_URL).mock(return_value=httpx.Response(404))
        with httpx.Client() as client:
            with pytest.raises(httpx.HTTPStatusError):
                fetch_summary(SUMMARY_URL, "2605", 313, client)

    @respx.mock
    def test_timeout_propagated(self):
        respx.get(SUMMARY_URL).mock(side_effect=httpx.TimeoutException("timeout"))
        with httpx.Client() as client:
            with pytest.raises(httpx.TimeoutException):
                fetch_summary(SUMMARY_URL, "2605", 313, client)

    @respx.mock
    def test_zero_value_month_returns_dict(self):
        """A month with all-zero values still returns a dict (caller decides to skip)."""
        payload = {"name": "Ministry of Finance", "budget": {"approved": 0.0, "reality": 0.0}, "children": []}
        respx.get(SUMMARY_URL).mock(return_value=httpx.Response(200, json=payload))
        with httpx.Client() as client:
            result = fetch_summary(SUMMARY_URL, "2612", 312, client)
        assert isinstance(result, dict)
