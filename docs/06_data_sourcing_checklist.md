# Data Sourcing Checklist — GeoProspector (WA Gold)

**Date:** 2026-06-03
**Purpose:** A prioritized, link-by-link acquisition list for the data layers GeoProspector
does not yet have. Each entry lists what it is, where to get it, format, native CRS, which
agent it feeds, and an ingestion note. Companion to `docs/05_knowledge_base_intake_2026-05-04.md`
(which covers *knowledge files*); this doc covers *spatial data layers*.

All geometries land in the DB as **SRID 4326 (WGS84)** per `CLAUDE.md`. "Reproject" below
means convert from the listed native CRS to 4326 on ingest.

---

## How this list is prioritized

Three factors, in order:

1. **Gold weight** — `weights.py` ranks the agents: structure 0.30, lithology 0.25,
   geochemistry 0.20, historical 0.15, remote sensing 0.07, proximity 0.03. Data feeding a
   high-weight agent moves the score more.
2. **Actionability** — a high-scoring cell that's inside a Wilderness area or on tribal land
   is not prospectable. The legal-tenure mask is what keeps the output honest.
3. **Blind-target value** — surface GIS overlays mostly re-find known mines (see
   `docs/geoprospector_critique.md`). Geophysics is the only data class that detects
   deposits hidden under cover or at depth.

The result: the two cheapest, highest-certainty wins are **(Tier 0)** loading the ~615 MB
already on disk and **(knowledge)** writing `structure/gold.md`. The highest-leverage *new*
acquisitions are the **legal mask (Tier 1)** and the **Republic Graben geophysics (Tier 2)**.

---

## Tier 0 — Already on disk, NOT loaded (load, don't find)

~615 MB sitting in `data/raw/`. This is the fastest path to real spatial context. No
sourcing required — only loaders + a working `_build_spatial_context()`.

| Dataset | Feature classes / layers | Feeds agent(s) | Native CRS | Note |
|---|---|---|---|---|
| `ger_portal_surface_geology_24k/WGS_Surface_Geology_24k.gdb` | `geologic_unit_poly`, `fault`, `fold`, `dike`, `contact`, `attitude_point` | **lithology, structure** | NAD83 / WA-specific | The single most valuable on-disk asset — feeds the #1 and #2 weighted agents statewide. |
| `ger_portal_mines_minerals/WGS_Mines_Minerals.gdb` | `Gold_Silver_Locations`, `Metallic_Mineral_Locations`, `IAML_Sites`, `Mining_Districts_WA` | **proximity, historical** | NAD83 | Direct WA DNR deposit/mine inventory. |
| `of00-495/*.e00` | `newageol`, `newafaul`, `newafold`, `newadike` | **lithology, structure** | UTM 11N / NAD27 (EPSG:26711) | NE WA WofE rasters. Reproject from 26711. Loader plan in `docs/04_usgs_of00_495_dataset.md`. |

Ingest note: GDAL/`ogr2ogr` reads `.gdb` and `.e00` directly; reproject to 4326 and upsert
into PostGIS feature tables. Wire the results into `orchestrator._build_spatial_context()`
under the keys `geology_units`, `fault_traces`, `known_deposits`, `historic_mines`.

---

## Tier 1 — Legal tenure mask (highest actionability) — GO FIND

Without this, the app will rank cells the user legally cannot touch. Build it as a hard
front-end filter, not an afterthought. None of this is on disk.

| Source | What it is | URL | Format | Native CRS | Feeds |
|---|---|---|---|---|---|
| **PAD-US 4.1** (USGS GAP) | Wilderness, NWR, parks, conservation easements — the "can't stake a claim here" layer | usgs.gov/programs/gap-analysis-project/science/pad-us-data-download | ArcGIS GDB, per-state download | NAD83 Albers | land mask (all agents / final filter) |
| **BLM Mining Claims — Not Closed** (MLRS) | Active federal lode/placer claims, PLSS-geocoded | gis.blm.gov/nlsdb/rest/services/HUB/BLM_Natl_MLRS_Mining_Claims_Not_Closed/FeatureServer/0 | ArcGIS FeatureServer → GeoJSON | EPSG:4326 / 3857 | **proximity**, land status |
| **BLM Surface Management Agency (SMA)** | Federal surface ownership (BLM/USFS/NPS/etc.) — what's open vs withdrawn | gbp-blm-egis.hub.arcgis.com (search "Surface Management Agency") | FeatureServer / shapefile | EPSG:4326 | land mask |
| **BIA AIAN-LAR** | Federal reservation + trust land boundaries | biamaps.doi.gov / onemap-bia-geospatial.hub.arcgis.com | FeatureServer / shapefile | NAD83 | land mask |

