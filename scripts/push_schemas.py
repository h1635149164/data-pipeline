"""
push_schemas.py — Utility script to push local JSON schemas to GitLab Snippet 5 (misc snippet).

Usage:
    uv run python scripts/push_schemas.py
    # Or with OPENBUDGET_CONFIG env-var:
    OPENBUDGET_CONFIG=config.json uv run python scripts/push_schemas.py
"""

import logging
import os
import sys
from pathlib import Path

import httpx

# Ensure project root is in sys.path when script is executed directly
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.support.getenv import config  # noqa: E402
from src.support.schemas import DEFAULT_MISC_SNIPPET_ENDPOINT, push_schemas_to_snippet  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Execute schema push utility."""
    files_filter = sys.argv[1:] if len(sys.argv) > 1 else None

    config_env = os.environ.get("OPENBUDGET_CONFIG")
    if config_env:
        cfg = config.from_json(config_env)
        logger.info("Loaded config from OPENBUDGET_CONFIG file: %s", config_env)
    elif (project_root / "config.json").exists():
        cfg = config.from_json(str(project_root / "config.json"))
        logger.info("Loaded config from default config.json file")
    else:
        cfg = config.from_env()
        logger.info("Loaded config from environment variables")

    misc_snippet_target = os.environ.get("OPENBUDGET_MISC_ENDPOINT", DEFAULT_MISC_SNIPPET_ENDPOINT)
    schemas_dir = project_root / "schemas"
    if files_filter:
        logger.info("Pushing specific schemas %s from '%s' to misc snippet %s...", files_filter, schemas_dir, misc_snippet_target)
    else:
        logger.info("Pushing schemas from '%s' to misc snippet %s...", schemas_dir, misc_snippet_target)

    with httpx.Client(timeout=30.0) as client:
        pushed = push_schemas_to_snippet(
            cfg,
            misc_snippet_target=misc_snippet_target,
            schemas_dir=schemas_dir,
            client=client,
            files_filter=files_filter,
        )

    logger.info("Successfully pushed %d schema file(s):", len(pushed))
    for item in pushed:
        logger.info("  - %s", item)


if __name__ == "__main__":
    main()
