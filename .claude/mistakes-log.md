# GeoProspector — Mistakes & Lessons Learned

> This file is maintained by the `/learn` command. Do not edit manually unless correcting
> a factual error. Each entry captures a real bug or design mistake with enough detail
> to prevent recurrence.
>
> **Add entries via:** `/learn <description of what went wrong>`

---

<!-- Entries are appended below by the /learn command. Most recent at the bottom. -->

<!-- TEMPLATE (for reference — do not delete this comment):

---

## [Short title — imperative, present tense]

**Date:** YYYY-MM-DD
**Tags:** `tag1` `tag2`
**Location:** `path/to/file.py :: function_name()`

### What Happened
[Symptom description]

### Root Cause
[Why it happened]

### Fix Applied
[What was changed]

### Prevention Rule
[Always/Never/Check... rule for future work]

### Code Smell / Warning Sign
[Pattern that should trigger suspicion in future]

-->

---

## Size the LLM output budget to the response you're demanding

**Date:** 2026-07-07
**Tags:** `llm` `truncation` `json-parsing` `agents`
**Location:** `backend/app/agents/base_agent.py :: call_llm() / _safe_parse_json()`

### What Happened
Every analysis returned score=0, confidence=0, composite=0 for all cells even
though agent_notes (the raw LLM excerpt) clearly contained correct per-cell
scores. Map shading was uniformly "negligible" gray.

### Root Cause
`max_tokens=4096` while prompts demanded a JSON array covering up to 50-60
cells with multi-string evidence. Responses were truncated mid-array, the
closing ``` never arrived, the fence regex failed, `json.loads` on the whole
text failed, parse returned None, and every cell fell into the
"Cell not scored by LLM" placeholder path (score 0 / confidence 0). The
scoring engine then produced 0/0 composites for the entire grid. The bug was
invisible in the UI because the response *excerpt* (first 1000 chars) looked
perfectly correct.

### Fix Applied
- max_tokens 4096 → 16000; compact-JSON response contract
- Batched scoring (50 cells per LLM call, bounded concurrency) in BaseAgent.run
- `_safe_parse_json` now repairs truncated arrays by salvaging complete objects
- Fill-missing centralized in BaseAgent.run; placeholders get confidence=0 so
  the engine ignores them instead of dragging composites to zero

### Prevention Rule
Whenever a prompt says "score EVERY item", compute worst-case response tokens
(items × per-item JSON size) and assert it fits within max_tokens with
headroom. Never let a parse failure silently zero out an entire result set —
distinguish "parsed as zero" from "not parsed".

### Code Smell / Warning Sign
A parser whose failure branch produces the same output shape as a legitimate
all-low result. If `status="completed"` can coexist with 100% placeholder
cells, the pipeline can lie to the UI.


---

## A silent `except` around your only data source is a lie the pipeline tells the UI

**Date:** 2026-08-01
**Tags:** `orchestrator` `dev-mode` `dependencies` `silent-failure` `open-issue`
**Location:** `backend/app/agents/orchestrator.py :: _gather_spatial_context()` (import at
:246, swallow at :314) · `backend/requirements-dev.txt`
**Status:** OPEN — documented, not fixed

### What Happened
Found during a full documentation audit, not from a bug report — which is the point. Every
analysis run under `DEV_MODE=true` scores its grid with **zero database evidence**. Agents
produce confident, plausible, fully-populated evidence strings anyway. Nothing in the UI,
the SSE stream, or the job result indicates that the entire spatial-context layer was
skipped. The only signal is one `logger.warning` in the backend console.

### Root Cause
`_gather_spatial_context()` imports `app.models.feature` inside its `try`, which requires
`geoalchemy2` and `asyncpg`. Neither is in `requirements-dev.txt` — deliberately, because
`models/__init__.py` has a lazy-import shim to keep dev installs off PostGIS. So the import
raises `ModuleNotFoundError`, the broad `except Exception` at :314 catches it, logs a
warning, and returns the empty context dict it was initialized with.

Two design decisions collided. Neither is wrong alone:
1. Keep dev installs light by excluding PostGIS deps.
2. Degrade gracefully when the DB is unreachable, so a missing DB doesn't kill a run.

Together they mean the *default* path silently degrades to "LLM freestyles from priors,"
and because `DEV_MODE=true` is the default in `config.py:7`, in `.env.example`, and forced
by `run-dev.sh`, that is the path essentially every run takes. Every agent prompt already
has a fallback branch for empty context, so output looks completely normal.

### Compounding Factor
`knowledge/` contains only `lithology/gold.md` and `historical/gold.md`, and there is no
`default.md`. The other four agents get `system=None` — no system prompt at all — while
still carrying 0.60 of the gold weight (structure alone is 0.30, the highest single weight).
So on a default run: no agent has data, and four of six have no domain grounding either.
The composite is presented with the same evidence-drilldown UI as a fully-grounded score.

### Fix Applied
None yet. Documented in `CLAUDE.md` under **Known Gaps** and in `README.md` under
**Current state**. Candidate fixes, cheapest first:

1. Add `geoalchemy2` + `asyncpg` to `requirements-dev.txt` — makes the query actually run
   in dev. Only helps if the DB is up and populated, which under `run-dev.sh` it isn't.
2. Catch `ImportError` separately from operational DB errors and surface it as a job-level
   `warning` on `AgentResult.warnings`, so the UI can show "scored without database
   evidence." The `warnings` field already exists and is unused.
3. Write `structure/gold.md` first — highest weight, zero grounding.
4. Have `load_knowledge()` returning `None` push a warning onto the agent's result rather
   than only logging.

### Prevention Rule
Never let a broad `except` wrap the acquisition of the data a component exists to reason
about. If the fallback path produces output that is indistinguishable from the success
path, the fallback must set a flag that reaches the user — a log line is not a user
interface. Catch the *specific* exceptions you actually intend to tolerate.

### Code Smell / Warning Sign
Same shape as the all-zero-scores bug above: **a failure mode whose output is
indistinguishable from a legitimate result.** That's the recurring pattern in this codebase
now — twice is a trend. When reviewing, ask of every `except` and every `if not x: return
default`: *could a user tell this apart from success?* If not, it needs a warning on the
result object, not in the log.

Second smell: a dependency list curated for speed that silently changes runtime behavior
rather than failing at import. If omitting a package changes what the program *computes*
instead of whether it *starts*, that omission is a config flag in disguise and belongs in
documentation.
