# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

本项目的所有重要变更都记录在此。格式遵循 Keep a Changelog，版本号遵循语义化版本。

## [Unreleased]

### Changed

- English is now the repository's default language. `README.md` is English; the Chinese
  version moved to `README.zh-CN.md`. Contributor-facing files (`CONTRIBUTING.md`,
  `SECURITY.md`, issue templates, `.env.example`) are bilingual, English first.
- English translations added for the architecture, model guide, roadmap, and open-edition
  documents.

### Added

- Contract tests that pin CI dependencies to runtime dependencies (`tests-v2/test_ci_requirements_match_runtime.py`).
- Demo recording rebuilt from a live instance: a 25-second walkthrough with captions covering
  sign-in → workspace → scenes → history → structured minutes, plus a full-resolution MP4 on
  the documentation site.
- `docs/demo-minutes.png` — a screenshot of the actual output document, which the README
  previously never showed.
- Comparison table against cloud note-takers and raw Whisper, including the honest case for
  *not* using this project.
- CI status badge.

### Security

- **The secret scanner missed OpenAI, Anthropic, Google, and Slack keys.** Its pattern
  required an underscore after the prefix (`sk_`), so `sk-proj-…`, `sk-…`, `sk-ant-…`,
  `AIza…`, and `xoxb-…` all passed straight through — two of seven common formats were
  caught. The scanner is the repository's only automated security gate, and it had no tests
  of its own. It now covers twelve formats, reports line numbers, scans line by line instead
  of stopping at the first hit, and supports a line-scoped `# hygiene:allow-secret` marker so
  that redaction tests can keep realistic fixtures without blinding the scanner to the rest
  of the file. 19 tests now cover the scanner itself.
- The scanner identifies virtualenvs by `pyvenv.cfg` rather than by directory name. Listing
  `.venv` and `.venv-test` meant any other local environment got scanned, and `certifi`'s
  bundled `cacert.pem` inside it produced a false "secret file committed" alarm. A scanner
  that cries wolf trains people to ignore it.
- Explicit warning about the default `JKINCO_AUTH=admin:123456` credential in `SECURITY.md`,
  `.env.example`, the README, and the documentation site. The server binds `0.0.0.0`, so the
  default is safe only on localhost.

### Fixed

- CI installed 626 MB it never used. `requirements-ci.txt` claimed to exclude FunASR and
  torch while its first line pulled in all of `requirements.txt`. The test suite never imports
  FunASR — `jkinco_asr.py` imports it lazily inside a function — so the CI install now drops
  from 774 MB / 109 packages to 148 MB / 52, with the full suite still passing. Five contract
  tests keep the two manifests from drifting apart, which is the risk that change introduces.
- Corrected stale test counts in the documentation (888 / 894 → 895, the measured value).
- `.gitignore` now covers virtualenvs under any name, not just `.venv/`.

## [0.1.1] - 2026-08-21

### Added

- **Demo data mode** (`JKINCO_DEMO_DATA=1`) — seeds two example minutes so the interface,
  export, and history search are usable before downloading any model.
- **Experimental local live captions** (`JKINCO_REALTIME_LOCAL_ASR=1`) — meeting and recorder
  captions run through a local `paraformer-zh-streaming` model. Data still never leaves the
  machine.
- **One-command install scripts** — `bash scripts/install.sh && bash scripts/start.sh` for
  people who would rather not use Docker.
- **Documentation site** at <https://wenxuanzhang1209-cyber.github.io/jkinco-listen-open/>.
- GitHub Discussions, and a star-milestone workflow.

### Changed

- Test suite grown from 888 to 894 cases. CI covers the boundary scan, backend tests,
  frontend build, and Docker image build.

## [0.1.0] - 2026-08-21

Initial public release of the Open Edition.

### Added

- End-to-end local pipeline: recording → transcription → scene detection → structured
  minutes → DOCX/PDF export. No API key, no cloud call.
- Local FunASR Chinese speech recognition plus a local LLM through Ollama or any
  OpenAI-compatible endpoint.
- Five meeting scenes: construction review, general minutes, personal notes, interview
  record, customer visit.
- **Evidence-gated scene detection** — rules decide first and the model may only review;
  it cannot promote a meeting into the construction template on generic words alone.
- Original-layout DOCX and PDF export, plus custom template upload.
- Local history knowledge base with meeting Q&A.
- One-command Docker deployment.
- CI: open-edition boundary scan, 891 tests, frontend build, Docker image build.

[Unreleased]: https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/releases/tag/v0.1.1
[0.1.0]: https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/releases/tag/v0.1.0
