# About the Open Edition

*[中文版 / Chinese version](OPEN_EDITION.md)*

> Want the ready-to-use mobile product instead?
> [Open 筑听](https://jkincolisten.cloud) ·
> [iOS / Android / HarmonyOS downloads](https://jkincolisten.cloud/download/ios)

This repository is the **Open Edition** of JKinco Listen:

- Runs 100% locally, with no API key required.
- Complete loop: live meeting / recording → local transcription → scene detection → minutes → export.
- Models, templates, and history data all belong to whoever runs it.

## Scope

- This repository contains the Open Edition only.
- The Open Edition includes a self-hosted LiveKit meeting room and local streaming captions.
  It does not depend on a cloud ASR provider or a hosted meeting service.
- It is independent of any commercial or internal edition, sharing neither code nor configuration.
- The CI boundary scan guarantees that cloud model traces, secrets, and production
  infrastructure details never enter this repository.

## Why the split exists

A privacy-first tool loses its point if the open version quietly depends on a hosted service.
Keeping the editions separate — and enforcing that separation in CI rather than by discipline —
is what makes the claim "your audio never leaves your machine" checkable by a stranger rather
than something you have to take on trust.

## Want to help?

See [CONTRIBUTING.md](../CONTRIBUTING.md) and [ROADMAP.en.md](ROADMAP.en.md).
