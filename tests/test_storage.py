"""tests/test_storage.py — Unit tests for src/support/storage.py"""

import json
import httpx
import pytest
import respx

from src.support.storage import list_snippet_files, read_snippet_file, upsert_snippet_files

TOKEN = "test-token-abc"
BASE_URL = "https://gitlab.example.com"
SNIPPET_TARGET = "99"
FULL_ENDPOINT = "https://gitlab.example.com/api/v4/projects/myproj/snippets/99"
FILENAME = "kapitola.json"
CONTENT = "test content"


# ---------------------------------------------------------------------------
# list_snippet_files
# ---------------------------------------------------------------------------


class TestListSnippetFiles:
    @respx.mock
    def test_happy_path_snippet_id(self):
        url = f"{BASE_URL}/api/v4/snippets/{SNIPPET_TARGET}"
        respx.get(url).mock(
            return_value=httpx.Response(200, json={"files": [{"path": "a.txt"}, {"path": "b.txt"}]})
        )
        with httpx.Client() as client:
            result = list_snippet_files(SNIPPET_TARGET, TOKEN, BASE_URL, client)
        assert result == ["a.txt", "b.txt"]

    @respx.mock
    def test_happy_path_full_url(self):
        respx.get(FULL_ENDPOINT).mock(
            return_value=httpx.Response(200, json={"files": [{"path": "c.txt"}]})
        )
        with httpx.Client() as client:
            result = list_snippet_files(FULL_ENDPOINT, TOKEN, client=client)
        assert result == ["c.txt"]

    @respx.mock
    def test_invalid_json(self):
        url = f"{BASE_URL}/api/v4/snippets/{SNIPPET_TARGET}"
        respx.get(url).mock(return_value=httpx.Response(200, text="not json"))
        with httpx.Client() as client:
            with pytest.raises(ValueError, match="Invalid JSON"):
                list_snippet_files(SNIPPET_TARGET, TOKEN, BASE_URL, client)


# ---------------------------------------------------------------------------
# read_snippet_file
# ---------------------------------------------------------------------------


class TestReadSnippetFile:
    @respx.mock
    def test_happy_path_project_snippet(self):
        url = f"{FULL_ENDPOINT}/files/main/{FILENAME}/raw"
        respx.get(url).mock(return_value=httpx.Response(200, text=CONTENT))
        with httpx.Client() as client:
            result = read_snippet_file(FULL_ENDPOINT, FILENAME, TOKEN, client=client)
        assert result == CONTENT

    @respx.mock
    def test_happy_path_fallback_personal_snippet(self):
        url1 = f"{FULL_ENDPOINT}/files/main/{FILENAME}/raw"
        url2 = f"{FULL_ENDPOINT}/raw/{FILENAME}"
        respx.get(url1).mock(return_value=httpx.Response(404))
        respx.get(url2).mock(return_value=httpx.Response(200, text=CONTENT))
        with httpx.Client() as client:
            result = read_snippet_file(FULL_ENDPOINT, FILENAME, TOKEN, client=client)
        assert result == CONTENT


# ---------------------------------------------------------------------------
# upsert_snippet_files
# ---------------------------------------------------------------------------


class TestUpsertSnippetFiles:
    @respx.mock
    def test_happy_path_create_and_update(self):
        respx.get(FULL_ENDPOINT).mock(
            return_value=httpx.Response(200, json={"files": [{"path": "existing.txt"}]})
        )
        
        captured_body = []
        def put_handler(request: httpx.Request) -> httpx.Response:
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={})

        respx.put(FULL_ENDPOINT).mock(side_effect=put_handler)

        files_to_upsert = [
            {"file_path": "existing.txt", "content": "updated content"},
            {"file_path": "new.txt", "content": "new content"},
        ]

        with httpx.Client() as client:
            upsert_snippet_files(FULL_ENDPOINT, files_to_upsert, TOKEN, client=client)

        assert len(captured_body) == 1
        actions = captured_body[0]["files"]
        assert len(actions) == 2
        assert actions[0] == {"action": "update", "file_path": "existing.txt", "content": "updated content"}
        assert actions[1] == {"action": "create", "file_path": "new.txt", "content": "new content"}
