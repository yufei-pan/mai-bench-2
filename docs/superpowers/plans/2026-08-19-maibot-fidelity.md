# MaiBot Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mai-bench-2’s official persona, prompts, planner protocol, tools, and replyer request shape match default MaiBot (Focus off, zh-CN) so a MaiBot-tuned model is scored on the same seat contract.

**Architecture:** Keep the fake-tool logical-clock harness. Replace `official` persona strings with `official_configs.py` Field defaults; copy zh-CN `maisaka_chat.prompt` / `maisaka_replyer.prompt` byte-for-byte; idle is analysis-plus-no-tools; `reply` uses official args; replyer history is role-split. No MaiBot import.

**Tech Stack:** Existing package: Python 3.11+, `tomllib`, pytest, `mai_bench2`. Upstream copy source: `/mnt/klein/work/maibot-plugins/MaiBot`.

**Spec:** `docs/superpowers/specs/2026-08-19-maibot-fidelity-design.md`

## Global Constraints

- Do not import or run MaiBot. Copy strings from the paths named in each task.
- Replace `official` in place. No `classic/` snapshot.
- Official persona hex after the Field-default rewrite is `77be5c59f150`.
- Idle: no `tool_calls` + non-empty assistant text → `none`. Empty text → `contract_fail`.
- Remove `no_action` and `lookup`. Unknown tool names → `contract_fail`.
- `reply` required arg is only `msg_id`. Optional: `set_quote`, `reply_reference`, `reply_style` ∈ {简短表达, 正常回复, 长回复}.
- Focus off: never offer `fetch_history` or `switch_chat`.
- Time origin: `2026-01-01 12:00:00` plus logical `t` seconds, format `%Y-%m-%d %H:%M:%S`.
- Bump `RUBRIC_VERSION` to `3` when briefing/idle contract code changes (Task 6).
- Run only the tests named in the task until that task’s end; full `pytest` in Task 8.
- Commits: do not run `git config`. Do not `--no-verify`. Do not push.

## File map

| Path | Responsibility |
|---|---|
| `personas/official.toml` | Field-default 人设 |
| `prompts/official.toml` | Byte-for-byte zh-CN templates + hashed Python-built strings |
| `prompts/minimal.toml` | Same keys, terse custom example |
| `src/mai_bench2/prompts.py` | Load / `prompts_hex` / `fill` for the expanded keys |
| `src/mai_bench2/maibot_shape.py` | Identity, 注意事项 wrapper, stamps, deferred reminder, replyer history, target block |
| `src/mai_bench2/tools.py` | Official schemas; stubs; `tool_search` unlock; fixtures |
| `src/mai_bench2/planner_loop.py` | Multi-message planner request; idle protocol |
| `src/mai_bench2/suites/replyer.py` | Role-split replyer messages |
| `src/mai_bench2/suites/e2e.py` | Handoff: `reply_reference` + analysis + tool reference |
| `src/mai_bench2/metrics.py` | Briefing over `reply_reference` + `assistant_text` |
| `src/mai_bench2/gold.py` | New handoff keys; reject old names |
| `src/mai_bench2/judge.py` | Show `reply_reference` / analysis |
| `tools/goldkit.py`, `tools/scenarios_*.py` | Gold authoring |
| `data/gold/*.jsonl` | Regenerated |
| tests / README / `THIRD_PARTY.md` | Pins and wording |

---

### Task 1: Official persona Field defaults

**Files:**
- Modify: `personas/official.toml`
- Modify: `tests/test_persona.py`
- Test: `tests/test_persona.py`

**Interfaces:**
- Consumes: `load_persona(spec: str, *, root: Path) -> Persona` (unchanged)
- Produces: official applied strings as in the spec; `persona.hex == "77be5c59f150"`

- [ ] **Step 1: Write the failing test**

Replace `tests/test_persona.py` with:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_persona.py::test_official_persona_hex -v`

Expected: FAIL on personality / hex (still the old snapshot).

- [ ] **Step 3: Rewrite `personas/official.toml` applied strings**

Keep `id`, `name`, `nickname = "麦麦"`, `unused_reply_styles`, and the existing `group_chat_prompt` / `private_chat_prompt` (already Field-default). Change:

```toml
[planner]
behavior_style = """是大二女大学生，现在正在上网和群友聊天。善于用人类的角度思考问题，聊天偏日常。不会没话题硬找话题，"""

[replyer]
personality = """是一个大二女大学生，现在正在上网和群友聊天。善于用人类的角度思考问题，聊天偏日常。"""
reply_style = """你的风格平淡简短，可以参考贴吧的回复风格。不滥用比喻或者生硬句子。视情况省略主语或者进行倒装，风格较为随意。"""
```

Update the file header comment: snapshot of `official_configs.py` Field defaults, not dashboard/WebUI reference.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_persona.py -v`

Expected: PASS. If hex is wrong, check `strip("\n")` on TOML triple-quoted strings (keep the trailing comma inside `behavior_style`; do not add a trailing newline after the last character of personality/reply_style).

- [ ] **Step 5: Commit**

```bash
git add personas/official.toml tests/test_persona.py
git commit -m "$(cat <<'EOF'
fix: snapshot official 人设 from MaiBot Field defaults

EOF
)"
```

---

### Task 2: Prompt schema, official templates, shared shape helpers

**Files:**
- Create: `src/mai_bench2/maibot_shape.py`
- Create: `tests/test_maibot_shape.py`
- Modify: `src/mai_bench2/prompts.py`
- Modify: `prompts/official.toml`
- Modify: `prompts/minimal.toml`
- Modify: `tests/test_prompts.py`

