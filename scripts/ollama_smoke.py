"""
Phase 9 — Step 1: Ollama HTTP smoke test.

Proves the local Ollama server is reachable on port 11434 and returns
generated text. No FastAPI, no RAG, no schema, no SQL — just the
smallest possible round-trip to the model.

Run from repo root:
    python scripts/ollama_smoke.py

Exit codes:
    0  Ollama responded with non-empty text
    1  Ollama unreachable, or returned an error / empty response
"""

from __future__ import annotations

import json
import sys
import time

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma3:latest"
PROMPT = "Reply with exactly the word: pong"
TIMEOUT = 120  # seconds; first call after a cold start loads the model into RAM


def main() -> int:
    payload = {
        "model": MODEL,
        "prompt": PROMPT,
        "stream": False,          # one-shot JSON response, not SSE
        "options": {
            "temperature": 0.0,   # deterministic — this is a smoke test, not a chat
            "num_predict": 16,    # cap generation; we only need one word
        },
    }

    print(f"POST {OLLAMA_URL}")
    print(f"  model  = {MODEL}")
    print(f"  prompt = {PROMPT!r}")
    t0 = time.perf_counter()

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
    except requests.ConnectionError:
        print()
        print("FAIL: could not connect to Ollama at localhost:11434")
        print("Fix:  open a terminal and run `ollama serve`,")
        print("      or launch the Ollama desktop app so the background server starts.")
        return 1
    except requests.Timeout:
        print()
        print(f"FAIL: Ollama did not respond within {TIMEOUT}s")
        print("Fix:  model may still be loading. Run `ollama run gemma3` once in a")
        print("      terminal to warm it, then retry this script.")
        return 1

    elapsed = time.perf_counter() - t0

    if resp.status_code != 200:
        print()
        print(f"FAIL: HTTP {resp.status_code}")
        print(f"Body: {resp.text[:500]}")
        return 1

    try:
        data = resp.json()
    except ValueError:
        print()
        print("FAIL: response was not JSON")
        print(f"Body: {resp.text[:500]}")
        return 1

    text = (data.get("response") or "").strip()
    if not text:
        print()
        print("FAIL: Ollama returned an empty response")
        print(f"Full payload: {json.dumps(data, indent=2)[:800]}")
        return 1

    print()
    print("PASS")
    print(f"  response    = {text!r}")
    print(f"  elapsed     = {elapsed:.2f}s")
    print(f"  total_dur   = {data.get('total_duration', 0) / 1e9:.2f}s (model-reported)")
    print(f"  eval_count  = {data.get('eval_count', '?')} tokens generated")
    return 0


if __name__ == "__main__":
    sys.exit(main())