# GeoProspector — Implementation Plan

> **How to use this:** Work through phases sequentially. Before starting any step, tell Claude: *"We are on Step X of the GeoProspector implementation plan. Here is the current codebase: [paste relevant files or use Claude Code]. Reference the system design in `docs/01_system_design.md`."*  
> Each step is scoped to be completable in one focused session.

---

## Phase 0 — Foundation (Days 1–2)

### Step 0.1 — Run scaffold & verify environment
- Paste `docs/02_scaffold_prompt.md` into Claude Code to generate full project skeleton
- Run `docker-compose up` and verify all 7 services start cleanly
- Confirm PostGIS extension is active: `SELECT PostGIS_Version();`
- Confirm Martin tileserver health: `GET http://localhost:3000/health`
- Confirm FastAPI docs load: `http://localhost:8000/docs`
- Confirm frontend dev server: `http://localhost:5173`
- **Exit criteria:** All services healthy, no errors in logs

### Step 0.2 — Database migrations
- Run `alembic upgrade head` to apply initial migration
- Verify tables exist with correct geometry columns
- Seed one test `Channel` record and one test `Feature` record manually via SQLAlchemy session
- Verify Feature is spatially queryable: `SELECT ST_AsGeoJSON(geometry) FROM features LIMIT 1;`
- **Exit criteria:** All tables created, spatial query works

### Step 0.3 — Martin tileserver serving features
- Confirm `features` table is exposed as `/features/{z}/{x}/{y}` via Martin
- Open frontend map and add a test vector tile source pointing at Martin
- Verify a feature point renders on the map at the correct location
- **Exit criteria:** Tile layer visible on map

---

## Phase 1 — Data Ingestion: First Connector (Days 3–5)

### Step 1.1 — Implement USGS MRDS connector
- Implement `backend/app/connectors/usgs_mrds.py`
- Use WFS GetFeature endpoint: `https://mrdata.usgs.gov/services/mrds?service=WFS&request=GetFeature&typeName=mrds&bbox={bbox}&outputFormat=json`
- Parse response GeoJSON → normalize to canonical `Feature` schema
- Map MRDS fields: `dep_id`, `name`, `commod1` (primary commodity), `lat`, `lon`, `oper_type`, `dev_stat`
- Handle pagination (MRDS WFS has result limits — implement paging loop)
- Write unit test: fetch a small known bbox (e.g. around Cripple Creek, CO), assert >0 gold features returned
- **Exit criteria:** Can fetch and parse MRDS features for a test bbox

### Step 1.2 — Implement ingestion pipeline task
- Implement `backend/app/pipeline/ingest.py` Celery task `sync_channel(channel_id)`
- Task flow: load channel config → instantiate connector → fetch(bbox from channel config) → normalize → upsert to PostGIS (use `ON CONFLICT DO UPDATE` on `source_record_id + source_channel`)
- Add progress logging: log count of records fetched, inserted, updated, skipped
- Test by triggering `sync_channel` for the MRDS channel via Celery
- Verify records appear in PostGIS after task completes
- **Exit criteria:** 100+ MRDS gold features ingested and queryable in PostGIS

### Step 1.3 — Wire up channels API + frontend dashboard
- Complete `backend/app/api/channels.py` CRUD endpoints
- Complete `frontend/src/components/ChannelDashboard/ChannelDashboard.tsx`
- Implement "Sync Now" button → calls `POST /api/v1/channels/{id}/sync` → shows task status
- Display `last_synced_at` and record count per channel
- **Exit criteria:** Can create a channel via UI and trigger sync; see count update

### Step 1.4 — Implement features query API + map layer
- Complete `backend/app/api/features.py` with bbox-filtered GeoJSON response
- Add viewport-based fetching in frontend map (query features API on map move/zoom)
- Render MRDS points as a styled layer on the map (color by commodity)
- Implement basic popup on click showing feature name, commodity, status
- **Exit criteria:** MRDS points render on map; click shows detail popup

---

## Phase 2 — Data Ingestion: Remaining Core Connectors (Days 6–10)

### Step 2.1 — BLM MLRS active claims connector
- Implement `backend/app/connectors/blm_mlrs.py`
- Use BLM MLRS public reports / GeoJSON download endpoint (check `mlrs.blm.gov` for current API surface — may require bulk file download + parse)
- Normalize to canonical schema with `feature_type = CLAIM`, `status = active`
- **Exit criteria:** Active federal mining claims ingested for a test state (e.g. Nevada)

