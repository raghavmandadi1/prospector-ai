# GeoProspector — System Design & Project Context

> **Version:** 0.2 — Vision & Domain Knowledge Update
> **Purpose:** Living reference document for the GeoProspector AI-powered mineral prospecting application. Use this document as context when working on any module of the codebase.
>
> **v0.2 changes:** Added project vision, primary geographic focus, land status flagging philosophy, placer gold mode, Washington State-specific geology and data sources, and domain knowledge for the North Fork Snoqualmie corridor. These additions reflect the goals of both collaborators and should inform all agent prompt design and data source prioritization.

---

## 1. Project Overview

GeoProspector is a multi-agent AI application that ingests geological, geochemical, remote sensing, and historical mining data from numerous online sources, then performs deep area-specific analysis to identify and prioritize the highest-probability locations for a target mineral within a user-defined area of interest (AOI).

The core value proposition is **not** a generic regional heatmap. It is a site-specific, evidence-based prioritization system that runs independent specialist agents against a precise polygon, each contributing a scored and weighted evidence layer, which are then synthesized into a ranked output map with full per-location explainability.

### 1.1 Project Vision & Intended Use

This tool is built to support **real field prospecting for hard-rock and placer gold in Washington State**, with the primary goal of generating better starting points for boots-on-the-ground exploration. The model is not expected to pinpoint a mine — it is expected to dramatically narrow the search space so that field time is spent only in geologically defensible locations.

The workflow this system is designed to support:

1. Run the model against a target corridor → get a ranked, evidence-backed map of high-probability zones
2. Use the placer gold layer to identify likely stream concentrations → scout those areas in the field
3. Use placer finds to work upstream toward probable lode sources
4. Return field observations to refine the model over time

**Critical design principle:** The model should flag legal, access, and land status constraints — but never suppress a geologically high-scoring cell because of them. A cell inside an active mining claim or near a wilderness boundary should still receive its full geological score; the flag is informational, not a filter. A high-scoring claimed area is still valuable intelligence (it confirms the geology) and an adjacent unclaimed area with the same geology becomes an obvious target.

### 1.2 Primary Geographic Focus

**Current development target:** The I-90 corridor in King County, Washington — specifically the **North Fork Snoqualmie Road** drainage and the public land accessible at its end, within the **Mount Baker-Snoqualmie National Forest**.

Key boundaries for this focus area:
- **Exclude (private):** Campbell Global Snoqualmie Tree Farm — approximately the first 16 miles of North Fork Road from North Bend. This is commercial timberland with no public mineral access.
- **Primary target zone:** National Forest land from the FS Road 57 boundary (where County road ends) to the **Alpine Lakes Wilderness boundary** — roughly the Lennox Creek / Bear Basin / Bare Mountain corridor.
- **Exclude (withdrawn):** Alpine Lakes Wilderness — closed to mineral entry under the Wilderness Act. Cells within this boundary should be flagged "WILDERNESS — NO MINERAL ENTRY" but still scored geologically.

**Future expansion:** Western Washington Cascades, then full state of Washington.

The system architecture should be fully general (any AOI, any mineral), but agent prompts, data source prioritization, and geological reasoning should be tuned first for this specific corridor.

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
│         PHASE 4: LAND STATUS OVERLAY (non-scoring)      │
│  Claim Status → Wilderness / Withdrawal Flags           │
│  → Private Land Boundaries → Access Constraints         │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                      OUTPUT                             │
│  Prioritized Map → Ranked Zone List → Evidence Drilldown│
│  → Land Status Flags → Field Target Export              │
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
- `data_type`: enum (LOCALITIES, CLAIMS, GEOCHEMISTRY, GEOLOGY, REMOTE_SENSING, LITERATURE, TOPO, LAND_STATUS, HYDROLOGY)
- `normalization_profile`: reference to a normalization config

### 4.2 Core Data Sources

#### Mining Localities & Occurrences
| Source | Type | URL | Notes |
|---|---|---|---|
| USGS MRDS | REST/WFS | `mrdata.usgs.gov/mrds` | ~300k+ global mineral deposits; WFS queryable by bbox. Note: ceased systematic updates 2011; positional accuracy variable |
| USGS MAS/MILS | (merged into MRDS) | Same endpoint | Historical Bureau of Mines data now part of MRDS |
| USGS National Mineral Assessment | Download | `mrdata.usgs.gov` | Deposit-type permissive tracts; key for regional targeting |
| MinDat.org | REST API | `mindat.io/api` | Mineral species + locality data; requires free API key. Well-populated for NF Snoqualmie mines (Bear Basin, Lennox, Monte Carlo confirmed) |
| USGS Historical Topo Mine Symbols | Shapefile | `mrdata.usgs.gov` | 400k+ digitized mine features from historical topos; 17 western states complete. Critical for locating adits/shafts not in MRDS |

