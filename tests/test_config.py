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
