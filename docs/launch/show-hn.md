# Show HN

发布时间建议：北京时间 16:00–17:00（美西 01:00 前后，HN 早高峰）。
标题控制在 80 字符内，先讲“self-hosted live meeting + local captions + filed minutes”，
再用工程场景证明专业深度。

## 标题（三选一）

1. Show HN: JKinco Listen – self-hosted meetings with local captions and filed minutes
2. Show HN: Live captions on your own machine, structured Word/PDF when the meeting ends
3. Show HN: A private meeting workspace built around live local transcription

## 正文

```text
Hi HN,

I built JKinco Listen (Open Edition): a self-hosted meeting workspace
where live captions and the document pipeline run on infrastructure you control.

What it does:
- Browser meeting through self-hosted LiveKit (audio, video, screen share, chat)
- Local streaming captions (FunASR paraformer-zh-streaming)
- Scene recognition with rule-gated evidence: site meeting / general /
  personal notes / interview / customer visit
- Local LLM (Ollama, any OpenAI-compatible model) generates structured
  minutes + a second quality-review pass
- Export original-layout DOCX/PDF per scene
- Local history search and "ask your meetings" Q&A

Why self-hosted:
- No cloud model API key. No cloud inference. No telemetry.
- Audio and text stay inside the deployment you control.
- Works fully offline after the first model download.

It is intentionally general: management reviews, customer calls, project
weeklies, interviews, training, and research interviews all use the same
workspace. Construction-site reviews are the stress test — multiple accountable
parties, dense terminology, and legal consequences when responsibility is
transcribed incorrectly.

Docker one-command:
docker compose up -d --build  →  http://localhost:8080

965 automated tests, MIT license, CI green (backend tests, frontend build,
Docker image build, working-tree red-line scan, and redacted full-history
secret scanning).

Looking for feedback on: streaming ASR adapters, speaker diarization, model
defaults, and whether the exported minutes are genuinely useful in your workflow.
```

## 第一小时必做

- 每一条评论都回复，哪怕只是“谢谢，已加入路线图”；
- 把有价值的问题整理成 FAQ 更新进 README；
- 有人报 issue 立刻修、立刻在 HN 回复修复版本。速度本身就是传播。
