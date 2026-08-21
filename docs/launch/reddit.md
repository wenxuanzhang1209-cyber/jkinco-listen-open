# Reddit 四连发

北京时间 08:00–09:00（美东 20:00 前后，晚高峰）。
四个子版块同一主题、不同角度，标题不要复制粘贴。

## r/selfhosted（转化主力）

```text
[Project] Self-hosted meeting minutes: recording in, Word/PDF out, 100%
local (FunASR + Ollama, zero API keys)

I built a fully local meeting-minutes workbench for engineering/business
meetings. Upload a recording → local ASR → rule-gated scene recognition →
local LLM generates structured minutes → export DOCX/PDF.

No cloud, no keys, no telemetry. Docker one-command. MIT. 888 tests.
GitHub: https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open
```

## r/LocalLLaMA（技术向）

```text
Local LLM + FunASR = meeting minutes that never leave your machine

Showcase: a FastAPI + React app where FunASR paraformer-zh does Chinese ASR
and Ollama (qwen2.5:7b default) does scene classification + structured
minutes + quality review. Includes rule-gated evidence so the LLM can't
overrule strong engineering evidence.

Happy to answer questions about chunking, retry/fallback design, and
local-model quality.
```

## r/opensource

```text
Just open-sourced my local-first meeting minutes tool (MIT)

Engineering supervision meetings in China produce hours of recordings that
someone transcribes by hand. I built a local tool that turns them into
scene-aware structured minutes with original-layout Word/PDF export. All
models run locally, works offline after first download.
```

## r/ChineseLanguage 或 r/China_irl（中文语境）

```text
工地例会的录音终于不用手动整理了：本地 AI 纪要工具（零云端）
```

## 规则

- 先参与社区讨论积累 karma，再发帖（r/selfhosted 对新号严格）；
- 回复每条评论，不要只发链接；
- 如果被标记为推广，承认是作者并说明开源免费。
