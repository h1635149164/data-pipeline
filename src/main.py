"""
main.py — Pipeline entrypoint.

Loads configuration from:
  1. OPENBUDGET_CONFIG env-var pointing to a config.json path (preferred).
  2. Individual OPENBUDGET_* environment variables (fallback).

Loads target endpoint configuration from:
  1. OPENBUDGET_TARGET env-var pointing to a target.json path (preferred).
  2. Individual OPENBUDGET_TARGET_* environment variables (fallback).

Then runs run_task() on a fixed interval loop using a shared httpx.Client.

Auto-reload:
  When OPENBUDGET_AUTO_RELOAD=true (default: false), the pipeline checks
  config.json and target.json for mtime changes every OPENBUDGET_RELOAD_INTERVAL
  seconds (default: 300) during the idle sleep between sync cycles. When a change
  is detected the in-memory config/target objects are reloaded without restarting
  the process.
"""

import logging
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.support.getenv import config, target
from src.task import run_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Auto-reload env-var names.
_ENV_AUTO_RELOAD = "OPENBUDGET_AUTO_RELOAD"
_ENV_RELOAD_INTERVAL = "OPENBUDGET_RELOAD_INTERVAL"
_DEFAULT_RELOAD_INTERVAL = 300  # seconds


def load_config() -> config:
    """Load pipeline config from a JSON file or environment variables.

    The env-var ``OPENBUDGET_CONFIG`` may point to a JSON config file path.
    If it is not set (or the file is missing), ``config.from_env()`` is used.

    Returns:
        Loaded config instance.

    Raises:
        ValueError: When neither source yields a valid config.
    """
    json_path = os.environ.get("OPENBUDGET_CONFIG")
    if json_path:
        try:
            cfg = config.from_json(json_path)
            logger.info("Config loaded from file: %s", json_path)
            return cfg
        except (FileNotFoundError, ValueError) as exc:
            logger.warning(
                "Could not load config from file (%s); falling back to env vars", exc
            )

    cfg = config.from_env()
    logger.info("Config loaded from environment variables")
    return cfg


def load_target() -> target:
    """Load target endpoint config from a JSON file or environment variables.

    The env-var ``OPENBUDGET_TARGET`` may point to a JSON target config file.
    If it is not set (or the file is missing), ``target.from_env()`` is used.

    Returns:
        Loaded target instance.

    Raises:
        ValueError: When neither source yields a valid target config.
    """
    json_path = os.environ.get("OPENBUDGET_TARGET")
    if json_path:
        try:
            tgt = target.from_json(json_path)
            logger.info("Target config loaded from file: %s", json_path)
            return tgt
        except (FileNotFoundError, ValueError) as exc:
            logger.warning(
                "Could not load target config from file (%s); falling back to env vars", exc
            )

    tgt = target.from_env()
    logger.info("Target config loaded from environment variables")
    return tgt


def _file_mtime(path: str | None) -> float | None:
    """Return the mtime of a file, or None if it does not exist."""
    if path is None:
        return None
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def _sleep_with_reload_check(
    cfg: config,
    tgt: target,
    total_seconds: int,
    reload_interval: int,
    cfg_path: str | None,
    tgt_path: str | None,
    cfg_mtime: float | None,
    tgt_mtime: float | None,
) -> tuple[config, target, float | None, float | None]:
    """Sleep for *total_seconds* in chunks, checking for config/target file changes.

    Checks file mtimes every *reload_interval* seconds. When a change is
    detected, the config or target object is reloaded transparently.

    Args:
        cfg:             Current config object.
        tgt:             Current target object.
        total_seconds:   Total sleep duration (seconds).
        reload_interval: How often to wake and check for file changes.
        cfg_path:        Path to config.json (or None).
        tgt_path:        Path to target.json (or None).
        cfg_mtime:       Last known mtime of config.json.
        tgt_mtime:       Last known mtime of target.json.

    Returns:
        Tuple of (cfg, tgt, cfg_mtime, tgt_mtime) — potentially updated if
        file changes were detected.
    """
    elapsed = 0
    chunk = min(reload_interval, total_seconds)

    while elapsed < total_seconds:
        sleep_for = min(chunk, total_seconds - elapsed)
        time.sleep(sleep_for)
        elapsed += sleep_for

        new_cfg_mtime = _file_mtime(cfg_path)
        new_tgt_mtime = _file_mtime(tgt_path)

        if cfg_path and new_cfg_mtime is not None and new_cfg_mtime != cfg_mtime:
            logger.info("config.json changed — reloading config")
            try:
                cfg = config.from_json(cfg_path)
                cfg_mtime = new_cfg_mtime
                logger.info("Config reloaded successfully from %s", cfg_path)
            except Exception as exc:
                logger.warning("Failed to reload config from %s: %s", cfg_path, exc)

        if tgt_path and new_tgt_mtime is not None and new_tgt_mtime != tgt_mtime:
            logger.info("target.json changed — reloading target config")
            try:
                tgt = target.from_json(tgt_path)
                tgt_mtime = new_tgt_mtime
                logger.info("Target reloaded successfully from %s", tgt_path)
            except Exception as exc:
                logger.warning("Failed to reload target from %s: %s", tgt_path, exc)

    return cfg, tgt, cfg_mtime, tgt_mtime


def main() -> None:
    """Start the pipeline loop."""
    cfg = load_config()
    tgt = load_target()

    auto_reload = os.environ.get(_ENV_AUTO_RELOAD, "false").lower() in ("1", "true", "yes")
    reload_interval = int(os.environ.get(_ENV_RELOAD_INTERVAL, _DEFAULT_RELOAD_INTERVAL))

    cfg_path: str | None = os.environ.get("OPENBUDGET_CONFIG") or getattr(cfg, "conf_path", None)
    tgt_path: str | None = os.environ.get("OPENBUDGET_TARGET")

    cfg_mtime = _file_mtime(cfg_path)
    tgt_mtime = _file_mtime(tgt_path)

    logger.info(
        "Pipeline started — snippet=%s  chapters=%s  interval=%ds  auto_reload=%s",
        cfg.endpoint,
        tgt.chapters_endpoint,
        cfg.interval,
        auto_reload,
    )

    with httpx.Client(timeout=30.0) as client:
        while True:
            try:
                run_task(cfg, tgt, client)
            except Exception as exc:
                logger.error("Unhandled error in run_task: %s", exc, exc_info=True)

            logger.info("Sleeping for %d seconds...", cfg.interval)

            if auto_reload:
                cfg, tgt, cfg_mtime, tgt_mtime = _sleep_with_reload_check(
                    cfg=cfg,
                    tgt=tgt,
                    total_seconds=cfg.interval,
                    reload_interval=reload_interval,
                    cfg_path=cfg_path,
                    tgt_path=tgt_path,
                    cfg_mtime=cfg_mtime,
                    tgt_mtime=tgt_mtime,
                )
            else:
                time.sleep(cfg.interval)


if __name__ == "__main__":
    main()
