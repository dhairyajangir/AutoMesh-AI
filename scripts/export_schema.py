"""Generate the TypeScript contract from this OpenAPI snapshot."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from automesh.api import app

if __name__ == "__main__":
    (ROOT / "frontend" / "openapi.json").write_text(
        json.dumps(app.openapi(), indent=2), encoding="utf-8"
    )
