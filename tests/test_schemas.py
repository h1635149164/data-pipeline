"""
test_schemas.py — Dedicated unit tests for src/support/schemas.py.
"""

from pathlib import Path

import httpx
import pytest
import respx

from src.support.getenv import config
from src.support.schemas import build_misc_index, push_schemas_to_snippet


def test_build_misc_index_basic() -> None:
    """Test build_misc_index generates valid dict matching misc.schema.json layout."""
    filenames = ["target.schema.json", "config.schema.json"]
    api_url = "https://gitlab.example.com/api/v4/projects/1/snippets/5"
    web_url = "https://gitlab.example.com/org/repo/-/snippets/5"
    today = "2026-07-21"

    result = build_misc_index(
        schema_filenames=filenames,
        snippet_api_url=api_url,
        snippet_web_url=web_url,
        today_str=today,
    )

    assert result["name"] == "miscelanious"
    assert result["link"] == web_url
    assert result["api"] == api_url
    assert len(result["content"]) == 2

    # Should be sorted by filename
    first = result["content"][0]
    assert first["comment"] == "config.schema.json"
    assert first["lastUpdate"] == today
    assert first["type"] == "schema/json"
    assert first["link"] == f"{web_url}/raw/main/config.schema.json"

    second = result["content"][1]
    assert second["comment"] == "target.schema.json"
    assert second["link"] == f"{web_url}/raw/main/target.schema.json"


def test_build_misc_index_fallback_web_url() -> None:
    """Test build_misc_index when snippet_web_url is empty."""
    filenames = ["test.schema.json"]
    api_url = "https://gitlab.example.com/api/v4/snippets/5"

    result = build_misc_index(
        schema_filenames=filenames,
        snippet_api_url=api_url,
        snippet_web_url="",
    )

    assert result["link"] == api_url
    assert result["content"][0]["link"] == "https://gitlab.example.com/api/v4/snippets/5/files/main/test.schema.json/raw"


def test_push_schemas_to_snippet_no_client(tmp_path: Path) -> None:
    """Test push_schemas_to_snippet raises ValueError when client is None."""
    cfg = config(endpoint="https://gitlab.example.com/api/v4/snippets/2", token="tok", interval=3600)
    with pytest.raises(ValueError, match="client parameter is required"):
        push_schemas_to_snippet(cfg, schemas_dir=tmp_path, client=None)


def test_push_schemas_to_snippet_missing_dir(tmp_path: Path) -> None:
    """Test push_schemas_to_snippet raises FileNotFoundError for non-existent directory."""
    cfg = config(endpoint="https://gitlab.example.com/api/v4/snippets/2", token="tok", interval=3600)
    missing_dir = tmp_path / "non_existent"
    with httpx.Client() as client:
        with pytest.raises(FileNotFoundError, match="Schemas directory not found"):
            push_schemas_to_snippet(cfg, schemas_dir=missing_dir, client=client)


def test_push_schemas_to_snippet_empty_dir(tmp_path: Path) -> None:
    """Test push_schemas_to_snippet raises ValueError when no json schemas found."""
    cfg = config(endpoint="https://gitlab.example.com/api/v4/snippets/2", token="tok", interval=3600)
    with httpx.Client() as client:
        with pytest.raises(ValueError, match="No schema JSON files found"):
            push_schemas_to_snippet(cfg, schemas_dir=tmp_path, client=client)


def test_push_schemas_to_snippet_malformed_json(tmp_path: Path) -> None:
    """Test push_schemas_to_snippet raises ValueError when a schema file is invalid JSON."""
    cfg = config(endpoint="https://gitlab.example.com/api/v4/snippets/2", token="tok", interval=3600)
    bad_file = tmp_path / "bad.schema.json"
    bad_file.write_text("{ invalid json }", encoding="utf-8")

    with httpx.Client() as client:
        with pytest.raises(ValueError, match="Malformed JSON in schema file"):
            push_schemas_to_snippet(cfg, schemas_dir=tmp_path, client=client)


