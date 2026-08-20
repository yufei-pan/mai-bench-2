import pytest

from conftest import ROOT
from mai_bench2.prompts import Prompts, default_prompts, fill, load_prompts, prompts_hex


def test_official_prompts_are_maibot_zh_cn():
    prompts = load_prompts("official", root=ROOT)
    assert prompts.id == "official"
    assert "你不是 {bot_name} 本人" in prompts.planner_system
    assert "MaiBot 形态的规划席" not in prompts.planner_system
    assert "{identity}" in prompts.replyer_system
    assert "{replyer_output_instruction}" in prompts.replyer_system
    assert prompts.hex == prompts_hex(prompts)
    assert load_prompts("official", root=ROOT).hex == prompts.hex


def test_custom_templates_change_the_hash_and_the_rubric():
    from mai_bench2.metrics import rubric_hash
    official = load_prompts("official", root=ROOT)
    custom = Prompts(
        id="mine", path="x",
        planner_system="自定义抬头 {bot_name}",
        planner_final_assistant_reminder="x",
        query_memory_rule_group="g",
        query_memory_rule_private="p",
        replyer_system="{nickname}",
        replyer_output_instruction="o",
        replyer_final_instruction="f",
        reply_style_short="s",
        reply_style_long="l",
    )
    custom = Prompts(**{**custom.__dict__, "hex": prompts_hex(custom)})
    assert custom.hex != official.hex
    assert rubric_hash(custom) != rubric_hash(official)


def test_fill_is_literal_and_leaves_braces_alone():
    assert fill("{a}-{b}", {"a": "1"}) == "1-{b}"
    assert fill("用 {} 包起来 {a}", {"a": "x"}) == "用 {} 包起来 x"


def test_load_prompts_rejects_incomplete_templates(tmp_path):
    directory = tmp_path / "prompts"
    directory.mkdir()
    (directory / "broken.toml").write_text('id="broken"\n[planner]\nsystem="x"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"missing"):
        load_prompts("broken", root=tmp_path)


def test_default_prompts_is_official():
    assert default_prompts().id == "official"


def test_planner_prompt_uses_the_template_not_a_hardcoded_string():
    from mai_bench2.persona import load_persona
    from mai_bench2.planner_loop import _planner_messages
    from mai_bench2.tools import tool_specs_for_item

    persona = load_persona("official", root=ROOT)
    custom = Prompts(
        id="mine", path="x",
        planner_system="自定义抬头\n{bot_name}\n{behavior_style}\n{group_chat_attention_block}\n{query_memory_rule}",
        planner_final_assistant_reminder="提醒 {bot_name}",
        query_memory_rule_group="- mem group",
        query_memory_rule_private="- mem private",
        replyer_system="{identity}",
        replyer_output_instruction="o",
        replyer_final_instruction="f",
        reply_style_short="s",
        reply_style_long="l",
    )
    messages = _planner_messages(persona, custom, {"channel": "group"}, [], tool_specs_for_item({}), [])
    system = messages[0]["content"]
    assert system.startswith("自定义抬头")
    assert persona.nickname in system
    assert "- mem group" in system
    assert "MaiBot 形态的规划席" not in system
