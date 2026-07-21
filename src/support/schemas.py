"""
schemas.py — JSON Schema indexing and GitLab Snippet synchronization.
"""

import json
import logging
from datetime import date
from pathlib import Path

import httpx

from src.support.getenv import config
from src.support.storage import upsert_snippet_files

logger = logging.getLogger(__name__)

MISC_FILENAME = "misc.json"
DEFAULT_MISC_SNIPPET_ENDPOINT = (
    "https://gitlab.h163.xyz/api/v4/projects/openbudget%2Fdata-pipeline/snippets/5"
)


def build_misc_index(
    schema_filenames: list[str],
    snippet_api_url: str,
    snippet_web_url: str = "",
    today_str: str | None = None,
) -> dict:
    """Build the dictionary representing misc.json according to misc.schema.json.

    Args:
        schema_filenames: List of schema filenames (e.g. ['config.schema.json', ...]).
        snippet_api_url:  Full GitLab API URL of the misc snippet (snippet 5).
        snippet_web_url:  Web interface URL of the misc snippet (optional).
        today_str:        ISO date string (YYYY-MM-DD), defaults to date.today().

    Returns:
        Dict conforming to misc.schema.json.
    """
    if today_str is None:
        today_str = date.today().isoformat()

    web_url = snippet_web_url or snippet_api_url

    content = []
    for fn in sorted(schema_filenames):
        if snippet_web_url:
            raw_link = f"{snippet_web_url.rstrip('/')}/raw/main/{fn}"
        else:
            raw_link = f"{snippet_api_url.rstrip('/')}/files/main/{fn}/raw"

        content.append(
            {
                "type": "schema/json",
                "link": raw_link,
                "lastUpdate": today_str,
                "comment": fn,
            }
        )

    return {
        "name": "miscelanious",
        "link": web_url,
        "api": snippet_api_url,
        "content": content,
    }


def push_schemas_to_snippet(
    cfg: config,
    misc_snippet_target: str = DEFAULT_MISC_SNIPPET_ENDPOINT,
    schemas_dir: str | Path = "schemas",
    client: httpx.Client | None = None,
) -> list[str]:
    """Read all JSON schemas in schemas_dir and push them to the misc snippet (snippet 5),
    and update misc.json in the main snippet (snippet 2).

    Args:
        cfg:                  Loaded pipeline config object.
        misc_snippet_target:  Full GitLab Snippet API endpoint for the misc snippet (defaults to snippet 5).
        schemas_dir:          Path to directory containing schema JSON files.
        client:               httpx.Client instance (required).

    Returns:
        List of schema file paths that were pushed.

    Raises:
        ValueError:        When client is missing, directory has no schema JSON files,
                           or a schema file contains malformed JSON.
        FileNotFoundError: When schemas_dir does not exist.
    """
    if client is None:
        raise ValueError("client parameter is required")

    path = Path(schemas_dir)
    if not path.is_dir():
        raise FileNotFoundError(f"Schemas directory not found: {schemas_dir}")

    schema_files = sorted([f for f in path.iterdir() if f.is_file() and f.name.endswith(".json")])
    if not schema_files:
        raise ValueError(f"No schema JSON files found in {schemas_dir}")

    web_url = misc_snippet_target
    try:
        resp = client.get(misc_snippet_target, headers={"PRIVATE-TOKEN": cfg.token})
        if resp.status_code == 200:
            meta = resp.json()
            web_url = meta.get("web_url", misc_snippet_target)
    except Exception as exc:
        logger.warning("Could not fetch misc snippet metadata for web_url: %s", exc)

    files_to_push = []
    schema_names = []

    for sfile in schema_files:
        fn = sfile.name
        raw_text = sfile.read_text(encoding="utf-8")
        try:
            json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in schema file '{fn}': {exc}") from exc

        files_to_push.append(
            {
                "file_path": fn,
                "content": raw_text,
            }
        )
        schema_names.append(fn)

    # 1. Push schema files to the dedicated misc snippet (snippet 5)
    upsert_snippet_files(
        snippet_target=misc_snippet_target,
        files=files_to_push,
        token=cfg.token,
        base_url=cfg.base_url,
        client=client,
    )

    # 2. Build misc.json index and update misc.json in the main snippet (snippet 2)
    misc_dict = build_misc_index(
        schema_filenames=schema_names,
        snippet_api_url=misc_snippet_target,
        snippet_web_url=web_url,
    )
    misc_json = json.dumps(misc_dict, indent=2, ensure_ascii=False)

    try:
        upsert_snippet_files(
            snippet_target=cfg.endpoint,
            files=[{"file_path": MISC_FILENAME, "content": misc_json}],
            token=cfg.token,
            base_url=cfg.base_url,
            client=client,
        )
    except Exception as exc:
        logger.warning("Could not update misc.json index in main snippet: %s", exc)

    pushed_paths = [f["file_path"] for f in files_to_push]
    logger.info(
        "Successfully pushed %d schema file(s) to snippet 5 and updated %s in main snippet",
        len(schema_names),
        MISC_FILENAME,
    )
    return pushed_paths
