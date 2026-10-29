# JKinco Listen v0.2.0

## Live meetings are now the default product path

**Live captions in the room. Filed minutes on your machine.**

`docker compose up` now starts the web app, local LLM, local streaming ASR, and a self-hosted
LiveKit media server. Open the meeting lobby, create a room, and captions appear locally while
people speak. End the meeting and its final transcript feeds the same structured minutes and
DOCX/PDF pipeline.

### What changed

- Self-hosted LiveKit meeting rooms: audio, video, screen sharing, and chat.
- Local streaming captions enabled by default; no cloud ASR dependency.
- Prominent links from the open repository to the live production app, its iOS, Android, and
  HarmonyOS downloads, and the 筑听 Z1 capture hardware.
- A production tour captured from the public download page, plus separate live-capture,
  automatic-minutes, and Sona Q&A clips.
- A clear Production App / Open Edition boundary, so “ready to use” and “runs on your own
  infrastructure” do not get conflated.
- The official JKinco Listen product lockup in the README and the production App icon in the
  social preview.
- Meeting controls enforced by server-side LiveKit grants.
- One release identifier shared by the API, health endpoint, frontend, and Docker image.
- English-first documentation and contributor workflow for a global audience.
- General-market positioning: management reviews, customer calls, project weeklies, interviews,
  training, research, and regulated meetings.
- Engineering supervision is documented as the evidence-heavy stress test that hardened the
  generic pipeline, not as a restriction on who can use it.

## Try the finished product

- Production app: <https://jkincolisten.cloud>
- Product tour + iOS / Android / HarmonyOS downloads:
  <https://jkincolisten.cloud/download/ios>
- 筑听 Z1 capture card: <https://jkincolisten.cloud/download/ios>

The Open Edition below remains fully self-hosted, MIT licensed, and independent of the
production service.

### Start

```bash
git clone https://github.com/wenxuanzhang1209-cyber/meetdo.git
cd meetdo
cp .env.example .env
docker compose up -d --build
```

Open <http://localhost:8080>. The first caption downloads the local streaming ASR model.

The bundled web login and LiveKit development credentials are for loopback-only trials.
Replace both, configure a reachable `LIVEKIT_PUBLIC_URL`, and terminate TLS before exposing the
service to a network.
