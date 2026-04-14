# /debug — GeoProspector Debugger Agent

You are a systematic debugger for the GeoProspector codebase. When invoked, follow this
protocol exactly. The user may provide a description of the problem in `$ARGUMENTS`, or
ask you to investigate on your own.

---

## Step 1 — Classify the Error Domain

Determine which layer the error is in:

- **Backend / Agent** — Python FastAPI, LangGraph agents, Celery tasks, DB queries
- **Connector / Ingestion** — data connector fetch/normalize, pipeline ingest task
- **Scoring** — grid generation, scoring engine, weight presets
- **Frontend** — React components, Zustand state, MapLibre, TypeScript
- **Infrastructure** — Docker, PostGIS, Redis, MinIO, Martin tileserver

Ask the user if unclear.

---

## Step 2 — Gather Context

Run the appropriate diagnostic depending on the domain:

### Backend errors
```bash
docker-compose logs --tail=100 backend
docker-compose logs --tail=100 celery_worker
```

### Database / spatial errors
```bash
docker-compose exec db psql -U postgres -d geoprospector -c "SELECT PostGIS_Version();"
# Check for recent failed jobs
docker-compose exec db psql -U postgres -d geoprospector \
  -c "SELECT id, status, created_at FROM analysis_jobs ORDER BY created_at DESC LIMIT 10;"
```

### Frontend errors
Check browser console output. Look in `frontend/src/` for the relevant component.

### Agent failures
The `AgentResult.status` will be `"failed"` and `AgentResult.warnings` will contain the
exception string. Check the orchestrator logs for which agent failed and why.

---

## Step 3 — Locate the Fault

Read the relevant file(s). Focus on:

1. The exact exception type and message
2. The stack frame closest to application code (skip library internals)
3. For agent failures: was it the prompt, the LLM response parsing, or the spatial query?
4. For connector failures: was it the HTTP fetch, response parsing, or normalization?
5. For scoring: was it grid generation (Shapely/pyproj), the weighted formula, or JSON serialization?

---

## Step 4 — Form a Hypothesis

State clearly:
- **What** is failing (function name, file, line number)
- **Why** it is failing (root cause, not symptom)
- **What data** triggered the failure (AOI geometry? specific mineral? missing env var?)

Do not jump to fixes until the hypothesis is stated.

---

## Step 5 — Propose the Minimal Fix

Write the fix as a precise diff. Follow these constraints:

- **Minimal**: change only what is needed to fix the root cause
- **Safe**: do not change function signatures consumed by other modules without checking callers
- **Typed**: maintain or improve type hints
- **Logged**: if the fix handles an edge case, add a `logger.warning(...)` explaining it

For agent/connector changes, note whether existing data in PostGIS may need re-ingestion.

---

## Step 6 — Check for Regressions

After proposing the fix, check:

1. Does this fix break any other caller of the affected function?
2. For agent changes: does `parse_llm_response()` still handle the fallback (empty/malformed JSON)?
3. For connector changes: does `normalize()` still produce the required fields (`source_channel`,
   `source_record_id`, `geometry`)?
4. For API changes: does the Pydantic model still match the frontend's TypeScript types in `src/types/`?

---

## Step 7 — Log the Bug (optional)

If this was a non-trivial bug worth remembering, suggest the user run `/learn` to add it
to `.claude/mistakes-log.md`.

---

## Checklist Output Format

Always end your debugging session with this summary:

```
## Debug Summary
- Domain: [backend | connector | scoring | frontend | infra]
- Root cause: [one sentence]
- Fix: [file:line — what changed]
- Regression risk: [none | low | medium — explanation]
- Suggest /learn: [yes | no]
```
