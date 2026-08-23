"""
check_models.py

Two model-name guesses in a row have turned out wrong (gemini-2.5-flash's
free tier was cut to ~20/day without me knowing, then gemini-2.5-flash-lite
turned out unavailable to newer accounts entirely). Model names and free-tier
availability are clearly changing faster than search results can keep up
with. Instead of guessing a third time, this asks YOUR account directly what
it can actually use right now - the only source that can't be stale.

Usage:
    python check_models.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", encoding="utf-8-sig")

from google import genai

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

print("Models available to this API key:\n")
for m in client.models.list():
    actions = getattr(m, "supported_actions", None)
    print(f"  {m.name}")
    if actions:
        print(f"      supports: {actions}")
