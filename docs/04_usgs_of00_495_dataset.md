# USGS OF 00-495 — NE Washington Geology (Boleneus & Causey, 2000)

**Status:** received as `newafull (1).tar.gz`, decompressed and inspected 2026-05-04.

## What it is

USGS Open-File Report 00-495, *"Geologic data sets for weights-of-evidence analysis in
northeast Washington — 1. Geologic raster data."* A digitally compiled geologic
dataset built explicitly for **mineral-deposit weights-of-evidence (W-of-E) modeling** —
i.e. exactly what GeoProspector does, just with 2000-era tooling.

Coverage: **117–120°W, 48–49°N** — the six 1:100,000 quadrangles of Colville, Chewelah,
Republic, Nespelem, Omak, Oroville. This is the heart of NE Washington gold country
(Republic epithermal district, Buckhorn Mountain, etc.).

## Files in the tarball

| File | Theme | Cell size | Format |
|---|---|---|---|
| `newageol.e00` | Geologic map units (lithology + age) | 50 m | ArcInfo GRID, exported as .e00 |
| `newafold.e00` | Folds (anticline/syncline/monocline + qualifier) | 50 m | ArcInfo GRID |
| `newafaul.e00` | Faults (thrust / normal / unknown + qualifier) | 100 m | ArcInfo GRID |
| `newadike.e00` | Dikes (Eocene / Cretaceous / Mesozoic intrusives) | 200 m | ArcInfo GRID |
| `of00-495.pdf` | Methodology + Appendix A (lithology codes) + Appendix B (value codes) | — | PDF |
| `of00-495.met` | FGDC metadata | — | Plain text |
| `fig1/2*.jpg` | Reference figures | — | JPEG |

**Native CRS:** UTM Zone 11N, **NAD27 / Clarke 1866** → `EPSG:26711`. We must reproject
to `EPSG:4326` to match the rest of the GeoProspector stack.

## Attribute schema

- `newageol.vat`: `value` (int), `count`, **`s_value`** (string, geologic unit + age code, e.g. `Eida`, `KJia`, `Czq`). The lookup from raw labels to the standardized 164-unit
  classification is **Appendix A-1** (~660 source labels → 164 standardized labels).
  Lithology descriptions are in **Appendix A-2**.
- `newadike.vat`: same `value / count / s_value` structure. 14 dike classes documented in
  Appendix B-3 (Eocene-dominant, plus KJ, Mz, TK).
- `newafaul.vat`: `value / count` only. Codes in **Appendix B-2**:
  `0` unknown, `1–4` fault unknown-offset (variants), `7–10` thrust, `31–33` low-angle normal,
  `43–45` normal.
- `newafold.vat`: `value / count` only. Codes in **Appendix B-1**:
  `1–3` anticline, `7–9` overturned anticline, `13–15` syncline, `19–21` overturned syncline,
  `31–33` monocline / anticlinal bend.

## Why this matters for GeoProspector

1. **Perfect scope match.** Project is already WA-only, gold-first (per memory). NE Washington is the highest-priority area in the state for Au prospecting; this dataset is purpose-built for the same problem.
2. **Authoritative lithology layer.** The `s_value` codes feed straight into the **Lithology agent**'s knowledge base — Appendix A-2 gives us hand-curated lithology descriptions per unit, much higher signal than what we'd get scraping Macrostrat alone.
3. **Pre-classified structures.** The fault/fold/dike value codes are ready-made categorical features for the **Structure agent** — no NLP or geometric inference needed to know "this segment is a thrust fault, concealed."
4. **Cell-size intent.** The authors chose 50/100/200 m cell sizes deliberately for a W-of-E pipeline. Our scoring grid should be aware of this so we don't oversample.

## Conversion path

### One-liner with Docker (recommended — zero install)