#### Mining Claims (Active & Historical)
| Source | Type | URL | Notes |
|---|---|---|---|
| BLM MLRS | Web + bulk download | `mlrs.blm.gov` | Active federal lode/placer claims; spatial data at quarter-section level. **Use for land status flagging, not geological scoring** |
| BLM GLO Records | REST + bulk | `glorecords.blm.gov` | ~6 million historical land patents; includes mineral patents |
| County Recorder Offices | Scrape / API varies | County-by-county | State-level claim filings; King County for primary focus area |
| Land Matters | Aggregated web | `mylandmatters.org` | Aggregates GLO + BLM + MRDS into map layers; useful cross-reference |

#### Geological Surveys & Formations
| Source | Type | URL | Notes |
|---|---|---|---|
| USGS National Geologic Map Database | WMS/WFS | `ngmdb.usgs.gov` | Geologic map units; lithology polygons |
| USGS Geologic Map I-2538 | Download | `pubs.usgs.gov/imap/i2538` | Snoqualmie Pass 30×60' quad — direct coverage of primary focus area. **High priority for initial development** |
| **WA DNR Geologic Information Portal** | WMS/WFS | `geologyportal.dnr.wa.gov` | **Washington State-specific geology — higher resolution and detail than federal sources for WA. Priority connector to build** |
| **WA DNR Mineral Resources** | Download/WFS | `geology.wa.gov/minerals` | State mineral occurrence database; complements USGS MRDS for WA |
| Macrostrat | REST API | `macrostrat.org/api` | Stratigraphic columns, rock unit descriptions; excellent API |
| USGS ScienceBase | REST | `sciencebase.gov/catalog` | Geologic reports, shapefiles, rasters |

#### Geochemical Data
| Source | Type | URL | Notes |
|---|---|---|---|
| USGS NGDB (National Geochemical Database) | Download | `mrdata.usgs.gov/geochem` | Stream sediment and soil sample geochemistry; pathfinder elements for gold (As, Sb, Hg, Tl) |
| USGS NURE | Archive download | Historical | National Uranium Resource Evaluation — extensive stream sediment sampling; contains Au, Ag, Cu data |
| ASTER Global Emissivity | Raster download | `lpdaac.usgs.gov` | Surface mineralogy; hydrothermal alteration mapping. See Remote Sensing section for specific band ratios |

#### Remote Sensing
| Source | Type | URL | Notes |
|---|---|---|---|
| USGS EarthExplorer | REST API | `earthexplorer.usgs.gov` | Landsat, ASTER, aerial imagery |
| Copernicus (ESA) | API | `dataspace.copernicus.eu` | Sentinel-2 multispectral; free. Key band combinations for WA geology noted in Section 7.5 |
| **USGS 3DEP (LiDAR)** | REST API | `tnmaccess.nationalmap.gov` | **National LiDAR-derived DEM. Critical for placer gold mode — stream gradient, channel morphology, lineament extraction. Priority connector** |
| OpenTopography | REST API | `opentopography.org/api` | High-res LiDAR DEMs; WA has excellent coverage. Fallback/supplement to 3DEP |
| NASA Earthdata | REST | `earthdata.nasa.gov` | SRTM DEM, MODIS, VIIRS |

#### Hydrology (New — Required for Placer Gold Mode)
| Source | Type | URL | Notes |
|---|---|---|---|
| **USGS NHD (National Hydrography Dataset)** | WFS/download | `hydro.nationalmap.gov` | **Stream network with flow direction, order, and gradient data. Required for placer agent. Priority connector** |
| **USGS StreamStats** | REST API | `streamstats.usgs.gov` | Watershed delineation and stream characteristics; drainage area, slope |
| **USGS NWIS Stream Chemistry** | REST | `waterservices.usgs.gov` | Dissolved metal anomalies in stream water; secondary placer indicator |

#### Land Status & Access (New — Required for Flag Layer)
| Source | Type | URL | Notes |
|---|---|---|---|
| **USFS Wilderness Boundaries** | Shapefile/WFS | `data.fs.usda.gov/geodata` | **Alpine Lakes and Glacier Peak Wilderness boundaries. Critical for NF Snoqualmie focus area. Flag cells WILDERNESS — NO MINERAL ENTRY** |
| **USFS Land Status Tracts** | WFS | `data.fs.usda.gov/geodata/edw` | Administrative land status (public domain, acquired, withdrawn, reserved). Use to flag non-claimable cells |
| **BLM MLRS Active Claims** | Web/bulk | `mlrs.blm.gov` | Flag cells overlapping active claims. Do not suppress scores — flag only |
| Private Land Boundaries | County GIS | King County Assessor | Campbell Global and other private land in NF Snoqualmie corridor. Flag cells PRIVATE LAND |

