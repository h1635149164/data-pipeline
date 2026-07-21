"""tests/test_getenv.py — Unit tests for src/support/getenv.py"""

import json

import pytest

from src.support.getenv import config, target


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_derived_properties(self):
        cfg = config(
            endpoint="https://gitlab.h163.xyz/api/v4/snippets/2",
            token="mytoken",
            interval=60,
        )
        assert cfg.base_url == "https://gitlab.h163.xyz"
        assert cfg.api_prefix == "https://gitlab.h163.xyz/api/v4"
        assert cfg.main_snippet_id == "2"

    def test_snippet_id_extraction_failure(self):
        cfg = config(
            endpoint="https://gitlab.example.com/api/v4/snippets/notanumber",
            token="mytoken",
            interval=60,
        )
        with pytest.raises(ValueError, match="Cannot extract snippet ID"):
            _ = cfg.main_snippet_id


class TestConfigFromJson:
    def test_happy_path(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "endpoint": "https://example.com/api/v4/snippets/42",
                    "token": "mytoken",
                    "interval": 60,
                }
            )
        )
        cfg = config.from_json(str(cfg_file))
        assert cfg.endpoint == "https://example.com/api/v4/snippets/42"
        assert cfg.token == "mytoken"
        assert cfg.interval == 60
        assert cfg.conf_path == str(cfg_file)

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            config.from_json(str(tmp_path / "nonexistent.json"))

    def test_missing_endpoint_key(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"token": "t", "interval": 10}))
        with pytest.raises(ValueError, match="Missing required key"):
            config.from_json(str(cfg_file))


class TestConfigFromEnv:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("OPENBUDGET_ENDPOINT", "https://env.example.com/api/v4/snippets/99")
        monkeypatch.setenv("OPENBUDGET_TOKEN", "envtoken")
        monkeypatch.setenv("OPENBUDGET_INTERVAL", "30")

        cfg = config.from_env()
        assert cfg.endpoint == "https://env.example.com/api/v4/snippets/99"
        assert cfg.token == "envtoken"
        assert cfg.interval == 30
        assert cfg.conf_path is None

    def test_missing_endpoint_env(self, monkeypatch):
        monkeypatch.delenv("OPENBUDGET_ENDPOINT", raising=False)
        monkeypatch.setenv("OPENBUDGET_TOKEN", "t")
        monkeypatch.setenv("OPENBUDGET_INTERVAL", "10")
        with pytest.raises(ValueError, match="Missing required environment variable"):
            config.from_env()


# ---------------------------------------------------------------------------
# target
# ---------------------------------------------------------------------------


class TestTargetFromJson:
    def test_happy_path(self, tmp_path):
        tgt_file = tmp_path / "target.json"
        tgt_file.write_text(
            json.dumps(
                {
                    "chapters_endpoint": "https://state.gov/api/c",
                    "summary_endpoint": "https://state.gov/api/s",
                }
            )
        )
        tgt = target.from_json(str(tgt_file))
        assert tgt.chapters_endpoint == "https://state.gov/api/c"
        assert tgt.summary_endpoint == "https://state.gov/api/s"

    def test_missing_key(self, tmp_path):
        f = tmp_path / "t.json"
        f.write_text(json.dumps({"chapters_endpoint": "http://x"}))
        with pytest.raises(ValueError, match="Missing required key"):
            target.from_json(str(f))


class TestTargetFromEnv:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("OPENBUDGET_TARGET_CHAPTERS_ENDPOINT", "http://c")
        monkeypatch.setenv("OPENBUDGET_TARGET_SUMMARY_ENDPOINT", "http://s")
        tgt = target.from_env()
        assert tgt.chapters_endpoint == "http://c"
        assert tgt.summary_endpoint == "http://s"

    def test_missing_env(self, monkeypatch):
        monkeypatch.setenv("OPENBUDGET_TARGET_CHAPTERS_ENDPOINT", "http://c")
        monkeypatch.delenv("OPENBUDGET_TARGET_SUMMARY_ENDPOINT", raising=False)
        with pytest.raises(ValueError, match="Missing required environment variable"):
            target.from_env()