Ingest note: the BLM MLRS "Not Closed" layer doubles as a *proximity* signal — active claims
mean someone is currently spending money on the belief gold is there. Pull it once via the
FeatureServer query API (paginate), store as points/polys.

---

## Tier 2 — Geophysics (finds blind targets) — GO FIND

The only data class that sees through cover. If the real goal is *undiscovered* deposits
rather than re-ranking known ones, this outranks every text source. Currently no agent
consumes geophysics — wiring it may warrant a new `geophysics_agent` or folding it into
`structure` (magnetic lineaments) and `remote_sensing`.

| Source | What it is | URL | Format | Native CRS | Priority |
|---|---|---|---|---|---|
| **Republic Graben high-res aeromag + radiometric** | Low-altitude helicopter mag/radiometric over Republic, Okanogan & Kettle core complexes — *directly over WA gold country*, modern resolution | usgs.gov/data/high-resolution-airborne-magnetic-and-radiometric-survey-republic-graben-okanogan-and-kettle | grids (GeoTIFF/Geosoft) | UTM/NAD83 | **HIGHEST** — did not exist for OF01-501 |
| **Merged Aeromagnetic Data for Washington** (OFR 98-241) | Statewide 500 m aeromag grid | pubs.usgs.gov/of/1998/0241/report.pdf | grid + plots | varies | statewide baseline (coarse) |
| **NURE aeromag + aeroradiometric** (national) | DOE-era airborne mag/radiometric, conterminous US | usgs.gov (search "Aeromagnetic and Aeroradiometric Data ... NURE") | grids | NAD27/83 | fill-in outside Republic |
| **Airborne Geophysical Survey Inventory** | Interactive map of what surveys exist where — query before downloading | usgs.gov/tools/airborne-geophysical-survey-inventory-us-interactive-application | web app | — | use to scope coverage |

Caveat: statewide aeromag at 400–500 m line spacing won't resolve individual vein swarms.
The Republic Graben high-res survey is the exception — use it where it covers, the coarse
grids elsewhere.

---

## Tier 3 — Geochemistry (feeds the 0.20 agent, currently empty) — GO FIND

The geochemistry agent (3rd-highest gold weight) receives an empty `geochemical_samples`
list today. These are the real sources.

| Source | What it is | URL | Format | Native CRS | Feeds |
|---|---|---|---|---|---|
| **USGS National Geochemical Database** | Rock/sediment/soil/mineral analyses, 1962–2023, ~1.5M samples | data.usgs.gov/datacatalog → "Geochemical data for rock, sediment, soil, and mineral samples 1962–2023"; also mrdata.usgs.gov | CSV / shapefile | NAD83 | **geochemistry** |
| **NURE-HSSR stream sediment** | DOE stream-sediment reconnaissance, ~398k records, state-filterable | mrdata.usgs.gov/nure/sediment/ (OFR 97-492) | CSV / dBASE / shapefile | NAD27 → reproject | **geochemistry** |

Connector exists as a stub: `connectors/usgs_ngdb.py`. Pull WA bbox, filter to pathfinder
elements (Au, As, Sb, Hg for gold), compute background/threshold per element on ingest.

Caveat: WA stream-sediment coverage is 1970s-vintage at ~1 sample / 15 km². Useful for
regional halos, **not** for resolving a 250 m grid cell — set agent confidence accordingly.

---

## Tier 4 — DEM + multispectral (lowest gold weight: remote sensing 0.07) — do last

| Source | What it is | URL | Format | Native CRS | Feeds |
|---|---|---|---|---|---|
| **USGS 3DEP 10 m DEM** (1/3 arc-sec) | Seamless elevation — slope, lineaments, drainage | apps.nationalmap.gov/downloader; GEE `USGS/3DEP/10m` | GeoTIFF | NAD83 / NAVD88 | **remote sensing**, structure (lineaments) |
| **ASTER / Sentinel-2** | SWIR alteration mapping (clay, alunite, iron oxide) | Google Earth Engine catalog | raster (via GEE) | UTM | **remote sensing** |

Ingest note: easiest via Earth Engine (the `remote_sensing_agent` already names ASTER SWIR
ratios as its eventual method). Low ROI until Tiers 0–3 are in.

---

## Optional / strategic — critical-minerals pivot