#### Historical Topo / Mine Symbols
| Source | Type | URL | Notes |
|---|---|---|---|
| USGS Historical Topographic Map Collection | WMS + download | `ngmdb.usgs.gov/topoview` | 180,000+ historical topo maps; mine shafts, adits, dumps digitized. Snoqualmie Lake quad (1965 edition) is key for NF Snoqualmie corridor |
| USGS TopoView Mine Symbols | Shapefile | `mrdata.usgs.gov` | 400k+ digitized mine-related features from historical topos |

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
  "feature_type": "MINE | PROSPECT | CLAIM | SAMPLE | FORMATION | SURVEY | STREAM_REACH | LAND_STATUS",
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
  "land_status": "open | claimed | wilderness | private | withdrawn | unknown",
  "access_notes": "string",
  "source_quality": 0.0,
  "ingested_at": "ISO8601",
  "raw_record_ref": "s3://bucket/path/to/raw"
}
```

### 5.3 Spatial Index Strategy

- All geometries stored in PostGIS with SRID 4326
- GIST spatial index on geometry column per feature type
- Additional BTREE indexes: `commodity_primary`, `feature_type`, `status`, `source_channel`, `land_status`
- Tile caching via Martin tile server for frontend rendering

---

## 6. Area Selection & Analysis Trigger

### 6.1 User Inputs
- **AOI (Area of Interest):** Free-draw polygon or bounding box on map. Stored as GeoJSON polygon.
- **Target mineral:** Dropdown selection. Supported: `gold_lode`, `gold_placer`, `silver`, `copper`, `platinum`, `lithium`. Note: `gold_lode` and `gold_placer` are treated as distinct modes with different agents and weights.
- **Analysis depth:** Quick (top 3 agents only) / Standard (all agents) / Deep (agents + literature search)
- **Evidence weights:** Optional advanced config — slider per evidence layer (defaults are mineral-specific presets)
- **Output resolution:** Grid cell size for scoring (default: 250m, options: 50m / 100m / 250m / 500m)
- **Land status behavior:** Always "flag, never filter" — land status constraints are displayed as overlays on the scored map, never used to zero out geological scores.

### 6.2 Analysis Job
When triggered, an analysis job is created:
```json
{
  "job_id": "uuid",
  "status": "queued | running | complete | failed",
  "aoi_geojson": {...},
  "target_mineral": "gold_lode | gold_placer",
  "config": {...},
  "created_at": "ISO8601",
  "agent_results": {},
  "final_scores": null,
  "land_status_flags": {}
}
```
Jobs are queued via Celery. Frontend polls job status via SSE for real-time progress.

---

## 7. Multi-Agent Analysis System

### 7.1 Orchestrator Agent

The orchestrator is a LangGraph/CrewAI supervisor agent that:
1. Receives the analysis job
2. Builds a spatial query context from the AOI (pre-fetches all relevant records from PostGIS intersecting the AOI)
3. Fans out to all specialist agents in parallel (async)
4. Monitors agent completion; handles retries and timeouts
5. Collects `AgentResult` objects
6. Runs the land status overlay pass (separate from scoring)
7. Passes collected results to the Scoring Engine
8. Returns the final scored output with land status flags attached

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
      "score": 0.0,
      "confidence": 0.0,
      "evidence": ["Calcareous sediments present", "Contact with intrusive body at 2.3km"],
      "data_sources_used": ["usgs_geologic_map", "macrostrat"]
    }
  ],
  "agent_notes": "string",
  "warnings": []
}
```

---

#### Agent 1: Lithology Agent

**Purpose:** Assess host rock favorability for the target mineral.

**Data consumed:** Geologic map units (USGS NGMDB, WA DNR Geology Portal, Macrostrat), rock type classifications.

**Logic for Gold (lode) — Washington Cascade / orogenic context:**

The primary gold systems in the Western Washington Cascades are **orogenic shear-zone-hosted quartz veins**. The Snoqualmie Batholith (late Oligocene–Miocene, ~17–28 Ma) acts as a regional heat and fluid source, not a host rock. Ore is deposited in the surrounding metamorphic country rocks, particularly at and near shear zones.

