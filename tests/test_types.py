from mai_bench2.types import SuiteResult, UsageSplit

def test_suite_result_defaults():
    result = SuiteResult(
        name="planner",
        status="ok",
        native={},
        subscore=None,
        usage=UsageSplit(),
        wall_s=0.0,
        n_items=0,
    )
    assert result.predictions == []
    assert result.usage.planner.requests == 0