@respx.mock
def test_push_schemas_to_snippet_success(tmp_path: Path) -> None:
    """Test push_schemas_to_snippet successfully pushes schema files to misc snippet and updates main snippet."""
    cfg = config(endpoint="https://gitlab.example.com/api/v4/snippets/2", token="tok", interval=3600)
    misc_endpoint = "https://gitlab.example.com/api/v4/snippets/5"

    # Create dummy schema files
    (tmp_path / "a.schema.json").write_text('{"title": "Schema A"}', encoding="utf-8")
    (tmp_path / "b.schema.json").write_text('{"title": "Schema B"}', encoding="utf-8")

    # Mock snippet 5 GET & PUT
    respx.get(misc_endpoint).respond(
        json={"id": 5, "web_url": "https://gitlab.example.com/snippets/5", "files": []}
    )
    respx.put(misc_endpoint).respond(
        json={"id": 5, "title": "misc"}
    )

    # Mock snippet 2 GET & PUT
    respx.get("https://gitlab.example.com/api/v4/snippets/2").respond(
        json={"id": 2, "web_url": "https://gitlab.example.com/snippets/2", "files": []}
    )
    respx.put("https://gitlab.example.com/api/v4/snippets/2").respond(
        json={"id": 2, "title": "open-data"}
    )

    with httpx.Client() as client:
        pushed = push_schemas_to_snippet(cfg, misc_snippet_target=misc_endpoint, schemas_dir=tmp_path, client=client)

    assert len(pushed) == 2
    assert "a.schema.json" in pushed
    assert "b.schema.json" in pushed


@respx.mock
def test_push_schemas_to_snippet_filtered(tmp_path: Path) -> None:
    """Test push_schemas_to_snippet only pushes files matching files_filter."""
    cfg = config(endpoint="https://gitlab.example.com/api/v4/snippets/2", token="tok", interval=3600)
    misc_endpoint = "https://gitlab.example.com/api/v4/snippets/5"

    (tmp_path / "a.schema.json").write_text('{"title": "Schema A"}', encoding="utf-8")
    (tmp_path / "b.schema.json").write_text('{"title": "Schema B"}', encoding="utf-8")

    respx.get(misc_endpoint).respond(json={"id": 5, "web_url": "https://gitlab.example.com/snippets/5", "files": []})
    respx.put(misc_endpoint).respond(json={"id": 5, "title": "misc"})
    respx.get("https://gitlab.example.com/api/v4/snippets/2").respond(json={"id": 2, "web_url": "https://gitlab.example.com/snippets/2", "files": []})
    respx.put("https://gitlab.example.com/api/v4/snippets/2").respond(json={"id": 2, "title": "open-data"})

    with httpx.Client() as client:
        pushed = push_schemas_to_snippet(
            cfg,
            misc_snippet_target=misc_endpoint,
            schemas_dir=tmp_path,
            client=client,
            files_filter=["schemas/a.schema.json"],
        )

    assert len(pushed) == 1
    assert pushed == ["a.schema.json"]


@respx.mock
def test_push_schemas_to_snippet_filtered_no_matches(tmp_path: Path) -> None:
    """Test push_schemas_to_snippet returns empty list when files_filter yields no matches."""
    cfg = config(endpoint="https://gitlab.example.com/api/v4/snippets/2", token="tok", interval=3600)
    misc_endpoint = "https://gitlab.example.com/api/v4/snippets/5"

    (tmp_path / "a.schema.json").write_text('{"title": "Schema A"}', encoding="utf-8")

    with httpx.Client() as client:
        pushed = push_schemas_to_snippet(
            cfg,
            misc_snippet_target=misc_endpoint,
            schemas_dir=tmp_path,
            client=client,
            files_filter=["schemas/nonexistent.schema.json"],
        )

    assert pushed == []

