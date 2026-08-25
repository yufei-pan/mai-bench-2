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

from conftest import ROOT
from mai_bench2.cli import _gold_ids_for_run, console, find_config, run_suites
from mai_bench2.types import SuiteResult, UsageSplit
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
    args = parse_args(["replyer", "--full", "--persona", "official", "--no-cache"])
    cfg = apply_overrides(_cfg(planner=_PLANNER), args)
    assert cfg.suite_flag == "replyer"
    assert cfg.run.smoke is False
    assert cfg.run.persona == "official"
    assert cfg.run.no_cache is True


def test_apply_overrides_all_clears_suite_flag():
    cfg = apply_overrides(_cfg(planner=_PLANNER), parse_args(["all"]))
    assert cfg.suite_flag is None


def test_apply_overrides_default_suite_is_all():
    cfg = apply_overrides(_cfg(planner=_PLANNER), parse_args([]))
    assert cfg.suite_flag is None
    assert cfg.run.smoke is True


def test_apply_overrides_concurrency_from_flag():
    args = parse_args(["--concurrency", "8"])
    cfg = apply_overrides(_cfg(planner=_PLANNER), args)
    assert cfg.run.concurrency == 8


def test_apply_overrides_concurrency_zero_becomes_one():
    args = parse_args(["--concurrency", "0"])
    cfg = apply_overrides(_cfg(planner=_PLANNER), args)
    assert cfg.run.concurrency == 1


def test_apply_overrides_concurrency_negative_becomes_one():
    args = parse_args(["--concurrency", "-3"])
    cfg = apply_overrides(_cfg(planner=_PLANNER), args)
    assert cfg.run.concurrency == 1


def test_apply_overrides_smoke_and_full_flags():
    smoke = apply_overrides(_cfg(planner=_PLANNER, run=RunConfig(smoke=False)), parse_args(["--smoke"]))
    assert smoke.run.smoke is True
    full = apply_overrides(_cfg(planner=_PLANNER), parse_args(["--full"]))
    assert full.run.smoke is False


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
    narrative_text = "含义\n- 规划器没有原生 tool_calls。\n\n最差样本：没有需要点名的失败项。\n"
    chat_error = None

    def __init__(self, endpoint, role, cache_dir, no_cache, create_fn=None, sleep_fn=None):
        self.endpoint = endpoint
        self.role = role
        self.cache_dir = cache_dir
        self.no_cache = no_cache

    def probe(self, messages, *, max_tokens=1):
        _FakeClient.probed.append((self.role, messages, max_tokens))

    def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
        from mai_bench2.types import ChatResult, TokenCounts

        if _FakeClient.chat_error:
            raise RuntimeError(_FakeClient.chat_error)
        return ChatResult(_FakeClient.narrative_text, TokenCounts(), False, True, [])


def _planner_run_toml(tmp_path: Path) -> tuple[Path, Path]:
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
    return cfg_path, out_dir


def _complete_ok_rows(name: str, result, kwargs: dict) -> None:
    ckpt = kwargs.get("checkpoint")
    if ckpt is None or getattr(result, "status", None) != "ok":
        return
    for row in ckpt.items:
        if row.suite == name and row.status == "pending":
            row.status = "ok"
            row.payload = {"action": "none"}


def _wrap_suite_fake(name: str, fn):
    def wrapped(*args, **kwargs):
        result = fn(*args, **kwargs)
        _complete_ok_rows(name, result, kwargs)
        return result

    return wrapped


def _patch_clients_and_suites(monkeypatch, *, planner=None, replyer=None, e2e=None):
    _FakeClient.probed = []
    _FakeClient.chat_error = None
    monkeypatch.setattr("mai_bench2.cli.ChatClient", _FakeClient)
    if planner is not None:
        monkeypatch.setattr("mai_bench2.cli.run_planner_suite", _wrap_suite_fake("planner", planner))
    if replyer is not None:
        monkeypatch.setattr("mai_bench2.cli.run_replyer_suite", _wrap_suite_fake("replyer", replyer))
    if e2e is not None:
        monkeypatch.setattr("mai_bench2.cli.run_e2e_suite", _wrap_suite_fake("e2e", e2e))


