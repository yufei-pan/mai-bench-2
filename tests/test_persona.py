from pathlib import Path
from mai_bench2.persona import load_persona

ROOT = Path("/mnt/klein/work/mai-bench-2")

def test_official_persona_hex():
    persona = load_persona("official", root=ROOT)
    assert persona.id == "official"
    assert persona.nickname == "麦麦"
    assert persona.hex == "1a46dd3e9eb3"

def test_missing_persona_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        load_persona("does-not-exist", root=ROOT)
