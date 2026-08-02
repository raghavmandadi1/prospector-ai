# GeoProspector — Claude Project Context

> This file is automatically loaded by Claude Code at the start of every session.
> Keep it current as the architecture evolves. Update when: new agents/connectors are added,
> major decisions are made, or the stack changes.

---

## What This Is

**GeoProspector** is a multi-agent AI application for mineral prospecting. It ingests geological,
geochemical, remote sensing, and historical mining data from public APIs, then runs specialist
AI agents in parallel to score a user-drawn area of interest (AOI) on an interactive map.

The core output is a **scored, color-coded grid** with per-cell evidence drilldown — not a
generic heatmap. The design goal is that every score is backed by traceable evidence from
named data sources. **Read "Known Gaps" below before trusting a score** — that goal is not
currently met on the default dev path.

## Scope

- **Geographic scope:** Washington State only — not a generic global tool. Knowledge bases,
  formation references, and named districts (Republic, Blewett, Monte Cristo, Buckhorn, etc.)
  are WA-specific.
- **Primary mineral:** Gold. The scoring engine (`weights.py`) supports five minerals
  (gold, silver, copper, uranium, lithium), but the agent knowledge base currently only
  covers gold. Other minerals fall back to the engine without specialist domain knowledge
  until per-mineral knowledge files are written.

---

## Known Gaps

Read this first. These are open issues, not TODOs someone will get to — they change how
you should interpret output today.

### 1. Only 2 of 6 agents have a knowledge file

`knowledge/` contains exactly `lithology/gold.md` and `historical/gold.md`. There is no
`default.md` anywhere. The other four agents — **structure, geochemistry, proximity,
remote_sensing** — call `load_knowledge()`, find nothing, log "No knowledge file", and run
with **`system=None`**: no system prompt at all.

Those four carry **0.60 of the gold weight**, structure alone the single highest at 0.30.
So the majority of every gold composite is ungrounded model prior, scored and displayed
identically to the grounded 0.40. Nothing in the UI distinguishes them.

### 2. Spatial context is dead in `DEV_MODE`

`orchestrator._build_spatial_context()` (defined at `orchestrator.py:214`, called at :131)
opens a `try` at :246 and imports, in order:

```
247  from sqlalchemy import select, func
248  from app.db.session import AsyncSessionLocal      # ← fails here
249  from app.models.feature import Feature
```

Line 248 is the one that blows up: `db/session.py` calls `create_async_engine()` at module
scope with a `postgresql+asyncpg://` URL, and **`asyncpg` is not in
`requirements-dev.txt`**. `ModuleNotFoundError` is raised before line 249 runs. The
`except Exception` at `orchestrator.py:314` swallows it and every agent receives the empty
context dict the function was initialized with.

Note the fix is *not* "add `geoalchemy2`" — line 249 never executes. You need `asyncpg`
first (and then `geoalchemy2` for line 249, and then a populated database for the query to
return anything).

The warning it logs is accurate and worth quoting: *"agents will run on LLM regional
knowledge only."* Every agent prompt has a fallback branch for this case, so the run
completes and looks normal. `DEV_MODE=true` is the `.env.example` default and `run-dev.sh`
forces it, so **this is the path you are almost certainly on.**

Combined with (1): on a default dev run, no agent sees database evidence and four of six
have no domain grounding either.

### 3. No tests

Zero. No pytest, conftest, vitest, or CI. `npm run lint` is defined in `package.json` but
eslint is neither installed nor configured, so it fails. Scoring math in `engine.py` and
`grid.py` is unverified by anything but inspection.

### 4. Stubs and unused infrastructure

- `blm_mlrs.py` and `glo_records.py` — `fetch()` returns `[]`. Registered and syncable;
  they just import nothing.
- **MinIO / boto3** — a compose service, four config settings, and a requirements entry.
  Zero usage in `backend/app`. Object storage is entirely aspirational.
- **LangGraph** — in `requirements.txt`, imported nowhere. Orchestration is plain
  `asyncio.gather`.
