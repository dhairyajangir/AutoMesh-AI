"""Verify the real model-download failure path with network calls blocked."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests
from PIL import Image

from automesh.store import Store
from automesh.worker import photo_mask


def deny_network(*args, **kwargs):
    raise requests.ConnectionError("Network intentionally blocked by validation")


requests.sessions.Session.request = deny_network
store = Store(ROOT / ".local/failed-download-test")
try:
    photo_mask(Image.open(ROOT / "data/examples/photos/side.jpg").convert("RGB"), store, print)
except ValueError as exc:
    message = str(exc)
    assert "could not load" in message and "Paint mask" in message, message
    (ROOT / "validation/download-failure.json").write_text(
        json.dumps(
            {
                "passed": True,
                "error": message,
                "method": "Real photo_mask with empty model cache and blocked requests",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(message)
else:
    raise AssertionError("Expected failed model download")