def test_run_suites_no_seats_exit_0():
    results, code = run_suites(_cfg(), root=ROOT)
    by_name = {result.name: result for result in results}
    assert code == 0
    assert by_name["planner"].status == "skipped"
    assert by_name["planner"].skip_reason == "no_planner"
    assert by_name["replyer"].status == "skipped"
    assert by_name["replyer"].skip_reason == "no_replyer"
    assert by_name["e2e"].status == "skipped"
    assert by_name["e2e"].skip_reason == "no_planner"


def test_default_run_skips_enabled_suites_missing_seats(monkeypatch: pytest.MonkeyPatch):
    _patch_clients_and_suites(
        monkeypatch,
        planner=lambda *a, **k: SuiteResult("planner", "ok", {}, 1.0, UsageSplit(), 0.0, 1),
    )
    results, code = run_suites(_cfg(planner=_PLANNER), root=ROOT)
    by_name = {result.name: result for result in results}
    assert code == 0
    assert by_name["planner"].status == "ok"
    assert by_name["replyer"].status == "skipped"
    assert by_name["replyer"].skip_reason == "no_replyer"
    assert by_name["e2e"].status == "skipped"
    assert by_name["e2e"].skip_reason == "no_replyer"


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


def test_run_suites_probes_every_seat_a_requested_suite_needs(monkeypatch: pytest.MonkeyPatch):
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
    # the replyer suite needs a judge, so the judge is probed too: a dead judge
    # used to be discovered only after a full replyer pass had been paid for
    probed = {role for role, *_ in _FakeClient.probed}
    assert probed == {"replyer", "judge"}


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
        ["mai-bench-2", "--config", str(cfg_path)],
    )
    with pytest.raises(SystemExit) as exited:
        console()
    assert exited.value.code == 0
    captured = capsys.readouterr()
    assert "SECRET_KEY" not in captured.out
    assert "SECRET_KEY" not in captured.err
    assert "77be5c59f150" in captured.out
    assert "official" in captured.out
    assert "WARNING: this was a smoke run. These numbers are not publishable." in captured.out
    runs = [path for path in out_dir.iterdir() if path.is_dir()]
    assert len(runs) == 1
    dumped = (runs[0] / "config.toml").read_text(encoding="utf-8")
    assert "SECRET_KEY" not in dumped
    assert "***" in dumped
    summary = json.loads((runs[0] / "summary.json").read_text(encoding="utf-8"))
    assert summary["persona_id"] == "official"
    assert summary["persona_hex"] == "77be5c59f150"
    assert "SECRET_KEY" not in json.dumps(summary)
    assert "含义" in captured.out
    assert (runs[0] / "digest.json").is_file()
    assert (runs[0] / "narrative.md").is_file()
    assert "含义" in (runs[0] / "narrative.md").read_text(encoding="utf-8")


def test_console_smoke_command_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
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
        ["mai-bench-2", "--smoke", "--config", str(cfg_path)],
    )
    with pytest.raises(SystemExit) as exited:
        console()
    assert exited.value.code == 0
    captured = capsys.readouterr()
    assert "WARNING: this was a smoke run. These numbers are not publishable." in captured.out
    runs = [path for path in out_dir.iterdir() if path.is_dir()]
    assert len(runs) == 1


