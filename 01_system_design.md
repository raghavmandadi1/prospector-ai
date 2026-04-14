# GeoProspector — System Design & Project Context

> **Version:** 0.1 — Initial Architecture  
> **Purpose:** Living reference document for the GeoProspector AI-powered mineral prospecting application. Use this document as context when working on any module of the codebase.

---

## 1. Project Overview

GeoProspector is a multi-agent AI application that ingests geological, geochemical, remote sensing, and historical mining data from numerous online sources, then performs deep area-specific analysis to identify and prioritize the highest-probability locations for a target mineral (e.g., gold, silver, copper) within a user-defined area of interest (AOI).

The core value proposition is **not** a generic regional heatmap. It is a site-specific, evidence-based prioritization system that runs independent specialist agents against a precise polygon, each contributing a scored and weighted evidence layer, which are then synthesized into a ranked output map with full per-location explainability.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  PHASE 1: DATA INGESTION                │
│  Channel Config → Source Connectors → Normalization     │
│  → Geocoding → Spatial Index (PostGIS / SpatiaLite)     │
└──────────────────────────┬──────────────────────────────┘
                           │ (background / scheduled)
┌──────────────────────────▼──────────────────────────────┐
│               PHASE 2: AREA SELECTION                   │
│  Map UI → Draw Polygon (AOI) → Select Mineral Target    │
│  → Set Analysis Config → Trigger Analysis               │
└──────────────────────────┬──────────────────────────────┘
                           │ (on-demand)
