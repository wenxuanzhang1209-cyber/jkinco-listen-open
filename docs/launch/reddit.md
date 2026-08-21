# Reddit 发布文案（吸引人版本）

原则：**先讲真实痛点，再给方案**；标题像“用户在抱怨”，不像“开发者在推销”。
每条都带演示 GIF（`https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/raw/main/docs/demo.gif`），
正文不超过 8 行核心内容，结尾给一个具体问题请人回答（评论数 = 传播权重）。

---

## 1. r/selfhosted（主力）

### 标题（三选一）

- I replaced my cloud meeting-notes habit with a fully local one: recording in, Word/PDF out, audio never leaves my desk
- Self-hosted meeting minutes with FunASR + Ollama: zero API keys, zero telemetry, one docker command
- After hand-writing construction-site minutes for years, I self-hosted an AI that does it locally

### 正文

```text
My job involves a lot of engineering meetings. The recordings live on a USB
recorder; the minutes used to live in my evenings.

Cloud transcription was a non-starter: site meetings contain contract numbers,
responsibility chains, acceptance details. Nobody wants that on a third-party
server.

So I built a fully local meeting-minutes workbench:

• FunASR paraformer-zh → local Chinese ASR
• Ollama (any OpenAI-compatible model) → scene classification + structured
  minutes + a second quality-review pass
• Rule-gated scene recognition: the LLM can't override strong engineering
  evidence (site meeting / general / personal / interview / customer visit)
• Original-layout DOCX/PDF export per scene
• Local history + "ask your meetings" Q&A

Zero API keys. Zero cloud. Zero telemetry. Works fully offline after the
first model download.

Docker one-command:
docker compose up -d --build   →   http://localhost:8080  (admin/123456)

MIT, 888 automated tests, CI green (tests + frontend + docker image build).

What would make you actually switch from your current workflow?

[repo](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open)

![demo](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/raw/main/docs/demo.gif)
```

---

## 2. r/LocalLLaMA（技术向）

### 标题（三选一）

- FunASR + Ollama meeting minutes, with rule-gated scene recognition so the LLM can't misclassify engineering meetings
- Local meeting minutes pipeline: ASR → evidence-gated classifier → LLM minutes → quality review. Ask me anything
- I gated my local LLM behind domain rules (engineering evidence) — here's why it beats letting the model decide

### 正文

```text
Posting because this sub helped me choose the stack:

• ASR: FunASR paraformer-zh + fsmn-vad + ct-punc (fully local, CPU-friendly)
• LLM: Ollama, default qwen2.5:7b-instruct, any OpenAI-compatible endpoint works
• Classification: local rule engine scores engineering evidence first
  (responsibility chain, production-control agenda, weekly cycle, deadlines);
  the LLM only reviews and cannot override strong evidence
• Minutes: chunked extraction for long recordings → synthesis → a second
  "fact/template/responsibility closure" review pass
• Fallbacks: model failover, per-chunk resilience (one bad chunk doesn't kill
  the whole 3-hour meeting)

888 tests, MIT, docker one-command, fully offline after model download.

Open questions for you:
1. Which local model gives you the best Chinese meeting-minutes quality on 16GB?
2. Anyone running streaming local ASR (paraformer-zh-streaming / sherpa) in
   production? That's our next milestone.

[repo](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open)
```

---

## 3. r/opensource（开源向）

### 标题（三选一）

- I just open-sourced (MIT) a local-first meeting minutes tool with 888 tests and a CI that even builds the Docker image
- Open-sourcing my local AI meeting minutes workbench — no SaaS, no keys, no telemetry
- MIT-licensed local meeting minutes: the full pipeline in one repo (FastAPI + React + FunASR + Ollama)

### 正文

```text
Just shipped the open-source version of a tool I use daily for engineering
meetings.

What's inside:
- FastAPI + React/TypeScript
- FunASR local ASR + Ollama local LLM
- Rule-gated scene recognition (site meeting / general / personal /
  interview / customer visit)
- Original-layout DOCX/PDF export + custom templates
- Local history search + meeting Q&A
- 888 automated tests; CI runs red-line scans, tests, frontend build and a
  real Docker image build

MIT. No API keys. No cloud calls. No telemetry. Works offline after the first
model download.

Looking for contributors on: streaming realtime subtitles, speaker
diarization, desktop installers.

[repo](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open)
```