**Interfaces:**
- Consumes: `fill(template, values)`
- Produces:

```python
@dataclass(frozen=True)
class Prompts:
    id: str
    path: str
    planner_system: str
    planner_final_assistant_reminder: str
    query_memory_rule_group: str
    query_memory_rule_private: str
    replyer_system: str
    replyer_output_instruction: str
    replyer_final_instruction: str
    reply_style_short: str
    reply_style_long: str
    hex: str = ""

def identity(nickname: str, personality: str) -> str: ...
def attention_block(chat_prompt: str) -> str: ...
def stamp(t: int) -> str: ...
def deferred_reminder(locked: list[tuple[str, str]]) -> str: ...
def target_block(messages: list[dict], msg_id: str, nickname: str) -> str: ...
def replyer_history(messages: list[dict]) -> list[dict]: ...
```

`prompts_hex` hashes these keys (sort_keys JSON, first 12 hex): `planner_system`, `planner_final_assistant_reminder`, `query_memory_rule_group`, `query_memory_rule_private`, `replyer_system`, `replyer_output_instruction`, `replyer_final_instruction`, `reply_style_short`, `reply_style_long`. No `tool_lines`. No `replyer_user`.

`load_prompts` requires every key above as a string (nested TOML as in Step 3). Missing → `ValueError`.

- [ ] **Step 1: Write the failing tests**

`tests/test_maibot_shape.py`:

```python
from mai_bench2.maibot_shape import (
    attention_block,
    deferred_reminder,
    identity,
    replyer_history,
    stamp,
    target_block,
)


def test_identity_wraps_nickname():
    text = identity("麦麦", "是一个大二女大学生，现在正在上网和群友聊天。")
    assert text == "你的名字是麦麦。\n是一个大二女大学生，现在正在上网和群友聊天。"


def test_attention_block_wraps_prompt():
    block = attention_block("群里简短。")
    assert block.startswith("在该聊天中的注意事项：\n通用注意事项：\n群里简短。")
    assert attention_block("  ") == ""


def test_stamp_is_deterministic():
    assert stamp(0) == "2026-01-01 12:00:00"
    assert stamp(90) == "2026-01-01 12:01:30"


def test_deferred_reminder_uses_system_reminder():
    text = deferred_reminder([("view_forward_message", "查看转发")])
    assert text.startswith("<system-reminder>")
    assert "view_forward_message: 查看转发" in text
    assert "tool_search" in text
    assert deferred_reminder([]) == ""


def test_replyer_history_splits_roles():
    rows = replyer_history([
        {"t": 0, "msg_id": "m1", "user": "q1", "group_card": "小徐", "text": "hi"},
        {"t": 1, "msg_id": "m2", "user": "麦麦", "text": "嗯", "is_self_message": True},
    ])
    assert rows == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "嗯"},
    ]
    assert "<message" not in rows[0]["content"]


def test_target_block_names_sender_and_id():
    messages = [{"t": 0, "msg_id": "m1", "user": "q1", "group_card": "小徐", "text": "麦麦 在吗"}]
    text = target_block(messages, "m1", "麦麦")
    assert "小徐" in text and "m1" in text and "麦麦 在吗" in text
    self_msg = [{"t": 0, "msg_id": "m9", "user": "麦麦", "text": "我刚说的", "is_self_message": True}]
    self_text = target_block(self_msg, "m9", "麦麦")
    assert "补充" in self_text and "我刚说的" in self_text
    assert target_block(messages, "missing", "麦麦") == ""
```

`tests/test_prompts.py` — replace official/custom tests:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_maibot_shape.py tests/test_prompts.py -v`

Expected: FAIL (`maibot_shape` missing; `load_prompts` missing new keys; `_planner_messages` missing).

- [ ] **Step 3: Implement helpers and prompt loader**

`src/mai_bench2/maibot_shape.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta

_ORIGIN = datetime(2026, 1, 1, 12, 0, 0)


def identity(nickname: str, personality: str) -> str:
    return f"你的名字是{nickname}。\n{personality}".strip()


def attention_block(chat_prompt: str) -> str:
    prompt = (chat_prompt or "").strip()
    if not prompt:
        return ""
    return "在该聊天中的注意事项：\n通用注意事项：\n" + prompt + "\n"


def stamp(t: int) -> str:
    return (_ORIGIN + timedelta(seconds=int(t))).strftime("%Y-%m-%d %H:%M:%S")


def deferred_reminder(locked: list[tuple[str, str]]) -> str:
    if not locked:
        return ""
    lines = [
        "<system-reminder>",
        "以下工具当前未直接暴露给你，但可以通过 tool_search 工具发现并在后续轮次中使用：",
    ]
    for index, (name, description) in enumerate(locked, start=1):
        desc = description.strip()
        lines.append(f"{index}. {name}: {desc}" if desc else f"{index}. {name}")
    lines.extend([
        "",
        "如需其中某个工具，请先调用 tool_search。tool_search 只负责发现工具，不直接执行。",
        "</system-reminder>",
    ])
    return "\n".join(lines)


def replyer_history(messages: list[dict]) -> list[dict]:
    rows = []
    for message in messages:
        if message.get("kind") and message.get("kind") != "message":
            continue
        role = "assistant" if message.get("is_self_message") else "user"
        rows.append({"role": role, "content": str(message.get("text") or "")})
    return rows