┌──────────────────────────▼──────────────────────────────┐
│            PHASE 3: MULTI-AGENT ANALYSIS                │
│  Orchestrator → Fan-out to Specialist Agents (parallel) │
│  → Evidence Collection → Scoring Engine → Synthesis     │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                      OUTPUT                             │
│  Prioritized Map → Ranked Zone List → Evidence Drilldown│
└─────────────────────────────────────────────────────────┘
```

---

## 3. Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| Backend API | Python / FastAPI | Async support, easy integration with geospatial libs |
| Agent Framework | LangGraph or CrewAI | Multi-agent orchestration with state management |
| LLM | Claude (Anthropic API) | Reasoning agent backbone for each specialist agent |
| Spatial Database | PostgreSQL + PostGIS | Industry standard for geospatial queries, bbox intersect, spatial indexing |
| Tile / Map Server | Martin (Rust) or pg_tileserv | Serve vector tiles from PostGIS directly |
| Frontend | React + MapLibre GL JS | High-performance map rendering, open source |
| Task Queue | Celery + Redis | Async agent task execution, progress tracking |
| Object Storage | S3-compatible (MinIO local / AWS S3 prod) | Store raw downloaded files, rasters, cached results |
| Cache | Redis | API response caching, agent intermediate results |
| Containerization | Docker + Docker Compose | Local dev parity, easy deployment |
| Auth | JWT + FastAPI security | Simple token auth for single/small-team use |

---

## 4. Data Sources & Channel System

### 4.1 Channel Configuration

The app supports user-defined data channels. Each channel is a configured connector with:
- `channel_id`: unique identifier
- `source_type`: enum (REST_API, WFS, WMS, FILE_DOWNLOAD, SCRAPE)
- `endpoint`: URL template
- `auth`: optional API key / OAuth config
- `refresh_schedule`: cron expression
- `spatial_coverage`: bounding box or "global"
- `data_type`: enum (LOCALITIES, CLAIMS, GEOCHEMISTRY, GEOLOGY, REMOTE_SENSING, LITERATURE, TOPO)
- `normalization_profile`: reference to a normalization config

### 4.2 Core Data Sources

#### Mining Localities & Occurrences
| Source | Type | URL | Notes |
|---|---|---|---|
| USGS MRDS | REST/WFS | `mrdata.usgs.gov/mrds` | ~300k+ global mineral deposits; WFS queryable by bbox; bulk CSV download available. Fields: deposit name, commodity, lat/lon, geology notes, production |
| USGS MAS/MILS | (merged into MRDS) | Same endpoint | Historical Bureau of Mines data now part of MRDS |
| USGS National Mineral Assessment | Download | `mrdata.usgs.gov` | Deposit-type permissive tracts; key for regional targeting |
| MinDat.org | REST API | `mindat.io/api` | Mineral species + locality data; requires free API key |
| GEOMARC / State Surveys | Varies | State-by-state | AZ, NV, CO, CA, MT each have their own portals |

#### Mining Claims (Active & Historical)
| Source | Type | URL | Notes |
|---|---|---|---|
| BLM MLRS | Web + bulk download | `mlrs.blm.gov` | Active federal lode/placer claims; spatial data at quarter-section level; replaces legacy LR2000 |
| BLM GLO Records | REST + bulk | `glorecords.blm.gov` | ~6 million historical land patents going back to 1700s; includes mineral patents with survey plats |
| National Archives NARA | Manual / FOIA | `archives.gov` | Land entry case files; 10M+ transactions; useful for deep historical research |
| County Recorder Offices | Scrape / API varies | County-by-county | State-level claim filings; critical because federal + state filings coexist |
| Land Matters | Aggregated web | `mylandmatters.org` | Aggregates GLO + BLM + MRDS into map layers; useful as a validation/cross-reference source |

#### Geological Surveys & Formations
| Source | Type | URL | Notes |
|---|---|---|---|
| USGS National Geologic Map Database | WMS/WFS | `ngmdb.usgs.gov` | Geologic map units; lithology polygons |
| USGS ScienceBase | REST | `sciencebase.gov/catalog` | Geologic reports, shapefiles, rasters |
| Macrostrat | REST API | `macrostrat.org/api` | Stratigraphic columns, rock unit descriptions; excellent API |
| USGS GeMS | GeoPackage download | `ngmdb.usgs.gov/gems` | National Geologic Map Schema standardized data |
| State Geological Surveys | Varies | e.g., Nevada Bureau of Mines (`nbmg.unr.edu`) | Often the best source for western US mining states |

#### Geochemical Data
| Source | Type | URL | Notes |
|---|---|---|---|
| USGS NGDB (National Geochemical Database) | Download | `mrdata.usgs.gov/geochem` | Stream sediment and soil sample geochemistry; pathfinder elements for gold (As, Sb, Hg, Tl) |
| USGS NURE | Archive download | Historical | National Uranium Resource Evaluation — extensive stream sediment sampling; contains Au, Ag, Cu data |
| Canadian GSC / Provincial Surveys | REST/download | `open.canada.ca` | If analyzing Canadian territory |
| ASTER Global Emissivity | Raster download | `lpdaac.usgs.gov` | Surface mineralogy; hydrothermal alteration mapping |

#### Remote Sensing
| Source | Type | URL | Notes |
|---|---|---|---|
| USGS EarthExplorer | REST API | `earthexplorer.usgs.gov` | Landsat, ASTER, aerial imagery |
| Copernicus (ESA) | API | `dataspace.copernicus.eu` | Sentinel-1 (SAR), Sentinel-2 (multispectral); free |
| NASA Earthdata | REST | `earthdata.nasa.gov` | SRTM DEM, MODIS, VIIRS |
| OpenTopography | REST API | `opentopography.org/api` | High-res LiDAR DEMs; critical for terrain analysis |
| Google Earth Engine | Python API | `earthengine.google.com` | For preprocessing rasters at scale (optional, requires account) |

#### Historical Topo / Mine Symbols
| Source | Type | URL | Notes |
|---|---|---|---|
| USGS Historical Topographic Map Collection | WMS + download | `ngmdb.usgs.gov/topoview` | 180,000+ historical topo maps; mine shafts, adits, dumps digitized |
| USGS TopoView Mine Symbols | Shapefile download | `mrdata.usgs.gov` | 400k+ digitized mine-related features from historical topos; 17 western states complete |

---

## 5. Data Ingestion Pipeline

### 5.1 Pipeline Stages

```
Source Connector
    │
    ▼
Raw Fetch (HTTP / file download / WFS GetFeature)
    │
    ▼
Raw Storage (S3/MinIO — immutable, dated)
    │
    ▼
Parser (format-specific: GeoJSON, Shapefile, CSV, GML, GeoPackage)
    │
    ▼
Normalizer (map source fields → canonical schema)
    │
    ▼
Geocoder (if lat/lon missing → resolve from PLSS / township-range / place name)
    │
    ▼
Validator (geometry validity, coordinate sanity, required fields)
    │
    ▼
Spatial Indexer → PostGIS (upsert with source_id dedup)
    │
    ▼
