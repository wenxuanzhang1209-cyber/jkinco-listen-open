# JKinco Listen (Open Edition)

*[简体中文](index.md)*

**A local-first AI meeting-minutes workbench.** Recording → transcript → scene detection →
structured minutes → DOCX/PDF, entirely on your own machine. No API key, no cloud call.

<video src="demo.mp4" controls muted loop playsinline width="100%" poster="demo-minutes.png"
       style="border-radius:12px;box-shadow:0 8px 28px rgba(15,23,42,.18);margin:20px 0">
  Your browser cannot play embedded video — <a href="demo.mp4">download the demo</a>.
</video>

---

## The constraint this is built for

Some meetings cannot be uploaded. Not "should not" — **cannot**.

A construction site review decides who is accountable for a defect. An interview record holds
a candidate's personal data. A client visit contains pricing. In regulated industries these
recordings are part of a legal archive, and sending them to a third-party server is not a
policy preference you can toggle.

Cloud meeting assistants are architecturally unable to serve that case. Their value comes from
models running on their infrastructure, so the audio has to get there. That is not a gap in
their product; it is what their product *is*.

So the people in those meetings do the only thing left: **they type the notes by hand.**

This project is for them. Everything runs on the machine in the room.

## What comes out

Summary, agenda flow, conclusions, and action items — with the full recording-to-export
pipeline on the right:

![Structured minutes](demo-minutes.png)

## Measured, not claimed

Ten noisy Chinese meeting clips, 36 terms a general model tends to mishear:

| | Terms recognised |
|---|---|
| Local ASR alone | 31 / 36 |
| **+ domain correction** | **36 / 36** |

Reproduce it in one command — see **[the benchmark page](BENCHMARK.md)** for the method, the
per-clip results, and the three bugs writing that benchmark found in this project's own code.

## Quick start

```bash
git clone https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open.git
cd jkinco-listen-open
cp .env.example .env
docker compose up -d --build
```

Open <http://localhost:8080>.

> ⚠️ **Change the password before exposing this to a network.** `.env.example` ships with
> `JKINCO_AUTH=admin:123456` so a local trial works immediately, and the container binds
> `0.0.0.0` — anyone who can reach the port can sign in with it.

Want to see the interface before downloading 2 GB of models? Set `JKINCO_DEMO_DATA=1` and two
example meetings appear, with export, search, and templates all usable.

## How it compares

|  | JKinco Listen | Cloud note-takers | Raw Whisper |
|---|---|---|---|
| Audio leaves your machine | **Never** | Yes | Never |
| API key required | **No** | Yes | No |
| Output | **Formatted DOCX/PDF** | Transcript + summary | Raw transcript |
| Meeting-type awareness | **5 scenes, evidence-gated** | One format | None |

If you only need a transcript, use Whisper directly — it is simpler. This exists for the part
*after* the transcript: turning it into a document somebody can file.

## Under the hood

- 🎙 **Local speech recognition** — FunASR `paraformer-zh`
- 🧠 **Local LLM** — Ollama (`qwen2.5:7b-instruct` by default) or any OpenAI-compatible endpoint
- 🏗 **Evidence-gated scene detection** — rules decide first; the model may downgrade a
  classification but never promote one, because the wrong template produces a document
  asserting the wrong legal responsibility
- 📄 **Original-layout DOCX/PDF export**
- 🔐 **No API key, no cloud, no telemetry** — enforced in CI, not asserted in a README
- 🐳 **One-command Docker deploy**

## Documentation

- [Project home (README)](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open)
- [Benchmark](BENCHMARK.md)
- [Architecture](ARCHITECTURE.en.md)
- [Model guide](LOCAL_MODELS.en.md)
- [Roadmap](ROADMAP.en.md)
- [About the Open Edition](OPEN_EDITION.en.md)

## Community

- [Discussions](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/discussions)
- [Issues](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/issues)

If this saved you from typing up one more set of meeting minutes, please ⭐ the repository.

MIT License © 2026 JKinco

---

<sub>
<b>JKinco</b> — local-first tools for work whose data cannot leave the building ·
<a href="https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open">Listen</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/jkinco-slides">Slides</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/JKinco-Skills-Lab">Skills Lab</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/personal-life-hub">Life Hub</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/jkinco-tools">Tools</a>
</sub>