def target_block(messages: list[dict], msg_id: str, nickname: str) -> str:
    target_id = (msg_id or "").strip()
    if not target_id:
        return ""
    row = next((m for m in messages if str(m.get("msg_id") or "") == target_id), None)
    if row is None:
        return ""
    text = str(row.get("text") or "").strip() or "[无可见文本内容]"
    if row.get("is_self_message"):
        return "\n".join([
            f"你想要补充说明你自己（{nickname}） 发送的 msg_id为 {target_id} 的消息，"
            "你可以在这条目标消息的基础上补充发言，不要把你自己的发言当成别人的发言。",
            f"- 你之前的发言内容：{text}",
        ])
    sender = str(row.get("group_card") or row.get("user") or "")
    lines = [
        f"你想要回复的消息是 {sender} 发送的 msg_id为 {target_id} 的消息，你这次要回复的就是这条目标消息，不要把其他历史消息当成当前回复对象。",
    ]
    if row.get("quote"):
        lines.append(f"- quote={row['quote']}")
    lines.append(f"- 发言内容：{text}")
    return "\n".join(lines)
```

Rewrite `prompts.py` `Prompts` / `_HEX_KEYS` / `load_prompts` to the interface above. Nested TOML:

```toml
id = "official"

[planner]
system = """...byte copy of /mnt/klein/work/maibot-plugins/MaiBot/prompts/zh-CN/maisaka_chat.prompt..."""
final_assistant_reminder = "我需要输出对{bot_name}发言的分析，视情况输出文本内容的分析，思考是否进行工具调用"

[planner.query_memory_rule]
group = "- query_memory()：只有回复明显依赖群内过去对话、共同经历、公开约定、任务进展或近期线索时使用；不要为了寒暄、即时情绪回应、轻松接话、只看最近消息就能回答的内容而检索。不要把私聊或个人隐私记忆带到群聊里。"
private = "- query_memory()：当对方提到“之前”“上次”“最近”“还记得吗”“我喜欢”“我说过”等信号，或回复依赖长期偏好、先前承诺、共同经历、人物长期信息时，可以更积极检索。"

[replyer]
system = """...byte copy of /mnt/klein/work/maibot-plugins/MaiBot/prompts/zh-CN/maisaka_replyer.prompt..."""
output_instruction = "请注意不要输出多余内容(包括不必要的前后缀，冒号，括号，表情包，@等 )，只输出发言内容就好。"
final_instruction = "请自然地回复。不要输出多余说明、括号、@ 或额外标记，只输出实际要发言的内容。"

[replyer.reply_style_message]
"简短表达" = "请简短的回复，允许句子残缺，奇怪表达，倒装，省略，符合口语习惯，符合省力随意回复习惯"
"长回复" = "可以针对问题做出较为详细的评论和说明"
```

Load with `styles.get("简短表达")` and `styles.get("长回复")`.

Copy the two `.prompt` files **verbatim** into the `system = """..."""` bodies (keep `{bot_name}` etc.).

`prompts/minimal.toml`: same sections, one-line stubs (must include `{bot_name}` / `{identity}` so fill still works).

Temporary stub so `test_planner_prompt_uses_the_template_not_a_hardcoded_string` can import: in `planner_loop.py` add:

```python
def _planner_messages(persona, prompts, item, visible, specs, unlocked):
    raise NotImplementedError
```

(`unlocked` unused until Task 4.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_maibot_shape.py tests/test_prompts.py tests/test_persona.py -v`

Expected: PASS except possibly `test_planner_prompt_uses_the_template_not_a_hardcoded_string` until Step 3 stub is replaced in Task 4. If that test is in `test_prompts.py`, implement a **minimal** `_planner_messages` now:

```python
def _planner_messages(persona, prompts, item, visible, specs, unlocked):
    from mai_bench2.maibot_shape import attention_block
    channel = item.get("channel")
    chat_prompt = persona.private_chat_prompt if channel == "private" else persona.group_chat_prompt
    rule = prompts.query_memory_rule_private if channel == "private" else prompts.query_memory_rule_group
    system = fill(prompts.planner_system, {
        "bot_name": persona.nickname,
        "behavior_style": persona.behavior_style,
        "group_chat_attention_block": attention_block(chat_prompt),
        "query_memory_rule": rule,
    })
    return [{"role": "system", "content": system}]
```

Task 4 expands this. Re-run Step 4 until PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/maibot_shape.py src/mai_bench2/prompts.py src/mai_bench2/planner_loop.py \
  prompts/official.toml prompts/minimal.toml tests/test_maibot_shape.py tests/test_prompts.py
git commit -m "$(cat <<'EOF'
feat: load official MaiBot zh-CN planner and replyer templates

EOF
)"
```

---

### Task 3: Official tool schemas, stubs, tool_search unlock

**Files:**
- Modify: `src/mai_bench2/tools.py`
- Modify: `tests/test_planner_loop.py` (tool-spec tests only in this task)

**Interfaces:**
- Consumes: `item["fixtures"]` keys `query_memory`, `query_person_profile`, `view_forward_message`
- Produces:

```python
ALWAYS_VISIBLE = ("wait", "reply", "query_memory", "query_person_profile", "send_emoji", "send_image", "tool_search")
DEFERRED = ("view_forward_message",)
KNOWN_TOOLS = set(ALWAYS_VISIBLE + DEFERRED)
_COMMITTING_TOOLS = {"wait", "reply"}
_INFO_TOOLS = ("query_memory", "query_person_profile", "view_forward_message")
MEMORY_MISS = "未找到匹配的长期记忆。"
PROFILE_MISS = "未找到可用的人物画像。"
FORWARD_MISS = "未找到该转发消息。"

