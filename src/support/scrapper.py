"""
scrapper.py — Tiny, single-purpose HTTP fetcher functions for the OpenBudget pipeline.

All network interactions are isolated here so the rest of the pipeline never
touches httpx directly. Each function raises on failure; callers decide how to
handle errors.

Live API reference (monitor.statnipokladna.gov.cz):
  - Chapters:  GET /api/kapitola          → JSON array
  - Summary:   GET /api/rozpocet/souhrnny?obdobi=<YYMM>&kapitola=<chapter_id>  → JSON object
"""

import httpx

CZECH_FIREFOX_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) "
        "Gecko/20100101 Firefox/135.0"
    ),
    "Accept-Language": "cs,sk;q=0.8,en-US;q=0.5,en;q=0.3",
    "Accept": "application/json, text/plain, */*",
}


def fetch_json(
    url: str,
    client: httpx.Client,
    params: dict | None = None,
    headers: dict | None = None,
) -> list | dict:
    """Perform a GET request with Czech Firefox headers and return the parsed JSON body.

    Args:
        url:     Full URL to fetch.
        client:  Shared httpx.Client instance (connection pooling).
        params:  Optional query-string parameters dict.
        headers: Optional additional/override headers dict.

    Returns:
        Parsed JSON as a list or dict.

    Raises:
        httpx.HTTPStatusError: On 4xx / 5xx responses.
        httpx.TimeoutException:  On network timeout.
        httpx.HTTPError:         On other network-level errors.
        ValueError:              When the response body is not valid JSON.
    """
    req_headers = dict(CZECH_FIREFOX_HEADERS)
    if headers:
        req_headers.update(headers)

    try:
        response = client.get(url, params=params, headers=req_headers)
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise
    except httpx.TimeoutException:
        raise
    except httpx.HTTPError:
        raise

    try:
        return response.json()
    except Exception as e:
        raise ValueError(f"Response from '{url}' is not valid JSON: {e}") from e


def fetch_chapters(endpoint: str, client: httpx.Client) -> list[dict]:
    """Fetch the official list of budget chapters (kapitola) from the state API in Czech.

    Calls GET <endpoint>  (e.g. https://monitor.statnipokladna.gov.cz/api/kapitola)

    Args:
        endpoint: Full URL of the chapters endpoint (no query string needed).
        client:   Shared httpx.Client instance.

    Returns:
        List of chapter dicts with keys: id, name, startDate, endDate, expired.

    Raises:
        httpx.HTTPError: On network or HTTP errors.
        ValueError:      When the response is not a JSON list.
    """
    data = fetch_json(endpoint, client)
    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON list from chapters endpoint '{endpoint}', "
            f"got {type(data).__name__}"
        )
    return data


def fetch_summary(
    endpoint: str,
    timeframe: str,
    chapter_id: int,
    client: httpx.Client,
) -> dict:
    """Fetch a monthly budget summary (souhrnny) for a specific chapter and YYMM timeframe in Czech.

    Calls GET <endpoint>?obdobi=<timeframe>&kapitola=<chapter_id>
    e.g. https://monitor.statnipokladna.gov.cz/api/rozpocet/souhrnny?obdobi=2605&kapitola=313

    Args:
        endpoint:   Full URL of the summary endpoint (no query string).
        timeframe:  YYMM string, e.g. '2605' for Year 2026, month 5 (May).
        chapter_id: Numeric chapter (kapitola) ID from the state system.
        client:     Shared httpx.Client instance.

    Returns:
        Parsed summary dict (contains 'name', 'budget', 'children' keys).

    Raises:
        httpx.HTTPError: On network or HTTP errors.
        ValueError:      When the response is not a JSON dict.
    """
    params = {"obdobi": timeframe, "kapitola": chapter_id}
    data = fetch_json(endpoint, client, params=params)
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected a JSON dict from summary endpoint '{endpoint}' "
            f"(timeframe={timeframe}, chapter={chapter_id}), "
            f"got {type(data).__name__}"
        )
    return data
