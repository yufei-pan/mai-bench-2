import pytest

from conftest import ROOT
from mai_bench2.prompts import Prompts, default_prompts, fill, load_prompts, prompts_hex


def test_official_prompts_load_and_hash_stably():
    prompts = load_prompts("official", root=ROOT)
    assert prompts.id == "official"
    assert prompts.hex == prompts_hex(prompts)
    assert len(prompts.hex) == 12 and prompts.hex == prompts.hex.lower()
    assert load_prompts("official", root=ROOT).hex == prompts.hex


def test_custom_templates_change_the_hash_and_the_rubric():
    from mai_bench2.metrics import rubric_hash

    official = load_prompts("official", root=ROOT)
    custom = Prompts(
        id="mine", path="x", planner_system="你是规划器模块。\n{tools}",
        replyer_system="{nickname}", replyer_user="{log}",
    )
    custom_hashed = Prompts(**{**custom.__dict__, "hex": prompts_hex(custom)})
    assert custom_hashed.hex != official.hex
    # a custom-prompt run must never be mistaken for an official one
    assert rubric_hash(custom_hashed) != rubric_hash(official)


def test_fill_is_literal_and_leaves_braces_alone():
    assert fill("{a}-{b}", {"a": "1"}) == "1-{b}"
    assert fill("用 {} 包起来 {a}", {"a": "x"}) == "用 {} 包起来 x"


def test_load_prompts_rejects_incomplete_templates(tmp_path):
    directory = tmp_path / "prompts"
    directory.mkdir()
    (directory / "broken.toml").write_text('id="broken"\n[planner]\nsystem="x"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"missing \[replyer\]"):
        load_prompts("broken", root=tmp_path)
    with pytest.raises(FileNotFoundError):
        load_prompts("nope", root=tmp_path)


def test_planner_prompt_uses_the_template_not_a_hardcoded_string():
    from mai_bench2.persona import load_persona
    from mai_bench2.planner_loop import _planner_prompt
    from mai_bench2.tools import tool_specs_for_item

    persona = load_persona("official", root=ROOT)
    custom = Prompts(
        id="mine", path="x",
        planner_system="自定义抬头\n工具：\n{tools}\n昵称 {nickname}",
        replyer_system="{nickname}", replyer_user="{log}",
        tool_lines={"wait": "- 等待", "reply": "- 回复", "no_action": "- 不说话"},
    )
    text = _planner_prompt(persona, custom, [], tool_specs_for_item({}))
    assert text.startswith("自定义抬头")
    assert "- 等待" in text and "- 不说话" in text
    assert persona.nickname in text
    assert "MaiBot 形态的规划席" not in text  # the official wording is gone


def test_default_prompts_is_official():
    assert default_prompts().id == "official"
