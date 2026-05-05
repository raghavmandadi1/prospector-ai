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
└── raw/                                ← gitignored
    ├── ger_portal_mines_minerals/      ← WA DNR / WGS Mines & Minerals
    ├── ger_portal_surface_geology_24k/ ← WA DNR / WGS Surface Geology 1:24k
    └── of00-495/                       ← USGS OFR 00-495 (NE Washington geology)
```

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
- Approx size: 320 MB
- Format: ArcInfo Interchange (`.e00`) raster grids — `newageol.e00` (geologic
  units), `newafold.e00` (folds), `newafaul.e00` (faults), `newadike.e00`
  (dikes); plus `of00-495.pdf` (report text), `of00-495.met` (metadata),
  appendix text, and JPEG figures.
- Background: see `docs/04_usgs_of00_495_dataset.md`.

## Re-downloading

If `data/raw/` is missing or empty after cloning, fetch each dataset from the
links above and unzip into the matching subdirectory. The connectors under
`backend/app/connectors/` read these files by relative path; they will fail
fast with a clear error message if a dataset is missing.

## Why these aren't in git

- Total size is ~615 MB across the three datasets; one `.e00` file alone is
  149 MB, exceeding GitHub's 100 MB hard limit.
- The data is redistributable from the original publishers (WA DNR and USGS)
  and is more reliably sourced from there.
- We only need to track our derived / normalized outputs, not the upstream
  binary GIS files.

If at some point we want versioned access to a derived snapshot (e.g., a Parquet
extract of `Gold_Silver_Locations`), drop it under `data/derived/` and add an
explicit `!data/derived/` rule above — but raw `.gdb` / `.e00` / `.mpkx` should
stay out.
