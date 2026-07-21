"""tests/integration/test_integration_storage.py — Live API tests for GitLab Snippet storage."""

import json

import httpx
import pytest

from src.support.getenv import config
from src.support.storage import list_snippet_files, read_snippet_file, upsert_snippet_files

pytestmark = pytest.mark.integration


def _get_config() -> config | None:
    try:
        return config.from_json("config.json")
    except (FileNotFoundError, ValueError):
        return None


cfg = _get_config()
pytestmark_skip = pytest.mark.skipif(
    cfg is None or "YOUR_MAIN_SNIPPET_ID" in cfg.endpoint or "xxxxxxxxxxxxxxxxxxxx" in cfg.token,
    reason="config.json is missing or contains placeholder values",
)


@pytestmark_skip
def test_live_gitlab_snippet_roundtrip():
    """Test reading and writing to the actual GitLab snippet configured in config.json."""
    cfg = config.from_json("config.json")
    test_filename = "_integration_test.json"
    test_content = json.dumps([{"test": "data", "id": 123}])

    with httpx.Client(timeout=10.0) as client:
        # 1. Upsert file
        upsert_snippet_files(
            snippet_target=cfg.endpoint,
            files=[{"file_path": test_filename, "content": test_content}],
            token=cfg.token,
            base_url=cfg.base_url,
            client=client,
        )

        # 2. List files and verify it exists
        files = list_snippet_files(
            snippet_target=cfg.endpoint,
            token=cfg.token,
            base_url=cfg.base_url,
            client=client,
        )
        assert test_filename in files

        # 3. Read file back and verify content
        read_content = read_snippet_file(
            snippet_target=cfg.endpoint,
            file_path=test_filename,
            token=cfg.token,
            base_url=cfg.base_url,
            client=client,
        )
        assert read_content == test_content