- `EQUAL_WEIGHTS` in `weights.py` is never referenced; `orchestrator.py:147` falls back to
  `{}` and `_weighted_mean` defaults each missing agent to 1.0. Same behavior, different
  code path than the one previously documented here.
- **608 MB of WA DNR geodatabases** sit in `data/raw/` referenced by no code at all.
- No export endpoint exists, in either mode.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.11 · FastAPI · async/await throughout |
| Agent orchestration | Plain `asyncio.gather` + `asyncio.Semaphore` — **no framework** |
| LLM | Anthropic `claude-sonnet-4-6` (hardcoded, `base_agent.py:248`) |
| Spatial Database | PostgreSQL 15 + PostGIS 3.4 (host port **5433**) |
| Task Queue | Celery + Redis — prod path only, bypassed when `DEV_MODE=true` |
| Object Storage | MinIO — *provisioned but unused, see Known Gaps* |
| Tile Server | Martin (Rust) — serves the `features` table as MVT |
| Frontend | React 18 + TypeScript · MapLibre GL JS · Zustand · Tailwind CSS |
| AOI drawing | `@mapbox/mapbox-gl-draw` + `@turf/area` (25 km² AOI minimum) |
| Build | Vite · Docker Compose (7 services) |

There is no agent framework. `langgraph` is in `requirements.txt` and imported nowhere —
don't write code assuming graph state, checkpointing, or conditional edges exist.

---

## Architecture: Three-Phase Pipeline

```
Phase 1 — Data Ingestion (background / scheduled)
  Connectors → fetch raw → normalize → upsert PostGIS

Phase 2 — Area Selection (on-demand)
  User draws AOI polygon → selects mineral target → triggers analysis job

Phase 3 — Multi-Agent Analysis
  Orchestrator → fan-out 6 specialist agents (asyncio.gather)
             → Scoring Engine (confidence-weighted mean)
             → Scored GeoJSON grid → map overlay
```

**Phase 3 runs on one of two paths, and they are not equivalent:**