def tool_specs_for_item(item: dict, *, unlocked: set[str] | None = None) -> list[dict]: ...
def execute_fake_tool(name: str, arguments: dict, item: dict, *, unlocked: set[str] | None = None) -> ToolOutput: ...
def search_deferred(query: str, *, limit: int = 5) -> list[str]: ...
```

`tool_specs_for_item` always offers `ALWAYS_VISIBLE`. Adds `view_forward_message` only if `"view_forward_message" in (unlocked or set())`.

`reply` schema (official, no rich-reply extras):

- description: `根据当前思考生成并发送一条可见回复。`
- properties: `msg_id` (string, 要回复的消息msg_id。), `set_quote` (boolean, 以引用回复的方式发送这条回复，当发言人数过多，聊天比较乱时使用。, default true), `reply_reference` (string, 有助于回复的信息，包括当前聊天状态、人物关系、事实信息、回忆信息。), `reply_style` (string, enum 简短表达/正常回复/长回复)
- required: `["msg_id"]`

`wait`: description `暂停当前对话并固定等待一段时间。`; `seconds` required integer `等待秒数。`

`query_memory`: description `检索长期记忆。`; properties `query`, `limit`, `mode` (enum search/time/hybrid/episode/aggregate), `person_name`, `time_start`, `time_end`, `respect_filter`. No required (MaiBot allows time-only; bench still matches on `query`). Extra args ignored.

`query_person_profile`: description `查询人物画像。`; `person_id`, `person_name`, `limit`.

`send_emoji`: description `发送一个表情包来表达情绪，参与聊天。`; properties `{}`. Execute → `ToolOutput("表情包发送成功", False)` (not an info hit).

`send_image`: description from MaiBot `send_image.py` (the 将context中的图片展示给用户… paragraph); properties `msg_id`, `media_index`, `index`. Execute → `ToolOutput("图片发送成功", False)`.

`tool_search`: description `在 deferred tools 列表中按名称或关键词搜索工具，并将命中的工具加入后续轮次的可用工具列表。`; required `query`; optional `limit`. Execute is done in the planner loop (Task 4) via `search_deferred`. In `execute_fake_tool`, `tool_search` may return a placeholder; the loop will overwrite with search results. Prefer implementing search here:

```python
_DEFERRED_CATALOG = (
    ("view_forward_message", "根据 msg_id 逐层查看合并转发消息。首次调用只展开顶层；遇到嵌套转发时，使用返回内容中的 path 再次调用以继续展开。"),
)

def search_deferred(query: str, *, limit: int = 5) -> list[str]:
    needle = " ".join(query.lower().split())
    if not needle:
        return []
    hits = []
    for name, description in _DEFERRED_CATALOG:
        blob = f"{name} {description}".lower()
        if needle in blob or any(term in blob for term in needle.replace("_", " ").split()):
            hits.append(name)
        if len(hits) >= max(1, limit):
            break
    return hits
```

`view_forward_message` spec: copy description/properties from MaiBot (`msg_id`, `path`). Execute: match `fixtures.view_forward_message` with `query_contains` against `msg_id` (same helper as memory). Miss → `FORWARD_MISS`.

Remove `no_action` and `lookup`.

- [ ] **Step 1: Write the failing tool-spec tests**

In `tests/test_planner_loop.py` replace `FORBIDDEN` / `test_tool_specs_omit_lookup_when_empty` / `test_tool_specs_add_lookup_when_nonempty` with:

```python
VISIBLE = ["wait", "reply", "query_memory", "query_person_profile", "send_emoji", "send_image", "tool_search"]
FOCUS = {"fetch_history", "switch_chat"}


def test_tool_specs_match_default_maibot_visible_set():
    specs = tool_specs_for_item(ITEM)
    assert _names(specs) == VISIBLE
    assert not FOCUS.intersection(_names(specs))
    assert "no_action" not in _names(specs)
    assert "lookup" not in _names(specs)
    assert "view_forward_message" not in _names(specs)
    by_name = {spec["function"]["name"]: spec["function"] for spec in specs}
    assert by_name["reply"]["parameters"]["required"] == ["msg_id"]
    assert set(by_name["reply"]["parameters"]["properties"]) >= {"msg_id", "set_quote", "reply_reference", "reply_style"}
    assert by_name["reply"]["parameters"]["properties"]["reply_style"]["enum"] == ["简短表达", "正常回复", "长回复"]


def test_view_forward_is_offered_after_unlock():
    specs = tool_specs_for_item(ITEM, unlocked={"view_forward_message"})
    assert "view_forward_message" in _names(specs)
```

Keep `ITEM` but drop `"lookup": []` from fixtures (optional).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_planner_loop.py::test_tool_specs_match_default_maibot_visible_set tests/test_planner_loop.py::test_view_forward_is_offered_after_unlock -v`

Expected: FAIL (still `no_action` / old reply properties).

- [ ] **Step 3: Implement `tools.py` as specified**

Update `planner_loop.KNOWN_TOOLS` / `_COMMITTING_TOOLS` in the same commit if imports break. `tool_specs_for_item` signature change: add `unlocked` with default `None`. Call sites in `planner_loop.py` can pass `unlocked=set()` for now.

- [ ] **Step 4: Run the two tests**

Run: `pytest tests/test_planner_loop.py::test_tool_specs_match_default_maibot_visible_set tests/test_planner_loop.py::test_view_forward_is_offered_after_unlock -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/tools.py src/mai_bench2/planner_loop.py tests/test_planner_loop.py
git commit -m "$(cat <<'EOF'
feat: offer MaiBot default planner tools and stub the rest

EOF
)"
```

---

### Task 4: Planner loop protocol and request shape