---

## 4. r/civilengineering（专业向，潜力最大）

### 标题（三选一）

- Site meeting minutes from recordings, fully local: supervision notices, 五方验收, responsibility chains — not generic AI slop
- We build sites, not summaries: a local tool that turns recorder audio into proper 工地例会 minutes
- How I stopped transcribing site meetings by hand (fully offline, evidence-gated, Word/PDF out)

### 正文

```text
Every construction project I know has the same ritual: someone records the
weekly site meeting on a USB recorder, then someone else writes the minutes by
hand — because the minutes carry real process weight: who is responsible,
what was inspected, what must be submitted by when.

Cloud AI tools won't touch this: contract numbers, acceptance criteria and
people's names shouldn't sit on a third-party server.

I built a fully local tool:

• Recordings → local Chinese ASR (FunASR)
• Scene classification gated by engineering evidence: multiple responsible
  parties + production-control agenda + weekly cycle + deadlines. No evidence,
  no engineering template. (A single "project progress" sentence won't trigger it.)
• Minutes follow the site-meeting template: progress / quality / safety /
  resources / actions with owners and dates
• Original-layout Word/PDF export

Docker one-command, MIT, 888 tests, works offline.

If you deal with 工地例会 / 监理例会, try it and tell me what the template
gets wrong — real-domain feedback is the fastest way to make it better.

[repo](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open)

![demo](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/raw/main/docs/demo.gif)
```

---

## 5. r/Construction（施工向）

### 标题（三选一）

- Construction meeting minutes from audio, 100% local: our site meetings finally get proper minutes without uploading anything
- Tool demo: recorder audio → site-meeting minutes → Word/PDF, offline, with responsibility tracking
- Does anyone else hand-write meeting minutes after every site meeting? I automated it locally

### 正文

```text
Site meeting at 4pm. Minutes due by 6pm. Every week.

I built a local tool that turns the recording into structured minutes:
progress, quality, safety, resources, and actions with owners + dates — using
the actual site-meeting template, not a generic summary.

Key difference from every SaaS: audio and text never leave the computer.
No API key, no account, no telemetry. It even works with the Wi-Fi off.

Docker one-command. MIT. 888 tests.

Curious: how do your projects handle meeting minutes today? By hand, by
recording app, or does someone just "have it in their head"?

[repo](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open)
```

---

## 6. r/ChineseLanguage / r/China_irl（中文向）

### 标题（三选一）

- 工地例会的会议纪要，我做了个 100% 本地的 AI 工具：录音不出电脑，Word/PDF 直接导出
- 受够了手写监理例会纪要？这个工具把录音直接变成结构化纪要（离线可用）
- 工程会议录音 → 本地 AI 纪要：五方验收、责任闭环，模板不是“通用摘要”

### 正文

```text
做工程的人应该都懂：会议录完音，纪要靠手写。

不是没人做 AI 纪要，而是大多数工具要把录音传到云端——合同编号、验收结论、
责任人和时间节点，哪个适合放第三方服务器？

所以我做了个全本地版本：

• FunASR 本地中文转写
• Ollama 本地大模型生成纪要
• 规则证据门控：没有施工/监理/建设多主体证据链，就不套工程模板
• 工程例会 / 通用 / 个人 / 面试 / 客户拜访五套模板，原版式 Word/PDF 导出
• 零 API Key、零云端、零遥测，模型下载完可断网使用

Docker 一条命令：docker compose up -d --build
MIT 开源，888 个自动化测试。

如果你们项目也是“录音一时爽，纪要火葬场”，欢迎试试，顺便告诉我模板哪里不对。

[仓库](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open)
```

---

## 发布技巧（决定点击率）

1. **同一天不要用同一个标题发多个版块**——每个版块换角度，避免被判 spam；
2. 发完 30 分钟内回复每一条评论，哪怕只是“好问题，已加入路线图”；
3. 标题里放具体词（FunASR、五方验收、Word/PDF、零 API Key），不要只写 “AI tool”；
4. 正文第一屏放 GIF 或截图，Reddit 网页版会自动展开；
5. 如果有人问“和你那个 SaaS 有什么区别”，回答锚定三件事：隐私、模板、证据门控。
