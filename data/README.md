# `data/` — Raw and Derived Datasets

This directory holds the geospatial source data the GeoProspector pipeline ingests.
The raw downloads under `data/raw/` are **not committed to git** (see `.gitignore`)
because the binary GIS formats are large (hundreds of MB), redistributable from the
original publishers, and not part of the application source.

If you've cloned this repo and want to run ingestion locally, recreate `data/raw/`
by following the download instructions below. Each subdirectory should be unzipped
in place so the layout matches what the connectors expect.

## Layout

```
data/
├── README.md                           ← this file (committed)
├── reference/                          ← COMMITTED — small, map-servable extracts
│   ├── gnis_wa.tsv                     ← GNIS place names (toponym matcher)
│   ├── wa_wilderness.geojson           ← USFS designated wilderness
│   ├── wa_occurrences.geojson          ← 3,314 WA DNR mineral occurrences
│   ├── wa_mining_districts.geojson     ← 68 mining districts with production
│   └── wa_iaml.geojson                 ← abandoned workings: adits, shafts, dumps
├── derived/                            ← gitignored — machine-built, reproducible
│   ├── wa_geology.sqlite               ← 24k units/faults/folds/dikes + R*Tree
│   └── of00495.sqlite                  ← OF-00-495 grids on 250 m analysis cells
├── user_sites/                         ← gitignored — imported field pins
├── literature/                         ← gitignored — scanned reports (see below)
├── runs/                               ← gitignored — one JSON per analysis
├── cache/cells.sqlite                  ← gitignored — per-cell score cache
└── raw/                                ← gitignored — the upstream downloads
    ├── ger_portal_mines_minerals/      ← WA DNR / WGS Mines & Minerals
    ├── ger_portal_surface_geology_24k/ ← WA DNR / WGS Surface Geology 1:24k
    └── of00-495/                       ← USGS OFR 00-495 (NE Washington geology)
```

