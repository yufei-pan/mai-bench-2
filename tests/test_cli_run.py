from mai_bench2.cli import parse_args
from mai_bench2.config import AppConfig, ConfigError, EndpointConfig, RunConfig, SuiteConfig, apply_overrides
from mai_bench2.cli import _missing_seat_error
import pytest

def test_explicit_e2e_without_replyer_errors():
    cfg = AppConfig(
        EndpointConfig("http://p/v1", "k", "m"),
        None,
        None,
        RunConfig(),
        SuiteConfig(),
        SuiteConfig(),
        SuiteConfig(smoke_n=4),
        "x",
        suite_flag="e2e",
    )
    with pytest.raises(ConfigError, match="e2e requires"):
        _missing_seat_error(cfg)


from pathlib import Path
import json
import sys

from mai_bench2.cli import console, find_config, run_suites
from mai_bench2.types import SuiteResult, UsageSplit

ROOT = Path("/mnt/klein/work/mai-bench-2")
_PLANNER = EndpointConfig("http://p/v1", "SECRET_KEY", "m")
_REPLYER = EndpointConfig("http://r/v1", "SECRET_KEY", "m")
_JUDGE = EndpointConfig("http://j/v1", "SECRET_KEY", "m")


def _cfg(**kwargs):
    return AppConfig(
        kwargs.get("planner", None),
        kwargs.get("replyer", None),
        kwargs.get("judge", None),
        kwargs.get("run", RunConfig()),
        SuiteConfig(),
        SuiteConfig(),
        SuiteConfig(smoke_n=4),
        kwargs.get("config_path", "x"),
        suite_flag=kwargs.get("suite_flag", None),
    )


def test_explicit_replyer_without_judge_errors():
    cfg = _cfg(replyer=_REPLYER, suite_flag="replyer")
    with pytest.raises(ConfigError, match="replyer requires"):
        _missing_seat_error(cfg)


def test_explicit_planner_without_seat_errors():
    cfg = _cfg(suite_flag="planner")
    with pytest.raises(ConfigError, match="planner requires"):
        _missing_seat_error(cfg)


def test_missing_seat_ok_without_suite_flag():
    _missing_seat_error(_cfg())


def test_apply_overrides_from_parse_args():
    args = parse_args(["run", "--suite", "replyer", "--full", "--persona", "official", "--no-cache"])
    cfg = apply_overrides(_cfg(planner=_PLANNER), args)
    assert cfg.suite_flag == "replyer"
    assert cfg.run.smoke is False
    assert cfg.run.persona == "official"
    assert cfg.run.no_cache is True


def test_find_config_explicit(tmp_path: Path):
    path = tmp_path / "my.toml"
    path.write_text("[planner]\n", encoding="utf-8")
    assert find_config(str(path)) == path.resolve()


def test_find_config_explicit_missing(tmp_path: Path):
    with pytest.raises(ConfigError):
        find_config(str(tmp_path / "nope.toml"))


def test_find_config_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.toml"
    path.write_text("[planner]\n", encoding="utf-8")
    assert find_config(None) == path.resolve()