### Step 2.2 — GLO historical patents connector
- Implement `backend/app/connectors/glo_records.py`
- Target: mineral patent records from `glorecords.blm.gov`
- Normalize: patent number, patent date, grantee name, legal land description, geometry (PLSS section polygon)
- Handle PLSS → geometry conversion (use `plss` Python lib or USGS PLSS WFS)
- **Exit criteria:** Historical mineral patents ingested for a test county

### Step 2.3 — USGS NGDB geochemistry connector
- Implement `backend/app/connectors/usgs_ngdb.py`
- Endpoint: `https://mrdata.usgs.gov/geochem` — download CSV or WFS
- Normalize: sample_id, lat, lon, Au_ppb, As_ppm, Sb_ppm, Hg_ppm, sample_medium (stream sediment / soil)
- Store geochemical values in `geochemical_values` JSONB column
- **Exit criteria:** Geochemical sample points ingested for a test region; queryable by element

### Step 2.4 — Macrostrat geology connector
- Implement `backend/app/connectors/macrostrat.py`
- Endpoint: `https://macrostrat.org/api/v2/geologic_units/map?lat={lat}&lng={lng}&adjacents=true`
- Also: `GET /api/v2/units?lat=&lng=` for column data
- Normalize: map unit name, lithology description, age, environment
- **Exit criteria:** Geologic unit data queryable for a test point/bbox

### Step 2.5 — USGS Historical Topo mine symbols connector
- Implement connector for USGS historical topo mine symbols shapefile
- Download from `mrdata.usgs.gov` — available as state-by-state shapefiles
- Parse shapefile with Fiona/GeoPandas → normalize to `feature_type = MINE_SYMBOL`
- Include symbol type (adit, shaft, pit, dump, tailings) in `deposit_type` field
- **Exit criteria:** Mine symbols ingested for at least 3 western states

---

## Phase 3 — Grid System & Scoring Foundation (Days 11–13)

### Step 3.1 — Grid generation
- Fully implement `backend/app/scoring/grid.py`
- `generate_grid(aoi_geojson, resolution_m)` → list of GeoJSON polygon cells
- Use Shapely + pyproj: reproject AOI to local UTM, generate regular grid, clip to AOI, reproject back to WGS84
- Assign each cell a unique `cell_id` (grid ref string: `{col}_{row}`)
- Test: generate grid for a 10km² test AOI at 250m resolution; verify expected cell count
- **Exit criteria:** Grid generation working for any polygon; cells fully cover AOI

### Step 3.2 — Scoring engine
- Fully implement `backend/app/scoring/engine.py`
- `synthesize(agent_results, grid_cells, weights)` → list of `ScoredCell` with composite score
- Implement confidence-weighted mean formula from system design
- Implement tier classification (high/medium/low/negligible)
- Implement `top_evidence` extraction (top 3 evidence strings by agent score)
- Implement `data_gaps` detection (agents with no data for cell)
- Unit test: mock 3 agent results → verify composite scores are mathematically correct
- **Exit criteria:** Scoring engine produces correct weighted composite scores

### Step 3.3 — Mineral weight presets
- Fully implement `backend/app/scoring/weights.py`
- Define default weight dicts for: gold, silver, copper, lithium as per system design
- Implement `get_weights(mineral, overrides=None)` to merge user overrides onto defaults
- **Exit criteria:** Weight presets loadable; user can override per-agent weights

---

## Phase 4 — Agent Implementation (Days 14–22)

> For each agent: implement `build_prompt()`, `call_llm()`, and `parse_llm_response()`. Test against a real AOI before moving to the next agent.

### Step 4.1 — Proximity Agent (start here — most straightforward)
- Implement `backend/app/agents/proximity_agent.py`
- Spatial context: query PostGIS for all `MINE`, `PROSPECT`, `CLAIM` features intersecting AOI + 5km buffer
- Per cell: compute distance to nearest known occurrence, count of occurrences within 500m/1km/2km/5km
- This agent can be implemented **without LLM** — pure spatial scoring (distance decay formula)
- Score formula: `1.0 / (1 + distance_km * 0.5)`, boosted by occurrence count and production status
- **Exit criteria:** Proximity agent returns scored cells with evidence for test AOI

### Step 4.2 — Historical Records Agent
- Implement `backend/app/agents/historical_agent.py`
- Spatial context: BLM claims density by section, GLO mineral patents, topo mine symbols
- Per cell: claim density score + patent presence bonus + mine symbol density
- Can be implemented without LLM (spatial statistics) — LLM optional for summarizing historical context
- **Exit criteria:** Historical agent returns scored cells with evidence

