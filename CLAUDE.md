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
generic heatmap. Every score is backed by traceable evidence from named data sources.

## Scope

- **Geographic scope:** Washington State only — not a generic global tool. Knowledge bases,
  formation references, and named districts (Republic, Blewett, Monte Cristo, Buckhorn, etc.)
  are WA-specific.
- **Primary mineral:** Gold. The scoring engine (`weights.py`) supports five minerals
  (gold, silver, copper, uranium, lithium), but the agent knowledge base currently only
  covers gold. Other minerals fall back to the engine without specialist domain knowledge
  until per-mineral knowledge files are written.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.11 · FastAPI · async/await throughout |
| Agent Framework | LangGraph · Anthropic `claude-sonnet-4-6` |
| Spatial Database | PostgreSQL 15 + PostGIS 3.4 |
| Task Queue | Celery + Redis |
| Object Storage | MinIO (S3-compatible) |
| Tile Server | Martin (Rust) — serves PostGIS tables as MVT |
| Frontend | React 18 + TypeScript · MapLibre GL JS · Zustand · Tailwind CSS |
| Build | Vite · Docker Compose (7 services) |

---

## Architecture: Three-Phase Pipeline

```
Phase 1 — Data Ingestion (background / scheduled)
  Connectors → fetch raw → normalize → upsert PostGIS

Phase 2 — Area Selection (on-demand)
  User draws AOI polygon → selects mineral target → triggers analysis job

Phase 3 — Multi-Agent Analysis (Celery task)
  Orchestrator → fan-out 6 specialist agents (asyncio.gather)
             → Scoring Engine (confidence-weighted mean)
             → Scored GeoJSON grid → map overlay
```

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
│       │       ├── lithology/
│       │       │   └── gold.md      ← WA-specific gold favorability by lithology (epithermal/orogenic/skarn)
│       │       └── historical/
│       │           └── gold.md      ← WA gold districts, production, claims/GLO interpretation
│       ├── connectors/              ← data source integrations
│       │   ├── base_connector.py    ← abstract base: fetch(bbox), normalize(raw)
│       │   ├── usgs_mrds.py         ← USGS Mineral Resources Data System (~300k+ deposits)
│       │   ├── usgs_ngdb.py         ← USGS National Geochemical Database
│       │   ├── macrostrat.py        ← Macrostrat geology formations
│       │   ├── blm_mlrs.py          ← BLM active federal mining claims
│       │   ├── glo_records.py       ← BLM GLO historical land patents
│       │   └── mindat.py            ← MinDat.org mineral localities
│       ├── pipeline/                ← Celery ingestion tasks
│       │   ├── ingest.py            ← sync_channel(channel_id) task
│       │   ├── normalize.py
│       │   ├── geocode.py
│       │   └── spatial_index.py
│       ├── scoring/
│       │   ├── engine.py            ← confidence-weighted mean synthesis
│       │   ├── grid.py              ← AOI → regular grid of cells (Shapely + pyproj)
│       │   └── weights.py           ← mineral-specific default weight presets
│       ├── api/
│       │   ├── channels.py          ← CRUD for data channel configs
│       │   ├── features.py          ← bbox-filtered GeoJSON feature query
│       │   └── analysis.py          ← job submission, status, SSE stream, export
│       ├── models/
│       │   ├── feature.py           ← canonical geospatial feature schema
│       │   ├── channel.py           ← data channel config
│       │   ├── analysis_job.py      ← job status + results
│       │   └── agent_result.py      ← AgentResult + ScoredCell Pydantic models
│       ├── db/session.py
│       └── config.py
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── Map/                 ← MapLibre map, layers, draw tool
│       │   ├── AnalysisPanel/       ← AOI config, job submission, SSE progress
│       │   ├── ResultsOverlay/      ← choropleth grid layer on map
│       │   ├── ChannelDashboard/    ← data channel list + sync controls
│       │   └── EvidenceDrawer/      ← per-cell score breakdown sidebar
│       ├── store/                   ← Zustand state (aoi, job, results, layers)
│       ├── api/                     ← typed API client
│       └── types/                   ← TypeScript interfaces
├── tileserver/                      ← Martin config
├── docker-compose.yml
├── docker-compose.dev.yml
└── docs/
    ├── 01_system_design.md                ← authoritative architecture reference
    ├── 02_scaffold_prompt.md
    ├── 03_implementation_plan.md
    └── 04_usgs_of00_495_dataset.md        ← NE WA geology W-of-E raster integration plan (designed, not yet implemented)
