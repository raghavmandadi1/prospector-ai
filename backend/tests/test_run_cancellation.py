"""
End-to-end smoke test for run cancellation and telemetry.

This is deliberately a plain script, not pytest, because it needs a live
uvicorn process to prove the thing it claims — that closing the HTTP stream
actually stops the agents, rather than leaving them burning tokens until they
finish.

(The original reason given here was "the repo has no test infrastructure yet".
That is no longer true — there are 195 pytest tests. The live-uvicorn
requirement is the reason it stays a script.)

NOT COLLECTED BY PYTEST, for the same reason as test_run_telemetry.py: the work
is in main(), so `pytest backend/tests` reports "no tests collected" here and a
green suite says nothing about this file.

Run:  python3 backend/tests/test_run_cancellation.py
Needs: fastapi uvicorn httpx anthropic shapely pyproj pydantic-settings
"""
import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DEV_MODE", "true")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")

import anthropic  # noqa: E402

# --- Fake Anthropic client -------------------------------------------------
# Counts calls and holds each one open long enough that we can cancel mid-run.

CALLS = {"started": 0, "finished": 0}
CALL_SECONDS = 20


class _FakeUsage:
    input_tokens = 12_345
    output_tokens = 3_210
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeBlock:
    text = '[{"cell_id":"c0_r0","score":0.5,"confidence":0.6,"evidence":["x"],"data_sources_used":["y"]}]'


class _FakeMessage:
    content = [_FakeBlock()]
    usage = _FakeUsage()
    stop_reason = "end_turn"


class _FakeMessages:
    async def create(self, **kwargs):
        CALLS["started"] += 1
        await asyncio.sleep(CALL_SECONDS)
        CALLS["finished"] += 1
        return _FakeMessage()


class FakeAsyncAnthropic:
    def __init__(self, *a, **kw):
        self.messages = _FakeMessages()


anthropic.AsyncAnthropic = FakeAsyncAnthropic

from fastapi import FastAPI  # noqa: E402
import uvicorn  # noqa: E402
import httpx  # noqa: E402

from app.api import analysis_dev  # noqa: E402

app = FastAPI()
app.include_router(analysis_dev.router, prefix="/api/v1")

AOI = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [-118.80, 48.60], [-118.60, 48.60],
                [-118.60, 48.75], [-118.80, 48.75], [-118.80, 48.60],
            ]],
        },
    }],
}

BODY = {
    "aoi_geojson": AOI,
    "target_mineral": "gold",
    "config": {"resolution_m": 5000, "enabled_agents": ["lithology", "structure"]},
    "anthropic_api_key": "sk-test",
}


def serve(port: int):
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> int:
    port = 8123
    threading.Thread(target=serve, args=(port,), daemon=True).start()
    time.sleep(2.5)

    events = []
    # trust_env=False: never route a localhost call through a system proxy
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        with client.stream(
            "POST", f"http://127.0.0.1:{port}/api/v1/analysis/jobs", json=BODY
        ) as resp:
            assert resp.status_code == 200, resp.status_code
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    events.append(ev)
                    print(f"  event: {ev['event']}", ev.get("agent_id", ""))
                    # Stop as soon as agents are mid-flight in an LLM call.
                    if ev["event"] == "batch_started":
                        print("  --> closing stream (simulating Stop)")
                        break
        # exiting the `with` closes the connection == AbortController.abort()

    started_at_cancel = CALLS["started"]
    print(f"\n  LLM calls in flight at cancel: {started_at_cancel}")

    # The server polls for disconnect every DISCONNECT_POLL_SECONDS. Wait past
    # that but well short of CALL_SECONDS, so any call that completes proves
    # the cancellation did not land.
    time.sleep(6)

    ok = True

    if CALLS["finished"] != 0:
        print(f"  FAIL: {CALLS['finished']} LLM calls completed after cancel")
        ok = False
    else:
        print("  PASS: no LLM call completed after cancel")

    if CALLS["started"] > started_at_cancel:
        print(f"  FAIL: {CALLS['started'] - started_at_cancel} new LLM calls started after cancel")
        ok = False
    else:
        print("  PASS: no new LLM calls started after cancel")

    names = [e["event"] for e in events]
    for required in ("started", "spatial_context", "agent_grounding", "batch_started"):
        if required in names:
            print(f"  PASS: emitted '{required}'")
        else:
            print(f"  FAIL: never emitted '{required}'")
            ok = False

    grounding = [e for e in events if e["event"] == "agent_grounding"]
    lith = next((e for e in grounding if e["agent_id"] == "lithology"), None)
    struct = next((e for e in grounding if e["agent_id"] == "structure"), None)
    if lith and lith.get("knowledge_file") == "lithology/gold.md":
        print("  PASS: lithology reported as grounded")
    else:
        print(f"  FAIL: lithology grounding wrong: {lith}")
        ok = False
    # Inverted 2026-08-13: this used to assert knowledge_file is None as a
    # tripwire on Known Gap #1. The gap closed, so the assertion now pins the
    # fixed state instead of the broken one.
    if struct and struct.get("knowledge_file") == "structure/gold.md":
        print("  PASS: structure reported as grounded")
    else:
        print(f"  FAIL: structure grounding wrong: {struct}")
        ok = False

    # Also inverted. This used to treat a spatial-context error as the PASS
    # case, because the PostGIS query was the only source and it always died on
    # the dev path (Known Gap #2). Local files now serve context in both modes,
    # and `_error` is set only when NO source produced anything — so an error
    # here is a real failure, not the expected dev state.
    ctx = next((e for e in events if e["event"] == "spatial_context"), None)
    if ctx is None:
        print("  FAIL: no spatial_context event emitted")
        ok = False
    elif ctx.get("error"):
        print(f"  FAIL: no spatial source produced anything: {ctx['error'][:80]}")
        ok = False
    else:
        print(
            "  PASS: spatial context built "
            f"(sources={ctx.get('sources')}, cells_with_facts={ctx.get('cells_with_facts')})"
        )

    print("\n" + ("ALL CHECKS PASSED" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
