# GeoProspector — Initial Scaffold Prompt

> **How to use this:** Paste this entire prompt into a new Claude Code session (or a new conversation with Claude) to generate the initial project scaffold. It will create the full directory structure, boilerplate files, Docker setup, and stub implementations for every major module.

---

## Prompt

You are helping me build **GeoProspector**, a multi-agent AI application for mineral prospecting. It ingests geological, geochemical, remote sensing, and historical mining data from online sources, then runs specialist AI agents over a user-defined area of interest (AOI) to identify and rank the highest-probability locations for a target mineral (e.g., gold, silver, copper).

Please scaffold the complete initial project structure. Here is the full specification:

---

### Stack
- **Backend:** Python 3.11, FastAPI (async), SQLAlchemy 2.0 (async), Alembic, Celery, Redis
- **Database:** PostgreSQL 15 + PostGIS 3.4
- **Agent Framework:** LangGraph (preferred) or CrewAI — set up for multi-agent coordination
- **LLM:** Anthropic Claude via `anthropic` Python SDK
- **Storage:** boto3 (S3-compatible, local dev uses MinIO)
- **Frontend:** React 18 + TypeScript, MapLibre GL JS, Zustand for state, Tailwind CSS
- **Tileserver:** Martin (Docker image) pointing at PostGIS
- **Containerization:** Docker + Docker Compose for all services

---

### Services in docker-compose.yml
1. `postgres` — PostgreSQL 15 + PostGIS, port 5432
2. `redis` — Redis 7, port 6379
3. `minio` — MinIO object storage, ports 9000 (API) + 9001 (console)
4. `backend` — FastAPI app, port 8000, hot reload in dev
5. `worker` — Celery worker (same image as backend)
6. `tileserver` — Martin tile server, port 3000
7. `frontend` — React dev server, port 5173

---

### Backend: Create these files with full stub implementations

**`backend/app/main.py`**
- FastAPI app with CORS, lifespan handler (DB pool init)
- Include routers: channels, features, analysis
- Health check endpoint `GET /health`

**`backend/app/db/session.py`**
- Async SQLAlchemy engine + session factory
- PostGIS extension enablement on first connect

**`backend/app/models/`** — SQLAlchemy ORM models with PostGIS geometry columns (use `geoalchemy2`):

`feature.py`:
```
Feature(id, source_channel, source_record_id, feature_type, name, 
        geometry [Point/Polygon], commodity_primary, commodity_secondary[],
        deposit_type, status, geologic_unit, rock_type, 
        geochemical_values JSONB, source_quality float,
        ingested_at, raw_record_ref)
```

`channel.py`:
```
Channel(id, name, source_type, endpoint, auth_config JSONB,
        refresh_schedule, spatial_coverage JSONB, data_type,
        normalization_profile, last_synced_at, is_active)
```

`analysis_job.py`:
```
AnalysisJob(id, status, aoi_geojson JSONB, target_mineral,
            config JSONB, agent_results JSONB, final_scores JSONB,
            created_at, completed_at, error_message)
```

`agent_result.py` — Pydantic model (not DB):
```
AgentResult(agent_id, status, scored_cells[], agent_notes, warnings[])
ScoredCell(cell_id, geometry, score, confidence, evidence[], data_sources_used[])
```

**`backend/app/api/channels.py`**
- `POST /api/v1/channels` — create channel
- `GET /api/v1/channels` — list channels
- `POST /api/v1/channels/{id}/sync` — enqueue sync task

**`backend/app/api/features.py`**
- `GET /api/v1/features` — query features with params: `bbox`, `commodity`, `feature_type`, `limit`, `offset`
- Returns GeoJSON FeatureCollection
- `GET /api/v1/features/{id}` — single feature

**`backend/app/api/analysis.py`**
- `POST /api/v1/analysis/jobs` — create and enqueue analysis job
- `GET /api/v1/analysis/jobs/{id}` — job status + results
- `GET /api/v1/analysis/jobs/{id}/events` — SSE endpoint for real-time agent progress

**`backend/app/agents/base_agent.py`**
Base class for all specialist agents:
```python
class BaseAgent:
    agent_id: str
    agent_name: str
    
    async def run(self, aoi_geojson, target_mineral, spatial_context, config) -> AgentResult:
        raise NotImplementedError
    
    def build_prompt(self, aoi_geojson, target_mineral, spatial_context) -> str:
        raise NotImplementedError
    
    async def call_llm(self, prompt) -> str:
        # Anthropic API call stub
        pass
    
    def parse_llm_response(self, response, grid_cells) -> list[ScoredCell]:
        raise NotImplementedError
```

**`backend/app/agents/orchestrator.py`**
```python
class OrchestratorAgent:
    agents: list[BaseAgent]
    
    async def run_analysis(self, job_id, aoi_geojson, target_mineral, config):
        # 1. Generate grid cells for AOI
        # 2. Query PostGIS for spatial context per agent domain
        # 3. Fan out to all agents in parallel (asyncio.gather)
        # 4. Collect AgentResults
        # 5. Pass to ScoringEngine
        # 6. Save final scores to AnalysisJob
        # 7. Emit SSE events throughout
        pass
```