```bash
DATA_DIR="$(pwd)/data/of00-495"
mkdir -p "$DATA_DIR" && tar -xzf "newafull (1).tar.gz" -C "$DATA_DIR"

docker run --rm -v "$DATA_DIR":/data ghcr.io/osgeo/gdal:alpine-small-latest sh -c '
  cd /data && for f in newageol newafaul newafold newadike; do
    echo "=== $f ==="
    # 1. .e00 → GeoTIFF, reprojected EPSG:4326 (preserves raster nature)
    gdal_translate -of GTiff "$f.e00" "$f.utm.tif"
    gdalwarp -t_srs EPSG:4326 -r near "$f.utm.tif" "$f.tif"
    # 2. Polygonize the raster back to vector (so we get GeoJSON / can load to PostGIS)
    gdal_polygonize.py "$f.tif" -f GeoJSON "$f.geojson" "$f" value
  done
'
```

That gives you both:
- `*.tif` — raster, useful for fast tile rendering and zonal stats per AOI cell.
- `*.geojson` — vector polygons / lines, useful for PostGIS load + per-cell evidence
  ("AOI cell intersects 3 thrust fault segments").

### Loading into PostGIS

```bash
docker compose exec backend bash -c '
  for f in newageol newafaul newafold newadike; do
    ogr2ogr -f PostgreSQL "$DATABASE_URL" "/data/$f.geojson" \
      -nln "of00_495_$f" -t_srs EPSG:4326 -lco GEOMETRY_NAME=geom -lco FID=id -overwrite
  done
'
```

Then add a Martin source so the layers are tile-served:
```yaml
# tileserver/config.yaml
postgres:
  tables:
    of00_495_newageol: { schema: public, geometry_column: geom, srid: 4326, geometry_type: POLYGON }
    of00_495_newafaul: { schema: public, geometry_column: geom, srid: 4326, geometry_type: LINESTRING }
    of00_495_newafold: { schema: public, geometry_column: geom, srid: 4326, geometry_type: LINESTRING }
    of00_495_newadike: { schema: public, geometry_column: geom, srid: 4326, geometry_type: LINESTRING }
```

> **Note on the `.e00` driver:** Modern GDAL ships the `AVCE00` driver out of the box for both vector and raster ArcInfo Export files. The `osgeo/gdal:alpine-small-latest` image works fine; if you hit a driver-not-found error, swap to `osgeo/gdal:ubuntu-small-latest` which has more drivers compiled in.

## Where this lives in the GeoProspector codebase

Treat OF 00-495 as a **static reference dataset**, not a live connector — it doesn't refresh.

```
backend/app/
├── connectors/
│   └── usgs_of00_495.py          ← one-time loader: tarball → PostGIS tables
├── agents/
│   └── knowledge/
│       ├── ne_wa_lithology.json   ← parsed from Appendix A-2 (164 units → lithology + age + Au-affinity prior)
│       ├── ne_wa_fault_codes.json ← from Appendix B-2 (value → human label + W+ prior)
│       ├── ne_wa_fold_codes.json  ← from Appendix B-1
│       └── ne_wa_dike_codes.json  ← from Appendix B-3
```

The `usgs_of00_495.py` connector is *not* a `BaseConnector` (which expects `fetch(bbox)`).
It's a one-shot Celery task: `load_of00_495(tarball_path)` that decompresses, converts,
loads to PostGIS, and seeds the four knowledge JSON files from the PDF's Appendices.

## Followups

- [ ] Parse Appendix A-1 / A-2 from the PDF into a structured lithology JSON (rule-based extraction; ~164 rows).
- [ ] Decide whether to also pull the **vector** versions of these maps (the WA DNR original ArcInfo coverages) for higher fidelity — the .e00 here is already raster-rounded.
- [ ] Look for the companion W-of-E results paper from Boleneus/Causey/Raines (the metadata names Gary Raines as process contact — he was the W-of-E lead at USGS-Reno). Their published W+/W- weights would seed our agent priors directly.
