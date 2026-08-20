# Third-party notices

## Official 人设

`personas/official.toml` is a snapshot of MaiBot Field defaults from
`src/config/official_configs.py` (`PersonalityConfig`, nickname, and Chat
`reply_style` group/private prompts). License: GPL-3, same as MaiBot and
mai-bench-2. The snapshot is not auto-updated if upstream changes.

## Official prompts

`prompts/official.toml` planner and replyer system bodies are a byte copy of
MaiBot `prompts/zh-CN/maisaka_chat.prompt` and `prompts/zh-CN/maisaka_replyer.prompt`.
License: GPL-3, same as MaiBot and mai-bench-2. The copy is not auto-updated if
upstream changes.

MaiBot is not a runtime dependency. This harness does not run or import MaiBot.
Judge and test-model HTTP providers are user-configured in `config.toml`; this
package does not ship API keys or bind a hosted vendor.

## Not included

This package contains no Ambient, When2Speak, or SOTOPIA code or data.
