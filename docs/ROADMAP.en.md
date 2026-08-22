# Roadmap

*[中文版 / Chinese version](ROADMAP.md)*

## v0.1 (current)

- [x] Upload → local transcription → scene detection → minutes → export, end to end
- [x] Five scenes: construction review / general minutes / personal notes / interview record / customer visit
- [x] DOCX and PDF export with original layout, plus custom templates
- [x] Local history knowledge base and "Ask JKinco" Q&A
- [x] One-command Docker deploy with CI (boundary scan / 895 tests / frontend build)

## v0.2

- [x] Local streaming live captions (experimental: `JKINCO_REALTIME_LOCAL_ASR=1`, FunASR streaming)
- [ ] Speaker diarization and role attribution
- [ ] Automatic recording backup (WebDAV / SMB)
- [ ] Desktop installers (macOS / Windows)

## v0.3

- [ ] Cantonese / English / Japanese recognition
- [ ] Shared team history (optional LAN mode)
- [ ] In-browser offline mode (WebAssembly ASR experiment)
- [ ] Plugin marketplace for scene templates and domain lexicons

## Longer term

- A construction-supervision domain knowledge base
- Action items that flow into ticketing systems and calendars
- Large-scale self-hosted benchmarks (10-hour recordings, 1000-participant meetings)

## Where help is most useful

Speaker diarization and desktop packaging are the two items most often asked for and least
started. Documentation translation is also open — the project began in Chinese, and the
English side is younger than the Chinese side.