`docs/geoprospector_critique.md` argues WA gold is mature and the fundable frontier is
critical minerals. If you pursue that, USGS Earth MRI is actively collecting *new* data:

| Source | What it is | URL |
|---|---|---|
| **Earth MRI data hub** | New geophysics, geochem, hyperspectral by state/project | usgs.gov/special-topics/earth-mri/data |
| **Earth MRI geochemical data v12.0 (Dec 2025)** | Aggregated new geochem from funded projects | usgs.gov/data (search "Geochemical data ... Earth MRI ver. 12.0") |
| **Earth MRI Acquisitions Viewer** | What's funded/collected where (check WA coverage) | mrdata.usgs.gov/earthmri/ |

---

## OCR backlog — already in your archive (`FOR GITHUB...zip`), not findable in better form

Image-only scans flagged in the intake doc. These need OCR, not sourcing:

1. **Bulletin 42 — Gold in Washington** (Huntting, ~1955), 162 pp — the canonical WA gold
   reference. **Highest OCR priority.**
2. **Hodges — Mining in the Pacific Northwest** (1897), 3 vols / 316 pp.
3. **Bulletin 36 — Sultan Basin**, ~18 pp.

A `tesseract` batch on these three unlocks most remaining text-mining value (~30–60 min).

---

## Knowledge files to write — NO external sourcing (sources already in repo)

From `docs/05_knowledge_base_intake_2026-05-04.md`, citation-ready today:

- **`agents/knowledge/structure/gold.md`** — MISSING and highest-weighted (0.30). Build from
  OF01-501 fault weights (1,700 m buffer, W+=1.4, contrast 5.0) + Index District arcuate-vein
  geometry. **Do this first.**
- Expand **`lithology/gold.md`** — Snoqualmie Batholith, Index, Conconully, OF01-501 weights.
- Expand **`historical/gold.md`** — Money Creek/Apex, Monte Cristo (Mystery & Justice),
  Conconully, Index, Devils Canyon (negative example).

---

## Agent → data crosswalk (target state)

| Agent | Gold wt | Knowledge file | Spatial data (and where it comes from) |
|---|---|---|---|
| Structure | 0.30 | ❌ write it | faults/folds: Surface Geology 24k (disk) + OF00-495 (disk); mag lineaments: Tier 2 |
| Lithology | 0.25 | ✅ deep | `geologic_unit_poly`: Surface Geology 24k (disk) + OF00-495 (disk) |
| Geochemistry | 0.20 | ❌ | NGDB + NURE-HSSR (Tier 3) |
| Historical | 0.15 | ✅ deep | Mines & Minerals gdb (disk) + OCR'd bulletins |
| Remote sensing | 0.07 | ❌ | 3DEP + ASTER/Sentinel (Tier 4) |
| Proximity | 0.03 | ❌ | Mines & Minerals gdb (disk) + BLM active claims (Tier 1) |

---

## Honest caveats

- Tiers 1, 3, 4 mostly make the existing model *more honest and complete*; they will not, by
  themselves, find new deposits. Only Tier 2 (geophysics) does that.
- Geochem and statewide aeromag resolution (km-scale) is coarser than the 250 m grid — don't
  let the grid imply precision the data doesn't have. Surface this in agent `confidence`.
- The single biggest score-mover for the lowest effort is **loading Tier 0 + writing
  `structure/gold.md`** — both fully unblocked, no acquisition needed.

---

## References

- PAD-US 4.1 — https://www.usgs.gov/data/protected-areas-database-united-states-pad-us-4
- BLM MLRS — https://www.blm.gov/services/land-records/mlrs ; Hub: https://gbp-blm-egis.hub.arcgis.com
- BIA Open Data — https://onemap-bia-geospatial.hub.arcgis.com/
- USGS National Geochemical Database — https://www.usgs.gov/centers/gggsc/science/national-geochemical-database
- NURE-HSSR sediment — https://mrdata.usgs.gov/nure/sediment/
- Republic Graben high-res survey — https://www.usgs.gov/data/high-resolution-airborne-magnetic-and-radiometric-survey-republic-graben-okanogan-and-kettle
- Merged WA aeromag (OFR 98-241) — https://pubs.usgs.gov/of/1998/0241/report.pdf
- Airborne Geophysical Survey Inventory — https://www.usgs.gov/tools/airborne-geophysical-survey-inventory-us-interactive-application
- 3DEP / The National Map — https://apps.nationalmap.gov/downloader/
- Earth MRI data — https://www.usgs.gov/special-topics/earth-mri/data