Key lithology signals for this region:
- **Highest score:** Carbonaceous / graphitic phyllite and argillite (Easton Metamorphic Suite). These rocks act as chemical reducing agents — hydrothermal fluids carrying gold as Au(HS)₂⁻ complexes encounter the carbonaceous material, which destabilizes the gold complex and causes precipitation. This is the most important and underappreciated lithological signal for this specific area.
- **High score:** Greenschist, chlorite-sericite schist (Shuksan Greenschist / Darrington Phyllite), metagraywacke — common host and wall rock for quartz veins in this district
- **High score:** Contact zones between carbonaceous metasediments and felsic intrusive dikes or the Snoqualmie Batholith margin — these are preferred precipitation sites
- **Medium score:** Felsic intrusives (source rocks for fluids), volcanic sequences
- **Low score:** Undifferentiated sedimentary, glacial cover, granite with no alteration evidence

When WA DNR geology data is available, it should take precedence over federal sources for lithology classification in Washington State.

**Logic for Gold (placer):** See Agent 7 (Placer Agent).

---

#### Agent 2: Structural Geology Agent

**Purpose:** Identify structural controls — fluid pathways and trap sites.

**Data consumed:** Fault/lineament databases, fold axes, contact traces, structural geology datasets, WA DNR structural data.

**Logic for Gold (lode) — key nuance for Cascade orogenic systems:**

Gold in shear-zone systems does not deposit uniformly throughout a fault or shear zone. It concentrates at specific structural positions:

- **Highest score: Contact zones (shear zone margins)** — The boundary between the intensely sheared/mylonitic core and the adjacent less-deformed host rock. Permeability contrasts at these contacts trap mineralizing fluids. This is more specific than simple "fault proximity."
- **Highest score: Structural jogs, bends, and dilational sites** — Where a fault or shear zone changes orientation, dilation creates open space for vein formation and gold deposition
- **High score: Fault / shear zone intersections** — Two intersecting structures create maximum permeability and fluid mixing
- **Medium score: Single fault proximity within 500m** — Useful regional signal but non-specific
- **Low score: >2km from any mapped structure**

The regional structural framework for the NF Snoqualmie focus area includes the Darrington-Devils Mountain Fault Zone (part of the broader Olympic-Wallowa Lineament system) and subsidiary faults controlling individual mine-scale veins. Note: GPT-generated references to an "Easton Fault Zone" or "Snoqualmie Fault" are not confirmed in primary literature — use USGS and WA DNR structural datasets for actual fault traces.

---

#### Agent 3: Proximity Agent

**Purpose:** Score spatial proximity and clustering relative to known mineral occurrences.

**Data consumed:** MRDS localities, MinDat occurrences, historical mine points, BLM claim density.

**Logic:**
- Kernel density estimation on known occurrences within and around AOI
- Distance decay scoring: <500m from known mine = very high; 500m–2km = high; 2–5km = medium
- Cluster bonus: areas within high-density claim sections scored up
- Production bonus: proximity to historically productive mines weighted higher than unproductive prospects
- **Shear zone alignment bonus:** If multiple mines in the vicinity align along a structural trend, cells along that trend between known mines should receive elevated scores (consistent with the fluid pathway model)

**Named sites of known importance in the primary focus area (for validation and tuning):**
- Bear Basin Mines (Buena Vista District) — 8 adits, ~2,165 ft workings, gold/silver/copper/lead/zinc
- Lennox Mine — 11 adits, ~600 ft longest, Lennox Creek drainage
- Monte Carlo Mine — 4 adits, NE of Illinois Creek–NF Snoqualmie confluence
- Coney Basin prospects — ridge system above NF Snoqualmie

---

#### Agent 4: Geochemistry Agent

**Purpose:** Identify geochemical anomalies indicative of mineralization.

**Data consumed:** USGS NGDB stream sediment samples, NURE data, soil sample databases.

**Logic for Gold (pathfinder elements):**
- Primary: Au (direct), As (arsenic), Sb (antimony)
- Secondary: Hg (mercury), Tl (thallium), Te (tellurium), W (tungsten)
- For carbonaceous/graphitic host rock systems specifically: also flag anomalous carbon or organic matter in sediment samples as a secondary indicator
- Spatial interpolation (IDW or kriging) to create continuous anomaly surface from point samples
- Scoring based on multi-element anomaly composite (not just single element)

**Data quality caveat:** Stream sediment samples are point data with variable density; confidence score should reflect data gap awareness.

---

#### Agent 5: Remote Sensing Agent

**Purpose:** Detect hydrothermal alteration, iron oxide anomalies, and structural lineaments from satellite imagery.

