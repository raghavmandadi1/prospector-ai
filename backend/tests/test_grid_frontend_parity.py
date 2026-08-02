"""
Parity between the TypeScript coordinate maths and the Python grid.

`frontend/src/components/Map/coords.ts` reimplements EPSG:5070 by hand so the
map can show the cell id under the cursor without shipping proj4. That means
two independent implementations of the same projection — and if they drift, the
map quietly points at the wrong cell while looking entirely correct.

This test runs the TypeScript through node and compares against pyproj. It
skips when node is unavailable rather than failing, since the backend does not
otherwise need it.

Run:  .venv/bin/python -m pytest backend/tests/test_grid_frontend_parity.py -q
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from app.scoring.grid import cell_id_for_point

COORDS_TS = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "Map"
    / "coords.ts"
)

# Spread across Washington: western Cascades, NE gold country, the Palouse, the
# Olympics and the far corners of the grid envelope.
POINTS = [
    (-121.44, 48.03),   # Monte Cristo
    (-121.55, 47.65),   # NF Snoqualmie
    (-118.74, 48.65),   # Republic
    (-117.06, 48.85),   # Metaline
    (-122.33, 47.61),   # Seattle
    (-123.90, 47.90),   # Olympic Peninsula
    (-119.50, 46.20),   # Columbia Basin
    (-116.95, 45.60),   # SE corner
    (-124.70, 48.90),   # NW corner
]

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


@pytest.fixture(scope="module")
def ts_results(tmp_path_factory):
    """Run the real coords.ts under node and return its output.

    Node's native type stripping runs the actual source file — no transpile
    step, no hand-rolled type remover that could quietly diverge from what the
    browser executes.
    """
    work = tmp_path_factory.mktemp("coords")
    (work / "coords.ts").write_text(
        COORDS_TS.read_text(encoding="utf-8"), encoding="utf-8"
    )

    runner = textwrap.dedent(
        f"""
        import {{ toAlbers5070, cellIdForPoint, toUTM }} from './coords.ts'

        const points = {json.dumps(POINTS)};
        const out = points.map(([lon, lat]) => ({{
          lon, lat,
          albers: toAlbers5070(lon, lat),
          cell1000: cellIdForPoint(lon, lat, 1000),
          cell125: cellIdForPoint(lon, lat, 125),
          utm: toUTM(lon, lat),
        }}));
        console.log(JSON.stringify(out));
        """
    )
    path = work / "run.ts"
    path.write_text(runner, encoding="utf-8")
    proc = subprocess.run(
        ["node", "--experimental-strip-types", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        if "experimental-strip-types" in proc.stderr or "Unknown" in proc.stderr:
            pytest.skip("node is too old for native TypeScript stripping")
        pytest.fail(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_albers_projection_matches_pyproj(ts_results):
    """Sub-millimetre agreement, or the cell boundaries are in different places."""
    import pyproj

    to_grid = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    for r in ts_results:
        x, y = to_grid.transform(r["lon"], r["lat"])
        assert r["albers"]["x"] == pytest.approx(x, abs=1e-3), r
        assert r["albers"]["y"] == pytest.approx(y, abs=1e-3), r


def test_cell_ids_match_the_backend_exactly(ts_results):
    """The string the readout shows must be the string in the run record."""
    for r in ts_results:
        assert r["cell1000"] == cell_id_for_point(r["lon"], r["lat"], 1000), r
        assert r["cell125"] == cell_id_for_point(r["lon"], r["lat"], 125), r


def test_utm_matches_pyproj(ts_results):
    """Within a metre — this readout gets transcribed into field notes."""
    import pyproj

    for r in ts_results:
        zone = r["utm"]["zone"]
        epsg = 32600 + zone  # northern hemisphere
        t = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        e, n = t.transform(r["lon"], r["lat"])
        assert r["utm"]["easting"] == pytest.approx(e, abs=1.0), r
        assert r["utm"]["northing"] == pytest.approx(n, abs=1.0), r


def test_northeast_washington_lands_in_utm_zone_11(ts_results):
    """Republic and Metaline are east of 120°W.

    A single hardcoded UTM zone for the analysis grid would have excluded them;
    the readout still has to report the correct zone for field use.
    """
    by_lon = {round(r["lon"], 2): r for r in ts_results}
    assert by_lon[-118.74]["utm"]["zone"] == 11
    assert by_lon[-117.06]["utm"]["zone"] == 11
    assert by_lon[-121.44]["utm"]["zone"] == 10