Change Log (what changed since last ingest)
```

### 5.2 Canonical Feature Schema

All ingested records are normalized to a common schema before storage:

```json
{
  "feature_id": "uuid",
  "source_channel": "usgs_mrds",
  "source_record_id": "10012345",
  "feature_type": "MINE | PROSPECT | CLAIM | SAMPLE | FORMATION | SURVEY",
  "name": "string",
  "geometry": "GeoJSON Point | Polygon | LineString",
  "commodity_primary": "gold",
  "commodity_secondary": ["silver", "tellurium"],
  "deposit_type": "lode | placer | skarn | porphyry ...",
  "status": "active | historical | prospect | unknown",
  "production_data": { "oz_au": null, "years_active": null },
  "geologic_unit": "string",
  "rock_type": "string",
  "structural_context": "string",
  "geochemical_values": { "Au_ppb": null, "As_ppm": null },
  "source_quality": 0.0-1.0,
  "ingested_at": "ISO8601",
  "raw_record_ref": "s3://bucket/path/to/raw"
}
```

### 5.3 Spatial Index Strategy

- All geometries stored in PostGIS with SRID 4326
- GIST spatial index on geometry column per feature type
- Additional BTREE indexes: `commodity_primary`, `feature_type`, `status`, `source_channel`
- Tile caching via Martin tile server for frontend rendering

---

## 6. Area Selection & Analysis Trigger

### 6.1 User Inputs
- **AOI (Area of Interest):** Free-draw polygon or bounding box on map. Stored as GeoJSON polygon.
- **Target mineral:** Dropdown selection (gold, silver, copper, platinum, lithium, etc.)
- **Analysis depth:** Quick (top 3 agents only) / Standard (all agents) / Deep (agents + literature search)
- **Evidence weights:** Optional advanced config — slider per evidence layer (defaults are mineral-specific presets)
- **Output resolution:** Grid cell size for scoring (default: 250m, options: 50m / 100m / 250m / 500m)

### 6.2 Analysis Job
When triggered, an analysis job is created:
```json
{
  "job_id": "uuid",
  "status": "queued | running | complete | failed",
  "aoi_geojson": {...},
  "target_mineral": "gold",
  "config": {...},
  "created_at": "ISO8601",
  "agent_results": {},
  "final_scores": null
}
```
Jobs are queued via Celery. Frontend polls job status via SSE (Server-Sent Events) for real-time progress.

---

## 7. Multi-Agent Analysis System

### 7.1 Orchestrator Agent

The orchestrator is a LangGraph/CrewAI supervisor agent that:
1. Receives the analysis job
2. Builds a spatial query context from the AOI (pre-fetches all relevant records from PostGIS intersecting the AOI)
3. Fans out to all specialist agents in parallel (async)
4. Monitors agent completion; handles retries and timeouts
5. Collects `AgentResult` objects
6. Passes collected results to the Scoring Engine
7. Returns the final scored output

### 7.2 Specialist Agents

Each agent receives:
- The AOI polygon
- The target mineral
- Pre-queried spatial data relevant to its domain (from PostGIS)
- Its specific scoring rubric (mineral-specific weighting rules)

Each agent returns an `AgentResult`:
```json
{
  "agent_id": "lithology_agent",
  "status": "complete",
  "scored_cells": [
    {
      "cell_id": "grid_ref",
      "score": 0.0-1.0,
      "confidence": 0.0-1.0,
      "evidence": ["Calcareous sediments present", "Contact with intrusive body at 2.3km"],
      "data_sources_used": ["usgs_geologic_map", "macrostrat"]
    }
  ],
  "agent_notes": "string",
  "warnings": []
}
```

#### Agent 1: Lithology Agent
**Purpose:** Assess host rock favorability for the target mineral.

**Data consumed:** Geologic map units (USGS NGMDB, Macrostrat), rock type classifications.

**Logic for Gold:**
- High score: Calcareous sediments, quartz veins in metamorphic rocks, intrusive/volcanic contacts, greenstone belts
- Medium score: Felsic intrusives (source rocks), volcanic sequences
- Low score: Undifferentiated sedimentary, glacial cover, basement gneiss with no alteration evidence

**Output:** Score per grid cell based on mapped lithologies intersecting that cell.

#### Agent 2: Structural Geology Agent
**Purpose:** Identify structural controls — fluid pathways and trap sites.

**Data consumed:** Fault/lineament databases, fold axes, contact traces, structural geology datasets.

**Logic for Gold:**
- High score: Fault intersections, flexural jogs, shear zones, fold hinge zones, permeable contacts
- Medium score: Single fault proximity (within 500m), regional lineaments
- Low score: >2km from any mapped structure

**Scoring modifiers:** Intersection density bonus; depth-to-basement penalty if available.

#### Agent 3: Proximity Agent
**Purpose:** Score spatial proximity and clustering relative to known mineral occurrences.

**Data consumed:** MRDS localities, MinDat occurrences, historical mine points, BLM claim density.

**Logic:**
- Kernel density estimation on known occurrences within and around AOI
- Distance decay scoring: <500m from known mine = very high; 500m–2km = high; 2–5km = medium
- Cluster bonus: areas within high-density claim sections scored up
- Production bonus: proximity to historically productive mines weighted higher than unproductive prospects

#### Agent 4: Geochemistry Agent
**Purpose:** Identify geochemical anomalies indicative of mineralization.

**Data consumed:** USGS NGDB stream sediment samples, NURE data, soil sample databases.

**Logic for Gold (pathfinder elements):**
- Primary: Au (direct), As (arsenic), Sb (antimony)
- Secondary: Hg (mercury), Tl (thallium), Te (tellurium), W (tungsten)
- Spatial interpolation (IDW or kriging) to create continuous anomaly surface from point samples
- Scoring based on multi-element anomaly composite (not just single element)

**Data quality caveat:** Stream sediment samples are point data with variable density; confidence score should reflect data gap awareness.

#### Agent 5: Remote Sensing Agent
**Purpose:** Detect hydrothermal alteration, iron oxide anomalies, and structural lineaments from satellite imagery.

**Data consumed:** ASTER multispectral bands, Sentinel-2 imagery, SRTM/LiDAR DEM.

**Analysis methods:**
- Band ratio analysis for iron oxides (Fe²⁺/Fe³⁺), clay minerals (alunite, kaolinite, illite), silicification
- Lineament extraction from hillshade DEMs (structural control indicator)
- NDVI inversion to identify sparse/anomalous vegetation (geobotanical indicator)
- Topographic wetness index for identifying drainage and secondary dispersion halos

**Implementation note:** This agent may call a Python raster analysis service (rasterio/GDAL microservice) rather than pure LLM reasoning.

#### Agent 6: Historical Records Agent
**Purpose:** Extract signal from historical mining activity and land records.

**Data consumed:** BLM MLRS claims, GLO mineral patents, USGS historical topo mine symbols, county records.

**Logic:**
- High historical claim density in a section → strong indicator of known mineralization
- Patented claims (especially lode patents from 1870–1920) → strong indicator of economic-grade mineralization was proven
- Old mine symbols on historical topos (adits, shafts, ore dumps) → indicator of historical workings
- Absence of claims in an area with good geology → potential overlooked target

**Output:** Per-cell score based on claim density, patent presence, historical workings density, and temporal activity patterns.

### 7.3 Optional Agents (Phase 2)

- **Literature Agent:** Searches geoscience literature (USGS publications, state survey reports, Google Scholar via SerpAPI) for mentions of mineralization within or near the AOI. Uses LLM to extract deposit descriptions and scoring-relevant facts.
- **Geophysics Agent:** Consumes airborne magnetic and gravity data (where available via USGS Earth MRI program) to identify buried intrusives, faults, and alteration zones.
- **Water Chemistry Agent:** Analyzes USGS NWIS stream chemistry data for dissolved metal anomalies.

---

## 8. Scoring & Synthesis Engine

### 8.1 Grid Cell Generation

The AOI is divided into a regular grid of cells at the configured resolution (default 250m). Each cell is identified by a unique grid reference. All agent scores are mapped to cells.

### 8.2 Weighted Evidence Combination

The synthesis engine combines agent scores using a configurable weighted sum:

```
composite_score(cell) = Σ (agent_weight[i] × agent_score[i][cell] × confidence[i][cell])
                        ────────────────────────────────────────────────────────────────
                                     Σ (agent_weight[i] × confidence[i][cell])
