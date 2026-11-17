"""Verify Gemini configuration (offline) and, with --live, that the model exists for your key.

Run before any customer or even realistic data is processed. NOT run in the build sandbox
(no access to Google APIs) - see docs/STATUS.md.
"""

import asyncio
import sys

from app.config import get_settings
from construction_variation_agent.model_config import check_model_configuration, verify_model_live


def main() -> int:
    s = get_settings()
    key = s.gemini_api_key.get_secret_value() if s.gemini_api_key else None
    res = check_model_configuration(s.gemini_model, key, s.google_genai_use_vertexai)
    print("offline check:", res)
    if not res["ok"]:
        return 1
    if "--live" in sys.argv:
        live = asyncio.run(verify_model_live(s.gemini_model, key, s.google_genai_use_vertexai))
        print("live check:", live)
        return 0 if live["ok"] else 1
    print("(offline only; pass --live to call the Gemini API)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
