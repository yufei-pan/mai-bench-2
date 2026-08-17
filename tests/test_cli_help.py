from mai_bench2.cli import parse_args


def test_parse_run_defaults():
    ns = parse_args(["run"])
    assert ns.command == "run"
    assert ns.full is False
    assert ns.suite is None
    assert ns.persona is None
    assert ns.no_cache is False


def test_parse_run_flags():
    ns = parse_args(["run", "--full", "--suite", "e2e", "--persona", "official", "--no-cache"])
    assert ns.full is True
    assert ns.suite == "e2e"
    assert ns.persona == "official"
    assert ns.no_cache is True