def test_find_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    dest = home / ".config" / "mai-bench-2"
    dest.mkdir(parents=True)
    path = dest / "config.toml"
    path.write_text("[planner]\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    assert find_config(None) == path.resolve()


def test_find_config_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    with pytest.raises(ConfigError):
        find_config(None)


class _FakeClient:
    probed: list = []

    def __init__(self, endpoint, role, cache_dir, no_cache, create_fn=None, sleep_fn=None):
        self.endpoint = endpoint
        self.role = role
        self.cache_dir = cache_dir
        self.no_cache = no_cache

    def probe(self, messages, *, max_tokens=1):
        _FakeClient.probed.append((self.role, messages, max_tokens))


def _patch_clients_and_suites(monkeypatch, *, planner=None, replyer=None, e2e=None):
    _FakeClient.probed = []
    monkeypatch.setattr("mai_bench2.cli.ChatClient", _FakeClient)
    if planner is not None:
        monkeypatch.setattr("mai_bench2.cli.run_planner_suite", planner)
    if replyer is not None:
        monkeypatch.setattr("mai_bench2.cli.run_replyer_suite", replyer)
    if e2e is not None:
        monkeypatch.setattr("mai_bench2.cli.run_e2e_suite", e2e)


def test_run_suites_no_seats_exit_0():
    results, code = run_suites(_cfg(), root=ROOT)
    assert results == []
    assert code == 0


def test_run_suites_explicit_e2e_without_replyer_errors():
    cfg = _cfg(planner=_PLANNER, suite_flag="e2e")
    with pytest.raises(ConfigError, match="e2e requires"):
        run_suites(cfg, root=ROOT)


def test_run_suites_error_exits_1(monkeypatch: pytest.MonkeyPatch):
    _patch_clients_and_suites(
        monkeypatch,
        planner=lambda *a, **k: SuiteResult(
            "planner", "error", {}, None, UsageSplit(), 0.0, 0, error_message="gold core empty"
        ),
    )
    results, code = run_suites(_cfg(planner=_PLANNER), root=ROOT)
    assert code == 1
    assert results[0].status == "error"


def test_run_suites_skip_ok_exit_0(monkeypatch: pytest.MonkeyPatch):
    _patch_clients_and_suites(
        monkeypatch,
        planner=lambda *a, **k: SuiteResult(
            "planner", "skipped", {}, None, UsageSplit(), 0.0, 0, skip_reason="no_planner"
        ),
    )
    results, code = run_suites(_cfg(planner=_PLANNER), root=ROOT)
    assert code == 0
    assert results[0].status == "skipped"


def test_run_suites_probes_planner_for_planner_and_e2e(monkeypatch: pytest.MonkeyPatch):
    _patch_clients_and_suites(
        monkeypatch,
        planner=lambda *a, **k: SuiteResult("planner", "ok", {}, 1.0, UsageSplit(), 0.0, 1),
        e2e=lambda *a, **k: SuiteResult("e2e", "ok", {}, 1.0, UsageSplit(), 0.0, 1),
        replyer=lambda *a, **k: SuiteResult("replyer", "ok", {}, 1.0, UsageSplit(), 0.0, 1),
    )
    run_suites(_cfg(planner=_PLANNER), root=ROOT)
    assert any(role == "planner" for role, *_ in _FakeClient.probed)
    _FakeClient.probed = []
    run_suites(
        _cfg(planner=_PLANNER, replyer=_REPLYER, judge=_JUDGE, suite_flag="e2e"),
        root=ROOT,
    )
    assert any(role == "planner" for role, *_ in _FakeClient.probed)
    _FakeClient.probed = []
    run_suites(
        _cfg(replyer=_REPLYER, judge=_JUDGE, suite_flag="replyer"),
        root=ROOT,
    )
    assert _FakeClient.probed == []


def test_console_run_writes_redacted_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    cfg_path = tmp_path / "config.toml"
    out_dir = tmp_path / "results"
    cfg_path.write_text(
        "\n".join(
            [
                "[planner]",
                'base_url = "http://p/v1"',
                'api_key = "SECRET_KEY"',
                'model = "m"',
                "[run]",
                f'output_dir = "{out_dir}"',
                f'cache_dir = "{tmp_path / "cache"}"',
            ]
        ),
        encoding="utf-8",
    )
    _patch_clients_and_suites(
        monkeypatch,
        planner=lambda *a, **k: SuiteResult(
            "planner", "ok", {"action": 1.0}, 50.0, UsageSplit(), 1.0, 3
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["mai-bench-2", "run", "--config", str(cfg_path)],
    )
    with pytest.raises(SystemExit) as exited:
        console()
    assert exited.value.code == 0
    captured = capsys.readouterr()
    assert "SECRET_KEY" not in captured.out
    assert "SECRET_KEY" not in captured.err
    assert "1a46dd3e9eb3" in captured.out
    assert "official" in captured.out
    assert "WARNING: this was a smoke run. These numbers are not publishable." in captured.out
    runs = [path for path in out_dir.iterdir() if path.is_dir()]
    assert len(runs) == 1
    dumped = (runs[0] / "config.toml").read_text(encoding="utf-8")
    assert "SECRET_KEY" not in dumped
    assert "***" in dumped
    summary = json.loads((runs[0] / "summary.json").read_text(encoding="utf-8"))
    assert summary["persona_id"] == "official"
    assert summary["persona_hex"] == "1a46dd3e9eb3"
    assert "SECRET_KEY" not in json.dumps(summary)