| | `DEV_MODE=true` (default) | `DEV_MODE=false` |
|---|---|---|
| Router mounted | `analysis_dev.py` **only** | `channels`, `features`, `analysis` |
| Execution | in-process `asyncio.Task` | Celery task on the `worker` service |
| SSE transport | `asyncio.Queue` streamed on the POST response | Redis pub/sub on `job:{id}:events` |
| Anthropic key | sent in the **request body** from the UI | read from `.env` |
| Persistence | none — results live in the browser only | `analysis_jobs` table |
| `/channels`, `/features` | **404** — ChannelDashboard tab is broken | working |
| Spatial context | fails, agents get empty context (Known Gaps #2) | works if the DB is populated |

Phase 1 (ingestion) is therefore unreachable in dev mode. The frontend only ever calls the
dev path: `AnalysisPanel` uses `runAnalysisDev`, and `analysisApi.createJob` /
`subscribeToJobEvents` / `featuresApi` in `api/client.ts` are dead code no component imports.

---

## Directory Map

```
prospector-ai/
├── CLAUDE.md                        ← you are here
├── .claude/
│   ├── commands/                    ← custom slash commands (agents & tools)
│   │   ├── debug.md                 ← /debug
│   │   ├── learn.md                 ← /learn
│   │   ├── clean.md                 ← /clean
│   │   ├── review.md                ← /review
│   │   ├── new-agent.md             ← /new-agent
│   │   └── new-connector.md         ← /new-connector
│   ├── skills/                      ← WA-specific geological reference skills
│   │   ├── wa-tertiary-stratigraphy.md      ← Weaver (1916) Tertiary units, W-of-Cascades
│   │   ├── wa-eocene-coal-fields.md         ← Newcastle/Renton/Green River/Centralia coal measures
│   │   ├── wa-pretertiary-basement.md       ← Old Metamorphic Series, Index granodiorite, Hoh fm
│   │   ├── wa-historical-geology-source.md  ← citation/nomenclature rules for Weaver (1916)
│   │   └── skill_creator.md
│   └── mistakes-log.md              ← running log of bugs & lessons learned
├── backend/
│   └── app/
│       ├── agents/                  ← specialist agents + orchestrator
│       │   ├── base_agent.py        ← abstract base: build_prompt(), call_llm(), parse_llm_response(), load_knowledge()
│       │   ├── orchestrator.py      ← fans out agents, collects AgentResult, calls scoring engine
│       │   ├── lithology_agent.py
│       │   ├── structure_agent.py
│       │   ├── proximity_agent.py
│       │   ├── geochemistry_agent.py
│       │   ├── remote_sensing_agent.py
│       │   ├── historical_agent.py
│       │   └── knowledge/           ← per-domain, per-mineral domain knowledge (markdown)
│       │       ├── lithology/       ← ONLY gold.md — see Known Gaps #1
│       │       │   └── gold.md      ← WA gold favorability by lithology (epithermal/orogenic/skarn)
│       │       └── historical/      ← ONLY gold.md
│       │           └── gold.md      ← WA gold districts, production, claims/GLO interpretation
│       │       (no structure/, geochemistry/, proximity/, remote_sensing/, no default.md)
│       ├── connectors/              ← data source integrations
│       │   ├── base_connector.py    ← abstract base: fetch(bbox), normalize(raw)
│       │   ├── usgs_mrds.py         ← USGS MRDS via WFS — WORKING (no pagination, 1000 cap)
│       │   ├── usgs_ngdb.py         ← USGS Geochemical DB — WORKING, typeName unverified
│       │   ├── macrostrat.py        ← Macrostrat geology formations — WORKING
│       │   ├── mindat.py            ← MinDat.org localities — WORKING (needs MINDAT_API_KEY)
│       │   ├── blm_mlrs.py          ← BLM federal claims — **STUB, fetch() returns []**
│       │   └── glo_records.py       ← BLM GLO patents — **STUB, fetch() returns []**
│       ├── pipeline/
│       │   ├── __init__.py
│       │   └── ingest.py            ← sync_channel(channel_id) Celery task + CONNECTOR_REGISTRY
│       ├── scoring/
│       │   ├── engine.py            ← synthesize(), normalize_relative(), tier thresholds
│       │   ├── grid.py              ← generate_grid(), interpolate_to_fine_grid() (IDW)
│       │   └── weights.py           ← mineral-specific default weight presets
│       ├── api/
│       │   ├── analysis_dev.py      ← DEV_MODE path: in-process run, SSE on the POST response
│       │   ├── channels.py          ← CRUD for data channel configs   (prod only)
│       │   ├── features.py          ← bbox-filtered GeoJSON feature query (prod only)
│       │   └── analysis.py          ← job submit / status / SSE via Redis (prod only; no export)
│       ├── models/
│       │   ├── feature.py           ← canonical geospatial feature schema
│       │   ├── channel.py           ← data channel config
│       │   ├── analysis_job.py      ← job status + results
│       │   └── agent_result.py      ← AgentResult + ScoredCell Pydantic models
│       ├── db/session.py
│       └── config.py
├── frontend/                        ← 11 source files, ~1,700 lines total
│   └── src/
│       ├── App.tsx                  ← flex shell; tab switcher is LOCAL state, not the store
│       ├── main.tsx
│       ├── components/              ← one .tsx per directory, no sub-components
│       │   ├── Map/MapView.tsx      ← MapLibre + draw tool + the results choropleth
│       │   ├── AnalysisPanel/       ← API key, AOI, mineral, weights, SSE progress, Past Runs
│       │   ├── ResultsOverlay/      ← summary bar + legend + Relative/Absolute toggle
│       │   ├── ChannelDashboard/    ← channel list + sync (404s under DEV_MODE)
│       │   └── EvidenceDrawer/      ← per-cell score breakdown sidebar
│       ├── store/index.ts           ← single flat Zustand store, no middleware, no persist
│       ├── api/client.ts            ← typed API client (several exports are dead code)
│       └── types/index.ts
├── scripts/
│   ├── convert_of00_495.sh          ← GDAL-in-Docker .e00 → GeoTIFF/EPSG:4326 conversion
│   └── extract_pdfs.py              ← PDF triage + text extraction for docs/intake_analyses/
├── data/                            ← see data/README.md; raw/ is gitignored and UNUSED by code
├── tileserver/config.yaml           ← Martin; serves exactly one table (public.features)
├── run-dev.sh                       ← primary local dev path — no Docker, forces DEV_MODE=true
├── docker-compose.yml
├── docker-compose.dev.yml           ← OVERLAY — must be passed with -f alongside the base file
└── docs/
    ├── 01_system_design.md                ← authoritative architecture reference
    ├── 02_scaffold_prompt.md
    ├── 03_implementation_plan.md
    ├── 04_usgs_of00_495_dataset.md        ← NE WA W-of-E raster integration plan (designed, not built)
    ├── 05_western_wa_mvp.md
    ├── 05_knowledge_base_intake_2026-05-04.md
    ├── 06_data_sourcing_checklist.md
    ├── geoprospector_critique.md
    └── intake_analyses/                   ← per-source extracts from the scanned literature
```

---

## Key Patterns

### Adding a New Specialist Agent

1. Create `backend/app/agents/<name>_agent.py`
2. Subclass `BaseAgent` from `app.agents.base_agent`
3. Set `agent_id`, `agent_name`, `knowledge_domain` as class attributes
4. Implement `build_prompt(aoi_geojson, target_mineral, spatial_context) -> str` — this is
   the **only** required method. Every existing agent implements exactly this and nothing else.
5. Register in the `AGENT_CLASSES` **dict** in `orchestrator.py:40` (keyed by agent id)
6. Add a weight entry for the agent in every mineral preset in `scoring/weights.py`
7. Add the agent id to `AGENTS` in `AnalysisPanel.tsx` so its checkbox and weight slider appear
8. Write `knowledge/<domain>/<mineral>.md` — **not optional in practice.** Without it the
   agent runs with no system prompt while still contributing full weight (Known Gaps #1).

Do **not** override `parse_llm_response()`. The shared implementation at `base_agent.py:257`
handles all six agents, including the truncated-JSON repair; overriding it loses that.

Use `/new-agent` command to scaffold the boilerplate.

### Adding a New Data Connector

1. Create `backend/app/connectors/<name>.py`
2. Subclass `BaseConnector` from `app.connectors.base_connector`
3. Implement `async fetch(bbox) -> List[Dict]`
4. Implement `async normalize(raw_records) -> List[Feature]`
5. Register key in `CONNECTOR_REGISTRY` in `pipeline/ingest.py`
6. Add a `Channel` seed record

Use `/new-connector` command to scaffold the boilerplate.

### Domain Knowledge Bases for Agents

Each specialist agent injects a Washington-specific, mineral-specific knowledge file as its
system prompt. This is how we ground the LLM in actual WA formations, districts, and
deposit models rather than generic global heuristics.

- Files live at `backend/app/agents/knowledge/<domain>/<mineral>.md`
  - `<domain>` is the agent name (`lithology`, `historical`, `structure`, etc.)
  - `<mineral>` is the lowercase target (`gold`, `silver`, `copper`, `uranium`, `lithium`)
- `BaseAgent.load_knowledge(domain, target_mineral)` loads the file for that combination,
  falling back to `<domain>/default.md` if the mineral-specific file does not exist,
  and returning `None` if neither is present.
- The loaded markdown is injected as the **system prompt** in the Anthropic API call, so
  the agent reasons with it baked into its persona — not as user-message context.

**Currently written — 2 files, covering 2 of 6 agents, gold only:**
- `lithology/gold.md` — WofE contrasts (USGS OF01-501), epithermal vs orogenic vs skarn
  scoring, NE WA grabens (Republic, Toroda Creek, Keller, First Thought), North Cascades
  metamorphic core
- `historical/gold.md` — district closure analysis (Republic, Monte Cristo, Blewett,
  Liberty/Swauk, Colville-Metaline), MRDS positional accuracy caveats, BLM/GLO claims
  interpretation, depth and technology modifiers

**Missing:** `structure/`, `geochemistry/`, `proximity/`, `remote_sensing/`, and any
`default.md`. Those four agents run with `system=None`. Highest-value next file is
`structure/gold.md` — structure carries the top gold weight (0.30) and has no grounding
at all. See Known Gaps #1.

**Pattern when adding a new knowledge file:** keep it WA-specific (cite real formations,
named districts, and named data sources), include a scoring rubric the LLM can apply
directly, and document confidence-calibration guidance and common pitfalls.

### Reference Skills (`.claude/skills/`)

Four Washington-geology reference skills, separate from agent knowledge files, encode
how to cite and interpret historical WA geological literature — primarily Weaver (1916,
WGS Bulletin 13). Agents producing evidence strings should reference these via the
Skill tool when their reasoning touches western WA Tertiary stratigraphy, Eocene coal
fields, or pre-Tertiary basement units. The `wa-historical-geology-source` skill defines
the canonical citation form (`Weaver_1916_WGS_Bulletin_13`) for `data_sources_used`.

### AgentResult / ScoredCell Schema

```python
AgentResult(
    agent_id="my_agent",
    status="completed",        # or "failed"
    scored_cells=[
        ScoredCell(
            cell_id="col_row",
            score=0.0–1.0,              # absolute composite, always preserved
            confidence=0.0–1.0,         # 0.0 means "LLM never scored this" — engine ignores it
            evidence=["Human-readable strings", ...],
            data_sources_used=["source_name", ...],
            # set by engine.normalize_relative() after synthesis:
            relative_score=0.0–1.0,     # min-max stretch within the AOI
            percentile=0.0–1.0,         # rank within the AOI
            tier="high|medium|low|negligible",
            # set by grid.interpolate_to_fine_grid() on IDW-downscaled cells:
            parent_cell_id="col_row",   # analysis cell this display cell inherited from
        )
    ],
    agent_notes="optional summary string",
    warnings=[]
)
```

`confidence=0.0` is load-bearing: it is how a cell the LLM skipped is distinguished from a
cell the LLM scored as genuinely poor. Never let a parse failure emit `confidence>0`.
The frontend mirror lives in `frontend/src/types/index.ts` and must be kept in sync.

### Scoring Tiers — AOI-Relative

Tiers and map shading are **relative to the AOI**, not absolute: the grid answers
"where are the best spots in THIS polygon", not "how does this area compare to
the world". After synthesis, `engine.normalize_relative()` annotates each cell
with `relative_score` (min-max stretch of the composite within the AOI),
`percentile` (rank within the AOI), and `tier` from percentile:

| Tier | Percentile within AOI |
|---|---|
| High priority | top 10% (≥ 0.90) |
| Medium priority | ≥ 0.65 |
| Low priority | ≥ 0.35 |
| Negligible | < 0.35 |

The absolute composite `score` is preserved on every cell; the UI legend has a
Relative/Absolute toggle and the EvidenceDrawer shows both. A uniform AOI
(max == min composite) gets flat mid shading — no invented hotspots.

### Two-Level Grid & LLM Batching

- Agents score a **coarse analysis grid** capped at `MAX_LLM_CELLS` (150) — the
  orchestrator doubles `resolution_m` (i.e. coarsens the grid, halving cell count each
  pass) until the AOI fits.
- If the user requested a finer display resolution (down to 100 m), coarse
  composite scores are IDW-interpolated onto the fine grid
  (`grid.interpolate_to_fine_grid`); fine cells carry `parent_cell_id` so the
  UI shows per-agent evidence from the analysis cell. The display grid is
  additionally capped at `MAX_DISPLAY_CELLS` (12,000).
- Within each agent, `BaseAgent.run()` scores cells in **batches of 50 per LLM
  call** (bounded concurrency), repairs truncated JSON responses, and fills
  LLM-missed cells with confidence=0 placeholders the engine ignores.
- Historical/geochemistry scoring follows **assay primacy**: records carrying
  assay/grade/production values dominate; district proximity without assay
  backing caps at ~0.6 (see `knowledge/historical/gold.md`).
- The UI keeps an in-memory **run history** (Past Runs in AnalysisPanel):
  old AOI polygons can be re-viewed and deleted after inspecting their data.

### Mineral Weight Presets (default)

Authoritative source: `backend/app/scoring/weights.py`. Weights are relative; the scoring
engine normalizes during weighted-mean computation. Minerals not listed get `{}` from
`orchestrator.py:147`, and `_weighted_mean` (`engine.py:159`) then defaults each agent to
1.0 — equal weighting. (`EQUAL_WEIGHTS` is defined in `weights.py` but never referenced;
same outcome, different path. Don't wire new code to it without also wiring the call site.)

| Agent | Gold | Silver | Copper | Uranium | Lithium |
|---|---|---|---|---|---|
| Lithology | 0.25 | 0.25 | 0.30 | 0.35 | 0.35 |
| Structure | 0.30 | 0.25 | 0.20 | 0.20 | 0.15 |
| Geochemistry | 0.20 | 0.20 | 0.25 | 0.25 | 0.25 |
| Historical | 0.15 | 0.15 | 0.03 | 0.10 | 0.03 |
| Remote Sensing | 0.07 | 0.05 | 0.15 | 0.03 | 0.07 |
| Proximity | 0.03 | 0.10 | 0.07 | 0.07 | 0.15 |

---

## Running the App

### Fastest path — no Docker (what you'll use day to day)

```bash
pip install -r backend/requirements-dev.txt
cd frontend && npm install && cd ..
./run-dev.sh          # forces DEV_MODE=true; starts uvicorn :8000 and vite :5173
```

Paste your Anthropic key into the AnalysisPanel field — in this mode the key travels in the
request body, not from `.env`. The Channels tab will 404 (see the DEV_MODE table above).

### Full stack — Docker Compose

```bash
# All 7 services: postgres, redis, minio, tileserver, backend, worker, frontend
docker-compose up

# Dev overlay — BOTH -f flags are required; docker-compose.dev.yml has no build context
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up

# Run DB migrations (only meaningful with DEV_MODE=false — dev mode never touches the DB)
docker-compose exec backend alembic upgrade head

# Check PostGIS is live — service is named `postgres`, not `db`
docker-compose exec postgres psql -U geoprospector -d geoprospector -c "SELECT PostGIS_Version();"

# Tail logs — the Celery service is named `worker`
docker-compose logs -f backend
docker-compose logs -f worker

open http://localhost:8000/docs     # API docs
open http://localhost:5173          # frontend
```

### Gotchas that will waste your afternoon

- **Postgres is on host port 5433**, not 5432 (`docker-compose.yml:9`, to dodge a local
  clash). `DATABASE_URL` in `config.py` defaults to `localhost:5432` — running the backend
  outside Docker against the Compose DB needs the port corrected.
- `VITE_TILESERVER_URL` is **set nowhere**, so the Martin `features-points` layer never
  renders in any configuration. Set it if you want vector tiles.
- `npm run lint` fails — eslint is neither installed nor configured. Use `npm run typecheck`.

---

## Development Conventions

### Python (backend)
- Async everywhere — all DB access and HTTP calls must use `async/await`
- Type hints on all function signatures
- Pydantic models for all API request/response bodies
- SQLAlchemy 2.0 style (`select()`, `session.execute()`, not legacy `.query()`)
- All geometries in SRID 4326 (WGS84) in the DB; use pyproj for UTM projections in grid math
- `logger = logging.getLogger(__name__)` at the top of every module
- Agents must never raise — catch exceptions in `run()`, return `AgentResult(status="failed")`

### TypeScript (frontend)
- Strict TypeScript — no `any` without a comment explaining why
- All API calls go through `src/api/` — no raw `fetch()` in components
- State in Zustand store — no prop drilling beyond 2 levels
- MapLibre layer IDs follow pattern: `<source>-<type>` (e.g., `mrds-points`, `results-fill`)
- Components are functional; hooks for logic

### Testing
There is no test suite yet, so "it ran without an exception" is the current bar and it is
too low — the all-zero-scores bug in `.claude/mistakes-log.md` passed that bar for weeks.
Until a suite exists, changes to `scoring/engine.py` or `scoring/grid.py` should be
exercised against a known AOI and the composite values eyeballed. Adding `pytest` +
`tests/test_engine.py` is the highest-leverage unclaimed task in the repo.

### Git
- Branch per feature: `feature/<slug>` or `fix/<slug>`
- Commit messages: `<type>(<scope>): <description>` (e.g., `feat(agents): add water chemistry agent`)
- Never commit `.env` or secrets
- Never commit binary GIS data or scanned PDFs — `data/raw/` and `data/literature/` are
  gitignored, and one `.e00` alone is 149 MB against GitHub's 100 MB hard limit

---

## Environment Variables

All config is loaded via Pydantic `BaseSettings` in `backend/app/config.py` from a `.env`
file. Variable names are case-insensitive. There are no raw `os.getenv()` calls in the
codebase — add new settings to `config.py` rather than reading env directly.

Copy `.env.example` → `.env` and fill in secrets before starting. **`.env.example` is the
source of truth** — it uses Docker service hostnames (`@postgres:5432`, `redis://redis:6379`,
`minio:9000`), which is what you want under Compose. Don't paste hostnames out of this file.

| Variable | Read by | Notes |
|---|---|---|
| `APP_ENV` | `config.py` | |
| `DEV_MODE` | `config.py:7` | defaults **true**; see below |
| `SECRET_KEY` | `config.py` | |
| `CORS_ORIGINS` | `config.py` | JSON list form: `["http://localhost:5173"]` |
| `DATABASE_URL` | `config.py` | `config.py` default says `localhost:5432`; Compose maps the host to **5433** |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | `config.py` | prod path only |
| `ANTHROPIC_API_KEY` | `config.py` | required for `DEV_MODE=false`; in dev the **UI** supplies it in the request body |
| `MINDAT_API_KEY` | `config.py` | required only for the mindat connector |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` | `config.py` | **declared and read by nothing.** No boto3/S3 code exists in `backend/app`. |
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | `docker-compose.yml` only | absent from `config.py` |
| `AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY/ENDPOINT_URL` | **nothing** | absent from `config.py` |

The last two groups are silently dropped by `extra="ignore"` (`config.py:35`) — setting
them has no effect on the application.

### `DEV_MODE` behavior

`DEV_MODE=true` is the default in both `config.py:7` and `.env.example`, and `run-dev.sh`
forces it. It is not a minor variation on the prod path — see the comparison table under
Architecture. Summary of what it costs you:

- Only `analysis_dev.router` is mounted. `/channels` and `/features` 404.
- Nothing is persisted. Results exist only in the browser's Zustand store, capped at 20
  runs and lost on reload.
- The PostGIS spatial-context query fails silently; agents get no data (Known Gaps #2).

Always test with `DEV_MODE=false` before assuming a change works in production-like
conditions. The code paths genuinely differ — different router, different SSE transport,
different source for the API key.

---

## Claude Slash Commands

| Command | Purpose |
|---|---|
| `/debug` | Systematic debugging workflow for this stack |
| `/learn` | Record a bug or mistake into the lessons-learned log |
| `/clean` | Audit and remove dead code, unused imports, stale TODOs |
| `/review` | Code review with project-specific checklist |
| `/new-agent` | Scaffold a new specialist agent |
| `/new-connector` | Scaffold a new data connector |

Definitions live in `.claude/commands/`.

---

## Static Reference Datasets

**None of these are wired into the application.** ~608 MB sits in `data/raw/` (gitignored)
that no module reads — grepping `backend/` and `frontend/src` for
`of00|ger_portal|surface_geology|mines_minerals` returns zero hits. The only code that
touches these paths is `scripts/convert_of00_495.sh`, an offline conversion step. Full
provenance in `data/README.md`.

- **WA DNR / WGS Mines & Minerals** (~77 MB) — ESRI File Geodatabase. Feature classes
  include `Gold_Silver_Locations`, `Metallic_Mineral_Occurences`, `Mining_Districts_WA`,
  `IAML_Sites`. This is the highest-value unused asset in the repo: statewide, WA-authored,
  and directly comparable to what MRDS already provides at lower positional accuracy.
- **WA DNR / WGS Surface Geology 1:24k** (~218 MB) — `geologic_unit_poly`, `fault`, `fold`,
  `dike`, `contact` statewide. Would ground the structure agent, which currently has neither
  a knowledge file nor data.
- **USGS Open-File Report 00-495** (Boleneus & Causey 2000) — *Geologic raster data
  for weights-of-evidence analysis in NE Washington.* Covers the six 1:100,000
  quadrangles (Colville, Chewelah, Republic, Nespelem, Omak, Oroville) — i.e. the
  heart of WA gold country. Four ArcInfo GRID layers: `newageol` (lithology, 50 m),
  `newafold` (folds, 50 m), `newafaul` (faults, 100 m), `newadike` (dikes, 200 m).
  Native CRS is UTM 11N / NAD27 (EPSG:26711) — must be reprojected to EPSG:4326.
  Full integration plan, conversion path, and proposed knowledge-JSON structure
  are in `docs/04_usgs_of00_495_dataset.md`. When implemented, the loader lives at
  `backend/app/connectors/usgs_of00_495.py` (one-time load, not a recurring sync).
  `scripts/convert_of00_495.sh` — the GDAL-in-Docker `.e00` → EPSG:4326 conversion — is
  already written and working; only the loader is missing.

---

## Current Implementation Status

Track progress in `docs/03_implementation_plan.md`.

- [x] **M1: Running scaffold** — 7 compose services defined; `run-dev.sh` boots without Docker
- [~] **M2: First data flowing** — 4 connectors fetch live; the Martin tile layer never renders
      because `VITE_TILESERVER_URL` is unset, and `/features` 404s under DEV_MODE
- [~] **M3: Full data layer** — 4 of 6 connectors real, `blm_mlrs` + `glo_records` are stubs;
      no loader for the 608 MB of local WA DNR / USGS data
- [ ] **M4: Scoring foundation** — grid + engine are written and behave correctly on
      inspection, but there is **not one test in the repo**. This stays unchecked until there is.
- [x] **M5: First end-to-end analysis** — full job runs draw → agents → synthesis → grid
- [x] **M6: Full UI** — draw → run → results → evidence drawer, plus Relative/Absolute
      toggle and Past Runs
- [ ] **M7: Production-ready MVP** — no export endpoint, no tests, no CI, no working lint;
      four agents ungrounded and spatial context dead in dev (Known Gaps #1, #2)

`[~]` = partially done.

---

*Last updated: 2026-08-01 — documentation audited line-by-line against the source and
corrected. Removed the LangGraph claim (never imported); documented the DEV_MODE vs prod
split and `analysis_dev.py`; marked `blm_mlrs`/`glo_records` as stubs and MinIO/boto3 as
unused; recorded that only 2 of 6 agents have knowledge files and that the PostGIS
spatial-context query is dead in dev mode; fixed compose service names (`worker`,
`postgres`), the 5433 host port, and the dev-overlay invocation; documented `run-dev.sh`,
`scripts/`, and the unused WA DNR datasets; replaced `OrchestratorAgent.agents` with
`AGENT_CLASSES`. Verified as still accurate: the full 5-mineral weight table, the percentile
tier thresholds, MAX_LLM_CELLS/MAX_DISPLAY_CELLS/batch constants, and the AOI-relative
scoring description.*
