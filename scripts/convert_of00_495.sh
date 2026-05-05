#!/usr/bin/env bash
# Convert USGS OF 00-495 (NE Washington geology) ArcInfo .e00 grids to GeoTIFF + GeoJSON.
#
# Inputs: a directory containing the four .e00 files (newageol, newafaul, newafold, newadike)
# Outputs: <name>.tif (raster, EPSG:4326) and <name>.geojson (vector polygons / lines)
#
# Requires Docker. No host-side GDAL needed.
#
# Usage:
#   ./scripts/convert_of00_495.sh /path/to/extracted/newa/

set -euo pipefail

DATA_DIR="${1:?Usage: $0 <dir-containing-newa*.e00>}"
DATA_DIR="$(cd "$DATA_DIR" && pwd)"

GDAL_IMAGE="${GDAL_IMAGE:-ghcr.io/osgeo/gdal:ubuntu-small-latest}"

echo "Using GDAL image: $GDAL_IMAGE"
echo "Working in:       $DATA_DIR"

# Sanity check the inputs are present
for f in newageol newafaul newafold newadike; do
  [[ -f "$DATA_DIR/$f.e00" ]] || { echo "Missing $DATA_DIR/$f.e00"; exit 1; }
done

docker run --rm -v "$DATA_DIR":/data "$GDAL_IMAGE" bash -c '
  set -euo pipefail
  cd /data

  for f in newageol newafaul newafold newadike; do
    echo
    echo "==================== $f ===================="

    # Inspect what GDAL sees in the .e00 (raster + vector layers)
    gdalinfo "$f.e00" | head -25 || true

    # 1) Raster path: .e00 → GeoTIFF in native UTM 11N NAD27, then warp to WGS84
    gdal_translate -of GTiff -a_srs EPSG:26711 "$f.e00" "${f}.utm.tif"
    gdalwarp -overwrite -t_srs EPSG:4326 -r near "${f}.utm.tif" "${f}.tif"

    # 2) Vectorize for use in PostGIS / per-cell evidence queries.
    #    Polygonize emits POLYGON features tagged with the raster value.
    rm -f "${f}.geojson"
    gdal_polygonize.py "${f}.tif" -f GeoJSON "${f}.geojson" "${f}" value
  done

  echo
  echo "Done. Output files:"
  ls -lh /data/*.tif /data/*.geojson
'

cat <<EOF

Next steps:
  1. Review the GeoJSON/TIF in QGIS or a quick MapLibre viewer.
  2. Load to PostGIS:
       ogr2ogr -f PostgreSQL "\$DATABASE_URL" "$DATA_DIR/newageol.geojson" \\
         -nln of00_495_newageol -lco GEOMETRY_NAME=geom -overwrite
     (and likewise for the other three)
  3. Wire into Martin via tileserver/config.yaml.
  4. Re-attach the s_value attribute (geologic-unit code) by joining on the raster
     value field — see the AVCE00 driver docs; the .vat dbf is exposed alongside.
EOF
