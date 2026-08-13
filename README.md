# GeoProspector

Draw a polygon in Washington State, pick a target mineral, and six specialist Claude agents
score a grid over it in parallel — lithology, structure, geochemistry, proximity, remote
sensing, historical mining. Each agent sees the actual mapped geology, structures and
recorded occurrences under every cell. A synthesis engine combines their scores into a
composite, ranks each cell against the rest of your AOI, and renders a grid you can click
into for the evidence behind any number.

A deterministic weights-of-evidence baseline scores the same cells with no model in the
loop, so the LLM composite can be checked against a published statistical model rather than
taken on faith.

FastAPI + React/MapLibre, Anthropic `claude-sonnet-4-6`.

> **Scope: Washington State, gold.** The engine handles five minerals, but the agent
> knowledge base is WA gold only — ask for silver or copper and all six agents run with no
> system prompt. Read [What works, what doesn't](#what-works-what-doesnt) before reading
> anything into a score.

---

## Quickstart

Python 3.11+ (3.14 works), Node 20+, an Anthropic API key. No Docker needed — this is the
path the frontend is built against and the one to use day to day.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd frontend && npm install && cd ..

./run-dev.sh
```

Backend on `:8000`, frontend on `:5173`. Draw a polygon (25 km² minimum), paste your
Anthropic key into the panel, pick gold, run.

**Activate the venv first.** `run-dev.sh` calls bare `uvicorn`, so without an active venv it
picks up system Python and dies on the `shapely` / `pyproj` imports.

### Build the evidence base — one time, ~25 min

Optional but strongly recommended. Without it the app still runs, but lithology, structure,
historical and proximity fall back to model prior alone, and the map's mine layers stay
greyed out.

```bash
.venv/bin/python scripts/build_reference_extracts.py all   # occurrences, districts, IAML
.venv/bin/python scripts/build_geology_store.py            # WA DNR 1:24k geology
.venv/bin/python scripts/build_of00495.py                  # NE WA WofE grids
```

Needs `data/raw/` populated first — see [`data/README.md`](data/README.md) for sources and
download steps. Outputs land in `data/derived/`, which is gitignored: large, machine-built,
and reproducible from the above. They survive a `git reset`, so you build them once.

### Check what the agents can actually see

Before spending tokens:

```bash
curl -s localhost:8000/api/v1/reference/layers | python3 -m json.tool
```

`geology_store: false` or `wofe_store: false` means the derived stores aren't built. Note
that an artifact *existing* is not the same as it *covering your AOI* — the run log's
`spatial_context` line reports coverage for the polygon you actually drew, and the 1:24k
geology has real holes where you most want it (Known Gap #2b in `CLAUDE.md`).

### What DEV_MODE means

`run-dev.sh` forces `DEV_MODE=true`:

- Analysis runs in-process — no Postgres, Redis or Celery
- Your API key travels in the request body from the browser, not from `.env`
- Nothing is persisted; results live in browser memory and are lost on reload
- The **Channels tab 404s** — data ingestion needs the full stack

### If the frontend shows ECONNREFUSED

The backend died and Vite is reporting it as a proxy error. `set -e` doesn't fire for
background jobs, and under `--reload` uvicorn's reloader survives an import error while the
worker dies — so the process looks alive. `run-dev.sh` now polls `/health` and exits with a
pointer to the traceback, but to see it directly:

```bash
source .venv/bin/activate && cd backend && uvicorn app.main:app --port 8000
```

---

## What works, what doesn't

Working end to end: draw → analyze → scored grid → per-cell evidence drilldown. Six agents
run concurrently, batched at 50 cells per LLM call with truncated-JSON repair. Two-level
grid — agents score a coarse grid (≤150 cells), results IDW-interpolate down to a display
grid as fine as 100 m. Scoring is AOI-relative, with a Relative/Absolute toggle.

| Gap | Impact |
|---|---|
| Knowledge base is **gold only** | Any other mineral runs all six agents with `system=None`. No `default.md` exists. |
| 1:24k geology has **coverage holes** | No data at Monte Cristo, Sultan Basin, Lennox Creek, North Fork Snoqualmie. An empty `geology` fact is not barren ground. |
| PostGIS path still dead under `DEV_MODE` | No longer load-bearing — agents read local files first — but the prod path needs `asyncpg` before it works. |
| `blm_mlrs` and `glo_records` connectors are stubs | `fetch()` returns `[]`. |
| No CI; `npm run lint` broken | Tests exist and pass locally, but nothing runs them automatically. No eslint config. |
| MinIO and LangGraph provisioned but unused | Ignore them — no object storage, no agent framework. |

`CLAUDE.md` has the full accounting under **Known Gaps**, including what closed and when.
Treat current output as a research aid, not an exploration target.

---

## How scoring works

Each agent returns a `score` and a `confidence` per cell plus human-readable evidence
strings. The engine takes a **confidence-weighted mean** across agents, using
mineral-specific weights from `backend/app/scoring/weights.py` — for gold: structure 0.30,
lithology 0.25, geochemistry 0.20, historical 0.15, remote sensing 0.07, proximity 0.03.

Tiers are **relative to your AOI**, not absolute. The grid answers "where are the best spots
in this polygon," not "how does this compare to the world."

| Tier | Percentile |
|---|---|
| High | ≥ 0.90 |
| Medium | ≥ 0.65 |
| Low | ≥ 0.35 |
| Negligible | < 0.35 |

The absolute composite is preserved on every cell — toggle Relative/Absolute in the map
legend. A uniform AOI gets flat mid-tone shading rather than invented hotspots. Cells the
LLM skipped return `confidence=0.0` and are ignored by the weighted mean, which keeps a
partial response from dragging the grid to zero.

### The deterministic baseline

`backend/app/scoring/wofe_baseline.py` reimplements USGS OF01-501 (Boleneus et al., 2001) —
weights-of-evidence for epithermal gold in NE Washington — over the raster data that study
was fitted on. No model in the loop. It answers two questions a composite alone cannot:
whether the LLM ranks cells the way a fitted statistical model does, and whether either
ranks recorded workings above the ground around them.

It refuses to score outside its fitted area rather than extrapolating. The North Cascades
districts are orogenic gold in metamorphic rocks; this model's six favourable units are
Eocene volcanic and do not exist there.

---

## Tests and benchmarks

```bash
cd backend && python -m pytest              # 13 test modules
python scripts/benchmark.py --noise-floor   # establish the noise floor FIRST
python scripts/benchmark.py --wofe-only     # deterministic baseline, zero tokens
```

`benchmark.py` runs offline against `data/runs/` — run records store absolute scores and
stable cell ids, and cell ids regenerate their own geometry, so historical runs re-score
without spending a token.

---

## Full stack — Docker

Needed only for data ingestion, job persistence and vector tiles. Not required for anything
above.

```bash
cp .env.example .env        # set ANTHROPIC_API_KEY, DEV_MODE=false
docker-compose up --build
docker-compose exec backend alembic upgrade head
```

| Service | URL | Compose name |
|---|---|---|
| Frontend | http://localhost:5173 | `frontend` |
| Backend API | http://localhost:8000 | `backend` |
| API docs | http://localhost:8000/docs | |
| Tileserver | http://localhost:3000 | `tileserver` |
| Postgres | localhost:**5433** | `postgres` |
| Celery worker | — | `worker` |

Hot-reload overlay — **both** `-f` flags are required, `docker-compose.dev.yml` has no build
context of its own:

```bash
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

Three things that reliably trip people up: Postgres is on host port **5433**, the Celery
service is named `worker` (not `celery_worker`), and the database service is `postgres`
(not `db`).

---

## Where things live

```
backend/app/agents/       six specialist agents + orchestrator
        app/agents/knowledge/<domain>/<mineral>.md   agent system prompts
        app/spatial/      per-cell evidence assembled from local files
        app/scoring/      synthesis engine, weights, WofE baseline
        app/connectors/   data source adapters
frontend/src/             React + MapLibre
scripts/build_*.py        offline data/raw/ → data/derived/ builders
data/                     see data/README.md
docs/                     design docs and source-literature extracts
```

---

## Docs

| File | What it covers |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Architecture, known gaps, conventions, gotchas — the deep reference |
| [`data/README.md`](data/README.md) | Every dataset: source, size, licence, re-download |
| [`docs/01_system_design.md`](docs/01_system_design.md) | Full system design and data flow |
| [`docs/04_usgs_of00_495_dataset.md`](docs/04_usgs_of00_495_dataset.md) | The NE WA weights-of-evidence rasters |
| [`docs/07_stable_cell_ids.md`](docs/07_stable_cell_ids.md) | Fixed EPSG:5070 grid and cell id scheme |
| [`docs/intake_analyses/`](docs/intake_analyses/) | Per-source extracts from the scanned literature |
| [`.claude/mistakes-log.md`](.claude/mistakes-log.md) | Bugs worth not repeating |

Extending the app — adding a connector, adding an agent — is documented under
**Development Conventions** in [`CLAUDE.md`](CLAUDE.md).

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic |
| Spatial runtime | `sqlite3` + `shapely` + `pyproj` — no PostGIS, no GDAL |
| Offline builders | `pyogrio` (GDAL bundled in the wheel) |
| Database | PostgreSQL 15 + PostGIS 3.4 (prod path only) |
| Task queue | Celery + Redis (prod path only) |
| Agents | Anthropic `claude-sonnet-4-6` via `asyncio.gather` — no agent framework |
| Frontend | React 18 + TypeScript, MapLibre GL JS, Zustand, Tailwind |
| Build | Vite, Docker Compose |