```

This is a confidence-weighted mean — agents with low confidence in a cell have reduced influence.

### 8.3 Mineral-Specific Default Weights

| Agent | Gold | Silver | Copper | Lithium |
|---|---|---|---|---|
| Lithology | 0.20 | 0.20 | 0.25 | 0.15 |
| Structure | 0.25 | 0.20 | 0.15 | 0.10 |
| Proximity | 0.20 | 0.20 | 0.20 | 0.20 |
| Geochemistry | 0.20 | 0.20 | 0.20 | 0.25 |
| Remote Sensing | 0.10 | 0.10 | 0.10 | 0.15 |
| Historical Records | 0.15 | 0.15 | 0.10 | 0.05 |

Weights are user-adjustable at analysis time.

### 8.4 Output Structure

```json
{
  "job_id": "uuid",
  "aoi": {...},
  "target_mineral": "gold",
  "grid_resolution_m": 250,
  "cells": [
    {
      "cell_id": "ref",
      "geometry": "GeoJSON polygon",
      "composite_score": 0.0-1.0,
      "tier": "high | medium | low | negligible",
      "agent_breakdown": {
        "lithology_agent": { "score": 0.85, "confidence": 0.9, "evidence": [...] },
        "structure_agent": { "score": 0.72, "confidence": 0.8, "evidence": [...] },
        ...
      },
      "top_evidence": ["Top 3 evidence statements across all agents"],
      "data_gaps": ["No geochemical samples within 5km"]
    }
  ],
  "summary": {
    "high_priority_zones": 4,
    "total_cells": 312,
    "data_coverage_score": 0.78,
    "recommended_follow_up": "..."
  }
}
```

### 8.5 Tier Classification

| Tier | Score Range | Meaning |
|---|---|---|
| High priority | 0.70–1.0 | Multiple strong evidence layers converging |
| Medium priority | 0.45–0.69 | Some positive indicators; warrants investigation |
| Low priority | 0.20–0.44 | Weak or isolated indicators |
| Negligible | 0.0–0.19 | No significant evidence |

---

## 9. Frontend Application

### 9.1 Key Views

**1. Data Channels Dashboard**
- List of configured data channels with last-sync status
- Add/edit/remove channels
- Manual trigger for re-sync per channel
- Data coverage map (shows spatial extent of ingested data)

**2. Map Exploration View**
- Base map (MapLibre + OpenStreetMap or satellite)
- Toggleable layers: MRDS localities, active claims, historical patents, geologic map, geochemistry samples, mine symbols
- Search / filter by commodity, feature type, date range

**3. Analysis Setup Panel**
- Draw AOI polygon tool
- Mineral target selector
- Analysis config (depth, resolution, weight sliders)
- "Run Analysis" button
- Job status progress (SSE real-time updates per agent)

**4. Results View**
- AOI with scored grid overlay (choropleth, tiered color)
- Sidebar: ranked list of top zones with composite score + top evidence
- Click any cell → detail panel showing all agent scores, evidence list, data sources, data gaps
- Export: GeoJSON, CSV of ranked zones, PDF summary report

### 9.2 Map Stack
- **MapLibre GL JS** for the map
- **Martin** (or `pg_tileserv`) to serve PostGIS feature layers as MVT vector tiles
- Results grid served as GeoJSON (small AOIs) or MVT (large AOIs)

---

## 10. API Design

### Core Endpoints

```
POST   /api/v1/channels              — Create data channel config
GET    /api/v1/channels              — List all channels
POST   /api/v1/channels/{id}/sync    — Trigger manual sync

