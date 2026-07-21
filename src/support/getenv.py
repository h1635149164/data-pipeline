"""
getenv.py — Configuration loading for the OpenBudget data pipeline.

Two config objects:

  config  — Pipeline credentials and GitLab Snippets connection settings.
             Loaded from config.json (or env vars OPENBUDGET_*).
             The single `endpoint` field is the full GitLab Snippet API URL,
             e.g. https://gitlab.h163.xyz/api/v4/snippets/2, from which
             base_url and main_snippet_id are derived automatically.

  target  — Czech state government source API endpoints.
             Loaded from target.json (or env vars OPENBUDGET_TARGET_*).
"""

import json
import os
import time
from urllib.parse import urlparse

# ── Environment variable names ───────────────────────────────────────────────

_ENV_ENDPOINT = "OPENBUDGET_ENDPOINT"
_ENV_TOKEN = "OPENBUDGET_TOKEN"
_ENV_INTERVAL = "OPENBUDGET_INTERVAL"

_ENV_TARGET_CHAPTERS = "OPENBUDGET_TARGET_CHAPTERS_ENDPOINT"
_ENV_TARGET_SUMMARY = "OPENBUDGET_TARGET_SUMMARY_ENDPOINT"
_ENV_TARGET_BACKLOG_START_YEAR = "OPENBUDGET_TARGET_BACKLOG_START_YEAR"


# ── config ───────────────────────────────────────────────────────────────────


class config:
    """Pipeline credentials and GitLab Snippets connection settings.

    The ``endpoint`` field is the full GitLab Snippet API URL of the
    *main* snippet, e.g.::

        https://gitlab.h163.xyz/api/v4/snippets/2

    ``base_url``, ``api_prefix``, and ``main_snippet_id`` are derived from it
    automatically.

    Attributes:
        endpoint:         Full GitLab Snippet API URL of the main snippet.
        token:            GitLab Personal Access Token (PRIVATE-TOKEN).
        interval:         Seconds between pipeline sync cycles.
        conf_path:        Path to the JSON file that was loaded (or None).
        last_changed:     Unix timestamp when this config instance was created.
    """

    def __init__(
        self,
        endpoint: str,
        token: str,
        interval: int,
        conf_path: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.token = token
        self.interval = interval
        self.conf_path = conf_path
        self.last_changed = time.time()

    # ── Derived properties ───────────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        """GitLab instance base URL (scheme + netloc).

        Example: ``https://gitlab.h163.xyz``
        """
        parsed = urlparse(self.endpoint)
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def api_prefix(self) -> str:
        """GitLab API prefix (base_url + /api/v4).

        Example: ``https://gitlab.h163.xyz/api/v4``
        """
        return f"{self.base_url}/api/v4"

    @property
    def main_snippet_id(self) -> str:
        """Main snippet ID extracted from the endpoint URL.

        Example: ``"2"`` from ``.../api/v4/snippets/2``

        Raises:
            ValueError: When the endpoint URL does not end with a numeric ID.
        """
        snippet_id = self.endpoint.rstrip("/").rsplit("/", 1)[-1]
        if not snippet_id.isdigit():
            raise ValueError(
                f"Cannot extract snippet ID from endpoint URL: '{self.endpoint}'. "
                "Expected the URL to end with a numeric snippet ID "
                "(e.g. https://gitlab.example.com/api/v4/snippets/42)."
            )
        return snippet_id

    # ── Loaders ──────────────────────────────────────────────────────────────

    @classmethod
    def from_json(cls, json_path: str) -> "config":
        """Load config from a JSON file.

        Expected keys: ``endpoint``, ``token``, ``interval``.

        Args:
            json_path: Path to the JSON config file.

        Raises:
            FileNotFoundError: When the JSON file does not exist.
            ValueError:        When the JSON is malformed or required keys are missing/invalid.
        """
        try:
            with open(json_path) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            raise FileNotFoundError(f"Config file not found: {json_path}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in config file '{json_path}': {exc}")

        try:
            return cls(
                endpoint=data["endpoint"],
                token=data["token"],
                interval=int(data["interval"]),
                conf_path=json_path,
            )
        except KeyError as exc:
            raise ValueError(f"Missing required key in config file: {exc}")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid value in config file: {exc}")

    @classmethod
    def from_env(cls) -> "config":
        """Load config from environment variables.

        Required variables: ``OPENBUDGET_ENDPOINT``, ``OPENBUDGET_TOKEN``,
        ``OPENBUDGET_INTERVAL``.

        Raises:
            ValueError: When required variables are missing or invalid.
        """
        try:
            return cls(
                endpoint=os.environ[_ENV_ENDPOINT],
                token=os.environ[_ENV_TOKEN],
                interval=int(os.environ[_ENV_INTERVAL]),
            )
        except KeyError as exc:
            raise ValueError(f"Missing required environment variable: {exc}")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid value in environment variable: {exc}")


# ── target ───────────────────────────────────────────────────────────────────


class target:
    """Czech state government source API endpoint configuration.

    Attributes:
        chapters_endpoint: Full URL for the chapter list API
                           (e.g. ``https://monitor.statnipokladna.gov.cz/api/kapitola``).
        summary_endpoint:  Full URL for the monthly budget summary API
                           (e.g. ``https://monitor.statnipokladna.gov.cz/api/rozpocet/souhrnny``).
    """

    def __init__(
        self,
        chapters_endpoint: str,
        summary_endpoint: str,
        backlog_start_year: int = 2010,
    ) -> None:
        self.chapters_endpoint = chapters_endpoint
        self.summary_endpoint = summary_endpoint
        self.backlog_start_year = backlog_start_year

    @classmethod
    def from_json(cls, json_path: str) -> "target":
        """Load target config from a JSON file.

        Expected keys: ``chapters_endpoint``, ``summary_endpoint``.

        Args:
            json_path: Path to the JSON target config file.

        Raises:
            FileNotFoundError: When the JSON file does not exist.
            ValueError:        When the JSON is malformed or required keys are missing/invalid.
        """
        try:
            with open(json_path) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            raise FileNotFoundError(f"Target config file not found: {json_path}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in target config file '{json_path}': {exc}")

        try:
            return cls(
                chapters_endpoint=data["chapters_endpoint"],
                summary_endpoint=data["summary_endpoint"],
                backlog_start_year=int(data.get("backlog_start_year", 2010)),
            )
        except KeyError as exc:
            raise ValueError(f"Missing required key in target config file: {exc}")

    @classmethod
    def from_env(cls) -> "target":
        """Load target config from environment variables.

        Required variables: ``OPENBUDGET_TARGET_CHAPTERS_ENDPOINT``,
        ``OPENBUDGET_TARGET_SUMMARY_ENDPOINT``.

        Raises:
            ValueError: When required variables are missing.
        """
        try:
            backlog_start = int(os.environ.get(_ENV_TARGET_BACKLOG_START_YEAR, 2010))
            return cls(
                chapters_endpoint=os.environ[_ENV_TARGET_CHAPTERS],
                summary_endpoint=os.environ[_ENV_TARGET_SUMMARY],
                backlog_start_year=backlog_start,
            )
        except KeyError as exc:
            raise ValueError(f"Missing required environment variable: {exc}")