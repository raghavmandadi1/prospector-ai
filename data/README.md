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
├── literature/                         ← gitignored — scanned reports (see below)
└── raw/                                ← gitignored
    ├── ger_portal_mines_minerals/      ← WA DNR / WGS Mines & Minerals
    ├── ger_portal_surface_geology_24k/ ← WA DNR / WGS Surface Geology 1:24k
    └── of00-495/                       ← USGS OFR 00-495 (NE Washington geology)
```

> **Status note:** none of the datasets below are wired into the application. The
> connectors under `backend/app/connectors/` all fetch from live web APIs; there
> is currently **no loader that reads `data/raw/`** (only the offline conversion
> step `scripts/convert_of00_495.sh`). These files were downloaded
> ahead of the ingestion work described in `docs/04_usgs_of00_495_dataset.md`
> and `docs/06_data_sourcing_checklist.md`.

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

## Source literature archive (`data/literature/`)

Separate from the GIS datasets above, a ~177 MB archive of scanned published
reports backs the district-level knowledge in
`backend/app/agents/knowledge/historical/gold.md` and the per-source analyses in
`docs/intake_analyses/`. It ships as `FOR GITHUB-<timestamp>.zip` and is
**gitignored** — unzip it to `data/literature/` if you need the primary sources.

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
links above and unzip into the matching subdirectory. No application code reads
these paths, so a missing `data/raw/` will not break the backend — it only blocks
`scripts/convert_of00_495.sh` and the offline-ingestion work still to be written.

## Why these aren't in git

- Total size is ~608 MB across the three datasets (77 + 218 + 314); one `.e00`
  file alone is 149 MB, exceeding GitHub's 100 MB hard limit.
- The data is redistributable from the original publishers (WA DNR and USGS)
  and is more reliably sourced from there.
- We only need to track our derived / normalized outputs, not the upstream
  binary GIS files.

If at some point we want versioned access to a derived snapshot (e.g., a Parquet
extract of `Gold_Silver_Locations`), drop it under `data/derived/` and add an
explicit `!data/derived/` rule above — but raw `.gdb` / `.e00` / `.mpkx` should
stay out.
