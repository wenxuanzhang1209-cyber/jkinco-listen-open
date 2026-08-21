# Show HN

发布时间建议：北京时间 16:00–17:00（美西 01:00 前后，HN 早高峰）。
标题控制在 80 字符内，突出“100% local + 工程会议纪要”的差异化。

## 标题（三选一）

1. Show HN: JKinco Listen – engineering-grade meeting minutes, 100% local, zero API keys
2. Show HN: I built a local-first meeting minutes tool for Chinese construction sites (FunASR + Ollama)
3. Show HN: Meeting recordings in, structured Word/PDF out – no cloud, no keys, Docker one-command

## 正文

```text
Hi HN,

I built JKinco Listen (Open Edition): a self-hosted meeting minutes
workbench where the entire pipeline runs on your machine.

What it does:
- Upload a meeting recording → local Chinese ASR (FunASR paraformer-zh)
- Scene recognition with rule-gated evidence: site meeting / general /
  personal notes / interview / customer visit
- Local LLM (Ollama, any OpenAI-compatible model) generates structured
  minutes + a second quality-review pass
- Export original-layout DOCX/PDF per scene
- Optional local history search and "ask your meetings" Q&A

Why local:
- No API keys. No cloud inference. No telemetry.
- Audio and text never leave your machine (matters a lot for
  engineering-supervision and HR interviews).
- Works fully offline after the first model download.

Why it matters to me: construction-site meetings in China are recorded on
USB recorders, then someone manually writes the minutes. The site-meeting
template has real legal/process value (五方验收, supervision notices...),
so a generic "AI summary" isn't enough — the classification must respect
engineering evidence, which the rule gate enforces.

Docker one-command:
docker compose up -d --build  →  http://localhost:8080

888 automated tests, MIT license, CI green (backend tests, frontend build,
docker image build, plus a red-line scanner that blocks cloud-service traces
and secrets from entering the repo).

Looking for feedback on: model defaults, template quality, and the
realtime-subtitle roadmap (next up: streaming local ASR + speaker diarization).
```

## 第一小时必做

- 每一条评论都回复，哪怕只是“谢谢，已加入路线图”；
- 把有价值的问题整理成 FAQ 更新进 README；
- 有人报 issue 立刻修、立刻在 HN 回复“fixed in v0.1.1”。速度本身就是传播。