### Step 4.3 — Geochemistry Agent
- Implement `backend/app/agents/geochemistry_agent.py`
- Spatial context: NGDB samples within AOI + 10km buffer
- Per cell: IDW (Inverse Distance Weighting) interpolation of Au, As, Sb values
- Multi-element anomaly composite (z-score normalization + sum)
- LLM used to: interpret the anomaly pattern and generate human-readable evidence strings
- Claude prompt: provide element values, ask for interpretation in context of gold mineralization
- **Exit criteria:** Geochemistry agent scores cells; evidence strings are geologically sensible

### Step 4.4 — Lithology Agent
- Implement `backend/app/agents/lithology_agent.py`
- Spatial context: Macrostrat geologic units intersecting AOI; USGS NGMDB map units
- Per cell: identify intersecting geologic units
- Claude prompt: given unit name, lithology description, and target mineral, score favorability (0–1) and explain
- Return score + evidence per cell
- **Exit criteria:** Lithology agent produces mineral-appropriate scoring (high for favorable rocks)

### Step 4.5 — Structure Agent
- Implement `backend/app/agents/structure_agent.py`
- Spatial context: fault/lineament datasets (USGS structural geology data), fold axes
- Per cell: proximity to faults, intersection count, fault type classification
- Claude prompt: given structural context description, score as fluid conduit / trap favorability
- **Exit criteria:** Structure agent scores cells; fault intersections score high

### Step 4.6 — Remote Sensing Agent
- Implement `backend/app/agents/remote_sensing_agent.py`
- **Option A (MVP):** Use ASTER pre-processed alteration maps (download from USGS) — spatial lookup only, no real-time processing
- **Option B (full):** Call rasterio microservice to compute ASTER band ratios for AOI on demand
- Start with Option A; upgrade later
- Per cell: lookup pre-computed alteration score from raster
- **Exit criteria:** Remote sensing agent returns alteration scores for cells

### Step 4.7 — Orchestrator
- Fully implement `backend/app/agents/orchestrator.py`
- Fan out all 6 agents in parallel using `asyncio.gather`
- Emit SSE events at each stage: `{ event: "agent_complete", data: { agent_id, cells_scored } }`
- Handle agent failures gracefully (failed agent excluded from scoring, flagged in output)
- After all agents complete, call scoring engine, save to `AnalysisJob.final_scores`
- **Exit criteria:** Full end-to-end analysis job runs for a test AOI and produces scored output

---

## Phase 5 — Analysis API & Job Plumbing (Days 23–25)

### Step 5.1 — Analysis job submission & status API
- Fully implement `backend/app/api/analysis.py`
- `POST /api/v1/analysis/jobs` — validate AOI, create job, enqueue Celery task
- `GET /api/v1/analysis/jobs/{id}` — return job status + final scores (GeoJSON)
- **Exit criteria:** Can submit job via API; poll for completion; receive scored GeoJSON

### Step 5.2 — SSE progress stream
- Implement SSE endpoint `GET /api/v1/analysis/jobs/{id}/events`
- Use Redis pub/sub: orchestrator publishes events; SSE endpoint subscribes and streams
- Events: `job_started`, `agent_started`, `agent_complete`, `scoring_started`, `job_complete`, `job_failed`
- **Exit criteria:** SSE stream delivers agent-by-agent progress in real time

### Step 5.3 — Analysis Celery task wiring
- Wire `run_analysis_job(job_id)` Celery task to call orchestrator
- Implement retry logic (max 2 retries on transient failures)
- Update `AnalysisJob.status` at each stage: `queued → running → complete | failed`
- **Exit criteria:** Job runs end-to-end via Celery worker

---

## Phase 6 — Frontend: Analysis Flow (Days 26–30)

### Step 6.1 — AOI draw tool
- Implement free-draw polygon tool in `MapView.tsx` using MapLibre Draw plugin (`@mapbox/maplibre-gl-draw`)
- Store drawn polygon in Zustand `aoi` state
- Show polygon on map; allow redraw
- **Exit criteria:** User can draw, see, and reset an AOI polygon

### Step 6.2 — Analysis panel & job submission
- Complete `AnalysisPanel.tsx`
- Mineral dropdown, resolution selector, weight sliders
- Submit button → POST job → store `job_id` in Zustand
- Connect to SSE stream → show per-agent progress indicators (spinner/checkmark per agent)
- **Exit criteria:** User can configure and submit analysis; see agents completing in real time

### Step 6.3 — Results grid overlay
- Complete `ResultsOverlay.tsx`
- When job completes, fetch scored GeoJSON
- Add choropleth fill layer to MapLibre map (color by tier: high=dark green, medium=yellow, low=orange, negligible=gray)
- Add tier legend
- **Exit criteria:** Scored grid renders correctly on map over AOI

