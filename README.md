# GeoProspector

GeoProspector scores mineral prospectivity for a user-drawn area of interest in Washington
State. You draw a polygon on a map, pick a target mineral, and six specialist Claude agents
score a grid over it in parallel — each from a different angle (lithology, structure,
geochemistry, proximity, remote sensing, historical mining). A synthesis engine combines
their per-cell scores into a composite, ranks every cell against the rest of the AOI, and
renders the result as a color-coded grid you can click into for the evidence behind any score.

FastAPI + PostGIS backend, React + MapLibre frontend, Anthropic `claude-sonnet-4-6`.

> **Scope:** Washington State, gold. The scoring engine handles five minerals, but the
> agent knowledge base is WA gold only. See [Current state](#current-state) before reading
> anything into a score.

---

## Current state

This is a working prototype, not a finished product. What's real:

- Draw → analyze → scored grid → per-cell evidence drilldown works end to end
- Six agents run concurrently, batched at 50 cells per LLM call with truncated-JSON repair
- Two-level grid: agents score a coarse grid (≤150 cells), results IDW-interpolate down to
  a display grid as fine as 100 m
- AOI-relative scoring — percentile tiers within your polygon, with a Relative/Absolute toggle

What isn't, and matters:

| Gap | Impact |
|---|---|
| Only `lithology` and `historical` have knowledge files | The other four agents run with **no system prompt** while carrying 0.60 of the gold weight |
| Spatial-context DB query fails under `DEV_MODE` | Agents get no data from the database and score from model priors alone |
| No tests, no CI, `npm run lint` is broken | Scoring math is unverified by anything but inspection |
| `blm_mlrs` and `glo_records` connectors are stubs | `fetch()` returns `[]` |
| MinIO and LangGraph are provisioned but unused | Ignore them; there is no object storage and no agent framework |

`CLAUDE.md` has the full accounting under **Known Gaps**. Treat current output as a
research aid, not an exploration target.

---

## Quickstart — no Docker

This is the fastest path and the one the frontend is actually built against.

```bash
# Prerequisites: Python 3.11+, Node 20+, an Anthropic API key
pip install -r backend/requirements-dev.txt
cd frontend && npm install && cd ..

./run-dev.sh
```

Opens the API on `:8000` and the UI on `:5173`. Draw a polygon (25 km² minimum), paste your
Anthropic key into the panel, pick gold, and run.

`run-dev.sh` forces `DEV_MODE=true`, which means:

- Analysis runs **in-process** — no Postgres, Redis, or Celery required
- Your API key travels in the request body from the browser, not from `.env`
- Nothing is persisted; results live in browser memory and are lost on reload
- The **Channels tab will 404** — data ingestion needs the full stack

---

## Full stack — Docker

Needed for data ingestion, job persistence, and vector tiles.

```bash
cp .env.example .env
# set ANTHROPIC_API_KEY=sk-ant-... and DEV_MODE=false
docker-compose up --build
docker-compose exec backend alembic upgrade head   # only meaningful with DEV_MODE=false
```

| Service | URL | Compose name |
|---|---|---|
| Frontend | http://localhost:5173 | `frontend` |
| Backend API | http://localhost:8000 | `backend` |
| API docs | http://localhost:8000/docs | |
| Tileserver | http://localhost:3000 | `tileserver` |
| MinIO console | http://localhost:9001 | `minio` |
| Postgres | localhost:**5433** | `postgres` |
| Celery worker | — | `worker` |

Hot-reload overlay — **both** `-f` flags are required, `docker-compose.dev.yml` has no
build context of its own:

```bash
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

Three things that reliably trip people up: Postgres is on host port **5433**, the Celery
service is named `worker` (not `celery_worker`), and the database service is `postgres`
(not `db`).

---

## How scoring works

Each agent returns a `score` and a `confidence` per cell plus human-readable evidence
strings. The engine takes a **confidence-weighted mean** across agents, using
mineral-specific weights from `backend/app/scoring/weights.py` (for gold: structure 0.30,
lithology 0.25, geochemistry 0.20, historical 0.15, remote sensing 0.07, proximity 0.03).

Tiers are **relative to your AOI**, not absolute. The grid answers "where are the best
spots in this polygon," not "how does this compare to the world." After synthesis, each
cell gets a percentile rank within the AOI:

| Tier | Percentile |
|---|---|
| High | ≥ 0.90 |
| Medium | ≥ 0.65 |
| Low | ≥ 0.35 |
| Negligible | < 0.35 |

The absolute composite is preserved on every cell — toggle Relative/Absolute in the map
legend. A uniform AOI gets flat mid-tone shading rather than invented hotspots.

Cells the LLM skipped come back with `confidence=0.0` and are ignored by the weighted mean,
which is what keeps a partial response from dragging the whole grid to zero.

---

## Development

### Adding a data connector

1. Create `backend/app/connectors/my_source.py`, subclass `BaseConnector`
2. Implement `async fetch(bbox)` and `async normalize(raw_records)`
3. Register the key in `CONNECTOR_REGISTRY` in `backend/app/pipeline/ingest.py`
4. `POST /api/v1/channels` with `source_type = "my_source"`, then
   `POST /api/v1/channels/{id}/sync` (both require `DEV_MODE=false`)

### Adding a specialist agent

1. Create `backend/app/agents/my_agent.py`, subclass `BaseAgent`
2. Set `agent_id`, `agent_name`, `knowledge_domain`; implement `build_prompt()`. That's the
   only method you need — **don't** override `parse_llm_response()`, the shared one in
   `base_agent.py` handles JSON repair for every agent.
3. Register in the `AGENT_CLASSES` dict in `backend/app/agents/orchestrator.py`
4. Add a weight for it in every mineral preset in `backend/app/scoring/weights.py`
5. Add the id to `AGENTS` in `AnalysisPanel.tsx` so the weight slider appears
6. Write `backend/app/agents/knowledge/<domain>/<mineral>.md`. Skipping this doesn't error —
   the agent silently runs with no system prompt and still contributes full weight.

### Useful commands

```bash
cd frontend && npm run typecheck    # npm run lint does NOT work — no eslint config
docker-compose logs -f worker
docker-compose exec postgres psql -U geoprospector -d geoprospector -c "SELECT PostGIS_Version();"
```

---

## Data

`data/raw/` holds ~608 MB of WA DNR and USGS geodatabases, and `data/literature/` holds
~177 MB of scanned reports. Both are **gitignored** — one `.e00` alone exceeds GitHub's
100 MB limit. See [`data/README.md`](data/README.md) for sources and re-download steps.

Note that no code currently reads either directory; the live connectors fetch from web APIs.
The local datasets are staged ahead of the ingestion work in
[`docs/06_data_sourcing_checklist.md`](docs/06_data_sourcing_checklist.md).

---

## Docs

| File | What it covers |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Architecture reference and known gaps — start here |
| [`docs/01_system_design.md`](docs/01_system_design.md) | Full system design and data flow |
| [`docs/03_implementation_plan.md`](docs/03_implementation_plan.md) | Milestones |
| [`docs/04_usgs_of00_495_dataset.md`](docs/04_usgs_of00_495_dataset.md) | NE WA weights-of-evidence raster plan |
| [`docs/06_data_sourcing_checklist.md`](docs/06_data_sourcing_checklist.md) | Dataset inventory and status |
| [`docs/intake_analyses/`](docs/intake_analyses/) | Per-source extracts from the scanned literature |
| [`.claude/mistakes-log.md`](.claude/mistakes-log.md) | Bugs worth not repeating |

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic |
| Database | PostgreSQL 15 + PostGIS 3.4 |
| Task queue | Celery + Redis (prod path only) |
| Agents | Anthropic `claude-sonnet-4-6` via `asyncio.gather` — no agent framework |
| Tileserver | Martin |
| Frontend | React 18 + TypeScript, MapLibre GL JS, `@mapbox/mapbox-gl-draw`, `@turf/area`, Zustand |
| Styling | Tailwind CSS |
| Build | Vite, Docker Compose |