**Data consumed:** ASTER multispectral bands, Sentinel-2 imagery, LiDAR DEM (3DEP / OpenTopography).

**Analysis methods:**

*Alteration mapping (ASTER):*
- Band ratio 5/7: iron oxide / gossan mapping (oxidized sulfides at surface — a classic gold indicator)
- Band ratio 4/2: carbonate alteration (ankerite, siderite — common in orogenic gold systems)
- Band ratio 8/6: clay minerals (alunite, kaolinite, illite — hydrothermal alteration halos)
- Combined alteration index using multiple ratios for composite scoring

*Alteration mapping (Sentinel-2):*
- Band 11/12 combination: differentiates carbonate vs. silicate-dominated terrain
- NDVI inversion: sparse/anomalous vegetation as geobotanical indicator

*Structural analysis (LiDAR/DEM):*
- Hillshade lineament extraction: subtle linear features that may represent concealed faults or veins
- Slope breaks and scarps: may indicate shear zone expression at surface
- In the NF Snoqualmie corridor specifically: LiDAR is extremely valuable for detecting old adits, waste rock piles, tramway foundations, and bench features under forest cover

**Implementation note:** This agent should call a Python raster analysis service (rasterio/GDAL microservice) rather than pure LLM reasoning for the band ratio computations.

---

#### Agent 6: Historical Records Agent

**Purpose:** Extract signal from historical mining activity and land records.

**Data consumed:** BLM MLRS claims, GLO mineral patents, USGS historical topo mine symbols, county records, WA DNR mine records.

**Logic:**
- High historical claim density in a section → strong indicator of known mineralization
- Patented claims (especially lode patents from 1870–1920) → strong indicator of economic-grade mineralization was proven
- Old mine symbols on historical topos (adits, shafts, ore dumps) → indicator of historical workings
- **Absence of claims in an area with good geology** → potential overlooked target — particularly relevant for areas above historical road access limits that were never worked
- For the NF Snoqualmie area: cross-reference WA DNR Bulletin 37 and Bulletin 63 for detailed mine records not captured in federal databases

**Output:** Per-cell score based on claim density, patent presence, historical workings density, and temporal activity patterns.

---

#### Agent 7: Placer Gold Agent *(new)*

**Purpose:** Identify locations where gold eroded from lode sources is likely to have concentrated in stream sediments, and infer upstream lode source directions.

**Data consumed:** NHD stream network, 3DEP LiDAR DEM, USGS StreamStats, stream chemistry (NWIS), historical placer mine records from MRDS.

**Why this agent matters for this project:** Placer gold is the field-accessible signal that points toward lode sources. In the NF Snoqualmie corridor, hard-rock outcrops are often inaccessible or buried under forest canopy. Finding placer gold in a stream reach allows you to work upstream along gradient breaks toward the probable lode. This agent generates field-testable targets that inform lode targeting.

**Placer gold concentration mechanics:**
Gold concentrates in streams due to its high density (19.3 g/cm³) settling wherever water velocity drops. Key trap types, in rough order of productivity:
- **Bedrock potholes:** Bowl-shaped depressions scoured into bedrock by swirling cobbles. Best traps hold packed black sand at the base; poor traps are smooth-walled with no sediment accumulation. Score higher if pothole geometry is concave (narrow top, wide base) and located on inside of bend.
- **Plunge pools below waterfalls:** Sudden energy drop causes heavy material to settle. Score highly — especially where waterfall is over fractured or faulted bedrock.
- **Inside river bends:** Velocity decreases on the inside; heavies settle while lighter sediment carries away.
- **Behind large boulders:** Eddy zones downstream of boulders trap material.
- **Bedrock shelves and ledges:** Gold lodges in cracks running perpendicular to current direction.
- **False bedrock (clay layers, hardpan, cemented gravel):** Can trap gold similarly to true bedrock; common in glacially influenced drainages like NF Snoqualmie.

**Data sources and scoring logic:**
- Stream gradient: Sudden decreases in gradient (from steep to flat) are deposition zones. Score stream reaches at and below gradient breaks. Extract from 3DEP DEM + NHD stream network.
- Stream order: Lower-order headwater streams near known lode sources are preferred for coarser gold; higher-order main-channel reaches accumulate finer flour gold.
- Proximity to known lode sources: Score stream reaches downstream of known mines or high-scoring lode cells; gold travels downstream from source.
- LiDAR: Flag bedrock exposure probability in channel based on DEM-derived channel incision depth and local slope.
- Historical placer records: MRDS `deposit_type = placer` records and historical topo placer workings.