GET    /api/v1/features              — Query features (bbox, commodity, type filters)
GET    /api/v1/features/{id}         — Single feature detail

POST   /api/v1/analysis/jobs         — Submit analysis job
GET    /api/v1/analysis/jobs/{id}    — Job status + results
GET    /api/v1/analysis/jobs         — Job history
DELETE /api/v1/analysis/jobs/{id}    — Cancel job

GET    /api/v1/analysis/jobs/{id}/export?format=geojson|csv|pdf
```

### SSE Endpoint
```
GET    /api/v1/analysis/jobs/{id}/events   — SSE stream for real-time agent progress
```

---

## 11. Data Quality & Confidence Framework

Every piece of data carries a `source_quality` score (0–1) based on:
- **Positional accuracy:** GPS-precise (1.0) → plotted on 1:24k topo (0.7) → estimated from report text (0.3) → no coordinates (0.0)
- **Age of data:** Recent survey (1.0) → pre-1980 data (0.6) → pre-1920 data (0.4)
- **Source authority:** USGS/state survey (1.0) → mining company report (0.7) → crowd-sourced (0.4)

Agents propagate `confidence` to reflect both source quality and data density in each cell. Data gaps are surfaced explicitly in the output.

---

## 12. Known Data Limitations

| Source | Limitation |
|---|---|
| MRDS | Ceased systematic updates in 2011; positional accuracy highly variable (~4000 records have no coordinates) |
| BLM MLRS | Quarter-section precision only; does not capture state-level claims |
| GLO Patents | Many pre-1900 images not yet digitized; text in 1800s cursive |
| USGS NGDB Geochemistry | Very uneven spatial coverage; sparse in eastern US |
| ASTER Alteration | 15m resolution; cloud cover issues; needs preprocessing |
| County Records | No unified API; requires state-by-state manual integration |

---

## 13. Project Structure (Target)

```
geoprospector/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app entry
│   │   ├── api/
│   │   │   ├── channels.py
│   │   │   ├── features.py
│   │   │   └── analysis.py
│   │   ├── agents/
│   │   │   ├── orchestrator.py
│   │   │   ├── lithology_agent.py
│   │   │   ├── structure_agent.py
│   │   │   ├── proximity_agent.py
│   │   │   ├── geochemistry_agent.py
│   │   │   ├── remote_sensing_agent.py
│   │   │   ├── historical_agent.py
│   │   │   └── base_agent.py
│   │   ├── connectors/
│   │   │   ├── base_connector.py
│   │   │   ├── blm_mlrs.py
│   │   │   ├── usgs_mrds.py
│   │   │   ├── usgs_ngdb.py
│   │   │   ├── usgs_ngmdb.py
│   │   │   ├── macrostrat.py
│   │   │   ├── glo_records.py
│   │   │   └── mindat.py
│   │   ├── pipeline/
│   │   │   ├── ingest.py
│   │   │   ├── normalize.py
│   │   │   ├── geocode.py
│   │   │   └── spatial_index.py
│   │   ├── scoring/
│   │   │   ├── engine.py
│   │   │   ├── grid.py
│   │   │   └── weights.py
│   │   ├── models/
│   │   │   ├── feature.py
│   │   │   ├── channel.py
│   │   │   ├── analysis_job.py
│   │   │   └── agent_result.py
│   │   └── db/
│   │       ├── session.py
│   │       └── migrations/
│   ├── celery_worker.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Map/
│   │   │   ├── AnalysisPanel/
│   │   │   ├── ResultsOverlay/
│   │   │   ├── ChannelDashboard/
│   │   │   └── EvidenceDrawer/
│   │   ├── hooks/
│   │   ├── store/
│   │   └── api/
│   ├── package.json
│   └── Dockerfile
├── tileserver/                      # Martin config
├── docker-compose.yml
├── docker-compose.dev.yml
└── docs/
    ├── 01_system_design.md          ← this document
    ├── 02_scaffold_prompt.md
    └── 03_implementation_plan.md