def test_console_prints_narrative_when_judge_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    cfg_path = tmp_path / "config.toml"
    out_dir = tmp_path / "results"
    cfg_path.write_text(
        "\n".join(
            [
                "[planner]",
                'base_url = "http://p/v1"',
                'api_key = "SECRET_KEY"',
                'model = "m"',
                "[judge]",
                'base_url = "http://j/v1"',
                'api_key = "JUDGE_SECRET"',
                'model = "judge-m"',
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
    # a terminal report, not a markdown document — `##` is rejected and retried
    _FakeClient.narrative_text = "含义\n- 规划器没有原生 tool_calls。\n\n最差样本：无。"
    monkeypatch.setattr(
        sys,
        "argv",
        ["mai-bench-2", "--config", str(cfg_path)],
    )
    with pytest.raises(SystemExit) as exited:
        console()
    assert exited.value.code == 0
    captured = capsys.readouterr()
    assert "WARNING: this was a smoke run. These numbers are not publishable." in captured.out
    assert "规划器没有原生 tool_calls" in captured.out
    assert "JUDGE_SECRET" not in captured.out
    runs = [path for path in out_dir.iterdir() if path.is_dir()]
    assert len(runs) == 1
    narrative = (runs[0] / "narrative.md").read_text(encoding="utf-8")
    assert "规划器没有原生 tool_calls" in narrative
    assert "JUDGE_SECRET" not in narrative


def test_console_narrative_failure_keeps_exit_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    cfg_path = tmp_path / "config.toml"
    out_dir = tmp_path / "results"
    cfg_path.write_text(
        "\n".join(
            [
                "[planner]",
                'base_url = "http://p/v1"',
                'api_key = "SECRET_KEY"',
                'model = "m"',
                "[judge]",
                'base_url = "http://j/v1"',
                'api_key = "JUDGE_SECRET"',
                'model = "judge-m"',
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
    _FakeClient.chat_error = "network down"
    monkeypatch.setattr(
        sys,
        "argv",
        ["mai-bench-2", "--config", str(cfg_path)],
    )
    with pytest.raises(SystemExit) as exited:
        console()
    assert exited.value.code == 0
    captured = capsys.readouterr()
    assert "narrative skipped:" in captured.out
    assert "network down" in captured.out
    runs = [path for path in out_dir.iterdir() if path.is_dir()]
    assert "含义" in captured.out
    assert (runs[0] / "narrative.md").is_file()
    assert "含义" in (runs[0] / "narrative.md").read_text(encoding="utf-8")


def test_suite_usage_is_a_delta_not_a_running_total(monkeypatch: pytest.MonkeyPatch):
    """A shared client's cumulative snapshot made the e2e row re-report the planner
    and replyer suites' tokens."""
    from mai_bench2 import cli
    from mai_bench2.types import SuiteResult, TokenCounts, UsageSplit

    class Counter:
        def __init__(self):
            self.total = 0

        def spend(self, n):
            self.total += n

        def probe(self, messages, *, max_tokens=1):
            return None

        def usage_snapshot(self):
            return TokenCounts(total_tokens=self.total, requests=1)

    planner = Counter()
    clients = {"planner": planner}

    def fake_planner(cfg, client, persona, *, root, **kwargs):
        client.spend(100)
        return SuiteResult(
            "planner", "ok", {}, 1.0, UsageSplit(planner=client.usage_snapshot()), 0.0, 1
        )

    def fake_e2e(cfg, planner_client, replyer_client, judge_client, persona, *, root, **kwargs):
        planner_client.spend(30)
        return SuiteResult(
            "e2e", "ok", {}, 1.0, UsageSplit(planner=planner_client.usage_snapshot()), 0.0, 1
        )

    monkeypatch.setattr(cli, "run_planner_suite", fake_planner)
    monkeypatch.setattr(cli, "run_e2e_suite", fake_e2e)
    monkeypatch.setattr(cli, "_build_clients", lambda cfg: clients)

    cfg = _cfg(planner=_PLANNER)
    cfg.replyer_suite.enabled = False
    results, _ = run_suites(cfg, root=ROOT, clients=clients)

    by_name = {result.name: result for result in results}
    assert by_name["planner"].usage.planner.total_tokens == 100
    assert by_name["e2e"].usage.planner.total_tokens == 30  # not 130
    # a seat the suite never touched reports zero, never a negative delta
    assert by_name["planner"].usage.replyer.total_tokens == 0
    assert by_name["e2e"].usage.judge.total_tokens == 0


def test_repeats_average_and_report_stderr(monkeypatch: pytest.MonkeyPatch):
    """One sample at temperature 0 says nothing about spread."""
    from mai_bench2 import cli
    from mai_bench2.types import SuiteResult, UsageSplit

    scores = iter([60.0, 70.0, 80.0])

    def fake_planner(cfg, client, persona, *, root, **kwargs):
        return SuiteResult("planner", "ok", {}, next(scores), UsageSplit(), 0.0, 3)

    monkeypatch.setattr(cli, "run_planner_suite", fake_planner)
    monkeypatch.setattr(cli, "_build_clients", lambda cfg: {})

    cfg = _cfg(planner=_PLANNER)
    cfg.run.repeats = 3
    cfg.replyer_suite.enabled = False
    cfg.e2e_suite.enabled = False
    results, _ = run_suites(cfg, root=ROOT, clients={})

    result = results[0]
    assert result.subscore_samples == [60.0, 70.0, 80.0]
    assert result.subscore == 70.0
    assert abs(result.subscore_stderr - (10.0 / 3**0.5)) < 1e-9
    assert result.repeats == 3


def test_repeat_index_changes_the_cache_key():
    from mai_bench2.client import ChatClient
    from mai_bench2.config import EndpointConfig

    client = ChatClient(EndpointConfig("http://x/v1", "k", "m"), "planner", Path("/tmp/x"), False)
    messages = [{"role": "user", "content": "hi"}]
    first = client._cache_path(messages, max_tokens=8, temperature=0.0, tools=None)
    client.set_sample(1)
    second = client._cache_path(messages, max_tokens=8, temperature=0.0, tools=None)
    assert first != second


def test_dead_judge_fails_the_replyer_suite_before_any_model_call(monkeypatch: pytest.MonkeyPatch):
    """A 502 judge used to be discovered only after the replyer had written every
    reply and each judge call had burned its whole retry budget."""
    ran = []

    class JudgeDown(_FakeClient):
        def probe(self, messages, *, max_tokens=16):
            _FakeClient.probed.append((self.role, messages, max_tokens))
            if self.role == "judge":
                raise RuntimeError(
                    "Error code: 502 - all providers, keys, and models were exhausted"
                )

    _patch_clients_and_suites(
        monkeypatch,
        replyer=lambda *a, **k: ran.append("replyer") or SuiteResult(
            "replyer", "ok", {}, 1.0, UsageSplit(), 0.0, 3
        ),
    )
    monkeypatch.setattr("mai_bench2.cli.ChatClient", JudgeDown)

    results, code = run_suites(
        _cfg(replyer=_REPLYER, judge=_JUDGE, suite_flag="replyer"), root=ROOT
    )
    assert ran == []  # the replyer model was never asked to write anything
    assert code == 1
    assert results[0].status == "error"
    assert results[0].error_message == "judge endpoint unreachable"
    assert "502" in results[0].error_detail


def test_console_writes_checkpoint_before_suites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    cfg_path, out_dir = _planner_run_toml(tmp_path)
    seen = {}

    def fake_planner(cfg, client, persona, **kwargs):
        stamps = [path for path in Path(cfg.run.output_dir).expanduser().iterdir() if path.is_dir()]
        assert stamps, "stamp directory must exist before run_planner_suite"
        ckpt_path = stamps[0] / "checkpoint.json"
        assert ckpt_path.is_file(), "checkpoint.json must exist before run_planner_suite"
        seen["data"] = json.loads(ckpt_path.read_text(encoding="utf-8"))
        assert seen["data"]["state"] == "running"
        assert (stamps[0] / "config.toml").is_file()
        return SuiteResult("planner", "ok", {"action": 1.0}, 50.0, UsageSplit(), 1.0, 3)

    _patch_clients_and_suites(monkeypatch, planner=fake_planner)
    with pytest.raises(SystemExit) as exited:
        console(["--config", str(cfg_path), "planner", "--smoke"])
    assert exited.value.code == 0
    runs = [path for path in out_dir.iterdir() if path.is_dir()]
    assert len(runs) == 1
    assert (runs[0] / "checkpoint.json").is_file()
    data = json.loads((runs[0] / "checkpoint.json").read_text(encoding="utf-8"))
    assert data["state"] == "complete"
    assert data["items"]
    assert seen["data"]["state"] == "running"


def test_console_transport_fail_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    cfg_path, out_dir = _planner_run_toml(tmp_path)

    def fake_planner(cfg, client, persona, **kwargs):
        ckpt = kwargs.get("checkpoint")
        if ckpt is not None:
            for row in ckpt.items:
                if row.suite == "planner":
                    row.status = "transport_fail"
                    row.error = "RuntimeError: boom"
        return SuiteResult("planner", "ok", {"action": 1.0}, 50.0, UsageSplit(), 1.0, 3)

    _patch_clients_and_suites(monkeypatch, planner=fake_planner)
    with pytest.raises(SystemExit) as exited:
        console(["--config", str(cfg_path), "planner", "--smoke"])
    assert exited.value.code == 1
    runs = [path for path in out_dir.iterdir() if path.is_dir()]
    assert len(runs) == 1
    data = json.loads((runs[0] / "checkpoint.json").read_text(encoding="utf-8"))
    assert data["state"] == "incomplete"
    assert any(item["status"] == "transport_fail" for item in data["items"])
    captured = capsys.readouterr()
    assert "planner" in captured.out


def test_install_run_signals_sigint_then_sigterm():
    import signal as signal_mod

    from mai_bench2.cli import install_run_signals
    from mai_bench2.parallel import RunControl

    previous_int = signal_mod.getsignal(signal_mod.SIGINT)
    previous_term = signal_mod.getsignal(signal_mod.SIGTERM)
    try:
        control = RunControl()
        caught = {"n": 0}
        install_run_signals(control, caught)
        sigint = signal_mod.getsignal(signal_mod.SIGINT)
        sigint(signal_mod.SIGINT, None)
        assert control.drain.is_set()
        assert not control.abandon.is_set()
        assert caught["n"] == 1
        sigint(signal_mod.SIGINT, None)
        assert control.abandon.is_set()
        assert caught["n"] == 2
        sigint(signal_mod.SIGINT, None)
        assert caught["n"] == 2

        term = RunControl()
        term_caught = {"n": 0}
        install_run_signals(term, term_caught)
        signal_mod.getsignal(signal_mod.SIGTERM)(signal_mod.SIGTERM, None)
        assert term.drain.is_set()
        assert not term.abandon.is_set()
        assert term_caught["n"] == 1
        signal_mod.getsignal(signal_mod.SIGINT)(signal_mod.SIGINT, None)
        assert term.abandon.is_set()
        assert term_caught["n"] == 2
    finally:
        signal_mod.signal(signal_mod.SIGINT, previous_int)
        signal_mod.signal(signal_mod.SIGTERM, previous_term)


def test_gold_ids_for_run_e2e_smoke_matches_hydrated_select():
    from mai_bench2.gold import load_gold, select_items
    from mai_bench2.suites.e2e import _hydrate

    cfg = _cfg(
        planner=_PLANNER,
        replyer=_REPLYER,
        judge=_JUDGE,
        suite_flag="e2e",
        run=RunConfig(smoke=True),
    )
    planned = _gold_ids_for_run(cfg, ROOT)["e2e"]
    items = _hydrate(load_gold(ROOT, "e2e"), ROOT)
    expected = [
        str(item.get("id") or "")
        for item in select_items(
            items,
            smoke=True,
            smoke_n=min(cfg.e2e_suite.smoke_n, len(items)),
        )
    ]
    assert planned == expected
    raw = [
        str(item.get("id") or "")
        for item in select_items(
            load_gold(ROOT, "e2e"),
            smoke=True,
            smoke_n=min(cfg.e2e_suite.smoke_n, len(load_gold(ROOT, "e2e"))),
        )
    ]
    assert planned != raw


def test_console_saves_terminal_checkpoint_before_dropping_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import signal as signal_mod

    from mai_bench2 import cli as cli_mod
    from mai_bench2.checkpoint import save_checkpoint as real_save

    cfg_path = tmp_path / "config.toml"
    out_dir = tmp_path / "results"
    cfg_path.write_text(
        "\n".join(
            [
                "[planner]",
                'base_url = "http://p/v1"',
                'api_key = "SECRET_KEY"',
                'model = "m"',
                "[judge]",
                'base_url = "http://j/v1"',
                'api_key = "JUDGE_SECRET"',
                'model = "judge-m"',
                "[run]",
                f'output_dir = "{out_dir}"',
                f'cache_dir = "{tmp_path / "cache"}"',
            ]
        ),
        encoding="utf-8",
    )
    previous_int = signal_mod.getsignal(signal_mod.SIGINT)
    seen = {}

    def tracking_save(directory, ckpt):
        real_save(directory, ckpt)
        if ckpt.state in {"complete", "incomplete"}:
            seen["state"] = ckpt.state
            seen["handler"] = signal_mod.getsignal(signal_mod.SIGINT)
            data = json.loads((directory / "checkpoint.json").read_text(encoding="utf-8"))
            seen["disk_state"] = data["state"]

    def boom_narrative(*args, **kwargs):
        seen["narrative_saw_save"] = "state" in seen
        raise RuntimeError("narrative boom")

    monkeypatch.setattr(cli_mod, "save_checkpoint", tracking_save)
    monkeypatch.setattr(cli_mod, "generate_narrative", boom_narrative)
    _patch_clients_and_suites(
        monkeypatch,
        planner=lambda *a, **k: SuiteResult(
            "planner", "ok", {"action": 1.0}, 50.0, UsageSplit(), 1.0, 3
        ),
    )
    with pytest.raises(RuntimeError, match="narrative boom"):
        console(["--config", str(cfg_path), "planner", "--smoke"])
    assert seen["narrative_saw_save"] is True
    assert seen["state"] == "complete"
    assert seen["disk_state"] == "complete"
    assert seen["handler"] is not previous_int
    assert seen["handler"] != signal_mod.SIG_DFL