> **Status:** all three raw datasets are read — by the offline build scripts, never at
> request time. They are the agents' evidence base. The build commands and when to run them
> are in the [root README](../README.md#build-the-evidence-base--one-time-25-min); this file
> is the reference for what each dataset *is*.
>
> Reading the geodatabases needs `pyogrio` (in `requirements-dev.txt`; the wheel bundles
> GDAL, so no `ogr2ogr` or Docker required). The **runtime** needs only `sqlite3` and
> `shapely` against the outputs, so a deployment can ship `data/derived/` and skip
> `data/raw/` entirely.
>
> **Trap:** `bbox=` / `mask=` spatial push-down returns **zero features** on both WA DNR
> geodatabases — the `.spx` indexes are stale and OGR short-circuits to an empty result with
> no error, which is indistinguishable from "no data here". The build scripts read whole
> layers for that reason.


## Datasets

### `ger_portal_mines_minerals/`

Washington Department of Natural Resources — Geology and Earth Resources
Division. Statewide compilation of mines, mineral occurrences, and abandoned-mine
inventory features.

- Source: WA DNR Geologic Information Portal — Mines and Minerals package
  (https://geologyportal.dnr.wa.gov/)
- Approx size: 77 MB
- Format: ESRI File Geodatabase (`WGS_Mines_Minerals.gdb`), ArcGIS map package
  (`mines_and_minerals.mpkx`), `.lyrx` layer files, HTML metadata
- Key feature classes: `Gold_Silver_Locations`, `Metallic_Mineral_Locations`,
  `Metallic_Mineral_Occurences`, `Nonmetallic_Mineral_Locations`,
  `Coal_Mine_Locations`, `Coal_Fields`, `Coal_Reserves`, `IAML_Sites` (Inactive
  and Abandoned Mines), `Hazardous_Minerals_Citations`, `Mining_Districts_WA`,
  plus arsenic / asbestos / mercury / radon / uranium location layers.
- See `README_mines_minerals.docx` and the `metadata/` HTML files for per-layer
  documentation.

**Read by** `scripts/build_reference_extracts.py` → the three `data/reference/*.geojson`
files. Layers used and their real row counts: `Gold_Silver_Locations` (1,467),
`Metallic_Mineral_Locations` (1,847), `Mining_Distircts_WA` (68 — *the typo in the layer
name is upstream and real*), `IAML_Sites` (97), `IAML_Features` (359), and
`Metallic_Minerals_Scanned_Documents` (107,739 rows joining sites to scanned literature by
`SITE_ID`).

Three attribute columns are the reason this beats MRDS for this project:

| column | measured distribution (gold/silver layer) | what it buys |
|---|---|---|
| `ASSAYS` | `'yes'` 649, empty 818 | turns the assay-primacy rule in `knowledge/historical/gold.md` from an inference into a lookup |
| `PRODUCTION` | `'yes'` 450, empty 1,017 | documented production vs a mere occurrence |
| `LOCATION_ACCURACY` | 8 distinct strings; **917 "coordinate accuracy highly variable", 24 "mining district centroid"**, 43 GPS, 244 topo | says *which* records are survey-grade instead of caveating all of them equally |

The 24 district centroids matter more than their count suggests: a district centre stored in a
site's row will anchor a confident distance argument about ground it says nothing about. They
are mapped to `accuracy_class: "district_centroid"`, passed to the model explicitly labelled
as having no site position, excluded from benchmark ground truth, and drawn on the map so they
look imprecise.

### `ger_portal_surface_geology_24k/`

Washington 1:24,000 surface geology compilation — bedrock and surficial map
units, faults, folds, dikes, and contacts statewide.

- Source: WA DNR Geologic Information Portal — Surface Geology 1:24k package
  (https://geologyportal.dnr.wa.gov/)
- Approx size: 218 MB
- Format: ESRI File Geodatabase (`WGS_Surface_Geology_24k.gdb`), ArcGIS map
  package (`Surface_geology_24k.mpkx`), `.lyrx` layer file, HTML metadata
- Key feature classes: `geologic_unit_poly`, `contact`, `fault`, `fold`, `dike`,
  `attitude_point`, `map_line`, `map_index`, `geologic_date`.
- See `README_surface_geology_24k.doc` and the `metadata/` HTML files.

**Read by** `scripts/build_geology_store.py` → `data/derived/wa_geology.sqlite`:
`geologic_unit_poly` (82,692), `fault` (12,416), `fold` (3,350), `dike` (2,467),
`volcanic_vent` (107), `unit_description` (9,637 rows → 2,184 distinct units).
`contact`'s 142,727 lines are deliberately skipped — heavy, and the per-cell unit
fractions already tell an agent when a cell straddles a contact.

**Two caveats that change how you should read it.**

*It is not statewide.* This is a mosaic of **342 published quadrangles**, and its holes are
badly placed for gold: no coverage at Monte Cristo, Sultan Basin, Lennox Creek, the North Fork
Snoqualmie corridor, or Republic. 38,060 of the 82,692 polygons sit in the −122° longitude
band (the Puget lowland); 1,862 sit in −118°. Of the eleven AOIs in `benchmarks/labels.yaml`,
exactly one has coverage and it is a *null* AOI. See CLAUDE.md → Known Gap #2b for the measured
table. NE Washington is served by OF-00-495 instead, which is the better source there anyway.

*Unit labels are quad-local.* `GUNIT_TXT` values like `Evs(t)`, `Ev(p)`, `Qfs(t2)` are scoped
to the publication they came from, so the same rock can carry different labels either side of a
quad line. They do **not** match the OF01-501 weights-of-evidence codes — `Evsf`, `Evst`,
`Eck`, `Evkct`, `Evkf`, `Eco` are all absent from the 2,186 distinct values. Do not attempt to
match them by string similarity; `Evs(t)` is not `Evst`, and the resulting scores would look
entirely plausible.

`fault`/`fold`/`dike`/`volcanic_vent` are unified into one `lin` table with an `azimuth_deg`
folded into [0, 180) — a fault trace has no direction, so the published favourable band of
345°–030° becomes `az <= 30 or az >= 165`. Both halves count; testing only the first would
discard every NNW-trending structure.

### `of00-495/`

USGS Open File Report 00-495 — *Geologic Datasets for Weights of Evidence
Analysis in Northeast Washington*. Boleneus & Causey, 2000. Covers
117–120°W / 48–49°N (Colville, Chewelah, Republic, Nespelem, Omak, Oroville
1:100k quads).

- Source: USGS — https://geopubs.wr.usgs.gov/open-file/of00-495 (also available
  via FTP at `geopubs.wr.usgs.gov:/pub/open-file/of00495`)
- Approx size: 314 MB
- Format: ArcInfo Interchange (`.e00`) raster grids — `newageol.e00` (geologic
  units), `newafold.e00` (folds), `newafaul.e00` (faults), `newadike.e00`
  (dikes); plus `of00-495.pdf` (report text), `of00-495.met` (metadata),
  appendix text, and JPEG figures.
- Background: see `docs/04_usgs_of00_495_dataset.md`.

**Read by** `scripts/build_of00495.py`, via the pure-Python E00 GRID reader in
`scripts/lib/e00.py` → `data/derived/of00495.sqlite` (395,605 rows on 250 m cells of the fixed
EPSG:5070 grid). No GDAL required: the `.e00` files are ASCII — a six-line header, then five
fixed-width integers per line, exactly `ncols × nrows` values, terminated by `EOG`, with the
value-attribute table in the trailing `IFO` section. The reader asserts the value count and
fails loudly; a silent off-by-one there would shift every cell's geology by one pixel and
nothing downstream would notice. This supersedes `scripts/convert_of00_495.sh`, which still
works but needs Docker.

**Why this dataset is not redundant with the 24k geology.** Its VAT carries the *standardised*
Appendix A-1 labels, which are the ones the published OF01-501 contrast values are keyed to. It
is the only dataset on disk that can attach a measured predictive weight to a cell rather than
a model's opinion of one. Favourable-unit cell counts as built: `Evsf` 10,554, `Evkf` 3,405,
`Eco` 1,145, `Evkct` 464, `Eck` 427, `Evst` 274. 21,400 cells carry a fault code.

| grid | size | native cell | in-file VAT | code meanings |
|---|---|---|---|---|
| `newageol` | 4,476 × 2,310 | 50 m | 170 records, `VALUE, COUNT, label` | labels in file; Appendix A-1 correlates them |
| `newafold` | 4,109 × 2,224 | 50 m | 14 records, `VALUE, COUNT` — sparse presence | **Appendix B-1** |
| `newafaul` | 2,224 × 1,143 | 100 m | 14 records, `VALUE, COUNT` — sparse presence | **Appendix B-2** |
| `newadike` | 1,110 × 571 | 200 m | 14 records, `VALUE, COUNT, S_value` | **Appendix B-3** |

The fault and fold VATs have **empty label columns in the `.e00` file**, which makes the codes
look uninterpretable if the raster is all you read. They are fully defined in Appendices B-1
and B-2 of `of00-495.pdf`, and `app/spatial/wofe_grid.py` transcribes both tables:

| fault codes | meaning | fold codes | meaning |
|---|---|---|---|
| 0 | unknown type | 1–3 | anticline |
| 1–4 | fault, unknown offset | 7–9 | overturned anticline |
| 7–10 | **thrust fault** | 13, 15 | syncline |
| 31, 33 | **low-angle normal fault** | 19–21 | overturned syncline |
| 43–45 | **normal fault** | 31–33 | monocline, anticlinal bend |

That distinction is load-bearing rather than descriptive: the OF01-501 predictor is
specifically a **normal** fault. A thrust is Mesozoic contraction that pre-dates the Eocene
ore event, and a low-angle normal fault is a core-complex detachment — the same extension but
regional-scale plumbing, not a steep vein conduit.

What these rasters genuinely cannot give you is **orientation**. They record presence per
pixel, not azimuth, so the 345°–030° trend half of the OF01-501 rule cannot be applied where
they are the only structural source. The prompts state that explicitly instead of letting the
model assume the favourable case.

Native CRS is UTM 11N / NAD27 (EPSG:26711), reprojected on build.

## Source literature archive (`data/literature/`)

Separate from the GIS datasets above, a ~187 MB archive of scanned published
reports backs the district-level knowledge in
`backend/app/agents/knowledge/historical/gold.md` and the per-source analyses in
`docs/intake_analyses/`. It ships as `FOR GITHUB-<timestamp>.zip` and is
**gitignored**. Extracted to `data/literature/` on 2026-08-12 (28 PDFs, 4 docx), with the
`FOR GITHUB/` prefix stripped so the layout matches what `scripts/extract_pdfs.py`
expects (`data/literature/I90Hiker`, …).

Extract it with Python, not `unzip`: one filename contains typographic quotes
(*"There's Gold in Them, Thar Hills"*) that Info-ZIP mangles to `???` and then fails on with a
misleading "write error (disk full?)". `zipfile` handles it, decoding cp437 back to UTF-8 when
the archive's UTF-8 flag is unset.

Contents, by folder:

| Folder | Documents |
|---|---|
| `I90Hiker/` | USGS MF-1380-E *Mines and Prospects Map of the Glacier Peak Roadless Area* (2 sheets); *Rockhound's Guide to Washington* Vols. 1 and 3; WA DGER Bulletin 36 (Sultan district); Survey No. 7, *Geology and Ore Deposits of the Index Mining District*; Information Circular 40 |
| `Mining in the Pacific Northwest LK Hodges/` | Hodges, *Mining in the Pacific Northwest*, pp. 1–100 and 201–316 |
| `Reports/` | WA DGER Apex mine report; *Inventory of Washington Minerals* |
| `Devils Canyon Mining/` | DMEA file 3557 |
| `USGS NE WA prospectivity model/` | USGS assessment methodology (also tracked at `docs/USGS_assessment_methodology.pdf`) |
| root | North Fork hand-drawn map; `CURRENT links.docx`, `similar models.docx`, `sources to do.docx` |

The committed markdown under `docs/intake_analyses/` is the derived, machine-
readable extract of these PDFs and is what agents and knowledge files should
cite. Regenerate it with `scripts/extract_pdfs.py` (see `--help`).

## Re-downloading

If `data/raw/` is missing or empty after cloning, fetch each dataset from the
links above and unzip into the matching subdirectory, then run the three build
scripts.

A missing `data/raw/` will not break the backend. Every consumer degrades: absent
artifacts mean greyed-out map toggles (`GET /reference/layers` reports which exist),
thinner agent prompts, and an empty `context_sources` in the run record. What it
does mean is that agents are back to scoring from model prior alone — which is a
much worse run, and the run record will say so rather than looking normal.

## Why these aren't in git

- Total size is ~608 MB across the three datasets (77 + 218 + 314); one `.e00`
  file alone is 149 MB, exceeding GitHub's 100 MB hard limit.
- The data is redistributable from the original publishers (WA DNR and USGS)
  and is more reliably sourced from there.
- We only need to track our derived / normalized outputs, not the upstream
  binary GIS files.

## What is and is not committed

The line is size and servability, not provenance — all of it is public data.

- **`data/reference/` is committed.** These are small (the largest is 4.2 MB) and the
  frontend fetches them over HTTP as map overlays. Rebuilding them is **byte-identical**
  when the source data has not changed: features are sorted by id, and
  `write_geojson()` keeps the existing `built_at` stamp rather than restamping, so a
  no-op rebuild does not show up as a 3,314-line diff. That matters — a file that churns
  on every build teaches everyone to stop reading its diffs. One feature per line for
  the same reason: a single-line 4 MB GeoJSON is unreviewable.
- **`data/derived/` is not.** `wa_geology.sqlite` (48 MB) and `of00495.sqlite` (31 MB)
  are large, fully reproducible from `data/raw/` in about two minutes, and only ever
  read by backend code — nothing serves them to a browser.
- **`data/user_sites/` is not**, and for a different reason: those are somebody's own
  field notes and GPS positions of workings they walked to. The "not in any database"
  subset is genuinely sensitive and is not ours to publish.

Raw `.gdb` / `.e00` / `.mpkx` stay out regardless: ~608 MB total, and one `.e00` alone
is 142 MB against GitHub's 100 MB hard limit.