**Placer → Lode inference:**
When high placer scores cluster in a stream reach, the agent should compute an upstream search corridor (based on stream gradient and typical transport distances for coarse gold) and flag the corresponding upstream cells as elevated candidates for lode investigation.

**Washington-specific context:**
- Most of the NF Snoqualmie drainage has deep glacial gravel cover, making bedrock exposure limited and sporadic. Scout bedrock at canyon narrows, high-gradient sections, and where the channel has cut through glacial deposits.
- Fine placer gold is widespread but diffuse; coarser gold and "pickers" are the useful field signal pointing toward a nearby lode source.
- Late summer / early fall is optimal field time for bedrock exposure scouting (low water).

---

### 7.3 Land Status Overlay *(new — runs after scoring, not as a scoring input)*

**Purpose:** Attach legal, access, and claim status flags to scored cells without modifying geological scores.

**This is not an agent — it is a post-scoring overlay pass.**

For each scored cell, query the land status data layer and attach one or more flags:

| Flag | Meaning | Source |
|---|---|---|
| `WILDERNESS` | Inside designated wilderness area — mineral entry prohibited | USFS wilderness boundaries |
| `ACTIVE_CLAIM` | Overlaps an active BLM lode or placer claim | BLM MLRS |
| `PRIVATE_LAND` | On private land (e.g., Campbell Global Snoqualmie Tree Farm) | County assessor / private parcel data |
| `WITHDRAWN` | Federal land withdrawn from mineral entry for other purposes | BLM MLRS / USFS land status |
| `PATENTED_CLAIM` | Historical patented mining claim (private mineral rights) | BLM GLO Records |
| `OPEN` | No known restrictions; appears open to mineral entry | Default if no flag applies |

**Rendering:** In the frontend, land status flags appear as an optional overlay layer on top of the scored heatmap. A cell can be both "HIGH PRIORITY (score: 0.87)" and "ACTIVE_CLAIM" simultaneously — this is by design. A high-scoring claimed cell tells you the geology is worth pursuing; look for adjacent open land.

---

### 7.4 Optional Agents (Phase 2)

- **Literature Agent:** Searches geoscience literature (USGS publications, WA DNR bulletins, Google Scholar) for mentions of mineralization within or near the AOI. WA DNR Bulletins 37 and 63 are specifically valuable for the NF Snoqualmie district.
- **Geophysics Agent:** Consumes airborne magnetic and gravity data (where available via USGS Earth MRI program) to identify buried intrusives, faults, and alteration zones.
- **Water Chemistry Agent:** Analyzes USGS NWIS stream chemistry data for dissolved metal anomalies.
- **Field Feedback Agent (future):** Ingests field observations (GPS coordinates + pan results, outcrop photos, sample assays) to update model priors and refine cell scores. Closes the loop between model output and real-world ground truthing.

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

**Gold (Lode) — Western Washington Cascades / orogenic shear-zone context:**

| Agent | Weight | Rationale |
|---|---|---|
| Structure | 0.30 | Shear zones and structural jogs are the primary control on orogenic gold in this region |
| Lithology | 0.28 | Carbonaceous/graphitic host rock is a critical and specific signal; weighted up from generic default |
| Geochemistry | 0.18 | Pathfinder elements valid but sparse data in Cascades |
| Historical | 0.14 | WA has good mine records; historical activity is a strong validator |
| Remote Sensing | 0.07 | Iron oxide and clay alteration indices useful; Cascades cloud cover reduces reliability |
| Proximity | 0.03 | Redundant with historical for this focused AOI |

**Gold (Placer) — Western Washington streams:**

| Agent | Weight | Rationale |
|---|---|---|
| Placer (hydraulics) | 0.40 | Stream gradient breaks, bedrock exposure, and pothole traps are the primary signal |
| Proximity (to lode sources) | 0.25 | Distance and direction from known lode deposits drives gold loading in stream |
| Historical | 0.20 | Historical placer workings are a direct validator |
| Geochemistry | 0.10 | Stream sediment As/Sb anomalies corroborate; sparse data |
| Remote Sensing | 0.05 | LiDAR DEM used for gradient and channel morphology |

**Other minerals (silver, copper, lithium):** See original weight presets; these are not the primary focus of current development.

### 8.4 Output Structure

