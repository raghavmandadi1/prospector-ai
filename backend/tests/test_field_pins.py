"""
Field-pin import and role enforcement (Workstream 4, §29–§30, phases 4.5–4.7).

Two of these tests exist to protect things that would fail silently and
expensively:

* `test_kml_and_gpx_produce_identical_geojson` is the §32 acceptance criterion.
  The two exports are the same three points in two formats; if the parsers
  disagree, the same field notebook produces two different datasets depending on
  which button Matthew pressed.
* `test_load_user_sites_excludes_truth_by_default` is the one that protects the
  benchmark. A `role: "truth"` pin that reaches a prompt makes the benchmark
  tautological (§30), and the failure is invisible — the map looks better, the
  numbers look better, and nothing has been learned.

Fixtures are inline XML strings rather than files on disk: they are small, and a
binary KMZ checked into the repo would be both unreviewable and against the
project's no-binary-GIS rule.

Run:  .venv/bin/python -m pytest backend/tests/test_field_pins.py -q
"""
import importlib.util
import itertools
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.spatial import user_sites
from app.spatial.user_sites import (
    ALL_ROLES,
    load_user_sites,
    role_counts,
    sites_geojson,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "import_field_pins.py"


def _load_script():
    """Import the CLI as a module so tests can call main() directly.

    A subprocess would test the same code more slowly and give worse failure
    output; the script keeps all its logic in importable functions precisely so
    this works.
    """
    spec = importlib.util.spec_from_file_location("import_field_pins", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


imp = _load_script()

_counter = itertools.count()


# --- fixtures --------------------------------------------------------------
#
# The same three points, exported two ways. Coordinates are in the Lennox Creek
# / Snoqualmie headwaters area, which is inside WA_BOUNDS.

GOOGLE_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Skykomish prospects</name>
    <Style id="icon-1"><IconStyle><scale>1</scale></IconStyle></Style>
    <Folder>
      <name>Prospect pins</name>
      <Placemark>
        <name>Lennox Creek adit</name>
        <description>Collapsed adit, 2 m portal</description>
        <TimeStamp><when>2025-09-14T17:02:00Z</when></TimeStamp>
        <Point><coordinates>-121.5000,48.0000,0</coordinates></Point>
      </Placemark>
      <Placemark>
        <name>Quartz float, upper basin</name>
        <description>Quartz-sulphide float on old dump</description>
        <Point><coordinates>-121.4880,48.0125,0</coordinates></Point>
      </Placemark>
      <Placemark>
        <name>Old road cut</name>
        <Point><coordinates>-121.5210,47.9910,0</coordinates></Point>
      </Placemark>
    </Folder>
  </Document>
</kml>
"""

#: The same KML with the placemarks at Document root. Google always wraps layers
#: in a Folder, but Gaia's KML export does not, and this is the variant that can
#: be compared to a GPX byte for byte.
FOLDERLESS_KML = GOOGLE_KML.replace("<Folder>\n      <name>Prospect pins</name>", "").replace(
    "    </Folder>\n", ""
)

GAIA_GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Gaia GPS" xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="48.0000" lon="-121.5000">
    <name>Lennox Creek adit</name>
    <desc>Collapsed adit, 2 m portal</desc>
    <time>2025-09-14T17:02:00Z</time>
  </wpt>
  <wpt lat="48.0125" lon="-121.4880">
    <name>Quartz float, upper basin</name>
    <desc>Quartz-sulphide float on old dump</desc>
  </wpt>
  <wpt lat="47.9910" lon="-121.5210">
    <name>Old road cut</name>
  </wpt>
</gpx>
"""

MULTI_FOLDER_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Everything</name>
    <Folder>
      <name>Visited</name>
      <Placemark>
        <name>Adit A</name>
        <Point><coordinates>-121.50,48.00</coordinates></Point>
      </Placemark>
      <Placemark>
        <name>Adit B</name>
        <Point><coordinates>-121.51,48.01</coordinates></Point>
      </Placemark>
    </Folder>
    <Folder>
      <name>Read about</name>
      <Placemark>
        <name>Bulletin 37 site</name>
        <Point><coordinates>-121.52,48.02</coordinates></Point>
      </Placemark>
    </Folder>
    <Document>
      <name>Nested layer</name>
      <Placemark>
        <name>Nested pin</name>
        <Point><coordinates>-121.53,48.03</coordinates></Point>
      </Placemark>
    </Document>
  </Document>
</kml>
"""

#: A KML carrying geometry we do not import. It must be counted and reported,
#: never dropped in silence — "the import worked" while half a map vanished is
#: the exact failure this reporting exists to prevent.
MIXED_GEOMETRY_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Mixed</name>
    <Placemark>
      <name>A point</name>
      <Point><coordinates>-121.50,48.00</coordinates></Point>
    </Placemark>
    <Placemark>
      <name>A track I walked</name>
      <LineString><coordinates>-121.5,48.0 -121.51,48.01</coordinates></LineString>
    </Placemark>
    <Placemark>
      <name>A claim block</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        -121.5,48.0 -121.51,48.0 -121.51,48.01 -121.5,48.0
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
  </Document>
</kml>
"""

GPX_WITH_TRACK = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Gaia GPS" xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="48.00" lon="-121.50">
    <name>Portal</name>
    <sym>Mine</sym>
  </wpt>
  <trk><name>Hike in</name><trkseg>
    <trkpt lat="48.00" lon="-121.50"/><trkpt lat="48.01" lon="-121.51"/>
  </trkseg></trk>
  <rte><name>Planned route</name><rtept lat="48.00" lon="-121.50"/></rte>
</gpx>
"""


def occurrences_file(path: Path, points: List[Dict[str, Any]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                        "properties": {"name": p["name"]},
                    }
                    for p in points
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def run_import(
    tmp_path: Path,
    *args: Any,
    out: Optional[Path] = None,
    occurrences: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the CLI and return the parsed output FeatureCollection.

    `--out` and `--occurrences` are always supplied. Without them the script
    would write into the developer's real `data/user_sites/` and read whatever
    `data/reference/wa_occurrences.geojson` happens to be on this machine, which
    would make these tests depend on the state of the repo.
    """
    out = out or tmp_path / f"out-{next(_counter)}.geojson"
    argv = [str(a) for a in args] + [
        "--out",
        str(out),
        "--occurrences",
        str(occurrences or tmp_path / "no-such-occurrences.geojson"),
    ]
    assert imp.main(argv) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def props_of(fc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [f["properties"] for f in fc["features"]]


def without(feats: List[Dict[str, Any]], *keys: str) -> List[Dict[str, Any]]:
    out = []
    for f in feats:
        f = json.loads(json.dumps(f))
        f["properties"] = {k: v for k, v in f["properties"].items() if k not in keys}
        out.append(f)
    return out


# --- §32 acceptance: KML and GPX agree -------------------------------------


def test_kml_and_gpx_produce_identical_geojson(tmp_path):
    """A Google My Maps KML and a Gaia GPX of the same points normalize alike.

    `source_file` and `folder` are the only fields that can legitimately differ:
    the filename carries the extension, and GPX has no concept of a layer. So the
    strict equality check runs against a folderless KML, and the foldered
    (i.e. real Google) export is checked field-for-field with `folder` set aside.
    """
    (tmp_path / "pins.kml").write_text(FOLDERLESS_KML, encoding="utf-8")
    (tmp_path / "pins.gpx").write_text(GAIA_GPX, encoding="utf-8")

    from_kml = run_import(tmp_path, tmp_path / "pins.kml")
    from_gpx = run_import(tmp_path, tmp_path / "pins.gpx")

    assert len(from_kml["features"]) == 3
    assert without(from_kml["features"], "source_file") == without(
        from_gpx["features"], "source_file"
    )

    # Sanity-check that the shared values are the ones we care about, not three
    # empty pins that happen to match.
    first = props_of(from_kml)[0]
    assert first["pin_id"] == "lennox-creek-adit"
    assert first["name"] == "Lennox Creek adit"
    assert first["source_note"] == "Collapsed adit, 2 m portal"
    assert first["date"] == "2025-09-14"
    assert from_kml["features"][0]["geometry"]["coordinates"] == [-121.5, 48.0]

    (tmp_path / "google.kml").write_text(GOOGLE_KML, encoding="utf-8")
    from_google = run_import(tmp_path, tmp_path / "google.kml")
    assert [p["folder"] for p in props_of(from_google)] == ["Prospect pins"] * 3
    assert without(from_google["features"], "source_file", "folder") == without(
        from_gpx["features"], "source_file", "folder"
    )


def test_kmz_unzips_and_parses(tmp_path):
    kmz = tmp_path / "MyMap.kmz"
    with zipfile.ZipFile(kmz, "w") as zf:
        zf.writestr("doc.kml", GOOGLE_KML)

    (tmp_path / "google.kml").write_text(GOOGLE_KML, encoding="utf-8")
    from_kmz = run_import(tmp_path, kmz)
    from_kml = run_import(tmp_path, tmp_path / "google.kml")

    assert len(from_kmz["features"]) == 3
    assert props_of(from_kmz)[0]["source_file"] == "MyMap.kmz"
    assert without(from_kmz["features"], "source_file") == without(
        from_kml["features"], "source_file"
    )


def test_multi_folder_kml_keeps_every_layer(tmp_path):
    """Every Folder and nested Document survives, and the layer name is kept.

    §29 warns that Google splits layers into separate documents and that this is
    where pins get lost; the folder name is also the only bulk handle for
    sorting "I stood here" from "I read about this" (§33).
    """
    (tmp_path / "all.kml").write_text(MULTI_FOLDER_KML, encoding="utf-8")
    fc = run_import(tmp_path, tmp_path / "all.kml")

    names = [p["name"] for p in props_of(fc)]
    assert names == ["Adit A", "Adit B", "Bulletin 37 site", "Nested pin"]
    assert [p["folder"] for p in props_of(fc)] == [
        "Visited",
        "Visited",
        "Read about",
        # A nested <Document> is a layer; the outermost one is the map title and
        # is deliberately not used as a folder name.
        "Nested layer",
    ]


def test_lines_and_polygons_are_counted_not_silently_dropped(tmp_path, capsys):
    (tmp_path / "mixed.kml").write_text(MIXED_GEOMETRY_KML, encoding="utf-8")
    fc = run_import(tmp_path, tmp_path / "mixed.kml")
    assert [p["name"] for p in props_of(fc)] == ["A point"]

    report = capsys.readouterr().out
    assert "geometry not imported" in report
    assert "LineString" in report and "Polygon" in report


def test_gpx_tracks_and_routes_are_counted_and_sym_kept(tmp_path, capsys):
    (tmp_path / "t.gpx").write_text(GPX_WITH_TRACK, encoding="utf-8")
    fc = run_import(tmp_path, tmp_path / "t.gpx")

    assert len(fc["features"]) == 1
    assert props_of(fc)[0]["symbol"] == "Mine"
    report = capsys.readouterr().out
    assert "track" in report and "route" in report


def test_rerun_is_byte_identical(tmp_path):
    """Idempotent: no timestamps, no ordering wobble, so a diff means something."""
    (tmp_path / "pins.kml").write_text(GOOGLE_KML, encoding="utf-8")
    a, b = tmp_path / "a.geojson", tmp_path / "b.geojson"
    run_import(tmp_path, tmp_path / "pins.kml", out=a)
    run_import(tmp_path, tmp_path / "pins.kml", out=b)
    assert a.read_bytes() == b.read_bytes()


# --- role enforcement ------------------------------------------------------


def test_role_defaults_to_display(tmp_path, capsys):
    (tmp_path / "pins.kml").write_text(GOOGLE_KML, encoding="utf-8")
    fc = run_import(tmp_path, tmp_path / "pins.kml")

    assert {p["role"] for p in props_of(fc)} == {"display"}
    assert fc["properties"]["counts_by_role"] == {"display": 3, "truth": 0, "evidence": 0}
    assert "nothing reaches the model" in capsys.readouterr().out


def test_invalid_role_on_the_cli_is_rejected(tmp_path):
    (tmp_path / "pins.kml").write_text(GOOGLE_KML, encoding="utf-8")
    with pytest.raises(SystemExit):
        imp.main(
            [
                str(tmp_path / "pins.kml"),
                "--role",
                "gospel",
                "--out",
                str(tmp_path / "x.geojson"),
            ]
        )


def test_invalid_role_in_annotations_csv_is_rejected(tmp_path):
    """An unknown enum value is a hard error, never a silent fallback."""
    (tmp_path / "pins.kml").write_text(GOOGLE_KML, encoding="utf-8")
    csv_path = tmp_path / "ann.csv"
    csv_path.write_text(
        "pin_id,role\nlennox-creek-adit,evidnce\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit) as exc:
        run_import(tmp_path, tmp_path / "pins.kml", "--annotations", csv_path)
    assert "evidnce" in str(exc.value)
    assert "not one of" in str(exc.value)


def test_promotions_are_reported(tmp_path, capsys):
    (tmp_path / "pins.kml").write_text(GOOGLE_KML, encoding="utf-8")
    csv_path = tmp_path / "ann.csv"
    csv_path.write_text(
        "pin_id,role,provenance,observed\n"
        "lennox-creek-adit,evidence,field_visit,Collapsed portal with quartz float\n"
        "old-road-cut,truth,literature,\n",
        encoding="utf-8",
    )
    fc = run_import(tmp_path, tmp_path / "pins.kml", "--annotations", csv_path)
    by_id = {p["pin_id"]: p for p in props_of(fc)}

    assert by_id["lennox-creek-adit"]["role"] == "evidence"
    assert by_id["lennox-creek-adit"]["provenance"] == "field_visit"
    assert by_id["lennox-creek-adit"]["visited"] is True
    assert by_id["lennox-creek-adit"]["observed"].startswith("Collapsed portal")
    assert by_id["old-road-cut"]["role"] == "truth"
    # Untouched rows keep the safe default.
    assert by_id["quartz-float-upper-basin"]["role"] == "display"

    report = capsys.readouterr().out
    assert "promoted to role=evidence" in report
    assert "promoted to role=truth" in report
    assert "lennox-creek-adit" in report


def test_annotations_blank_cells_do_not_clobber(tmp_path):
    """A template handed back unedited must be a no-op."""
    (tmp_path / "pins.kml").write_text(GOOGLE_KML, encoding="utf-8")
    template = tmp_path / "template.csv"
    fc_plain = run_import(tmp_path, tmp_path / "pins.kml", "--emit-template", template)

    rows = template.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 4  # header + three pins
    assert rows[0].startswith("pin_id,name,folder,role,provenance")
    assert "Collapsed adit, 2 m portal" in template.read_text(encoding="utf-8")

    fc_annotated = run_import(tmp_path, tmp_path / "pins.kml", "--annotations", template)
    assert props_of(fc_annotated) == props_of(fc_plain)


def test_load_user_sites_excludes_truth_by_default(tmp_path, monkeypatch):
    """The test that protects the benchmark.

    `load_user_sites()` with no arguments must return evidence pins only. A
    caller that forgets to filter — and `build_local_context` is exactly such a
    caller — must not be handed ground truth to put in a prompt (§30,
    CONTRACT invariant 1).
    """
    (tmp_path / "pins.kml").write_text(GOOGLE_KML, encoding="utf-8")
    csv_path = tmp_path / "ann.csv"
    csv_path.write_text(
        "pin_id,role,provenance\n"
        "lennox-creek-adit,evidence,field_visit\n"
        "quartz-float-upper-basin,truth,field_visit\n",
        encoding="utf-8",
    )
    sites_dir = tmp_path / "user_sites"
    sites_dir.mkdir()
    run_import(
        tmp_path,
        tmp_path / "pins.kml",
        "--annotations",
        csv_path,
        out=sites_dir / "pins.geojson",
    )
    monkeypatch.setattr(user_sites, "USER_SITES_DIR", sites_dir)

    default = load_user_sites()
    assert [p["pin_id"] for p in default] == ["lennox-creek-adit"]
    assert all(p["role"] == "evidence" for p in default)
    # Named explicitly: the truth pin is absent by pin_id AND by name, because a
    # leaked name in a prompt is as damaging as a leaked coordinate.
    assert "quartz-float-upper-basin" not in {p["pin_id"] for p in default}
    assert "Quartz float, upper basin" not in {p["name"] for p in default}

    every = load_user_sites(roles=ALL_ROLES)
    assert len(every) == 3
    assert role_counts() == {"display": 1, "truth": 1, "evidence": 1}

    # The map may draw every role — the map is not the model — but the role has
    # to travel with the feature so the UI can style it.
    fc = sites_geojson()
    assert len(fc["features"]) == 3
    assert {f["properties"]["role"] for f in fc["features"]} == {
        "display",
        "truth",
        "evidence",
    }
    assert fc["properties"]["counts_by_role"]["truth"] == 1


def test_unrecognised_role_on_disk_is_downgraded_to_display(tmp_path, monkeypatch):
    """A hand-edited typo must never resolve into a role that reaches the model."""
    sites_dir = tmp_path / "user_sites"
    sites_dir.mkdir()
    (sites_dir / "hand.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-121.5, 48.0]},
                        "properties": {"pin_id": "typo", "name": "Typo", "role": "evidnce"},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-121.5, 48.1]},
                        # Case and whitespace are normalized, so this really is
                        # ground truth and really must be withheld.
                        "properties": {"pin_id": "shouty", "name": "Shouty", "role": " TRUTH "},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(user_sites, "USER_SITES_DIR", sites_dir)

    assert load_user_sites() == []
    assert role_counts() == {"display": 1, "truth": 1, "evidence": 0}
    assert load_user_sites(roles=["display"])[0]["pin_id"] == "typo"


def test_malformed_file_is_skipped_not_fatal(tmp_path, monkeypatch, caplog):
    sites_dir = tmp_path / "user_sites"
    sites_dir.mkdir()
    (sites_dir / "broken.geojson").write_text("{not json,", encoding="utf-8")
    (sites_dir / "good.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-121.5, 48.0]},
                        "properties": {"pin_id": "ok", "name": "OK", "role": "evidence"},
                    },
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[-121.5, 48.0], [-121.4, 48.0], [-121.4, 48.1]]],
                        },
                        "properties": {"pin_id": "poly", "role": "evidence"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(user_sites, "USER_SITES_DIR", sites_dir)

    pins = load_user_sites()
    assert [p["pin_id"] for p in pins] == ["ok"]


def test_missing_directory_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(user_sites, "USER_SITES_DIR", tmp_path / "nope")
    assert load_user_sites() == []
    assert role_counts() == {"display": 0, "truth": 0, "evidence": 0}
    assert sites_geojson()["features"] == []


# --- §30.3 "not in any database" ------------------------------------------


def test_potentially_new_is_metric_not_degrees(tmp_path):
    """200 m is a distance, and a degree is not.

    At 48°N a degree of longitude is ~74.6 km and a degree of latitude
    ~111.2 km. These two pins are offset from the same occurrence by the *same*
    0.0025°, one east and one north:

        east  → 186 m → inside 200 m → NOT new
        north → 278 m → outside      → potentially new

    Any implementation that compares coordinate deltas gives both pins the same
    answer, so this pair fails a degrees-based check in one direction or the
    other no matter which threshold it picks.
    """
    occ = occurrences_file(
        tmp_path / "occ.geojson", [{"lon": -121.5, "lat": 48.0, "name": "Copper Key"}]
    )
    kml = tmp_path / "near.kml"
    kml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>East pin</name>
    <Point><coordinates>-121.4975,48.0000</coordinates></Point></Placemark>
  <Placemark><name>North pin</name>
    <Point><coordinates>-121.5000,48.0025</coordinates></Point></Placemark>
  <Placemark><name>Far pin</name>
    <Point><coordinates>-121.4000,48.0000</coordinates></Point></Placemark>
</Document></kml>
""",
        encoding="utf-8",
    )
    fc = run_import(
        tmp_path, kml, "--provenance", "field_visit", occurrences=occ
    )
    by_id = {p["pin_id"]: p for p in props_of(fc)}

    east, north, far = by_id["east-pin"], by_id["north-pin"], by_id["far-pin"]

    # Identical degree offsets...
    assert abs(-121.4975 - -121.5) == pytest.approx(0.0025)
    assert abs(48.0025 - 48.0) == pytest.approx(0.0025)
    # ...different distances, and different answers.
    assert east["nearest_db_km"] == pytest.approx(0.186, abs=0.004)
    assert north["nearest_db_km"] == pytest.approx(0.278, abs=0.004)
    assert east["potentially_new"] is False
    assert north["potentially_new"] is True

    assert far["nearest_db_km"] == pytest.approx(7.46, abs=0.05)
    assert far["potentially_new"] is True
    assert far["nearest_db_name"] == "Copper Key"


def test_potentially_new_requires_field_visit_provenance(tmp_path):
    """"I read about this" is never a discovery, however far from a database."""
    occ = occurrences_file(
        tmp_path / "occ.geojson", [{"lon": -121.5, "lat": 48.0, "name": "Copper Key"}]
    )
    (tmp_path / "pins.kml").write_text(GOOGLE_KML, encoding="utf-8")
    csv_path = tmp_path / "ann.csv"
    csv_path.write_text(
        "pin_id,provenance\n"
        "quartz-float-upper-basin,field_visit\n"
        "old-road-cut,hearsay\n",
        encoding="utf-8",
    )
    fc = run_import(
        tmp_path,
        tmp_path / "pins.kml",
        "--provenance",
        "literature",
        "--annotations",
        csv_path,
        occurrences=occ,
    )
    by_id = {p["pin_id"]: p for p in props_of(fc)}

    # All three are well outside 200 m of the single occurrence...
    assert all(p["nearest_db_km"] > 0.2 for p in props_of(fc) if p["pin_id"] != "lennox-creek-adit")
    # ...but only the field_visit pin is flagged.
    assert by_id["quartz-float-upper-basin"]["potentially_new"] is True
    assert by_id["old-road-cut"]["potentially_new"] is False
    assert by_id["lennox-creek-adit"]["potentially_new"] is False
    # visited follows provenance when it is not stated on the CLI.
    assert by_id["quartz-float-upper-basin"]["visited"] is True
    assert by_id["old-road-cut"]["visited"] is False


def test_missing_occurrence_file_leaves_distances_null_and_says_so(tmp_path, capsys):
    """"We did not look" and "nothing within 200 m" must not share a value."""
    (tmp_path / "pins.kml").write_text(GOOGLE_KML, encoding="utf-8")
    fc = run_import(tmp_path, tmp_path / "pins.kml", "--provenance", "field_visit")

    for p in props_of(fc):
        assert p["nearest_db_km"] is None
        assert p["nearest_db_name"] is None
        assert p["potentially_new"] is False

    report = capsys.readouterr().out
    assert "NOT EVALUATED" in report
    assert "3 field_visit pin(s)" in report