**Files:**
- Modify: `src/mai_bench2/planner_loop.py`
- Modify: `tests/test_planner_loop.py`

**Interfaces:**
- Consumes: `Prompts` fields from Task 2; `tool_specs_for_item(..., unlocked=)`; `search_deferred`; `stamp`; `attention_block`; `deferred_reminder`
- Produces: `run_planner_loop` predicted `action` `none` on analysis+no tools; `contract_fail` on empty+no tools; `reply_args` may contain `reply_reference`; `_planner_messages(...)` returns the full first-turn list

First-turn messages (in order):

1. `system` — filled planner template (`bot_name`, `behavior_style`, `group_chat_attention_block`, `query_memory_rule`)
2. `user` — `render_log(visible)` if non-empty
3. `user` — `时间：{stamp(clock)}` (`clock` is `target_t` at start)
4. `user` — `deferred_reminder` of still-locked deferred tools (omit if empty)
5. `assistant` — filled `planner_final_assistant_reminder` with `{bot_name}`

Then the model is called. After each step, if tools ran, append assistant+tool messages as today. After `wait`, append 新消息 / 没有新消息 user lines as today. Rebuild **tool specs** each step from `unlocked`. Do not rebuild the system prompt every step (keep the conversation). On `tool_search` success, add names to `unlocked` and put MaiBot-shaped tool result text on the tool message:

- miss: `未找到匹配的 deferred tools，请尝试更完整的工具名、前缀或其他关键词。`
- hit: `已找到 N 个 deferred tools，它们会在后续轮次中加入可用工具列表：` then `- name（本次新发现）`

If `view_forward_message` is called while locked: treat as unknown → `contract_fail` (name not in current `KNOWN` offered set). Implementation: `_malformed` if `call.name not in {spec names this step} ∪ already unlocked` — simpler: `_malformed` if name not in `ALWAYS_VISIBLE ∪ unlocked`.

Idle: if a step has no tool_calls: `stop_reason = "no_tool_call"`; `action = "none"` if `(result.text or "").strip()` else `CONTRACT_FAIL`. Do not require a prior commit. `final_action` = that same label.

`reply` with missing/blank `msg_id` → malformed → `contract_fail`. `reply` with only `msg_id` is valid. Extra keys allowed.

Remove `_label` mapping for `no_action`. Calling `no_action` is malformed.

When `reply` fires, `reply_args = dict(call.arguments)` (official keys). Hits of info tools still fill `tool_reference_text` with `【内部参考】`.

- [ ] **Step 1: Write the failing loop tests**

Replace loop tests in `tests/test_planner_loop.py` that mention `no_action`, `reply_guide`, `lookup`, or a single packed user prompt. Required cases:

```python
def test_loop_memory_then_reply():
    persona = load_persona("official", root=ROOT)
    class Client:
        n = 0
        def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
            self.n += 1
            if self.n == 1:
                return ChatResult("需要记忆", TokenCounts(), False, True, [ToolCall("1", "query_memory", {"query": "上海"})])
            return ChatResult("回复", TokenCounts(), False, True, [ToolCall("2", "reply", {"msg_id": "m1", "reply_reference": "用户下周去上海"})])
    trace = run_planner_loop(Client(), persona, ITEM)
    assert trace.action == "reply"
    assert trace.tools_called == ["query_memory", "reply"]
    assert "用户下周去上海" in trace.reply_args["reply_reference"]


def test_idle_with_analysis_is_none():
    client = SequenceClient([[]])  # chat returns no tools; SequenceClient must set text
    ...
```

`SequenceClient` today always uses `ChatResult("", ...)`. Extend it:

```python
class SequenceClient:
    def __init__(self, calls, texts=None):
        self.calls = list(calls)
        self.texts = list(texts or [])
        self.seen = []
    def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
        self.seen.append({"messages": list(messages), "tools": tools})
        text = self.texts.pop(0) if self.texts else ""
        step = self.calls.pop(0)
        return ChatResult(text, TokenCounts(), False, True, step)
```

Then:

