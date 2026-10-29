# Roadmap

*[中文版 / Chinese version](ROADMAP.md)*

## v0.2 (current)

- [x] Upload → local transcription → scene detection → minutes → export, end to end
- [x] Self-hosted real-time meeting with LiveKit
- [x] Local streaming captions, screen sharing, chat, and post-meeting minutes
- [x] Five scenes: construction review / general minutes / personal notes / interview record / customer visit
- [x] DOCX and PDF export with original layout, plus custom templates
- [x] Local history knowledge base and "Ask JKinco" Q&A
- [x] WorkBuddy connection for authorized meeting-context handoff
- [x] One-command Docker deploy with CI (boundary scan / 977 tests / frontend build)

## v0.3

- [ ] Speaker diarization and role attribution
- [ ] Automatic recording backup (WebDAV / SMB)
- [ ] Desktop installers (macOS / Windows)

## v0.4

- [ ] Additional streaming ASR adapters (Cantonese / multilingual English / Japanese)
- [ ] Shared team history (optional LAN mode)
- [ ] In-browser offline mode (WebAssembly ASR experiment)
- [ ] Plugin marketplace for scene templates and domain lexicons

## Longer term

- A construction-domain knowledge base
- Personal Agent action orchestration with ticketing systems and calendars
- MCP/API interoperability for additional office and agent ecosystems
- Large-scale self-hosted benchmarks (10-hour recordings, 1000-participant meetings)

## Where help is most useful

Speaker diarization and additional streaming ASR adapters are the highest-leverage
contributions. Security review, exported-document fixtures, and reproducible model benchmarks
are equally welcome.
