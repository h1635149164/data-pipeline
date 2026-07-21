"""
storage.py — GitLab Snippets v4 API I/O for the OpenBudget pipeline.

All remote storage operations target a GitLab instance via its REST v4 API.
Functions support both Personal Snippet endpoints (/api/v4/snippets/:id)
and Project Snippet endpoints (/api/v4/projects/:project_id/snippets/:id).

GitLab Snippet API reference:
  List files:  GET  <snippet_endpoint>
  Read file:   GET  <snippet_endpoint>/files/main/:file_path/raw
               (fallback: GET <snippet_endpoint>/raw/:file_path)
  Upsert:      PUT  <snippet_endpoint> with files[] payload
"""

import httpx


def _gitlab_headers(token: str) -> dict[str, str]:
    """Return standard headers for GitLab API requests."""
    return {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    }


def _resolve_endpoint(snippet_target: str, base_url: str) -> str:
    """Resolve snippet target (ID or full URL) into a canonical endpoint URL.

    If ``snippet_target`` is a full HTTP(S) URL, returns it with trailing slashes stripped.
    Otherwise, builds ``f"{base_url}/api/v4/snippets/{snippet_target}"``.
    """
    if snippet_target.startswith("http://") or snippet_target.startswith("https://"):
        return snippet_target.rstrip("/")
    return f"{base_url.rstrip('/')}/api/v4/snippets/{snippet_target}"


def list_snippet_files(
    snippet_target: str,
    token: str,
    base_url: str = "",
    client: httpx.Client | None = None,
) -> list[str]:
    """Return the list of file paths stored in a GitLab Snippet.

    Calls ``GET <endpoint>`` and extracts the ``files[].path`` array from
    the snippet metadata response.

    Args:
        snippet_target: Full snippet endpoint URL (or numeric snippet ID).
        token:          GitLab Personal/Project Access Token.
        base_url:       GitLab instance base URL (optional if full URL passed).
        client:         httpx.Client instance.

    Returns:
        Sorted list of file path strings present in the snippet.
    """
    if client is None:
        raise ValueError("client parameter is required")

    url = _resolve_endpoint(snippet_target, base_url)
    response = client.get(url, headers=_gitlab_headers(token))
    response.raise_for_status()
    try:
        data = response.json()
    except Exception as exc:
        raise ValueError(f"Invalid JSON in snippet metadata response: {exc}") from exc
    return sorted(f["path"] for f in data.get("files", []))


def read_snippet_file(
    snippet_target: str,
    file_path: str,
    token: str,
    base_url: str = "",
    client: httpx.Client | None = None,
) -> str:
    """Read the raw text content of a specific file within a GitLab Snippet.

    Tries ``GET <endpoint>/files/main/:file_path/raw`` first (Project Snippet API),
    falling back to ``GET <endpoint>/raw/:file_path`` (Personal Snippet API).

    Args:
        snippet_target: Full snippet endpoint URL (or numeric snippet ID).
        file_path:      Path of the file inside the snippet (e.g. ``"kapitola.json"``).
        token:          GitLab Access Token.
        base_url:       GitLab instance base URL.
        client:         httpx.Client instance.

    Returns:
        Raw file content as a string.
    """
    if client is None:
        raise ValueError("client parameter is required")

    endpoint_url = _resolve_endpoint(snippet_target, base_url)
    raw_url = f"{endpoint_url}/files/main/{file_path}/raw"
    headers = _gitlab_headers(token)

    response = client.get(raw_url, headers=headers)
    if response.status_code == 404:
        # Fallback to personal snippet raw URL format
        fallback_url = f"{endpoint_url}/raw/{file_path}"
        response = client.get(fallback_url, headers=headers)

    response.raise_for_status()
    return response.text


def upsert_snippet_files(
    snippet_target: str,
    files: list[dict],
    token: str,
    base_url: str = "",
    client: httpx.Client | None = None,
) -> None:
    """Create or update one or more files within a GitLab Snippet.

    Calls ``GET <endpoint>`` to list existing files, then issues a single
    ``PUT <endpoint>`` with ``"action": "create"`` or ``"update"`` per file.

    Args:
        snippet_target: Full snippet endpoint URL (or numeric snippet ID).
        files:          List of dicts with ``file_path`` (str) and ``content`` (str).
        token:          GitLab Access Token.
        base_url:       GitLab instance base URL.
        client:         httpx.Client instance.
    """
    if client is None:
        raise ValueError("client parameter is required")
    if not files:
        raise ValueError("files list must not be empty")

    endpoint_url = _resolve_endpoint(snippet_target, base_url)
    existing: set[str] = set(list_snippet_files(endpoint_url, token, base_url, client))

    actions: list[dict] = []
    for f in files:
        try:
            fp: str = f["file_path"]
            content: str = f["content"]
        except KeyError as exc:
            raise ValueError(f"Each file entry must have 'file_path' and 'content': missing {exc}") from exc

        action = "update" if fp in existing else "create"
        actions.append({"action": action, "file_path": fp, "content": content})

    response = client.put(endpoint_url, json={"files": actions}, headers=_gitlab_headers(token))
    response.raise_for_status()