```python
def test_idle_with_analysis_is_none():
    client = SequenceClient([[]], texts=["本轮没有值得回复的内容"])
    trace = run_planner_loop(client, _persona(), ITEM)
    assert trace.action == "none"
    assert trace.stop_reason == "no_tool_call"


def test_mute_empty_text_is_contract_fail():
    client = SequenceClient([[]], texts=[""])
    trace = run_planner_loop(client, _persona(), ITEM)
    assert trace.action == CONTRACT_FAIL


def test_no_action_name_is_contract_fail():
    client = SequenceClient([[ToolCall("1", "no_action", {})]], texts=["x"])
    trace = run_planner_loop(client, _persona(), ITEM)
    assert trace.action == CONTRACT_FAIL


def test_reply_without_msg_id_is_contract_fail():
    client = SequenceClient([[ToolCall("1", "reply", {"reply_reference": "x"})]], texts=["x"])
    trace = run_planner_loop(client, _persona(), ITEM)
    assert trace.action == CONTRACT_FAIL


def test_reply_with_only_msg_id_is_valid():
    client = SequenceClient([[ToolCall("1", "reply", {"msg_id": "m1"})]], texts=["分析"])
    trace = run_planner_loop(client, _persona(), ITEM)
    assert trace.action == "reply"
    assert trace.reply_args["msg_id"] == "m1"


def test_planner_first_turn_shape():
    client = SequenceClient([[ToolCall("1", "reply", {"msg_id": "m1"})]], texts=["分析"])
    run_planner_loop(client, _persona(), ITEM)
    first = client.seen[0]["messages"]
    assert first[0]["role"] == "system"
    assert "你不是 麦麦 本人" in first[0]["content"] or "你不是{bot_name}本人" not in first[0]["content"]
    assert "你不是" in first[0]["content"] and "麦麦" in first[0]["content"]
    assert first[0]["content"].count("MaiBot 形态的规划席") == 0
    roles = [m["role"] for m in first]
    assert roles[0] == "system"
    assert "user" in roles and roles[-1] == "assistant"
    assert any(m["role"] == "user" and m["content"].startswith("时间：") for m in first)
    assert any("<message" in (m.get("content") or "") for m in first if m["role"] == "user")


def test_tool_search_unlocks_view_forward():
    item = deepcopy(ITEM)
    item["fixtures"] = {
        "query_memory": [],
        "query_person_profile": [],
        "view_forward_message": [{"query_contains": "m1", "results": ["转发正文：版本 9.9"]}],
    }
    client = SequenceClient(
        [
            [ToolCall("1", "tool_search", {"query": "view_forward"})],
            [ToolCall("2", "view_forward_message", {"msg_id": "m1"})],
            [ToolCall("3", "reply", {"msg_id": "m1", "reply_reference": "版本 9.9"})],
        ],
        texts=["搜", "看", "回"],
    )
    trace = run_planner_loop(client, _persona(), item)
    assert trace.action == "reply"
    assert "view_forward_message" in trace.tools_called
    assert "版本 9.9" in trace.tool_reference_text
    assert "view_forward_message" in _names(client.seen[1]["tools"])


def test_view_forward_before_unlock_is_contract_fail():
    client = SequenceClient([[ToolCall("1", "view_forward_message", {"msg_id": "m1"})]], texts=["x"])
    trace = run_planner_loop(client, _persona(), ITEM)
    assert trace.action == CONTRACT_FAIL
```

Delete tests that asserted `no_action` in the packed prompt or lookup-in-first-specs.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_planner_loop.py -v`

Expected: FAIL on idle / first-turn shape / unlock.

- [ ] **Step 3: Implement the loop**

Rewrite `run_planner_loop` conversation init to `_planner_messages(...)`. Track `unlocked: set[str]`. Each `client.chat(..., tools=tool_specs_for_item(item, unlocked=unlocked))`. Handle `tool_search` by calling `search_deferred`, updating `unlocked`, writing the result text. Treat `send_emoji` / `send_image` as non-committing success via `execute_fake_tool`.

Idle branch **before** the current “no tools ⇒ contract_fail” that ignored text.

- [ ] **Step 4: Run `tests/test_planner_loop.py`**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/planner_loop.py tests/test_planner_loop.py
git commit -m "$(cat <<'EOF'
feat: match MaiBot planner idle and request shape

EOF
)"
```

---

### Task 5: Replyer role-split messages

**Files:**
- Modify: `src/mai_bench2/suites/replyer.py`
- Modify: `src/mai_bench2/suites/e2e.py`
- Modify: `tests/test_suite_replyer.py`
- Modify: `tests/test_suite_e2e.py`

**Interfaces:**
- Consumes: `identity`, `attention_block`, `stamp`, `target_block`, `replyer_history`, `fill`, `Prompts`
- Produces: `generate_reply` / `_replyer_messages(persona, item, prompts) -> list[dict]`

Handoff dict keys: `messages`, `reply_reference` (str, may be empty), optional `analysis`, optional `msg_id` (default `gold.reply_msg_id` or planner `reply_args.msg_id`), optional `reply_style`.

`_replyer_messages` order:

1. system: `fill(prompts.replyer_system, {identity, reply_style: persona.reply_style, group_chat_attention_block, replyer_output_instruction})`
2. `replyer_history(handoff["messages"])`
3. if `reply_reference.strip()`: user with that text (if e2e appended `【内部参考】`, it is already inside this string). elif `analysis.strip()`: user `当前思考：\n{analysis}`
4. user: `"\n\n".join` of `当前时间：{stamp(t)}`, target_block, `prompts.replyer_final_instruction` (skip empty parts)
5. if `reply_style == "简短表达"`: user `prompts.reply_style_short`; if `长回复`: user `prompts.reply_style_long`

`t` for stamp: `item["target_t"]` in replyer suite; e2e may pass the same.

E2e handoff builder (replace `reply_guide` / `reference_info`):

```python
reference = str(reply_args.get("reply_reference") or "")
if trace.tool_reference_text:
    reference = f"{reference}\n{trace.tool_reference_text}" if reference else trace.tool_reference_text
handoff = {
    "messages": trace.handoff_messages,
    "reply_reference": reference,
    "analysis": trace.assistant_text,
    "msg_id": str(reply_args.get("msg_id") or ""),
    "reply_style": str(reply_args.get("reply_style") or ""),
}
```

- [ ] **Step 1: Write failing suite tests**

In `tests/test_suite_replyer.py` change fixtures that build `oracle_handoff` to the new keys. Replace `test_replyer_system_is_personality_style_group_prompt` with:

```python
def test_replyer_system_is_maibot_identity_and_attention():
    ...
    system = ...  # first message of the replyer client
    assert "你的名字是麦麦。" in system["content"]
    assert persona.personality in system["content"]
    assert persona.reply_style in system["content"]
    assert "在该聊天中的注意事项：" in system["content"]
    assert persona.group_chat_prompt.strip() in system["content"]
    user_roles = [m for m in captured if m["role"] in {"user", "assistant"}]
    assert not any("<message" in (m.get("content") or "") for m in user_roles[:-1])
    assert "请自然地回复" in captured[-1]["content"] or "请自然地回复" in captured[-2]["content"]
```