```json
{
  "job_id": "uuid",
  "aoi": {},
  "target_mineral": "gold_lode | gold_placer",
  "grid_resolution_m": 250,
  "cells": [
    {
      "cell_id": "ref",
      "geometry": "GeoJSON polygon",
      "composite_score": 0.0,
      "tier": "high | medium | low | negligible",
      "land_status_flags": ["ACTIVE_CLAIM", "OPEN"],
      "agent_breakdown": {
        "lithology_agent": { "score": 0.85, "confidence": 0.9, "evidence": [] },
        "structure_agent": { "score": 0.72, "confidence": 0.8, "evidence": [] }
      },
      "top_evidence": ["Top 3 evidence statements across all agents"],
      "data_gaps": ["No geochemical samples within 5km"],
      "upstream_lode_candidate": false
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
- **Land status overlay layer (toggleable):** Wilderness boundaries, private land, active claims — color-coded
- Search / filter by commodity, feature type, date range

**3. Analysis Setup Panel**
- Draw AOI polygon tool
- Mineral target selector: `gold_lode` or `gold_placer` (distinct modes)
- Analysis config (depth, resolution, weight sliders)
- "Run Analysis" button
- Job status progress (SSE real-time updates per agent)

**4. Results View**
- AOI with scored grid overlay (choropleth, tiered color)
- Land status flag overlay (toggleable, does not affect score colors)
- Sidebar: ranked list of top zones with composite score + top evidence + land status flags
- Click any cell → detail panel showing all agent scores, evidence list, data sources, data gaps, land status
- **Placer mode only:** upstream lode candidate indicator on high-scoring placer cells
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
| USGS NGDB Geochemistry | Very uneven spatial coverage; sparse in Cascades |
| ASTER Alteration | 15m resolution; cloud cover issues in western WA; needs preprocessing |
| County Records | No unified API; requires state-by-state manual integration |
| NHD Stream Data | Channel gradient derived from DEM has error in steep terrain; validate against LiDAR |
| Private Land Boundaries | Campbell Global and other timberland boundaries not always in public GIS; may require King County assessor parcel data |

---

## 13. Washington State Geological Context for Agent Prompt Design

*This section is domain knowledge for the primary focus area. Reference it when writing or reviewing agent prompts.*

### 13.1 Rock Units in the NF Snoqualmie / Lennox Creek Corridor

| Unit | Age | Description | Gold Relevance |
|---|---|---|---|
| Easton Metamorphic Suite | Jurassic–Cretaceous | Graphitic phyllite, argillite, greenschist, blue-amphibole schist; derived from deep marine sediments and ocean floor basalt | **Highest** — carbonaceous units are gold-precipitation sites |
| Darrington Phyllite | Jurassic–Cretaceous | Graphitic quartz-albite-sericite phyllite; part of broader Easton suite | **Highest** — same mechanism |
| Shuksan Greenschist | Jurassic–Cretaceous | Metamorphosed ocean floor basalt; epidote-actinolite-chlorite assemblage | High — common host and wall rock for orogenic veins |
| Snoqualmie Batholith | Late Oligocene–Miocene (~17–28 Ma) | Granodiorite and tonalite; large intrusive body | Low as host; important as heat/fluid source; contact zones with metamorphics are elevated targets |
| Glacial deposits | Quaternary | Thick overburden throughout valley floors; obscures bedrock and traps placer gold | Relevant to placer agent; reduces confidence of lithology agent where cover is thick |

### 13.2 Structural Context

- The Darrington-Devils Mountain Fault Zone and related structures control fluid pathways in the broader region
- Individual mine veins (Lennox, Bear Basin, Monte Carlo) are hosted in smaller-scale shear zones and fracture sets related to this regional framework
- Gold concentrates at shear zone **contact zones** (the margin between mylonitic core and less-deformed host), not uniformly throughout the shear zone
- Multiple mines aligning along a ridge or drainage trend is a strong indicator of a controlling structural feature — flag these alignments in the proximity and structure agents

### 13.3 Known Mine Sites (Primary Focus Area)

| Mine | Location | Workings | Commodities | Notes |
|---|---|---|---|---|
| Bear Basin Mines | Bear Creek, NF Snoqualmie drainage | 8 adits + winze, ~2,165 ft total | Au, Ag, Cu, Pb, Zn, Sb, Sn | Buena Vista District; mill built ~1917, burned 1934; discovered 1925 |
| Lennox Mine | Lennox Creek, off NF Snoqualmie Road | 11 adits, longest ~600 ft | Au, Ag | 1 ton recorded production 1938; "Silver Bowl Mine" alias unconfirmed |
| Monte Carlo Mine | NE of Illinois Creek–NF confluence | 4 adits, longest ~350 ft | Au | Local oral history: ore barrels floated downstream on NF Snoqualmie; buried ore house under landslide — unverified but worth investigating |
| Coney Basin prospects | Ridge above NF Snoqualmie | Various small workings | Au | Related district |

### 13.4 Land Status Summary for Primary Focus Area

- **Campbell Global private land:** ~first 16 miles of North Fork Road from North Bend. Flag all cells. No mineral access.
- **Mt. Baker-Snoqualmie National Forest:** From FS Road 57 boundary onward. Open to casual use and mineral claims subject to USFS regulations and BLM MLRS status.
- **Alpine Lakes Wilderness:** Begins in the upper Bear Basin / trail area. Mineral entry prohibited. Flag cells `WILDERNESS`.
- **Active claims:** Check BLM MLRS at analysis time; status changes. Flag but do not suppress scores.

---

## 14. Project Structure (Target)

```
geoprospector/
├── backend/
│   ├── app/
│   │   ├── main.py
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
│   │   │   ├── placer_agent.py          ← new
│   │   │   └── base_agent.py
│   │   ├── connectors/
│   │   │   ├── base_connector.py
│   │   │   ├── blm_mlrs.py
│   │   │   ├── usgs_mrds.py
│   │   │   ├── usgs_ngdb.py
│   │   │   ├── usgs_ngmdb.py
│   │   │   ├── macrostrat.py
│   │   │   ├── glo_records.py
│   │   │   ├── mindat.py
│   │   │   ├── wa_dnr_geology.py        ← new
│   │   │   ├── usgs_nhd.py              ← new (hydrology for placer)
│   │   │   ├── usgs_3dep.py             ← new (LiDAR DEM)
│   │   │   └── land_status.py           ← new (wilderness, claims overlay)
│   │   ├── pipeline/
│   │   │   ├── ingest.py
│   │   │   ├── normalize.py
│   │   │   ├── geocode.py
│   │   │   └── spatial_index.py
│   │   ├── scoring/
│   │   │   ├── engine.py
│   │   │   ├── grid.py
│   │   │   ├── weights.py
│   │   │   └── land_status_overlay.py   ← new
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
│   │   │   ├── LandStatusOverlay/       ← new
│   │   │   ├── ChannelDashboard/
│   │   │   └── EvidenceDrawer/
│   │   ├── hooks/
│   │   ├── store/
│   │   └── api/
│   ├── package.json
│   └── Dockerfile
├── tileserver/
├── docker-compose.yml
├── docker-compose.dev.yml
└── docs/
    ├── 01_system_design.md          ← this document
    ├── 02_scaffold_prompt.md
    └── 03_implementation_plan.md
