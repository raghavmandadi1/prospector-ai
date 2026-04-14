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
│   └── mistakes-log.md              ← running log of bugs & lessons learned
├── backend/
│   └── app/
│       ├── agents/                  ← specialist agents + orchestrator
│       │   ├── base_agent.py        ← abstract base: build_prompt(), call_llm(), parse_llm_response()
│       │   ├── orchestrator.py      ← fans out agents, collects AgentResult, calls scoring engine
│       │   ├── lithology_agent.py
│       │   ├── structure_agent.py
│       │   ├── proximity_agent.py
│       │   ├── geochemistry_agent.py
│       │   ├── remote_sensing_agent.py
│       │   └── historical_agent.py
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
    ├── 01_system_design.md          ← authoritative architecture reference
    ├── 02_scaffold_prompt.md
    └── 03_implementation_plan.md
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

| Agent | Gold | Silver | Copper | Lithium |
|---|---|---|---|---|
| Lithology | 0.20 | 0.20 | 0.25 | 0.15 |
| Structure | 0.25 | 0.20 | 0.15 | 0.10 |
| Proximity | 0.20 | 0.20 | 0.20 | 0.20 |
| Geochemistry | 0.20 | 0.20 | 0.20 | 0.25 |
| Remote Sensing | 0.10 | 0.10 | 0.10 | 0.15 |
| Historical | 0.15 | 0.15 | 0.10 | 0.05 |

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

## Environment Variables (required)

```env
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://localhost:6379/0
S3_ENDPOINT=http://localhost:9000
S3_BUCKET=geoprospector-raw
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
ANTHROPIC_API_KEY=...
MINDAT_API_KEY=...          # optional
NASA_EARTHDATA_TOKEN=...    # optional, for remote sensing
```

Copy `.env.example` → `.env` and fill in secrets before starting.

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

*Last updated: project setup. Update this file when major architecture decisions change.*