### Step 6.4 — Evidence drawer
- Complete `EvidenceDrawer.tsx`
- Click on any cell → slide-in panel
- Show: composite score (large), tier badge, score bar per agent, evidence bullets per agent, data source list, data gap warnings
- **Exit criteria:** Clicking a cell shows full evidence breakdown

---

## Phase 7 — Export, Polish & Hardening (Days 31–35)

### Step 7.1 — Export endpoints
- `GET /api/v1/analysis/jobs/{id}/export?format=geojson` — scored grid as GeoJSON
- `GET /api/v1/analysis/jobs/{id}/export?format=csv` — ranked cell list as CSV (columns: cell_id, score, tier, top_evidence, lat_center, lon_center)
- Wire export buttons in frontend
- **Exit criteria:** User can download GeoJSON and CSV exports

### Step 7.2 — Error handling & data gap reporting
- Agents should gracefully handle empty spatial context (no data for AOI)
- Surface data gap warnings prominently in UI ("No geochemical samples found within 10km — geochemistry score excluded")
- Add overall `data_coverage_score` to job summary (fraction of agents with adequate data)
- **Exit criteria:** Analysis runs cleanly even when some data layers are empty for the AOI

### Step 7.3 — Add second connector: MinDat
- Implement `mindat.py` connector
- API docs at `api.mindat.io`; requires free API key
- Fetches mineral localities by bounding box
- Normalizes to canonical Feature schema
- **Exit criteria:** MinDat localities appear in map layer alongside MRDS

### Step 7.4 — Performance: spatial query optimization
- Review all PostGIS queries in agent spatial context fetching
- Ensure GIST indexes are being used (check with `EXPLAIN ANALYZE`)
- Add materialized view or index on `commodity_primary` + geometry for common query patterns
- Profile full analysis job timing; identify slowest agent
- **Exit criteria:** Full analysis job for a 100km² AOI completes in <60 seconds

### Step 7.5 — Logging, monitoring, and job history
- Add structured JSON logging to all agents (log: agent_id, job_id, cells_scored, duration_ms)
- Add job history view in frontend (table of past jobs with AOI thumbnail, mineral, date, status)
- Implement `DELETE /api/v1/analysis/jobs/{id}` for cleanup
- **Exit criteria:** Past jobs browsable in UI; logs structured and queryable

---

## Phase 8 — Optional Enhancements (Post-MVP)

These are prioritized but not required for first working version:

| Step | Description | Value |
|---|---|---|
| 8.1 | Literature Agent — search USGS pubs + state survey reports via LLM | High — finds overlooked targets |
| 8.2 | Geophysics Agent — USGS Earth MRI magnetic/gravity data | High for buried targets |
| 8.3 | Real-time ASTER band ratio computation (rasterio microservice) | Medium — improves remote sensing agent |
| 8.4 | Water chemistry agent (USGS NWIS dissolved metals) | Medium |
| 8.5 | Canadian data sources (NRCan MRDS equivalent, provincial surveys) | Medium if Canada in scope |
| 8.6 | Saved AOI / watchlist — re-run analysis automatically when new data ingested | High for operational use |
| 8.7 | PDF summary report export | Medium |
| 8.8 | User accounts + multi-user support | Low for single-user tool |
| 8.9 | USGS Earth MRI connector (airborne survey data) | High — new high-quality data |
| 8.10 | Confidence calibration — backtest agent weights against known producing mines | High for accuracy improvement |

---

## Reference: How to Use This Plan with Claude

When starting any step, use this pattern:

```
We are working on GeoProspector.
Current step: [Step X.Y — Title]

Reference documents:
- System design: docs/01_system_design.md (attached or pasted below)
- Current codebase context: [paste relevant files]

Task: [paste the step description above]

[Any additional context about what's already done or decisions made]
```

For debugging or extension work:
```
We are working on GeoProspector.
Module: [agent name / connector / etc.]

The current implementation does [X].
The problem is [Y].
Relevant code: [paste]
System design reference section: [Section N]
```

---

## Milestone Summary

| Milestone | Steps | Outcome |
|---|---|---|
| **M1: Running scaffold** | 0.1–0.3 | All services up; PostGIS live; map renders |
| **M2: First data flowing** | 1.1–1.4 | MRDS points on map; sync via UI |
| **M3: Full data layer** | 2.1–2.5 | All core connectors ingesting data |
| **M4: Scoring foundation** | 3.1–3.3 | Grid + scoring engine unit tested |
| **M5: First end-to-end analysis** | 4.1–4.7, 5.1–5.3 | Full job runs; scored output in DB |
| **M6: Full UI** | 6.1–6.4 | Draw AOI → run → see results on map |
| **M7: Production-ready MVP** | 7.1–7.5 | Exports, error handling, performance |