(Inspect however that test currently captures `client.chat` arguments; keep the same capture style.)

Update e2e `test_e2e_replyer_prompt_includes_nickname_and_planner_handoff` to look for `reply_reference` / `当前思考` instead of `reply_guide`.

- [ ] **Step 2: Run those tests; expect FAIL**

Run: `pytest tests/test_suite_replyer.py tests/test_suite_e2e.py -v`

Expected: FAIL on missing keys / old system packing. (Gold jsonl still old — if load_gold fails, Task 6/7; for this task use in-test items, not disk gold, if the suite tests load ROOT gold.)

If suite tests load `ROOT` gold and will error until Task 7, **narrow** this task’s tests to functions that take an in-memory item: export `_replyer_messages` and unit-test it with a dict, and only run that plus e2e tests that construct items inline.

Add `tests/test_replyer_messages.py`:

```python
from mai_bench2.persona import load_persona
from mai_bench2.prompts import load_prompts
from mai_bench2.suites.replyer import _replyer_messages
from conftest import ROOT

def test_replyer_messages_role_split_and_current_thinking():
    persona = load_persona("official", root=ROOT)
    prompts = load_prompts("official", root=ROOT)
    item = {
        "channel": "group",
        "target_t": 0,
        "oracle_handoff": {
            "messages": [
                {"t": 0, "msg_id": "m1", "user": "q1", "group_card": "小徐", "text": "麦麦 在吗"},
                {"t": 1, "msg_id": "m2", "user": "麦麦", "text": "嗯", "is_self_message": True},
            ],
            "reply_reference": "",
            "analysis": "对方在叫我，回一声",
            "msg_id": "m1",
        },
    }
    messages = _replyer_messages(persona, item, prompts)
    assert messages[0]["role"] == "system"
    assert "你的名字是麦麦。" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "麦麦 在吗"}
    assert messages[2] == {"role": "assistant", "content": "嗯"}
    assert any(m["content"].startswith("当前思考：") for m in messages)
    assert any("当前时间：2026-01-01 12:00:00" in m["content"] for m in messages)
    assert any("m1" in m["content"] and "小徐" in m["content"] for m in messages)
    assert any("请自然地回复" in m["content"] for m in messages)
```

- [ ] **Step 3: Implement `_replyer_messages` and e2e handoff**

- [ ] **Step 4: Run `pytest tests/test_replyer_messages.py tests/test_maibot_shape.py -v`**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/suites/replyer.py src/mai_bench2/suites/e2e.py tests/test_replyer_messages.py tests/test_suite_replyer.py tests/test_suite_e2e.py
git commit -m "$(cat <<'EOF'
feat: build replyer requests like MaiBot role-split history

EOF
)"
```

---

### Task 6: Gold loader, metrics, judge wording

**Files:**
- Modify: `src/mai_bench2/gold.py`
- Modify: `src/mai_bench2/metrics.py`
- Modify: `src/mai_bench2/judge.py`
- Modify: `tests/test_gold.py`
- Modify: `tests/test_metrics.py`
- Modify: `tests/test_judge.py`

**Interfaces:**
- Consumes: `PlannerTrace.reply_args["reply_reference"]`, `PlannerTrace.assistant_text`
- Produces: `validate_item` requires `oracle_handoff` keys `messages` and `reply_reference`; rejects `reply_guide`, `reference_info`, `lookup` fixtures, gold.tools containing `lookup`; `RUBRIC_VERSION = 3`

```python
def _briefing_text(trace: PlannerTrace) -> str:
    args = trace.reply_args or {}
    return (
        str(args.get("reply_reference") or "")
        + str(trace.assistant_text or "")
        + str(trace.tool_reference_text or "")
    )
```

`is_info_tool` no longer includes `lookup`; include `view_forward_message`.

Judge user payload: `reply_reference` and `analysis` lines instead of `reply_guide` / `reference_info`. Rubric sentence: `grounding 是否只用 reply_reference / 当前思考、不编造`.

- [ ] **Step 1: Write failing gold/metrics tests**

`test_gold.py`: an item with `oracle_handoff.reply_guide` must `ValueError`; a valid item uses `reply_reference` (empty string OK). `fixtures.lookup` non-empty → invalid. `gold.tools: ["lookup"]` → invalid.

`test_metrics.py`: briefing coverage hits facts in `reply_args["reply_reference"]` **or** `assistant_text`. `tool_f1(["query_memory", "view_forward_message"], ["query_memory"])` uses info-tool filter (emoji not counted). Remove the old `lookup` F1 example; use `view_forward_message` as the extra pred if needed.

- [ ] **Step 2: Run; expect FAIL**

Run: `pytest tests/test_gold.py tests/test_metrics.py tests/test_judge.py -v`

- [ ] **Step 3: Implement validation, `_briefing_text`, judge strings, `RUBRIC_VERSION = 3`**

- [ ] **Step 4: Re-run Step 2 tests**

Expected: PASS. Disk gold jsonl may still make `load_gold(ROOT)` fail — keep those tests using tmp_path items until Task 7.

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/gold.py src/mai_bench2/metrics.py src/mai_bench2/judge.py \
  tests/test_gold.py tests/test_metrics.py tests/test_judge.py
git commit -m "$(cat <<'EOF'
fix: score idle and briefing on MaiBot handoff fields

EOF
)"
```

---

### Task 7: Rewrite scenarios and regenerate gold

