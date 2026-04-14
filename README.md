# GeoProspector

GeoProspector is a multi-agent AI application for mineral prospecting. It ingests geological, geochemical, remote sensing, and historical mining data from public online sources, then orchestrates a team of specialist AI agents over a user-defined area of interest (AOI) to identify and rank the highest-probability locations for a target mineral (e.g., gold, silver, copper). Results are rendered as a scored, color-coded grid on an interactive map.

The system is built on a FastAPI + PostgreSQL/PostGIS backend, a React + MapLibre GL JS frontend, and a LangGraph-compatible multi-agent framework powered by the Anthropic Claude API. Each specialist agent (lithology, structure, geochemistry, proximity, remote sensing, historical) produces per-cell scores that a synthesis engine combines into a composite prospectivity map.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) 24+
- [Docker Compose](https://docs.docker.com/compose/install/) v2.20+
- An [Anthropic API key](https://console.anthropic.com/)

---

## Quickstart

```bash
# 1. Clone and enter the project
git clone <repo-url> geoprospector
cd geoprospector

# 2. Configure environment
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 3. Start all services
docker-compose up --build
```

Services will be available at:
| Service       | URL                          |
|---------------|------------------------------|
| Frontend      | http://localhost:5173        |
| Backend API   | http://localhost:8000        |
| API Docs      | http://localhost:8000/docs   |
| Tileserver    | http://localhost:3000        |
| MinIO Console | http://localhost:9001        |

---

## After first run — run database migrations

```bash
# Run Alembic migrations inside the backend container
docker-compose exec backend alembic upgrade head
```

This creates all tables and enables the PostGIS extension.

---

## Development workflow

### Adding a new data connector

1. Create `backend/app/connectors/my_source.py` inheriting from `BaseConnector`
2. Implement `fetch()` and `normalize()` — see existing connectors for field mapping examples
3. Register the connector in `CONNECTOR_REGISTRY` in `backend/app/pipeline/ingest.py`
4. Create a `Channel` record via `POST /api/v1/channels` with `source_type = "my_source"`
5. Trigger a sync via `POST /api/v1/channels/{id}/sync`

### Adding a new specialist agent

1. Create `backend/app/agents/my_agent.py` inheriting from `BaseAgent`
2. Implement `build_prompt()` (craft a domain-specific LLM prompt) and `parse_llm_response()` (parse JSON into `ScoredCell` objects)
3. Register the agent in `OrchestratorAgent.agents` in `backend/app/agents/orchestrator.py`
4. Add a default weight entry in `backend/app/scoring/weights.py` for each target mineral
5. Add the agent ID to the `AGENTS` list in `frontend/src/components/AnalysisPanel/AnalysisPanel.tsx` so the weight slider appears

---

## Architecture

See [`docs/01_system_design.md`](docs/01_system_design.md) for the full system design, data flow diagrams, and agent architecture.

---

## Stack

| Layer        | Technology                                      |
|--------------|-------------------------------------------------|
| Backend      | Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic   |
| Database     | PostgreSQL 15 + PostGIS 3.4                     |
| Task queue   | Celery + Redis                                  |
| AI agents    | LangGraph, Anthropic Claude (claude-sonnet-4-6) |
| Object store | MinIO (S3-compatible)                           |
| Tileserver   | Martin (MapLibre)                               |
| Frontend     | React 18 + TypeScript, MapLibre GL JS, Zustand  |
| Styling      | Tailwind CSS                                    |
| Container    | Docker + Docker Compose                         |