```

---

## 15. Environment Variables

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
USGS_EARTHEXPLORER_USER=...
USGS_EARTHEXPLORER_PASS=...

# App
SECRET_KEY=...
ENVIRONMENT=development
LOG_LEVEL=INFO
```

---

## 16. Key Design Decisions & Rationale

| Decision | Rationale |
|---|---|
| PostGIS over vector file approach | Enables live spatial queries (bbox intersect, proximity) at ingestion time rather than loading everything into memory per analysis |
| Agents run against pre-queried PostGIS data, not raw APIs | Decouples real-time analysis from upstream API availability and rate limits |
| Confidence-weighted scoring vs. simple weighted average | Handles data gaps gracefully — a cell with no geochemical data doesn't get penalized by a zero score, it gets down-weighted confidence |
| Grid-based output vs. continuous raster | Easier to attach per-cell evidence breakdowns; more interpretable to end user; simpler to export |
| SSE for job progress vs. polling | Lower overhead than websockets for one-directional progress stream; works well with FastAPI |
| Mineral-specific weight presets | Deposit controls differ dramatically by mineral type; a single weight set would be misleading |
| Land status as overlay, not filter | A high-scoring cell that happens to be claimed is still geologically meaningful intelligence. The goal is to find gold, not to pre-filter based on current claim status, which changes. |
| Separate gold_lode and gold_placer modes | The two search problems are fundamentally different: lode searches for source rocks and structures; placer searches for hydraulic concentration points. A single mode would require compromised agent weights. |
| WA DNR as priority geology source over federal | Washington State DNR geological data is higher resolution and more current for WA than federal equivalents. Always prefer state source when available. |
| Placer → Lode inference built into placer agent | Placer concentrations are field-accessible indicators that point upstream toward lode sources. Building this inference into the agent enables the intended field workflow: find placer → identify lode candidate zone → verify in field. |

---

*Last updated: v0.2 — vision, geographic focus, placer gold mode, WA geology context, and land status overlay added.*
*Update this document when architecture decisions change. Both collaborators should review before major changes to agent prompt logic or data source prioritization.*
