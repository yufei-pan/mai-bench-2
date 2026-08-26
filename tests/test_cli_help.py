from mai_bench2.cli import parse_args
import pytest


def test_parse_defaults():
    ns = parse_args([])
    assert ns.suite == "all"
    assert ns.full is False
    assert ns.smoke_flag is False
    assert ns.persona is None
    assert ns.no_cache is False
    assert ns.concurrency is None
    assert ns.command == "run"


def test_parse_compare_subcommand():
    ns = parse_args(["compare"])
    assert ns.command == "compare"
    assert ns.group is None
    assert ns.full is False
    assert ns.smoke_flag is False
    assert ns.config is None


def test_parse_compare_full_and_group():
    ns = parse_args(["compare", "--full", "--group", "abc123"])
    assert ns.command == "compare"
    assert ns.full is True
    assert ns.smoke_flag is False
    assert ns.group == "abc123"


def test_parse_compare_smoke_and_full_are_mutually_exclusive():
    with pytest.raises(SystemExit) as exited:
        parse_args(["compare", "--smoke", "--full"])
    assert exited.value.code == 2


def test_compare_help(capsys):
    with pytest.raises(SystemExit) as exited:
        parse_args(["compare", "-h"])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert "--group" in out
    assert "--smoke" in out
    assert "--full" in out


def test_parse_suite_and_full():
    ns = parse_args(["e2e", "--full", "--persona", "official", "--no-cache"])
    assert ns.suite == "e2e"
    assert ns.full is True
    assert ns.smoke_flag is False
    assert ns.persona == "official"
    assert ns.no_cache is True


def test_parse_planner_smoke():
    ns = parse_args(["planner", "--smoke", "--persona", "official"])
    assert ns.suite == "planner"
    assert ns.full is False
    assert ns.smoke_flag is True
    assert ns.persona == "official"


def test_parse_full_flag_without_suite():
    ns = parse_args(["--full", "--no-cache"])
    assert ns.suite == "all"
    assert ns.full is True
    assert ns.no_cache is True


def test_parse_all_explicit():
    ns = parse_args(["all"])
    assert ns.suite == "all"
    assert ns.full is False


def test_parse_smoke_and_full_are_mutually_exclusive():
    with pytest.raises(SystemExit) as exited:
        parse_args(["--smoke", "--full"])
    assert exited.value.code == 2


def test_parse_invalid_suite_exits():
    with pytest.raises(SystemExit) as exited:
        parse_args(["run"])
    assert exited.value.code == 2


def _assert_argparse_help(out: str) -> None:
    assert "usage:" in out
    assert "--smoke" in out
    assert "--full" in out
    assert "planner" in out
    assert "replyer" in out
    assert "e2e" in out
    assert "resume" in out
    assert "{run,smoke,full}" not in out
    assert "compare" in out


def test_help_short_flag(capsys):
    with pytest.raises(SystemExit) as exited:
        parse_args(["-h"])
    assert exited.value.code == 0
    _assert_argparse_help(capsys.readouterr().out)


def test_help_long_flag(capsys):
    with pytest.raises(SystemExit) as exited:
        parse_args(["--help"])
    assert exited.value.code == 0
    _assert_argparse_help(capsys.readouterr().out)


def test_smoke_console_prepends_smoke(monkeypatch):
    import sys

    from mai_bench2 import cli as cli_mod

    captured = {}

    def fake_parse(argv=None):
        captured["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(cli_mod, "parse_args", fake_parse)
    monkeypatch.setattr(sys, "argv", ["mai-bench-2-smoke", "planner"])
    with pytest.raises(SystemExit):
        cli_mod.smoke_console()
    assert captured["argv"] == ["--smoke", "planner"]


def test_parse_concurrency_flag():
    ns = parse_args(["--concurrency", "8"])
    assert ns.concurrency == 8


def test_parse_concurrency_with_full():
    ns = parse_args(["--full", "--concurrency", "4"])
    assert ns.concurrency == 4
    assert ns.full is True


def test_full_console_prepends_full(monkeypatch):
    import sys

    from mai_bench2 import cli as cli_mod

    captured = {}

    def fake_parse(argv=None):
        captured["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(cli_mod, "parse_args", fake_parse)
    monkeypatch.setattr(sys, "argv", ["mai-bench-2-full", "--no-cache"])
    with pytest.raises(SystemExit):
        cli_mod.full_console()
    assert captured["argv"] == ["--full", "--no-cache"]


def test_parse_resume_subcommand():
    ns = parse_args(["resume"])
    assert ns.command == "resume"
    assert ns.stamp is None
    assert ns.config is None


def test_parse_resume_stamp():
    ns = parse_args(["resume", "--stamp", "2026-08-25T000000Z", "--config", "c.toml"])
    assert ns.stamp == "2026-08-25T000000Z"
    assert ns.config == "c.toml"


def test_parse_resume_rejects_full():
    with pytest.raises(SystemExit) as exited:
        parse_args(["resume", "--full"])
    assert exited.value.code == 2


def test_resume_help(capsys):
    with pytest.raises(SystemExit) as exited:
        parse_args(["resume", "-h"])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert "--stamp" in out
    assert "--repeats" not in out
