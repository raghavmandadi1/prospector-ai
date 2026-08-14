"""
Happy-path smoke test: a full run completes and emits usable telemetry.

Companion to test_run_cancellation.py. Asserts the token ledger adds up, that
batch parse health is reported honestly, and that a truncated response is
flagged rather than silently repaired.

NOT COLLECTED BY PYTEST. There is no test function here — the work happens in
main() behind `if __name__ == "__main__"`, so `pytest backend/tests` reports
"no tests collected" for this file and a green suite says nothing about it.
It needs a live uvicorn, so it stays hand-run. Verify with:

    pytest backend/tests/test_run_telemetry.py --collect-only -q

Run:  python3 backend/tests/test_run_telemetry.py
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

IN_TOK, OUT_TOK = 10_000, 2_000
CALLS = {"n": 0}


class _Usage:
    input_tokens = IN_TOK
    output_tokens = OUT_TOK
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Block:
    def __init__(self, text):
        self.text = text


class _Message:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.usage = _Usage()
        self.stop_reason = stop_reason


def _cells_json(cell_ids):
    return json.dumps([
        {"cell_id": c, "score": 0.5, "confidence": 0.7,
         "evidence": ["fake"], "data_sources_used": ["fake_source"]}
        for c in cell_ids
    ])


class _Messages:
    async def create(self, **kwargs):
        CALLS["n"] += 1
        # Snapshot before awaiting: up to MAX_CONCURRENT_BATCHES calls are in
        # flight per agent, so reading the counter after the await would race.
        n = CALLS["n"]
        await asyncio.sleep(0.05)
        # Recover the cell ids the prompt asked about so the parser maps them.
        import re
        ids = re.findall(r"\b(c\d+_r\d+)\b", kwargs["messages"][0]["content"])
        ids = list(dict.fromkeys(ids))
        # One call returns a half-answer with a max_tokens stop reason — this
        # is the truncation case that caused the all-zero-scores bug.
        if n == 2:
            return _Message(_cells_json(ids[: max(1, len(ids) // 2)]), stop_reason="max_tokens")
        return _Message(_cells_json(ids))


class FakeAsyncAnthropic:
    def __init__(self, *a, **kw):
        self.messages = _Messages()


anthropic.AsyncAnthropic = FakeAsyncAnthropic

from fastapi import FastAPI  # noqa: E402
import uvicorn  # noqa: E402
import httpx  # noqa: E402

from app.api import analysis_dev  # noqa: E402

app = FastAPI()
app.include_router(analysis_dev.router, prefix="/api/v1")

BODY = {
    "aoi_geojson": {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[
                [-118.80, 48.60], [-118.50, 48.60],
                [-118.50, 48.80], [-118.80, 48.80], [-118.80, 48.60],
            ]]},
        }],
    },
    "target_mineral": "gold",
    "config": {"resolution_m": 2000, "enabled_agents": ["lithology", "structure"]},
    "anthropic_api_key": "sk-test",
}


def main() -> int:
    port = 8124
    threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning"),
        daemon=True,
    ).start()
    time.sleep(2.5)

    events = []
    with httpx.Client(timeout=120.0, trust_env=False) as client:
        with client.stream("POST", f"http://127.0.0.1:{port}/api/v1/analysis/jobs", json=BODY) as resp:
            assert resp.status_code == 200, resp.status_code
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

    names = [e["event"] for e in events]
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  PASS: " if cond else "  FAIL: ") + msg)
        if not cond:
            ok = False

    check("job_complete" in names, "run reached job_complete")

    batches = [e for e in events if e["event"] == "batch_complete"]
    check(len(batches) == CALLS["n"], f"one batch_complete per LLM call ({len(batches)} vs {CALLS['n']})")
    check(
        all(b.get("input_tokens") == IN_TOK for b in batches),
        "every batch reports input_tokens from the usage block",
    )
    check(
        all("response_preview" in b and b["response_preview"] for b in batches),
        "every batch carries a response preview",
    )

    truncated = [b for b in batches if b.get("stop_reason") == "max_tokens"]
    check(len(truncated) == 1, "the truncated batch is flagged with stop_reason=max_tokens")
    check(
        truncated and truncated[0]["parse_status"] == "partial",
        "the truncated batch reports parse_status=partial",
    )
    check(
        truncated and truncated[0]["cells_scored"] < truncated[0]["cells_requested"],
        "the truncated batch reports fewer cells scored than requested",
    )

    usage = next((e for e in events if e["event"] == "usage"), None)
    check(usage is not None, "job-level usage rollup emitted")
    if usage:
        check(
            usage["input_tokens"] == IN_TOK * CALLS["n"],
            f"rollup input_tokens == sum of calls ({usage['input_tokens']} vs {IN_TOK * CALLS['n']})",
        )
        check(usage["llm_calls"] == CALLS["n"], "rollup llm_calls matches actual call count")
        expected_cost = (IN_TOK * CALLS["n"] * 3.0 + OUT_TOK * CALLS["n"] * 15.0) / 1e6
        check(
            abs(usage["est_cost_usd"] - expected_cost) < 1e-6,
            f"cost estimate correct (${usage['est_cost_usd']:.4f} vs ${expected_cost:.4f})",
        )
        # Inverted 2026-08-13. This used to assert == ["structure"] as a
        # deliberate tripwire on Known Gap #1. The gap closed — all six agents
        # have knowledge/<domain>/gold.md — so the tripwire fired and was
        # rewritten to assert the fixed state, per the convention in CLAUDE.md
        # ("canary tests are inverted, not deleted, when a gap closes"). The
        # gap reopening is still a failure.
        check(
            usage["ungrounded_agents"] == [],
            f"no agent runs ungrounded: {usage['ungrounded_agents']}",
        )
        per_agent_in = sum(u["input_tokens"] for u in usage["by_agent"].values())
        check(per_agent_in == usage["input_tokens"], "per-agent usage sums to the job total")

    results = next((e for e in events if e["event"] == "results"), None)
    check(results is not None, "final results delivered")
    if results:
        lith = results["agent_results"]["lithology"]
        check(lith.get("usage") is not None, "usage attached to AgentResult")
        check(lith.get("knowledge_file") == "lithology/gold.md", "knowledge_file attached to AgentResult")

    print("\n" + ("ALL CHECKS PASSED" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
