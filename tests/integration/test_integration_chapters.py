"""tests/integration/test_integration_chapters.py — Live API tests for state chapters."""

import httpx
import pytest

from src.support.getenv import target
from src.support.scrapper import fetch_chapters

pytestmark = pytest.mark.integration


def _get_target() -> target | None:
    try:
        return target.from_json("target.json")
    except (FileNotFoundError, ValueError):
        return None


tgt = _get_target()
pytestmark_skip = pytest.mark.skipif(
    tgt is None or not tgt.chapters_endpoint.startswith("http"),
    reason="target.json is missing or contains placeholder values",
)


@pytestmark_skip
def test_live_fetch_chapters():
    """Fetch chapters from the live state API and verify the structure."""
    assert tgt is not None
    with httpx.Client(timeout=10.0) as client:
        chapters = fetch_chapters(tgt.chapters_endpoint, client)

    assert isinstance(chapters, list)
    assert len(chapters) > 0

    first = chapters[0]
    assert "id" in first
    assert "name" in first
    assert "startDate" in first
    assert "endDate" in first
    assert "expired" in first