**`backend/app/agents/`** — Create stub files for each agent (inherits BaseAgent, has placeholder `build_prompt` and `parse_llm_response`):
- `lithology_agent.py`
- `structure_agent.py`
- `proximity_agent.py`
- `geochemistry_agent.py`
- `remote_sensing_agent.py`
- `historical_agent.py`

**`backend/app/connectors/base_connector.py`**
```python
class BaseConnector:
    channel_config: Channel
    
    async def fetch(self, bbox=None) -> list[dict]:
        raise NotImplementedError
    
    async def normalize(self, raw_records) -> list[Feature]:
        raise NotImplementedError
```

**`backend/app/connectors/`** — Stub connector files (each inherits BaseConnector with correct endpoint URLs and field mapping stubs):
- `usgs_mrds.py` — endpoint: `https://mrdata.usgs.gov/services/mrds`, WFS GetFeature
- `blm_mlrs.py` — endpoint: `https://mlrs.blm.gov` (scrape/download stub)
- `glo_records.py` — endpoint: `https://glorecords.blm.gov` 
- `usgs_ngdb.py` — endpoint: `https://mrdata.usgs.gov/geochem`
- `macrostrat.py` — endpoint: `https://macrostrat.org/api/v2`
- `mindat.py` — endpoint: `https://api.mindat.org`

**`backend/app/pipeline/ingest.py`**
- Celery task `sync_channel(channel_id)` — fetch → normalize → upsert to PostGIS
- Celery task `run_analysis_job(job_id)` — trigger orchestrator

**`backend/app/scoring/grid.py`**
- `generate_grid(aoi_geojson, resolution_m) -> list[GridCell]`
- Uses shapely to divide AOI polygon into grid cells

**`backend/app/scoring/engine.py`**
- `synthesize(agent_results, grid_cells, weights, config) -> list[ScoredCell]`
- Implements confidence-weighted mean formula
- Assigns tier labels (high/medium/low/negligible)

**`backend/app/scoring/weights.py`**
- `DEFAULT_WEIGHTS` dict keyed by mineral name, values are per-agent weights as defined in system design

**`backend/celery_worker.py`**
- Celery app init with Redis broker

**`backend/requirements.txt`**
```
fastapi>=0.111.0
uvicorn[standard]
sqlalchemy[asyncio]>=2.0
asyncpg
geoalchemy2
alembic
celery[redis]
redis
anthropic
langgraph
boto3
shapely
pyproj
fiona
geopandas
httpx
pydantic>=2.0
python-dotenv
```

**`backend/Dockerfile`** — Python 3.11 slim, installs GDAL system deps, pip installs requirements

---

### Alembic Setup
- Initialize alembic in `backend/alembic/`
- Create initial migration that creates all tables with PostGIS geometry columns
- Add `CREATE EXTENSION IF NOT EXISTS postgis` to migration

---

### Frontend: Create this structure with stubs

**`frontend/src/App.tsx`** — Main app layout: sidebar + full-screen map

**`frontend/src/components/Map/MapView.tsx`**
- MapLibre GL JS map initialized with `style: 'https://demotiles.maplibre.org/style.json'`
- Placeholder draw tool for AOI polygon
- Layer stubs for: features layer (from Martin), results grid layer

**`frontend/src/components/AnalysisPanel/AnalysisPanel.tsx`**
- Mineral selector (dropdown)
- Resolution selector
- Weight sliders (one per agent, default from preset)
- Run button → calls `POST /api/v1/analysis/jobs`
- Progress bar reading from SSE stream

**`frontend/src/components/ResultsOverlay/ResultsOverlay.tsx`**
- Receives scored GeoJSON grid
- Renders choropleth layer on map
- Tier legend

**`frontend/src/components/EvidenceDrawer/EvidenceDrawer.tsx`**
- Slide-in panel when a cell is clicked
- Shows agent breakdown: score bars, evidence list, data sources, data gaps

**`frontend/src/components/ChannelDashboard/ChannelDashboard.tsx`**
- Table of channels with status, last sync, sync button

**`frontend/src/api/client.ts`**
- Typed API client with all endpoints
- SSE hook for job progress

**`frontend/src/store/index.ts`**
- Zustand store: `{ aoi, targetMineral, currentJob, analysisResults, activeView }`

**`frontend/package.json`** — Include: react, react-dom, maplibre-gl, zustand, tailwindcss, typescript, vite, @types/react

---

### Configuration Files
- `.env.example` — all env vars from system design
- `.gitignore` — standard Python + Node + .env
- `docker-compose.yml` — all 7 services wired together
- `docker-compose.dev.yml` — overrides for hot reload

---

### Martin Tileserver Config
- `tileserver/config.yaml` — connect to PostGIS, expose `features` table as vector tile source at `/features/{z}/{x}/{y}`

---

### README.md
Include:
1. Project overview (2 paragraphs)
2. Prerequisites (Docker, Docker Compose)
3. Quickstart: `cp .env.example .env` → `docker-compose up`
4. After first run: how to run Alembic migrations
5. Development workflow: how to add a new data connector, how to add a new agent
6. Link to `docs/01_system_design.md` for architecture details

---

Please generate all files. For stub implementations, include meaningful comments explaining what each function should do when fully implemented. Do not leave empty files — every file should have enough structure to understand its role and serve as a solid starting point for implementation.
