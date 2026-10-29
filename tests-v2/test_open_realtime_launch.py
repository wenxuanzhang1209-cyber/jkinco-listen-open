"""Contracts for the public real-time baseline.

The real-time meeting was already implemented, but the Docker launcher did not start LiveKit,
did not pass the local-ASR switch into the app, and the meeting dialog still said the feature
was unavailable. These checks keep the product entry point aligned with the code.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app


ROOT = Path(__file__).resolve().parent.parent


def test_compose_starts_livekit_and_enables_local_realtime_asr():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "JKINCO_REALTIME_LOCAL_ASR: ${JKINCO_REALTIME_LOCAL_ASR:-1}" in compose
    assert "LIVEKIT_API_URL: ${LIVEKIT_API_URL:-http://livekit:7880}" in compose
    assert "\n  livekit:\n" in compose
    assert '"${LIVEKIT_HOST_BIND:-127.0.0.1}:7880:7880"' in compose


def test_health_reports_the_release_and_local_streaming_state():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    with patch("backend.main.REALTIME_LOCAL_ASR", True):
        with TestClient(app) as client:
            health = client.get("/api/health").json()

    assert health["version"] == version
    assert health["realtime_asr"] == "local_streaming"


def test_frontend_package_uses_the_same_release_identifier():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))

    assert package["version"] == version


def test_public_surfaces_do_not_claim_realtime_is_unavailable():
    paths = (
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "docs" / "OPEN_EDITION.en.md",
        ROOT / "docs" / "OPEN_EDITION.md",
        ROOT / "frontend" / "src" / "MeetingModule.tsx",
    )
    stale_claims = ("开源版暂未开放实时字幕", "does not include cloud-based real-time streaming captions")

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for claim in stale_claims:
            assert claim not in text, f"{path.relative_to(ROOT)} still says: {claim}"
