<div align="center">

<img src="assets/jkinco_logo_cropped.png" width="140" alt="JKinco Listen" />

# JKinco Listen (Open Edition)

**Local-first AI meeting minutes workbench**

Upload a recording → get a scene-aware, structured meeting minutes → export to
Word/PDF. The entire pipeline runs **100% locally**: no API keys, no cloud
calls, and your audio and minutes **never leave your machine**.

[中文](README.md) · [Architecture](docs/ARCHITECTURE.md) · [Model Guide](docs/LOCAL_MODELS.md) · [Roadmap](docs/ROADMAP.md) · [Growth](docs/GROWTH.md)

![MIT](https://img.shields.io/badge/license-MIT-green.svg)
![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)
![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688.svg)
![React](https://img.shields.io/badge/Frontend-React%20%2B%20TypeScript-61dafb.svg)
![Tests](https://img.shields.io/badge/tests-888%20passed-brightgreen.svg)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

</div>

---

## Why

Meeting transcription usually means uploading sensitive audio to a third party.
For engineering supervision, client visits, interviews and personal notes, that
is often a deal-breaker. JKinco Listen (Open Edition) keeps everything on-device:

- **Local speech recognition** — FunASR `paraformer-zh` with VAD and punctuation
  restoration, plus an engineering-domain correction lexicon.
- **Local LLM** — scene classification, minutes generation, quality review and
  meeting Q&A via any OpenAI-compatible local model (Ollama by default).
- **Template engine** — five scene types (site meeting, general meeting, personal
  notes, interview, customer visit) with original-layout DOCX/PDF export.
- **Local history** — transcripts, minutes and reviewed versions stay in a local
  database, searchable and exportable.

## Quick start (Docker)

```bash
git clone https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open.git
cd jkinco-listen-open
cp .env.example .env
docker compose up -d --build
```

Open <http://localhost:8080> and sign in with `admin / 123456`.

The first start downloads the local ASR model (≈1–2 GB) and the default LLM
(`qwen2.5:7b-instruct`). After that it works fully offline.

## Manual setup

```bash
brew install ollama && ollama pull qwen2.5:7b-instruct   # or Linux installer
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cd frontend && npm ci && npm run build && cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

## Features

- Local Chinese ASR + local LLM, zero cloud calls
- Rule-gated scene recognition (engineering evidence cannot be overridden by the model)
- Long-audio chunking with resilient partial failure handling
- Original-layout DOCX/PDF export for five scene types
- Local meeting history, search and “ask your meetings” Q&A
- Optional signed DingTalk bot push (disabled by default)
- 888 automated tests, rate limiting, security headers, audit logs
- Docker one-command deployment

## Privacy

No API keys. No cloud inference. No telemetry. Audio and text never leave your
machine. A CI red-line scanner blocks cloud-service traces and secrets from ever
entering this repository.

## Testing

```bash
python -m pytest tests-v2 -q
python scripts/smoke_test.py
python scripts/check_open_source_hygiene.py
```

## Roadmap

- [x] Recording → transcript → scene → minutes → export
- [x] Local history and meeting Q&A
- [ ] Streaming realtime subtitles
- [ ] Speaker diarization
- [ ] Desktop installers (macOS / Windows)
- [ ] More languages

See [docs/ROADMAP.md](docs/ROADMAP.md).

## License

[MIT](LICENSE) © 2026 JKinco

---

⭐ If this saves you from handwriting another meeting summary, star the repo.

[![Star History Chart](https://api.star-history.com/svg?repos=wenxuanzhang1209-cyber/jkinco-listen-open&type=Date)](https://star-history.com/#wenxuanzhang1209-cyber/jkinco-listen-open&Date)
