<div align="center">

<img src="assets/jkinco_logo_cropped.png" width="140" alt="JKinco Listen" />

# JKinco Listen (Open Edition)

**Live captions in the room. Filed minutes on your machine.**

Start a browser meeting with self-hosted media, watch captions stream from a local ASR model,
and finish with structured minutes plus **DOCX/PDF**. No cloud model calls. Your meeting
audio stays on infrastructure you control.

[简体中文](README.zh-CN.md) · [Docs site](https://wenxuanzhang1209-cyber.github.io/jkinco-listen-open/en.html) · [Architecture](docs/ARCHITECTURE.en.md) · [Model guide](docs/LOCAL_MODELS.en.md) · [Roadmap](docs/ROADMAP.en.md) · [Changelog](CHANGELOG.md)

[![CI](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/actions/workflows/ci.yml/badge.svg)](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/actions/workflows/ci.yml)
![MIT License](https://img.shields.io/badge/license-MIT-green.svg)
![GitHub Stars](https://img.shields.io/github/stars/wenxuanzhang1209-cyber/jkinco-listen-open?style=social)
![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)
![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688.svg)
![React](https://img.shields.io/badge/Frontend-React%20%2B%20TypeScript-61dafb.svg)
![Live captions](https://img.shields.io/badge/Live_captions-local-3fb950.svg)
![Self-hosted](https://img.shields.io/badge/Meetings-self--hosted-1f6feb.svg)
![Tests](https://img.shields.io/badge/tests-965%20passing-brightgreen.svg)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

</div>

---

## Why this exists

Most meeting tools stop at a transcript, or send the room to somebody else's infrastructure.
Neither is enough when the transcript needs to become an accountable document and the audio
cannot leave the building.

JKinco Listen is for the whole path from conversation to filing:

- **Live meeting** — browser video, screen sharing, chat, and local streaming captions.
- **Local speech recognition** — meeting-grade Mandarin ASR with voice-activity detection,
  punctuation restoration, and domain-term correction.
- **Local LLM** — scene detection, minutes generation, quality review, and meeting Q&A run
  against a model you host (Ollama, or any OpenAI-compatible endpoint).
- **Documents people can file** — five meeting types and custom templates exported to
  DOCX/PDF with the original layout preserved.
- **Local history** — meetings, transcripts, minutes, and human edits stay in a SQLite file
  you own.

> One command to deploy. Join a meeting, speak, and leave with a document.

## Built for any high-context meeting

The product is intentionally general: the same workspace handles a management review, a
customer call, a project weekly, an interview, a training session, or a research interview.
Industry vocabulary and templates are extensions, not the front door.

| Meeting | What JKinco Listen adds |
|---|---|
| Management / board review | Decisions, owners, risks, and follow-ups in one structured record |
| Project weekly / operations | Live captions, searchable history, and repeatable action-item extraction |
| Customer call / discovery | Needs, objections, commitments, and next steps without uploading the call |
| Interview / hiring panel | A consistent evidence-oriented record and exportable interview minutes |
| Training / workshop | Searchable notes and a durable summary for people who could not attend |
| Research / field interview | Local storage, editable transcript, and portable DOCX/PDF output |

### Engineering is our stress test, not our ceiling

A construction site review has multiple accountable parties, dense domain terms, formal
deliverables, and legal consequences if a responsibility is transcribed incorrectly. We use
that environment to harden the generic pipeline: the domain lexicon, evidence gate, and
original-layout export all started there.

The result is stronger for every meeting: evidence-gated scene detection, exact templates,
and a local model stack you can replace.

## How it compares

|  | JKinco Listen (Open) | Cloud note-takers | Raw Whisper / FunASR |
|---|---|---|---|
| Audio leaves your machine | **Never** | Yes | Never |
| Cloud AI API key / subscription | **None** | Required | None |
| Works fully offline | **Yes**, after first model download | No | Yes |
| Live meeting + local captions | **Yes, self-hosted** | Yes, through their cloud | Captions only |
| Output | **Formatted DOCX/PDF minutes** | Transcript + summary | Raw transcript |
| Domain accuracy | **Hotword lexicon + post-correction** | Generic | Generic |
| Meeting-type awareness | **5 scenes, evidence-gated** | One format | None |
| Self-hosted, MIT licensed | **Yes** | No | Library only |

If you only need a transcript, use Whisper directly — it is simpler. This project exists for
the part *after* the transcript: turning it into a document somebody can actually file.

### Measured domain accuracy

Full results and methodology: **[docs/BENCHMARK.md](docs/BENCHMARK.md)**

Ten noisy Chinese meeting clips, 36 domain terms that a general model tends to get wrong
(construction, HR, and commercial vocabulary), run through the local `paraformer-zh`:

| | Terms recognised |
|---|---|
| Local ASR alone | 31 / 36 |
| **+ domain correction** | **36 / 36** |

Every one of the six failures was a construction term:

```
航站记录 ← 旁站记录      微大工程 ← 危大工程
石滩落度 ← 时坍落度      同条件是块 ← 同条件试块
```

The correction table is built from **what the model actually gets wrong**, not from what a
human might mistype — the first thirteen entries were written by imagining plausible errors,
and when this benchmark ran, not one of them fired. Reproduce it yourself — one command, no arguments:

```bash
pip install funasr torch torchaudio numpy
python3 scripts/benchmark_domain_accuracy.py
```

It synthesises the clips, degrades them with noise and tempo shift, runs the local model, and
prints the same table. The audio is synthetic and therefore **cleaner than a real meeting room**
— treat the number as an upper bound. Its value is comparability: change the model or the
lexicon, run it again, and the delta is trustworthy.

<sub>十段加噪中文会议音频、36 个通用模型容易听错的领域词，过本地 paraformer-zh：
仅本地识别 31/36，加上领域纠错 36/36。六处失误全在工程术语上。
纠错表是照着**模型真正会犯的错**写的——最初那 13 条是想象出来的，跑基准时一条都没命中。</sub>

## Highlights

| | |
|---|---|
| **Live meeting, local captions** | Self-hosted LiveKit rooms, screen sharing, chat, and local streaming ASR in one workspace |
| **Two local model stacks** | FunASR `paraformer-zh` for speech + any Ollama / OpenAI-compatible model for text |
| **Evidence-gated scene detection** | Rule-based gate first, model second — the model cannot force a construction template onto a meeting that lacks the evidence |
| **Original-layout export** | DOCX/PDF that match the real corporate templates, not a generic markdown dump |
| **Privacy by construction** | No cloud AI keys, no telemetry, no outbound model calls; CI scans the working tree and Git history |
| **One-command deploy** | `docker compose up` brings up web, backend, local ASR, Ollama, and a self-hosted LiveKit room |
| **965 automated tests** | Plus security headers, rate limiting, injection defenses, and an audit log |
| **Local knowledge base** | Search past meetings and ask questions across them, without anything leaving the machine |

## Screenshots

![Demo](docs/demo.gif)

<sub>Full-resolution MP4: <a href="docs/demo.mp4">docs/demo.mp4</a></sub>

**This is what comes out** — summary, agenda flow, conclusions, and action items, with the
full recording-to-export pipeline on the right:

![Structured minutes](docs/demo-minutes.png)

<details>
<summary>More screens (click to expand)</summary>

**Sign-in** — a local account, no cloud registration

![Sign-in](docs/demo-login.png)

**Workspace** — upload, live recording, or read straight from a recorder; seven scene tabs

![Workspace](docs/demo-workspace.png)

**History** — per-scene stats, full-text search, reopen and export any past meeting

![History](docs/demo-history.png)

</details>

## Quick start (Docker)

Requires Docker. 16 GB RAM recommended; 8 GB works but is slower.

```bash
git clone https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open.git
cd jkinco-listen-open
cp .env.example .env
docker compose up -d --build
```

Open <http://localhost:8080>.

**First live meeting:** sign in → **实时会议** / Live meetings → **开始会议** → allow the
browser microphone and camera. The first caption downloads the local streaming ASR model;
after that, recognition runs on the same machine.

> **Local trial credentials are deliberately obvious.** `.env.example` ships with the web
> login `admin:123456` and LiveKit development mode (`devkey` / `secret`). The web app listens
> on `0.0.0.0`, while LiveKit's ports bind to loopback by default. Replace both credentials,
> set a reachable `LIVEKIT_PUBLIC_URL`, and terminate TLS before exposing meetings to a network.

The first start automatically:

1. downloads the local ASR model (~1–2 GB; fully offline afterwards);
2. pulls the local LLM `qwen2.5:7b-instruct` via Ollama;
3. builds the web UI and starts the backend.

Want a different model? `OLLAMA_MODEL=qwen2.5:14b docker compose up -d` — nothing else changes.

**NVIDIA GPU:**

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

Apple Silicon needs no configuration; Ollama uses Metal automatically.

**Try it before downloading any model:**

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d
```

Demo mode seeds two example minutes so the UI, exports, and history search are all usable
without a model.

## Manual setup

One command, for people who would rather not use Docker:

```bash
bash scripts/install.sh        # Ollama model + Python deps + frontend
bash scripts/start.sh
```

Or step by step:

```bash
# 1. Local LLM (pick one)
brew install ollama && ollama pull qwen2.5:7b-instruct   # macOS
curl -fsSL https://ollama.com/install.sh | sh             # Linux

# 2. Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# 3. Frontend
cd frontend && npm ci && npm run build && cd ..

# 4. Run
uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

Larger model: `OLLAMA_MODEL=qwen2.5:14b bash scripts/install.sh`

The script starts the application and local ASR. For browser meetings, run LiveKit separately
or use the Docker path above, which starts it automatically.

## Model guide

| Stage | Default | Alternatives | Notes |
|---|---|---|---|
| Speech recognition | FunASR `paraformer-zh` | `fsmn-vad` + `ct-punc` | Best value for Chinese meetings; runs on CPU |
| Live captions | FunASR `paraformer-zh-streaming` | Replace the adapter with another streaming ASR | Runs locally; extra model download on first use |
| Scene review | Ollama `qwen2.5:7b-instruct` | `qwen2.5:14b`, `qwen3:8b` | The evidence gate has the final say, so a wrong model answer cannot flip the result |
| Minutes generation | Ollama `qwen2.5:7b-instruct` | Larger models | Chunk → extract in parallel → merge → quality review |
| Meeting Q&A | Same | Same | Local retrieval and generation; history stays on the machine |

Hardware and quantization guidance: [docs/LOCAL_MODELS.en.md](docs/LOCAL_MODELS.en.md).

English-first documentation and contributor workflow are part of the open repository. The
default speech models are Mandarin-first; the architecture is model-agnostic, and additional
language adapters remain an explicit roadmap item rather than an accuracy claim.

## Architecture

```mermaid
flowchart LR
    U[Browser · React + TS] -->|WebRTC meeting| LK[Self-hosted LiveKit]
    U -->|upload / microphone| API[FastAPI backend]
    LK -->|audio in the browser| ASR[FunASR local recognition]
    API --> ASR
    ASR --> LEX[Domain lexicon correction]
    LEX --> CLS[Scene detection<br/>evidence gate + model review]
    CLS --> LLM[Local LLM via Ollama<br/>OpenAI-compatible API]
    LLM --> MIN[Structured minutes + quality review]
    MIN --> EXP[DOCX / PDF export]
    MIN --> DB[(Local history · SQLite)]
    DB --> QA[Ask JKinco · local meeting Q&A]
```

Module boundaries, data flow, and the security perimeter: [docs/ARCHITECTURE.en.md](docs/ARCHITECTURE.en.md).

## Core capabilities

### Real-time meetings

- Self-hosted LiveKit for audio, video, screen sharing, and meeting chat.
- Local streaming captions with reconnect handling for long meetings.
- Meeting controls enforced by server-side grants, not hidden buttons.
- End the room, and the final transcript feeds the same local minutes pipeline.

### Evidence-gated scene detection

Five meeting types are distinguished by the roles, business actions, and deliverables that
appear in the transcript:

- **Construction site review** — multiple accountable parties (contractor, supervisor,
  owner) plus a production-control agenda plus periodic review. The formal template is used
  only when that evidence chain is present.
- **General minutes** — management reports, business reviews, project weeklies. No fixed
  template; the model organizes the clearest structure for the source material.
- **Personal assistant** — personal notes, retrospectives, follow-ups.
- **Interview record** — candidate evaluation, competency assessment, hiring recommendation.
- **Customer visit** — customer needs, discussion points, ownership.

The rule-based gate runs first, and it wins: **the model cannot classify a meeting as a
construction review on the strength of generic words like "project", "schedule", "quality".**

### Long recordings

- Duration gate with decompression-bomb protection, measured by decoded duration rather than
  file size.
- Long transcripts are chunked and extracted in parallel; gaps are recorded rather than
  silently discarding the whole run.
- Job queue with per-user quotas and retries. A dropped connection does not lose work already
  transcribed.

### Export and delivery

- Six templates: construction, general, personal, interview, customer visit, and custom
  uploads.
- DOCX and PDF export preserving the original layout, with collision-proof filenames.
- Optional DingTalk bot delivery with request signing. Disabled unless configured.

## Privacy and security

- No cloud model calls, no API keys, no telemetry.
- Audio, transcripts, minutes, and human edits are stored only on the local machine.
- Login rate limiting, CAPTCHA, signed sessions, CSP and security headers, audit log.
- Template upload hardening: zip bombs, path traversal, and XML entity attacks.
- CI enforces the open-edition boundary and scans the full Git history with redacted output;
  the build fails if cloud-model references or credentials appear in either place.

## Testing

```bash
python -m pytest tests-v2                    # 965 tests
python scripts/smoke_test.py                 # offline smoke: scene routing + every export
python scripts/check_open_source_hygiene.py  # open-edition boundary scan
```

GitHub Actions runs the boundary scan, the backend tests, and the frontend build on every
push.

## FAQ

**Do I need a GPU?**
No. The FunASR Chinese model and a quantized 7B model run on a 16 GB CPU machine — just
slower. An NVIDIA or Apple Silicon GPU speeds things up noticeably.

**What if the model download fails?**
Only the first download needs network access; everything afterwards is offline. Point
`JKINCO_ASR_MODEL_DIR` at an existing model directory to skip the download entirely.

**Can I look around before downloading models?**
Set `JKINCO_DEMO_DATA=1` and start. Two example minutes (a construction review and a customer
visit) appear in history, and export, search, and templates all work without any model.

**Is there live captioning?**
Yes. `docker compose up` starts a self-hosted LiveKit server and enables local streaming
captions by default. The first use downloads `paraformer-zh-streaming`; no meeting audio is
sent to a cloud ASR provider. Set `JKINCO_REALTIME_LOCAL_ASR=0` to disable it.

**Can I use it commercially?**
Yes, under the MIT License. Please keep the copyright notice.

**Does it work with languages other than Chinese?**
The repository, documentation, and contributor workflow are English-first. The default ASR
model and domain lexicon are tuned for Mandarin meetings. The architecture is model-agnostic,
so an English or multilingual ASR adapter can be contributed and benchmarked; built-in
multilingual accuracy is not claimed until it is measured.

## Roadmap

- [x] Upload → local transcription → scene detection → minutes → export
- [x] Self-hosted real-time meeting, live local captions, screen sharing, and chat
- [x] History knowledge base and local meeting Q&A
- [ ] Speaker diarization and role attribution
- [ ] WebDAV / cloud-drive backup
- [ ] Desktop installers (macOS / Windows)
- [ ] Additional speech-model adapters (Cantonese / multilingual English / Japanese)

Full plan: [docs/ROADMAP.en.md](docs/ROADMAP.en.md).

## Contributing

Issues, pull requests, and localization help are all welcome. Please read
[CONTRIBUTING.md](CONTRIBUTING.md) first, and run the boundary scan and the test suite before
submitting.

[SUPPORT.md](SUPPORT.md) states what the maintainer commits to — first response within five
working days, security acknowledgement within 72 hours, pull requests reviewed within seven —
and, just as importantly, what is out of scope.

## License

[MIT](LICENSE) © 2026 JKinco

---

---

<sub>
<b>JKinco</b> — local-first tools for work whose data cannot leave the building ·
<a href="https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open">Listen</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/jkinco-slides">Slides</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/JKinco-Skills-Lab">Skills Lab</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/personal-life-hub">Life Hub</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/jkinco-tools">Tools</a>
</sub>

## Support this project

If JKinco Listen saved you from typing up one more set of meeting minutes, please **Star** it ⭐.

[![Star History Chart](https://api.star-history.com/svg?repos=wenxuanzhang1209-cyber/jkinco-listen-open&type=Date)](https://star-history.com/#wenxuanzhang1209-cyber/jkinco-listen-open&Date)
