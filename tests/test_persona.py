from mai_bench2.persona import load_persona

from conftest import ROOT


def test_official_persona_hex():
    persona = load_persona("official", root=ROOT)
    assert persona.id == "official"
    assert persona.nickname == "麦麦"
    assert persona.personality == (
        "是一个大二女大学生，现在正在上网和群友聊天。善于用人类的角度思考问题，聊天偏日常。"
    )
    assert persona.behavior_style == (
        "是大二女大学生，现在正在上网和群友聊天。善于用人类的角度思考问题，聊天偏日常。不会没话题硬找话题，"
    )
    assert persona.reply_style == (
        "你的风格平淡简短，可以参考贴吧的回复风格。不滥用比喻或者生硬句子。视情况省略主语或者进行倒装，风格较为随意。"
    )
    assert persona.hex == "77be5c59f150"


def test_missing_persona_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        load_persona("does-not-exist", root=ROOT)