**Files:**
- Modify: `tools/goldkit.py`
- Modify: `tools/scenarios_replyer.py`
- Modify: `tools/scenarios_planner.py`
- Modify: `tools/scenarios_e2e.py` (if it mentions lookup/guide)
- Regenerate: `data/gold/planner.jsonl`, `data/gold/replyer.jsonl`, `data/gold/e2e.jsonl`

**Interfaces:**
- Consumes: Task 6 validator
- Produces: 108 / 110 / 108 items still; no `lookup` / `reply_guide` / `no_action` strings in jsonl

`Item.to_json` fixtures:

```python
"fixtures": {
    "query_memory": self.fixtures.get("query_memory", []),
    "query_person_profile": self.fixtures.get("query_person_profile", []),
    "view_forward_message": self.fixtures.get("view_forward_message", []),
},
```

`R(...)` in `scenarios_replyer.py`:

```python
def R(..., guide: str, ref: str = "", ...):
    handoff={
        "reply_reference": ref,
        "analysis": guide if not ref else "",
        "messages": [...],
        "msg_id": reply_to,
    }
```

If `ref` is non-empty, put facts there and put `guide` in `analysis` only when `guide` is not a duplicate of `ref`. If `ref` is empty, `analysis = guide` (style-only thinking).

Planner: change `tools=("lookup",)` to `tools=("query_memory",)` and `fixtures={"lookup": [memory(...)]}` to `fixtures={"query_memory": [memory(...)]}`. Rename ids `p-tool-lookup-001` **keep the id** (stable) even if tags stay `lookup` or change to `memory` — keep id to avoid e2e pointer breakage.

Update `p-contract-003` note: idle is analysis and no tools, not `no_action`.

- [ ] **Step 1: Write a failing check that ROOT gold loads under the new validator**

In `tests/test_gold.py`:

```python
def test_shipped_gold_loads_under_maibot_handoff():
    for suite in ("planner", "replyer", "e2e"):
        items = load_gold(ROOT, suite)
        assert items
        blob = str(items)
        assert "reply_guide" not in blob
        assert "no_action" not in blob
```

(`reference_info` must not appear as a **key**; the Chinese prose may still contain 参考.)

- [ ] **Step 2: Run; expect FAIL** (old jsonl)

Run: `pytest tests/test_gold.py::test_shipped_gold_loads_under_maibot_handoff -v`

- [ ] **Step 3: Edit tables and regenerate**

```bash
cd /mnt/klein/work/mai-bench-2
python tools/build_gold.py
```

(Use whatever entry `tests/test_gold.py` / README already use. If `uv run python tools/build_gold.py`, do that.)

- [ ] **Step 4: Re-run gold tests including counts**

Run: `pytest tests/test_gold.py -v`

Expected: PASS; item counts still 108 / 110 / 108.

- [ ] **Step 5: Commit**

```bash
git add tools/goldkit.py tools/scenarios_planner.py tools/scenarios_replyer.py tools/scenarios_e2e.py \
  data/gold/*.jsonl tests/test_gold.py
git commit -m "$(cat <<'EOF'
chore: regenerate gold for MaiBot reply_reference handoff

EOF
)"
```

---

### Task 8: Remaining tests, docs, full suite

**Files:**
- Modify: `tests/test_suite_planner.py` (`no_action` scripted calls → analysis + `[]` tools)
- Modify: `tests/test_cli_run.py`, `tests/test_report.py`, `tests/test_narrative.py`, `tests/test_docs.py` (`1a46dd3e9eb3` → `77be5c59f150`)
- Modify: `src/mai_bench2/narrative.py` (tool list; drop lookup / no_action)
- Modify: `README.md`, `THIRD_PARTY.md`
- Modify leftover assertions in `tests/test_suite_replyer.py` / `tests/test_suite_e2e.py` to new handoff keys

**Interfaces:** none new.

README must say: official templates are MaiBot zh-CN `maisaka_chat` / `maisaka_replyer`; idle is no tool call with analysis; tools listed as in Task 3; persona hex `77be5c59f150`; `reply_reference` not `reply_guide`.

`THIRD_PARTY.md`: Field defaults + `prompts/zh-CN/maisaka_*.prompt`.

- [ ] **Step 1: Grep for leftovers**

```bash
rg -n "no_action|reply_guide|reference_info|lookup|1a46dd3e9eb3|MaiBot 形态的规划席" --glob '!docs/superpowers/**' --glob '!.git/**'
```

Fix every hit under `src/`, `tests/`, `prompts/`, `README.md`, `THIRD_PARTY.md`, `tools/` (docs/superpowers historical specs may keep old words).

- [ ] **Step 2: Run full tests**

Run: `pytest -q`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
docs: describe official as MaiBot-faithful prompts and protocol

EOF
)"
```

---

## Self-review (plan vs spec)

| Spec section | Task |
|---|---|
| §1 Persona Field defaults + hex | 1 |
| §2 Prompt files + hashed Python strings | 2 |
| §3 Identity / attention / stamp | 2 |
| §4 Planner idle, request shape, tools, deferred | 3, 4 |
| §5 Replyer messages + e2e handoff | 5 |
| §6 Scoring briefing / none | 6 |
| §7 Gold rewrite | 7 |
| §8 Tests and docs | 1–8 |
| §9 Non-goals (no Focus, no MCP, no classic snapshot) | global constraints |
| send_emoji/image stubs not scoring as reply | 3, 4 (`_COMMITTING_TOOLS`) |
| `tool_search` then `view_forward_message` | 4 tests |
| `prompts/minimal.toml` new schema | 2 |

No TBD. `Prompts` fields named in Task 2 are what later tasks import. `RUBRIC_VERSION = 3` only in Task 6.