```

---

## 14. Environment Variables

```env
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/geoprospector
REDIS_URL=redis://localhost:6379/0

# Storage
S3_ENDPOINT=http://localhost:9000
S3_BUCKET=geoprospector-raw
S3_ACCESS_KEY=...
S3_SECRET_KEY=...

# LLM
ANTHROPIC_API_KEY=...

# External APIs
MINDAT_API_KEY=...
NASA_EARTHDATA_TOKEN=...
COPERNICUS_CLIENT_ID=...
COPERNICUS_CLIENT_SECRET=...

# App
SECRET_KEY=...
ENVIRONMENT=development
LOG_LEVEL=INFO
```

---

## 15. Key Design Decisions & Rationale

| Decision | Rationale |
|---|---|
| PostGIS over vector file approach | Enables live spatial queries (bbox intersect, proximity) at ingestion time rather than loading everything into memory per analysis |
| Agents run against pre-queried PostGIS data, not raw APIs | Decouples real-time analysis from upstream API availability and rate limits |
| Confidence-weighted scoring vs. simple weighted average | Handles data gaps gracefully — a cell with no geochemical data doesn't get penalized by a zero score, it gets down-weighted confidence |
| Grid-based output vs. continuous raster | Easier to attach per-cell evidence breakdowns; more interpretable to end user; simpler to export |
| SSE for job progress vs. polling | Lower overhead than websockets for one-directional progress stream; works well with FastAPI |
| Mineral-specific weight presets | Deposit controls differ dramatically by mineral type; a single weight set would be misleading |

---

*Last updated: project inception. Update this document when architecture decisions change.*