```

---

## Key Patterns

### Adding a New Specialist Agent

1. Create `backend/app/agents/<name>_agent.py`
2. Subclass `BaseAgent` from `app.agents.base_agent`
3. Implement `build_prompt(aoi_geojson, target_mineral, spatial_context) -> str`
4. Implement `parse_llm_response(response, grid_cells) -> List[ScoredCell]`
5. Register in `OrchestratorAgent.agents` list in `orchestrator.py`

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

**Currently written (gold-only):**
- `lithology/gold.md` — WofE contrasts (USGS OF01-501), epithermal vs orogenic vs skarn
  scoring, NE WA grabens (Republic, Toroda Creek, Keller, First Thought), North Cascades
  metamorphic core
- `historical/gold.md` — district closure analysis (Republic, Monte Cristo, Blewett,
  Liberty/Swauk, Colville-Metaline), MRDS positional accuracy caveats, BLM/GLO claims
  interpretation, depth and technology modifiers

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
            score=0.0–1.0,
            confidence=0.0–1.0,
            evidence=["Human-readable strings", ...],
            data_sources_used=["source_name", ...]
        )
    ],
    agent_notes="optional summary string",
    warnings=[]
)
```

### Scoring Tiers

| Tier | Score Range |
|---|---|
| High priority | 0.70–1.0 |
| Medium priority | 0.45–0.69 |
| Low priority | 0.20–0.44 |
| Negligible | 0.0–0.19 |

### Mineral Weight Presets (default)

Authoritative source: `backend/app/scoring/weights.py`. Weights are relative; the scoring
engine normalizes during weighted-mean computation. Minerals not listed fall back to
`EQUAL_WEIGHTS` (1.0 across all six agents).

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

```bash
# Start all 7 services (postgres, redis, minio, martin, backend, frontend, celery)
docker-compose up

# Dev mode (hot reload)
docker-compose -f docker-compose.dev.yml up

# Run DB migrations
docker-compose exec backend alembic upgrade head

# Check PostGIS is live
docker-compose exec db psql -U postgres -c "SELECT PostGIS_Version();"

# Tail backend logs
docker-compose logs -f backend

# Tail Celery worker logs
docker-compose logs -f celery_worker

# Open API docs
open http://localhost:8000/docs

# Open frontend
open http://localhost:5173
```

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

### Git
- Branch per feature: `feature/<slug>` or `fix/<slug>`
- Commit messages: `<type>(<scope>): <description>` (e.g., `feat(agents): add water chemistry agent`)
- Never commit `.env` or secrets

---

## Environment Variables

All config is loaded via Pydantic `BaseSettings` in `backend/app/config.py` from a `.env`
file. Variable names are case-insensitive. There are no raw `os.getenv()` calls in the
codebase — add new settings to `config.py` rather than reading env directly.

```env
# App
APP_ENV=development
DEV_MODE=true                  # when true, analysis runs in-process (no Celery/Redis required)
SECRET_KEY=change-this-secret-key
CORS_ORIGINS=["http://localhost:5173"]

# Database / Redis
DATABASE_URL=postgresql+asyncpg://geoprospector:changeme@localhost:5432/geoprospector
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# MinIO (S3-compatible object store)
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=geoprospector

# LLM
ANTHROPIC_API_KEY=...          # required

# Connector API keys
MINDAT_API_KEY=...             # required for the mindat connector; optional otherwise
```

Copy `.env.example` → `.env` and fill in secrets before starting.

### `DEV_MODE` behavior

When `DEV_MODE=true`, the analysis pipeline runs **in-process** instead of dispatching
through Celery — useful for local debugging without booting Redis or the celery_worker
container. Always test with `DEV_MODE=false` before assuming a change works in
production-like conditions; the code paths are not identical.

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

In addition to the live API connectors above, a one-time-loaded reference dataset
is in design but **not yet implemented**:

- **USGS Open-File Report 00-495** (Boleneus & Causey 2000) — *Geologic raster data
  for weights-of-evidence analysis in NE Washington.* Covers the six 1:100,000
  quadrangles (Colville, Chewelah, Republic, Nespelem, Omak, Oroville) — i.e. the
  heart of WA gold country. Four ArcInfo GRID layers: `newageol` (lithology, 50 m),
  `newafold` (folds, 50 m), `newafaul` (faults, 100 m), `newadike` (dikes, 200 m).
  Native CRS is UTM 11N / NAD27 (EPSG:26711) — must be reprojected to EPSG:4326.
  Full integration plan, conversion path, and proposed knowledge-JSON structure
  are in `docs/04_usgs_of00_495_dataset.md`. When implemented, the loader lives at
  `backend/app/connectors/usgs_of00_495.py` (one-time load, not a recurring sync).

---

## Current Implementation Status

Track progress in `docs/03_implementation_plan.md`. Update the status line below
as milestones complete:

- [ ] M1: Running scaffold — all services healthy
- [ ] M2: First data flowing — MRDS points on map
- [ ] M3: Full data layer — all core connectors
- [ ] M4: Scoring foundation — grid + engine unit tested
- [ ] M5: First end-to-end analysis — full job runs
- [ ] M6: Full UI — draw → run → results on map
- [ ] M7: Production-ready MVP — exports, error handling, perf

---

*Last updated: 2026-05-04 — added WA scope, gold-first priority, knowledge-base architecture
(`agents/knowledge/<domain>/<mineral>.md`), `.claude/skills/` reference, USGS OF00-495 dataset
plan, corrected mineral weight presets and env-var schema, documented `DEV_MODE` in-process
mode. Update this file when major architecture decisions change.*
