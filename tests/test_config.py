from pathlib import Path
import pytest
from mai_bench2.config import ConfigError, apply_overrides, load_config, requested_suites

def test_load_planner_only(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(
        '[planner]\nbase_url="http://p/v1"\napi_key="k"\nmodel="m"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.planner is not None
    assert cfg.replyer is None
    assert requested_suites(cfg) == ["planner"]

def test_missing_env(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(
        '[planner]\nbase_url="http://p/v1"\napi_key="${NOPE}"\nmodel="m"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="NOPE"):
        load_config(path, env={})

def test_suite_flag_keeps_e2e_even_if_unrunnable(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(
        '[planner]\nbase_url="http://p/v1"\napi_key="k"\nmodel="m"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    cfg.suite_flag = "e2e"
    assert requested_suites(cfg) == ["e2e"]


def test_load_run_concurrency(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text("[run]\nconcurrency = 8\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.run.concurrency == 8


def test_load_run_concurrency_defaults_to_1(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text("[run]\nsmoke = true\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.run.concurrency == 1


def _planner_toml(**extra: str) -> str:
    body = '[planner]\nbase_url="http://p/v1"\napi_key="k"\nmodel="m"\n'
    if extra:
        body += "\n".join(extra.values()) + "\n"
    return body


def test_load_assistant_prefill_omitted_is_false(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(_planner_toml(), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.planner is not None
    assert cfg.planner.assistant_prefill is False


def test_load_assistant_prefill_true(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(_planner_toml(prefill="assistant_prefill = true\n"), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.planner.assistant_prefill is True


def test_load_http_limit_omitted_is_none(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(_planner_toml(), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.planner is not None
    assert cfg.planner.http_limit is None


def test_load_http_limit_positive(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(_planner_toml(limit="http_limit = 2\n"), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.planner.http_limit == 2


def test_load_http_limit_zero_becomes_one(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(_planner_toml(limit="http_limit = 0\n"), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.planner.http_limit == 1


def test_load_http_limit_negative_becomes_one(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(_planner_toml(limit="http_limit = -3\n"), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.planner.http_limit == 1
